"""HF monitor stop decisions. No Fluent."""

from __future__ import annotations

import pytest

from ro.hf_monitor_stop import (
    iterations_after_resume,
    hf_monitor_ready,
    phase_record,
    require_hf_monitor_stop,
    resume_iteration_budget,
    resume_stop_reason,
    run_hf_monitor_iterations,
    should_resume_production,
)
from ro.lf_monitor_stop import lf_monitor_decision


def _flat(start: int, count: int, value: float):
    return [(iteration, value) for iteration in range(start, start + count)]


def _decision(stable: bool):
    series = _flat(351, 200, 1.0)
    decision = lf_monitor_decision(
        series,
        series,
        ramp_end=150,
        n=200,
        tol_lmh=1.0e-4,
        tol_dp=1.0e-4,
    )
    assert decision["stable"] is stable
    return decision


def test_ready_when_monitors_and_continuity_are_inside_the_caps():
    assert hf_monitor_ready(_decision(True), 1.0e-6, 1.0e-5) is True
    assert hf_monitor_ready(_decision(True), 1.0e-5, 1.0e-5) is True


def test_not_ready_when_continuity_is_above_the_cap_or_missing():
    assert hf_monitor_ready(_decision(True), 1.1e-5, 1.0e-5) is False
    assert hf_monitor_ready(_decision(True), None, 1.0e-5) is False
    assert hf_monitor_ready(_decision(True), float("nan"), 1.0e-5) is False


def test_window_overlapping_the_ramp_is_not_ready():
    series = _flat(150, 200, 1.0)
    decision = lf_monitor_decision(
        series,
        series,
        ramp_end=150,
        n=200,
        tol_lmh=1.0e-4,
        tol_dp=1.0e-4,
    )
    assert decision["stable"] is False
    assert hf_monitor_ready(decision, 1.0e-8, 1.0e-5) is False


def test_iteration_loop_waits_for_continuity_then_stops():
    calls = []
    continuities = [1.0e-4, 1.0e-6]

    def iterate(count):
        calls.append(count)

    def read_decision():
        return _decision(True)

    def read_continuity():
        return continuities.pop(0)

    decision = run_hf_monitor_iterations(
        iterate,
        read_decision,
        read_continuity,
        continuity_max=1.0e-5,
        ramp_end=150,
        n=200,
        max_iterations=2000,
    )
    assert calls == [350, 1]
    assert decision["stop_reason"] == "hf_monitor_stable"
    assert decision["iterations_run"] == 351
    assert decision["continuity"] == pytest.approx(1.0e-6)


def test_resume_only_after_a_failed_gate_with_budget_left():
    assert should_resume_production(
        stop_reason="hf_monitor_stable",
        convergence_quality="FAIL",
        iterations_run=594,
        max_iterations=2000,
    )
    assert not should_resume_production(
        stop_reason="hf_monitor_stable",
        convergence_quality="PASS",
        iterations_run=594,
        max_iterations=2000,
    )
    assert not should_resume_production(
        stop_reason="hf_monitor_stable",
        convergence_quality="FAIL",
        iterations_run=2000,
        max_iterations=2000,
    )
    assert not should_resume_production(
        stop_reason="max_iter_reached",
        convergence_quality="FAIL",
        iterations_run=2000,
        max_iterations=2000,
    )
    with pytest.raises(ValueError, match="production gate"):
        should_resume_production(
            stop_reason="hf_monitor_stable",
            convergence_quality=None,
            iterations_run=594,
            max_iterations=2000,
        )


def test_resume_budget_and_iteration_total():
    assert resume_iteration_budget(594, 2000) == 1406
    assert iterations_after_resume(594, 595, 2000) == 2000
    assert iterations_after_resume(594, 1, 1406) == 2000
    assert iterations_after_resume(594, 595, 800) == 800
    assert iterations_after_resume(594, 1, 206) == 800
    with pytest.raises(ValueError, match="iterations_already_run"):
        resume_iteration_budget(2000, 2000)


def test_restarted_transcript_at_the_cap_is_max_iter_reached():
    assert (
        resume_stop_reason(
            "unknown_early_stop",
            iterations_already_run=594,
            final_iteration=1406,
            max_iterations_total=2000,
        )
        == "max_iter_reached"
    )
    assert (
        resume_stop_reason(
            "residual_converged",
            iterations_already_run=594,
            final_iteration=800,
            max_iterations_total=2000,
        )
        == "residual_converged"
    )
    assert (
        resume_stop_reason(
            "unknown_early_stop",
            iterations_already_run=594,
            final_iteration=700,
            max_iterations_total=2000,
        )
        == "unknown_early_stop"
    )


def test_phase_record_keeps_both_stops():
    first = phase_record("hf_monitor", "hf_monitor_stable", 594, "FAIL", ["lmh"])
    second = phase_record("production", "residual_converged", 800, "PASS")
    assert first["stop_reason"] == "hf_monitor_stable"
    assert second["convergence_quality"] == "PASS"
    assert second["failures"] == []


def test_stop_spec_requires_continuity_max():
    with pytest.raises(KeyError, match="continuity_max"):
        require_hf_monitor_stop({"tol_lmh": 1e-4, "tol_dp": 1e-4, "n": 200})
