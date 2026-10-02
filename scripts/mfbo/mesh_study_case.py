"""Mesh, solve, and extract one campaign case with mesh-knob overrides.

The production baseline is not rebuilt here. Re-extract that leaf with
``reextract_runs.py --copy-from-production``. This driver refuses a study
``mesh_id`` that matches the production mesh id.

Before the meshing worker starts, the production geometry file
``C:/ro_data/geometries/<family>/<geo_id>/<geo_id>.dsco`` is copied into
the study data root. An existing copy is reused only when its sha256
matches. The source is never moved.

``format_production_mesh_id`` encodes max/min/cpg/bl/peel and omits the
default first-height factor. A factor that differs from the template is
inserted as ``_fNNN`` (``round(factor * 100)``, so 0.40 is ``f040``) before
``_peel``, which is the token ``MESH_ID_RE`` already accepts.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_meshing
import batch_report_extract
import batch_solver_sweep
import mfbo._common as mfbo_common
import mfbo.solve_campaign_case as campaign
from ro.campaign_matrix import format_production_mesh_id
from ro.convergence_quality import continuity_final_from_case_dir
from ro.mesh_common import load_mesh_run_record, parse_meshing_input_summary
from ro.paths import MESH_ID_RE, project_root

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12
PRODUCTION_DATA_ROOT = Path("C:/ro_data")

MESH_PRINT_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
)
SUMMARY_PRINT_FIELDS = (
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cpc_window_avg_flux",
    "cp_q99_window_flux",
    "cp_q999_window_flux",
)


def resolve_data_root(value):
    """Return an absolute data root that is not C:/ro_data or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    folded = text.casefold().rstrip("/")
    if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"data root must be absolute, got {value!r}.")
        path = path.resolve()
    resolved = path.as_posix().casefold().rstrip("/")
    if resolved == "c:/ro_data" or resolved.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if resolved == "/mnt/c/ro_data" or resolved.startswith("/mnt/c/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    return path


def _is_windows_absolute(text):
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def require_base_case(case):
    return campaign.require_base_case(case)


def collect_overrides(
    *,
    m_max=None,
    m_min=None,
    m_cpg=None,
    bl_layers=None,
    bl_height_factor=None,
):
    """Return the mesh knobs the caller set. At least one is required."""
    changes = {}
    if m_max is not None:
        _require_positive("m_max", m_max)
        changes["m_max"] = float(m_max)
    if m_min is not None:
        _require_positive("m_min", m_min)
        changes["m_min"] = float(m_min)
    if m_cpg is not None:
        _require_positive_int("m_cpg", m_cpg)
        changes["m_cpg"] = int(m_cpg)
    if bl_layers is not None:
        _require_positive_int("bl_layers", bl_layers)
        changes["bl_layers"] = int(bl_layers)
    if bl_height_factor is not None:
        _require_positive("bl_height_factor", bl_height_factor)
        changes["bl_height_factor"] = float(bl_height_factor)
    if not changes:
        raise ValueError(
            "At least one mesh override is required "
            "(--m-max, --m-min, --m-cpg, --bl-layers, "
            "--bl-first-height-factor). The production baseline is "
            "reextract_runs.py --copy-from-production."
        )
    return changes


def _require_positive(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    if not math.isfinite(float(value)) or float(value) <= 0.0:
        raise ValueError(f"{name} must be a positive number, got {value!r}.")


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")


def default_bl_height_factor():
    """``run_config.bl_height_factor``. The production template does not set it."""
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "mesh_study_run_config",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return float(module.bl_height_factor)


def template_bl_height_factor(template):
    if template.get("bl_height_factor") is not None:
        return float(template["bl_height_factor"])
    return default_bl_height_factor()


def with_height_factor_token(mesh_id, factor):
    """Insert ``_fNNN`` before ``_peel``. ``NNN`` is ``round(factor * 100)``."""
    number = int(round(float(factor) * 100.0))
    if number < 0 or number > 999:
        raise ValueError(
            f"bl_height_factor {factor!r} does not fit the _fNNN mesh_id token."
        )
    stem, peel = str(mesh_id).rsplit("_peel", 1)
    return f"{stem}_f{number:03d}_peel{peel}"


def study_mesh_id(
    *,
    m_max,
    m_min,
    m_cpg,
    bl_layers,
    peel_layers,
    bl_height_factor,
    template_factor,
):
    """Production formatter, plus ``_fNNN`` when the factor is not the template."""
    mesh_id = format_production_mesh_id(
        m_max=m_max,
        m_min=m_min,
        m_cpg=m_cpg,
        bl_layers=bl_layers,
        peel_layers=peel_layers,
    )
    if not math.isclose(
        float(bl_height_factor),
        float(template_factor),
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    ):
        mesh_id = with_height_factor_token(mesh_id, bl_height_factor)
    if MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"study mesh_id is invalid: {mesh_id!r}.")
    return mesh_id


def require_distinct_mesh_id(study_id, production_id):
    if study_id == production_id:
        raise ValueError(
            f"study mesh_id {study_id!r} equals the production mesh_id. "
            "The baseline is reextract_runs.py --copy-from-production."
        )
    return study_id


def select_production_mesh_case(cases, geo_id):
    selected = [case for case in cases if case.get("geo_id") == geo_id]
    if len(selected) != 1:
        raise RuntimeError(
            "Expected exactly one production_mesh_batch_cases entry with "
            f"geo_id {geo_id!r}, found {len(selected)}."
        )
    return selected[0]


def load_mesh_template(geo_id):
    """One production mesh case, merged by the batch meshing override builder."""
    batchcfg = batch_meshing._load_module(
        "batch_config_mesh_study",
        batch_meshing.BATCH_CONFIG_PATH,
    )
    case = select_production_mesh_case(
        batchcfg.production_mesh_batch_cases,
        geo_id,
    )
    common = getattr(batchcfg, "common_mesh_settings", {})
    overrides = batch_meshing._build_overrides(case, common)
    retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            getattr(batchcfg, "cad_import_max_retries", 2),
        )
    )
    return case, overrides, retries


def apply_study_overrides(template, changes):
    """Copy the template, apply knobs, and set the study mesh_id."""
    overrides = dict(template)
    overrides.update(changes)
    factor = float(
        overrides.get("bl_height_factor", template_bl_height_factor(template))
    )
    m_max = float(overrides["m_max"])
    m_min = float(overrides["m_min"])
    if m_min > m_max:
        raise ValueError(
            f"m_min must be <= m_max. m_min={m_min!r}, m_max={m_max!r}."
        )
    mesh_id = study_mesh_id(
        m_max=m_max,
        m_min=m_min,
        m_cpg=int(overrides["m_cpg"]),
        bl_layers=int(overrides["bl_layers"]),
        peel_layers=int(overrides["peel_layers"]),
        bl_height_factor=factor,
        template_factor=template_bl_height_factor(template),
    )
    require_distinct_mesh_id(mesh_id, template["mesh_id"])
    overrides["mesh_id"] = mesh_id
    overrides["case_name"] = mesh_id
    if "bl_height_factor" in changes:
        overrides["bl_height_factor"] = factor
    return overrides


def _values_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=_FLOAT_REL_TOL,
            abs_tol=_FLOAT_ABS_TOL,
        )
    return left == right


def override_rows(template, overrides):
    keys = sorted(set(template) | set(overrides))
    rows = []
    for key in keys:
        in_template = key in template
        in_overrides = key in overrides
        template_value = template.get(key)
        override_value = overrides.get(key)
        if in_template and in_overrides and _values_equal(template_value, override_value):
            status = "same"
        else:
            status = "differs"
        rows.append(
            {
                "key": key,
                "template": template_value if in_template else None,
                "override": override_value if in_overrides else None,
                "status": status,
            }
        )
    return rows


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def print_override_table(rows):
    print("Mesh study overrides vs production template:")
    print(f"{'key':<32} {'template':<36} {'override':<36} status")
    for row in rows:
        print(
            f"{row['key']:<32} {_cell(row['template']):<36} "
            f"{_cell(row['override']):<36} {row['status']}"
        )


def production_geometry_dsco(family, geo_id):
    """``C:/ro_data/geometries/<family>/<geo_id>/<geo_id>.dsco``."""
    return (
        PRODUCTION_DATA_ROOT
        / "geometries"
        / family
        / geo_id
        / f"{geo_id}.dsco"
    )


def study_geometry_dsco(data_root, family, geo_id):
    return (
        Path(data_root) / "geometries" / family / geo_id / f"{geo_id}.dsco"
    )


def _under_production_tree(path):
    folded = Path(path).as_posix().casefold().rstrip("/")
    return (
        folded == "c:/ro_data"
        or folded.startswith("c:/ro_data/")
        or folded == "/mnt/c/ro_data"
        or folded.startswith("/mnt/c/ro_data/")
    )


def ensure_production_geometry_dsco(data_root, family, geo_id):
    """Copy the production .dsco, or reuse it when the sha256 matches.

    Uses ``run_parity_mesh.copy_geometry_file`` (copy only; sha256 checked
    after the copy). An existing destination is not rewritten: the same
    helper's sha256 compare must match, or this raises and leaves the file
    in place. The source is never moved.
    """
    import mfbo.run_parity_mesh as parity_mesh

    source = production_geometry_dsco(family, geo_id)
    dest = study_geometry_dsco(data_root, family, geo_id)
    if _under_production_tree(dest):
        raise ValueError(f"Refusing to copy geometry into C:/ro_data: {dest}")
    if not source.is_file():
        raise FileNotFoundError(f"Geometry file not found: {source}")
    if dest.exists():
        if not dest.is_file():
            raise FileExistsError(
                f"Refusing to overwrite geometry path that is not a file: {dest}"
            )
        digest = parity_mesh.assert_sha256_equal(source, dest)
        print(f"Reusing geometry {dest} sha256={digest}")
        return dest
    digest = parity_mesh.copy_geometry_file(source, dest)
    print(f"Copied geometry {source} -> {dest} sha256={digest}")
    return dest


def mesh_leaf(data_root, family, geo_id, mesh_id):
    return Path(data_root) / "meshes" / family / geo_id / mesh_id


def run_leaf(data_root, family, geo_id, mesh_id, run_id):
    return Path(data_root) / "runs" / family / geo_id / mesh_id / run_id


def refuse_existing_directory(path, label):
    directory = Path(path)
    if directory.exists():
        raise FileExistsError(
            f"Refusing to continue because the {label} already exists: {directory}."
        )


def launch_meshing(overrides, data_root, mesh_directory, mesh_id, max_retries):
    """Launch the production meshing worker. ``RO_DATA_ROOT`` is child-only."""
    cmd = [sys.executable, str(batch_meshing.MESHING_SCRIPT_PATH)]
    env = {
        **os.environ,
        "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides),
        "RO_DATA_ROOT": str(data_root),
    }
    env.pop("PYFLUENT_RUN_CONFIG", None)
    env.pop("PYFLUENT_SKIP_VALIDATION", None)
    print(f"Command: {' '.join(cmd)}")
    print(f"RO_DATA_ROOT (child only): {data_root}")
    result, attempts, retry_kinds = batch_meshing.run_meshing_attempts(
        cmd=cmd,
        env=env,
        cwd=str(batch_meshing.SCRIPT_DIR),
        mesh_log_path=Path(mesh_directory) / f"mesh_log_{mesh_id}.txt",
        mesh_run_record_path=Path(mesh_directory) / "mesh_run_record.json",
        max_retries=max_retries,
    )
    if result is None:
        raise RuntimeError("Meshing worker did not return a process result.")
    print(
        f"Meshing return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result


def solver_overrides_for_study(solver_overrides, study_mesh_id):
    """Replace only ``mesh_id`` on the production solver overrides."""
    updated = dict(solver_overrides)
    if updated.get("mesh_id") == study_mesh_id:
        raise ValueError(
            "solver template mesh_id is already the study mesh_id "
            f"{study_mesh_id!r}."
        )
    updated["mesh_id"] = study_mesh_id
    return updated


def first_bl_height_mm(mesh_directory, mesh_id):
    """Height the worker printed and passed to Add Boundary Layers, in mm.

    The mesh manifest does not store it. The input summary in the mesh log
    does, and ``mesh_run_record.json`` stores the same ``bl_height``.
    """
    directory = Path(mesh_directory)
    log_path = directory / f"mesh_log_{mesh_id}.txt"
    if log_path.is_file():
        parsed = parse_meshing_input_summary(
            log_path.read_text(encoding="utf-8", errors="ignore")
        )
        if parsed.get("bl_height") is not None:
            return parsed["bl_height"]
    record = load_mesh_run_record(directory / "mesh_run_record.json")
    if record is not None and record.get("bl_height") is not None:
        return record["bl_height"]
    raise RuntimeError(
        "Worker did not record the boundary-layer first height in "
        f"{log_path} or mesh_run_record.json."
    )


def _read_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def read_summary_wide(path):
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: {path}"
        )
    return rows[0]


def study_report(mesh_directory, mesh_id, run_directory):
    manifest_path = Path(mesh_directory) / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Mesh manifest not found: {manifest_path}")
    mesh_manifest = _read_json(manifest_path)
    missing = [key for key in MESH_PRINT_FIELDS if key not in mesh_manifest]
    if missing:
        raise KeyError(f"Mesh manifest is missing fields: {missing}.")
    run_manifest_path = Path(run_directory) / "manifest.json"
    run_manifest = (
        _read_json(run_manifest_path) if run_manifest_path.is_file() else {}
    )
    summary = read_summary_wide(
        Path(run_directory) / "post" / "reports" / "summary_metrics_wide.csv"
    )
    continuity = run_manifest.get("continuity_final")
    if continuity is None and Path(run_directory).is_dir():
        continuity = continuity_final_from_case_dir(run_directory)
    report = {
        "mesh_id": mesh_id,
        "first_bl_height_mm": first_bl_height_mm(mesh_directory, mesh_id),
        "stop_reason": run_manifest.get("stop_reason"),
        "continuity_final": continuity,
    }
    for key in MESH_PRINT_FIELDS:
        report[key] = mesh_manifest[key]
    for key in SUMMARY_PRINT_FIELDS:
        report[key] = summary.get(key)
    return report


def print_study_report(report):
    fields = (
        "mesh_id",
        *MESH_PRINT_FIELDS,
        "first_bl_height_mm",
        "stop_reason",
        "continuity_final",
        *SUMMARY_PRINT_FIELDS,
    )
    print("Mesh study:")
    for key in fields:
        print(f"  {key}: {_cell(report.get(key))}")


def _exit_code(returncode):
    if returncode:
        return int(returncode)
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Mesh, solve, and extract one campaign case with mesh overrides. "
            "Refuses C:/ro_data and a mesh_id equal to production."
        ),
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child processes. Not C:/ro_data.",
    )
    parser.add_argument("--geo-id", required=True, help="Campaign geo_id.")
    parser.add_argument(
        "--case",
        default="u0p2_p6M",
        help="Base run id (default u0p2_p6M).",
    )
    parser.add_argument("--m-max", type=float, default=None)
    parser.add_argument("--m-min", type=float, default=None)
    parser.add_argument("--m-cpg", type=int, default=None)
    parser.add_argument("--bl-layers", type=int, default=None)
    parser.add_argument("--bl-first-height-factor", type=float, default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    case_id = require_base_case(args.case)
    changes = collect_overrides(
        m_max=args.m_max,
        m_min=args.m_min,
        m_cpg=args.m_cpg,
        bl_layers=args.bl_layers,
        bl_height_factor=args.bl_first_height_factor,
    )
    case, template, max_retries = load_mesh_template(args.geo_id)
    family = case["family"]
    production_mesh_id = case["mesh_id"]
    overrides = apply_study_overrides(template, changes)
    print_override_table(override_rows(template, overrides))
    study_id = overrides["mesh_id"]
    leaf = mesh_leaf(data_root, family, args.geo_id, study_id)
    refuse_existing_directory(leaf, "mesh leaf")
    ensure_production_geometry_dsco(data_root, family, args.geo_id)
    mesh_result = launch_meshing(
        overrides,
        data_root,
        leaf,
        study_id,
        max_retries,
    )
    if mesh_result.returncode != 0:
        return _exit_code(mesh_result.returncode)

    _entry, solver_template, solver_retries, settle_s = campaign.load_template(
        args.geo_id,
        production_mesh_id,
        case_id,
    )
    solver_overrides = solver_overrides_for_study(solver_template, study_id)
    run_directory = run_leaf(data_root, family, args.geo_id, study_id, case_id)
    refuse_existing_directory(run_directory, "run leaf")
    solver_result = campaign.launch_solver(
        solver_overrides,
        data_root,
        run_directory,
        geo_id=args.geo_id,
        mesh_id=study_id,
        run_id=case_id,
        max_retries=solver_retries,
        settle_s=settle_s,
    )
    if not batch_solver_sweep.solver_worker_succeeded(solver_result.returncode):
        return _exit_code(solver_result.returncode)

    extract_result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        _entry,
        geo_id=args.geo_id,
        mesh_id=study_id,
        run_id=case_id,
    )
    if extract_result.returncode != 0:
        return _exit_code(extract_result.returncode)

    summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    print_study_report(study_report(leaf, study_id, run_directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
