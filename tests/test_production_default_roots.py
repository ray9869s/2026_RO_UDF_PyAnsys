"""RO_DATA_ROOT=C:\\ro_data in the host shell: no tool writes there by default.

Each tool below defaulted an output, a log, a Fluent working directory or
a deletion to a path under the data root. With the data root set to the
production root (native spelling for this OS) every one refuses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module

PRODUCTION = "C:/ro_data" if os.name == "nt" else "/mnt/c/ro_data"


@pytest.fixture
def production_env(monkeypatch):
    monkeypatch.setenv("RO_DATA_ROOT", PRODUCTION)


def test_watchdog_refuses_a_production_attempt_log_before_starting(tmp_path):
    from ro.hang_watchdog import tee_until_hang

    started = []
    with pytest.raises(ValueError, match="production"):
        tee_until_hang([sys.executable, "-c", "pass"], env=None,
                       log_path=Path(PRODUCTION) / "runs" / "x" / "solver_attempt1.log",
                       stage="solve", idle_s=1, popen_factory=lambda *a, **k: started.append(a))
    assert started == []


def test_solver_sweep_leaf_writes_refuse_production():
    sweep = load_module("sweep_prod_guard", SCRIPTS_DIR / "batch_solver_sweep.py")
    leaf = Path(PRODUCTION) / "runs" / "pillar" / "P" / "m" / "u0p2_p6M"
    with pytest.raises(ValueError, match="production"):
        sweep.write_solver_retry_record(leaf, attempts=1, retry_kinds=[], attempt_logs=[])
    with pytest.raises(ValueError, match="production"):
        sweep.preserve_solver_worker_logs(leaf, "u0p2_p6M", 1)


def test_meshing_scratch_cleanup_refuses_production():
    meshing = load_module("meshing_prod_guard", SCRIPTS_DIR / "batch_meshing.py")
    with pytest.raises(ValueError, match="production"):
        meshing.cleanup_fm_scratch_dirs(Path(PRODUCTION) / "meshes" / "pillar" / "P" / "m")


@pytest.mark.parametrize("script", ["case_inventory.py", "residual_measurement_report.py"])
def test_inventory_reports_refuse_the_default_output(production_env, tmp_path, script):
    module = load_module(f"inv_{script[:-3]}", SCRIPTS_DIR / script)
    args = module.parse_args(["--results-root", str(tmp_path)])
    with pytest.raises(ValueError, match="production"):
        module.resolve_path_defaults(args)
    args = module.parse_args(["--results-root", str(tmp_path), "--output-dir", str(tmp_path / "out")])
    assert module.resolve_path_defaults(args).output_dir == (tmp_path / "out").resolve()


def test_solver_rerun_and_figures_refuse_default_outputs(production_env):
    rerun = load_module("rerun_prod_guard", SCRIPTS_DIR / "batch_solver_rerun.py")
    with pytest.raises(ValueError, match="production"):
        rerun.resolve_path_defaults(rerun.parse_args([]))
    figures = load_module("figures_prod_guard", SCRIPTS_DIR / "make_summary_figures.py")
    with pytest.raises(ValueError, match="production"):
        figures.resolve_path_defaults(figures.parse_args([]))


def test_solver_rerun_leaf_writes_refuse_production(tmp_path):
    rerun = load_module("rerun_leaf_guard", SCRIPTS_DIR / "batch_solver_rerun.py")
    leaf = Path(PRODUCTION) / "runs" / "pillar" / "P" / "m" / "u0p2_p6M"
    with pytest.raises(ValueError, match="production"):
        rerun.create_backup(leaf, leaf / "a.cas.h5", leaf / "a.dat.h5", "t")
    source = (SCRIPTS_DIR / "batch_solver_rerun.py").read_text(encoding="utf-8")
    assert source.count("refuse_production_root(launch_working_dir)") == 2


def test_postprocess_refuses_production_case_and_inventory():
    post = load_module("post_prod_guard", SCRIPTS_DIR / "batch_postprocess_all_cases.py")
    with pytest.raises(ValueError, match="production"):
        post.ensure_safe_write_root(Path(PRODUCTION) / "inventory" / "batch_postprocess")
    source = (SCRIPTS_DIR / "batch_postprocess_all_cases.py").read_text(encoding="utf-8")
    start = source.index("        case_dir = resolve_run_directory(row)")
    assert "refuse_production_root(case_dir)" in source[start:start + 200]


@pytest.mark.parametrize("script", ["diagnose_cp_face_filters.py", "diagnose_cp_quantile_spread.py",
                                    "diagnose_yi_saturation.py", "probe_cff_surface_areaavg.py"])
def test_fluent_diagnostics_refuse_a_production_working_directory(script):
    source = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    launch = source.index("pyfluent.launch_fluent(")
    assert "refuse_production_root(case_dir)" in source[launch - 400:launch]


def test_probes_default_outside_production_and_refuse_it():
    probe = load_module("probe_ref_guard", SCRIPTS_DIR / "_probe_reference_geometry.py")
    for default in (probe.WORK_DIR, probe.DIAMOND_WORK_DIR):
        assert default.startswith("C:/ro_data_mfbo/")
    source = (SCRIPTS_DIR / "_probe_reference_geometry.py").read_text(encoding="utf-8")
    assert source.count("refuse_production_root(work_dir)") == 3
    pmdb = (SCRIPTS_DIR / "_probe_pmdb_import.py").read_text(encoding="utf-8")
    assert 'WORK_DIR = "C:/ro_data_mfbo/' in pmdb and "refuse_production_root(WORK_DIR)" in pmdb


def test_2d_root_and_grid_chunks_refuse_production(tmp_path):
    from ro_2d_pilot.paths import assert_data_root_allowed

    with pytest.raises(ValueError, match="production"):
        assert_data_root_allowed(Path(PRODUCTION) / "ro_2d")
    grid = load_module("grid_prod_guard", SCRIPTS_DIR / "mfbo" / "pregenerate_grid.py")
    chunk = tmp_path / "chunk.json"
    chunk.write_text('[{"geo_id": "x", "out_dir": "%s/geometries/pillar/x"}]' % PRODUCTION, encoding="utf-8")
    with pytest.raises(ValueError, match="production"):
        grid.execute_chunk(chunk, command=lambda path: ["never"])
