"""R-10 dead-gate reports and selected-case wiring. No Fluent."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import LEGACY_ML_GEO_ID_PREFIX
from ro.manifest import write_mesh_manifest, write_run_manifest
from ro.paths import mesh_dir, run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, mesh_payload, run_payload


def test_batch_drivers_gate_selected_legacy_ml_geo_ids():
    for name in ("batch_meshing.py", "batch_solver_sweep.py"):
        text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        assert "assert_selected_cases_are_not_legacy_ml(" in text
        assert "assert_no_legacy_ml_geo_paths(" not in text


def test_solver_does_not_call_spacer_or_replace_log_gates():
    text = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    assert "validate_spacer_wall_zones(" not in text
    assert "inspect_replace_log_identity(" not in text
    assert "assert_replace_log_matches_mesh_manifest(" not in text


def test_legacy_ml_report_requires_ro_data_root(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    report = load_module("report_legacy_ml_paths_r10", SCRIPTS_DIR / "report_legacy_ml_paths.py")
    with pytest.raises(ValueError, match="RO_DATA_ROOT"):
        report.main()


def test_legacy_ml_report_archive_is_out_of_scope(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    (tmp_path / "_archive" / "ml_pre_wedge_rule" / f"{LEGACY_ML_GEO_ID_PREFIX}050").mkdir(parents=True)
    (tmp_path / "meshes" / "ml" / "M_c160").mkdir(parents=True)
    report = load_module("report_legacy_ml_paths_r10", SCRIPTS_DIR / "report_legacy_ml_paths.py")
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "PRESENT_OUT_OF_SCOPE" in out
    assert "campaign_tree_legacy=0" in out


def test_replace_log_report_not_checked_without_log(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, run_payload())
    report = load_module(
        "report_replace_log_identities_r10",
        SCRIPTS_DIR / "report_replace_log_identities.py",
    )
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "NOT_CHECKED" in out
    assert "reject=0" in out


def test_spacer_report_no_log_is_not_reject(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, run_payload())
    report = load_module(
        "report_spacer_wall_zones_r10",
        SCRIPTS_DIR / "report_spacer_wall_zones.py",
    )
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "registry=PASS" in out
    assert "log=NO_LOG" in out
    assert "REJECT=0" in out
