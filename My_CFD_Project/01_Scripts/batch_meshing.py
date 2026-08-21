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
import time
from pathlib import Path

from ro.mesh_common import (
    MESH_METRIC_NAMES,
    MESH_PARAMETER_NAMES,
    assert_mesh_case_name_matches,
    build_mesh_ledger_record,
    load_mesh_run_record,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_from_log,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
)
from ro.paths import data_root, mesh_dir
from ro.solver_common import merge_batch_case_overrides

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
    overrides = merge_batch_case_overrides(common_settings, case_dict)
    # The meshing worker names its output folder after case_name.
    overrides["case_name"] = overrides.pop("mesh_case_name")
    return overrides


def _continue_on_failure(batchcfg):
    return getattr(batchcfg, "continue_on_failure", True)


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
        if not error_summary:
            error_summary = worker_record.get("error_summary", "")

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


def main():
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)

    dry_run = getattr(batchcfg, "dry_run", False)
    continue_on_failure = _continue_on_failure(batchcfg)
    skip_existing_mesh = getattr(batchcfg, "skip_existing_mesh", True)
    common_mesh_settings = getattr(batchcfg, "common_mesh_settings", {})
    mesh_batch_cases = getattr(batchcfg, "mesh_batch_cases", [])

    base_cfg = _load_module("_base_cfg", BASE_RUN_CONFIG_PATH)
    ledger_path = data_root() / "inventory" / "mesh_ledger.csv"

    successes = []
    failures = []
    skipped = []

    total = len(mesh_batch_cases)
    print(f"\n{'='*72}")
    print(f"BATCH MESHING: {total} case(s)")
    print(f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  skip_existing_mesh={skip_existing_mesh}")
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(mesh_batch_cases):
        family = case_dict["family"]
        geo_id = case_dict["geo_id"]
        mesh_id = case_dict["mesh_id"]
        geo_name = case_dict["geo_name"]
        mesh_case_name = case_dict["mesh_case_name"]
        label = f"{geo_name}/{mesh_case_name}"
        overrides = _build_overrides(case_dict, common_mesh_settings)
        mesh_parameters = _resolved_mesh_parameters(base_cfg, overrides)
        assert_mesh_case_name_matches(
            mesh_case_name,
            mesh_parameters["m_max"],
            mesh_parameters["m_min"],
            mesh_parameters["m_cpg"],
            mesh_parameters["bl_layers"],
            allow_legacy=mesh_parameters[
                "allow_legacy_mesh_case_name_mismatch"
            ],
        )

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")

        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        expected_mesh = mesh_directory / f"{geo_name}_{mesh_case_name}.msh.h5"
        mesh_log_path = (
            Path(expected_mesh).parent
            / f"mesh_log_{mesh_case_name}.txt"
        )
        print(f"Expected mesh output: {expected_mesh}")

        if skip_existing_mesh and os.path.isfile(expected_mesh):
            print(f"SKIP: Mesh already exists: {expected_mesh}")
            skipped.append(label)
            _write_case_ledger(
                ledger_path=ledger_path,
                geo_name=geo_name,
                mesh_case_name=mesh_case_name,
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

        if dry_run:
            print("[DRY RUN] Skipping Fluent execution.")
            skipped.append(label)
            continue

        started = time.monotonic()
        result = subprocess.run(cmd, env=env, cwd=str(SCRIPT_DIR), check=False)
        wall_time_seconds = time.monotonic() - started

        if result.returncode == 0:
            print(f"\nSUCCESS: {label} (return code {result.returncode})")
            successes.append(label)
            status = "SUCCESS"
        else:
            print(f"\nFAILED: {label} (return code {result.returncode})")
            failures.append(label)
            status = "FAILED"

        _write_case_ledger(
            ledger_path=ledger_path,
            geo_name=geo_name,
            mesh_case_name=mesh_case_name,
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

        if result.returncode != 0:
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
