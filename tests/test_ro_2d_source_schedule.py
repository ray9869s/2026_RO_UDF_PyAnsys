"""Ramp-aware 2D solve control. No Fluent session."""

from __future__ import annotations

import re

from helpers import REPO_ROOT
from ro.solver_common import classify_solver_stop_reason
from ro_2d_pilot.fluent_session import classify_transcript
from ro_2d_pilot.ladder import compare_ladder, format_ladder
from ro_2d_pilot.plan import build_plan
from ro_2d_pilot.record import build_result_record
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.fluent_session import FluentSetupError, _set_convergence_checks
from ro_2d_pilot.source_schedule import (
    CHUNK_ITERATIONS,
    CONVERGED_BEFORE_FULL_SOURCE,
    FULL_RAMP_START_ITER,
    FULL_SOURCE_MIN_ITERATIONS,
    ITERATION_STATE_BACKEND,
    RAMP_TELEMETRY_WARNING,
    ScheduleObservation,
    display_ramp,
    earliest_convergence_iteration,
    last_solver_iteration,
    observation_from_transcript,
    parse_source_ramp,
    ramp_telemetry_warning,
    run_source_schedule,
    source_ramp_factor,
)


def _metrics(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "cell_count": 1000,
        "lmh": 20.0,
        "pressure_drop_pa": 1000.0,
        "mass_balance_rel": 1.0e-6,
        "convergence_status": "residual_converged",
        "solver_iterations": 210,
        "source_ramp_final": 1.0,
        "full_source_reached": True,
        "full_source_start_iteration": FULL_RAMP_START_ITER,
        "full_source_iterations": FULL_SOURCE_MIN_ITERATIONS + 11,
        "convergence_checked_after_full_source": True,
        "total_iterations": 210,
        "convergence_iteration": 210,
    }
    payload.update(overrides)
    return payload


def _record(plan, **overrides: object) -> dict[str, object]:
    return build_result_record(plan, _metrics(**overrides))


def test_early_residual_stop_continues_until_full_source_settling() -> None:
    observations = [
        ScheduleObservation(95, 0.5, 95, False),
        ScheduleObservation(FULL_RAMP_START_ITER, 1.0, FULL_RAMP_START_ITER, False),
        ScheduleObservation(
            FULL_RAMP_START_ITER + FULL_SOURCE_MIN_ITERATIONS,
            1.0,
            FULL_RAMP_START_ITER,
            False,
        ),
        ScheduleObservation(210, 1.0, 210, False),
    ]
    requests = []

    def iterate(request) -> None:
        requests.append(request)

    def observe():
        return observations.pop(0)

    result = run_source_schedule(iterate, observe, 2000)
    assert requests[0].check_convergence is False
    assert requests[0].count == CHUNK_ITERATIONS
    assert requests[1].check_convergence is False
    assert requests[1].count == CHUNK_ITERATIONS
    assert requests[2].check_convergence is False
    assert requests[2].count == CHUNK_ITERATIONS
    assert requests[3].check_convergence is True
    assert all(request.check_convergence is False for request in requests[:3])
    assert result.full_source_reached is True
    assert result.source_ramp_final == 1.0
    assert result.convergence_checked_after_full_source is True
    assert result.full_source_start_iteration == FULL_RAMP_START_ITER
    assert earliest_convergence_iteration(FULL_RAMP_START_ITER) == (
        FULL_RAMP_START_ITER + FULL_SOURCE_MIN_ITERATIONS + 1
    )


def test_ramp_below_one_cannot_be_valid() -> None:
    plan = build_plan(PilotConfig(d_m=4.0e-4, L_m=4.0e-3, fidelity="medium"))
    for factor, iteration in ((0.5, 95), (0.8, 116)):
        record = _record(
            plan,
            source_ramp_final=factor,
            full_source_reached=False,
            convergence_checked_after_full_source=False,
            full_source_iterations=0,
            solver_iterations=iteration,
            total_iterations=iteration,
            convergence_iteration=iteration,
        )
        assert record["validity"] == "invalid"
        assert record["validity_reason"] == CONVERGED_BEFORE_FULL_SOURCE
        assert source_ramp_factor(iteration) == factor


def test_full_source_after_settling_can_be_valid() -> None:
    plan = build_plan(PilotConfig(d_m=4.0e-4, L_m=4.0e-3, fidelity="fine"))
    record = _record(plan)
    assert record["validity"] == "valid"
    assert record["source_ramp_final"] == 1.0
    assert record["validity_reason"] is None
    assert record["full_source_iterations"] >= FULL_SOURCE_MIN_ITERATIONS
    capped = _record(plan, convergence_status="max_iter_reached", convergence_iteration=None)
    assert capped["validity"] == "valid"
    assert capped["convergence_status"] == "max_iter_reached"


def test_max_iterations_before_full_source_stays_explicit() -> None:
    requests = []

    def iterate(request) -> None:
        requests.append(request)

    def observe():
        return ScheduleObservation(100, 0.8, 100, False)

    result = run_source_schedule(iterate, observe, 100)
    assert len(requests) == 1
    assert requests[0].check_convergence is False
    assert requests[0].count == CHUNK_ITERATIONS
    assert result.convergence_checked_after_full_source is False
    assert result.source_ramp_final == 0.8
    assert result.full_source_reached is False
    status = classify_transcript(
        "iteration 100:\n",
        max_iterations=100,
        accept_convergence_after=result.accept_convergence_after,
    )
    assert status["convergence_status"] == "max_iter_reached"


def test_early_convergence_marker_is_ignored_until_settling_finishes() -> None:
    text = "\n".join(
        [
            "iteration 95:",
            "95 solution is converged",
            "RO2D_SOURCE_RAMP iter=210 factor=1",
            "iteration 210:",
            "210 solution is converged",
        ]
    )
    early = classify_transcript(
        text,
        max_iterations=2000,
        accept_convergence_after=2000,
    )
    assert early["convergence_status"] == "unknown_early_stop"
    assert early["convergence_iteration"] is None
    finished = classify_transcript(
        text,
        max_iterations=2000,
        accept_convergence_after=FULL_RAMP_START_ITER + FULL_SOURCE_MIN_ITERATIONS,
    )
    assert finished["convergence_status"] == "residual_converged"
    assert finished["convergence_iteration"] == 210
    assert parse_source_ramp(text) == (210, 1.0)


def test_3d_convergence_classifier_is_unchanged() -> None:
    assert classify_solver_stop_reason(
        diverged=False,
        residuals_met=True,
        qoi_met=False,
        final_iteration=95,
        max_iterations=2000,
    ) == "residual_converged"
    production = (REPO_ROOT / "udfs" / "260822_RO_UDF.c").read_text(encoding="utf-8")
    solver = (REPO_ROOT / "scripts" / "solver_code_260616.py").read_text(encoding="utf-8")
    assert "RO2D_SOURCE_RAMP" not in production
    assert "source_schedule" not in solver
    pilot = (REPO_ROOT / "udfs" / "260929_RO_UDF.c").read_text(encoding="utf-8")
    assert "water_flux * area_mag * RHO_REF / cell_volume" in pilot
    assert "#define RAMP_ITER_3      150" in pilot
    assert "#define RAMP_FACTOR_3    0.8" in pilot
    assert 'Message("RO2D_SOURCE_RAMP iter=%d factor=%.6g\\n"' in pilot


def test_ladder_skips_qoi_discrepancy_when_the_ramp_is_not_full() -> None:
    shared = {
        "geo_id": "case",
        "d_m": 4.0e-4,
        "L_m": 4.0e-3,
        "channel_height_m": 7.7e-4,
        "n_pitches": 3,
        "inlet_velocity_m_s": 0.2,
        "outlet_gauge_pressure_pa": 6.0e6,
        "cell_count": 10,
        "lmh": 20.0,
        "cp_average": 1.05,
        "pressure_drop_per_length_pa_per_m": 25000.0,
        "mass_balance_rel": 1.0e-6,
        "solver_wall_time_s": 10.0,
        "total_wall_time_s": 20.0,
        "convergence_status": "residual_converged",
    }
    partial = dict(
        shared,
        mesh_level="medium",
        fidelity="medium",
        source_ramp_final=0.8,
        full_source_reached=False,
        convergence_checked_after_full_source=False,
        full_source_iterations=0,
        total_iterations=116,
        solver_iterations=116,
        lmh=20.67,
    )
    full = dict(
        shared,
        mesh_level="fine",
        fidelity="fine",
        source_ramp_final=1.0,
        full_source_reached=True,
        convergence_checked_after_full_source=True,
        full_source_iterations=60,
        total_iterations=220,
        solver_iterations=220,
        lmh=18.0,
        cell_count=20,
    )
    report = compare_ladder([partial, full])
    assert report["adjacent"][0]["qoi_compared"] is False
    assert report["adjacent"][0]["relative_discrepancy"]["lmh"] is None
    text = format_ladder(report)
    assert "unsuitable" in text
    assert "full source ramp was not 1.0" in text


def test_result_schema_records_the_ramp_metadata() -> None:
    plan = build_plan(PilotConfig(d_m=4.0e-4, L_m=4.0e-3, fidelity="very_fine"))
    record = _record(plan)
    for key in (
        "source_ramp_final",
        "full_source_reached",
        "full_source_start_iteration",
        "full_source_iterations",
        "convergence_checked_after_full_source",
        "convergence_iteration",
        "total_iterations",
        "validity_reason",
        "iteration_state_backend",
    ):
        assert key in record
    assert record["source_ramp_final"] == 1.0
    assert record["full_source_reached"] is True
    assert record["iteration_state_backend"] is None


def _transcript(*steps: tuple[int, float]) -> str:
    lines = ["iter continuity x-velocity y-velocity nacl time/iter"]
    for iteration, factor in steps:
        lines.append(f"{iteration} 1.0e-4 2.0e-4 3.0e-4 4.0e-4 0:00:01 10")
        lines.append(f"RO2D_SOURCE_RAMP iter={iteration} factor={factor}")
    return "\n".join(lines)


def test_parser_returns_the_latest_ramp_not_the_first() -> None:
    text = _transcript((10, 0.2), (70, 0.5), (120, 0.8))
    assert parse_source_ramp(text) == (120, 0.8)
    assert last_solver_iteration(text) == 120


def test_synthetic_iterations_follow_the_schedule() -> None:
    text = _transcript((10, 0.2), (70, 0.5), (120, 0.8), (160, 1.0))
    observed = observation_from_transcript(text, diverged=False)
    assert observed.iteration == 160
    assert observed.ramp_factor == 1.0
    assert observed.first_full_ramp_iteration == 160
    for iteration, factor in ((10, 0.2), (70, 0.5), (120, 0.8), (160, 1.0)):
        assert source_ramp_factor(iteration) == factor


def test_missing_iteration_is_not_recorded_as_zero() -> None:
    def iterate(_request) -> None:
        return None

    result = run_source_schedule(
        iterate,
        lambda: ScheduleObservation(None, 0.8, 80, False),
        2000,
    )
    assert result.completed_iterations is None
    assert result.completed_iterations != 0
    assert result.source_ramp_final == 0.8
    assert display_ramp(None, 0) is None
    assert display_ramp(None, None) is None


def test_successful_iteration_is_recorded() -> None:
    def iterate(_request) -> None:
        return None

    result = run_source_schedule(
        iterate,
        lambda: ScheduleObservation(10, 0.2, 10, False),
        2000,
    )
    assert result.completed_iterations == 10
    assert result.source_ramp_final == 0.2


def test_stage_b_does_not_open_before_iteration_150() -> None:
    requests = []

    def iterate(request) -> None:
        requests.append(request)

    def observe():
        return ScheduleObservation(40, 1.0, 40, False)

    result = run_source_schedule(iterate, observe, 80)
    assert len(requests) >= 2
    assert all(request.check_convergence is False for request in requests)
    assert result.convergence_checked_after_full_source is False
    assert result.full_source_reached is False


def test_stage_c_waits_for_fifty_full_source_iterations() -> None:
    iteration = 0
    checks: list[bool] = []

    def iterate(request) -> None:
        nonlocal iteration
        checks.append(request.check_convergence)
        if request.check_convergence:
            assert iteration >= FULL_RAMP_START_ITER + FULL_SOURCE_MIN_ITERATIONS
        iteration += request.count

    def observe():
        return ScheduleObservation(
            iteration,
            source_ramp_factor(iteration),
            iteration,
            False,
            first_full_ramp_iteration=(
                FULL_RAMP_START_ITER if iteration >= FULL_RAMP_START_ITER else None
            ),
        )

    result = run_source_schedule(iterate, observe, 220)
    assert checks[-1] is True
    assert all(flag is False for flag in checks[:-1])
    assert result.completed_iterations == 220
    assert result.full_source_iterations is not None
    assert result.full_source_iterations >= FULL_SOURCE_MIN_ITERATIONS
    assert result.convergence_checked_after_full_source is True
    assert result.source_ramp_final == 1.0


def test_convergence_checks_are_written_and_read_back() -> None:
    class _Node:
        def __init__(self) -> None:
            self.value = True

        def set_state(self, value: bool) -> None:
            self.value = value

        def get_state(self) -> bool:
            return self.value

    class _Equation:
        def __init__(self) -> None:
            self.check_convergence = _Node()

    class _Equations:
        def __init__(self) -> None:
            self.items = {
                "continuity": _Equation(),
                "x-velocity": _Equation(),
                "y-velocity": _Equation(),
                "nacl": _Equation(),
            }

        def get_state(self) -> dict[str, _Equation]:
            return self.items

        def __getitem__(self, name: str) -> _Equation:
            return self.items[name]

    equations = _Equations()

    class _Residual:
        pass

    residual = _Residual()
    residual.equations = equations

    class _Monitor:
        pass

    monitor = _Monitor()
    monitor.residual = residual

    class _Solution:
        pass

    solution = _Solution()
    solution.monitor = monitor
    _set_convergence_checks(solution, False)
    assert all(
        item.check_convergence.get_state() is False
        for item in equations.items.values()
    )
    _set_convergence_checks(solution, True)
    assert all(
        item.check_convergence.get_state() is True
        for item in equations.items.values()
    )
    equations.items["nacl"].check_convergence.get_state = lambda: True
    try:
        _set_convergence_checks(solution, False)
    except FluentSetupError as exc:
        assert "read-back" in str(exc)
    else:
        raise AssertionError("a mismatched read-back must fail")


def test_ramp_schedule_matches_the_2d_udf_defines() -> None:
    text = (REPO_ROOT / "udfs" / "260929_RO_UDF.c").read_text(encoding="utf-8")

    def defined(name: str) -> float:
        match = re.search(rf"#define\s+{name}\s+([0-9.]+)", text)
        assert match is not None
        return float(match.group(1))

    assert defined("RAMP_ITER_1") == 50
    assert defined("RAMP_ITER_2") == 100
    assert defined("RAMP_ITER_3") == 150
    assert source_ramp_factor(49) == defined("RAMP_FACTOR_1")
    assert source_ramp_factor(50) == defined("RAMP_FACTOR_2")
    assert source_ramp_factor(100) == defined("RAMP_FACTOR_3")
    assert source_ramp_factor(149) == defined("RAMP_FACTOR_3")
    assert source_ramp_factor(150) == defined("RAMP_FACTOR_FULL")


def test_flux_ratio_disagreement_warns_without_controlling_the_solve() -> None:
    assert ramp_telemetry_warning(0.2, 0.8) == RAMP_TELEMETRY_WARNING
    assert ramp_telemetry_warning(1.0, 0.99) is None
    assert ITERATION_STATE_BACKEND == "transcript"
