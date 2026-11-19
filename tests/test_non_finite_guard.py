"""C1-01: NaN/inf in gate inputs or monitor values never pass."""

from __future__ import annotations

import math

import pytest

from ro.convergence_quality import evaluate_convergence_quality
from ro.solver_common import fluent_report_relative_change, fluent_report_relative_window_met

GOOD = {"lmh_relative_difference": 1e-4, "mass_balance_relative_error": 1e-5}


def test_finite_inputs_still_pass():
    assert evaluate_convergence_quality(GOOD, continuity_final=1e-5)["convergence_quality"] == "PASS"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf", " -Infinity "])
@pytest.mark.parametrize("field", ["lmh_relative_difference", "mass_balance_relative_error", "continuity_final"])
def test_non_finite_gate_input_fails(field, bad):
    metrics = dict(GOOD)
    continuity = 1e-5
    if field == "continuity_final":
        continuity = bad
    else:
        metrics[field] = bad
    result = evaluate_convergence_quality(metrics, continuity_final=continuity)
    assert result["convergence_quality"] == "FAIL"
    assert field in result["failures"]
    assert result["checks"][field]["non_finite"] is True


def test_non_finite_continuity_in_metrics_fails_when_argument_missing():
    result = evaluate_convergence_quality({**GOOD, "continuity_final": "nan"})
    assert result["convergence_quality"] == "FAIL"


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 0.0])
def test_non_finite_gate_limit_is_refused(limit):
    with pytest.raises(ValueError, match="finite positive"):
        evaluate_convergence_quality(GOOD, continuity_final=1e-5, lmh_rel_abs_max=limit)


def test_nan_inside_a_monitor_window_is_not_evaluable():
    window = [1.0, float("nan"), 1.0, 1.0]
    assert fluent_report_relative_change(window) is None
    assert fluent_report_relative_window_met(window, 1e-4) is False
    assert fluent_report_relative_change([1.0, math.inf, 1.0]) is None
    assert fluent_report_relative_change([1.0, 1.0, 1.0]) == 0.0
