# ==========================================================
# batch_solver_sweep.py
# Sequential batch driver for solver parameter sweeps
# Location: My_CFD_Project/01_Scripts/batch_solver_sweep.py
# Usage: python My_CFD_Project/01_Scripts/batch_solver_sweep.py
# ==========================================================

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from ro.paths import mesh_dir, project_root, run_dir
from ro.solver_common import (
    describe_solver_worker_failure,
    make_base_case_name,
    make_mesh_qualified_case_name,
    merge_batch_case_overrides,
    pressure_to_case_token,
    resolve_case_names,
    resolve_input_mode,
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


def main():
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = getattr(batchcfg, "continue_on_failure", False)
    skip_existing_final_data = getattr(batchcfg, "skip_existing_final_data", True)
    common_solver_settings = getattr(batchcfg, "common_solver_settings", {})
    solver_sweep_cases = getattr(batchcfg, "solver_sweep_cases", [])

    successes = []
    failures = []
    skipped = []

    total = len(solver_sweep_cases)
    print(f"\n{'='*72}")
    print(f"BATCH SOLVER SWEEP: {total} case(s)")
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_final_data={skip_existing_final_data}")
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(solver_sweep_cases):
        family = case_dict["family"]
        geo_id = case_dict["geo_id"]
        mesh_id = case_dict["mesh_id"]
        run_id = case_dict["run_id"]
        geo_name = case_dict["geo_name"]
        mesh_case_name = case_dict["mesh_case_name"]
        base_case_name, case_name = resolve_case_names(case_dict)
        label = f"{geo_name}/{case_name}"

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")
        overrides = merge_batch_case_overrides(common_solver_settings, case_dict)
        # The solver must receive the resolved case_name; base_case_name is
        # batch-side naming metadata only, not a solver config key.
        overrides["case_name"] = case_name
        overrides.pop("base_case_name", None)

        input_mode, restart_case_file, restart_data_file = resolve_input_mode(overrides)

        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        expected_mesh = mesh_directory / f"{geo_name}_{mesh_case_name}.msh.h5"
        target_case_folder = run_dir(family, geo_id, mesh_id, run_id)
        expected_final_case = (
            target_case_folder / f"{geo_name}_{case_name}_final.cas.h5"
        )
        expected_final_data = (
            target_case_folder / f"{geo_name}_{case_name}_final.dat.h5"
        )

        print(f"geo_name       : {geo_name}")
        print(f"mesh_case_name : {mesh_case_name}")
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

        if dry_run:
            if input_mode == "mesh_initialization" and not os.path.isfile(expected_mesh):
                print("[DRY RUN] Note: mesh file not found (expected when run off-server).")
            if input_mode == "restart_continuation":
                if not os.path.isfile(restart_case_file):
                    print("[DRY RUN] Note: restart case file not found (expected when run off-server).")
                if not os.path.isfile(restart_data_file):
                    print("[DRY RUN] Note: restart data file not found (expected when run off-server).")
            print("[DRY RUN] Skipping Fluent execution.")
            skipped.append(label)
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

        if skip_existing_final_data:
            if os.path.isfile(expected_final_case) and os.path.isfile(expected_final_data):
                print("SKIP: Final case and data already exist.")
                skipped.append(label)
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

    print(f"\n{'='*72}")
    print("BATCH SOLVER SWEEP SUMMARY")
    print(f"{'='*72}")
    print(f"  Succeeded : {len(successes)}")
    print(f"  Skipped   : {len(skipped)}")
    print(f"  Failed    : {len(failures)}")
    if successes:
        print("  Succeeded cases:")
        for s in successes:
            print(f"    {s}")
    if skipped:
        print("  Skipped cases:")
        for s in skipped:
            print(f"    {s}")
    if failures:
        print("  FAILED cases:")
        for f in failures:
            print(f"    {f}")
    print(f"{'='*72}\n")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
