"""Pilot objective: maximize LMH subject to a pressure-drop limit.

CP is recorded and reported. It is not an objective and not a constraint.

``P_LIMIT_PA_PER_M`` is a pilot constraint, not a membrane specification.
The eight valid very_fine screening points span about 1.30e4 to 7.22e4 Pa/m
(median about 2.37e4). 3.0e4 Pa/m sits above the solved center
(d = 0.40 mm, L = 4.0 mm, 2.59e4 Pa/m) and below the short-pitch
d = 0.40 mm point (3.34e4 Pa/m) and both d = 0.50 mm points.
The feasible screened set is therefore nonempty, and the constraint is active.
"""

from __future__ import annotations

P_LIMIT_PA_PER_M = 30_000.0
P_LIMIT_RATIONALE = (
    "Pilot limit of 30000 Pa/m on very_fine pressure drop per unit length. "
    "Screened very_fine values span about 13000 to 72200 Pa/m. "
    "30000 Pa/m keeps the solved center feasible and excludes the "
    "high-blockage and shortest mid-diameter points, so the constraint is active."
)


def pressure_feasible(dp_per_l: float, limit: float = P_LIMIT_PA_PER_M) -> bool:
    return float(dp_per_l) <= float(limit)
