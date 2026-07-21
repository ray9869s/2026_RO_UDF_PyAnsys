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

# Solver worker exit codes (solver_code_260616.py):
#   0 = success (final .cas.h5/.dat.h5 exist and are non-empty)
#   1 = unhandled exception / preflight failure
#   2 = final artifact verification failure after write
SOLVER_EXIT_SUCCESS = 0
SOLVER_EXIT_ARTIFACT_FAILURE = 2


def solver_worker_succeeded(returncode: int) -> bool:
    """True only when the solver worker completed with verified final artifacts."""
    return returncode == SOLVER_EXIT_SUCCESS


def describe_solver_worker_failure(returncode: int) -> str:
    if returncode == SOLVER_EXIT_ARTIFACT_FAILURE:
        return "final case/data missing or empty"
    return "worker failed"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------
# Case naming helpers
# ----------------------------------------------------------

def velocity_to_case_token(u):
    # 0.1 -> "u0p1"
    return f"u0p{int(round(float(u) * 10))}"


def pressure_to_case_token(p):
    # 4.0e6 -> "p4M"
    return f"p{int(round(float(p) / 1.0e6))}M"


def make_base_case_name(u, p):
    # (0.1, 4.0e6) -> "u0p1_p4M"
    return f"{velocity_to_case_token(u)}_{pressure_to_case_token(p)}"


def make_mesh_qualified_case_name(base_case_name, mesh_case_name):
    if mesh_case_name:
        return f"{base_case_name}__{mesh_case_name}"
    return base_case_name


def resolve_case_names(case_dict):
    """Return (base_case_name, case_name) for a solver sweep entry.

    Priority:
      1. Explicit "case_name" is used as-is (legacy behavior).
      2. Explicit "base_case_name" is mesh-qualified with mesh_case_name.
      3. inlet_velocity_value + outlet_gauge_pressure + mesh_case_name derive both.
      4. Otherwise "case_name" is required, as before.
    """
    base_case_name = case_dict.get("base_case_name")
    mesh_case_name = case_dict.get("mesh_case_name")
    explicit_case_name = case_dict.get("case_name")

    if explicit_case_name:
        return base_case_name, explicit_case_name

    if base_case_name and mesh_case_name:
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    u = case_dict.get("inlet_velocity_value")
    p = case_dict.get("outlet_gauge_pressure")
    if u is not None and p is not None and mesh_case_name:
        base_case_name = make_base_case_name(u, p)
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    # Legacy behavior: an explicit case_name is required when it cannot be derived.
    return base_case_name, case_dict["case_name"]


def resolve_input_mode(case_settings):
    """Return the solver input mode and optional restart source paths."""
    restart_case = case_settings.get("restart_from_case_file")
    restart_data = case_settings.get("restart_from_data_file")

    has_restart_case = restart_case is not None
    has_restart_data = restart_data is not None

    if has_restart_case != has_restart_data:
        raise ValueError(
            "restart_from_case_file and restart_from_data_file must be provided together."
        )

    if has_restart_case:
        if not str(restart_case).strip() or not str(restart_data).strip():
            raise ValueError(
                "restart_from_case_file and restart_from_data_file must be non-empty paths."
            )
        return "restart_continuation", restart_case, restart_data

    return "mesh_initialization", None, None


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
        base_case_name, case_name = resolve_case_names(case_dict)
        label = f"{geo_name}/{case_name}"

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")
        overrides = {**common_solver_settings, **case_dict}
        # The solver must receive the resolved case_name; base_case_name is
        # batch-side naming metadata only, not a solver config key.
        overrides["case_name"] = case_name
        overrides.pop("base_case_name", None)

        input_mode, restart_case_file, restart_data_file = resolve_input_mode(overrides)

        expected_mesh = os.path.join(
            project_root, "03_Results", geo_name, mesh_case_name,
            f"{geo_name}_{mesh_case_name}.msh.h5",
        )
        target_case_folder = os.path.join(
            project_root, "03_Results", geo_name, case_name,
        )
        expected_final_case = os.path.join(
            target_case_folder,
            f"{geo_name}_{case_name}_final.cas.h5",
        )
        expected_final_data = expected_final_case.replace(".cas.h5", ".dat.h5")

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
