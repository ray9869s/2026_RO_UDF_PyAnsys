"""Solve and extract one MFP case with the production workers.

Reuses ``scripts/batch_solver_sweep.py`` for overrides and
``run_solver_attempts``, and ``scripts/mfbo/_common.py`` for the same report
extraction call as ``run_parity_solve.py``. ``RO_DATA_ROOT`` is set on the
child processes. The parent sets it only while resolving the MFP registry
and while extraction reads the new run manifest, then restores it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_report_extract
import batch_solver_sweep
import mfbo._common as mfbo_common
from ro.geometry_registry import MFBO_PILLAR_GEO_ID_RE, resolve_geometry_parameters

TEMPLATE_GEO_ID = "P_p100_h30"
DEFAULT_MESH_ID = "max085_min006_cpg5_bl4_peel2"
DEFAULT_CASE = "u0p2_p6M"
FAMILY = "pillar"

ALLOWED_OVERRIDE_DELTA = frozenset(
    {
        "geo_id",
        "geo_name",
        "spacing_code",
        "filament_d_m",
        "bridge_radius_m",
        "wall_spacer_labels",
    }
)

SUMMARY_PRINT_FIELDS = (
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cp_canon_window_avg",
    "cp_canon_window_max",
    "pp_cp_canon_max_cell_5",
    "pp_cp_canon_max_cell_6",
    "pp_cp_canon_max_cell_7",
    "pp_cp_canon_max_cell_8",
)

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12


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


def require_mfbo_geo_id(geo_id):
    """Require an MFP pillar id. Campaign ids are not accepted here."""
    if not isinstance(geo_id, str) or MFBO_PILLAR_GEO_ID_RE.fullmatch(geo_id) is None:
        raise ValueError(
            f"geo_id must match MFBO_PILLAR_GEO_ID_RE, got {geo_id!r}."
        )
    return geo_id


def mesh_leaf(data_root, geo_id, mesh_id):
    return Path(data_root) / "meshes" / FAMILY / geo_id / mesh_id


def run_leaf(data_root, geo_id, mesh_id, run_id):
    return Path(data_root) / "runs" / FAMILY / geo_id / mesh_id / run_id


def mesh_file(mesh_directory, geo_id, mesh_id):
    return Path(mesh_directory) / f"{geo_id}_{mesh_id}.msh.h5"


def require_mesh_leaf(mesh_directory, geo_id, mesh_id):
    """Require the mesh manifest and ``.msh.h5``."""
    directory = Path(mesh_directory)
    manifest = directory / "manifest.json"
    mesh = mesh_file(directory, geo_id, mesh_id)
    missing = [path for path in (manifest, mesh) if not path.is_file()]
    if missing:
        listed = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "Mesh leaf is missing the manifest or .msh.h5: "
            f"{listed}."
        )
    return manifest, mesh


def refuse_existing_run(run_directory):
    """Refuse to solve into a run leaf that already exists."""
    directory = Path(run_directory)
    if directory.exists():
        raise FileExistsError(
            f"Refusing to solve because the run leaf already exists: {directory}."
        )


def call_with_data_root(data_root, fn):
    """Call ``fn`` with ``RO_DATA_ROOT`` set, then restore the parent env."""
    return mfbo_common.call_with_data_root(data_root, fn)


def resolve_registry(data_root, geo_id):
    """Read the MFP registry entry. Parent ``RO_DATA_ROOT`` is restored."""
    return call_with_data_root(
        data_root,
        lambda: resolve_geometry_parameters(geo_id),
    )


def load_template(mesh_id, run_id):
    """Production P_p100_h30 solver case, merged by batch_solver_sweep."""
    batchcfg = batch_solver_sweep._load_module(
        "batch_config_mfbo_solve",
        batch_solver_sweep.BATCH_CONFIG_PATH,
    )
    cases = [
        case
        for case in batchcfg.production_solver_sweep_cases
        if case.get("geo_id") == TEMPLATE_GEO_ID
        and case.get("mesh_id") == mesh_id
        and case.get("run_id") == run_id
    ]
    if len(cases) != 1:
        raise RuntimeError(
            "Expected exactly one production_solver_sweep_cases entry with "
            f"geo_id {TEMPLATE_GEO_ID!r}, mesh_id {mesh_id!r}, "
            f"run_id {run_id!r}, found {len(cases)}."
        )
    case = cases[0]
    if case.get("family") != FAMILY:
        raise RuntimeError(
            f"Source case family must be {FAMILY!r}, got {case.get('family')!r}."
        )
    common = getattr(batchcfg, "common_solver_settings", {})
    batch_solver_sweep.require_explicit_inlet_velocity_profile(common, [case])
    _base_case_name, case_name = batch_solver_sweep.resolve_case_names(case)
    overrides = batch_solver_sweep.merge_batch_case_overrides(common, case)
    overrides["geo_name"] = case["geo_id"]
    overrides["case_name"] = case_name
    overrides.pop("base_case_name", None)
    retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            batch_solver_sweep.SOLVER_TRANSIENT_FAILURE_MAX_RETRIES,
        )
    )
    settle_s = float(
        getattr(
            batchcfg,
            "post_failure_settle_s",
            batch_solver_sweep.SOLVER_POST_FAILURE_SETTLE_S,
        )
    )
    return case, overrides, retries, settle_s


def apply_mfbo_overrides(template, geo_id, registry):
    """Copy the template and replace only the MFP geometry-identity keys."""
    overrides = dict(template)
    overrides["geo_id"] = geo_id
    overrides["geo_name"] = geo_id
    overrides["spacing_code"] = registry["spacing_code"]
    overrides["filament_d_m"] = registry["filament_d_m"]
    overrides["bridge_radius_m"] = registry["bridge_radius_m"]
    overrides["wall_spacer_labels"] = list(registry["spacer_wall_zones"])
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


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def assert_override_delta(template, overrides):
    """Print every override against the template. Only MFP keys may differ."""
    keys = sorted(set(template) | set(overrides))
    rows = []
    unexpected = []
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
        if status == "differs" and key not in ALLOWED_OVERRIDE_DELTA:
            unexpected.append(key)
    _print_override_table(rows)
    if unexpected:
        raise ValueError(
            "MFBO overrides may only change "
            f"{sorted(ALLOWED_OVERRIDE_DELTA)}. Unexpected differences: "
            f"{unexpected}."
        )
    return rows


def _print_override_table(rows):
    print("MFBO overrides vs production solver template:")
    print(f"{'key':<32} {'template':<36} {'override':<36} status")
    for row in rows:
        print(
            f"{row['key']:<32} {_cell(row['template']):<36} "
            f"{_cell(row['override']):<36} {row['status']}"
        )


def solver_child_env(data_root, overrides):
    """Child env for the solver worker. Does not mutate the parent env."""
    env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
    env["RO_DATA_ROOT"] = str(data_root)
    env.pop("PYFLUENT_RUN_CONFIG", None)
    env.pop("PYFLUENT_SKIP_VALIDATION", None)
    return env


def launch_solver(
    overrides,
    data_root,
    run_directory,
    *,
    geo_id,
    mesh_id,
    run_id,
    max_retries,
    settle_s,
):
    """Launch solver_code_260616.py the way batch_solver_sweep.main does."""
    cmd = [sys.executable, str(batch_solver_sweep.SOLVER_SCRIPT_PATH)]
    env = solver_child_env(data_root, overrides)
    print(f"Command: {' '.join(cmd)}")
    print(f"cwd: {batch_solver_sweep.SCRIPT_DIR}")
    print(f"RO_DATA_ROOT (child only): {data_root}")
    result, attempts, retry_kinds, attempt_logs = batch_solver_sweep.run_solver_attempts(
        cmd=cmd,
        env=env,
        cwd=str(batch_solver_sweep.SCRIPT_DIR),
        run_directory=run_directory,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
        case_name=overrides["case_name"],
        max_retries=max_retries,
        settle_s=settle_s,
    )
    if result is None:
        raise RuntimeError("Solver worker did not return a process result.")
    try:
        batch_solver_sweep.write_solver_retry_record(
            run_directory,
            attempts=attempts,
            retry_kinds=retry_kinds,
            attempt_logs=attempt_logs,
        )
    except OSError as exc:
        print(f"Warning: could not write solver_retry_record.json: {exc}")
    print(
        f"Solver return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def read_summary_wide(path):
    """Return the single data row of summary_metrics_wide.csv."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"summary_metrics_wide.csv not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: {path}"
        )
    return rows[0]


def result_fields(summary_row, mesh_manifest, run_manifest):
    """Fields printed after a successful extract."""
    if not isinstance(summary_row, dict):
        raise TypeError("summary row must be a dict.")
    values = {}
    missing = []
    for key in SUMMARY_PRINT_FIELDS:
        if key not in summary_row or summary_row[key] == "":
            missing.append(key)
        else:
            values[key] = summary_row[key]
    manifest_sources = {
        "inlet_profile_G": mesh_manifest,
        "u_mean_ms": run_manifest,
        "stop_reason": run_manifest,
    }
    for key, source in manifest_sources.items():
        if not isinstance(source, dict) or key not in source:
            missing.append(key)
        else:
            values[key] = source[key]
    if missing:
        raise KeyError(f"Result fields missing: {missing}.")
    return values


def print_result_fields(values):
    print("MFBO solve results:")
    for key in (*SUMMARY_PRINT_FIELDS, "inlet_profile_G", "u_mean_ms", "stop_reason"):
        print(f"  {key}: {_cell(values[key])}")


def _exit_code(returncode):
    if returncode:
        return int(returncode)
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="Solve and extract one MFP case with the production workers.",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child processes. Not C:/ro_data.",
    )
    parser.add_argument(
        "--geo-id",
        required=True,
        help="MFP pillar geo_id, for example MFP_d1000_h0300_f0400.",
    )
    parser.add_argument(
        "--mesh-id",
        default=DEFAULT_MESH_ID,
        help=f"Production mesh id (default {DEFAULT_MESH_ID}).",
    )
    parser.add_argument(
        "--case",
        default=DEFAULT_CASE,
        help=f"Production run id (default {DEFAULT_CASE}).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    geo_id = require_mfbo_geo_id(args.geo_id)
    mesh_directory = mesh_leaf(data_root, geo_id, args.mesh_id)
    run_directory = run_leaf(data_root, geo_id, args.mesh_id, args.case)
    require_mesh_leaf(mesh_directory, geo_id, args.mesh_id)
    refuse_existing_run(run_directory)

    registry = resolve_registry(data_root, geo_id)
    case, template, max_retries, settle_s = load_template(args.mesh_id, args.case)
    overrides = apply_mfbo_overrides(template, geo_id, registry)
    assert_override_delta(template, overrides)

    result = launch_solver(
        overrides,
        data_root,
        run_directory,
        geo_id=geo_id,
        mesh_id=args.mesh_id,
        run_id=args.case,
        max_retries=max_retries,
        settle_s=settle_s,
    )
    if not batch_solver_sweep.solver_worker_succeeded(result.returncode):
        return _exit_code(result.returncode)

    extract_result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        case,
        geo_id=geo_id,
        mesh_id=args.mesh_id,
        run_id=args.case,
    )
    if extract_result.returncode != 0:
        return _exit_code(extract_result.returncode)

    summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    try:
        fields = result_fields(
            read_summary_wide(summary),
            _read_json(mesh_directory / "manifest.json"),
            _read_json(run_directory / "manifest.json"),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAILED_RESULT_PRINT: {exc}")
        return 1
    print_result_fields(fields)
    print(f"Extract summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
