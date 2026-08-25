"""Tests for CP modulus definitions and averaging-order convention."""

from __future__ import annotations

import pytest

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    average_of_ratios_cp_bae_approx,
    canonical_rescale_factor,
    cp_l1_gu2017,
    ratio_of_averages_cp_bae_approx,
    scalar_rescale_guard_delta,
    window_area_weighted_average,
)


def test_average_of_ratios_differs_from_ratio_of_averages():
    """Regression guard: deterministic face data with visible ordering gap."""
    cp_face_a = 1.10
    cp_face_b = 1.30
    area_a = 1.0
    area_b = 3.0
    area_total = area_a + area_b

    avg_of_ratios = average_of_ratios_cp_bae_approx(
        cp_face_a * area_a + cp_face_b * area_b,
        area_total,
    )

    cm_a, cm_b = 620.0, 680.0
    cp_perm_a, cp_perm_b = 0.45, 0.55
    c0 = 597.8268309
    ratio_avg = ratio_of_averages_cp_bae_approx(
        cm_a * area_a + cm_b * area_b,
        cp_perm_a * area_a + cp_perm_b * area_b,
        area_total,
        c0,
    )

    assert avg_of_ratios == pytest.approx(1.25)
    assert abs(avg_of_ratios - ratio_avg) > 0.04


def test_canonical_rescale_factor_and_guard():
    c0 = 597.8268309
    cb = 610.0
    cp_avg = 0.50
    cp_min = 0.48
    cp_max = 0.52
    k, delta = canonical_rescale_factor(
        c0,
        cb,
        cp_avg,
        cp_perm_min_mol_per_m3=cp_min,
        cp_perm_max_mol_per_m3=cp_max,
    )
    expected_delta = scalar_rescale_guard_delta(c0, cb, cp_min, cp_max)
    assert delta == pytest.approx(expected_delta)
    assert delta < CP_SCALAR_RESCALE_GUARD_THRESHOLD
    assert k == pytest.approx((c0 - cp_avg) / (cb - cp_avg))


def test_cp_l1_denominator_face_independent():
    cm_avg = 650.0
    cb = 600.0
    assert cp_l1_gu2017(cm_avg, cb) == pytest.approx(cm_avg / cb)


def test_window_area_weighted_average():
    values = {4: 1.1, 5: 1.3}
    areas = {4: 2.0, 5: 1.0}
    assert window_area_weighted_average(values, areas, [4, 5]) == pytest.approx(
        (1.1 * 2.0 + 1.3 * 1.0) / 3.0
    )


def test_scalar_rescale_guard_raises_when_delta_too_large():
    c0 = 597.8268309
    cb = 598.0
    cp_min = 597.5
    cp_max = 598.2
    with pytest.raises(ValueError, match="scalar-rescale guard failed"):
        canonical_rescale_factor(
            c0,
            cb,
            597.9,
            cp_perm_min_mol_per_m3=cp_min,
            cp_perm_max_mol_per_m3=cp_max,
        )
