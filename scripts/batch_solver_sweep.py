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
from pathlib import Path

from ro.campaign_matrix import (
    CASE_SET_CHOICES,
    CASE_SET_EXPLORATORY,
    cases_for_case_set,
)
from ro.manifest import ManifestError, read_run_manifest
from ro.paths import mesh_dir, project_root, run_dir
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


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_INLET_PROFILE_FLAG = "use_inlet_velocity_profile"


def solver_case_label(geo_id, mesh_id, run_id):
    return f"{geo_id}/{mesh_id}/{run_id}"


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
            "exploratory: solver_sweep_cases (currently 5). "
            "production: production_solver_sweep_cases (279). "
            "Default exploratory so existing batch runs cannot launch 279 solves."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    cli_args = parse_batch_solver_sweep_cli(argv)
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = getattr(batchcfg, "continue_on_failure", False)
    skip_existing_final_data = getattr(batchcfg, "skip_existing_final_data", True)
    common_solver_settings = getattr(batchcfg, "common_solver_settings", {})
    solver_sweep_cases = cases_for_case_set(
        batchcfg,
        cli_args.case_set,
        exploratory_attr="solver_sweep_cases",
        production_attr="production_solver_sweep_cases",
    )
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
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_final_data={skip_existing_final_data}")
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

        result = subprocess.run(cmd, env=env, cwd=str(SCRIPT_DIR), check=False)

        if solver_worker_succeeded(result.returncode):
            print(f"\nSUCCESS: {label} (return code {result.returncode})")
            successes.append(label)
        else:
            failure_detail = describe_solver_worker_failure(result.returncode)
            print(
                f"\nFAILED: {label} (return code {result.returncode}: {failure_detail})"
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
