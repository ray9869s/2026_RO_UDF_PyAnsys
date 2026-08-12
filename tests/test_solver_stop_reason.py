"""Unit tests for QoI/residual stop-reason helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import load_case_inventory, load_solver_common


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


@pytest.fixture(scope="module")
def inventory():
    return load_case_inventory()


def test_fluent_report_relative_window_met_matches_ug_formula(common):
    # Flat history: max relative excursion is 0.
    assert common.fluent_report_relative_window_met([10.0] * 101, 1e-3) is True
    # One early outlier outside 0.1% of current.
    values = [10.0] * 100 + [10.0]
    values[0] = 10.02
    assert common.fluent_report_relative_window_met(values, 1e-3) is False
    values[0] = 10.005
    assert common.fluent_report_relative_window_met(values, 1e-3) is True


def test_classify_solver_stop_reason_priority(common):
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            qoi_check_enabled=False,
            qoi_report_evaluable=False,
            final_iteration=None,
            max_iterations=2000,
            calculation_ran=False,
        )
        == common.STOP_REASON_NOT_RUN
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=True,
            residuals_met=True,
            qoi_met=True,
            qoi_check_enabled=True,
            qoi_report_evaluable=True,
            final_iteration=100,
            max_iterations=2000,
        )
        == common.STOP_REASON_DIVERGED
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=True,
            qoi_met=True,
            qoi_check_enabled=True,
            qoi_report_evaluable=True,
            final_iteration=400,
            max_iterations=2000,
        )
        == common.STOP_REASON_RESIDUAL_CONVERGED
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=True,
            qoi_check_enabled=True,
            qoi_report_evaluable=True,
            final_iteration=500,
            max_iterations=2000,
        )
        == common.STOP_REASON_QOI_CONVERGED
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            qoi_check_enabled=True,
            qoi_report_evaluable=False,
            final_iteration=900,
            max_iterations=2000,
        )
        == common.STOP_REASON_QOI_REPORT_UNAVAILABLE
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            qoi_check_enabled=True,
            qoi_report_evaluable=True,
            final_iteration=900,
            max_iterations=2000,
        )
        == common.STOP_REASON_UNKNOWN_EARLY_STOP
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            qoi_check_enabled=True,
            qoi_report_evaluable=True,
            final_iteration=2000,
            max_iterations=2000,
        )
        == common.STOP_REASON_MAX_ITER_REACHED
    )


def test_stop_reason_enum_is_complete(common):
    assert set(common.STOP_REASON_VALUES) == {
        "residual_converged",
        "qoi_converged",
        "max_iter_reached",
        "diverged",
        "unknown_early_stop",
        "qoi_report_unavailable",
        "not_run",
    }


def test_parse_fluent_report_file_series(common):
    text = (
        '"lmh_udm_avg_rfile"\n'
        '"Iteration" "lmh_udm_avg"\n'
        "1 12.5\n"
        "2 12.6\n"
        "3 12.7\n"
    )
    rows = common.parse_fluent_report_file_series(text)
    assert rows == [(1, 12.5), (2, 12.6), (3, 12.7)]


def test_inventory_parses_stop_reason_marker(inventory):
    text = (
        "iter continuity ...\n"
        "solution is converged\n"
        "SOLVER_STOP_REASON=qoi_converged\n"
    )
    analysis = inventory.LogFileAnalysis(
        path=Path("solver_log_case.txt"),
        role=inventory.ROLE_SOLVER_RUN,
        text=text,
    )
    parsed = inventory.parse_logs(
        [analysis],
        max_iter_target=2000,
        has_case_data_pair=True,
        has_summary_metrics_wide=False,
    )
    assert parsed.stop_reason == "qoi_converged"
    assert parsed.convergence_status == inventory.CONVERGED


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [
        ("unknown_early_stop", "POSSIBLY_INCOMPLETE"),
        ("qoi_report_unavailable", "POSSIBLY_INCOMPLETE"),
        ("not_run", "POSSIBLY_INCOMPLETE"),
    ],
)
def test_inventory_maps_ambiguous_stop_reasons(inventory, reason, expected_status):
    analysis = inventory.LogFileAnalysis(
        path=Path("solver_log_case.txt"),
        role=inventory.ROLE_SOLVER_RUN,
        text=f"SOLVER_STOP_REASON={reason}\n",
    )
    parsed = inventory.parse_logs(
        [analysis],
        max_iter_target=2000,
        has_case_data_pair=True,
        has_summary_metrics_wide=False,
    )
    assert parsed.stop_reason == reason
    assert parsed.convergence_status == getattr(inventory, expected_status)
    assert parsed.likely_complete_from_logs is False
