"""Tests for CP modulus definitions and averaging-order convention."""

from __future__ import annotations

import pytest

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    FACET_MIN_REJECT_AREA_FRAC,
    FACET_MIN_TARGET_AREA_FRAC,
    average_of_ratios_cp_bae_approx,
    canonical_rescale_factor,
    cp_l1_gu2017,
    facet_min_check_threshold,
    ratio_of_averages_cp_bae_approx,
    resolve_area_backed_minimum,
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


def test_guard_threshold_is_1e_3_and_accepts_u0p1_delta():
    """Campaign threshold must clear observed u=0.1 / p=6 MPa delta (~2.32e-4)."""
    assert CP_SCALAR_RESCALE_GUARD_THRESHOLD == pytest.approx(1.0e-3)
    # Inputs tuned so delta sits at the observed u0p1_p6M scale (~2.32e-4).
    c0 = 597.83
    cb = 626.83
    cp_min = 0.5
    cp_max = 3.64
    delta = scalar_rescale_guard_delta(c0, cb, cp_min, cp_max)
    assert delta == pytest.approx(2.32e-4, rel=0.05)
    assert delta < CP_SCALAR_RESCALE_GUARD_THRESHOLD
    k, guarded = canonical_rescale_factor(
        c0,
        cb,
        2.0,
        cp_perm_min_mol_per_m3=cp_min,
        cp_perm_max_mol_per_m3=cp_max,
    )
    assert guarded == pytest.approx(delta)
    assert k > 0.0


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


def test_facet_min_check_threshold_adds_relative_slack():
    assert facet_min_check_threshold(0.0) == pytest.approx(0.0)
    assert facet_min_check_threshold(617.93) == pytest.approx(617.93 * 1.001)


def test_resolve_area_backed_minimum_keeps_supported_facetmin():
    used, rejected = resolve_area_backed_minimum(
        610.0,
        area_frac_at_check=1.0e-4,
        area_frac_below_fn=lambda _t: 1.0,
        search_upper=620.0,
    )
    assert rejected is False
    assert used == pytest.approx(610.0)


def test_resolve_area_backed_minimum_rejects_zero_area_poison():
    """u0p1-style: facetmin=0 with no area; substitute area-bearing floor."""
    true_min = 617.93

    def area_frac_below(threshold: float) -> float:
        if threshold < true_min:
            return 0.0
        # Jump to well above TARGET once the floor is crossed.
        return 1.0e-3

    used, rejected = resolve_area_backed_minimum(
        0.0,
        area_frac_at_check=0.0,
        area_frac_below_fn=area_frac_below,
        search_upper=620.0,
        reject_frac=FACET_MIN_REJECT_AREA_FRAC,
        target_frac=FACET_MIN_TARGET_AREA_FRAC,
    )
    assert rejected is True
    assert used == pytest.approx(true_min, rel=1e-4)
    assert used > 600.0
