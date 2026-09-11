"""Production 31-mesh / 279-run matrix is generated from the registry."""

from __future__ import annotations

from collections import Counter

import pytest

from helpers import CONFIGS_DIR, SCRIPTS_DIR, load_batch_solver_sweep, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_IDS, assert_selected_cases_are_not_legacy_ml
from ro.campaign_geometry import _DIAMOND_LAYOUTS, geometry_parameters_for_geo_id
from ro.campaign_matrix import (
    CASE_SET_EXPLORATORY,
    CASE_SET_PRODUCTION,
    EXPECTED_OPERATING_POINTS_PER_GEO,
    EXPECTED_PRODUCTION_MESH_COUNT,
    EXPECTED_PRODUCTION_SOLVER_COUNT,
    LAYOUT_KEYS_NOT_FROM_COMMON,
    PRODUCTION_MESH_ID_D0817_A60,
    PRODUCTION_MESH_ID_DEFAULT,
    build_production_mesh_batch_cases,
    build_production_solver_sweep_cases,
    cases_for_case_set,
    mesh_settings_without_layout,
)
from ro.solver_common import make_base_case_name


def _load_batch_config():
    return load_module("batch_config_campaign_matrix", CONFIGS_DIR / "batch_config.py")


def _load_batch_meshing():
    return load_module("batch_meshing_campaign_matrix", SCRIPTS_DIR / "batch_meshing.py")


@pytest.fixture
def batchcfg():
    return _load_batch_config()


def test_exploratory_lists_are_unchanged(batchcfg):
    assert len(batchcfg.mesh_batch_cases) == 22
    assert len(batchcfg.solver_sweep_cases) == 7
    families = Counter(case["family"] for case in batchcfg.mesh_batch_cases)
    assert families == {"ml": 3, "pillar": 9, "sin": 9, "empty": 1}
    assert all(case["family"] != "diamond" for case in batchcfg.mesh_batch_cases)


def test_production_lists_are_distinct_from_exploratory(batchcfg):
    assert batchcfg.production_mesh_batch_cases is not batchcfg.mesh_batch_cases
    assert batchcfg.production_solver_sweep_cases is not batchcfg.solver_sweep_cases
    assert len(batchcfg.production_mesh_batch_cases) == EXPECTED_PRODUCTION_MESH_COUNT
    assert len(batchcfg.production_solver_sweep_cases) == EXPECTED_PRODUCTION_SOLVER_COUNT


def test_production_mesh_count_and_distinct_mesh_ids(batchcfg):
    mesh_ids = {case["mesh_id"] for case in batchcfg.production_mesh_batch_cases}
    assert len(batchcfg.production_mesh_batch_cases) == 31
    assert len(mesh_ids) == 2
    assert mesh_ids == {PRODUCTION_MESH_ID_DEFAULT, PRODUCTION_MESH_ID_D0817_A60}
    by_geo = {case["geo_id"]: case["mesh_id"] for case in batchcfg.production_mesh_batch_cases}
    assert by_geo["D0817_a60"] == PRODUCTION_MESH_ID_D0817_A60
    for geo_id, mesh_id in by_geo.items():
        if geo_id != "D0817_a60":
            assert mesh_id == PRODUCTION_MESH_ID_DEFAULT


def test_production_layout_matches_registry_per_geometry(batchcfg):
    """n_active_cells / pitch / periodic_shift_y come from the registry, not _COMMON_MESH."""
    assert batchcfg._COMMON_MESH["n_active_cells"] == 7
    assert batchcfg._COMMON_MESH["cell_length_x_m"] == 0.003465
    assert batchcfg._COMMON_MESH["periodic_shift_y"] == 3.465
    for case in batchcfg.production_mesh_batch_cases:
        geometry = geometry_parameters_for_geo_id(case["geo_id"])
        assert case["n_active_cells"] == geometry["n_active_cells"]
        assert case["cell_length_x_m"] == pytest.approx(geometry["cell_length_x_m"])
        assert case["periodic_shift_y"] == pytest.approx(
            geometry["periodic_shift_y_m"] * 1.0e3
        )


def test_production_diamond_layout_matches_diamond_layouts_table(batchcfg):
    by_geo = {case["geo_id"]: case for case in batchcfg.production_mesh_batch_cases}
    for geo_id, (n_active, pitch_mm, periodic_dy_mm, _angle) in _DIAMOND_LAYOUTS.items():
        case = by_geo[geo_id]
        assert case["n_active_cells"] == n_active
        assert case["cell_length_x_m"] == pytest.approx(pitch_mm * 1.0e-3)
        assert case["periodic_shift_y"] == pytest.approx(periodic_dy_mm)


def test_common_mesh_layout_is_stripped_before_overlay():
    poisoned = {
        "n_active_cells": 7,
        "cell_length_x_m": 0.003465,
        "periodic_shift_y": 3.465,
        "m_max": 0.085,
        "m_min": 0.006,
        "m_cpg": 5,
        "bl_layers": 4,
        "peel_layers": 2,
        "attack_angle_deg": 45,
    }
    stripped = mesh_settings_without_layout(poisoned)
    assert LAYOUT_KEYS_NOT_FROM_COMMON.isdisjoint(stripped)
    cases = build_production_mesh_batch_cases(poisoned)
    d0817_a30 = next(case for case in cases if case["geo_id"] == "D0817_a30")
    assert d0817_a30["n_active_cells"] == 27
    assert d0817_a30["n_active_cells"] != poisoned["n_active_cells"]
    d0817_a60 = next(case for case in cases if case["geo_id"] == "D0817_a60")
    assert d0817_a60["m_max"] == 0.060
    assert d0817_a60["mesh_id"] == PRODUCTION_MESH_ID_D0817_A60


def test_only_d0817_a60_uses_finer_m_max(batchcfg):
    for case in batchcfg.production_mesh_batch_cases:
        if case["geo_id"] == "D0817_a60":
            assert case["m_max"] == 0.060
        else:
            assert case["m_max"] == 0.085


def test_production_solver_run_ids_match_make_base_case_name(batchcfg):
    geo_ids = {case["geo_id"] for case in batchcfg.production_solver_sweep_cases}
    assert geo_ids == set(CAMPAIGN_GEO_IDS)
    counts = Counter(case["geo_id"] for case in batchcfg.production_solver_sweep_cases)
    assert all(count == EXPECTED_OPERATING_POINTS_PER_GEO for count in counts.values())
    keys = [
        (case["geo_id"], case["mesh_id"], case["run_id"])
        for case in batchcfg.production_solver_sweep_cases
    ]
    assert len(keys) == len(set(keys))
    for case in batchcfg.production_solver_sweep_cases:
        assert case["run_id"] == make_base_case_name(
            case["inlet_velocity_value"],
            case["outlet_gauge_pressure"],
        )
        assert case["case_name"] == case["run_id"]


def test_solver_cases_attach_to_selected_mesh_leaves(batchcfg):
    mesh_id_by_geo = {
        case["geo_id"]: case["mesh_id"]
        for case in batchcfg.production_mesh_batch_cases
    }
    rebuilt = build_production_solver_sweep_cases(batchcfg.production_mesh_batch_cases)
    assert rebuilt == batchcfg.production_solver_sweep_cases
    for case in rebuilt:
        assert case["mesh_id"] == mesh_id_by_geo[case["geo_id"]]


def test_default_case_set_is_exploratory(batchcfg):
    meshing = _load_batch_meshing()
    sweep = load_batch_solver_sweep()
    assert meshing.parse_batch_meshing_cli([]).case_set == CASE_SET_EXPLORATORY
    assert sweep.parse_batch_solver_sweep_cli([]).case_set == CASE_SET_EXPLORATORY
    exploratory_mesh = cases_for_case_set(
        batchcfg,
        CASE_SET_EXPLORATORY,
        exploratory_attr="mesh_batch_cases",
        production_attr="production_mesh_batch_cases",
    )
    production_mesh = cases_for_case_set(
        batchcfg,
        CASE_SET_PRODUCTION,
        exploratory_attr="mesh_batch_cases",
        production_attr="production_mesh_batch_cases",
    )
    assert len(exploratory_mesh) == 22
    assert len(production_mesh) == 31
    exploratory_solver = cases_for_case_set(
        batchcfg,
        CASE_SET_EXPLORATORY,
        exploratory_attr="solver_sweep_cases",
        production_attr="production_solver_sweep_cases",
    )
    production_solver = cases_for_case_set(
        batchcfg,
        CASE_SET_PRODUCTION,
        exploratory_attr="solver_sweep_cases",
        production_attr="production_solver_sweep_cases",
    )
    assert len(exploratory_solver) == 7
    assert len(production_solver) == 279


def test_exploratory_solver_includes_d0817_g_and_30cell_pilots(batchcfg):
    by_geo = {case["geo_id"]: case for case in batchcfg.solver_sweep_cases}
    assert by_geo["D0817_a60"]["mesh_id"] == PRODUCTION_MESH_ID_D0817_A60
    assert by_geo["D0817_a60"]["run_id"] == "u0p2_p6M"
    assert by_geo["D0817_a60"]["inlet_velocity_value"] == pytest.approx(0.2)
    assert by_geo["D0817_a60"]["outlet_gauge_pressure"] == pytest.approx(6.0e6)
    assert by_geo["D0817_a30"]["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert by_geo["D0817_a30"]["run_id"] == "u0p2_p6M"
    assert by_geo["D0817_a30"]["inlet_velocity_value"] == pytest.approx(0.2)
    geos = [case["geo_id"] for case in batchcfg.solver_sweep_cases]
    assert geos.index("D0817_a60") < geos.index("D0817_a30")


def test_geo_id_filter_keeps_case_set_order_and_refuses_unknown():
    sweep = load_batch_solver_sweep()
    cases = [
        {"geo_id": "D0817_a60", "run_id": "u0p2_p6M"},
        {"geo_id": "D0817_a30", "run_id": "u0p2_p6M"},
        {"geo_id": "REF_empty", "run_id": "u0p2_p6M"},
    ]
    assert sweep.filter_solver_cases_by_geo_id(cases, None) == cases
    selected = sweep.filter_solver_cases_by_geo_id(
        cases, ["D0817_a30", "D0817_a60", "D0817_a60"]
    )
    assert [case["geo_id"] for case in selected] == ["D0817_a60", "D0817_a30"]
    with pytest.raises(ValueError, match="not in the selected case-set"):
        sweep.filter_solver_cases_by_geo_id(cases, ["D2450_a45"])
    args = sweep.parse_batch_solver_sweep_cli(
        ["--geo-id", "D0817_a60", "--geo-id", "D0817_a30"]
    )
    assert args.geo_ids == ["D0817_a60", "D0817_a30"]
    assert sweep.parse_batch_solver_sweep_cli([]).geo_ids is None


def test_production_solver_cases_pass_inlet_profile_gate(batchcfg):
    sweep = load_batch_solver_sweep()
    sweep.require_explicit_inlet_velocity_profile(
        batchcfg.common_solver_settings,
        batchcfg.production_solver_sweep_cases,
    )


def test_batch_lists_have_no_legacy_ml_geo_ids(batchcfg):
    assert_selected_cases_are_not_legacy_ml(batchcfg.mesh_batch_cases)
    assert_selected_cases_are_not_legacy_ml(batchcfg.solver_sweep_cases)
    assert_selected_cases_are_not_legacy_ml(batchcfg.production_mesh_batch_cases)
    assert_selected_cases_are_not_legacy_ml(batchcfg.production_solver_sweep_cases)
