"""Unit tests for manifest geometry validation helpers."""

from __future__ import annotations

import math

import pytest

from ro.campaign_geometry import (
    CAMPAIGN_H_M,
    compute_curvature_margin,
    geometry_parameters_for_geo_id,
    membrane_contact_width_m,
)
from ro.manifest_errors import ManifestError
from ro.manifest_validation import (
    collect_spacer_wall_zones_from_fluent,
    derive_periodic_shift_y_from_angle_m,
    validate_curvature_margin,
    validate_joint_sphere_consistency,
    validate_mesh_geometry_fields,
    validate_metre_field_scales,
    validate_periodic_shift_y,
    validate_sigma_d_invariant,
    validate_spacer_wall_zones,
)
from test_manifest import mesh_payload, run_payload


def _diamond_mesh_payload(**updates):
    payload = mesh_payload()
    payload.update(updates)
    return payload


def test_derive_periodic_shift_y_formula():
    lf_m = 0.002450124996
    derived = derive_periodic_shift_y_from_angle_m(lf_m, 45.0)
    assert derived == pytest.approx(lf_m / math.sin(math.radians(45.0)))


def test_explicit_source_does_not_invoke_derivation_check():
    payload = _diamond_mesh_payload(periodic_shift_y_source="explicit")
    payload["periodic_shift_y_m"] = 0.004
    validate_periodic_shift_y(payload)


def test_metre_fields_reject_millimetre_leakage():
    payload = _diamond_mesh_payload(periodic_shift_y_m=3.465)
    with pytest.raises(ManifestError, match="mm leaked into a metre field"):
        validate_metre_field_scales(payload, kind="Mesh")


def test_metre_fields_reject_micrometre_leakage():
    payload = _diamond_mesh_payload(periodic_shift_y_m=3.465e-6)
    with pytest.raises(ManifestError, match="micrometres leaked into a metre field"):
        validate_metre_field_scales(payload, kind="Mesh")


def test_metre_field_list_rejects_millimetre_leakage():
    payload = _diamond_mesh_payload(layer_diameters_m=[3.465, 3.465, 3.465])
    with pytest.raises(ManifestError, match=r"layer_diameters_m\[0\]"):
        validate_metre_field_scales(payload, kind="Mesh")


def test_plausible_metre_fields_pass_scale_guard():
    validate_metre_field_scales(mesh_payload(), kind="Mesh")
    validate_metre_field_scales(run_payload(), kind="Run")


def test_feature_scale_rejects_both_unit_leaks():
    high = _diamond_mesh_payload(filament_d_m=0.4)  # 0.4 mm written as metres
    with pytest.raises(ManifestError, match="mm leaked into a metre field"):
        validate_metre_field_scales(high, kind="Mesh")
    low = _diamond_mesh_payload(filament_d_m=4.0e-7)  # 0.4 um as metres
    with pytest.raises(ManifestError, match="micrometres leaked into a metre field"):
        validate_metre_field_scales(low, kind="Mesh")


def test_derived_source_requires_usable_angle():
    payload = _diamond_mesh_payload()
    validate_periodic_shift_y(payload)
    payload["attack_angle_deg"] = 0.0
    with pytest.raises(ManifestError, match="sin\\(theta\\) > 0"):
        validate_periodic_shift_y(payload)


def test_diamond_sigma_d_and_trim_are_independent_constants():
    geometry = geometry_parameters_for_geo_id("D2450_a45")
    assert geometry["Sigma_d_nominal_m"] == pytest.approx(0.000800)
    assert geometry["membrane_trim_m"] == pytest.approx(0.000015)
    assert geometry["membrane_trim_m"] == pytest.approx(
        (geometry["Sigma_d_nominal_m"] - CAMPAIGN_H_M) / 2.0
    )
    assert "unit_cell_xy_m" not in geometry
    assert geometry["porosity_eps"] is None


def test_membrane_contact_width_diamond_formula():
    width = membrane_contact_width_m(0.000400, 0.000015)
    assert width == pytest.approx(
        2.0 * math.sqrt(0.000400 * 0.000015 - 0.000015**2)
    )
    assert width == pytest.approx(0.000152, abs=5.0e-7)
    geometry = geometry_parameters_for_geo_id("D2450_a45")
    assert geometry["membrane_contact_width_m"] == pytest.approx(width)


def test_membrane_contact_width_ml_uses_outer_layer_diameter():
    for geo_id, expected_d in (
        ("M_c160", 0.000320),
        ("M_c267", 0.000266670),
        ("M_c400", 0.000200),
    ):
        geometry = geometry_parameters_for_geo_id(geo_id)
        assert geometry["membrane_contact_width_m"] == pytest.approx(
            membrane_contact_width_m(expected_d, 0.000015)
        )


def test_pillar_contact_width_is_none():
    geometry = geometry_parameters_for_geo_id("P_p80_h20")
    assert geometry["Sigma_d_nominal_m"] is None
    assert geometry["membrane_trim_m"] == 0.0
    assert geometry["membrane_contact_width_m"] is None


def test_ml_and_pillar_spacer_wall_zones_match_cad_named_selections():
    """Registry must list real CAD zones, not design-discussion aliases."""
    ml_expected = [
        "wall_spacer_top",
        "wall_spacer_mid",
        "wall_spacer_bottom",
        "wall_spacer_bridge",
        "wall_spacer_buffer",
    ]
    for geo_id in ("M_c160", "M_c267", "M_c400"):
        assert geometry_parameters_for_geo_id(geo_id)["spacer_wall_zones"] == ml_expected

    h20 = geometry_parameters_for_geo_id("P_p80_h20")["spacer_wall_zones"]
    assert h20 == [
        "wall_spacer_filament",
        "wall_spacer_pillar",
        "wall_spacer_hole",
        "wall_spacer_buffer",
    ]
    h00 = geometry_parameters_for_geo_id("P_p80_h00")["spacer_wall_zones"]
    assert h00 == [
        "wall_spacer_filament",
        "wall_spacer_pillar",
        "wall_spacer_buffer",
    ]
    assert "wall_spacer_hole" not in h00
    # Cross-check against Fluent-shaped zone lists (rule 3-3 both ways).
    validate_spacer_wall_zones(ml_expected, ml_expected, geo_id="M_c160")
    validate_spacer_wall_zones(h20, h20, geo_id="P_p80_h20")
    validate_spacer_wall_zones(h00, h00, geo_id="P_p80_h00")


def test_sigma_d_invariant_holds_for_diamond():
    validate_sigma_d_invariant(mesh_payload())


def test_sigma_d_invariant_rejects_wrong_trim_not_derived_sigma():
    payload = mesh_payload()
    payload["membrane_trim_m"] = 0.000185  # old clearance formula
    with pytest.raises(ManifestError, match="membrane_trim_m"):
        validate_sigma_d_invariant(payload)


def test_sigma_d_tolerance_accepts_mc267_truncation():
    payload = mesh_payload()
    payload.update(
        {
            "family": "ml",
            "geo_id": "M_c267",
            **geometry_parameters_for_geo_id("M_c267"),
        }
    )
    payload["porosity_eps"] = 0.70
    validate_sigma_d_invariant(payload)
    assert sum(payload["layer_diameters_m"]) == pytest.approx(0.00080001, abs=1e-8)


def _ml_mesh_payload(geo_id: str) -> dict:
    payload = mesh_payload()
    payload.update({"family": "ml", "geo_id": geo_id, **geometry_parameters_for_geo_id(geo_id)})
    payload["porosity_eps"] = 0.70
    return payload


def test_joint_sphere_count_zero_requires_null_fields():
    payload = mesh_payload()
    payload["joint_sphere_R_ratio"] = 0.55
    with pytest.raises(ManifestError, match="must be null when joint_sphere_count is 0"):
        validate_joint_sphere_consistency(payload)


def test_joint_sphere_count_positive_requires_all_fields():
    payload = _ml_mesh_payload("M_c160")
    payload["joint_sphere_R_ratio"] = None
    with pytest.raises(ManifestError, match="joint_sphere_R_ratio"):
        validate_joint_sphere_consistency(payload)


def test_joint_sphere_ratio_consistency_guard():
    payload = _ml_mesh_payload("M_c160")
    payload["joint_sphere_R_m"] = 0.001
    with pytest.raises(ManifestError, match="inconsistent with"):
        validate_joint_sphere_consistency(payload)


def test_joint_sphere_ml_requires_count_two_and_two_z_positions():
    payload = _ml_mesh_payload("M_c160")
    payload["joint_sphere_count"] = 1
    with pytest.raises(ManifestError, match="joint_sphere_count=2"):
        validate_joint_sphere_consistency(payload)


def test_joint_sphere_overlap_guard_raises_when_separation_too_small():
    payload = _ml_mesh_payload("M_c160")
    payload["joint_sphere_z_m"] = [0.000400, 0.000500]
    with pytest.raises(ManifestError, match="must exceed 2\\*joint_sphere_R_m"):
        validate_joint_sphere_consistency(payload)


def test_joint_sphere_overlap_guard_passes_for_registry_values():
    for geo_id in ("M_c160", "M_c267", "M_c400"):
        validate_joint_sphere_consistency(_ml_mesh_payload(geo_id))


def test_joint_sphere_middle_layer_cut_warns_not_raises():
    payload = _ml_mesh_payload("M_c160")
    payload["joint_sphere_z_m"] = [0.000500, 0.000300]
    payload["joint_sphere_R_m"] = 0.000085
    payload["joint_sphere_R_ratio"] = (
        payload["joint_sphere_R_m"] / payload["joint_sphere_r_min_m"]
    )
    with pytest.warns(UserWarning, match="middle layer"):
        validate_joint_sphere_consistency(payload)


def test_sigma_d_pillar_requires_null_sigma_and_zero_trim():
    payload = mesh_payload()
    payload.update(
        {
            "family": "pillar",
            "geo_id": "P_p80_h20",
            **geometry_parameters_for_geo_id("P_p80_h20"),
        }
    )
    payload["porosity_eps"] = 0.94
    validate_sigma_d_invariant(payload)
    payload["membrane_trim_m"] = 1.0e-6
    with pytest.raises(ManifestError, match="Sigma_d_nominal_m is null"):
        validate_sigma_d_invariant(payload)


def test_porosity_eps_allows_null_and_rejects_out_of_range():
    payload = mesh_payload()
    payload["porosity_eps"] = None
    validate_mesh_geometry_fields(payload)
    payload["porosity_eps"] = 0.2
    with pytest.raises(ManifestError, match="porosity_eps"):
        validate_mesh_geometry_fields(payload)
    payload["porosity_eps"] = 0.995
    with pytest.raises(ManifestError, match="porosity_eps"):
        validate_mesh_geometry_fields(payload)


def test_spacer_wall_zone_cross_check_raises_on_undeclared():
    declared = ["wall_spacer_filament", "wall_spacer_pillar", "wall_spacer_hole"]
    fluent = declared + ["wall_spacer_extra"]
    with pytest.raises(ManifestError, match="undeclared"):
        validate_spacer_wall_zones(declared, fluent, geo_id="P_p80_h20")


def test_spacer_wall_zone_cross_check_raises_on_missing_declared():
    declared = ["wall_spacer_filament", "wall_spacer_pillar", "wall_spacer_hole"]
    fluent = ["wall_spacer_filament", "wall_spacer_pillar"]
    with pytest.raises(ManifestError, match="absent in Fluent"):
        validate_spacer_wall_zones(declared, fluent, geo_id="P_p80_h20")


def test_h00_with_hole_zone_raises():
    declared = ["wall_spacer_filament", "wall_spacer_pillar", "wall_spacer_hole"]
    fluent = declared
    with pytest.raises(ManifestError, match="h00 case but declares"):
        validate_spacer_wall_zones(declared, fluent, geo_id="P_p80_h00")


def test_h20_without_hole_zone_raises():
    declared = ["wall_spacer_filament", "wall_spacer_pillar"]
    fluent = declared
    with pytest.raises(ManifestError, match="lacks wall_spacer_hole"):
        validate_spacer_wall_zones(declared, fluent, geo_id="P_p80_h20")


def test_collect_spacer_wall_zones_empty_fluent_raises():
    with pytest.raises(ManifestError, match="empty"):
        collect_spacer_wall_zones_from_fluent([])


def test_collect_spacer_wall_zones_requires_prefix_match():
    zones = collect_spacer_wall_zones_from_fluent(
        ["wall_spacer", "inlet", "wall_spacer_filament"]
    )
    assert zones == ["wall_spacer", "wall_spacer_filament"]


def test_curvature_margin_below_threshold_raises():
    payload = mesh_payload()
    payload.update(
        {
            "family": "sin",
            "geo_id": "S3465_A400",
            **geometry_parameters_for_geo_id("S3465_A400"),
        }
    )
    payload["porosity_eps"] = 0.73
    payload["curvature_margin"] = 1.0
    with pytest.raises(ManifestError, match="self-intersection risk"):
        validate_curvature_margin(payload)


def test_curvature_margin_formula_store_value():
    wavelength_m = 0.003465
    amplitude_m = 0.0004
    margin = compute_curvature_margin(wavelength_m, amplitude_m)
    assert margin is not None
    assert margin >= 1.2


def test_run_u_mean_source_mesh_id_must_match():
    payload = run_payload()
    payload["u_mean_source_mesh_id"] = "other_mesh"
    from ro.manifest_validation import validate_u_mean_source_mesh_id

    with pytest.raises(ManifestError, match="does not match mesh_id"):
        validate_u_mean_source_mesh_id(payload)
