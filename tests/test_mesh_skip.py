"""Mesh skip requires a validated manifest, matching SHA, and current layout."""

from __future__ import annotations

import hashlib
import io

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_matrix import CASE_SET_PRODUCTION, cases_for_case_set
from ro.manifest import write_mesh_manifest
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload


def _load_batch_meshing():
    return load_module("batch_meshing_skip_under_test", SCRIPTS_DIR / "batch_meshing.py")


def _layout_from_payload(payload):
    return {
        "n_active_cells": payload["n_active_cells"],
        "cell_length_x_m": payload["cell_length_x_m"],
        "buffer_length_in_m": payload["buffer_length_in_m"],
        "buffer_length_out_m": payload["buffer_length_out_m"],
        "periodic_shift_y_m": payload["periodic_shift_y_m"],
    }


def _write_mesh_leaf(
    monkeypatch,
    tmp_path,
    *,
    mesh_bytes=b"mesh-bytes",
    write_manifest=True,
    empty_mesh=False,
    payload=None,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    mesh_file = directory / f"{GEO_ID}_{MESH_ID}.msh.h5"
    mesh_file.write_bytes(b"" if empty_mesh else mesh_bytes)
    if write_manifest:
        if payload is None:
            payload = mesh_payload()
        payload = dict(payload)
        payload["mesh_sha256"] = hashlib.sha256(mesh_file.read_bytes()).hexdigest()
        write_mesh_manifest(directory, payload)
    else:
        payload = mesh_payload()
    return {
        "directory": directory,
        "mesh_file": mesh_file,
        "payload": payload,
        "expected_layout": _layout_from_payload(payload),
    }


def _classify(meshing, leaf, *, skip_existing=True, dry_run=True, expected_layout=None):
    return meshing.classify_mesh_pre_execution(
        skip_existing_mesh=skip_existing,
        dry_run=dry_run,
        mesh_directory=leaf["directory"],
        mesh_file=leaf["mesh_file"],
        expected_layout=leaf["expected_layout"]
        if expected_layout is None
        else expected_layout,
    )


def test_expected_layout_merges_common_mesh_buffers():
    meshing = _load_batch_meshing()
    layout = meshing.expected_mesh_layout_from_case(
        {
            "n_active_cells": 7,
            "cell_length_x_m": 0.003465,
            "periodic_shift_y": 3.465,
        },
        {
            "buffer_length_in_m": 0.003465,
            "buffer_length_out_m": 0.00693,
        },
    )
    assert layout["n_active_cells"] == 7
    assert layout["buffer_length_out_m"] == 0.00693
    assert layout["periodic_shift_y_m"] == pytest.approx(3.465e-3)


def test_msh_only_is_not_skipped(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(monkeypatch, tmp_path, write_manifest=False)
    outcome, reason = _classify(meshing, leaf, dry_run=True)
    assert outcome == "dry_run"
    assert "mesh manifest not usable" in reason


def test_empty_msh_is_not_skipped(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(
        monkeypatch, tmp_path, write_manifest=False, empty_mesh=True
    )
    outcome, reason = _classify(meshing, leaf, dry_run=False)
    assert outcome == "run"
    assert "empty" in reason


def test_complete_mesh_skip_wins_over_dry_run(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(monkeypatch, tmp_path)
    outcome, reason = _classify(meshing, leaf, dry_run=True)
    assert outcome == "skipped_existing"
    assert reason == "complete current-case mesh"


def test_hash_mismatch_is_not_skipped(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(monkeypatch, tmp_path, mesh_bytes=b"old-mesh")
    leaf["mesh_file"].write_bytes(b"new-mesh")
    outcome, reason = _classify(meshing, leaf, dry_run=False)
    assert outcome == "run"
    assert "mesh_sha256" in reason


def test_layout_mismatch_is_not_skipped(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(monkeypatch, tmp_path)
    expected = dict(leaf["expected_layout"])
    expected["n_active_cells"] = 27
    outcome, reason = _classify(meshing, leaf, dry_run=False, expected_layout=expected)
    assert outcome == "run"
    assert "n_active_cells" in reason


def test_skip_disabled_stays_dry_run_even_if_complete(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    leaf = _write_mesh_leaf(monkeypatch, tmp_path)
    outcome, reason = _classify(meshing, leaf, skip_existing=False, dry_run=True)
    assert outcome == "dry_run"
    assert reason is None


def test_report_skip_lists_production_and_extra_leaves(monkeypatch, tmp_path):
    meshing = _load_batch_meshing()
    complete = _write_mesh_leaf(monkeypatch, tmp_path)
    extra_dir = mesh_dir(FAMILY, GEO_ID, "max120_min006_cpg5_bl4_peel2")
    extra_dir.mkdir(parents=True)
    extra_msh = extra_dir / f"{GEO_ID}_max120_min006_cpg5_bl4_peel2.msh.h5"
    extra_msh.write_bytes(b"extra-mesh")

    batchcfg = meshing._load_module(
        "batch_config_skip_report_under_test",
        meshing.BATCH_CONFIG_PATH,
    )
    production = cases_for_case_set(
        batchcfg,
        CASE_SET_PRODUCTION,
        exploratory_attr="mesh_batch_cases",
        production_attr="production_mesh_batch_cases",
    )
    buf = io.StringIO()
    rows = meshing.report_mesh_skip_status(
        production,
        batchcfg.common_mesh_settings,
        root=tmp_path / "meshes",
        file=buf,
    )
    prod_rows = [row for row in rows if row["in_production"]]
    extra_rows = [row for row in rows if not row["in_production"]]
    assert len(prod_rows) == 31
    complete_row = next(
        row
        for row in prod_rows
        if row["geo_id"] == GEO_ID and row["mesh_id"] == MESH_ID
    )
    assert complete_row["skip_decision"] == "SKIP"
    assert complete_row["manifest_valid"] is True
    assert complete_row["sha_match"] is True
    assert complete_row["mesh_directory"] == str(complete["directory"])
    extra = next(row for row in extra_rows if row["mesh_id"].startswith("max120"))
    assert extra["skip_decision"] == "RUN"
    assert extra["msh_exists"] is True
    assert extra["manifest_exists"] is False
    text = buf.getvalue()
    assert "MESH SKIP REPORT" in text
    assert "production 1 SKIP" in text
