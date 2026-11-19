"""B5-01: one canonical production-root guard, used by every writer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR
from ro.paths import is_production_path, refuse_production_root


@pytest.mark.parametrize(
    "path",
    [
        "C:/ro_data",
        "c:\\RO_DATA\\runs\\x",
        "C:/ro_data_mfbo/../ro_data/runs",
        "C:/ro_data_mfbo/campaign_v3/../../ro_data/meshes",
        "C:/ro_data./runs",
        "C:/ro_data /runs",
        "\\\\?\\C:\\ro_data\\runs",
        "//?/C:/ro_data",
        "/mnt/c/ro_data/runs",
        "/mnt/c/RO_DATA",
        "/mnt/c/ro_data_mfbo/../ro_data",
    ],
)
def test_production_spellings_are_refused(path):
    assert is_production_path(path)
    with pytest.raises(ValueError, match="production"):
        refuse_production_root(path)


@pytest.mark.parametrize(
    "path",
    ["C:/ro_data_mfbo/campaign_v3", "C:/ro_data2", "/mnt/c/ro_data_mfbo", "/tmp/ro_data"],
)
def test_study_roots_pass(path):
    assert not is_production_path(path)
    assert refuse_production_root(path) == path


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlink_and_samefile_alias_are_refused(tmp_path):
    production = tmp_path / "prod"
    (production / "runs").mkdir(parents=True)
    alias = tmp_path / "study_alias"
    try:
        alias.symlink_to(production, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")
    roots = (str(production),)
    assert is_production_path(alias / "runs" / "new_leaf", roots=roots)
    assert is_production_path(str(tmp_path / "x" / ".." / "prod"), roots=roots)
    assert not is_production_path(tmp_path / "other", roots=roots)


def test_drivers_use_the_canonical_guard():
    for name in (
        "mfbo/mesh_study_case.py",
        "mfbo/run_parity_solve.py",
        "mfbo/mesh_mfbo_case.py",
        "mfbo/run_geometry_parity_all.py",
        "mfbo/compare_parity_solves.py",
        "mfbo/diagnose_cp_max_hotspots.py",
    ):
        source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        assert "refuse_production_root(" in source, name
        assert 'folded == "c:/ro_data"' not in source, name


def test_writers_refuse_production(tmp_path):
    from ro.atomic_reports import promote_staged_reports, staging_report_path
    from ro.extract_skip import write_extract_source_record
    from ro.manifest import write_mesh_manifest, write_run_manifest

    target = "C:/ro_data_mfbo/../ro_data/runs/diamond/D/m/u0p2_p6M"
    for call in (
        lambda: staging_report_path(Path(target) / "post" / "reports"),
        lambda: promote_staged_reports(tmp_path, Path(target) / "post" / "reports"),
        lambda: write_extract_source_record(Path(target) / "post" / "reports", {}),
        lambda: write_run_manifest(target, {}),
        lambda: write_mesh_manifest(target, {}),
    ):
        with pytest.raises(ValueError, match="production"):
            call()


def test_mesh_study_resolve_data_root_refuses_dotdot():
    from helpers import load_module

    driver = load_module("mesh_study_guard", SCRIPTS_DIR / "mfbo" / "mesh_study_case.py")
    with pytest.raises(ValueError, match="production"):
        driver.resolve_data_root("C:/ro_data_mfbo/../ro_data")
    with pytest.raises(ValueError, match="production"):
        driver.resolve_data_root("C:/ro_data.")


def test_shared_write_points_refuse_production():
    """RO_DATA_ROOT=C:\\ro_data on the host: every shared writer refuses it."""
    from ro.atomic_reports import clear_manifest_gate
    from ro.manifest import _atomic_write
    from ro.mesh_common import write_mesh_run_record

    for target in ("C:/ro_data/runs/diamond/D/m/u0p2_p6M", r"C:\ro_data\meshes\x", "/mnt/c/ro_data/runs/x"):
        for call in (
            lambda: _atomic_write(Path(target) / "manifest.json", {}),
            lambda: write_mesh_run_record(Path(target) / "mesh_run_record.json", {}),
            lambda: clear_manifest_gate(target),
        ):
            with pytest.raises(ValueError, match="production|must be absolute"):
                call()


def test_fluent_workers_refuse_a_production_leaf_at_start():
    solver = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    start = solver.index('case_path = resolved_paths["run_directory"]')
    assert "refuse_production_root(case_path)" in solver[start:start + 300]
    meshing = (SCRIPTS_DIR / "meshing_code_260616.py").read_text(encoding="utf-8")
    start = meshing.index('case_path = resolved_paths["mesh_directory"]')
    assert "refuse_production_root(case_path)" in meshing[start:start + 300]
    extract = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    staging = extract.index("report_path = staging_report_path(final_report_path)")
    assert staging < extract.index("post_path.mkdir(parents=True, exist_ok=True)")
