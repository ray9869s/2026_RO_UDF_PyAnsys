"""Unit tests for the D2450 fresh-start reference diagnostic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR
from ro.manifest import write_mesh_manifest, write_run_manifest
from ro.paths import mesh_dir, run_dir
from test_manifest import mesh_payload, run_payload


def load_diagnostic():
    path = SCRIPTS_DIR / "check_d2450_fresh_start_reference.py"
    name = "check_d2450_fresh_start_reference_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses looks the class up in sys.modules during decoration.
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_compare_number_reports_abs_and_rel_diffs():
    diag = load_diagnostic()
    row = diag.compare_number(
        "u_mean_ms",
        0.199282,
        diag.EXPECTED_U_MEAN_MS,
        abs_tol=diag.U_MEAN_ABS_TOL,
    )
    assert row.abs_diff == pytest.approx(1.0e-6)
    assert row.rel_diff == pytest.approx(1.0e-6 / diag.EXPECTED_U_MEAN_MS)
    assert row.within_tol is False


def test_collect_comparisons_ok_against_archive_constants():
    diag = load_diagnostic()
    mesh = {
        "peel": diag.EXPECTED_PEEL,
        "cell_count": diag.EXPECTED_CELL_COUNT,
        "inlet_profile_G": diag.EXPECTED_INLET_PROFILE_G,
    }
    run = {"u_mean_ms": diag.EXPECTED_U_MEAN_MS}
    rows = {row.name: row for row in diag.collect_comparisons(
        mesh_payload=mesh,
        run_payload=run,
    )}
    assert rows["cell_count"].within_tol
    assert rows["u_mean_ms"].within_tol
    assert rows["inlet_profile_G"].within_tol


def test_collect_comparisons_warns_when_peel_disagrees():
    diag = load_diagnostic()
    mesh = {
        "peel": 0,
        "cell_count": diag.EXPECTED_CELL_COUNT,
        "inlet_profile_G": diag.EXPECTED_INLET_PROFILE_G,
    }
    rows = diag.collect_comparisons(mesh_payload=mesh, run_payload={
        "u_mean_ms": diag.EXPECTED_U_MEAN_MS,
    })
    cell = next(row for row in rows if row.name == "cell_count")
    assert "peel0" in cell.note or "987599" in cell.note


def test_main_warns_without_raising(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    diag = load_diagnostic()
    mesh_directory = mesh_dir(diag.FAMILY, diag.GEO_ID, diag.MESH_ID)
    run_directory = run_dir(diag.FAMILY, diag.GEO_ID, diag.MESH_ID, diag.RUN_ID)
    mesh_directory.mkdir(parents=True)
    run_directory.mkdir(parents=True)

    mesh = mesh_payload()
    mesh["cell_count"] = diag.EXPECTED_CELL_COUNT + 10
    mesh["peel"] = diag.EXPECTED_PEEL
    mesh["inlet_profile_G"] = diag.EXPECTED_INLET_PROFILE_G
    write_mesh_manifest(mesh_directory, mesh)

    run = run_payload()
    run["u_mean_ms"] = diag.EXPECTED_U_MEAN_MS
    write_run_manifest(run_directory, run)

    assert diag.main([]) == 0
    captured = capsys.readouterr().out
    assert "cell_count:" in captured
    assert "WARNING: cell_count drifted" in captured
    assert "260822_RO_UDF.c" in captured or "abs_diff=" in captured
