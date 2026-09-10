"""Tests for post-hoc convergence quality gate."""

from __future__ import annotations

import pytest

from helpers import load_case_inventory
from ro.convergence_quality import (
    PRESSURE_DROP_REL_SPREAD_NOTE,
    PRESSURE_DROP_REL_SPREAD_WARNING,
    QUALITY_FAIL,
    QUALITY_PASS,
    QUALITY_UNKNOWN,
    evaluate_convergence_quality,
    manifest_quality_payload,
    metrics_from_summary_row,
    relative_spread,
)
from ro.domain_layout import (
    BUFFER_LENGTH_IN_M,
    BUFFER_LENGTH_OUT_M,
    CURRENT_EVALUATION_WINDOW,
    CURRENT_LAYOUT,
    DomainLayout,
    EvaluationWindow,
)


D2450_WINDOW_CELLS = CURRENT_EVALUATION_WINDOW.evaluation_cell_numbers(CURRENT_LAYOUT)

# Study values (D2450_a45, p=6 MPa). Cell dP spreads are diagnostics only.
# Continuity column 4–7 is ~2.58% at u=0.2 and ~14.7% at u=0.3 (converged).
U0P2_PASS = {
    "lmh_relative_difference": 1.33e-4,
    "mass_balance_relative_error": 1.0e-4,
    "pp_pressure_drop_cell_4": 117.44,
    "pp_pressure_drop_cell_5": 114.47,
    "pp_pressure_drop_cell_6": 114.47,
    "pp_pressure_drop_cell_7": 115.92,
    "pp_pressure_drop_cell_8": 116.54,
}
U0P3_FAIL_301 = {
    "lmh_relative_difference": -2.92e-1,
    "mass_balance_relative_error": 2.264e-1,
    "pp_pressure_drop_cell_4": 219.07,
    "pp_pressure_drop_cell_5": 201.94,
    "pp_pressure_drop_cell_6": 234.75,
    "pp_pressure_drop_cell_7": 233.22,
    "pp_pressure_drop_cell_8": 237.56,
}
U0P3_PASS_CONV2000 = {
    "lmh_relative_difference": 1.30e-4,
    "mass_balance_relative_error": -4.850e-6,
    "pp_pressure_drop_cell_4": 219.05,
    "pp_pressure_drop_cell_5": 201.93,
    "pp_pressure_drop_cell_6": 234.70,
    "pp_pressure_drop_cell_7": 233.67,
    "pp_pressure_drop_cell_8": 237.56,
}


def _evaluate(metrics, continuity_final, cells=D2450_WINDOW_CELLS):
    return evaluate_convergence_quality(
        metrics,
        continuity_final=continuity_final,
        evaluation_cell_numbers=cells,
    )


class TestEvaluateConvergenceQuality:
    def test_u0p2_passes_despite_dP_spread(self):
        result = _evaluate(U0P2_PASS, continuity_final=4e-6)
        assert result["convergence_quality"] == QUALITY_PASS
        assert result["needs_longer_solve"] is False
        assert result["failures"] == []
        assert result["warnings"] == [PRESSURE_DROP_REL_SPREAD_WARNING]
        assert PRESSURE_DROP_REL_SPREAD_WARNING == "pp_pressure_drop_rel_spread_window"
        assert result["pp_pressure_drop_rel_spread_note"] == PRESSURE_DROP_REL_SPREAD_NOTE
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.0258, rel=1e-2
        )
        assert result["pp_pressure_drop_rel_spread_window"] == pytest.approx(
            relative_spread([114.47, 114.47, 115.92, 116.54])
        )
        assert (
            result["pp_pressure_drop_rel_spread_window"]
            != result["pp_pressure_drop_rel_spread_cells_4_7"]
        )

    def test_u0p3_301_fails_on_lmh_and_mass_balance_not_spread(self):
        result = _evaluate(U0P3_FAIL_301, continuity_final=6.4e-3)
        assert result["convergence_quality"] == QUALITY_FAIL
        assert result["needs_longer_solve"] is True
        assert "lmh_relative_difference" in result["failures"]
        assert "mass_balance_relative_error" in result["failures"]
        assert "continuity_final" in result["failures"]
        assert "pp_pressure_drop_rel_spread_window" not in result["failures"]
        assert "pp_pressure_drop_rel_spread_cells_4_7" not in result["failures"]
        assert result["warnings"] == [PRESSURE_DROP_REL_SPREAD_WARNING]
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.1476, rel=1e-2
        )

    def test_u0p3_conv2000_passes_with_same_large_dP_spread(self):
        result = _evaluate(U0P3_PASS_CONV2000, continuity_final=1e-6)
        assert result["convergence_quality"] == QUALITY_PASS
        assert result["needs_longer_solve"] is False
        assert result["warnings"] == [PRESSURE_DROP_REL_SPREAD_WARNING]
        assert result["pp_pressure_drop_rel_spread_note"] == PRESSURE_DROP_REL_SPREAD_NOTE
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.1474, rel=1e-2
        )

    def test_window_spread_is_evaluation_cells_not_hardcoded_4_7(self):
        assert D2450_WINDOW_CELLS == [5, 6, 7, 8]
        result = _evaluate(U0P2_PASS, continuity_final=4e-6)
        window_values = [U0P2_PASS[f"pp_pressure_drop_cell_{c}"] for c in (5, 6, 7, 8)]
        legacy_values = [U0P2_PASS[f"pp_pressure_drop_cell_{c}"] for c in (4, 5, 6, 7)]
        assert result["pp_pressure_drop_rel_spread_window"] == pytest.approx(
            relative_spread(window_values)
        )
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            relative_spread(legacy_values)
        )

    def test_d0817_window_is_cells_5_through_22(self):
        layout = DomainLayout(
            n_buffer_in=1,
            n_active=21,
            n_buffer_out=2,
            cell_length_x_m=0.001155,
            buffer_length_in_m=BUFFER_LENGTH_IN_M,
            buffer_length_out_m=BUFFER_LENGTH_OUT_M,
        )
        cells = EvaluationWindow(3, 0).evaluation_cell_numbers(layout)
        assert cells == list(range(5, 23))
        metrics = {
            "lmh_relative_difference": 1e-4,
            "mass_balance_relative_error": 1e-4,
            "pp_pressure_drop_cell_4": 200.0,
        }
        for cell in cells:
            metrics[f"pp_pressure_drop_cell_{cell}"] = 100.0
        metrics["pp_pressure_drop_cell_5"] = 102.1
        result = _evaluate(metrics, continuity_final=1e-6, cells=cells)
        window_values = [metrics[f"pp_pressure_drop_cell_{c}"] for c in cells]
        assert result["pp_pressure_drop_rel_spread_window"] == pytest.approx(
            relative_spread(window_values)
        )
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            relative_spread([200.0, 102.1, 100.0, 100.0])
        )

    def test_missing_window_cell_leaves_legacy_column(self):
        metrics = {k: v for k, v in U0P2_PASS.items() if k != "pp_pressure_drop_cell_8"}
        result = _evaluate(metrics, continuity_final=4e-6)
        assert result["pp_pressure_drop_rel_spread_window"] is None
        assert result["warnings"] == []
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.0258, rel=1e-2
        )

    def test_unknown_when_inputs_missing(self):
        result = evaluate_convergence_quality({})
        assert result["convergence_quality"] == QUALITY_UNKNOWN
        assert result["needs_longer_solve"] is False
        assert set(result["unavailable"]) == {
            "lmh_relative_difference",
            "mass_balance_relative_error",
            "continuity_final",
        }
        assert result["pp_pressure_drop_rel_spread_window"] is None
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] is None
        assert result["warnings"] == []
        assert result["pp_pressure_drop_rel_spread_note"] is None

    def test_fail_even_if_some_checks_unavailable(self):
        result = evaluate_convergence_quality(
            {"lmh_relative_difference": -0.3},
            continuity_final=None,
        )
        assert result["convergence_quality"] == QUALITY_FAIL
        assert result["failures"] == ["lmh_relative_difference"]

    def test_metrics_from_summary_row_is_case_insensitive(self):
        metrics = metrics_from_summary_row(
            {
                "LMH_Relative_Difference": "1.7e-4",
                "mass_balance_relative_error": "1e-4",
                "pp_pressure_drop_cell_4": "1",
                "pp_pressure_drop_cell_5": "1",
                "pp_pressure_drop_cell_6": "1",
                "pp_pressure_drop_cell_7": "1",
                "pp_pressure_drop_cell_8": "1",
                "pp_pressure_drop_cell_22": "1",
            }
        )
        assert metrics["lmh_relative_difference"] == "1.7e-4"
        assert metrics["pp_pressure_drop_cell_8"] == "1"
        assert metrics["pp_pressure_drop_cell_22"] == "1"
        result = _evaluate(metrics, continuity_final=1e-6)
        assert result["convergence_quality"] == QUALITY_PASS
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(0.0)
        assert result["pp_pressure_drop_rel_spread_window"] == pytest.approx(0.0)

    def test_manifest_payload_carries_spread_warning_not_failure(self):
        result = _evaluate(U0P3_PASS_CONV2000, continuity_final=1e-6)
        payload = manifest_quality_payload(result)
        assert payload["convergence_quality"] == QUALITY_PASS
        assert payload["needs_longer_solve"] is False
        assert payload["convergence_quality_failures"] == []
        assert payload["convergence_quality_warnings"] == [
            PRESSURE_DROP_REL_SPREAD_WARNING
        ]
        assert payload["pp_pressure_drop_rel_spread_window"] == result[
            "pp_pressure_drop_rel_spread_window"
        ]
        assert payload["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.1474, rel=1e-2
        )
        assert payload["pp_pressure_drop_rel_spread_note"] == PRESSURE_DROP_REL_SPREAD_NOTE

    def test_relative_spread_definition(self):
        assert relative_spread([100.0, 101.0, 102.0, 101.3]) == pytest.approx(
            2.0 / 101.075
        )


@pytest.fixture(scope="module")
def inventory():
    return load_case_inventory()


class TestInventoryQualityClassification:
    def test_detect_convergence_quality_records_dP_spread_diagnostic(self, inventory):
        record = {
            "_summary_row": {
                "lmh_relative_difference": "1.01e-4",
                "mass_balance_relative_error": "1e-4",
                "pp_pressure_drop_cell_4": "117.44",
                "pp_pressure_drop_cell_5": "114.47",
                "pp_pressure_drop_cell_6": "114.47",
                "pp_pressure_drop_cell_7": "115.92",
                "pp_pressure_drop_cell_8": "116.54",
            },
            "_case_dir_path": None,
            "_evaluation_cell_numbers": D2450_WINDOW_CELLS,
        }
        inventory.detect_convergence_quality(record)
        assert record["pp_pressure_drop_rel_spread_cells_4_7"] == pytest.approx(
            0.0258, rel=1e-2
        )
        assert record["pp_pressure_drop_rel_spread_window"] == pytest.approx(
            relative_spread([114.47, 114.47, 115.92, 116.54])
        )
        assert record["convergence_quality_warnings"] == [
            PRESSURE_DROP_REL_SPREAD_WARNING
        ]
        assert record["pp_pressure_drop_rel_spread_note"] == PRESSURE_DROP_REL_SPREAD_NOTE
        assert "pp_pressure_drop_rel_spread_window" in inventory.COMPACT_FIELDNAMES
        assert "pp_pressure_drop_rel_spread_cells_4_7" in inventory.COMPACT_FIELDNAMES
        assert "convergence_quality_warnings" in inventory.COMPACT_FIELDNAMES
        assert "pp_pressure_drop_rel_spread_note" in inventory.COMPACT_FIELDNAMES
        assert "pp_pressure_drop_rel_spread_window" in inventory.CASE_INVENTORY_FIELDNAMES
        assert "pp_pressure_drop_rel_spread_cells_4_7" in inventory.CASE_INVENTORY_FIELDNAMES
        assert "convergence_quality_warnings" in inventory.CASE_INVENTORY_FIELDNAMES
        assert "pp_pressure_drop_rel_spread_note" in inventory.CASE_INVENTORY_FIELDNAMES

    def test_quality_fail_flags_longer_solve_despite_qoi_converged(self, inventory):
        record = {
            "convergence_status": inventory.CONVERGED,
            "has_case_data_pair": True,
            "has_summary_metrics_wide": True,
            "has_all_basic_contours": True,
            "has_all_pyensight_contours": True,
            "has_shear_contour": True,
            "hard_solver_failure_detected": False,
            "likely_complete_from_logs": True,
            "contour_failed_count": 0,
            "contour_export_overall_status": "",
            "shear_export_status": "",
            "convergence_quality": QUALITY_FAIL,
            "needs_longer_solve": True,
            "convergence_quality_failures": ["lmh_relative_difference"],
        }
        inventory.classify_case(record)
        assert record["case_status"] == inventory.NEEDS_LONGER_SOLVE
        assert record["needs_longer_solve"] is True
        assert record["likely_complete"] is False
        assert "longer" in record["suggested_next_action"].lower()

    def test_max_iter_with_quality_pass_stays_postprocessed_unconverged(
        self, inventory
    ):
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
            "convergence_quality": QUALITY_PASS,
            "needs_longer_solve": False,
            "convergence_quality_failures": [],
        }
        inventory.classify_case(record)
        assert record["case_status"] == inventory.POSTPROCESSED_UNCONVERGED
        assert record["needs_solver_rerun"] is False
        assert record["needs_longer_solve"] is False
        assert record["convergence_quality"] == QUALITY_PASS

    def test_max_iter_quality_pass_incomplete_artifacts_not_solver_rerun(
        self, inventory
    ):
        """Gate PASS clears MAX_ITER from the NEEDS_SOLVER_RERUN / rerun queue."""
        record = {
            "convergence_status": inventory.MAX_ITER_REACHED,
            "has_case_data_pair": True,
            "has_summary_metrics_wide": True,
            "has_all_basic_contours": False,
            "has_all_pyensight_contours": False,
            "has_shear_contour": False,
            "hard_solver_failure_detected": False,
            "likely_complete_from_logs": False,
            "contour_failed_count": 0,
            "contour_export_overall_status": "",
            "shear_export_status": "",
            "convergence_quality": QUALITY_PASS,
            "needs_longer_solve": False,
            "convergence_quality_failures": [],
        }
        inventory.classify_case(record)
        assert record["case_status"] != inventory.NEEDS_SOLVER_RERUN
        assert record["case_status"] == inventory.READY_FOR_POSTPROCESSING
        assert record["needs_solver_rerun"] is False
        # Same filter as run_inventory → rerun_candidates.csv
        is_rerun_candidate = bool(
            record.get("needs_solver_rerun") or record.get("needs_longer_solve")
        )
        assert is_rerun_candidate is False
