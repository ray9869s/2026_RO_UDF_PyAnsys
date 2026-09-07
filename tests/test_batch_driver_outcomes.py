"""Dry-run must not hide skip_existing; labels must include mesh_id."""

from __future__ import annotations

from helpers import SCRIPTS_DIR, load_batch_solver_sweep, load_module


def _load_batch_meshing():
    return load_module("batch_meshing_outcomes_under_test", SCRIPTS_DIR / "batch_meshing.py")


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


def test_solver_existing_final_wins_over_dry_run():
    sweep = load_batch_solver_sweep()
    outcome, reason = sweep.classify_solver_pre_execution(
        skip_existing_final_data=True,
        final_pair_exists=True,
        dry_run=True,
    )
    assert outcome == "skipped_existing"
    assert reason == "existing final pair"


def test_solver_dry_run_when_finals_are_missing():
    sweep = load_batch_solver_sweep()
    outcome, reason = sweep.classify_solver_pre_execution(
        skip_existing_final_data=True,
        final_pair_exists=False,
        dry_run=True,
    )
    assert outcome == "dry_run"
    assert reason is None


def test_solver_skip_disabled_stays_dry_run_even_if_finals_exist():
    sweep = load_batch_solver_sweep()
    outcome, reason = sweep.classify_solver_pre_execution(
        skip_existing_final_data=False,
        final_pair_exists=True,
        dry_run=True,
    )
    assert outcome == "dry_run"
    assert reason is None


def test_mesh_existing_file_wins_over_dry_run():
    meshing = _load_batch_meshing()
    outcome, reason = meshing.classify_mesh_pre_execution(
        skip_existing_mesh=True,
        mesh_exists=True,
        dry_run=True,
    )
    assert outcome == "skipped_existing"
    assert reason == "existing mesh"


def test_mesh_dry_run_when_file_is_missing():
    meshing = _load_batch_meshing()
    outcome, reason = meshing.classify_mesh_pre_execution(
        skip_existing_mesh=True,
        mesh_exists=False,
        dry_run=True,
    )
    assert outcome == "dry_run"
    assert reason is None


def test_select_mesh_batch_cases_filters_sin_geo_id():
    meshing = _load_batch_meshing()
    batchcfg = meshing._load_module(
        "batch_config_select_under_test",
        meshing.BATCH_CONFIG_PATH,
    )
    cases = meshing.select_mesh_batch_cases(
        batchcfg.mesh_batch_cases,
        geo_id="S_a144_l1733",
    )
    assert len(cases) == 1
    assert cases[0]["geo_id"] == "S_a144_l1733"
    assert cases[0]["family"] == "sin"
