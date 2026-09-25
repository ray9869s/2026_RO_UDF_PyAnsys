"""Dry-run must not hide skip_existing; labels must include mesh_id."""

from __future__ import annotations

import hashlib

from helpers import SCRIPTS_DIR, load_batch_solver_sweep, load_module
from ro.manifest import write_run_manifest
from ro.paths import mesh_dir, run_dir
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    sha256_file,
)
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, run_payload


def _load_batch_meshing():
    return load_module("batch_meshing_outcomes_under_test", SCRIPTS_DIR / "batch_meshing.py")


def _write_solver_leaf(
    monkeypatch,
    tmp_path,
    *,
    stop_reason,
    stamp_hashes=True,
    attempt_id="attempt-1",
    case_bytes=b"cas-bytes",
    data_bytes=b"dat-bytes",
    mesh_bytes=b"mesh-bytes",
    write_manifest=True,
    write_finals=True,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    mesh_file = mesh_directory / f"{GEO_ID}_{MESH_ID}.msh.h5"
    mesh_file.write_bytes(mesh_bytes)
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    final_case = run_directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5"
    final_data = run_directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5"
    if write_finals:
        final_case.write_bytes(case_bytes)
        final_data.write_bytes(data_bytes)
    if write_manifest:
        payload = run_payload()
        payload["stop_reason"] = stop_reason
        payload["mesh_sha256"] = hashlib.sha256(mesh_bytes).hexdigest()
        payload[SOLVER_ATTEMPT_ID_FIELD] = attempt_id
        if stamp_hashes:
            payload[FINAL_CASE_SHA256_FIELD] = hashlib.sha256(case_bytes).hexdigest()
            payload[FINAL_DATA_SHA256_FIELD] = hashlib.sha256(data_bytes).hexdigest()
        write_run_manifest(run_directory, payload)
    return {
        "run_directory": run_directory,
        "final_case": final_case,
        "final_data": final_data,
        "mesh_file": mesh_file,
    }


def _classify(sweep, leaf, *, skip_existing=True, dry_run=True):
    return sweep.classify_solver_pre_execution(
        skip_existing_final_data=skip_existing,
        dry_run=dry_run,
        run_directory=leaf["run_directory"],
        final_case_path=leaf["final_case"],
        final_data_path=leaf["final_data"],
        mesh_file_path=leaf["mesh_file"],
    )


def test_solver_case_label_includes_mesh_id():
    sweep = load_batch_solver_sweep()
    assert sweep.solver_case_label(
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    ) == "D2450_a45/max085_min006_cpg5_bl4_peel2/u0p2_p6M"


def test_mesh_case_label_is_geo_and_mesh():
    meshing = _load_batch_meshing()
    assert meshing.mesh_case_label(
        "D2450_a45",
        "max120_min006_cpg5_bl4_peel2",
    ) == "D2450_a45/max120_min006_cpg5_bl4_peel2"


def test_solver_complete_current_attempt_wins_over_dry_run(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(monkeypatch, tmp_path, stop_reason="residual_converged")
    outcome, reason = _classify(sweep, leaf, dry_run=True)
    assert outcome == "skipped_existing"
    assert reason == "complete current-attempt finals"


def test_solver_files_only_are_not_skipped(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="residual_converged",
        write_manifest=False,
    )
    outcome, reason = _classify(sweep, leaf, dry_run=True)
    assert outcome == "dry_run"
    assert "run manifest not usable" in reason


def test_solver_running_manifest_with_finals_is_not_skipped(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="RUNNING",
        stamp_hashes=False,
    )
    outcome, reason = _classify(sweep, leaf, dry_run=False)
    assert outcome == "run"
    assert "not a skip-complete reason" in reason


def test_solver_new_terminal_reason_with_previous_files_is_not_skipped(
    monkeypatch, tmp_path
):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="residual_converged",
        stamp_hashes=False,
    )
    outcome, reason = _classify(sweep, leaf, dry_run=False)
    assert outcome == "run"
    assert reason == "missing current-attempt final artifact hashes"


def test_solver_stale_hash_does_not_skip(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="residual_converged",
        stamp_hashes=True,
        case_bytes=b"old-cas",
    )
    leaf["final_case"].write_bytes(b"new-cas")
    outcome, reason = _classify(sweep, leaf, dry_run=False)
    assert outcome == "run"
    assert "final case sha256" in reason


def test_solver_mesh_hash_mismatch_does_not_skip(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(monkeypatch, tmp_path, stop_reason="qoi_converged")
    leaf["mesh_file"].write_bytes(b"other-mesh")
    outcome, reason = _classify(sweep, leaf, dry_run=False)
    assert outcome == "run"
    assert "mesh_sha256" in reason


def test_solver_determination_failed_does_not_skip(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="stop_reason_determination_failed",
        stamp_hashes=False,
    )
    outcome, reason = _classify(sweep, leaf, dry_run=False)
    assert outcome == "run"
    assert "not a skip-complete reason" in reason


def test_solver_dry_run_when_finals_are_missing(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(
        monkeypatch,
        tmp_path,
        stop_reason="residual_converged",
        write_finals=False,
        stamp_hashes=False,
    )
    outcome, reason = _classify(sweep, leaf, dry_run=True)
    assert outcome == "dry_run"
    assert "was not found" in reason


def test_solver_skip_disabled_stays_dry_run_even_if_finals_exist(
    monkeypatch, tmp_path
):
    sweep = load_batch_solver_sweep()
    leaf = _write_solver_leaf(monkeypatch, tmp_path, stop_reason="residual_converged")
    outcome, reason = _classify(sweep, leaf, skip_existing=False, dry_run=True)
    assert outcome == "dry_run"
    assert reason is None


def test_sha256_file_matches_helper(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_mesh_dry_run_when_file_is_missing():
    meshing = _load_batch_meshing()
    outcome, reason = meshing.classify_mesh_pre_execution(
        skip_existing_mesh=True,
        dry_run=True,
        skip_block="mesh file was not found: missing.msh.h5",
    )
    assert outcome == "dry_run"
    assert "was not found" in reason


def test_select_mesh_batch_cases_filters_d0817_a30():
    meshing = _load_batch_meshing()
    batchcfg = meshing._load_module(
        "batch_config_select_under_test",
        meshing.BATCH_CONFIG_PATH,
    )
    cases = meshing.select_mesh_batch_cases(
        batchcfg.mesh_batch_cases,
        geo_id="D0817_a30",
    )
    assert len(cases) == 1
    assert cases[0]["geo_id"] == "D0817_a30"
    assert cases[0]["family"] == "diamond"
    assert cases[0]["mesh_id"] == "max085_min006_cpg7_bl4_peel2"
    parked_sin = meshing.select_mesh_batch_cases(
        batchcfg._MESH_BATCH_CASES_EXPLORATORY,
        geo_id="S_a144_l1733",
    )
    assert len(parked_sin) == 1
    assert parked_sin[0]["family"] == "sin"
