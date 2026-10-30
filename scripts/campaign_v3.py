"""Benchmark campaign on one mesh spec (31 geometries, p6M).

Data root ``C:/ro_data_mfbo/campaign_v3``. Leaves stay
``runs/<family>/<geo>/<mesh_id>/<case>`` so extraction, the evaluation
window, and ``summarize_results`` do not change. Geometry is the
converted ``.pmdb`` tree used by the mesh study that fixed this spec.

``configs/mesh_spec_v3.json`` is per family. The default is ``m_max``
0.035 mm, membrane y1 2.0 um last-ratio, polyhedra bulk 0.034 mm.
A family object overrides those fields. The smooth-transition ``bl``
token stays 4.

CP in the comparison and in ``seminar_cases.csv`` is
``cpc_window_avg_flux``. CP excess is that value minus 1, reported
beside it. The mesh-to-mesh step ``cp_rel_change`` uses CP itself.

``--mesh-only`` writes 31 meshing jobs. ``--mesh-table`` prints cell
count, membrane layers, orthogonal quality, aspect ratio, the quality
gate, and a solve-time estimate. A manifest is written only after the
gate passes, so a refused mesh is read from its mesh log and listed as
FAIL. The estimate is linear in cell count from D2450_a45 at 4.34 M
cells and 6.5 h.

The solve queue is u0p2, then u0p1, then u0p3. Every solve passes
``--reuse-mesh`` and ``--checkpoint-every 200``. ``job_queue run`` is
one Fluent at a time. ``job_queue run --resume`` appends ``--resume``
to a failed solve because the argv is ``mesh_study_case``.

Importing this module does not launch Fluent. ``--emit-queue`` only
writes JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mfbo.mesh_study_case as mesh_study  # noqa: E402
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id  # noqa: E402
from ro.campaign_matrix import (  # noqa: E402
    PRODUCTION_MESH_ID_D0817_A60,
    PRODUCTION_MESH_ID_DEFAULT,
)
from ro.mesh_common import parse_mesh_metrics_text  # noqa: E402
from ro.mesh_spec import (  # noqa: E402
    changes_for_settings,
    default_spec_path,
    family_settings,
    load_mesh_spec,
    resolve_spec_path,
    spec_file_sha256,
)
from ro.paths import project_root  # noqa: E402
from ro.solver_common import make_base_case_name  # noqa: E402

DATA_ROOT = "C:/ro_data_mfbo/campaign_v3"
GEOMETRY_ROOT = "C:/ro_data_mfbo/geometries_pmdb"
V2_ROOT = "C:/ro_data_mfbo/campaign_reextract_v2"
PRESSURE_PA = 6.0e6
# u0p2 first, then the other two p6M speeds.
VELOCITY_ORDER = (0.2, 0.1, 0.3)
# run_config.py mesh quality gate.
ORTHO_MIN_LIMIT = 0.05
AR_MAX_LIMIT = 150.0
SKEW_MAX_LIMIT = 0.85
SKEWED_FRACTION_LIMIT = 3.0e-5
_MEMBRANE_LAYERS_RE = re.compile(
    r"Membrane BL number of layers \[-\]:\s*(\d+)"
)
# D2450_a45 polyhedra bulk 0.034 mm: 4.34 M cells, about 6.5 h.
SOLVE_TIME_REFERENCE_CELLS = 4_340_000
SOLVE_TIME_REFERENCE_HOURS = 6.5

SEMINAR_CASES_COLUMNS = (
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "cpc_window_avg_flux",
    "cp_excess",
    "cp_q999_window_flux",
    "pressure_drop_spacer_per_m",
    "lmh_window_exposed",
    "lmh_window_module",
    "convergence_quality",
    "mesh_spec_sha256",
    "git_head",
)
COMPARE_COLUMNS = (
    "family",
    "geo_id",
    "run_id",
    "v2_mesh_id",
    "v3_mesh_id",
    "v2_cp",
    "v3_cp",
    "cp_rel_change",
    "v2_cp_excess",
    "v3_cp_excess",
    "v2_q999",
    "v3_q999",
    "v2_dp_per_m",
    "v3_dp_per_m",
    "v2_lmh_exposed",
    "v3_lmh_exposed",
    "v2_lmh_installed",
    "v3_lmh_installed",
    "v2_gate",
    "v3_gate",
)


def production_mesh_id(geo_id):
    """Production BL4 mesh id used by campaign_reextract_v2."""
    if geo_id == "D0817_a60":
        return PRODUCTION_MESH_ID_D0817_A60
    return PRODUCTION_MESH_ID_DEFAULT


def run_ids():
    return tuple(make_base_case_name(velocity, PRESSURE_PA) for velocity in VELOCITY_ORDER)


def production_templates():
    """One production meshing template per campaign geo, loaded once."""
    batch_meshing = mesh_study.batch_meshing
    batchcfg = batch_meshing._load_module(
        "batch_config_campaign_v3",
        batch_meshing.BATCH_CONFIG_PATH,
    )
    common = getattr(batchcfg, "common_mesh_settings", {})
    templates = {}
    for case in batchcfg.production_mesh_batch_cases:
        templates[case["geo_id"]] = batch_meshing._build_overrides(case, common)
    return templates


def mesh_ids_for_spec(spec, templates=None):
    """Study mesh id each geometry gets from its family settings."""
    if templates is None:
        templates = production_templates()
    mesh_ids = {}
    for geo_id in CAMPAIGN_GEO_ID_ORDER:
        settings = family_settings(spec, family_for_geo_id(geo_id))
        overrides = mesh_study.apply_study_overrides(
            templates[geo_id],
            changes_for_settings(settings),
        )
        mesh_ids[geo_id] = overrides["mesh_id"]
        mesh_ids[geo_id + ":layers"] = overrides["membrane_bl_y1"]["layers"]
    return mesh_ids


def _fmt_mm(value):
    return f"{float(value):.3f}"


def _job(job_id, argv):
    return {
        "id": job_id,
        "argv": argv,
        "cwd": str(project_root()),
    }


def spec_argv_token(spec_path):
    """Repo-relative spec path when the file lives in this checkout."""
    path = Path(spec_path).resolve()
    root = project_root().resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _study_argv(geo_id, settings, run_id, *, mesh_only, reuse_mesh, spec_token):
    argv = [
        "{python}",
        "scripts/mfbo/mesh_study_case.py",
        "--data-root",
        DATA_ROOT,
        "--geo-id",
        geo_id,
        "--case",
        run_id,
        "--m-max",
        _fmt_mm(settings["m_max"]),
        "--m-min",
        _fmt_mm(settings["m_min"]),
        "--m-cpg",
        str(int(settings["cpg"])),
        "--bl-layers",
        str(changes_for_settings(settings)["bl_layers"]),
        "--membrane-bl-y1-um",
        f"{float(settings['membrane_bl_y1_um']):g}",
        "--bulk-max-size",
        _fmt_mm(settings["bulk_max_size_mm"]),
        "--peel-layers",
        str(int(settings["peel"])),
        "--geometry-suffix",
        ".pmdb",
        "--geometry-root",
        GEOMETRY_ROOT,
        "--mesh-spec",
        spec_token,
    ]
    if mesh_only:
        argv.append("--mesh-only")
    else:
        if reuse_mesh:
            argv.append("--reuse-mesh")
        argv.extend(["--checkpoint-every", "200"])
    return argv


def mesh_jobs(spec, spec_path):
    """One mesh job per geometry. No solve."""
    token = spec_argv_token(spec_path)
    jobs = []
    for geo_id in CAMPAIGN_GEO_ID_ORDER:
        settings = family_settings(spec, family_for_geo_id(geo_id))
        jobs.append(
            _job(
                f"mesh-{geo_id}",
                _study_argv(
                    geo_id,
                    settings,
                    run_ids()[0],
                    mesh_only=True,
                    reuse_mesh=False,
                    spec_token=token,
                ),
            )
        )
    return jobs


def solve_jobs(spec, spec_path, *, run_id_filter=None):
    """Solves ordered u0p2, u0p1, u0p3. Each reuses the mesh when it exists."""
    token = spec_argv_token(spec_path)
    selected = run_ids() if not run_id_filter else tuple(run_id_filter)
    unknown = [run_id for run_id in selected if run_id not in run_ids()]
    if unknown:
        raise ValueError(
            f"run id is not a campaign v3 p6M case: {unknown}. "
            f"expected {list(run_ids())}."
        )
    jobs = []
    for run_id in selected:
        for geo_id in CAMPAIGN_GEO_ID_ORDER:
            settings = family_settings(spec, family_for_geo_id(geo_id))
            jobs.append(
                _job(
                    f"{run_id}-{geo_id}",
                    _study_argv(
                        geo_id,
                        settings,
                        run_id,
                        mesh_only=False,
                        reuse_mesh=True,
                        spec_token=token,
                    ),
                )
            )
    return jobs


def estimate_solve_hours(cell_count):
    """Hours from the D2450_a45 4.34 M cell / 6.5 h calibration."""
    if isinstance(cell_count, bool) or not isinstance(cell_count, (int, float)):
        return None
    if cell_count <= 0:
        return None
    return (
        float(cell_count)
        / SOLVE_TIME_REFERENCE_CELLS
        * SOLVE_TIME_REFERENCE_HOURS
    )


def quality_gate(ortho_min, ar_max, skewness_max=None, skewed_face_fraction=None):
    """PASS, FAIL, or MISSING. Limits are the production mesh quality gate.

    Orthogonal quality and aspect ratio are required. Skewness and the
    skewed-face fraction fail the gate when they are present and over
    the limit. A missing pair of orthogonal quality and aspect ratio is
    MISSING, which the table still prints.
    """
    if ortho_min is None or ar_max is None:
        return "MISSING"
    if float(ortho_min) < ORTHO_MIN_LIMIT or float(ar_max) > AR_MAX_LIMIT:
        return "FAIL"
    if skewness_max is not None and float(skewness_max) > SKEW_MAX_LIMIT:
        return "FAIL"
    if (
        skewed_face_fraction is not None
        and float(skewed_face_fraction) > SKEWED_FRACTION_LIMIT
    ):
        return "FAIL"
    return "PASS"


def _read_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _optional_float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text or text.upper() == "MISSING":
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def mesh_manifest_path(data_root, geo_id, mesh_id):
    family = family_for_geo_id(geo_id)
    return (
        Path(data_root)
        / "meshes"
        / family
        / geo_id
        / mesh_id
        / "manifest.json"
    )


def mesh_log_path(data_root, geo_id, mesh_id):
    """Transcript written before the quality gate can refuse the mesh."""
    return mesh_manifest_path(data_root, geo_id, mesh_id).parent / (
        f"mesh_log_{mesh_id}.txt"
    )


def _layers_from_log(text):
    matches = list(_MEMBRANE_LAYERS_RE.finditer(text))
    if not matches:
        return None
    return int(matches[-1].group(1))


def _metrics_from_log(path):
    """Quality numbers from a mesh that failed before the manifest write."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_mesh_metrics_text(text)
    cell_count = parsed.get("cell_count")
    if isinstance(cell_count, bool) or not isinstance(cell_count, int):
        cell_count = None
    return {
        "cell_count": cell_count,
        "membrane_layers": _layers_from_log(text),
        "ortho_min": _optional_float(parsed.get("min_orthogonal_quality")),
        "AR_max": _optional_float(parsed.get("max_aspect_ratio")),
        "skewness_max": _optional_float(parsed.get("max_skewness")),
        "skewed_face_fraction": _optional_float(parsed.get("skewed_face_fraction")),
    }


def mesh_table_rows(data_root, mesh_ids):
    """One row per campaign geometry. A failed or missing mesh stays in the list.

    The worker writes the manifest only after the quality gate passes.
    A refused mesh is read from its mesh log so the gate result is FAIL
    rather than absent.
    """
    rows = []
    for geo_id in CAMPAIGN_GEO_ID_ORDER:
        family = family_for_geo_id(geo_id)
        mesh_id = mesh_ids[geo_id]
        path = mesh_manifest_path(data_root, geo_id, mesh_id)
        if path.is_file():
            manifest = _read_json(path)
            layers = None
            stack = manifest.get("membrane_bl_y1")
            if isinstance(stack, dict):
                layers = stack.get("layers")
            cell_count = manifest.get("cell_count")
            if isinstance(cell_count, bool) or not isinstance(cell_count, int):
                cell_count = None
            ortho = _optional_float(manifest.get("ortho_min"))
            aspect = _optional_float(manifest.get("AR_max"))
            skewness = _optional_float(manifest.get("skewness_max"))
            fraction = _optional_float(manifest.get("skewed_face_fraction"))
            gate = quality_gate(ortho, aspect, skewness, fraction)
        else:
            logged = _metrics_from_log(mesh_log_path(data_root, geo_id, mesh_id))
            if logged is None:
                cell_count = layers = ortho = aspect = None
                gate = "MISSING"
            else:
                cell_count = logged["cell_count"]
                layers = logged["membrane_layers"]
                ortho = logged["ortho_min"]
                aspect = logged["AR_max"]
                gate = quality_gate(
                    ortho,
                    aspect,
                    logged["skewness_max"],
                    logged["skewed_face_fraction"],
                )
        rows.append(
            {
                "geo_id": geo_id,
                "family": family,
                "mesh_id": mesh_id,
                "cell_count": cell_count,
                "membrane_layers": layers,
                "ortho_min": ortho,
                "AR_max": aspect,
                "gate": gate,
                "solve_h": estimate_solve_hours(cell_count),
            }
        )
    return rows


def format_mesh_table(rows):
    """Text table plus an explicit FAIL list. Rows are not filtered."""
    headers = (
        "geo_id",
        "family",
        "cells",
        "layers",
        "ortho_min",
        "AR_max",
        "gate",
        "solve_h",
    )
    body = []
    for row in rows:
        hours = row["solve_h"]
        body.append(
            [
                row["geo_id"],
                row["family"],
                "-" if row["cell_count"] is None else str(row["cell_count"]),
                "-" if row["membrane_layers"] is None else str(row["membrane_layers"]),
                "-" if row["ortho_min"] is None else f"{row['ortho_min']:.6g}",
                "-" if row["AR_max"] is None else f"{row['AR_max']:.6g}",
                row["gate"],
                "-" if hours is None else f"{hours:.2f}",
            ]
        )
    widths = [len(header) for header in headers]
    for cells in body:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    def _format(cells):
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(cells)
        ).rstrip()

    lines = [_format(list(headers)), _format(["-" * width for width in widths])]
    lines.extend(_format(cells) for cells in body)
    failed = [row["geo_id"] for row in rows if row["gate"] == "FAIL"]
    listed = ", ".join(failed) if failed else "-"
    lines.append(f"quality gate FAIL ({len(failed)}): {listed}")
    return "\n".join(lines)


def run_leaf(root, geo_id, mesh_id, run_id):
    family = family_for_geo_id(geo_id)
    return Path(root) / "runs" / family / geo_id / mesh_id / run_id


def _wide_row(run_directory):
    path = Path(run_directory) / "post" / "reports" / "summary_metrics_wide.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: {path}"
        )
    return rows[0]


def _run_manifest(run_directory):
    path = Path(run_directory) / "manifest.json"
    if not path.is_file():
        return {}
    return _read_json(path)


def leaf_metrics(run_directory):
    """CP, excess, q999, dP/L, LMH, and the convergence gate for one leaf.

    CP is ``cpc_window_avg_flux``. Excess is CP minus 1. q999 is
    ``cp_q999_window_flux``, not that value minus 1. Installed LMH is
    ``lmh_window_module``.
    """
    wide = _wide_row(run_directory)
    manifest = _run_manifest(run_directory)
    cp = _optional_float(wide.get("cpc_window_avg_flux"))
    gate = manifest.get("convergence_quality")
    if not isinstance(gate, str) or not gate.strip():
        gate = "MISSING"
    return {
        "cp": cp,
        "cp_excess": None if cp is None else cp - 1.0,
        "q999": _optional_float(wide.get("cp_q999_window_flux")),
        "dp_per_m": _optional_float(wide.get("pressure_drop_spacer_per_m")),
        "lmh_exposed": _optional_float(wide.get("lmh_window_exposed")),
        "lmh_installed": _optional_float(wide.get("lmh_window_module")),
        "gate": gate,
        "mesh_spec_sha256": manifest.get("mesh_spec_sha256") or "",
        "git_head": manifest.get("git_head") or "",
    }


def _number_cell(value):
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return format(float(value), ".12g")


def comparison_rows(v2_root, v3_root, v3_mesh_ids):
    """One row per geometry and p6M speed. A missing leaf stays blank."""
    rows = []
    for run_id in run_ids():
        for geo_id in CAMPAIGN_GEO_ID_ORDER:
            v2_mesh = production_mesh_id(geo_id)
            v3_mesh = v3_mesh_ids[geo_id]
            v2 = leaf_metrics(run_leaf(v2_root, geo_id, v2_mesh, run_id))
            v3 = leaf_metrics(run_leaf(v3_root, geo_id, v3_mesh, run_id))
            if v2["cp"] is None or v3["cp"] is None or v2["cp"] == 0.0:
                step = None
            else:
                step = (v3["cp"] - v2["cp"]) / v2["cp"]
            rows.append(
                {
                    "family": family_for_geo_id(geo_id),
                    "geo_id": geo_id,
                    "run_id": run_id,
                    "v2_mesh_id": v2_mesh,
                    "v3_mesh_id": v3_mesh,
                    "v2_cp": v2["cp"],
                    "v3_cp": v3["cp"],
                    "cp_rel_change": step,
                    "v2_cp_excess": v2["cp_excess"],
                    "v3_cp_excess": v3["cp_excess"],
                    "v2_q999": v2["q999"],
                    "v3_q999": v3["q999"],
                    "v2_dp_per_m": v2["dp_per_m"],
                    "v3_dp_per_m": v3["dp_per_m"],
                    "v2_lmh_exposed": v2["lmh_exposed"],
                    "v3_lmh_exposed": v3["lmh_exposed"],
                    "v2_lmh_installed": v2["lmh_installed"],
                    "v3_lmh_installed": v3["lmh_installed"],
                    "v2_gate": v2["gate"],
                    "v3_gate": v3["gate"],
                }
            )
    return rows


def seminar_rows(data_root, mesh_ids):
    """Benchmark figure table. CP is ``cpc_window_avg_flux``."""
    rows = []
    for run_id in run_ids():
        for geo_id in CAMPAIGN_GEO_ID_ORDER:
            mesh_id = mesh_ids[geo_id]
            metrics = leaf_metrics(run_leaf(data_root, geo_id, mesh_id, run_id))
            mesh_path = mesh_manifest_path(data_root, geo_id, mesh_id)
            mesh_manifest = _read_json(mesh_path) if mesh_path.is_file() else {}
            spec_hash = metrics["mesh_spec_sha256"] or mesh_manifest.get(
                "mesh_spec_sha256", ""
            )
            git_head = metrics["git_head"] or mesh_manifest.get("git_head", "")
            rows.append(
                {
                    "family": family_for_geo_id(geo_id),
                    "geo_id": geo_id,
                    "mesh_id": mesh_id,
                    "run_id": run_id,
                    "cpc_window_avg_flux": metrics["cp"],
                    "cp_excess": metrics["cp_excess"],
                    "cp_q999_window_flux": metrics["q999"],
                    "pressure_drop_spacer_per_m": metrics["dp_per_m"],
                    "lmh_window_exposed": metrics["lmh_exposed"],
                    "lmh_window_module": metrics["lmh_installed"],
                    "convergence_quality": metrics["gate"],
                    "mesh_spec_sha256": spec_hash or "",
                    "git_head": git_head or "",
                }
            )
    return rows


def write_csv(path, columns, rows):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: _number_cell(row.get(key)) for key in columns}
            )
    return destination


def write_queue(path, jobs):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    return destination


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Write the campaign v3 queue, the mesh-quality table, "
            "the v2/v3 comparison, or seminar_cases.csv. "
            "Does not launch Fluent."
        )
    )
    parser.add_argument(
        "--emit-queue",
        default=None,
        help="Queue JSON path to write.",
    )
    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Queue 31 mesh jobs and do not solve.",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=None,
        help="Limit the solve queue to this run id. Repeatable. u0p2_p6M is first.",
    )
    parser.add_argument(
        "--mesh-table",
        action="store_true",
        help="Print the mesh quality table from manifests under the data root.",
    )
    parser.add_argument(
        "--data-root",
        default=DATA_ROOT,
        help=f"Campaign data root (default {DATA_ROOT}).",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Write the v2 versus v3 CSV to this path.",
    )
    parser.add_argument(
        "--v2-root",
        default=V2_ROOT,
        help=f"Production reextract root (default {V2_ROOT}).",
    )
    parser.add_argument(
        "--seminar-csv",
        default=None,
        help="Write seminar_cases.csv for the v3 leaves to this path.",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help="Mesh spec JSON. Default configs/mesh_spec_v3.json.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mesh_only and args.run_id:
        raise ValueError("--mesh-only does not take --run-id.")
    if not any((args.emit_queue, args.mesh_table, args.compare, args.seminar_csv)):
        raise ValueError(
            "Choose --emit-queue, --mesh-table, --compare, or --seminar-csv."
        )
    spec_path = resolve_spec_path(
        default_spec_path() if args.spec is None else args.spec
    )
    spec = load_mesh_spec(spec_path)
    needs_mesh_ids = args.mesh_table or args.compare or args.seminar_csv
    mesh_ids = mesh_ids_for_spec(spec) if needs_mesh_ids or args.emit_queue else None
    if args.emit_queue:
        if mesh_ids is None:
            mesh_ids = mesh_ids_for_spec(spec)
        jobs = (
            mesh_jobs(spec, spec_path)
            if args.mesh_only
            else solve_jobs(spec, spec_path, run_id_filter=args.run_id)
        )
        path = write_queue(args.emit_queue, jobs)
        families_seen = []
        for geo_id in CAMPAIGN_GEO_ID_ORDER:
            family = family_for_geo_id(geo_id)
            if family in families_seen:
                continue
            families_seen.append(family)
            print(
                f"{family}: {mesh_ids[geo_id]} "
                f"membrane_bl_layers={mesh_ids[geo_id + ':layers']}"
            )
        print(f"spec_sha256={spec_file_sha256(spec_path)}")
        print(f"Wrote {len(jobs)} jobs to {path}")
    if args.mesh_table:
        rows = mesh_table_rows(args.data_root, mesh_ids)
        print(format_mesh_table(rows))
    if args.compare:
        rows = comparison_rows(args.v2_root, args.data_root, mesh_ids)
        path = write_csv(args.compare, COMPARE_COLUMNS, rows)
        print(f"Wrote {len(rows)} comparison rows to {path}")
    if args.seminar_csv:
        rows = seminar_rows(args.data_root, mesh_ids)
        path = write_csv(args.seminar_csv, SEMINAR_CASES_COLUMNS, rows)
        print(f"Wrote {len(rows)} seminar rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
