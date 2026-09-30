"""Empirical fidelity costs from the eight valid screening pairs.

Solver time is the acquisition and budget cost. Total wall time is stored
on each run and summed when the record has it. The two are not mixed.
"""

from __future__ import annotations

import statistics

# very_fine solver seconds from the eight valid medium/very_fine pairs.
SCREENED_HF_SOLVER_S = (
    476.231,
    654.182,
    904.453,
    461.51,
    862.139,
    884.834,
    438.753,
    864.711,
)
# medium solver seconds, same pair order.
SCREENED_LF_SOLVER_S = (
    54.532,
    63.6281,
    71.1858,
    55.8753,
    109.228,
    74.4304,
    65.925,
    70.6827,
)


def reference_hf_solver_s() -> float:
    return float(statistics.median(SCREENED_HF_SOLVER_S))


def lf_over_hf_cost_ratio() -> float:
    ratios = [
        low / high
        for low, high in zip(SCREENED_LF_SOLVER_S, SCREENED_HF_SOLVER_S, strict=True)
    ]
    return float(statistics.median(ratios))


def normalized_fidelity_cost(fidelity: str) -> float:
    """HF cost is 1. LF cost is the median screened solver-time ratio."""
    from ro_2d_pilot.optimization.domain import FIDELITY_HIGH, FIDELITY_LOW

    if fidelity == FIDELITY_HIGH:
        return 1.0
    if fidelity == FIDELITY_LOW:
        return lf_over_hf_cost_ratio()
    raise ValueError(f"Unknown fidelity {fidelity!r}.")


def hf_equivalent(solver_s: float) -> float:
    """Solver seconds divided by the screened median very_fine solver time."""
    reference = reference_hf_solver_s()
    if reference <= 0.0:
        raise ValueError("reference HF solver time must be positive.")
    return float(solver_s) / reference
