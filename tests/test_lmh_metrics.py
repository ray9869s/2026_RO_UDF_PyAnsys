"""Tests for LMH effective-area helpers."""

from __future__ import annotations

import pytest

from ro.lmh_metrics import (
    LmhMetricsError,
    effective_membrane_area_m2,
    lmh_from_mass_imbalance_kg_s,
    lmh_mass_balance_expression,
)


def test_effective_area_excludes_blocked_fraction():
    assert effective_membrane_area_m2(1.0e-4, 0.1) == pytest.approx(9.0e-5)


def test_zero_blocked_fraction_matches_nominal():
    assert effective_membrane_area_m2(6.2423e-05, 0.0) == pytest.approx(6.2423e-05)


def test_blocked_fraction_out_of_range_raises():
    with pytest.raises(LmhMetricsError, match="\\[0, 1\\)"):
        effective_membrane_area_m2(1.0, 1.0)


def test_lmh_mass_balance_expression_uses_effective_factor():
    expr = lmh_mass_balance_expression(
        m_in_name="m_in",
        m_out_name="m_out",
        density_value=998.2,
        area_mem_name="area_mem",
        membrane_blocked_area_frac=0.1,
    )
    assert "area_mem * 0.9" in expr
    assert "abs(m_in + m_out)" in expr


def test_lmh_from_mass_imbalance_matches_manual():
    rho = 998.2
    area = 6.2423e-05
    blocked = 0.0
    m_in = 1.0e-5
    m_out = -9.5e-6
    lmh = lmh_from_mass_imbalance_kg_s(
        m_in,
        m_out,
        density_kg_m3=rho,
        nominal_area_m2=area,
        membrane_blocked_area_frac=blocked,
    )
    manual = abs(m_in + m_out) / (rho * area) * 3.6e6
    assert lmh == pytest.approx(manual)
