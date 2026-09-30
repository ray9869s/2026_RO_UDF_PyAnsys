"""Physical design box and the unit-square coordinates used by the GP.

The optimizer sees only ``(x1, x2)`` in ``[0, 1]``. Physical ``d`` and ``L``
stay on every stored result. Geometry rejection uses ``PilotConfig``, so a
later evaluator can supply a different design box without this module
knowing how a mesh is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from ro_2d_pilot.config import MESH_LEVEL_MEDIUM, MESH_LEVEL_VERY_FINE, PilotConfig

# Pilot box from the fidelity screen. Not a claim about the global design space.
D_MIN_M = 0.30e-3
D_MAX_M = 0.50e-3
L_MIN_M = 3.0e-3
L_MAX_M = 5.0e-3

FIDELITY_LOW = MESH_LEVEL_MEDIUM
FIDELITY_HIGH = MESH_LEVEL_VERY_FINE
FIDELITIES = (FIDELITY_LOW, FIDELITY_HIGH)
TARGET_FIDELITY = FIDELITY_HIGH


@dataclass(frozen=True)
class Design:
    d_m: float
    L_m: float

    @property
    def x(self) -> tuple[float, float]:
        return normalize(self.d_m, self.L_m)


def normalize(d_m: float, L_m: float) -> tuple[float, float]:
    return (
        (float(d_m) - D_MIN_M) / (D_MAX_M - D_MIN_M),
        (float(L_m) - L_MIN_M) / (L_MAX_M - L_MIN_M),
    )


def denormalize(x1: float, x2: float) -> Design:
    return Design(
        d_m=D_MIN_M + float(x1) * (D_MAX_M - D_MIN_M),
        L_m=L_MIN_M + float(x2) * (L_MAX_M - L_MIN_M),
    )


def inside_box(d_m: float, L_m: float) -> bool:
    return D_MIN_M <= float(d_m) <= D_MAX_M and L_MIN_M <= float(L_m) <= L_MAX_M


def geometry_allowed(d_m: float, L_m: float) -> bool:
    """True when the existing 2D geometry rules accept this pair."""
    if not inside_box(d_m, L_m):
        return False
    try:
        PilotConfig(d_m=float(d_m), L_m=float(L_m), fidelity=FIDELITY_HIGH, n_pitches=3)
    except ValueError:
        return False
    return True
