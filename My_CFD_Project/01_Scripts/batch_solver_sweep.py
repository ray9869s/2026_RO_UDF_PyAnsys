# ==========================================================
# batch_solver_sweep.py
# Sequential batch driver for solver parameter sweeps
# Location: My_CFD_Project/01_Scripts/batch_solver_sweep.py
# Usage: python My_CFD_Project/01_Scripts/batch_solver_sweep.py
# ==========================================================

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_CONFIG_PATH = SCRIPT_DIR / "batch_config.py"
BATCH_RUN_CONFIGS_DIR = SCRIPT_DIR / "_batch_run_configs"
BASE_RUN_CONFIG_PATH = SCRIPT_DIR / "run_config.py"
SOLVER_SCRIPT_PATH = SCRIPT_DIR / "solver_code_260616.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _safe_name(s):
    """Sanitize a string for use as a filename component."""
    return re.sub(r"[^\w\-]", "_", str(s)).strip("_")


def _generate_solver_config(case_dict, common_settings, index):
    """Write a temporary override config for one solver case and return its path."""
    geo_name = case_dict["geo_name"]
    mesh_case_name = case_dict["mesh_case_name"]
    case_name = case_dict["case_name"]
    inlet_velocity_value = case_dict["inlet_velocity_value"]
    operating_pressure = case_dict["operating_pressure"]
    outlet_gauge_pressure = case_dict["outlet_gauge_pressure"]

    filename = f"solver_{index:03d}_{_safe_name(geo_name)}_{_safe_name(case_name)}.py"
    config_path = BATCH_RUN_CONFIGS_DIR / filename

    merged = {**common_settings, **case_dict}

    run_calculation_enabled = merged.get("run_calculation_enabled", True)
    max_iterations = merged.get("max_iterations", 2000)
    residual_target = merged.get("residual_target", 1e-7)

    lines = [
        "# Auto-generated solver override config — do not edit manually.",
        "from pathlib import Path",
        "import sys",
        "",
        "SCRIPT_DIR = Path(__file__).resolve().parents[1]",
        "if str(SCRIPT_DIR) not in sys.path:",
        "    sys.path.insert(0, str(SCRIPT_DIR))",
        "",
        "from run_config import *",
        "",
        f"geo_name = {geo_name!r}",
        f"case_name = {case_name!r}",
        f"mesh_case_name = {mesh_case_name!r}",
        f"inlet_velocity_value = {inlet_velocity_value!r}",
        f"operating_pressure = {operating_pressure!r}",
        f"outlet_gauge_pressure = {outlet_gauge_pressure!r}",
        f"run_calculation_enabled = {run_calculation_enabled!r}",
        f"max_iterations = {max_iterations!r}",
        f"residual_target = {residual_target!r}",
    ]

    handled = {
        "geo_name", "mesh_case_name", "case_name",
        "inlet_velocity_value", "operating_pressure", "outlet_gauge_pressure",
        "run_calculation_enabled", "max_iterations", "residual_target",
    }
    for k, v in merged.items():
        if k not in handled:
            lines.append(f"{k} = {v!r}")

    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def main():
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = getattr(batchcfg, "continue_on_failure", False)
    skip_existing_final_data = getattr(batchcfg, "skip_existing_final_data", True)
    common_solver_settings = getattr(batchcfg, "common_solver_settings", {})
    solver_sweep_cases = getattr(batchcfg, "solver_sweep_cases", [])

    BATCH_RUN_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

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

        config_path = _generate_solver_config(case_dict, common_solver_settings, i)
        print(f"Generated config: {config_path}")

        cmd = [sys.executable, str(SOLVER_SCRIPT_PATH)]
        env = {**os.environ, "PYFLUENT_RUN_CONFIG": str(config_path)}

        print(f"Command: {' '.join(cmd)}")
        print(f"PYFLUENT_RUN_CONFIG={config_path}")

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
