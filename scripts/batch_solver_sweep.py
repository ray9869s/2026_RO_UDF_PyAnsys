# ==========================================================
# batch_solver_sweep.py
# Sequential batch driver for solver parameter sweeps
# Location: My_CFD_Project/01_Scripts/batch_solver_sweep.py
# Usage: python My_CFD_Project/01_Scripts/batch_solver_sweep.py
# ==========================================================

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ro.campaign_geo_ids import assert_selected_cases_are_not_legacy_ml
from ro.campaign_matrix import (
    CASE_SET_CHOICES,
    CASE_SET_EXPLORATORY,
    cases_for_case_set,
    filter_cases_by_geo_id as filter_solver_cases_by_geo_id,
    filter_cases_by_outlet_gauge_pressure as filter_solver_cases_by_outlet_gauge_pressure,
)
from ro.manifest import ManifestError, read_run_manifest
from ro.paths import mesh_dir, project_root, run_dir
from ro.session_retry import (
    classify_retryable_session_crash,
    describe_attempt_orphans,
)
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    SOLVER_SKIP_ALLOWED_STOP_REASONS,
    collect_solver_final_artifact_failures,
    describe_solver_worker_failure,
    make_base_case_name,
    merge_batch_case_overrides,
    pressure_to_case_token,
    resolve_case_names,
    resolve_input_mode,
    sha256_file,
    solver_worker_succeeded,
    velocity_to_case_token,
)

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_CONFIG_PATH = project_root() / "configs" / "batch_config.py"
SOLVER_SCRIPT_PATH = SCRIPT_DIR / "solver_code_260616.py"
SOLVER_TRANSIENT_FAILURE_MAX_RETRIES = 2
SOLVER_POST_FAILURE_SETTLE_S = 15.0


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_INLET_PROFILE_FLAG = "use_inlet_velocity_profile"


def solver_case_label(geo_id, mesh_id, run_id):
    return f"{geo_id}/{mesh_id}/{run_id}"


def format_selected_solver_case(index, total, case):
    """One four-id line for the pre-Fluent case list."""
    family = case.get("family") or "?"
    geo_id = case.get("geo_id") or "?"
    mesh_id = case.get("mesh_id") or "?"
    run_id = case.get("run_id") or "?"
    return f"  {index}/{total}  {family}/{geo_id}/{mesh_id}/{run_id}"


def solver_skip_block_reason(
    run_directory,
    final_case_path,
    final_data_path,
    mesh_file_path,
):
    """Return None when this leaf may be skipped; else why it must run.

    Skip evidence is the pair of SHA-256 digests stamped onto the run
    manifest only after this attempt's canonical write_case_data succeeded,
    plus the attempt id assigned before launch. A previous attempt's
    nonempty finals cannot satisfy those hashes: a new worker start rewrites
    the manifest without them, and a terminal stop_reason written before a
    failed write never restamps them.
    """
    artifact_failures = collect_solver_final_artifact_failures(
        final_case_path,
        final_data_path,
    )
    if artifact_failures:
        return artifact_failures[0]
    try:
        payload = read_run_manifest(run_directory)
    except (OSError, ManifestError) as exc:
        return f"run manifest not usable for skip: {exc}"
    stop_reason = payload.get("stop_reason")
    if stop_reason not in SOLVER_SKIP_ALLOWED_STOP_REASONS:
        return f"stop_reason {stop_reason!r} is not a skip-complete reason"
    attempt_id = payload.get(SOLVER_ATTEMPT_ID_FIELD)
    if not attempt_id:
        return "missing solver_attempt_id"
    case_sha = payload.get(FINAL_CASE_SHA256_FIELD)
    data_sha = payload.get(FINAL_DATA_SHA256_FIELD)
    if not case_sha or not data_sha:
        return "missing current-attempt final artifact hashes"
    if sha256_file(final_case_path) != case_sha:
        return "final case sha256 does not match run manifest"
    if sha256_file(final_data_path) != data_sha:
        return "final data sha256 does not match run manifest"
    try:
        mesh_sha = sha256_file(mesh_file_path)
    except OSError as exc:
        return f"mesh file not readable for skip: {exc}"
    if payload.get("mesh_sha256") != mesh_sha:
        return "run mesh_sha256 does not match current mesh file"
    return None


def solver_attempt_log_path(run_directory, geo_id, mesh_id, run_id, attempt):
    """Driver log for one solver worker attempt; mesh_id and attempt are required."""
    return Path(run_directory) / (
        f"{geo_id}__{mesh_id}__{run_id}__solver_attempt{int(attempt)}.log"
    )


def solver_worker_log_paths(run_directory, case_name):
    run_directory = Path(run_directory)
    return (
        run_directory / f"solver_log_{case_name}.txt",
        run_directory / f"solver_mesh_replace_log_{case_name}.txt",
    )


def collect_solver_attempt_evidence(log_path, extra_paths=()):
    """Read this attempt's driver log and current worker transcripts only.

    Older attempt logs are not included: a leftover socket string must not
    make a later UDF/G/inlet/manifest failure look retryable.
    """
    chunks = []
    for path in (Path(log_path),) + tuple(Path(p) for p in extra_paths):
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return "\n".join(chunks)


def preserve_solver_worker_logs(run_directory, case_name, attempt):
    """Rename canonical worker logs so the next attempt cannot overwrite them."""
    preserved = []
    for src in solver_worker_log_paths(run_directory, case_name):
        if not src.is_file():
            continue
        dest = src.with_name(f"{src.stem}__attempt{int(attempt)}{src.suffix}")
        src.replace(dest)
        preserved.append(dest)
    return preserved


def write_solver_retry_record(
    run_directory,
    *,
    attempts,
    retry_kinds,
    attempt_logs,
):
    directory = Path(run_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "solver_retry_record.json"
    payload = {
        "transient_failure_attempts": int(attempts),
        "retry_kinds": list(retry_kinds or []),
        "attempt_logs": [str(p) for p in attempt_logs],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def run_solver_worker_tee(cmd, env, cwd, log_path):
    """Run the solver worker, teeing stdout/stderr to an attempt-specific log."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
        returncode = proc.wait()
    return subprocess.CompletedProcess(cmd, returncode)


def run_solver_attempts(
    *,
    cmd,
    env,
    cwd,
    run_directory,
    geo_id,
    mesh_id,
    run_id,
    case_name,
    max_retries,
    settle_s=0.0,
    runner=None,
    sleeper=None,
    process_lister=None,
):
    """Run the solver worker, retrying Fluent session deaths.

    max_retries is retries after the first attempt (default 2 → 3 total).
    Each attempt is a new subprocess. Skip is not re-evaluated here: leftover
    finals from a failed attempt are not skip-complete under R-02, and this
    loop must not consult skip.

    Returns (result, attempts, retry_kinds, attempt_logs).
    """
    if runner is None:
        runner = run_solver_worker_tee
    if sleeper is None:
        sleeper = time.sleep
    max_retries = max(0, int(max_retries))
    max_attempts = max_retries + 1
    retry_kinds = []
    attempt_logs = []
    result = None
    run_directory = Path(run_directory)
    for attempt in range(1, max_attempts + 1):
        log_path = solver_attempt_log_path(
            run_directory, geo_id, mesh_id, run_id, attempt
        )
        attempt_logs.append(log_path)
        print(
            f"Solver worker attempt {attempt}/{max_attempts}: "
            f"{' '.join(cmd)}"
        )
        print(f"Attempt log: {log_path}")
        result = runner(cmd, env=env, cwd=cwd, log_path=log_path)
        if solver_worker_succeeded(result.returncode):
            return result, attempt, retry_kinds, attempt_logs

        evidence = collect_solver_attempt_evidence(
            log_path,
            solver_worker_log_paths(run_directory, case_name),
        )
        for line in describe_attempt_orphans(
            evidence, process_lister=process_lister
        ):
            print(line)
        preserve_solver_worker_logs(run_directory, case_name, attempt)

        retry_kind = classify_retryable_session_crash(evidence)
        can_retry = attempt < max_attempts and retry_kind is not None
        if can_retry:
            retry_kinds.append(retry_kind)
            remaining = max_attempts - attempt
            print(
                f"Solver: transient {retry_kind} on attempt {attempt}; "
                f"retrying after {float(settle_s):g}s ({remaining} retry left)."
            )
            if settle_s > 0.0:
                sleeper(float(settle_s))
            continue
        if attempt < max_attempts:
            print(
                f"Solver: non-retryable failure on attempt {attempt} "
                f"(return code {result.returncode}); not retrying."
            )
        return result, attempt, retry_kinds, attempt_logs
    return result, max_attempts, retry_kinds, attempt_logs


def classify_solver_pre_execution(
    *,
    skip_existing_final_data,
    dry_run,
    run_directory,
    final_case_path,
    final_data_path,
    mesh_file_path,
):
    """Decide dry-run vs current-attempt skip before Fluent is launched.

    Existing-final skip wins over dry-run only when current-attempt
    completion evidence is present. File existence alone is not enough.
    """
    skip_block = None
    if skip_existing_final_data:
        skip_block = solver_skip_block_reason(
            run_directory,
            final_case_path,
            final_data_path,
            mesh_file_path,
        )
        if skip_block is None:
            return "skipped_existing", "complete current-attempt finals"
    if dry_run:
        return "dry_run", skip_block
    return "run", skip_block


def _print_batch_summary(dry_run_cases, skipped_existing, successes, failures):
    print(f"\n{'='*72}")
    print("BATCH SOLVER SWEEP SUMMARY")
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
        for label in successes:
            print(f"    {label}")
    if failures:
        print("  FAILED cases:")
        for label in failures:
            print(f"    {label}")
    print(f"{'='*72}\n")


def require_explicit_inlet_velocity_profile(
    common_solver_settings,
    solver_sweep_cases=None,
):
    """Refuse a sweep that would inherit run_config's plug default.

    The flag must be present in common_solver_settings, or on every case
    after merge. An explicit False is allowed (deliberate plug). Missing
    is not: that used to launch nine plug runs against a parabolic campaign.
    """
    common = common_solver_settings if hasattr(common_solver_settings, "keys") else {}
    if _INLET_PROFILE_FLAG in common:
        return
    cases = list(solver_sweep_cases or [])
    missing = []
    for case_dict in cases:
        merged = merge_batch_case_overrides(common, case_dict)
        if _INLET_PROFILE_FLAG not in merged:
            label = solver_case_label(
                case_dict.get("geo_id", "?"),
                case_dict.get("mesh_id", "?"),
                case_dict.get("run_id", "?"),
            )
            missing.append(label)
    if missing or not cases:
        raise ValueError(
            "batch_config must set use_inlet_velocity_profile explicitly "
            "(common_solver_settings or every solver_sweep_cases entry). "
            "Omitting it falls through to run_config's plug default. "
            + (
                f"Unset in: {missing}."
                if missing
                else "common_solver_settings does not set it."
            )
        )


def parse_batch_solver_sweep_cli(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Sequential solver sweep from configs/batch_config.py. "
            "Default --case-set exploratory uses solver_sweep_cases. "
            "Pass --case-set production for production_solver_sweep_cases (279)."
        ),
    )
    parser.add_argument(
        "--case-set",
        choices=CASE_SET_CHOICES,
        default=CASE_SET_EXPLORATORY,
        help=(
            "exploratory: solver_sweep_cases "
            "(P_p100_h00 cpg5 u0p2_p6M_src0 source-off diagnostic). "
            "production: production_solver_sweep_cases (279). "
            "Default exploratory so existing batch runs cannot launch 279 solves."
        ),
    )
    parser.add_argument(
        "--geo-id",
        action="append",
        dest="geo_ids",
        default=None,
        metavar="GEO_ID",
        help=(
            "Restrict the selected case-set to these geo_id values. "
            "Repeatable. Omitted: run the whole case-set. "
            "A geo_id that is not in the case-set is an error, not an empty sweep."
        ),
    )
    parser.add_argument(
        "--outlet-gauge-pressure",
        action="append",
        dest="outlet_gauge_pressures",
        default=None,
        type=float,
        metavar="PA",
        help=(
            "Restrict the selected case-set to these outlet_gauge_pressure "
            "values in Pa. Repeatable. Example: 6.0e6 for the p6M column "
            "(93 production cases). A value that is not in the case-set is "
            "an error, not an empty sweep."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the selected cases and skip/dry-run decisions without "
            "launching Fluent. ORs with batch_config.dry_run. Existing-final "
            "skip still wins when current-attempt evidence is complete."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    cli_args = parse_batch_solver_sweep_cli(argv)
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = bool(getattr(batchcfg, "dry_run", False) or cli_args.dry_run)
    continue_on_failure = getattr(batchcfg, "continue_on_failure", False)
    skip_existing_final_data = getattr(batchcfg, "skip_existing_final_data", True)
    common_solver_settings = getattr(batchcfg, "common_solver_settings", {})
    transient_failure_max_retries = getattr(
        batchcfg,
        "transient_failure_max_retries",
        SOLVER_TRANSIENT_FAILURE_MAX_RETRIES,
    )
    post_failure_settle_s = getattr(
        batchcfg,
        "post_failure_settle_s",
        SOLVER_POST_FAILURE_SETTLE_S,
    )
    solver_sweep_cases = filter_solver_cases_by_outlet_gauge_pressure(
        filter_solver_cases_by_geo_id(
            cases_for_case_set(
                batchcfg,
                cli_args.case_set,
                exploratory_attr="solver_sweep_cases",
                production_attr="production_solver_sweep_cases",
            ),
            cli_args.geo_ids,
        ),
        cli_args.outlet_gauge_pressures,
    )
    assert_selected_cases_are_not_legacy_ml(solver_sweep_cases)
    require_explicit_inlet_velocity_profile(
        common_solver_settings,
        solver_sweep_cases,
    )

    successes = []
    failures = []
    dry_run_cases = []
    skipped_existing = []

    total = len(solver_sweep_cases)
    print(f"\n{'='*72}")
    print(f"BATCH SOLVER SWEEP: {total} case(s)  case_set={cli_args.case_set}")
    if cli_args.geo_ids:
        print(f"geo_id filter: {list(dict.fromkeys(cli_args.geo_ids))}")
    if cli_args.outlet_gauge_pressures:
        print(
            "outlet_gauge_pressure filter: "
            f"{list(dict.fromkeys(cli_args.outlet_gauge_pressures))}"
        )
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_final_data={skip_existing_final_data}")
    print(
        f"transient_failure_max_retries={transient_failure_max_retries}  "
        f"post_failure_settle_s={post_failure_settle_s}"
    )
    print("Selected cases (Fluent has not launched):")
    if not solver_sweep_cases:
        print("  (none)")
    else:
        for listed_idx, listed_case in enumerate(solver_sweep_cases, start=1):
            print(
                format_selected_solver_case(
                    listed_idx, total, listed_case
                )
            )
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(solver_sweep_cases):
        family = case_dict["family"]
        geo_id = case_dict["geo_id"]
        mesh_id = case_dict["mesh_id"]
        run_id = case_dict["run_id"]
        base_case_name, case_name = resolve_case_names(case_dict)
        label = solver_case_label(geo_id, mesh_id, run_id)

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")
        overrides = merge_batch_case_overrides(common_solver_settings, case_dict)
        # The solver must receive the resolved case_name; base_case_name is
        # batch-side naming metadata only, not a solver config key.
        overrides["geo_name"] = geo_id
        overrides["case_name"] = case_name
        overrides.pop("base_case_name", None)

        input_mode, restart_case_file, restart_data_file = resolve_input_mode(overrides)

        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        expected_mesh = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
        target_case_folder = run_dir(family, geo_id, mesh_id, run_id)
        expected_final_case = (
            target_case_folder / f"{geo_id}_{run_id}_final.cas.h5"
        )
        expected_final_data = (
            target_case_folder / f"{geo_id}_{run_id}_final.dat.h5"
        )

        print(f"geo_id         : {geo_id}")
        print(f"mesh_id        : {mesh_id}")
        print(f"run_id         : {run_id}")
        print(f"base_case_name : {base_case_name if base_case_name else '(n/a)'}")
        print(f"case_name      : {case_name}")
        print(f"input_mode     : {input_mode}")
        if input_mode == "restart_continuation":
            print(f"Restart case file: {restart_case_file}")
            print(f"Restart data file: {restart_data_file}")
            print(f"Mesh input       : (not used for restart_continuation)")
        else:
            print(f"Required mesh input: {expected_mesh}")
        print(f"Target case folder: {target_case_folder}")
        print(f"Target case_name  : {case_name}")
        print(f"Target final case : {expected_final_case}")
        print(f"Target final data : {expected_final_data}")
        print(f"Overrides: {json.dumps(overrides, indent=2)}")

        cmd = [sys.executable, str(SOLVER_SCRIPT_PATH)]
        print(f"Command: {' '.join(cmd)}")

        outcome, skip_reason = classify_solver_pre_execution(
            skip_existing_final_data=skip_existing_final_data,
            dry_run=dry_run,
            run_directory=target_case_folder,
            final_case_path=expected_final_case,
            final_data_path=expected_final_data,
            mesh_file_path=expected_mesh,
        )
        if outcome == "skipped_existing":
            print(f"SKIP: {skip_reason}:")
            print(f"  {expected_final_case}")
            print(f"  {expected_final_data}")
            skipped_existing.append((label, skip_reason))
            continue

        if skip_existing_final_data and skip_reason:
            print(f"Not skipping existing finals: {skip_reason}")

        if outcome == "dry_run":
            if input_mode == "mesh_initialization" and not os.path.isfile(expected_mesh):
                print("[DRY RUN] Note: mesh file not found (expected when run off-server).")
            if input_mode == "restart_continuation":
                if not os.path.isfile(restart_case_file):
                    print("[DRY RUN] Note: restart case file not found (expected when run off-server).")
                if not os.path.isfile(restart_data_file):
                    print("[DRY RUN] Note: restart data file not found (expected when run off-server).")
            print("[DRY RUN] Skipping Fluent execution.")
            dry_run_cases.append(label)
            continue

        missing_input_files = []
        if input_mode == "restart_continuation":
            if not os.path.isfile(restart_case_file):
                missing_input_files.append(f"Restart case file not found: {restart_case_file}")
            if not os.path.isfile(restart_data_file):
                missing_input_files.append(f"Restart data file not found: {restart_data_file}")
        elif not os.path.isfile(expected_mesh):
            missing_input_files.append(f"Mesh file not found: {expected_mesh}")

        if missing_input_files:
            print("FAILED (pre-check):")
            for message in missing_input_files:
                print(f"  {message}")
            failures.append(label)
            if not continue_on_failure:
                print("Stopping batch because continue_on_failure=False.")
                break
            continue

        env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
        # The worker must load the base run_config.py, not a leftover env config.
        env.pop("PYFLUENT_RUN_CONFIG", None)
        env.pop("PYFLUENT_SKIP_VALIDATION", None)

        result, attempts, retry_kinds, attempt_logs = run_solver_attempts(
            cmd=cmd,
            env=env,
            cwd=str(SCRIPT_DIR),
            run_directory=target_case_folder,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=run_id,
            case_name=case_name,
            max_retries=transient_failure_max_retries,
            settle_s=post_failure_settle_s,
        )
        try:
            write_solver_retry_record(
                target_case_folder,
                attempts=attempts,
                retry_kinds=retry_kinds,
                attempt_logs=attempt_logs,
            )
        except OSError as exc:
            print(f"Warning: could not write solver_retry_record.json: {exc}")

        if solver_worker_succeeded(result.returncode):
            status = "SUCCESS_AFTER_RETRY" if attempts > 1 else "SUCCESS"
            print(
                f"\nSUCCESS: {label} (return code {result.returncode}, "
                f"attempts={attempts}, status={status})"
            )
            successes.append(label)
        else:
            failure_detail = describe_solver_worker_failure(result.returncode)
            print(
                f"\nFAILED: {label} (return code {result.returncode}: {failure_detail}, "
                f"attempts={attempts})"
            )
            failures.append(label)
            if not continue_on_failure:
                print("Stopping batch because continue_on_failure=False.")
                break

    _print_batch_summary(dry_run_cases, skipped_existing, successes, failures)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
