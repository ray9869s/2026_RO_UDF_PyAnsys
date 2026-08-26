"""LMH helpers using effective membrane area (blocked fraction excluded)."""

from __future__ import annotations

import math

MS_TO_LMH = 3.6e6


class LmhMetricsError(ValueError):
    """Raised when LMH inputs are invalid."""


def effective_membrane_area_m2(
    nominal_area_m2: float,
    membrane_blocked_area_frac: float,
) -> float:
    """Return membrane area excluding blocked (non-permeating) fraction."""
    if not math.isfinite(nominal_area_m2) or nominal_area_m2 <= 0.0:
        raise LmhMetricsError(
            f"nominal_area_m2 must be a positive finite float, got {nominal_area_m2!r}."
        )
    if not math.isfinite(membrane_blocked_area_frac):
        raise LmhMetricsError(
            "membrane_blocked_area_frac must be finite, "
            f"got {membrane_blocked_area_frac!r}."
        )
    if not 0.0 <= membrane_blocked_area_frac < 1.0:
        raise LmhMetricsError(
            "membrane_blocked_area_frac must be in [0, 1), "
            f"got {membrane_blocked_area_frac!r}."
        )
    return nominal_area_m2 * (1.0 - membrane_blocked_area_frac)


def lmh_from_mass_imbalance_kg_s(
    mass_in_kg_s: float,
    mass_out_kg_s: float,
    *,
    density_kg_m3: float,
    nominal_area_m2: float,
    membrane_blocked_area_frac: float,
) -> float:
    """Mass-balance LMH on effective membrane area (one basis only)."""
    if not math.isfinite(density_kg_m3) or density_kg_m3 <= 0.0:
        raise LmhMetricsError(
            f"density_kg_m3 must be a positive finite float, got {density_kg_m3!r}."
        )
    effective_area = effective_membrane_area_m2(
        nominal_area_m2,
        membrane_blocked_area_frac,
    )
    return abs(mass_in_kg_s + mass_out_kg_s) / (density_kg_m3 * effective_area) * MS_TO_LMH


def lmh_denominator_area_report_name(nominal_report: str = "area_mem") -> str:
    """Fluent report name for the LMH denominator (effective membrane area)."""
    if not nominal_report or not nominal_report.strip():
        raise LmhMetricsError(
            f"nominal_report must be a non-empty string, got {nominal_report!r}."
        )
    return f"{nominal_report}_effective"


def lmh_mass_balance_expression(
    *,
    m_in_name: str,
    m_out_name: str,
    density_value: float,
    area_mem_name: str,
    membrane_blocked_area_frac: float,
    signed: bool = False,
) -> str:
    """Build a Fluent single-valued expression for LMH on effective area."""
    if not 0.0 <= membrane_blocked_area_frac < 1.0:
        raise LmhMetricsError(
            "membrane_blocked_area_frac must be in [0, 1), "
            f"got {membrane_blocked_area_frac!r}."
        )
    effective_factor = 1.0 - membrane_blocked_area_frac
    numerator = f"({m_in_name} + {m_out_name})"
    if not signed:
        numerator = f"abs{numerator}"
    return (
        f"{numerator} / ({density_value} * {area_mem_name} * {effective_factor}) "
        f"* {MS_TO_LMH}"
    )
