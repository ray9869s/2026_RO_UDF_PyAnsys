"""Production 31-mesh / 279-run matrix is generated from the registry."""

from __future__ import annotations

from collections import Counter

import pytest

from helpers import (
    CONFIGS_DIR,
    SCRIPTS_DIR,
    apply_json_overrides,
    load_batch_solver_sweep,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)
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
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.solver_common import (
    make_base_case_name,
    merge_batch_case_overrides,
    require_run_id_matches_operating_point,
    resolve_input_mode,
)


def _load_batch_config():
    return load_module("batch_config_campaign_matrix", CONFIGS_DIR / "batch_config.py")


def _load_batch_meshing():
    return load_module("batch_meshing_campaign_matrix", SCRIPTS_DIR / "batch_meshing.py")


@pytest.fixture
def batchcfg():
    return _load_batch_config()


def test_exploratory_lists_are_unchanged(batchcfg):
    assert len(batchcfg._MESH_BATCH_CASES_EXPLORATORY) == 22
    parked_families = Counter(
        case["family"] for case in batchcfg._MESH_BATCH_CASES_EXPLORATORY
    )
    assert parked_families == {"ml": 3, "pillar": 9, "sin": 9, "empty": 1}
    assert all(
        case["family"] != "diamond"
        for case in batchcfg._MESH_BATCH_CASES_EXPLORATORY
    )
    assert len(batchcfg.mesh_batch_cases) == 1
    assert len(batchcfg._SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS) == 7
    assert len(batchcfg.solver_sweep_cases) == 1
    assert (
        batchcfg.solver_sweep_cases
        is not batchcfg._SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS
    )
    assert batchcfg.solver_sweep_cases is not batchcfg._SOLVER_SWEEP_CASES_GTS_PILOT
    assert batchcfg.solver_sweep_cases is not batchcfg._SOLVER_SWEEP_CASES_CPG7_PILOT
    assert batchcfg.solver_sweep_cases is not batchcfg._SOLVER_SWEEP_CASES_D0817_SRC0
    assert (
        batchcfg.solver_sweep_cases
        is not batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_SRC0
    )
    assert (
        batchcfg.solver_sweep_cases
        is not batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_IC01
    )
    assert (
        batchcfg.solver_sweep_cases
        is not batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_BLEND0
    )
    assert (
        batchcfg.solver_sweep_cases
        is not batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_BLEND1
    )
    assert batchcfg.mesh_batch_cases is not batchcfg._MESH_BATCH_CASES_EXPLORATORY
    assert batchcfg.mesh_batch_cases is not batchcfg._MESH_BATCH_CASES_CPG7
    assert batchcfg.skip_existing_mesh is True
    assert batchcfg.skip_existing_final_data is True


def test_exploratory_mesh_is_the_p_p100_h00_max060_leaf(batchcfg):
    cases = batchcfg.mesh_batch_cases
    assert len(cases) == 1
    case = cases[0]
    geometry = geometry_parameters_for_geo_id("P_p100_h00")
    assert case["family"] == "pillar"
    assert case["geo_id"] == "P_p100_h00"
    assert case["mesh_id"] == batchcfg._MESH_ID_MAX060
    assert case["mesh_id"] == "max060_min006_cpg5_bl4_peel2"
    assert case["m_max"] == pytest.approx(0.060)
    assert case["m_min"] == batchcfg._COMMON_MESH["m_min"]
    assert case["m_cpg"] == batchcfg._COMMON_MESH["m_cpg"]
    assert case["m_cpg"] == 5
    assert case["bl_layers"] == batchcfg._COMMON_MESH["bl_layers"]
    assert case["peel_layers"] == batchcfg._COMMON_MESH["peel_layers"]
    assert case["n_active_cells"] == geometry["n_active_cells"]
    assert case["cell_length_x_m"] == pytest.approx(geometry["cell_length_x_m"])
    assert case["periodic_shift_y"] == pytest.approx(
        float(geometry["periodic_shift_y_m"]) * 1.0e3
    )
    assert case["spacing_code"] == geometry["spacing_code"]
    assert case["wall_spacer_labels"] == list(geometry["spacer_wall_zones"])
    assert case["filament_d_m"] == pytest.approx(geometry["filament_d_m"])
    assert case["bridge_radius_m"] == pytest.approx(geometry["bridge_radius_m"])
    assert case["overlap_m"] == pytest.approx(geometry["overlap_m"])
    assert case["active_membrane_wall_labels"] == batchcfg._COMMON_MESH[
        "active_membrane_wall_labels"
    ]
    assert case["buffer_wall_labels"] == batchcfg._COMMON_MESH["buffer_wall_labels"]
    assert case["periodic_after_surface_mesh"] is True
    for gate in (
        "min_orthogonal_quality_threshold",
        "max_aspect_ratio_threshold",
        "max_skewness_threshold",
    ):
        assert gate not in case
    production = next(
        entry
        for entry in batchcfg.production_mesh_batch_cases
        if entry["geo_id"] == "P_p100_h00"
    )
    assert production["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert production["m_max"] == pytest.approx(0.085)
    assert set(case) == set(production)
    changed = {key for key in production if case[key] != production[key]}
    assert changed == {"m_max", "mesh_id"}
    parked_cpg7 = batchcfg._MESH_BATCH_CASES_CPG7
    assert len(parked_cpg7) == 1
    assert parked_cpg7[0]["geo_id"] == "D0817_a30"
    assert parked_cpg7[0]["mesh_id"] == batchcfg._MESH_ID_CPG7
    assert parked_cpg7[0]["m_cpg"] == 7


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
    assert meshing.parse_batch_meshing_cli([]).dry_run is False
    assert meshing.parse_batch_meshing_cli(["--dry-run"]).dry_run is True
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
    assert len(exploratory_mesh) == 1
    assert exploratory_mesh[0]["mesh_id"] == "max060_min006_cpg5_bl4_peel2"
    assert exploratory_mesh[0]["geo_id"] == "P_p100_h00"
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
    assert len(exploratory_solver) == 1
    assert len(production_solver) == 279


def test_exploratory_solver_includes_d0817_g_and_30cell_pilots(batchcfg):
    by_geo = {
        case["geo_id"]: case
        for case in batchcfg._SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS
    }
    assert by_geo["D0817_a60"]["mesh_id"] == PRODUCTION_MESH_ID_D0817_A60
    assert by_geo["D0817_a60"]["run_id"] == "u0p2_p6M"
    assert by_geo["D0817_a60"]["inlet_velocity_value"] == pytest.approx(0.2)
    assert by_geo["D0817_a60"]["outlet_gauge_pressure"] == pytest.approx(6.0e6)
    assert by_geo["D0817_a30"]["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert by_geo["D0817_a30"]["run_id"] == "u0p2_p6M"
    assert by_geo["D0817_a30"]["inlet_velocity_value"] == pytest.approx(0.2)
    geos = [
        case["geo_id"] for case in batchcfg._SOLVER_SWEEP_CASES_EXPLORATORY_PILOTS
    ]
    assert geos.index("D0817_a60") < geos.index("D0817_a30")


def test_exploratory_solver_is_the_p_p100_h00_max060_u0p2(batchcfg):
    cases = batchcfg.solver_sweep_cases
    assert len(cases) == 1
    case = cases[0]
    assert case["family"] == "pillar"
    assert case["geo_id"] == "P_p100_h00"
    assert case["mesh_id"] == batchcfg._MESH_ID_MAX060
    assert case["mesh_id"] == "max060_min006_cpg5_bl4_peel2"
    assert case["mesh_id"] != PRODUCTION_MESH_ID_DEFAULT
    assert case["run_id"] == "u0p2_p6M"
    assert case["case_name"] == "u0p2_p6M"
    assert case["inlet_velocity_value"] == pytest.approx(0.2)
    assert case["outlet_gauge_pressure"] == pytest.approx(6.0e6)
    assert "max_iterations" not in case
    assert "enable_qoi_convergence_stop" not in case
    assert "first_to_second_order_blending" not in case
    assert "disable_membrane_source_terms" not in case
    assert "pseudo_time_verbosity" not in case
    assert "pseudo_time_time_step_size_scale_factor" not in case
    assert "restart_from_case_file" not in case
    assert "restart_from_data_file" not in case
    production = next(
        entry
        for entry in batchcfg.production_solver_sweep_cases
        if entry["geo_id"] == "P_p100_h00" and entry["run_id"] == "u0p2_p6M"
    )
    assert production["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert set(case) == set(production)
    changed = {key for key in production if case[key] != production[key]}
    assert changed == {"mesh_id"}
    merged = merge_batch_case_overrides(batchcfg.common_solver_settings, case)
    assert merged["max_iterations"] == 2000
    assert merged["residual_target"] == pytest.approx(1e-7)
    assert merged["use_inlet_velocity_profile"] is True
    assert "enable_qoi_convergence_stop" not in merged
    assert "first_to_second_order_blending" not in merged
    assert "disable_membrane_source_terms" not in merged
    assert "pseudo_time_verbosity" not in merged
    assert "pseudo_time_time_step_size_scale_factor" not in merged
    assert resolve_input_mode(merged) == ("mesh_initialization", None, None)
    require_run_id_matches_operating_point(
        case["run_id"],
        case["inlet_velocity_value"],
        case["outlet_gauge_pressure"],
    )
    parked_gts = batchcfg._SOLVER_SWEEP_CASES_GTS_PILOT
    assert len(parked_gts) == 1
    assert parked_gts[0]["run_id"] == "u0p3_p6M_ptgts3"
    assert parked_gts[0]["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert parked_gts[0]["pseudo_time_time_step_size_scale_factor"] == pytest.approx(
        3.0
    )
    parked_cpg7 = batchcfg._SOLVER_SWEEP_CASES_CPG7_PILOT
    assert len(parked_cpg7) == 1
    assert parked_cpg7[0]["mesh_id"] == batchcfg._MESH_ID_CPG7
    assert parked_cpg7[0]["run_id"] == "u0p3_p6M"
    assert parked_cpg7[0]["mesh_id"] != case["mesh_id"]
    parked_d0817_src0 = batchcfg._SOLVER_SWEEP_CASES_D0817_SRC0
    assert len(parked_d0817_src0) == 1
    assert parked_d0817_src0[0]["geo_id"] == "D0817_a30"
    assert parked_d0817_src0[0]["run_id"] == "u0p3_p6M_src0"
    assert parked_d0817_src0[0]["disable_membrane_source_terms"] is True
    parked_pillar_src0 = batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_SRC0
    assert len(parked_pillar_src0) == 1
    assert parked_pillar_src0[0]["geo_id"] == "P_p100_h00"
    assert parked_pillar_src0[0]["run_id"] == "u0p2_p6M_src0"
    assert parked_pillar_src0[0]["disable_membrane_source_terms"] is True
    parked_ic01 = batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_IC01
    assert len(parked_ic01) == 1
    assert parked_ic01[0]["run_id"] == "u0p2_p6M_ic01"
    assert parked_ic01[0]["geo_id"] == "P_p100_h00"
    assert "restart_from_case_file" in parked_ic01[0]
    parked_blend0 = batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_BLEND0
    assert len(parked_blend0) == 1
    assert parked_blend0[0]["run_id"] == "u0p2_p6M_blend0"
    assert parked_blend0[0]["geo_id"] == "P_p100_h00"
    assert parked_blend0[0]["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert parked_blend0[0]["first_to_second_order_blending"] == pytest.approx(0.0)
    assert parked_blend0[0]["max_iterations"] == 1000
    assert parked_blend0[0]["enable_qoi_convergence_stop"] is False
    assert "restart_from_case_file" not in parked_blend0[0]
    parked_blend1 = batchcfg._SOLVER_SWEEP_CASES_P_P100_H00_BLEND1
    assert len(parked_blend1) == 1
    assert parked_blend1[0]["run_id"] == "u0p2_p6M_blend1"
    assert parked_blend1[0]["geo_id"] == "P_p100_h00"
    assert parked_blend1[0]["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT
    assert parked_blend1[0]["first_to_second_order_blending"] == pytest.approx(1.0)
    assert "restart_from_case_file" in parked_blend1[0]
    production_ids = {
        (entry["geo_id"], entry["mesh_id"], entry["run_id"])
        for entry in batchcfg.production_solver_sweep_cases
    }
    assert ("P_p100_h00", PRODUCTION_MESH_ID_DEFAULT, "u0p1_p6M") in production_ids
    assert ("P_p100_h00", PRODUCTION_MESH_ID_DEFAULT, "u0p2_p6M") in production_ids
    assert (
        "P_p100_h00",
        PRODUCTION_MESH_ID_DEFAULT,
        "u0p2_p6M_ic01",
    ) not in production_ids
    assert (
        "P_p100_h00",
        PRODUCTION_MESH_ID_DEFAULT,
        "u0p2_p6M_src0",
    ) not in production_ids
    assert (
        "P_p100_h00",
        PRODUCTION_MESH_ID_DEFAULT,
        "u0p2_p6M_blend0",
    ) not in production_ids
    assert (
        "P_p100_h00",
        PRODUCTION_MESH_ID_DEFAULT,
        "u0p2_p6M_blend1",
    ) not in production_ids
    assert (
        "P_p100_h00",
        batchcfg._MESH_ID_MAX060,
        "u0p2_p6M",
    ) not in production_ids
    assert (
        "D0817_a30",
        PRODUCTION_MESH_ID_DEFAULT,
        "u0p3_p6M_src0",
    ) not in production_ids
    assert ("D0817_a30", PRODUCTION_MESH_ID_DEFAULT, "u0p3_p6M") in production_ids
    assert (
        "D0817_a30",
        batchcfg._MESH_ID_CPG7,
        "u0p3_p6M",
    ) not in production_ids
    assert "ptgts3" not in case["run_id"]
    assert "cpg7" not in case["mesh_id"]
    assert "p4M" not in case["run_id"]
    assert "p8M" not in case["run_id"]
    assert "src0" not in case["run_id"]
    assert "ic01" not in case["run_id"]
    assert "blend0" not in case["run_id"]
    assert "blend1" not in case["run_id"]


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
    assert sweep.parse_batch_solver_sweep_cli([]).outlet_gauge_pressures is None
    assert sweep.parse_batch_solver_sweep_cli([]).dry_run is False


def test_pressure_filter_keeps_p6m_column_and_refuses_unknown(batchcfg):
    sweep = load_batch_solver_sweep()
    production = batchcfg.production_solver_sweep_cases
    selected = sweep.filter_solver_cases_by_outlet_gauge_pressure(
        production, [6.0e6]
    )
    assert len(selected) == 93
    assert {case["outlet_gauge_pressure"] for case in selected} == {6.0e6}
    assert {case["run_id"] for case in selected} == {
        "u0p1_p6M",
        "u0p2_p6M",
        "u0p3_p6M",
    }
    assert len({case["geo_id"] for case in selected}) == 31
    assert [case["geo_id"] for case in selected] == [
        case["geo_id"]
        for case in production
        if case["outlet_gauge_pressure"] == 6.0e6
    ]
    assert sweep.filter_solver_cases_by_outlet_gauge_pressure(
        production, None
    ) == production
    with pytest.raises(ValueError, match="not in the selected case-set"):
        sweep.filter_solver_cases_by_outlet_gauge_pressure(production, [5.0e6])
    args = sweep.parse_batch_solver_sweep_cli(
        [
            "--case-set",
            "production",
            "--outlet-gauge-pressure",
            "6.0e6",
            "--dry-run",
        ]
    )
    assert args.case_set == "production"
    assert args.outlet_gauge_pressures == [6.0e6]
    assert args.dry_run is True


def test_format_selected_solver_case_prints_four_id():
    sweep = load_batch_solver_sweep()
    line = sweep.format_selected_solver_case(
        1,
        93,
        {
            "family": "diamond",
            "geo_id": "D0817_a30",
            "mesh_id": "max085_min006_cpg5_bl4_peel2",
            "run_id": "u0p2_p6M",
        },
    )
    assert line == (
        "  1/93  diamond/D0817_a30/max085_min006_cpg5_bl4_peel2/u0p2_p6M"
    )


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


def test_format_selected_mesh_case_prints_three_id():
    meshing = _load_batch_meshing()
    line = meshing.format_selected_mesh_case(
        1,
        1,
        {
            "family": "pillar",
            "geo_id": "P_p100_h00",
            "mesh_id": "max060_min006_cpg5_bl4_peel2",
        },
    )
    assert line == "  1/1  pillar/P_p100_h00/max060_min006_cpg5_bl4_peel2"


def test_cpg7_mesh_manifest_preserves_27_cell_layout(batchcfg):
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    meshing = _load_batch_meshing()
    case = batchcfg._MESH_BATCH_CASES_CPG7[0]
    overrides = meshing._build_overrides(case, batchcfg.common_mesh_settings)
    apply_json_overrides(cfg, overrides)
    geometry = geometry_parameters_for_geo_id("D0817_a30")
    payload = build_mesh_manifest_payload(
        cfg,
        {
            "min_orthogonal_quality": 0.12,
            "max_aspect_ratio": 42.0,
            "max_skewness": 0.78,
            "skewed_face_fraction": 1.0e-6,
            "cell_count": 2000000,
        },
        "a" * 64,
        created_utc="2026-09-25T00:00:00Z",
    )
    assert payload["family"] == "diamond"
    assert payload["geo_id"] == "D0817_a30"
    assert payload["mesh_id"] == "max085_min006_cpg7_bl4_peel2"
    assert payload["n_active_cells"] == 27
    assert payload["n_active_cells"] != batchcfg._COMMON_MESH["n_active_cells"]
    assert payload["cell_length_x_m"] == pytest.approx(geometry["cell_length_x_m"])
    assert payload["periodic_shift_y_m"] == pytest.approx(
        geometry["periodic_shift_y_m"]
    )
    assert payload["attack_angle_deg"] == pytest.approx(geometry["attack_angle_deg"])
    assert payload["cpg"] == 7
    assert payload["max_size_mm"] == batchcfg._COMMON_MESH["m_max"]
    assert payload["min_size_mm"] == batchcfg._COMMON_MESH["m_min"]
    assert payload["bl"] == batchcfg._COMMON_MESH["bl_layers"]
    assert payload["peel"] == batchcfg._COMMON_MESH["peel_layers"]
    assert payload["membrane_wall_base_names"] == batchcfg._COMMON_MESH[
        "active_membrane_wall_labels"
    ]
    assert payload["buffer_wall_base_names"] == batchcfg._COMMON_MESH[
        "buffer_wall_labels"
    ]
    assert payload["n_buffer_in"] == batchcfg._COMMON_MESH["n_buffer_in"]
    assert payload["n_buffer_out"] == batchcfg._COMMON_MESH["n_buffer_out"]
    assert payload["n_lead_excluded"] == batchcfg._COMMON_MESH["n_lead_excluded"]


def test_p_p100_h00_max060_mesh_manifest_stamps_m_max(batchcfg):
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    meshing = _load_batch_meshing()
    case = batchcfg.mesh_batch_cases[0]
    overrides = meshing._build_overrides(case, batchcfg.common_mesh_settings)
    apply_json_overrides(cfg, overrides)
    geometry = geometry_parameters_for_geo_id("P_p100_h00")
    payload = build_mesh_manifest_payload(
        cfg,
        {
            "min_orthogonal_quality": 0.12,
            "max_aspect_ratio": 42.0,
            "max_skewness": 0.78,
            "skewed_face_fraction": 1.0e-6,
            "cell_count": 2000000,
        },
        "a" * 64,
        created_utc="2026-09-28T00:00:00Z",
    )
    assert payload["family"] == "pillar"
    assert payload["geo_id"] == "P_p100_h00"
    assert payload["mesh_id"] == "max060_min006_cpg5_bl4_peel2"
    assert payload["n_active_cells"] == geometry["n_active_cells"]
    assert payload["cell_length_x_m"] == pytest.approx(geometry["cell_length_x_m"])
    assert payload["periodic_shift_y_m"] == pytest.approx(
        geometry["periodic_shift_y_m"]
    )
    assert payload["cpg"] == 5
    assert payload["max_size_mm"] == pytest.approx(0.060)
    assert payload["max_size_mm"] != batchcfg._COMMON_MESH["m_max"]
    assert payload["min_size_mm"] == batchcfg._COMMON_MESH["m_min"]
    assert payload["bl"] == batchcfg._COMMON_MESH["bl_layers"]
    assert payload["peel"] == batchcfg._COMMON_MESH["peel_layers"]
    assert payload["membrane_wall_base_names"] == batchcfg._COMMON_MESH[
        "active_membrane_wall_labels"
    ]
    assert payload["buffer_wall_base_names"] == batchcfg._COMMON_MESH[
        "buffer_wall_labels"
    ]
