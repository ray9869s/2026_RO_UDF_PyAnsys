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

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_CONFIG_PATH = SCRIPT_DIR / "batch_config.py"
BASE_RUN_CONFIG_PATH = SCRIPT_DIR / "run_config.py"
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

    base_cfg = _load_module("_base_cfg", BASE_RUN_CONFIG_PATH)
    project_root = base_cfg.project_root

    successes = []
    failures = []
    skipped = []

    total = len(solver_sweep_cases)
    print(f"\n{'='*72}")
    print(f"BATCH SOLVER SWEEP: {total} case(s)")
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_final_data={skip_existing_final_data}")
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(solver_sweep_cases):
        geo_name = case_dict["geo_name"]
        mesh_case_name = case_dict["mesh_case_name"]
        case_name = case_dict["case_name"]
        label = f"{geo_name}/{case_name}"

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")

        expected_mesh = os.path.join(
            project_root, "03_Results", geo_name, mesh_case_name,
            f"{geo_name}_{mesh_case_name}.msh.h5",
        )
        expected_final_case = os.path.join(
            project_root, "03_Results", geo_name, case_name,
            f"{geo_name}_{case_name}_final.cas.h5",
        )
        expected_final_data = expected_final_case.replace(".cas.h5", ".dat.h5")

        print(f"Required mesh input:  {expected_mesh}")
        print(f"Expected final case:  {expected_final_case}")
        print(f"Expected final data:  {expected_final_data}")

        if not os.path.isfile(expected_mesh):
            print(f"FAILED (pre-check): Mesh file not found: {expected_mesh}")
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

        overrides = {**common_solver_settings, **case_dict}
        print(f"Overrides: {json.dumps(overrides, indent=2)}")

        cmd = [sys.executable, str(SOLVER_SCRIPT_PATH)]
        env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
        # The worker must load the base run_config.py, not a leftover env config.
        env.pop("PYFLUENT_RUN_CONFIG", None)

        print(f"Command: {' '.join(cmd)}")

        if dry_run:
            print("[DRY RUN] Skipping Fluent execution.")
            skipped.append(label)
            continue

        result = subprocess.run(cmd, env=env, cwd=str(SCRIPT_DIR), check=False)

        if result.returncode == 0:
            print(f"\nSUCCESS: {label} (return code {result.returncode})")
            successes.append(label)
        else:
            print(f"\nFAILED: {label} (return code {result.returncode})")
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
