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

import os
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


# Independent CSV column contract for case_inventory.csv. Do not build this
# from CASE_INVENTORY_FIELDNAMES — that would make the test self-validating.
EXPECTED_CASE_INVENTORY_FIELDNAMES = (
    "geo_name",
    "case_name",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "u_target_ms",
    "p_gauge_pa",
    "case_dir",
    "post_dir",
    "reports_dir",
    "contours_dir",
    "cas_files",
    "dat_files",
    "final_cas_file",
    "final_dat_file",
    "has_final_cas",
    "has_final_dat",
    "has_case_data_pair",
    "cas_file_count",
    "dat_file_count",
    "log_files",
    "transcript_files",
    "latest_log_file",
    "log_file_count",
    "transcript_file_count",
    "log_files_by_role",
    "log_role_by_file",
    "solver_log_files",
    "postprocessing_log_files",
    "report_log_files",
    "meshing_log_files",
    "udf_compile_log_files",
    "unknown_log_files",
    "convergence_status",
    "stop_reason",
    "convergence_quality",
    "needs_longer_solve",
    "convergence_quality_failures",
    "convergence_quality_warnings",
    "continuity_final",
    "lmh_relative_difference",
    "mass_balance_relative_error",
    "pp_pressure_drop_rel_spread_window",
    "pp_pressure_drop_rel_spread_cells_4_7",
    "pp_pressure_drop_rel_spread_note",
    "max_iteration_detected",
    "max_iter_target",
    "hit_max_iter_target",
    "likely_complete_from_logs",
    "likely_complete",
    "convergence_evidence",
    "failure_evidence",
    "warning_evidence",
    "completion_evidence",
    "max_iter_evidence",
    "iteration_notes",
    "log_parse_errors",
    "report_expression_warning_count",
    "report_expression_warning_files",
    "report_expression_warning_evidence",
    "postprocessing_graphics_error_files",
    "postprocessing_graphics_error_evidence",
    "meshing_error_files",
    "meshing_error_evidence",
    "udf_compile_error_files",
    "udf_compile_error_evidence",
    "launch_error_files",
    "launch_error_evidence",
    "has_summary_metrics_wide",
    "summary_metrics_wide_file",
    "report_csv_count",
    "report_files",
    "summary_metrics_read_error",
    "c_bulk_center_whole_domain_area_avg",
    "lmh",
    "cp",
    "pressure_drop",
    "mass_balance",
    "wall_shear_avg",
    "wall_shear_rate_avg",
    "has_cp_contour",
    "has_water_flux_contour",
    "has_lmh_contour",
    "has_salt_flux_contour",
    "has_all_pyensight_contours",
    "has_shear_contour",
    "has_all_basic_contours",
    "contour_status_file",
    "shear_status_file",
    "colorbar_metadata_files",
    "contour_export_overall_status",
    "contour_success_count",
    "contour_failed_count",
    "shear_export_status",
    "shear_native_status",
    "shear_fallback_status",
    "shear_derived_variable_mode",
    "shear_legend_mode",
    "shear_colorbar_metadata_written",
    "postprocessing_status",
    "report_status",
    "case_status",
    "has_postprocessing_graphics_errors",
    "has_report_expression_warnings",
    "has_udf_compile_errors",
    "has_meshing_errors",
    "has_launch_errors",
    "hard_solver_failure_detected",
    "max_iter_only",
    "has_postprocessing_runtime_crash",
    "postprocessing_runtime_crash_evidence",
    "failed_postprocessing_stage",
    "inventory_confidence",
    "failure_evidence_short",
    "needs_solver_rerun",
    "needs_report_extraction",
    "needs_basic_contours",
    "needs_shear_contour",
    "needs_shear_postprocessing",
    "needs_manual_review",
    "ready_for_batch_contours",
    "suggested_next_action",
)


class TestClassifyCaseLikelyComplete:
    def test_inventory_csv_schema_unchanged(self, inventory):
        """CSV column contract is the sequence: names and order.

        Exact set is also asserted, plus uniqueness. A matching set in a
        different order is a contract break because writers emit this order.
        EXPECTED_CASE_INVENTORY_FIELDNAMES is listed independently of
        CASE_INVENTORY_FIELDNAMES so a rename, add, or drop in the production
        constant fails this test.
        """
        fieldnames = list(inventory.CASE_INVENTORY_FIELDNAMES)
        expected = list(EXPECTED_CASE_INVENTORY_FIELDNAMES)
        assert len(expected) == len(set(expected))
        assert len(fieldnames) == len(set(fieldnames))
        assert set(fieldnames) == set(expected)
        assert fieldnames == expected

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


class TestManifestStopReasonClassification:
    @pytest.mark.parametrize(
        ("stop_reason", "expected"),
        [
            ("residual_converged", "CONVERGED"),
            ("qoi_converged", "CONVERGED"),
            ("max_iter_reached", "MAX_ITER_REACHED"),
            ("diverged", "FAILED_OR_DIVERGED"),
            ("unknown_early_stop", "POSSIBLY_INCOMPLETE"),
            ("qoi_report_unavailable", "POSSIBLY_INCOMPLETE"),
            ("iteration_unknown", "POSSIBLY_INCOMPLETE"),
            ("not_run", "POSSIBLY_INCOMPLETE"),
            ("RUNNING", "POSSIBLY_INCOMPLETE"),
        ],
    )
    def test_mapping(self, inventory, stop_reason, expected):
        status = inventory.convergence_status_from_stop_reason(stop_reason)
        assert status == getattr(inventory, expected)

    def test_unknown_stop_reason_is_loud(self, inventory):
        with pytest.raises(ValueError, match="Unsupported run manifest stop_reason"):
            inventory.convergence_status_from_stop_reason("CONVERGED")

    def test_manifest_stop_reason_overrides_log_parse(self, inventory, tmp_path):
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        (case_dir / "solver_log.txt").write_text(
            "Solution is converged.\n", encoding="utf-8"
        )
        record = {
            "geo_name": "D2450_a45",
            "case_name": "u0p2_p6M",
            "stop_reason": "max_iter_reached",
            "_case_dir_path": case_dir,
            "has_case_data_pair": True,
            "has_summary_metrics_wide": False,
        }
        inventory.detect_logs_and_convergence(record, 2000)
        assert record["stop_reason"] == "max_iter_reached"
        assert record["convergence_status"] == inventory.MAX_ITER_REACHED
        assert record["max_iter_only"] is True


class TestPostprocessingNodeErrorLogResidue:
    def test_fluent_node_error_log_is_not_solver_run_role(self, inventory):
        path = Path("/tmp/fluent-999999-error.log")
        assert inventory.is_fluent_node_error_log(path)
        assert not inventory.is_fluent_solver_transcript(path)
        role = inventory.classify_log_role(
            path,
            "Error [node 999999] [time 8/31/26 18:47:33] Abnormal Exit!\n",
        )
        assert role == inventory.ROLE_POSTPROCESSING_GRAPHICS

    def test_postdated_node_error_log_does_not_force_solver_rerun(
        self, inventory, tmp_path
    ):
        """u0p1/u0p3 residue: Abnormal Exit after a finished qoi_converged solve."""
        case_dir = tmp_path / "u0p1_p6M"
        case_dir.mkdir()
        trn = case_dir / "fluent-20260827-170215-92588.trn"
        trn.write_text(
            "iterate\nresidual\ncontinuity\nx-velocity\n"
            "Solution is converged.\nwriting final.cas\nwriting final.dat\n",
            encoding="utf-8",
        )
        err = case_dir / "fluent-999999-error.log"
        err.write_text(
            "Error [node 999999] [time 8/31/26 18:47:33] Abnormal Exit!\n",
            encoding="utf-8",
        )
        solver_mtime = 1_724_760_000.0  # ordering matters, not calendar
        post_mtime = solver_mtime + 4 * 86400.0
        os.utime(trn, (solver_mtime, solver_mtime))
        os.utime(err, (post_mtime, post_mtime))

        record = {
            "geo_name": "D2450_a45",
            "case_name": "u0p1_p6M",
            "stop_reason": "qoi_converged",
            "_case_dir_path": case_dir,
            "has_case_data_pair": True,
            "has_summary_metrics_wide": False,
            "has_all_basic_contours": False,
            "has_all_pyensight_contours": False,
            "has_shear_contour": False,
            "final_cas_file": str(case_dir / "final.cas.h5"),
            "final_dat_file": str(case_dir / "final.dat.h5"),
        }
        inventory.detect_logs_and_convergence(record, 2000)
        inventory.classify_case(record)

        assert record["convergence_status"] == inventory.CONVERGED
        assert record["hard_solver_failure_detected"] is False
        assert record["case_status"] == inventory.NEEDS_REPORT_EXTRACTION
        assert record["needs_report_extraction"] is True
        assert "report extraction" in record["suggested_next_action"].lower()
        assert record["case_status"] != inventory.NEEDS_SOLVER_RERUN
