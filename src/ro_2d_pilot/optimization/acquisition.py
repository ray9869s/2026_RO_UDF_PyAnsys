"""Constrained expected improvement, and Forrester's cost-aware extension.

HF-only maximization uses constrained EI: expected LMH improvement times
the probability that pressure drop per length is below the limit.

MFBO scores a low-fidelity query with Forrester's augmented EI: the same
high-fidelity constrained EI, multiplied by the cross-fidelity correlation
and by the HF/LF cost ratio. The query with the larger score is chosen.
"""

from __future__ import annotations

import math

import numpy as np

from ro_2d_pilot.optimization.cost import normalized_fidelity_cost
from ro_2d_pilot.optimization.domain import FIDELITY_HIGH, FIDELITY_LOW
from ro_2d_pilot.optimization.gp import AutoregressiveModel, normal_cdf, normal_pdf


def expected_improvement(mean: float, variance: float, incumbent: float) -> float:
    sigma = math.sqrt(max(variance, 0.0))
    if sigma < 1.0e-12:
        return max(mean - incumbent, 0.0)
    z = (mean - incumbent) / sigma
    return (mean - incumbent) * normal_cdf(z) + sigma * normal_pdf(z)


def feasibility_probability(mean: float, variance: float, limit: float) -> float:
    sigma = math.sqrt(max(variance, 0.0))
    if sigma < 1.0e-12:
        return 1.0 if mean <= limit else 0.0
    return normal_cdf((limit - mean) / sigma)


def constrained_ei(
    lmh_mean: float,
    lmh_variance: float,
    dp_mean: float,
    dp_variance: float,
    *,
    incumbent: float | None,
    limit: float,
) -> float:
    feasible = feasibility_probability(dp_mean, dp_variance, limit)
    if incumbent is None:
        return feasible * lmh_mean
    return expected_improvement(lmh_mean, lmh_variance, incumbent) * feasible


def forrester_scores(
    cei: float,
    correlation: float,
) -> dict[str, float]:
    """Comparable HF and LF scores. LF is discounted by correlation and cost."""
    ratio = normalized_fidelity_cost(FIDELITY_LOW)
    hf_score = cei / normalized_fidelity_cost(FIDELITY_HIGH)
    lf_score = cei * max(correlation, 0.0) * (1.0 / ratio) / normalized_fidelity_cost(FIDELITY_HIGH)
    # Forrester compares EI_cheap = EI * corr * (C_hf / C_lf) with EI_hf.
    # Dividing both by C_hf leaves EI_hf / C_hf and EI_hf * corr * (C_hf / C_lf) / C_hf.
    return {FIDELITY_HIGH: hf_score, FIDELITY_LOW: lf_score}


def stack_xy(points: list[tuple[float, float]]) -> np.ndarray:
    if not points:
        return np.zeros((0, 2))
    return np.asarray(points, dtype=float)
