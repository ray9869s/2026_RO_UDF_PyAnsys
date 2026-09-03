"""Unit tests for origin-agnostic channel mid-plane helpers."""

from __future__ import annotations

import math

import pytest

from ro.fluent_report_helpers import (
    MIDPLANE_CB_MIXING_CUP_REL_TOL,
    assert_midplane_c_b_matches_boundary_mixing_cup,
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


def test_resolve_midplane_fallback_is_centred_origin_zero():
    z_mid, diag = resolve_channel_midplane_z_m()
    assert z_mid == 0.0
    assert diag["source"] == "fallback_centred_origin"


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
