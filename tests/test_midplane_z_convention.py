"""Unit tests for origin-agnostic channel mid-plane helpers."""

from __future__ import annotations

import math

import pytest

from ro.fluent_report_helpers import (
    CAMPAIGN_MIDPLANE_Z_TOL_M,
    MIDPLANE_CB_MIXING_CUP_REL_TOL,
    _MEMBRANE_Z_MAX_REPORT,
    _MEMBRANE_Z_MIN_REPORT,
    assert_midplane_c_b_matches_boundary_mixing_cup,
    campaign_midplane_manifest_fields,
    resolve_channel_midplane_z_m,
)


def test_resolve_midplane_from_explicit_centred_bounds():
    z_mid, diag = resolve_channel_midplane_z_m(
        z_min_m=-3.850994e-04,
        z_max_m=3.851833e-04,
    )
    assert math.isclose(z_mid, 0.5 * (-3.850994e-04 + 3.851833e-04), rel_tol=0.0, abs_tol=1e-15)
    assert abs(z_mid) < 1e-7
    assert diag["source"] == "explicit_bounds"


def test_resolve_midplane_from_explicit_bottom_origin_bounds():
    z_mid, diag = resolve_channel_midplane_z_m(z_min_m=0.0, z_max_m=7.7e-4)
    assert math.isclose(z_mid, 3.85e-4, rel_tol=1e-12)
    assert diag["source"] == "explicit_bounds"


def test_resolve_midplane_without_a_measurement_raises():
    with pytest.raises(ValueError, match="required"):
        resolve_channel_midplane_z_m()


def test_legacy_fluid_reduction_keeps_the_centred_origin_fallback():
    z_mid, diag = resolve_channel_midplane_z_m(legacy_fluid_z_reduction=True)
    assert z_mid == 0.0
    assert diag["source"] == "fallback_centred_origin"


class _Report:
    def __init__(self):
        self.state = {}

    def set_state(self, state):
        self.state = dict(state)


class _SurfaceGroup:
    def __init__(self):
        self.reports = {}

    def get_object_names(self):
        return list(self.reports)

    def create(self, name):
        report = _Report()
        self.reports[name] = report
        return report

    def __getitem__(self, name):
        return self.reports[name]


class _Solution:
    def __init__(self, bounds):
        self.surface = _SurfaceGroup()
        self.bounds = bounds
        self.report_definitions = self

    def compute(self, report_defs):
        state = self.surface.reports[report_defs[0]].state
        if state["report_type"] == "surface-facetmin":
            return self.bounds[0]
        if state["report_type"] == "surface-facetmax":
            return self.bounds[1]
        raise AssertionError(state["report_type"])


def test_membrane_wall_facets_accept_a_centred_campaign_plane():
    solution = _Solution((-3.85e-4, 3.85e-4))
    walls = ["wall_bottom_mem", "wall_top_mem"]
    z_mid, diag = resolve_channel_midplane_z_m(
        solution=solution,
        membrane_wall_names=walls,
    )
    assert z_mid == pytest.approx(0.0, abs=CAMPAIGN_MIDPLANE_Z_TOL_M)
    assert diag["source"] == "membrane_wall_facet_bounds"
    minimum = solution.surface.reports[_MEMBRANE_Z_MIN_REPORT].state
    maximum = solution.surface.reports[_MEMBRANE_Z_MAX_REPORT].state
    assert minimum["report_type"] == "surface-facetmin"
    assert maximum["report_type"] == "surface-facetmax"
    assert minimum["field"] == "z-coordinate"
    assert maximum["field"] == "z-coordinate"
    assert minimum["surface_names"] == walls
    assert maximum["surface_names"] == walls


def test_mesh_check_midpoint_is_kept_inside_1e6():
    # D2450_a45 mesh-check extrema. Mid-point is about 4.2e-8 m, not 0.
    z_min = -3.850994e-4
    z_max = 3.851833e-4
    measured = 0.5 * (z_min + z_max)
    assert abs(measured) > 1e-9
    assert abs(measured) < CAMPAIGN_MIDPLANE_Z_TOL_M
    solution = _Solution((z_min, z_max))
    z_mid, diag = resolve_channel_midplane_z_m(
        solution=solution,
        membrane_wall_names=["wall_bottom_mem", "wall_top_mem"],
    )
    assert z_mid == pytest.approx(measured)
    assert z_mid != 0.0
    assert diag["z_mid_offset_m"] == pytest.approx(measured)
    recorded = campaign_midplane_manifest_fields(z_mid)
    assert recorded["channel_midplane_z_m"] == pytest.approx(measured)
    assert recorded["channel_midplane_offset_m"] == pytest.approx(measured)


def test_membrane_wall_offset_above_1e6_raises():
    solution = _Solution((0.0, 7.7e-4))
    with pytest.raises(RuntimeError, match="1e-06"):
        resolve_channel_midplane_z_m(
            solution=solution,
            membrane_wall_names=["wall_top_mem"],
        )


def test_mixing_cup_guard_accepts_close_values():
    c_b = {5: 599.0}
    cups = {4: 598.5, 5: 599.5}
    assert_midplane_c_b_matches_boundary_mixing_cup(c_b, cups, [5])


def test_mixing_cup_guard_rejects_wall_like_offset():
    # Historical bug: mid-plane sampled at wall (~620) vs bulk cups (~599).
    c_b = {5: 622.63}
    cups = {4: 598.37, 5: 599.08}
    with pytest.raises(RuntimeError, match="disagrees with x-normal"):
        assert_midplane_c_b_matches_boundary_mixing_cup(
            c_b,
            cups,
            [5],
            rel_tol=MIDPLANE_CB_MIXING_CUP_REL_TOL,
        )


def test_mixing_cup_guard_requires_flanking_boundaries():
    with pytest.raises(KeyError):
        assert_midplane_c_b_matches_boundary_mixing_cup(
            {5: 599.0},
            {5: 599.0},
            [5],
        )
