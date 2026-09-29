"""When the 2D membrane source is at full strength.

The factors match ``udfs/260929_RO_UDF.c``. Residual convergence is not
accepted until the UDF has reported ramp 1 and the full-source settling
iterations have already been run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ro.solver_common import (
    parse_fluent_convergence_marker,
    parse_last_residual_iteration_from_transcript_text,
)

RAMP_ITER_1 = 50
RAMP_FACTOR_1 = 0.2
RAMP_ITER_2 = 100
RAMP_FACTOR_2 = 0.5
RAMP_ITER_3 = 150
RAMP_FACTOR_3 = 0.8
FULL_RAMP_FACTOR = 1.0
# N_ITER >= FULL_RAMP_START_ITER selects FULL_RAMP_FACTOR in the UDF.
FULL_RAMP_START_ITER = RAMP_ITER_3
# Extra iterations after the first recorded full-ramp iteration, before
# residual or QoI convergence may stop the case.
FULL_SOURCE_MIN_ITERATIONS = 50
CHUNK_ITERATIONS = 25
CONVERGED_BEFORE_FULL_SOURCE = "converged_before_full_source"
ITERATION_STATE_BACKEND = "transcript"
RAMP_TELEMETRY_WARNING = (
    "ramp telemetry inconsistent with source/surface flux ratio"
)
RAMP_LINE_PREFIX = "RO2D_SOURCE_RAMP"

_RAMP_LINE_RE = re.compile(
    rf"{RAMP_LINE_PREFIX}\s+iter=(\d+)\s+factor="
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


@dataclass(frozen=True)
class IterateRequest:
    count: int
    check_convergence: bool


@dataclass(frozen=True)
class ScheduleObservation:
    iteration: int | None
    ramp_factor: float | None
    ramp_iteration: int | None
    diverged: bool
    first_full_ramp_iteration: int | None = None


@dataclass
class ScheduleResult:
    completed_iterations: int | None
    source_ramp_final: float | None
    full_source_reached: bool
    full_source_start_iteration: int | None
    full_source_iterations: int | None
    convergence_checked_after_full_source: bool
    diverged: bool
    stalled: bool
    accept_convergence_after: int | None


@dataclass
class _State:
    stage: str = "ramp"
    completed: int = 0
    ramp: float | None = None
    ramp_iteration: int | None = None
    start: int | None = None
    checked: bool = False
    diverged: bool = False
    stalled: bool = False
    issued_final: bool = False
    saw_observation: bool = False
    ramp_matches_schedule: bool = True


def source_ramp_factor(iteration: object) -> float | None:
    """Return the UDF ramp at this ``N_ITER`` value."""
    if isinstance(iteration, bool) or not isinstance(iteration, (int, float)):
        return None
    if isinstance(iteration, float) and not iteration.is_integer():
        return None
    step = int(iteration)
    if step < RAMP_ITER_1:
        return RAMP_FACTOR_1
    if step < RAMP_ITER_2:
        return RAMP_FACTOR_2
    if step < RAMP_ITER_3:
        return RAMP_FACTOR_3
    return FULL_RAMP_FACTOR


def display_ramp(recorded: object, iteration: object) -> float | None:
    """Recorded ramp, or the schedule value for a real iteration count.

    Iteration 0 is not a completed solve. Do not report the first ramp
    step for that placeholder.
    """
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool):
        return float(recorded)
    factor = source_ramp_factor(iteration)
    if factor is None:
        return None
    if isinstance(iteration, bool) or not isinstance(iteration, (int, float)):
        return None
    if int(iteration) < 1:
        return None
    return factor


def earliest_convergence_iteration(full_source_start_iteration: int) -> int:
    """First iteration that may stop the case after settling.

    Settling runs with convergence checks off. The next iteration is the
    first one allowed to satisfy the residual or QoI criterion.
    """
    if full_source_start_iteration < 1:
        raise ValueError(
            "full_source_start_iteration must be >= 1, "
            f"got {full_source_start_iteration!r}."
        )
    return full_source_start_iteration + FULL_SOURCE_MIN_ITERATIONS + 1


def parse_source_ramp(text: str) -> tuple[int, float] | None:
    """Return the last UDF ramp line as ``(N_ITER, factor)``."""
    found: tuple[int, float] | None = None
    for match in _RAMP_LINE_RE.finditer(text):
        found = (int(match.group(1)), float(match.group(2)))
    return found


def first_full_ramp_iteration(text: str) -> int | None:
    """Return the first ``N_ITER`` whose recorded ramp is 1."""
    for match in _RAMP_LINE_RE.finditer(text):
        if float(match.group(2)) == FULL_RAMP_FACTOR:
            return int(match.group(1))
    return None


def observation_from_transcript(
    text: str,
    *,
    diverged: bool,
) -> ScheduleObservation:
    """Read the latest iteration and the latest ramp line.

    A 2D residual row has four residual columns, so the 3D five-column
    parser misses it. The iteration used here is the greatest completed
    iteration among the 2D row, the 3D row, the convergence marker, and
    the latest UDF ramp line.
    """
    parsed = parse_source_ramp(text)
    iteration = last_solver_iteration(text)
    ramp_factor = None if parsed is None else parsed[1]
    ramp_iteration = None if parsed is None else parsed[0]
    if ramp_factor is None and iteration is not None and iteration >= 1:
        ramp_factor = source_ramp_factor(iteration)
        ramp_iteration = iteration
    return ScheduleObservation(
        iteration=iteration,
        ramp_factor=ramp_factor,
        ramp_iteration=ramp_iteration,
        diverged=diverged,
        first_full_ramp_iteration=first_full_ramp_iteration(text),
    )


def last_solver_iteration(text: str) -> int | None:
    """Latest completed iteration visible in a 2D or 3D transcript."""
    values = [
        _last_residual_row_iteration(text),
        parse_last_residual_iteration_from_transcript_text(text),
        parse_fluent_convergence_marker(text)[1],
    ]
    parsed = parse_source_ramp(text)
    if parsed is not None:
        values.append(parsed[0])
    found = [
        value
        for value in values
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if not found:
        return None
    return max(found)


def _last_residual_row_iteration(text: str) -> int | None:
    """Last residual-table iteration, including a 2D four-column row."""
    last: int | None = None
    header_seen = False
    for raw in text.splitlines():
        tokens = raw.split()
        if not tokens:
            continue
        lowered = [token.lower() for token in tokens]
        if lowered[0] == "iter" and "continuity" in lowered:
            header_seen = True
            continue
        if not header_seen or len(tokens) < 5:
            continue
        try:
            iteration = int(tokens[0])
            for index in range(1, 5):
                float(tokens[index])
        except ValueError:
            continue
        last = iteration
    return last


def full_source_gate_ok(
    *,
    source_ramp_final: object,
    full_source_reached: object,
    convergence_checked_after_full_source: object,
    full_source_iterations: object,
) -> bool:
    """True only for a solution finished at ramp 1 after settling."""
    if isinstance(source_ramp_final, bool) or not isinstance(
        source_ramp_final, (int, float)
    ):
        return False
    if float(source_ramp_final) != FULL_RAMP_FACTOR:
        return False
    if full_source_reached is not True:
        return False
    if convergence_checked_after_full_source is not True:
        return False
    if isinstance(full_source_iterations, bool) or not isinstance(
        full_source_iterations, int
    ):
        return False
    return full_source_iterations >= FULL_SOURCE_MIN_ITERATIONS


def run_source_schedule(iterate, observe, max_iterations: int) -> ScheduleResult:
    """Advance ramp, settling, then one convergence-checked chunk.

    ``iterate`` receives an ``IterateRequest``. ``observe`` returns a
    ``ScheduleObservation`` after that chunk. A residual stop before the
    recorded ramp is 1 does not end the schedule.
    """
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations!r}.")
    state = _State()
    while True:
        request = _next_request(state, max_iterations)
        if request is None:
            break
        iterate(request)
        observation = observe()
        if not isinstance(observation, ScheduleObservation):
            raise TypeError("observe() must return a ScheduleObservation.")
        _absorb(state, observation)
        if state.diverged or state.stalled:
            break
    return _result(state, max_iterations)


def _next_request(state: _State, max_iterations: int) -> IterateRequest | None:
    if state.diverged or state.stalled or state.issued_final:
        return None
    remaining = max_iterations - state.completed
    if remaining <= 0:
        return None
    if (
        state.stage == "ramp"
        and state.completed >= FULL_RAMP_START_ITER
        and _ramp_is_full(state)
    ):
        state.stage = "settle"
        if state.start is None:
            state.start = state.ramp_iteration
    if state.stage == "ramp":
        return IterateRequest(_ramp_chunk(state, remaining), False)
    if state.stage == "settle":
        assert state.start is not None
        target = state.start + FULL_SOURCE_MIN_ITERATIONS
        if state.completed < target:
            return IterateRequest(
                min(CHUNK_ITERATIONS, target - state.completed, remaining),
                False,
            )
        state.stage = "final"
    if state.stage == "final":
        state.issued_final = True
        state.checked = True
        return IterateRequest(remaining, True)
    return None


def _ramp_is_full(state: _State) -> bool:
    return (
        state.ramp == FULL_RAMP_FACTOR
        and state.ramp_matches_schedule
        and state.completed >= FULL_RAMP_START_ITER
        and isinstance(state.ramp_iteration, int)
        and not isinstance(state.ramp_iteration, bool)
    )


def _ramp_chunk(state: _State, remaining: int) -> int:
    if state.completed < FULL_RAMP_START_ITER:
        span = FULL_RAMP_START_ITER - state.completed
    elif state.ramp is None or state.ramp != FULL_RAMP_FACTOR:
        span = 1
    else:
        span = 1
    return min(CHUNK_ITERATIONS, span, remaining)


def _absorb(state: _State, observation: ScheduleObservation) -> None:
    state.saw_observation = True
    if observation.diverged:
        state.diverged = True
        state.stage = "done"
        _remember_progress(state, observation)
        return
    if observation.iteration is None or observation.iteration <= state.completed:
        if observation.ramp_factor is not None and state.completed < 1:
            state.ramp = float(observation.ramp_factor)
            state.ramp_iteration = observation.ramp_iteration
        state.stalled = True
        state.stage = "done"
        return
    state.completed = observation.iteration
    _remember_ramp(state, observation)
    _remember_full_start(state, observation)


def _remember_progress(state: _State, observation: ScheduleObservation) -> None:
    if observation.iteration is not None and observation.iteration >= state.completed:
        state.completed = observation.iteration
    _remember_ramp(state, observation)
    _remember_full_start(state, observation)


def _remember_ramp(state: _State, observation: ScheduleObservation) -> None:
    factor = observation.ramp_factor
    if factor is None and state.completed >= 1:
        factor = source_ramp_factor(state.completed)
        iteration = state.completed
    else:
        iteration = observation.ramp_iteration
    if factor is None:
        return
    expected = (
        source_ramp_factor(state.completed) if state.completed >= 1 else None
    )
    state.ramp_matches_schedule = not (
        expected is not None
        and observation.ramp_factor is not None
        and abs(float(observation.ramp_factor) - expected) > 1.0e-9
    )
    state.ramp = float(factor)
    state.ramp_iteration = iteration


def _remember_full_start(state: _State, observation: ScheduleObservation) -> None:
    start = observation.first_full_ramp_iteration
    if isinstance(start, bool) or not isinstance(start, int):
        return
    if state.start is None or start < state.start:
        state.start = start


def _result(state: _State, max_iterations: int) -> ScheduleResult:
    reached = _ramp_is_full(state)
    start = state.start if state.start is not None else (
        state.ramp_iteration if reached else None
    )
    if reached and start is not None and state.completed >= start:
        full_iterations: int | None = state.completed - start + 1
    elif state.saw_observation and state.completed >= 1:
        full_iterations = 0
    else:
        full_iterations = None
    if state.checked and start is not None:
        accept_after: int | None = start + FULL_SOURCE_MIN_ITERATIONS
    else:
        accept_after = max_iterations
    if state.saw_observation and state.completed >= 1:
        completed: int | None = state.completed
    else:
        completed = None
    return ScheduleResult(
        completed_iterations=completed,
        source_ramp_final=state.ramp,
        full_source_reached=reached,
        full_source_start_iteration=start if reached else None,
        full_source_iterations=full_iterations,
        convergence_checked_after_full_source=state.checked,
        diverged=state.diverged,
        stalled=state.stalled,
        accept_convergence_after=accept_after,
    )


def ramp_telemetry_warning(
    recorded_ramp: object,
    inferred_ramp: object,
) -> str | None:
    """Cross-check only. This does not control the solver."""
    if isinstance(recorded_ramp, bool) or not isinstance(recorded_ramp, (int, float)):
        return None
    if isinstance(inferred_ramp, bool) or not isinstance(inferred_ramp, (int, float)):
        return None
    if abs(float(recorded_ramp) - float(inferred_ramp)) > 0.05:
        return RAMP_TELEMETRY_WARNING
    return None
