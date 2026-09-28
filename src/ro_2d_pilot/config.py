"""Explicit inputs for one 2D pilot case.

Design variables are filament diameter ``d`` and streamwise pitch ``L``.
Operating conditions default to the campaign point 0.2 m/s and 6 MPa but
are stored on the case, not hidden in the mesher. No design-space bounds
are encoded here: only geometric impossibility is rejected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ro.campaign_geometry import CAMPAIGN_H_M
from ro.solver_common import make_base_case_name

FIDELITY_LOW = "low"
FIDELITY_HIGH = "high"
FIDELITIES = (FIDELITY_LOW, FIDELITY_HIGH)

DEFAULT_INLET_VELOCITY_M_S = 0.2
DEFAULT_OUTLET_GAUGE_PRESSURE_PA = 6.0e6
DEFAULT_N_PITCHES = 1
# Fluent 2D reports are per unit depth. One metre matches that convention.
DEFAULT_UNIT_DEPTH_M = 1.0


def _positive_finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a positive finite float, got {value!r}.")
    return number


def _nonnegative_finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(
            f"{name} must be a nonnegative finite float, got {value!r}."
        )
    return number


@dataclass(frozen=True)
class OperatingPoint:
    inlet_velocity_m_s: float = DEFAULT_INLET_VELOCITY_M_S
    outlet_gauge_pressure_pa: float = DEFAULT_OUTLET_GAUGE_PRESSURE_PA

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inlet_velocity_m_s",
            _positive_finite("inlet_velocity_m_s", self.inlet_velocity_m_s),
        )
        object.__setattr__(
            self,
            "outlet_gauge_pressure_pa",
            _nonnegative_finite(
                "outlet_gauge_pressure_pa",
                self.outlet_gauge_pressure_pa,
            ),
        )

    @property
    def run_id(self) -> str:
        return make_base_case_name(
            self.inlet_velocity_m_s,
            self.outlet_gauge_pressure_pa,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "inlet_velocity_m_s": self.inlet_velocity_m_s,
            "outlet_gauge_pressure_pa": self.outlet_gauge_pressure_pa,
        }


@dataclass(frozen=True)
class PilotConfig:
    """One geometry at one mesh fidelity and one operating point."""

    d_m: float
    L_m: float
    fidelity: str
    operating: OperatingPoint | None = None
    channel_height_m: float = CAMPAIGN_H_M
    n_pitches: int = DEFAULT_N_PITCHES
    unit_depth_m: float = DEFAULT_UNIT_DEPTH_M

    def __post_init__(self) -> None:
        d_m = _positive_finite("d_m", self.d_m)
        L_m = _positive_finite("L_m", self.L_m)
        height = _positive_finite("channel_height_m", self.channel_height_m)
        depth = _positive_finite("unit_depth_m", self.unit_depth_m)
        if self.fidelity not in FIDELITIES:
            raise ValueError(
                f"fidelity must be one of {FIDELITIES}, got {self.fidelity!r}."
            )
        if isinstance(self.n_pitches, bool) or not isinstance(self.n_pitches, int):
            raise ValueError(
                f"n_pitches must be a positive int, got {self.n_pitches!r}."
            )
        if self.n_pitches < 1:
            raise ValueError(
                f"n_pitches must be >= 1, got {self.n_pitches!r}."
            )
        if not d_m < height:
            raise ValueError(
                "filament diameter must be smaller than the channel height "
                f"(d_m={d_m!r}, channel_height_m={height!r})."
            )
        if not L_m > d_m:
            raise ValueError(
                "pitch must be larger than the filament diameter so neighboring "
                f"obstacles do not overlap (L_m={L_m!r}, d_m={d_m!r})."
            )
        object.__setattr__(self, "d_m", d_m)
        object.__setattr__(self, "L_m", L_m)
        object.__setattr__(self, "channel_height_m", height)
        object.__setattr__(self, "unit_depth_m", depth)
        if self.operating is None:
            object.__setattr__(self, "operating", OperatingPoint())

    def to_dict(self) -> dict[str, object]:
        operating = self.operating
        assert operating is not None
        return {
            "d_m": self.d_m,
            "L_m": self.L_m,
            "fidelity": self.fidelity,
            "channel_height_m": self.channel_height_m,
            "n_pitches": self.n_pitches,
            "unit_depth_m": self.unit_depth_m,
            "operating": operating.to_dict(),
            "run_id": operating.run_id,
        }


def configs_for_fidelities(
    *,
    d_m: float,
    L_m: float,
    operating: OperatingPoint | None = None,
    channel_height_m: float = CAMPAIGN_H_M,
    n_pitches: int = DEFAULT_N_PITCHES,
    unit_depth_m: float = DEFAULT_UNIT_DEPTH_M,
) -> dict[str, PilotConfig]:
    """Low and high fidelity cases that share geometry and operating point."""
    point = operating if operating is not None else OperatingPoint()
    return {
        fidelity: PilotConfig(
            d_m=d_m,
            L_m=L_m,
            fidelity=fidelity,
            operating=point,
            channel_height_m=channel_height_m,
            n_pitches=n_pitches,
            unit_depth_m=unit_depth_m,
        )
        for fidelity in FIDELITIES
    }
