"""Parity tests for _solver_common naming helpers (phase 2)."""

from __future__ import annotations

import pytest

from helpers import load_solver_common

SIN_MESH = "mesh_max100_min006_cpg3_bl3"
ML_MESH = "mesh_max085_min005_cpg5_bl3"

CAMPAIGN_CASES = [
    (0.3, 4.0e6, "u0p3_p4M", None),
    (0.3, 6.0e6, "u0p3_p6M", None),
    (0.3, 8.0e6, "u0p3_p8M", None),
    (0.1, 4.0e6, "u0p1_p4M", SIN_MESH),
    (0.1, 6.0e6, "u0p1_p6M", SIN_MESH),
    (0.1, 8.0e6, "u0p1_p8M", SIN_MESH),
    (0.2, 4.0e6, "u0p2_p4M", SIN_MESH),
    (0.2, 6.0e6, "u0p2_p6M", SIN_MESH),
    (0.2, 8.0e6, "u0p2_p8M", SIN_MESH),
    (0.3, 4.0e6, "u0p3_p4M", SIN_MESH),
    (0.3, 6.0e6, "u0p3_p6M", SIN_MESH),
    (0.3, 8.0e6, "u0p3_p8M", SIN_MESH),
]


def legacy_velocity_to_case_token(u) -> str:
    return f"u0p{int(round(float(u) * 10))}"


def legacy_pressure_to_case_token(p) -> str:
    return f"p{int(round(float(p) / 1.0e6))}M"


def legacy_make_base_case_name(u, p) -> str:
    return f"{legacy_velocity_to_case_token(u)}_{legacy_pressure_to_case_token(p)}"


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


class TestNamingParityAcrossCampaign:
    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_velocity_token(self, common, velocity, pressure, base_name, mesh_name):
        assert common.velocity_to_case_token(velocity) == legacy_velocity_to_case_token(velocity)

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_pressure_token(self, common, velocity, pressure, base_name, mesh_name):
        assert common.pressure_to_case_token(pressure) == legacy_pressure_to_case_token(pressure)

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_make_base_case_name(self, common, velocity, pressure, base_name, mesh_name):
        assert common.make_base_case_name(velocity, pressure) == legacy_make_base_case_name(
            velocity, pressure
        )
        assert common.make_base_case_name(velocity, pressure) == base_name

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_resolve_case_names_from_operating_values(
        self, common, velocity, pressure, base_name, mesh_name
    ):
        case_dict = {
            "inlet_velocity_value": velocity,
            "outlet_gauge_pressure": pressure,
            "mesh_case_name": mesh_name,
        }
        assert common.resolve_case_names(case_dict) == (base_name, base_name)


class TestResolveCaseNames:
    def test_explicit_case_name(self, common):
        case_dict = {
            "case_name": "custom_case",
            "base_case_name": "ignored_base",
            "mesh_case_name": SIN_MESH,
        }
        assert common.resolve_case_names(case_dict) == ("ignored_base", "custom_case")

    def test_run_id_wins_over_mesh_case_name(self, common):
        case_dict = {
            "run_id": "u0p1_p4M",
            "mesh_case_name": SIN_MESH,
            "inlet_velocity_value": 0.1,
            "outlet_gauge_pressure": 4.0e6,
        }
        assert common.resolve_case_names(case_dict) == ("u0p1_p4M", "u0p1_p4M")

    def test_from_base_without_mesh_suffix(self, common):
        case_dict = {
            "base_case_name": "u0p1_p4M",
            "mesh_case_name": SIN_MESH,
        }
        assert common.resolve_case_names(case_dict) == ("u0p1_p4M", "u0p1_p4M")

    def test_legacy_plain_entry(self, common):
        case_dict = {
            "geo_name": "Multi_Layer_diff",
            "mesh_case_name": ML_MESH,
            "case_name": "u0p3_p4M",
            "inlet_velocity_value": 0.3,
            "outlet_gauge_pressure": 4.0e6,
        }
        assert common.resolve_case_names(case_dict) == (None, "u0p3_p4M")

    def test_mesh_qualified_case_name_is_refused(self, common):
        with pytest.raises(ValueError, match="mesh-qualified"):
            common.resolve_case_names(
                {"case_name": f"u0p1_p4M__{SIN_MESH}"}
            )


class TestF05MatrixBaseCaseNameHelpers:
    """is_matrix_base_case_name used by 07 select_candidates."""

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_is_matrix_base_case_name_on_base_token(
        self, common, velocity, pressure, base_name, mesh_name
    ):
        assert common.is_matrix_base_case_name(base_name) is True

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_is_matrix_base_case_name_false_on_mesh_qualified(
        self, common, velocity, pressure, base_name, mesh_name
    ):
        if mesh_name:
            qualified = f"{base_name}__{mesh_name}"
            assert common.is_matrix_base_case_name(qualified) is False

    def test_optional_run_id_suffix_is_not_a_matrix_case(self, common):
        assert common.MATRIX_BASE_CASE_RE.pattern == r"^u\d+p\d+_p\d+M$"
        assert common.is_matrix_base_case_name("u0p2_p6M") is True
        assert common.is_matrix_base_case_name("u0p2_p6M_plug") is False
