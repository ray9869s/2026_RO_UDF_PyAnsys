"""Unit tests for QoI/residual stop-reason helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import load_case_inventory, load_solver_code, load_solver_common


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
            final_iteration=2000,
            max_iterations=2000,
        )
        == common.STOP_REASON_MAX_ITER_REACHED
    )
    assert (
        common.classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            final_iteration=None,
            max_iterations=2000,
        )
        == common.STOP_REASON_ITERATION_UNKNOWN
    )


def test_stop_reason_enum_is_complete(common):
    assert set(common.STOP_REASON_VALUES) == {
        "residual_converged",
        "qoi_converged",
        "max_iter_reached",
        "diverged",
        "unknown_early_stop",
        "qoi_report_unavailable",
        "iteration_unknown",
        "not_run",
        "stop_reason_determination_failed",
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
        ("iteration_unknown", "POSSIBLY_INCOMPLETE"),
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


QOI_MARKER_LINE = "!  301 report definition solution is converged"
RESIDUAL_MARKER_LINE = "!  379 solution is converged"

RESIDUAL_TABLE = """\
iter  continuity  x-velocity  y-velocity  z-velocity  nacl
  100  1.0e-5      1.0e-5      1.0e-5      1.0e-5      1.0e-5
  200  4.7851e-06  1.0e-6      1.0e-6      1.0e-6      9.8e-08
"""


def test_parse_fluent_convergence_marker_prefers_qoi_phrase(common):
    text = (
        "iteration 300:  continuity 5.0054e-06   nacl 1.0e-07\n"
        f"{QOI_MARKER_LINE}\n"
    )
    reason, iteration = common.parse_fluent_convergence_marker(text)
    assert reason == "qoi_converged"
    assert iteration == 301


def test_parse_fluent_convergence_marker_residual_stop(common):
    text = f"{RESIDUAL_MARKER_LINE}\n"
    reason, iteration = common.parse_fluent_convergence_marker(text)
    assert reason == "residual_converged"
    assert iteration == 379


def test_parse_fluent_convergence_marker_does_not_treat_qoi_as_residual(common):
    # The QoI phrase contains "solution is converged" as a substring.
    reason, iteration = common.parse_fluent_convergence_marker(QOI_MARKER_LINE)
    assert reason == "qoi_converged"
    assert iteration == 301
    assert common.parse_fluent_convergence_marker("no such event") == (None, None)


def test_parse_last_residual_iteration_from_table_and_prefix(common):
    assert (
        common.parse_last_residual_iteration_from_transcript_text(RESIDUAL_TABLE)
        == 200
    )
    prefix_text = "iteration 301:  continuity 4.7851e-06   nacl 9.8107e-08\n"
    assert (
        common.parse_last_residual_iteration_from_transcript_text(prefix_text)
        == 301
    )


def test_determine_stop_reason_from_qoi_transcript(tmp_path, capsys):
    solver_code = load_solver_code("solver_stop_reason_qoi")
    (tmp_path / "fluent-solve.trn").write_text(
        RESIDUAL_TABLE + QOI_MARKER_LINE + "\n",
        encoding="utf-8",
    )
    reason = solver_code.determine_and_print_stop_reason(
        case_dir=tmp_path,
        solver_log_path=tmp_path / "solver_log_case.txt",
        max_iterations=2000,
        qoi_enabled=True,
        qoi_report_file_paths=[],
        qoi_previous_values_to_consider=100,
        qoi_stop_criterion=1e-3,
        diverged=False,
    )
    assert reason == "qoi_converged"
    out = capsys.readouterr().out
    assert "SOLVER_STOP_REASON=qoi_converged" in out
    assert "final_iteration=301" in out
    assert "discrepancy=" in out
    assert "transcript qoi_converged but report-file window check" in out


def test_determine_stop_reason_qoi_disabled_does_not_claim_unevaluable(tmp_path, capsys):
    solver_code = load_solver_code("solver_stop_reason_qoi_off")
    (tmp_path / "fluent-solve.trn").write_text(
        RESIDUAL_TABLE.replace("200", "2000"),
        encoding="utf-8",
    )
    reason = solver_code.determine_and_print_stop_reason(
        case_dir=tmp_path,
        solver_log_path=tmp_path / "solver_log_case.txt",
        max_iterations=2000,
        qoi_enabled=False,
        qoi_report_file_paths=[tmp_path / "lmh_udm_avg.out"],
        qoi_previous_values_to_consider=100,
        qoi_stop_criterion=1e-3,
        diverged=False,
    )
    assert reason == "max_iter_reached"
    out = capsys.readouterr().out
    assert "SOLVER_STOP_REASON=max_iter_reached" in out
    assert "qoi_check_enabled=False" in out
    assert "qoi_report_check=skipped (enable_qoi_convergence_stop=False)" in out
    assert "qoi_report_evaluable=" not in out


def test_determine_stop_reason_unknown_early_stop_uses_table_iteration(tmp_path, capsys):
    solver_code = load_solver_code("solver_stop_reason_early")
    (tmp_path / "fluent-solve.trn").write_text(RESIDUAL_TABLE, encoding="utf-8")
    reason = solver_code.determine_and_print_stop_reason(
        case_dir=tmp_path,
        solver_log_path=tmp_path / "solver_log_case.txt",
        max_iterations=2000,
        qoi_enabled=False,
        qoi_report_file_paths=[],
        qoi_previous_values_to_consider=100,
        qoi_stop_criterion=1e-3,
        diverged=False,
    )
    assert reason == "unknown_early_stop"
    out = capsys.readouterr().out
    assert "final_iteration=200" in out
    assert "table_iteration=200" in out


def test_determine_stop_reason_iteration_unknown_without_transcript(tmp_path, capsys):
    solver_code = load_solver_code("solver_stop_reason_unknown")
    reason = solver_code.determine_and_print_stop_reason(
        case_dir=tmp_path,
        solver_log_path=tmp_path / "solver_log_case.txt",
        max_iterations=2000,
        qoi_enabled=False,
        qoi_report_file_paths=[],
        qoi_previous_values_to_consider=100,
        qoi_stop_criterion=1e-3,
        diverged=False,
    )
    assert reason == "iteration_unknown"
    out = capsys.readouterr().out
    assert "SOLVER_STOP_REASON=iteration_unknown" in out
    assert "final_iteration=None" in out


def test_determine_stop_reason_prefers_newest_trn_without_falling_back_to_old_marker(
    tmp_path,
):
    solver_code = load_solver_code("solver_stop_reason_newest")
    older = tmp_path / "fluent-old.trn"
    newer = tmp_path / "fluent-new.trn"
    older.write_text(QOI_MARKER_LINE + "\n", encoding="utf-8")
    newer.write_text(RESIDUAL_TABLE.replace("200", "2000"), encoding="utf-8")
    older_mtime = older.stat().st_mtime
    os.utime(newer, (older_mtime + 10, older_mtime + 10))
    reason = solver_code.determine_and_print_stop_reason(
        case_dir=tmp_path,
        solver_log_path=tmp_path / "solver_log_case.txt",
        max_iterations=2000,
        qoi_enabled=False,
        qoi_report_file_paths=[],
        qoi_previous_values_to_consider=100,
        qoi_stop_criterion=1e-3,
        diverged=False,
    )
    assert reason == "max_iter_reached"


def test_qoi_met_from_report_files_requires_both(tmp_path):
    solver_code = load_solver_code("solver_stop_reason_files")
    lmh = tmp_path / "lmh_udm_avg.out"
    dp = tmp_path / "pressure_drop_spacer.out"
    header = '"rfile"\n"Iteration" "value"\n'
    lmh.write_text(header + "\n".join(f"{i} 10.0" for i in range(1, 103)), encoding="utf-8")
    dp.write_text(header + "\n".join(f"{i} 2000.0" for i in range(1, 103)), encoding="utf-8")
    evaluable, met, final_iteration = solver_code.qoi_met_from_report_files(
        [lmh, dp],
        100,
        1e-3,
    )
    assert evaluable is True
    assert met is True
    assert final_iteration == 102

    dp.write_text(
        header + "\n".join(f"{i} {2000.0 + i}" for i in range(1, 103)),
        encoding="utf-8",
    )
    evaluable, met, _ = solver_code.qoi_met_from_report_files([lmh, dp], 100, 1e-3)
    assert evaluable is True
    assert met is False

