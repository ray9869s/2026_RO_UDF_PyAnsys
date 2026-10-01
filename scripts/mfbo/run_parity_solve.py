"""Solve one production case under a separate data root, then extract it.

Reuses ``scripts/batch_solver_sweep.py`` to launch ``solver_code_260616.py``
and ``scripts/batch_report_extract.py`` to launch ``pyfluent_report_extract.py``
(``build_post_case_overrides``, then the env and ``run_extract_attempts`` call
at batch_report_extract.py:819-836). ``RO_DATA_ROOT`` is set on those child
processes. The parent sets it only while ``build_post_case_overrides`` reads
the new run's manifests, then restores the previous value.
"""

from __future__ import annotations

import argparse
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

SOURCE_GEO_ID = "P_p100_h30"
SOURCE_MESH_ID = "max085_min006_cpg5_bl4_peel2"
SOURCE_RUN_ID = "u0p2_p6M"
SOURCE_FAMILY = "pillar"

REFERENCE_RUN_MANIFEST = Path(
    "C:/ro_data/runs/pillar/P_p100_h30/"
    "max085_min006_cpg5_bl4_peel2/u0p2_p6M/manifest.json"
)

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12

_GATE_FIELDS = ("u_target_ms", "p_gauge_pa", "inlet_bc_type")


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


def mesh_leaf(data_root):
    return (
        Path(data_root)
        / "meshes"
        / SOURCE_FAMILY
        / SOURCE_GEO_ID
        / SOURCE_MESH_ID
    )


def run_leaf(data_root):
    return (
        Path(data_root)
        / "runs"
        / SOURCE_FAMILY
        / SOURCE_GEO_ID
        / SOURCE_MESH_ID
        / SOURCE_RUN_ID
    )


def mesh_file(mesh_directory):
    return Path(mesh_directory) / f"{SOURCE_GEO_ID}_{SOURCE_MESH_ID}.msh.h5"


def require_mesh_leaf(mesh_directory):
    """Require the parity mesh manifest and hashed mesh file."""
    directory = Path(mesh_directory)
    manifest = directory / "manifest.json"
    mesh = mesh_file(directory)
    missing = [path for path in (manifest, mesh) if not path.is_file()]
    if missing:
        listed = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "Mesh leaf is missing the parity mesh manifest or .msh.h5: "
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
    previous = os.environ.get("RO_DATA_ROOT")
    os.environ["RO_DATA_ROOT"] = str(data_root)
    try:
        return fn()
    finally:
        if previous is None:
            os.environ.pop("RO_DATA_ROOT", None)
        else:
            os.environ["RO_DATA_ROOT"] = previous


def load_source_case():
    """Return the single production P_p100_h30 / max085 / u0p2_p6M case."""
    batchcfg = batch_solver_sweep._load_module(
        "batch_config_parity_solve",
        batch_solver_sweep.BATCH_CONFIG_PATH,
    )
    cases = [
        case
        for case in batchcfg.production_solver_sweep_cases
        if case.get("geo_id") == SOURCE_GEO_ID
        and case.get("mesh_id") == SOURCE_MESH_ID
        and case.get("run_id") == SOURCE_RUN_ID
    ]
    if len(cases) != 1:
        raise RuntimeError(
            "Expected exactly one production_solver_sweep_cases entry with "
            f"geo_id {SOURCE_GEO_ID!r}, mesh_id {SOURCE_MESH_ID!r}, "
            f"run_id {SOURCE_RUN_ID!r}, found {len(cases)}."
        )
    case = cases[0]
    if case.get("family") != SOURCE_FAMILY:
        raise RuntimeError(
            f"Source case family must be {SOURCE_FAMILY!r}, got {case.get('family')!r}."
        )
    common = getattr(batchcfg, "common_solver_settings", {})
    batch_solver_sweep.require_explicit_inlet_velocity_profile(common, [case])
    _base_case_name, case_name = batch_solver_sweep.resolve_case_names(case)
    # Same three assignments as batch_solver_sweep.main before the worker env.
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


def udf_name_for_overrides(overrides):
    """UDF file the worker will load: override, else run_config default."""
    if "udf_source_file_name" in overrides:
        return overrides["udf_source_file_name"]
    cfg = batch_solver_sweep._load_module(
        "run_config_parity_solve",
        batch_solver_sweep.project_root() / "configs" / "run_config.py",
    )
    return cfg.udf_source_file_name


def inlet_bc_type_from_overrides(overrides):
    if "use_inlet_velocity_profile" not in overrides:
        raise ValueError(
            "use_inlet_velocity_profile is not in the solver overrides."
        )
    flag = overrides["use_inlet_velocity_profile"]
    if not isinstance(flag, bool):
        raise TypeError(
            "use_inlet_velocity_profile must be a bool, "
            f"got {type(flag).__name__}."
        )
    return "parabolic" if flag else "plug"


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


def _solver_setting(manifest, key):
    settings = manifest.get("solver_settings")
    if not isinstance(settings, dict) or key not in settings:
        return None
    return settings[key]


def compare_operating_settings(manifest, overrides, *, udf_file_name):
    """Print operating settings. Raise only when the three gates differ."""
    if not isinstance(manifest, dict):
        raise TypeError(f"manifest must be a dict, got {type(manifest).__name__}.")
    sent = {
        "u_target_ms": overrides.get("inlet_velocity_value"),
        "p_gauge_pa": overrides.get("outlet_gauge_pressure"),
        "inlet_bc_type": inlet_bc_type_from_overrides(overrides),
        "max_iterations": overrides.get("max_iterations"),
        "residual_target": overrides.get("residual_target"),
        "operating_pressure": overrides.get("operating_pressure"),
        "udf_version": udf_file_name,
    }
    recorded = {
        "u_target_ms": manifest.get("u_target_ms"),
        "p_gauge_pa": manifest.get("p_gauge_pa"),
        "inlet_bc_type": manifest.get("inlet_bc_type"),
        "max_iterations": _solver_setting(manifest, "max_iterations"),
        "residual_target": _solver_setting(manifest, "residual_target"),
        "operating_pressure": _solver_setting(manifest, "operating_pressure"),
        "udf_version": manifest.get("udf_version"),
    }
    rows = []
    gate_mismatches = []
    for field in (
        "u_target_ms",
        "p_gauge_pa",
        "inlet_bc_type",
        "max_iterations",
        "residual_target",
        "operating_pressure",
        "udf_version",
    ):
        match = _values_equal(recorded[field], sent[field])
        row = {
            "field": field,
            "manifest_value": recorded[field],
            "override_value": sent[field],
            "status": "ok" if match else "diff",
            "gate": field in _GATE_FIELDS,
        }
        rows.append(row)
        if row["gate"] and row["status"] == "diff":
            gate_mismatches.append(row)
    _print_operating_table(rows)
    if gate_mismatches:
        detail = "; ".join(
            f"{row['field']}: manifest={row['manifest_value']!r} "
            f"override={row['override_value']!r}"
            for row in gate_mismatches
        )
        raise ValueError(
            "Operating point differs from the production run manifest: "
            f"{detail}"
        )
    return rows


def _print_operating_table(rows):
    print("Operating settings (production run manifest vs overrides to be sent):")
    print(f"{'field':<24} {'manifest':<28} {'override':<28} status")
    for row in rows:
        print(
            f"{row['field']:<24} {_cell(row['manifest_value']):<28} "
            f"{_cell(row['override_value']):<28} {row['status']}"
        )


def _cell(value):
    if isinstance(value, float):
        return f"{value:.12g}"
    if value is None:
        return ""
    return str(value)


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def solver_child_env(data_root, overrides):
    """Child env for the solver worker. Does not mutate the parent env."""
    env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
    env["RO_DATA_ROOT"] = str(data_root)
    env.pop("PYFLUENT_RUN_CONFIG", None)
    env.pop("PYFLUENT_SKIP_VALIDATION", None)
    return env


def launch_solver(overrides, data_root, run_directory, *, max_retries, settle_s):
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
        geo_id=SOURCE_GEO_ID,
        mesh_id=SOURCE_MESH_ID,
        run_id=SOURCE_RUN_ID,
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


def extract_launcher_paths():
    """Worker script and base post config, as batch_report_extract.main loads them."""
    return mfbo_common.extract_launcher_paths()


def extract_child_env(data_root, overrides, base_config):
    """Child env for report extraction. Does not mutate the parent env."""
    return mfbo_common.extract_child_env(data_root, overrides, base_config)


def launch_extract(data_root, run_directory, case):
    """Run pyfluent_report_extract.py the way batch_report_extract.py does."""
    return mfbo_common.launch_extract(
        data_root,
        run_directory,
        case,
        geo_id=SOURCE_GEO_ID,
        mesh_id=SOURCE_MESH_ID,
        run_id=SOURCE_RUN_ID,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Solve production P_p100_h30 / max085_min006_cpg5_bl4_peel2 / "
            "u0p2_p6M under a separate data root, then extract the new run."
        ),
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child processes. Not C:/ro_data.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    case, overrides, max_retries, settle_s = load_source_case()
    mesh_directory = mesh_leaf(data_root)
    run_directory = run_leaf(data_root)
    require_mesh_leaf(mesh_directory)
    refuse_existing_run(run_directory)

    reference = _read_json(REFERENCE_RUN_MANIFEST)
    compare_operating_settings(
        reference,
        overrides,
        udf_file_name=udf_name_for_overrides(overrides),
    )

    result = launch_solver(
        overrides,
        data_root,
        run_directory,
        max_retries=max_retries,
        settle_s=settle_s,
    )
    if not batch_solver_sweep.solver_worker_succeeded(result.returncode):
        return int(result.returncode) if result.returncode else 1

    extract_result = launch_extract(data_root, run_directory, case)
    if extract_result.returncode != 0:
        return int(extract_result.returncode) if extract_result.returncode else 1

    summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    print(f"Extract summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
