"""Characterization tests for case-name building and operating-value parsing.

Batch naming helpers are re-exported from _solver_common via batch_solver_sweep
(phase 2). Report (01_batch_report_extract) and post (06) naming are unchanged
here; a later phase may wire them to the shared module.
"""

from __future__ import annotations

import pytest

from helpers import (
    load_batch_postprocess,
    load_batch_report_extract,
    load_batch_solver_sweep,
)

SIN_MESH = "mesh_max100_min006_cpg3_bl3"
ML_MESH = "mesh_max085_min005_cpg5_bl3"

# Active 24-case campaign naming matrix (batch_config.py).
CAMPAIGN_CASES = [
    # Group 1: legacy plain names at u=0.3 m/s
    (0.3, 4.0e6, "u0p3_p4M", None),
    (0.3, 6.0e6, "u0p3_p6M", None),
    (0.3, 8.0e6, "u0p3_p8M", None),
    # Groups 2-3: mesh-qualified sinusoidal sweep
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


@pytest.fixture(scope="module")
def sweep():
    return load_batch_solver_sweep()


@pytest.fixture(scope="module")
def report_batch():
    return load_batch_report_extract()


@pytest.fixture(scope="module")
def post_batch():
    return load_batch_postprocess()


class TestBatchSolverSweepNaming:
    @pytest.mark.parametrize(
        ("velocity", "pressure", "expected_token_u", "expected_token_p"),
        [
            (0.1, 4.0e6, "u0p1", "p4M"),
            (0.2, 6.0e6, "u0p2", "p6M"),
            (0.3, 8.0e6, "u0p3", "p8M"),
        ],
    )
    def test_token_helpers(self, sweep, velocity, pressure, expected_token_u, expected_token_p):
        assert sweep.velocity_to_case_token(velocity) == expected_token_u
        assert sweep.pressure_to_case_token(pressure) == expected_token_p
        assert sweep.make_base_case_name(velocity, pressure) == f"{expected_token_u}_{expected_token_p}"

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_mesh_qualified_names(self, sweep, velocity, pressure, base_name, mesh_name):
        derived_base = sweep.make_base_case_name(velocity, pressure)
        assert derived_base == base_name

        if mesh_name:
            qualified = sweep.make_mesh_qualified_case_name(base_name, mesh_name)
            assert qualified == f"{base_name}__{mesh_name}"
            assert qualified.count("__") == 1
        else:
            assert sweep.make_mesh_qualified_case_name(base_name, None) == base_name
            assert sweep.make_mesh_qualified_case_name(base_name, "") == base_name

    def test_resolve_case_names_explicit_case_name(self, sweep):
        base, case = sweep.resolve_case_names(
            {
                "case_name": "custom_case",
                "base_case_name": "ignored_base",
                "mesh_case_name": SIN_MESH,
            }
        )
        assert base == "ignored_base"
        assert case == "custom_case"

    def test_resolve_case_names_from_base_and_mesh(self, sweep):
        base, case = sweep.resolve_case_names(
            {
                "base_case_name": "u0p1_p4M",
                "mesh_case_name": SIN_MESH,
            }
        )
        assert base == "u0p1_p4M"
        assert case == f"u0p1_p4M__{SIN_MESH}"

    def test_resolve_case_names_from_operating_values(self, sweep):
        base, case = sweep.resolve_case_names(
            {
                "inlet_velocity_value": 0.2,
                "outlet_gauge_pressure": 6.0e6,
                "mesh_case_name": SIN_MESH,
            }
        )
        assert base == "u0p2_p6M"
        assert case == f"u0p2_p6M__{SIN_MESH}"

    def test_resolve_case_names_legacy_plain_entry(self, sweep):
        base, case = sweep.resolve_case_names(
            {
                "geo_name": "Multi_Layer_diff",
                "mesh_case_name": ML_MESH,
                "case_name": "u0p3_p4M",
                "inlet_velocity_value": 0.3,
                "outlet_gauge_pressure": 4.0e6,
            }
        )
        assert case == "u0p3_p4M"
        assert "__" not in case


class TestBatchReportExtractNaming:
    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_make_case_name_matches_solver_sweep(self, sweep, report_batch, velocity, pressure, base_name, mesh_name):
        assert report_batch.make_case_name(velocity, pressure) == sweep.make_base_case_name(velocity, pressure)

    def test_resolve_post_case_mesh_qualified(self, report_batch):
        resolved = report_batch.resolve_post_case(
            {
                "geo_name": "Sin_ST",
                "inlet_velocity_value": 0.1,
                "outlet_gauge_pressure": 4.0e6,
                "mesh_case_name": SIN_MESH,
            }
        )
        assert resolved["case_name"] == f"u0p1_p4M__{SIN_MESH}"
        assert resolved["base_case_name"] == "u0p1_p4M"


class TestParseCaseOperatingValues:
    @pytest.mark.parametrize(
        ("case_name", "expected_u", "expected_p"),
        [
            ("u0p1_p4M", 0.1, 4.0e6),
            ("u0p2_p6M", 0.2, 6.0e6),
            ("u0p3_p8M", 0.3, 8.0e6),
        ],
    )
    def test_plain_case_names(self, post_batch, case_name, expected_u, expected_p):
        u, p = post_batch.parse_case_operating_values(case_name)
        assert u == expected_u
        assert p == expected_p

    @pytest.mark.parametrize(
        ("mesh_qualified_name", "expected_u", "expected_p"),
        [
            (f"u0p1_p4M__{SIN_MESH}", 0.1, 4.0e6),
            (f"u0p2_p6M__{SIN_MESH}", 0.2, 6.0e6),
            (f"u0p3_p8M__{SIN_MESH}", 0.3, 8.0e6),
        ],
    )
    def test_mesh_qualified_names_parse_operating_values(
        self, post_batch, mesh_qualified_name, expected_u, expected_p
    ):
        u, p = post_batch.parse_case_operating_values(mesh_qualified_name)
        assert u == expected_u
        assert p == expected_p
