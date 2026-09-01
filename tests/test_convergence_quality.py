"""Tests for post-hoc convergence quality gate."""

from __future__ import annotations

import pytest

from helpers import load_case_inventory
from ro.convergence_quality import (
    QUALITY_FAIL,
    QUALITY_PASS,
    QUALITY_UNKNOWN,
    evaluate_convergence_quality,
    metrics_from_summary_row,
    relative_spread,
)


# Study values (D2450_a45, p=6 MPa). Pressure cells approximate the reported
# 1.3% vs 8% relative spreads when mean-normalized.
U0P1_PASS = {
    "lmh_relative_difference": 1.01e-4,
    "mass_balance_relative_error": 1.0e-4,
    "pp_pressure_drop_cell_4": 291.0,
    "pp_pressure_drop_cell_5": 292.0,
    "pp_pressure_drop_cell_6": 294.0,
    "pp_pressure_drop_cell_7": 293.0,
}
U0P3_FAIL_301 = {
    "lmh_relative_difference": -2.92e-1,
    "mass_balance_relative_error": 2.0e-1,
    "pp_pressure_drop_cell_4": 1600.0,
    "pp_pressure_drop_cell_5": 1650.0,
    "pp_pressure_drop_cell_6": 1700.0,
    "pp_pressure_drop_cell_7": 1730.0,
}


class TestEvaluateConvergenceQuality:
    def test_u0p1_style_max_iter_still_passes(self):
        result = evaluate_convergence_quality(
            U0P1_PASS,
            continuity_final=4.2e-7,
        )
        assert result["convergence_quality"] == QUALITY_PASS
        assert result["needs_longer_solve"] is False
        assert result["failures"] == []
        assert result["pp_pressure_drop_rel_spread_cells_4_7"] < 0.03

    def test_u0p3_301_fails_on_lmh_and_continuity(self):
        result = evaluate_convergence_quality(
            U0P3_FAIL_301,
            continuity_final=6.4e-3,
        )
        assert result["convergence_quality"] == QUALITY_FAIL
        assert result["needs_longer_solve"] is True
        assert "lmh_relative_difference" in result["failures"]
        assert "continuity_final" in result["failures"]
        assert "pp_pressure_drop_rel_spread_cells_4_7" in result["failures"]

    def test_unknown_when_inputs_missing(self):
        result = evaluate_convergence_quality({})
        assert result["convergence_quality"] == QUALITY_UNKNOWN
        assert result["needs_longer_solve"] is False
        assert set(result["unavailable"]) == {
            "lmh_relative_difference",
            "mass_balance_relative_error",
            "continuity_final",
            "pp_pressure_drop_rel_spread_cells_4_7",
        }

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
            }
        )
        assert metrics["lmh_relative_difference"] == "1.7e-4"
        result = evaluate_convergence_quality(metrics, continuity_final=1e-6)
        assert result["convergence_quality"] == QUALITY_PASS

    def test_relative_spread_definition(self):
        assert relative_spread([100.0, 101.0, 102.0, 101.3]) == pytest.approx(
            2.0 / 101.075
        )


@pytest.fixture(scope="module")
def inventory():
    return load_case_inventory()


class TestInventoryQualityClassification:
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
        assert record["needs_longer_solve"] is False
        assert record["convergence_quality"] == QUALITY_PASS
