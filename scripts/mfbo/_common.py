"""Shared MFBO helpers. Importing this module does not launch Fluent."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_report_extract
import batch_solver_sweep
from ro.extract_profile import report_extract_argv


def call_with_data_root(data_root, fn):
    """Call ``fn`` with ``RO_DATA_ROOT`` set, then restore the parent env."""
    previous = os.environ.get("RO_DATA_ROOT")
    os.environ["RO_DATA_ROOT"] = str(data_root)
    try:
        return fn()
    finally:
        if previous is None:
            os.environ.pop("RO_DATA_ROOT", None)
        else:
            os.environ["RO_DATA_ROOT"] = previous


def extract_launcher_paths():
    """Worker script and base post config, as batch_report_extract.main loads them."""
    bcfg = batch_report_extract.load_python_config(
        batch_solver_sweep.project_root() / "configs" / "batch_post_config.py",
        "batch_post_config_mfbo_extract",
    )
    base_config = Path(bcfg.base_post_config)
    if not base_config.is_file():
        base_config = (
            batch_solver_sweep.project_root() / "configs" / base_config.name
        )
    return Path(bcfg.single_case_worker), base_config


def extract_child_env(data_root, overrides, base_config):
    """Child env for report extraction. Does not mutate the parent env."""
    env = os.environ.copy()
    env["RO_DATA_ROOT"] = str(data_root)
    env["PYFLUENT_POST_CONFIG"] = str(base_config)
    env["PYFLUENT_POST_OVERRIDES"] = json.dumps(overrides)
    return env


def launch_extract(
    data_root,
    run_directory,
    case,
    *,
    geo_id,
    mesh_id,
    run_id,
    profile=None,
):
    """Run pyfluent_report_extract.py the way batch_report_extract.py does.

    ``profile=None`` keeps the worker argv unchanged. ``profile="mfbo"``
    appends ``--profile mfbo``.
    """
    run_directory = Path(run_directory)
    worker, base_config = extract_launcher_paths()
    final_case = run_directory / f"{geo_id}_{run_id}_final.cas.h5"
    final_data = run_directory / f"{geo_id}_{run_id}_final.dat.h5"

    def _build():
        overrides, error = batch_report_extract.build_post_case_overrides(
            geo_name=geo_id,
            case_name=run_id,
            final_case_file=final_case,
            final_data_file=final_data,
            inlet_velocity_value=case["inlet_velocity_value"],
            outlet_gauge_pressure=case["outlet_gauge_pressure"],
            case_dir=run_directory,
        )
        if error is not None:
            raise RuntimeError(f"Could not build post overrides: {error}")
        return overrides

    overrides = call_with_data_root(data_root, _build)
    env = extract_child_env(data_root, overrides, base_config)
    cmd = report_extract_argv(sys.executable, worker, profile)
    print(f"Extract command: {' '.join(cmd)}")
    print(f"RO_DATA_ROOT (child only): {data_root}")
    print(f"Extract overrides: {json.dumps(overrides)}")
    result, attempts, retry_kinds, _attempt_logs = (
        batch_report_extract.run_extract_attempts(
            cmd=cmd,
            env=env,
            run_directory=run_directory,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=run_id,
        )
    )
    if result is None:
        raise RuntimeError("Extract worker did not return a process result.")
    print(
        f"Extract return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result
