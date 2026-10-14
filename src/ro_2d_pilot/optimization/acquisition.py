"""Constrained expected improvement, and Forrester's cost-aware extension.

HF-only maximization uses constrained EI: expected LMH improvement times
the probability of feasibility. With one constraint that probability is
the probability that pressure drop per length is below the limit. A
second constraint multiplies a second Gaussian probability. The 2D pilot
sets only the pressure limit.

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
    extra_constraints: tuple[tuple[float, float, float], ...] = (),
) -> float:
    """EI times the product of per-constraint feasibility probabilities.

    ``extra_constraints`` entries are ``(mean, variance, limit)``. An empty
    tuple is the single pressure constraint and leaves the value unchanged.
    """
    feasible = feasibility_probability(dp_mean, dp_variance, limit)
    for mean, variance, bound in extra_constraints:
        feasible *= feasibility_probability(mean, variance, bound)
    if incumbent is None:
        return feasible * lmh_mean
    return expected_improvement(lmh_mean, lmh_variance, incumbent) * feasible


def forrester_scores(
    cei: float,
    correlation: float,
    *,
    cost_ratio: float | None = None,
    high_name: str | None = None,
    low_name: str | None = None,
) -> dict[str, float]:
    """Comparable HF and LF scores. LF is discounted by correlation and cost.

    Omitting the cost keywords uses the 2D screened solver-time ratio and
    the 2D fidelity names. Setting a cost passes all three keywords.
    """
    if cost_ratio is None and high_name is None and low_name is None:
        ratio = normalized_fidelity_cost(FIDELITY_LOW)
        hf_score = cei / normalized_fidelity_cost(FIDELITY_HIGH)
        lf_score = (
            cei * max(correlation, 0.0) * (1.0 / ratio) / normalized_fidelity_cost(FIDELITY_HIGH)
        )
        # Forrester compares EI_cheap = EI * corr * (C_hf / C_lf) with EI_hf.
        # Dividing both by C_hf leaves EI_hf / C_hf and EI_hf * corr * (C_hf / C_lf) / C_hf.
        return {FIDELITY_HIGH: hf_score, FIDELITY_LOW: lf_score}
    if cost_ratio is None or high_name is None or low_name is None:
        raise ValueError("cost_ratio, high_name, and low_name must be set together.")
    if not math.isfinite(cost_ratio) or cost_ratio <= 0.0:
        raise ValueError(f"cost_ratio must be positive and finite, got {cost_ratio!r}.")
    hf_score = cei / 1.0
    lf_score = cei * max(correlation, 0.0) * (1.0 / cost_ratio) / 1.0
    return {high_name: hf_score, low_name: lf_score}


def stack_xy(points: list[tuple[float, float]]) -> np.ndarray:
    if not points:
        return np.zeros((0, 2))
    return np.asarray(points, dtype=float)
