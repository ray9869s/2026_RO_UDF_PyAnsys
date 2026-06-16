# ==========================================================
# batch_meshing.py
# Sequential batch driver for meshing multiple geometries
# Location: My_CFD_Project/01_Scripts/batch_meshing.py
# Usage: python My_CFD_Project/01_Scripts/batch_meshing.py
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
MESHING_SCRIPT_PATH = SCRIPT_DIR / "meshing_code_260616.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _safe_name(s):
    """Sanitize a string for use as a filename component."""
    return re.sub(r"[^\w\-]", "_", str(s)).strip("_")


def _generate_meshing_config(case_dict, common_settings, index):
    """Write a temporary override config for one meshing case and return its path."""
    geo_name = case_dict["geo_name"]
    mesh_case_name = case_dict["mesh_case_name"]

    filename = f"mesh_{index:03d}_{_safe_name(geo_name)}_{_safe_name(mesh_case_name)}.py"
    config_path = BATCH_RUN_CONFIGS_DIR / filename

    merged = {**common_settings, **case_dict}

    m_max = merged["m_max"]
    m_min = merged["m_min"]
    m_cpg = merged["m_cpg"]
    bl_layers = merged["bl_layers"]
    wall_spacer_labels = case_dict.get("wall_spacer_labels", [])

    lines = [
        "# Auto-generated meshing override config — do not edit manually.",
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
        f"case_name = {mesh_case_name!r}",
        f"wall_spacer_labels = {wall_spacer_labels!r}",
        f"m_max = {m_max!r}",
        f"m_min = {m_min!r}",
        f"m_cpg = {m_cpg!r}",
        f"bl_layers = {bl_layers!r}",
    ]

    handled = {
        "geo_name", "mesh_case_name", "case_name",
        "wall_spacer_labels", "m_max", "m_min", "m_cpg", "bl_layers",
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
    skip_existing_mesh = getattr(batchcfg, "skip_existing_mesh", True)
    common_mesh_settings = getattr(batchcfg, "common_mesh_settings", {})
    mesh_batch_cases = getattr(batchcfg, "mesh_batch_cases", [])

    BATCH_RUN_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

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

        config_path = _generate_meshing_config(case_dict, common_mesh_settings, i)
        print(f"Generated config: {config_path}")

        cmd = [sys.executable, str(MESHING_SCRIPT_PATH)]
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
