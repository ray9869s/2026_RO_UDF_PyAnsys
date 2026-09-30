"""Offline replay table from the 3x3 fidelity screen.

These values are lookup results, not a license to invent a CFD answer.
A design that is absent raises ``ReplayMiss``. The diverged very_fine
point at d = 0.50 mm, L = 4.0 mm is a failure, not an objective value.
Medium QoIs for that geometry were not in the comparable-pair table, so
they are absent here too.
"""

from __future__ import annotations

from ro_2d_pilot.optimization.data import (
    STATE_DIVERGED,
    STATE_VALID,
    Outcome,
)
from ro_2d_pilot.optimization.domain import FIDELITY_HIGH, FIDELITY_LOW

# d_mm, L_mm, lmh_m, lmh_vf, cp_m, cp_vf, dp_m, dp_vf, solve_m, solve_vf
_PAIRS = (
    (0.30, 3.00, 25.7887, 25.3042, 1.06162, 1.08, 18902.6, 18834.7, 54.532, 476.231),
    (0.30, 4.00, 25.5379, 25.0233, 1.07113, 1.09066, 15240.8, 15180.5, 63.6281, 654.182),
    (0.30, 5.00, 25.316, 24.7917, 1.07956, 1.09946, 13025.8, 12972.8, 71.1858, 904.453),
    (0.40, 3.00, 25.8842, 25.3769, 1.05802, 1.07726, 33415.1, 33377.0, 55.8753, 461.51),
    (0.40, 4.00, 25.6382, 25.0861, 1.06735, 1.0883, 25951.0, 25924.3, 109.228, 862.139),
    (0.40, 5.00, 25.4219, 24.8509, 1.07556, 1.09724, 21538.7, 21518.8, 74.4304, 884.834),
    (0.50, 3.00, 25.8463, 25.3693, 1.05959, 1.07753, 70863.8, 72226.0, 65.925, 438.753),
    (0.50, 5.00, 25.4314, 24.7814, 1.07525, 1.09994, 43328.8, 44234.7, 70.6827, 864.711),
)


class ReplayMiss(KeyError):
    """The offline table has no CFD result for this design and fidelity."""


def replay_designs() -> list[tuple[float, float]]:
    designs = [(row[0] * 1.0e-3, row[1] * 1.0e-3) for row in _PAIRS]
    designs.append((0.50e-3, 4.0e-3))
    return designs


def lookup(d_m: float, L_m: float, fidelity: str) -> Outcome:
    if fidelity == FIDELITY_HIGH and _same(d_m, 0.50e-3) and _same(L_m, 4.0e-3):
        return Outcome(
            d_m=0.50e-3,
            L_m=4.0e-3,
            fidelity=FIDELITY_HIGH,
            state=STATE_DIVERGED,
            solver_s=None,
            cost_imputed=True,
        )
    for row in _PAIRS:
        d = row[0] * 1.0e-3
        length = row[1] * 1.0e-3
        if not (_same(d_m, d) and _same(L_m, length)):
            continue
        if fidelity == FIDELITY_LOW:
            return _valid(d, length, fidelity, row[2], row[6], row[4], row[8])
        if fidelity == FIDELITY_HIGH:
            return _valid(d, length, fidelity, row[3], row[7], row[5], row[9])
        raise ReplayMiss(fidelity)
    raise ReplayMiss(
        f"No offline_replay result for d={d_m} L={L_m} fidelity={fidelity}."
    )


def _valid(
    d_m: float,
    L_m: float,
    fidelity: str,
    lmh: float,
    dp_per_l: float,
    cp: float,
    solver_s: float,
) -> Outcome:
    return Outcome(
        d_m=d_m,
        L_m=L_m,
        fidelity=fidelity,
        state=STATE_VALID,
        lmh=lmh,
        dp_per_l=dp_per_l,
        cp=cp,
        solver_s=solver_s,
        wall_s=None,
    )


def _same(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= 1.0e-12
