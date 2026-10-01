"""Re-solve one production campaign case under a separate data root.

Copies the production mesh leaf (never moves it) and launches the production
solver and report extraction. ``RO_DATA_ROOT`` is set on those children only.
The production tree ``C:/ro_data`` is read, not written.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_report_extract
import batch_solver_sweep
import mfbo._common as mfbo_common
import mfbo.run_parity_mesh as parity_mesh
from ro.convergence_quality import (
    continuity_final_from_case_dir,
    evaluate_convergence_quality,
)
from ro.geometry_registry import family_for_known_geo_id
from ro.residual_transcript import (
    PARSE_OK,
    read_text_replace,
    select_solve_transcript,
    transcript_max_iteration,
)

PRODUCTION_DATA_ROOT = Path("C:/ro_data")
VISCOUS_MODELS = (
    "laminar",
    "k-omega-sst",
    "k-epsilon-realizable-ewt",
)
_VISCOUS_RUN_SUFFIX = {
    "laminar": "",
    "k-omega-sst": "_sst",
    "k-epsilon-realizable-ewt": "_rke",
}
_BASE_CASE_RE = re.compile(r"^u\d+p\d+_p\d+M$")
SUMMARY_FIELDS = (
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cp_canon_window_avg",
    "cp_canon_window_max",
)
REPORT_FIELDS = (
    "stop_reason",
    "final_iteration",
    "continuity_final",
    "convergence_quality",
    "failures",
    *SUMMARY_FIELDS,
    "viscous_model",
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
    """Require an unsuffixed production run id such as ``u0p3_p6M``."""
    if not isinstance(case, str) or _BASE_CASE_RE.fullmatch(case) is None:
        raise ValueError(
            f"case must be a base run id such as u0p3_p6M, got {case!r}."
        )
    return case


def run_id_for_viscous_model(case, viscous_model):
    """Laminar keeps the base id. SST and realizable k-epsilon add a suffix."""
    try:
        suffix = _VISCOUS_RUN_SUFFIX[viscous_model]
    except KeyError as exc:
        raise ValueError(
            f"viscous_model must be one of {VISCOUS_MODELS}, got {viscous_model!r}."
        ) from exc
    return f"{case}{suffix}"


def apply_viscous_overrides(overrides, case, viscous_model, turbulence_residual_target):
    """Return solver overrides. Laminar leaves the production case unchanged."""
    run_id = run_id_for_viscous_model(case, viscous_model)
    if viscous_model == "laminar":
        if turbulence_residual_target is not None:
            raise ValueError(
                "turbulence_residual_target is only allowed when "
                "viscous_model is not laminar."
            )
        return overrides, run_id
    if turbulence_residual_target is None:
        raise ValueError(
            "turbulence_residual_target is required when viscous_model "
            f"is {viscous_model!r}."
        )
    if isinstance(turbulence_residual_target, bool) or not isinstance(
        turbulence_residual_target, (int, float)
    ):
        raise TypeError(
            "turbulence_residual_target must be a positive number, "
            f"got {turbulence_residual_target!r}."
        )
    if turbulence_residual_target <= 0:
        raise ValueError(
            "turbulence_residual_target must be positive, "
            f"got {turbulence_residual_target!r}."
        )
    updated = dict(overrides)
    updated["run_id"] = run_id
    updated["case_name"] = run_id
    updated["viscous_model"] = viscous_model
    updated["turbulence_residual_target"] = float(turbulence_residual_target)
    return updated, run_id


def production_mesh_leaf(family, geo_id, mesh_id):
    return PRODUCTION_DATA_ROOT / "meshes" / family / geo_id / mesh_id


def production_run_leaf(family, geo_id, mesh_id, case):
    return PRODUCTION_DATA_ROOT / "runs" / family / geo_id / mesh_id / case


def mesh_leaf(data_root, family, geo_id, mesh_id):
    return Path(data_root) / "meshes" / family / geo_id / mesh_id


def run_leaf(data_root, family, geo_id, mesh_id, run_id):
    return Path(data_root) / "runs" / family / geo_id / mesh_id / run_id


def relative_files(directory):
    """Sorted relative paths of every file under ``directory``."""
    root = Path(directory)
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def sha256_map(directory):
    """SHA-256 of every file under ``directory``, keyed by relative path."""
    root = Path(directory)
    return {
        relative: parity_mesh.sha256_file(root / relative)
        for relative in relative_files(root)
    }


def copy_or_reuse_mesh_leaf(source, dest):
    """Copy a mesh leaf, or reuse it when every file sha256 already matches.

    Copy only: the source tree is not moved or deleted. An existing destination
    with any different file set or hash is refused, not overwritten.
    """
    source = Path(source)
    dest = Path(dest)
    if not source.is_dir():
        raise FileNotFoundError(f"Production mesh leaf not found: {source}")
    source_hashes = sha256_map(source)
    if not source_hashes:
        raise FileNotFoundError(f"Production mesh leaf has no files: {source}")
    if dest.exists():
        if not dest.is_dir():
            raise FileExistsError(
                f"Refusing to overwrite mesh leaf path that is not a directory: {dest}"
            )
        dest_hashes = sha256_map(dest)
        if dest_hashes != source_hashes:
            raise RuntimeError(
                "Mesh leaf already exists and sha256s are not identical: "
                f"{dest}."
            )
        print(
            f"Reusing mesh leaf {dest} "
            f"({len(dest_hashes)} files, sha256 match)"
        )
        return dest_hashes
    for relative, digest in source_hashes.items():
        written = parity_mesh.copy_geometry_file(source / relative, dest / relative)
        if written != digest:
            raise RuntimeError(
                f"Copied mesh file sha256 changed for {relative}: "
                f"{digest} -> {written}."
            )
    if not source.is_dir() or sha256_map(source) != source_hashes:
        raise RuntimeError(f"Production mesh source changed during copy: {source}")
    print(f"Copied {len(source_hashes)} mesh files {source} -> {dest}")
    return source_hashes


def refuse_existing_run(run_directory):
    directory = Path(run_directory)
    if directory.exists():
        raise FileExistsError(
            f"Refusing to solve because the run leaf already exists: {directory}."
        )


def load_template(geo_id, mesh_id, case):
    """The one production_solver_sweep_cases entry, merged like the batch."""
    batchcfg = batch_solver_sweep._load_module(
        "batch_config_campaign_solve",
        batch_solver_sweep.BATCH_CONFIG_PATH,
    )
    cases = [
        entry
        for entry in batchcfg.production_solver_sweep_cases
        if entry.get("geo_id") == geo_id
        and entry.get("mesh_id") == mesh_id
        and entry.get("run_id") == case
    ]
    if len(cases) != 1:
        raise RuntimeError(
            "Expected exactly one production_solver_sweep_cases entry with "
            f"geo_id {geo_id!r}, mesh_id {mesh_id!r}, run_id {case!r}, "
            f"found {len(cases)}."
        )
    entry = cases[0]
    family = entry.get("family")
    expected_family = family_for_known_geo_id(geo_id)
    if family != expected_family:
        raise RuntimeError(
            f"Source case family must be {expected_family!r}, got {family!r}."
        )
    common = getattr(batchcfg, "common_solver_settings", {})
    batch_solver_sweep.require_explicit_inlet_velocity_profile(common, [entry])
    _base_case_name, case_name = batch_solver_sweep.resolve_case_names(entry)
    overrides = batch_solver_sweep.merge_batch_case_overrides(common, entry)
    overrides["geo_name"] = entry["geo_id"]
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
    return entry, overrides, retries, settle_s


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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def read_summary_wide(path):
    path = Path(path)
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: {path}"
        )
    return rows[0]


def final_iteration_from_case_dir(case_dir):
    path, status, _detail = select_solve_transcript(Path(case_dir))
    if path is None or status != PARSE_OK:
        return None
    text, _err = read_text_replace(path)
    if text is None:
        return None
    return transcript_max_iteration(text)


def report_from_run(run_directory):
    """Read manifest, summary, and transcript fields. Does not write."""
    directory = Path(run_directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_json(manifest_path)
    summary = read_summary_wide(directory / "post" / "reports" / "summary_metrics_wide.csv")
    summary = summary or {}
    continuity = manifest.get("continuity_final")
    if continuity is None:
        continuity = continuity_final_from_case_dir(directory)
    quality = manifest.get("convergence_quality")
    failures = manifest.get("convergence_quality_failures")
    if quality is None:
        evaluated = evaluate_convergence_quality(
            summary,
            continuity_final=continuity,
        )
        quality = evaluated["convergence_quality"]
        failures = evaluated["failures"]
        if continuity is None:
            continuity = evaluated["continuity_final"]
    settings = manifest.get("solver_settings")
    viscous_model = None
    if isinstance(settings, dict):
        viscous_model = settings.get("viscous_model")
    report = {
        "stop_reason": manifest.get("stop_reason"),
        "final_iteration": final_iteration_from_case_dir(directory),
        "continuity_final": continuity,
        "convergence_quality": quality,
        "failures": failures,
        "viscous_model": viscous_model,
    }
    for key in SUMMARY_FIELDS:
        report[key] = summary.get(key)
    return report


def _cell(value):
    if value is None:
        return "null"
    return str(value)


def print_side_by_side(solve_report, production_report, production_path):
    print("Campaign re-solve vs production laminar run:")
    if production_report is None:
        print(f"  production laminar run not found: {production_path}")
    print(f"  {'field':<32} {'solve':<36} production")
    for key in REPORT_FIELDS:
        production_value = (
            "absent" if production_report is None else production_report.get(key)
        )
        print(
            f"  {key:<32} {_cell(solve_report.get(key)):<36} "
            f"{_cell(production_value)}"
        )


def _exit_code(returncode):
    if returncode:
        return int(returncode)
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Re-solve one production campaign case under a separate data root."
        ),
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child processes. Not C:/ro_data.",
    )
    parser.add_argument("--geo-id", required=True, help="Campaign geo_id.")
    parser.add_argument("--mesh-id", required=True, help="Production mesh id.")
    parser.add_argument(
        "--case",
        required=True,
        help="Base run id, for example u0p3_p6M.",
    )
    parser.add_argument(
        "--viscous-model",
        default="laminar",
        choices=VISCOUS_MODELS,
        help="Viscous model. Default laminar keeps the base run id.",
    )
    parser.add_argument(
        "--turbulence-residual-target",
        type=float,
        default=None,
        help="Required when --viscous-model is not laminar.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    case_id = require_base_case(args.case)
    entry, overrides, max_retries, settle_s = load_template(
        args.geo_id,
        args.mesh_id,
        case_id,
    )
    family = entry["family"]
    overrides, run_id = apply_viscous_overrides(
        overrides,
        case_id,
        args.viscous_model,
        args.turbulence_residual_target,
    )
    source_mesh = production_mesh_leaf(family, args.geo_id, args.mesh_id)
    dest_mesh = mesh_leaf(data_root, family, args.geo_id, args.mesh_id)
    copy_or_reuse_mesh_leaf(source_mesh, dest_mesh)
    run_directory = run_leaf(data_root, family, args.geo_id, args.mesh_id, run_id)
    refuse_existing_run(run_directory)

    result = launch_solver(
        overrides,
        data_root,
        run_directory,
        geo_id=args.geo_id,
        mesh_id=args.mesh_id,
        run_id=run_id,
        max_retries=max_retries,
        settle_s=settle_s,
    )
    if not batch_solver_sweep.solver_worker_succeeded(result.returncode):
        return _exit_code(result.returncode)

    extract_result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        entry,
        geo_id=args.geo_id,
        mesh_id=args.mesh_id,
        run_id=run_id,
    )
    if extract_result.returncode != 0:
        return _exit_code(extract_result.returncode)

    summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1

    solve_report = report_from_run(run_directory)
    if solve_report is None:
        raise FileNotFoundError(f"Run manifest missing after extract: {run_directory}")
    production_path = production_run_leaf(family, args.geo_id, args.mesh_id, case_id)
    production_report = None
    if production_path.is_dir():
        production_report = report_from_run(production_path)
    print_side_by_side(solve_report, production_report, production_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
