"""Characterization tests for inventory convergence classification (F-02).

parse_logs() is the pure function behind detect_logs_and_convergence() in
00_case_inventory.py. These tests avoid filesystem scans and Ansys imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import load_case_inventory

SOLVER_LOG = Path("/tmp/fake_solver_run.log")
REPORT_LOG = Path("/tmp/fake_report_extract.log")


@pytest.fixture(scope="module")
def inventory():
    return load_case_inventory()


def solver_analysis(inventory, text: str):
    return inventory.LogFileAnalysis(
        path=SOLVER_LOG,
        role=inventory.ROLE_SOLVER_RUN,
        text=text,
    )


def classify_record(inventory, **overrides):
    """Minimal inventory row for classify_case() characterization tests."""
    record = {
        "convergence_status": inventory.MAX_ITER_REACHED,
        "has_case_data_pair": True,
        "has_summary_metrics_wide": True,
        "has_all_basic_contours": True,
        "has_all_pyensight_contours": True,
        "has_shear_contour": True,
        "hard_solver_failure_detected": False,
        "likely_complete_from_logs": False,
        "contour_failed_count": 0,
        "contour_export_overall_status": "",
        "shear_export_status": "",
    }
    record.update(overrides)
    inventory.classify_case(record)
    return record


class TestInventoryConvergenceClassification:
    def test_explicit_convergence_phrase(self, inventory):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "Solution is converged.")],
            max_iter_target=2000,
            has_case_data_pair=False,
            has_summary_metrics_wide=False,
        )
        assert result.convergence_status == inventory.CONVERGED

    def test_max_iter_phrase_without_convergence(self, inventory):
        result = inventory.parse_logs(
            analyses=[
                solver_analysis(
                    inventory,
                    "Stopping: maximum number of iterations reached.\niteration: 1000",
                )
            ],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.convergence_status == inventory.MAX_ITER_REACHED

    def test_campaign_1000_iterations_with_batch_config_default_is_max_iter_reached(self, inventory):
        max_iter_target = inventory._default_max_iter_target()
        assert max_iter_target == 1000
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=max_iter_target,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is True
        assert result.convergence_status == inventory.MAX_ITER_REACHED

    def test_campaign_1000_iterations_with_explicit_2000_target_is_possibly_incomplete(self, inventory):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is False
        assert result.convergence_status == inventory.POSSIBLY_INCOMPLETE

    def test_campaign_1000_iterations_with_matching_target_is_max_iter_reached(self, inventory):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=1000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is True
        assert result.convergence_status == inventory.MAX_ITER_REACHED

    def test_artifacts_with_only_non_solver_logs_are_possibly_incomplete(self, inventory):
        result = inventory.parse_logs(
            analyses=[
                inventory.LogFileAnalysis(
                    path=REPORT_LOG,
                    role=inventory.ROLE_REPORT_EXTRACTION,
                    text="Wrote summary_metrics_wide.csv",
                )
            ],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.convergence_status == inventory.POSSIBLY_INCOMPLETE

    def test_case_data_without_summary_stays_incomplete(self, inventory):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 500\n")],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=False,
        )
        assert result.convergence_status == inventory.POSSIBLY_INCOMPLETE

    def test_no_logs_no_artifacts_is_unknown(self, inventory):
        result = inventory.parse_logs(
            analyses=[],
            max_iter_target=2000,
            has_case_data_pair=False,
            has_summary_metrics_wide=False,
        )
        assert result.convergence_status == inventory.UNKNOWN_NO_LOG

    def test_default_max_iter_cli_matches_batch_config(self, inventory):
        args = inventory.parse_args([])
        assert args.max_iter == inventory._default_max_iter_target()
        assert args.max_iter == 1000


class TestClassifyCaseLikelyComplete:
    def test_inventory_csv_schema_unchanged(self, inventory):
        fieldnames = inventory.CASE_INVENTORY_FIELDNAMES
        assert len(fieldnames) == 106
        assert fieldnames.count("likely_complete") == 1
        assert fieldnames[fieldnames.index("likely_complete_from_logs") + 1] == "likely_complete"
        assert fieldnames == list(dict.fromkeys(fieldnames))

    def test_max_iter_with_full_artifacts_not_likely_complete_queue_unchanged(self, inventory):
        record = classify_record(inventory)
        assert record["likely_complete"] is False
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC
        assert record["needs_solver_rerun"] is False

    def test_converged_with_artifacts_is_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.CONVERGED,
        )
        assert record["likely_complete"] is True
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC

    def test_possibly_incomplete_with_artifacts_not_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.POSSIBLY_INCOMPLETE,
        )
        assert record["likely_complete"] is False
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC

    def test_likely_complete_from_logs_does_not_override_non_converged(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.POSSIBLY_INCOMPLETE,
            likely_complete_from_logs=True,
        )
        assert record["likely_complete"] is False

    def test_failed_or_diverged_with_artifacts_not_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.FAILED_OR_DIVERGED,
            hard_solver_failure_detected=True,
        )
        assert record["likely_complete"] is False
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC

    def test_crash_reclassified_to_converged_is_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.CONVERGED,
            has_postprocessing_runtime_crash=True,
        )
        assert record["likely_complete"] is True
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC
