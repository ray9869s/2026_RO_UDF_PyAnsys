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

    def test_campaign_1000_iterations_with_default_2000_target_falls_through_to_converged(
        self, inventory
    ):
        """F-02: 1000-iter campaign + default --max-iter 2000 + artifacts => CONVERGED."""
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is False
        assert result.convergence_status == inventory.CONVERGED

    def test_campaign_1000_iterations_with_matching_target_is_max_iter_reached(self, inventory):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=1000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is True
        assert result.convergence_status == inventory.MAX_ITER_REACHED

    def test_artifacts_with_only_non_solver_logs_are_converged(self, inventory):
        """F-02: cas/dat + summary_metrics_wide upgrade status without solver evidence."""
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
        assert result.convergence_status == inventory.CONVERGED

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

    def test_default_max_iter_cli_value_is_2000(self, inventory):
        args = inventory.parse_args([])
        assert args.max_iter == 2000
