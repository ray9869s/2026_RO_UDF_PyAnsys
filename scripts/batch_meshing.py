# ==========================================================
# batch_meshing.py
# Sequential batch driver for meshing multiple geometries
# Location: My_CFD_Project/01_Scripts/batch_meshing.py
# Usage: python My_CFD_Project/01_Scripts/batch_meshing.py
# ==========================================================

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ro.campaign_matrix import (
    CASE_SET_CHOICES,
    CASE_SET_EXPLORATORY,
    cases_for_case_set,
)
from ro.mesh_common import (
    MESH_METRIC_NAMES,
    MESH_PARAMETER_NAMES,
    build_mesh_ledger_record,
    load_mesh_run_record,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_from_log,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
    write_mesh_run_record,
)
from ro.paths import data_root, mesh_dir, project_root
from ro.session_retry import (
    RETRY_KIND_SOCKET_RESET,
    is_session_socket_reset_failure,
)
from ro.solver_common import merge_batch_case_overrides

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_CONFIG_PATH = project_root() / "configs" / "batch_config.py"
BASE_RUN_CONFIG_PATH = project_root() / "configs" / "run_config.py"
MESHING_SCRIPT_PATH = SCRIPT_DIR / "meshing_code_260616.py"

# Narrow CAD-import contention signatures (Fluent Discovery / PartMgr).
CAD_ATTACH_ASSEMBLY_PATTERNS = (
    re.compile(r"AttachAssembly", re.IGNORECASE),
    re.compile(r"attaching to assembly failed", re.IGNORECASE),
    re.compile(r"Error in CAD Import", re.IGNORECASE),
    re.compile(r"pIPartMgr", re.IGNORECASE),
)

RETRY_KIND_CAD_ATTACH = "cad_attach_assembly"

# Process-image markers checked after a failed case settles.
LEFTOVER_PROCESS_MARKERS = (
    "fluent",
    "discovery",
    "cadreaders",
    "cadreader",
)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_overrides(case_dict, common_settings):
    """Merge common settings and one case entry into the worker override dict."""
    overrides = merge_batch_case_overrides(common_settings, case_dict)
    overrides["geo_name"] = overrides["geo_id"]
    if "case_name" not in overrides:
        overrides["case_name"] = overrides["mesh_id"]
    overrides.pop("mesh_case_name", None)
    return overrides


def _continue_on_failure(batchcfg):
    return getattr(batchcfg, "continue_on_failure", True)


def mesh_case_label(geo_id, mesh_id):
    return f"{geo_id}/{mesh_id}"


def parse_batch_meshing_cli(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Sequential meshing batch from configs/batch_config.py. "
            "Default --case-set exploratory uses mesh_batch_cases (22). "
            "Pass --case-set production for production_mesh_batch_cases (31)."
        ),
    )
    parser.add_argument(
        "--case-set",
        choices=CASE_SET_CHOICES,
        default=CASE_SET_EXPLORATORY,
        help=(
            "exploratory: mesh_batch_cases (22, no Diamond). "
            "production: production_mesh_batch_cases (31). "
            "Default exploratory so existing batch runs cannot launch the 31-mesh set."
        ),
    )
    parser.add_argument(
        "--geo-id",
        help="Run only selected case-set entries with this geo_id.",
    )
    parser.add_argument(
        "--mesh-id",
        help="Run only selected case-set entries with this mesh_id.",
    )
    return parser.parse_args(argv)


def select_mesh_batch_cases(cases, *, geo_id=None, mesh_id=None):
    """Return mesh_batch_cases entries matching optional geo_id / mesh_id filters."""
    selected = list(cases)
    if geo_id is not None:
        selected = [case for case in selected if case["geo_id"] == geo_id]
    if mesh_id is not None:
        selected = [case for case in selected if case["mesh_id"] == mesh_id]
    return selected


def classify_mesh_pre_execution(*, skip_existing_mesh, mesh_exists, dry_run):
    """Decide dry-run vs existing-mesh skip before Fluent is launched.

    Existing-mesh skip wins over dry-run so a dry-run summary can still
    show that skip_existing_mesh fired.
    """
    if skip_existing_mesh and mesh_exists:
        return "skipped_existing", "existing mesh"
    if dry_run:
        return "dry_run", None
    return "run", None


def is_cad_attach_assembly_failure(text):
    """True when failure text matches the Discovery AttachAssembly signature."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in CAD_ATTACH_ASSEMBLY_PATTERNS)


def classify_retryable_session_failure(text):
    """Return retry kind for session-contention failures, else None.

    CAD AttachAssembly and socket-reset are both retryable but kept as
    distinct kinds so logs and mesh_run_record stay distinguishable.
    """
    if is_cad_attach_assembly_failure(text):
        return RETRY_KIND_CAD_ATTACH
    if is_session_socket_reset_failure(text):
        return RETRY_KIND_SOCKET_RESET
    return None


def collect_session_failure_evidence(mesh_log_path, mesh_run_record_path):
    """Concatenate worker error_summary and mesh log for retry matching.

    pyfluent_watchdog.err is intentionally excluded: it is usually benign
    noise and must not drive retry classification.
    """
    chunks = []
    record = load_mesh_run_record(mesh_run_record_path)
    if record:
        error_summary = record.get("error_summary") or ""
        if error_summary:
            chunks.append(str(error_summary))
    mesh_log_path = Path(mesh_log_path)
    if mesh_log_path.is_file():
        try:
            chunks.append(
                mesh_log_path.read_text(encoding="utf-8", errors="ignore")
            )
        except OSError:
            pass
    return "\n".join(chunks)


# Backward-compatible alias used by older tests/imports.
collect_cad_failure_evidence = collect_session_failure_evidence


def cleanup_fm_scratch_dirs(mesh_directory):
    """Remove Fluent FM_<HOST>_<PID>/ scratch dirs under a mesh leaf.

    Returns the list of removed directory paths.
    """
    mesh_directory = Path(mesh_directory)
    removed = []
    if not mesh_directory.is_dir():
        return removed
    for child in sorted(mesh_directory.iterdir()):
        if child.is_dir() and child.name.startswith("FM_"):
            shutil.rmtree(child)
            removed.append(child)
            print(f"Removed Fluent scratch directory: {child}")
    return removed


def _process_line_matches_leftover(line):
    lowered = line.lower()
    return any(marker in lowered for marker in LEFTOVER_PROCESS_MARKERS)


def list_leftover_meshing_processes(*, runner=None):
    """Return leftover Fluent/Discovery/CADReaders process lines, if any."""
    if runner is None:
        runner = subprocess.run
    lines = []
    try:
        if os.name == "nt":
            result = runner(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            raw = result.stdout or ""
            for line in raw.splitlines():
                if _process_line_matches_leftover(line):
                    lines.append(line.strip())
        else:
            result = runner(
                ["ps", "-eo", "pid=,comm=,args="],
                capture_output=True,
                text=True,
                check=False,
            )
            raw = result.stdout or ""
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped and _process_line_matches_leftover(stripped):
                    lines.append(stripped)
    except OSError as exc:
        print(f"Warning: could not list leftover meshing processes: {exc}")
        return []
    return lines


def settle_before_next_case(
    *,
    previous_status,
    inter_case_delay_s,
    post_failure_settle_s,
    sleeper=time.sleep,
    process_lister=None,
):
    """Sleep and scan for leftovers before starting the next meshing case.

    After a non-SUCCESS case, always settle at least post_failure_settle_s
    (even when inter_case_delay_s is 0). Returns leftover process lines.
    """
    if process_lister is None:
        process_lister = list_leftover_meshing_processes

    previous_failed = previous_status not in ("SUCCESS", "SUCCESS_AFTER_RETRY")
    if previous_failed:
        delay_s = max(float(inter_case_delay_s), float(post_failure_settle_s))
        reason = "post-failure"
    else:
        delay_s = float(inter_case_delay_s)
        reason = "inter-case"

    if delay_s > 0.0:
        print(
            f"{reason.capitalize()} settle delay: {delay_s:g}s "
            f"(inter_case_delay_s={inter_case_delay_s:g}, "
            f"post_failure_settle_s={post_failure_settle_s:g})."
        )
        sleeper(delay_s)

    leftovers = process_lister()
    if leftovers:
        print(
            "WARNING: leftover Fluent/Discovery/CADReaders processes still "
            f"present after {reason} settle ({len(leftovers)}):"
        )
        for line in leftovers:
            print(f"  LEFTOVER: {line}")
    elif previous_failed:
        print("No leftover Fluent/Discovery/CADReaders processes after settle.")
    return leftovers


def record_session_attempt_metadata(
    mesh_run_record_path,
    *,
    attempts,
    status,
    retry_kinds=None,
    prior_case_leftover_processes=None,
):
    """Persist attempt/retry metadata on the worker mesh_run_record.json."""
    path = Path(mesh_run_record_path)
    record = load_mesh_run_record(path) or {}
    attempts = int(attempts)
    retry_kinds = list(retry_kinds or [])
    record["transient_failure_attempts"] = attempts
    # Backward-compatible alias used by earlier batch summaries.
    record["cad_import_attempts"] = attempts
    record["succeeded_on_transient_retry"] = status == "SUCCESS_AFTER_RETRY"
    record["succeeded_on_cad_import_retry"] = status == "SUCCESS_AFTER_RETRY"
    record["retry_reasons"] = retry_kinds
    record["retry_reason"] = retry_kinds[-1] if retry_kinds else None
    record["session_retry_kinds"] = retry_kinds
    if prior_case_leftover_processes:
        record["prior_case_leftover_processes"] = list(
            prior_case_leftover_processes
        )
        record["prior_case_leftovers_detected"] = True
    write_mesh_run_record(path, record)
    return record


# Backward-compatible alias.
record_cad_import_attempts = record_session_attempt_metadata


def run_meshing_attempts(
    *,
    cmd,
    env,
    cwd,
    mesh_log_path,
    mesh_run_record_path,
    max_retries,
    runner=None,
):
    """Run the meshing worker, retrying CAD AttachAssembly / socket-reset.

    max_retries is the number of *retries* after the first attempt (default 2
    means up to 3 total invocations).

    Returns (result, attempts, retry_kinds).
    """
    if runner is None:
        runner = subprocess.run
    max_retries = max(0, int(max_retries))
    max_attempts = max_retries + 1
    result = None
    retry_kinds = []
    for attempt in range(1, max_attempts + 1):
        print(
            f"Meshing worker attempt {attempt}/{max_attempts}: "
            f"{' '.join(cmd)}"
        )
        result = runner(cmd, env=env, cwd=cwd, check=False)
        if result.returncode == 0:
            return result, attempt, retry_kinds

        evidence = collect_session_failure_evidence(
            mesh_log_path, mesh_run_record_path
        )
        retry_kind = classify_retryable_session_failure(evidence)
        can_retry = attempt < max_attempts and retry_kind is not None
        if can_retry:
            retry_kinds.append(retry_kind)
            if retry_kind == RETRY_KIND_CAD_ATTACH:
                print(
                    f"CAD AttachAssembly / Import failure on attempt {attempt}; "
                    f"retrying ({max_attempts - attempt} retry left)."
                )
            else:
                print(
                    f"Session socket-reset failure on attempt {attempt}; "
                    f"retrying ({max_attempts - attempt} retry left)."
                )
            continue

        if attempt < max_attempts:
            print(
                f"Non-retryable meshing failure on attempt {attempt} "
                f"(return code {result.returncode}); not retrying."
            )
        return result, attempt, retry_kinds

    return result, max_attempts, retry_kinds


def _format_summary_case_line(entry):
    label = entry["label"]
    status = entry.get("status", "")
    attempts = entry.get("attempts")
    wall = entry.get("wall_time_seconds")
    cell_count = entry.get("cell_count")
    attempts_s = "-" if attempts is None else str(attempts)
    wall_s = "-" if wall is None else f"{float(wall):.1f}s"
    cell_s = "-" if cell_count is None else str(cell_count)
    return (
        f"    {label}  status={status}  attempts={attempts_s}  "
        f"wall={wall_s}  cell_count={cell_s}"
    )


def _print_batch_summary(dry_run_cases, skipped_existing, successes, failures):
    print(f"\n{'='*72}")
    print("BATCH MESHING SUMMARY")
    print(f"{'='*72}")
    print(f"  dry_run          : {len(dry_run_cases)}")
    print(f"  skipped_existing : {len(skipped_existing)}")
    print(f"  succeeded        : {len(successes)}")
    print(f"  failed           : {len(failures)}")
    if dry_run_cases:
        print("  Dry-run cases:")
        for label in dry_run_cases:
            print(f"    {label}")
    if skipped_existing:
        print("  Skipped existing:")
        for label, reason in skipped_existing:
            print(f"    {label}  ({reason})")
    if successes:
        print("  Succeeded cases:")
        for entry in successes:
            if isinstance(entry, dict):
                print(_format_summary_case_line(entry))
            else:
                print(f"    {entry}")
    if failures:
        print("  FAILED cases:")
        for entry in failures:
            if isinstance(entry, dict):
                print(_format_summary_case_line(entry))
            else:
                print(f"    {entry}")
    print(f"{'='*72}\n")


def _resolved_mesh_parameters(base_cfg, overrides):
    values = {
        name: getattr(base_cfg, name, None)
        for name in MESH_PARAMETER_NAMES
    }
    values.update(overrides)
    return mesh_parameters_from_mapping(values)


def _write_case_ledger(
    *,
    ledger_path,
    geo_name,
    mesh_case_name,
    mesh_parameters,
    status,
    exit_code,
    wall_time_seconds,
    mesh_log_path,
    mesh_file_path,
    error_summary="",
):
    """Merge worker/log details and upsert one aggregate ledger row."""
    mesh_log_path = Path(mesh_log_path)
    worker_record_path = mesh_log_path.parent / "mesh_run_record.json"
    worker_record = load_mesh_run_record(worker_record_path)

    actual_parameters = dict(mesh_parameters)
    metrics = parse_mesh_metrics_from_log(mesh_log_path)
    if mesh_log_path.is_file():
        text = mesh_log_path.read_text(encoding="utf-8", errors="ignore")
        parsed_parameters = parse_meshing_input_summary(text)
        actual_parameters.update({
            key: value
            for key, value in parsed_parameters.items()
            if value is not None
        })

        if worker_record:
            actual_parameters.update({
                name: worker_record.get(name)
                for name in MESH_PARAMETER_NAMES
                if worker_record.get(name) is not None
            })
            metrics.update({
                name: worker_record.get(name)
                for name in MESH_METRIC_NAMES
                if worker_record.get(name) is not None
            })
            worker_error = worker_record.get("error_summary") or ""
            if worker_error:
                error_summary = worker_error

    record = build_mesh_ledger_record(
        geo_name=geo_name,
        mesh_case_name=mesh_case_name,
        mesh_parameters=actual_parameters,
        status=status,
        exit_code=exit_code,
        wall_time_seconds=wall_time_seconds,
        metrics=metrics,
        mesh_log_path=mesh_log_path,
        mesh_file_path=mesh_file_path,
        error_summary=error_summary,
    )
    upsert_mesh_ledger_csv(ledger_path, [record])
    return record


def main(argv=None):
    cli_args = parse_batch_meshing_cli(argv)
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = _continue_on_failure(batchcfg)
    skip_existing_mesh = getattr(batchcfg, "skip_existing_mesh", True)
    inter_case_delay_s = float(getattr(batchcfg, "inter_case_delay_s", 0.0))
    post_failure_settle_s = float(
        getattr(batchcfg, "post_failure_settle_s", 15.0)
    )
    # New name covers CAD AttachAssembly and socket-reset; keep reading the
    # old key so existing batch_config files still work.
    transient_failure_max_retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            getattr(batchcfg, "cad_import_max_retries", 2),
        )
    )
    clean_fm_scratch_on_success = bool(
        getattr(batchcfg, "clean_fm_scratch_on_success", True)
    )
    common_mesh_settings = getattr(batchcfg, "common_mesh_settings", {})
    mesh_batch_cases = select_mesh_batch_cases(
        cases_for_case_set(
            batchcfg,
            cli_args.case_set,
            exploratory_attr="mesh_batch_cases",
            production_attr="production_mesh_batch_cases",
        ),
        geo_id=cli_args.geo_id,
        mesh_id=cli_args.mesh_id,
    )
    if cli_args.geo_id is not None or cli_args.mesh_id is not None:
        if not mesh_batch_cases:
            filters = []
            if cli_args.geo_id is not None:
                filters.append(f"geo_id={cli_args.geo_id!r}")
            if cli_args.mesh_id is not None:
                filters.append(f"mesh_id={cli_args.mesh_id!r}")
            raise SystemExit(
                f"No mesh_batch_cases match {' and '.join(filters)}."
            )

    base_cfg = _load_module("_base_cfg", BASE_RUN_CONFIG_PATH)
    ledger_path = data_root() / "inventory" / "mesh_ledger.csv"

    successes = []
    failures = []
    dry_run_cases = []
    skipped_existing = []
    executed_case_count = 0
    previous_status = None
    pending_leftovers = None

    total = len(mesh_batch_cases)
    print(f"\n{'='*72}")
    print(f"BATCH MESHING: {total} case(s)  case_set={cli_args.case_set}")
    print(
        f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  "
        f"skip_existing_mesh={skip_existing_mesh}"
    )
    print(
        f"inter_case_delay_s={inter_case_delay_s}  "
        f"post_failure_settle_s={post_failure_settle_s}  "
        f"transient_failure_max_retries={transient_failure_max_retries}  "
        f"clean_fm_scratch_on_success={clean_fm_scratch_on_success}"
    )
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(mesh_batch_cases):
        family = case_dict["family"]
        geo_id = case_dict["geo_id"]
        mesh_id = case_dict["mesh_id"]
        label = mesh_case_label(geo_id, mesh_id)
        overrides = _build_overrides(case_dict, common_mesh_settings)
        mesh_parameters = _resolved_mesh_parameters(base_cfg, overrides)

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")

        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        expected_mesh = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
        mesh_log_path = mesh_directory / f"mesh_log_{mesh_id}.txt"
        mesh_run_record_path = mesh_directory / "mesh_run_record.json"
        print(f"Expected mesh output: {expected_mesh}")

        outcome, skip_reason = classify_mesh_pre_execution(
            skip_existing_mesh=skip_existing_mesh,
            mesh_exists=os.path.isfile(expected_mesh),
            dry_run=dry_run,
        )
        if outcome == "skipped_existing":
            print(f"SKIP: {skip_reason}: {expected_mesh}")
            skipped_existing.append((label, skip_reason))
            _write_case_ledger(
                ledger_path=ledger_path,
                geo_name=geo_id,
                mesh_case_name=mesh_id,
                mesh_parameters=mesh_parameters,
                status="SKIPPED_EXISTING",
                exit_code=None,
                wall_time_seconds=0.0,
                mesh_log_path=mesh_log_path,
                mesh_file_path=expected_mesh,
            )
            continue

        print(f"Overrides: {json.dumps(overrides, indent=2)}")

        cmd = [sys.executable, str(MESHING_SCRIPT_PATH)]
        env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
        # The worker must load the base run_config.py, not a leftover env config.
        env.pop("PYFLUENT_RUN_CONFIG", None)
        env.pop("PYFLUENT_SKIP_VALIDATION", None)

        print(f"Command: {' '.join(cmd)}")

        if outcome == "dry_run":
            print("[DRY RUN] Skipping Fluent execution.")
            dry_run_cases.append(label)
            continue

        prior_leftovers_for_record = None
        if executed_case_count > 0 and previous_status is not None:
            leftovers = settle_before_next_case(
                previous_status=previous_status,
                inter_case_delay_s=inter_case_delay_s,
                post_failure_settle_s=post_failure_settle_s,
            )
            if leftovers:
                prior_leftovers_for_record = leftovers
            pending_leftovers = leftovers

        started = time.monotonic()
        result, attempts, retry_kinds = run_meshing_attempts(
            cmd=cmd,
            env=env,
            cwd=str(SCRIPT_DIR),
            mesh_log_path=mesh_log_path,
            mesh_run_record_path=mesh_run_record_path,
            max_retries=transient_failure_max_retries,
        )
        wall_time_seconds = time.monotonic() - started
        executed_case_count += 1

        if result.returncode == 0:
            status = (
                "SUCCESS_AFTER_RETRY" if attempts > 1 else "SUCCESS"
            )
            print(
                f"\nSUCCESS: {label} (return code {result.returncode}, "
                f"attempts={attempts}, status={status})"
            )
        else:
            status = "FAILED"
            print(
                f"\nFAILED: {label} (return code {result.returncode}, "
                f"attempts={attempts})"
            )

        record_session_attempt_metadata(
            mesh_run_record_path,
            attempts=attempts,
            status=status,
            retry_kinds=retry_kinds,
            prior_case_leftover_processes=prior_leftovers_for_record,
        )

        ledger_record = _write_case_ledger(
            ledger_path=ledger_path,
            geo_name=geo_id,
            mesh_case_name=mesh_id,
            mesh_parameters=mesh_parameters,
            status=status,
            exit_code=result.returncode,
            wall_time_seconds=wall_time_seconds,
            mesh_log_path=mesh_log_path,
            mesh_file_path=expected_mesh,
            error_summary=(
                ""
                if result.returncode == 0
                else f"meshing worker exited with code {result.returncode}"
            ),
        )

        summary_entry = {
            "label": label,
            "status": status,
            "attempts": attempts,
            "wall_time_seconds": wall_time_seconds,
            "cell_count": ledger_record.get("cell_count"),
        }

        previous_status = status
        if result.returncode == 0:
            successes.append(summary_entry)
            if clean_fm_scratch_on_success:
                cleanup_fm_scratch_dirs(mesh_directory)
        else:
            failures.append(summary_entry)
            if not continue_on_failure:
                print("Stopping batch because continue_on_failure=False.")
                break

    _print_batch_summary(dry_run_cases, skipped_existing, successes, failures)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
