"""Characterization tests for inventory convergence classification (F-02 / F-02c).

parse_logs() is the pure function behind detect_logs_and_convergence() in
case_inventory.py. These tests avoid filesystem scans and Ansys imports.

Max-iter default resolution is tested against injected common_solver_settings
(and optional tmp batch_config files), not against whatever live batch_config.py
contains for the current campaign — same fixture style as
tests/test_batch_layout_wiring.py (tmp_path-written configs) and helpers that
pass explicit settings into pure functions rather than reading campaign state.
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

    def test_missing_common_solver_settings_falls_back_to_2000_with_warning(
        self, inventory, capsys
    ):
        max_iter_target = inventory.max_iter_target_from_common_solver_settings(None)
        assert max_iter_target == 2000
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "common_solver_settings" in err
        assert "falling back to 2000" in err
        # A 1000-iteration transcript against that fallback is incomplete, not max-iter.
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=max_iter_target,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is False
        assert result.convergence_status == inventory.POSSIBLY_INCOMPLETE

    def test_present_common_solver_settings_uses_campaign_cap_without_warning(
        self, inventory, capsys
    ):
        max_iter_target = inventory.max_iter_target_from_common_solver_settings(
            {"max_iterations": 2000}
        )
        assert max_iter_target == 2000
        err = capsys.readouterr().err
        assert "WARNING" not in err

    def test_malformed_max_iterations_falls_back_with_warning(self, inventory, capsys):
        max_iter_target = inventory.max_iter_target_from_common_solver_settings(
            {"max_iterations": "not-an-int"}
        )
        assert max_iter_target == 2000
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "max_iterations" in err
        assert "falling back to 2000" in err

    def test_tmp_batch_config_without_common_settings_warns(
        self, inventory, tmp_path: Path, capsys
    ):
        # Fixture injection via tmp_path-written config (not live batch_config.py).
        cfg = tmp_path / "batch_config.py"
        cfg.write_text("mesh_batch_cases = []\n", encoding="utf-8")
        max_iter_target = inventory._default_max_iter_target(cfg)
        assert max_iter_target == 2000
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "common_solver_settings" in err

    def test_tmp_batch_config_with_common_settings_happy_path(
        self, inventory, tmp_path: Path, capsys
    ):
        cfg = tmp_path / "batch_config.py"
        cfg.write_text(
            "common_solver_settings = {'max_iterations': 1000}\n",
            encoding="utf-8",
        )
        max_iter_target = inventory._default_max_iter_target(cfg)
        assert max_iter_target == 1000
        err = capsys.readouterr().err
        assert "WARNING" not in err

    def test_campaign_1000_iterations_with_explicit_2000_target_is_possibly_incomplete(
        self, inventory
    ):
        result = inventory.parse_logs(
            analyses=[solver_analysis(inventory, "iteration: 1000\n")],
            max_iter_target=2000,
            has_case_data_pair=True,
            has_summary_metrics_wide=True,
        )
        assert result.hit_max_iter_target is False
        assert result.convergence_status == inventory.POSSIBLY_INCOMPLETE

    def test_campaign_1000_iterations_with_matching_target_is_max_iter_reached(
        self, inventory
    ):
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

    def test_default_max_iter_cli_uses_injected_helper(self, inventory, monkeypatch):
        # Do not bind CLI default to live batch_config shape; stub the loader.
        monkeypatch.setattr(
            inventory, "_default_max_iter_target", lambda: 2000
        )
        args = inventory.parse_args([])
        assert args.max_iter == 2000


class TestClassifyCaseLikelyComplete:
    def test_inventory_csv_schema_unchanged(self, inventory):
        fieldnames = inventory.CASE_INVENTORY_FIELDNAMES
        assert len(fieldnames) == 107
        assert fieldnames.count("likely_complete") == 1
        assert fieldnames[fieldnames.index("likely_complete_from_logs") + 1] == "likely_complete"
        assert "stop_reason" in fieldnames
        assert fieldnames[fieldnames.index("convergence_status") + 1] == "stop_reason"
        assert fieldnames == list(dict.fromkeys(fieldnames))

    def test_max_iter_with_full_artifacts_is_postprocessed_unconverged(self, inventory):
        # F-02c: was POSTPROCESSED_BASIC; now honest unconverged completeness.
        record = classify_record(inventory)
        assert record["likely_complete"] is False
        assert record["case_status"] == inventory.POSTPROCESSED_UNCONVERGED
        assert record["needs_solver_rerun"] is False

    def test_converged_with_artifacts_is_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.CONVERGED,
        )
        assert record["likely_complete"] is True
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC
        assert record["needs_solver_rerun"] is False

    def test_possibly_incomplete_with_artifacts_is_unknown_review(self, inventory):
        # F-02c: was POSTPROCESSED_BASIC; ambiguous solve is not auto-unconverged.
        record = classify_record(
            inventory,
            convergence_status=inventory.POSSIBLY_INCOMPLETE,
        )
        assert record["likely_complete"] is False
        assert record["case_status"] == inventory.UNKNOWN_REVIEW_REQUIRED
        assert record["needs_solver_rerun"] is False

    def test_likely_complete_from_logs_does_not_override_non_converged(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.POSSIBLY_INCOMPLETE,
            likely_complete_from_logs=True,
        )
        assert record["likely_complete"] is False

    def test_crash_reclassified_to_converged_is_likely_complete(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.CONVERGED,
            has_postprocessing_runtime_crash=True,
        )
        assert record["likely_complete"] is True
        assert record["case_status"] == inventory.POSTPROCESSED_BASIC
        assert record["needs_solver_rerun"] is False

    def test_missing_pair_is_missing_case_or_data(self, inventory):
        record = classify_record(
            inventory,
            has_case_data_pair=False,
            has_all_basic_contours=False,
            has_all_pyensight_contours=False,
            has_shear_contour=False,
            convergence_status=inventory.UNKNOWN_NO_LOG,
        )
        assert record["case_status"] == inventory.MISSING_CASE_OR_DATA
        assert record["needs_solver_rerun"] is False


class TestClassifyCaseSolveTrustTable:
    """Table-driven F-02c coverage: convergence_status x artifact completeness."""

    @pytest.mark.parametrize(
        "convergence_status,has_all_basic,hard_failure,expected_status,expected_needs_rerun,expected_likely",
        [
            ("CONVERGED", True, False, "POSTPROCESSED_BASIC", False, True),
            ("MAX_ITER_REACHED", True, False, "POSTPROCESSED_UNCONVERGED", False, False),
            ("FAILED_OR_DIVERGED", True, True, "NEEDS_SOLVER_RERUN", True, False),
            ("FAILED_OR_DIVERGED", True, False, "NEEDS_SOLVER_RERUN", True, False),
            ("POSSIBLY_INCOMPLETE", True, False, "UNKNOWN_REVIEW_REQUIRED", False, False),
            ("UNKNOWN_NO_LOG", True, False, "UNKNOWN_REVIEW_REQUIRED", False, False),
            ("UNKNOWN_UNPARSED", True, False, "UNKNOWN_REVIEW_REQUIRED", False, False),
            # Incomplete artifacts: transitional branches unchanged in spirit.
            ("MAX_ITER_REACHED", False, False, "NEEDS_SOLVER_RERUN", True, False),
            ("CONVERGED", False, False, "READY_FOR_POSTPROCESSING", False, True),
            ("FAILED_OR_DIVERGED", False, True, "NEEDS_SOLVER_RERUN", True, False),
        ],
    )
    def test_classify_matrix(
        self,
        inventory,
        convergence_status,
        has_all_basic,
        hard_failure,
        expected_status,
        expected_needs_rerun,
        expected_likely,
    ):
        record = classify_record(
            inventory,
            convergence_status=getattr(inventory, convergence_status),
            has_all_basic_contours=has_all_basic,
            has_all_pyensight_contours=has_all_basic,
            has_shear_contour=has_all_basic,
            hard_solver_failure_detected=hard_failure,
        )
        assert record["case_status"] == getattr(inventory, expected_status)
        assert record["needs_solver_rerun"] is expected_needs_rerun
        assert record["likely_complete"] is expected_likely

    def test_needs_shear_branch_unchanged_for_max_iter(self, inventory):
        record = classify_record(
            inventory,
            convergence_status=inventory.MAX_ITER_REACHED,
            has_all_basic_contours=False,
            has_all_pyensight_contours=True,
            has_shear_contour=False,
        )
        assert record["case_status"] == inventory.NEEDS_SHEAR_POSTPROCESSING
        assert record["needs_solver_rerun"] is False

    def test_hard_failure_forces_needs_solver_status_even_if_convergence_says_converged(
        self, inventory
    ):
        # case_status demotion is unconditional on hard_failure; queue membership
        # still follows the approved formula (solver_status_needs_rerun ∩ exclusions).
        record = classify_record(
            inventory,
            convergence_status=inventory.CONVERGED,
            hard_solver_failure_detected=True,
        )
        assert record["case_status"] == inventory.NEEDS_SOLVER_RERUN
        assert record["needs_solver_rerun"] is False
        assert record["likely_complete"] is True
