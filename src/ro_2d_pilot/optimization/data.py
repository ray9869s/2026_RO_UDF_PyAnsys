"""Observed CFD outcomes. Only valid rows train the GP."""

from __future__ import annotations

from dataclasses import dataclass, field

from ro_2d_pilot.optimization.domain import FIDELITY_HIGH, Design, normalize
from ro_2d_pilot.optimization.objective import pressure_feasible
from ro_2d_pilot.source_schedule import full_source_gate_ok

STATE_VALID = "valid"
STATE_DIVERGED = "diverged"
STATE_INVALID = "invalid"
STATE_EXECUTION_FAILED = "execution_failed"
_FAILURE_STATES = frozenset({STATE_DIVERGED, STATE_INVALID, STATE_EXECUTION_FAILED})


@dataclass
class Outcome:
    d_m: float
    L_m: float
    fidelity: str
    state: str
    lmh: float | None = None
    dp_per_l: float | None = None
    cp: float | None = None
    solver_s: float | None = None
    wall_s: float | None = None
    reused_existing: bool = False
    cost_imputed: bool = False

    @property
    def key(self) -> tuple[float, float, str]:
        return (round(self.d_m, 12), round(self.L_m, 12), self.fidelity)

    @property
    def x(self) -> tuple[float, float]:
        return normalize(self.d_m, self.L_m)

    @property
    def failed(self) -> bool:
        return self.state in _FAILURE_STATES

    @property
    def trains_gp(self) -> bool:
        return (
            self.state == STATE_VALID
            and self.lmh is not None
            and self.dp_per_l is not None
        )


@dataclass
class Dataset:
    outcomes: list[Outcome] = field(default_factory=list)

    def seen(self, d_m: float, L_m: float, fidelity: str) -> bool:
        key = (round(d_m, 12), round(L_m, 12), fidelity)
        return any(item.key == key for item in self.outcomes)

    def add(self, outcome: Outcome) -> None:
        if self.seen(outcome.d_m, outcome.L_m, outcome.fidelity):
            raise ValueError(
                "Refusing to repeat "
                f"d={outcome.d_m} L={outcome.L_m} fidelity={outcome.fidelity}."
            )
        self.outcomes.append(outcome)

    def training(self, fidelity: str) -> list[Outcome]:
        return [
            item
            for item in self.outcomes
            if item.fidelity == fidelity and item.trains_gp
        ]

    def best_hf_feasible_lmh(self, limit: float) -> float | None:
        values = [
            item.lmh
            for item in self.training(FIDELITY_HIGH)
            if item.lmh is not None
            and item.dp_per_l is not None
            and pressure_feasible(item.dp_per_l, limit)
        ]
        if not values:
            return None
        return max(values)

    def best_hf_feasible(self, limit: float) -> Outcome | None:
        best: Outcome | None = None
        for item in self.training(FIDELITY_HIGH):
            if item.lmh is None or item.dp_per_l is None:
                continue
            if not pressure_feasible(item.dp_per_l, limit):
                continue
            if best is None or item.lmh > (best.lmh or -1.0):
                best = item
        return best


def outcome_from_result(
    d_m: float,
    L_m: float,
    fidelity: str,
    record: dict[str, object] | None,
    *,
    reused_existing: bool,
) -> Outcome:
    if record is None:
        return Outcome(
            d_m=d_m,
            L_m=L_m,
            fidelity=fidelity,
            state=STATE_EXECUTION_FAILED,
            reused_existing=reused_existing,
        )
    state = _state_of(record)
    if state != STATE_VALID:
        return Outcome(
            d_m=d_m,
            L_m=L_m,
            fidelity=fidelity,
            state=state,
            solver_s=_float_or_none(record.get("solver_wall_time_s")),
            wall_s=_float_or_none(record.get("total_wall_time_s")),
            reused_existing=reused_existing,
        )
    return Outcome(
        d_m=d_m,
        L_m=L_m,
        fidelity=fidelity,
        state=STATE_VALID,
        lmh=_required_float(record, "lmh"),
        dp_per_l=_required_float(record, "pressure_drop_per_length_pa_per_m"),
        cp=_float_or_none(record.get("cp_average")),
        solver_s=_float_or_none(record.get("solver_wall_time_s")),
        wall_s=_float_or_none(record.get("total_wall_time_s")),
        reused_existing=reused_existing,
    )


def _state_of(record: dict[str, object]) -> str:
    status = record.get("convergence_status")
    validity = record.get("validity")
    if status == "diverged":
        return STATE_DIVERGED
    if validity != "valid":
        return STATE_INVALID if validity == "invalid" else STATE_EXECUTION_FAILED
    if not _finite_pair(record.get("lmh"), record.get("pressure_drop_per_length_pa_per_m")):
        return STATE_INVALID
    mass = record.get("mass_balance_rel")
    if not isinstance(mass, (int, float)) or isinstance(mass, bool) or abs(float(mass)) >= 1.0e-3:
        return STATE_INVALID
    if not full_source_gate_ok(
        source_ramp_final=record.get("source_ramp_final"),
        full_source_reached=record.get("full_source_reached"),
        convergence_checked_after_full_source=record.get(
            "convergence_checked_after_full_source"
        ),
        full_source_iterations=record.get("full_source_iterations"),
    ):
        return STATE_INVALID
    return STATE_VALID


def _finite_pair(left: object, right: object) -> bool:
    return all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (left, right)
    )


def _required_float(record: dict[str, object], name: str) -> float:
    value = record[name]
    return float(value)  # type: ignore[arg-type]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def design_key(design: Design) -> tuple[float, float]:
    return (round(design.d_m, 12), round(design.L_m, 12))
