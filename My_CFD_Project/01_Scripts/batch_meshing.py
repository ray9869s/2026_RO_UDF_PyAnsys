# ==========================================================
# batch_meshing.py
# Sequential batch driver for meshing multiple geometries
# Location: My_CFD_Project/01_Scripts/batch_meshing.py
# Usage: python My_CFD_Project/01_Scripts/batch_meshing.py
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
MESHING_SCRIPT_PATH = SCRIPT_DIR / "meshing_code_260616.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_overrides(case_dict, common_settings):
    """Merge common settings and one case entry into the worker override dict."""
    overrides = {**common_settings, **case_dict}
    # The meshing worker names its output folder after case_name.
    overrides["case_name"] = overrides.pop("mesh_case_name")
    return overrides


def main():
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = getattr(batchcfg, "continue_on_failure", False)
    skip_existing_mesh = getattr(batchcfg, "skip_existing_mesh", True)
    common_mesh_settings = getattr(batchcfg, "common_mesh_settings", {})
    mesh_batch_cases = getattr(batchcfg, "mesh_batch_cases", [])

    base_cfg = _load_module("_base_cfg", BASE_RUN_CONFIG_PATH)
    project_root = base_cfg.project_root

    successes = []
    failures = []
    skipped = []

    total = len(mesh_batch_cases)
    print(f"\n{'='*72}")
    print(f"BATCH MESHING: {total} case(s)")
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_mesh={skip_existing_mesh}")
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(mesh_batch_cases):
        geo_name = case_dict["geo_name"]
        mesh_case_name = case_dict["mesh_case_name"]
        label = f"{geo_name}/{mesh_case_name}"

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")

        expected_mesh = os.path.join(
            project_root, "03_Results", geo_name, mesh_case_name,
            f"{geo_name}_{mesh_case_name}.msh.h5",
        )
        print(f"Expected mesh output: {expected_mesh}")

        if skip_existing_mesh and os.path.isfile(expected_mesh):
            print(f"SKIP: Mesh already exists: {expected_mesh}")
            skipped.append(label)
            continue

        overrides = _build_overrides(case_dict, common_mesh_settings)
        print(f"Overrides: {json.dumps(overrides, indent=2)}")

        cmd = [sys.executable, str(MESHING_SCRIPT_PATH)]
        env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
        # The worker must load the base run_config.py, not a leftover env config.
        env.pop("PYFLUENT_RUN_CONFIG", None)
        env.pop("PYFLUENT_SKIP_VALIDATION", None)

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
    print("BATCH MESHING SUMMARY")
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
