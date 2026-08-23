"""Parity harness: asymmetric DomainLayout scoring vs legacy symmetric helpers.

Keeps the old symmetric implementations reachable and asserts old == new on
real inputs before any deletion of the legacy path.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from ro.domain_layout import CELL_LENGTH_X_M, CURRENT_LAYOUT, EvaluationWindow, LEGACY_LAYOUT
from ro.fluent_report_helpers import (
    derive_periodic_spacer_pressure_metrics,
    derive_periodic_spacer_pressure_metrics_for_layout,
    derive_spacer_cell_metrics,
    derive_spacer_cell_metrics_for_layout,
    resolve_evaluation_window_from_config,
    resolve_scoring_layout_from_config,
    scoring_geometry_from_layout,
    spacer_cell_numbers,
    unit_cell_boundary_positions,
    unit_cell_concentration_report_name,
    unit_cell_pressure_report_name,
)


def _assert_close_sequence(actual, expected):
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected):
        assert math.isclose(left, right, rel_tol=1.0e-12, abs_tol=0.0)


class TestScoringGeometryParityLegacy:
    """Legacy 1+3+1: new path must match the pre-change symmetric formulas."""

    def test_boundaries_spacer_cells_and_lengths(self):
        domain_x_min_m = 0.0
        domain_length_m = 0.017325
        buffer_length_m = 0.003465
        n_unit_cells = 5
        n_buffer_cells_each_end = 1

        old_boundaries = unit_cell_boundary_positions(
            domain_x_min_m,
            domain_length_m,
            n_unit_cells,
        )
        old_spacer_cells = spacer_cell_numbers(
            n_unit_cells,
            n_buffer_cells_each_end,
        )
        old_spacer_x_in = domain_x_min_m + buffer_length_m
        old_spacer_x_out = domain_x_min_m + domain_length_m - buffer_length_m
        old_spacer_length = domain_length_m - 2.0 * buffer_length_m

        new = scoring_geometry_from_layout(LEGACY_LAYOUT, domain_x_min_m)

        assert len(new.unit_cell_boundary_x_m) == 6
        assert len(old_boundaries) == 6
        assert n_unit_cells == 5
        assert LEGACY_LAYOUT.n_total == 5
        _assert_close_sequence(new.unit_cell_boundary_x_m, old_boundaries)
        assert new.spacer_cells == old_spacer_cells == [2, 3, 4]
        assert math.isclose(new.spacer_x_in_m, old_spacer_x_in, rel_tol=1.0e-12)
        assert math.isclose(new.spacer_x_out_m, old_spacer_x_out, rel_tol=1.0e-12)
        assert math.isclose(new.spacer_length_m, old_spacer_length, rel_tol=1.0e-12)
        assert math.isclose(new.domain_length_m, domain_length_m, rel_tol=1.0e-12)

    def test_derived_cell_and_periodic_metrics(self):
        pressures = [100.0, 90.0, 77.0, 61.0, 42.0, 20.0]
        concentrations = [0.035, 0.036, 0.038, 0.041, 0.045, 0.050]
        values = {}
        for index, value in enumerate(pressures):
            values[unit_cell_pressure_report_name(index)] = value
        for index, value in enumerate(concentrations):
            values[unit_cell_concentration_report_name(index)] = value

        old_derived = derive_spacer_cell_metrics(values, 5, 1)
        new_derived = derive_spacer_cell_metrics_for_layout(values, LEGACY_LAYOUT)
        assert new_derived == old_derived

        old_periodic = derive_periodic_spacer_pressure_metrics(
            old_derived,
            domain_length_m=0.017325,
            n_unit_cells=5,
            n_buffer_cells_each_end=1,
            n_inlet_spacer_cells_excluded=1,
        )
        new_periodic = derive_periodic_spacer_pressure_metrics_for_layout(
            new_derived,
            LEGACY_LAYOUT,
            EvaluationWindow(n_lead_excluded=1, n_trail_excluded=0),
        )
        assert new_periodic.keys() == old_periodic.keys()
        for key in old_periodic:
            assert math.isclose(
                new_periodic[key],
                old_periodic[key],
                rel_tol=1.0e-12,
                abs_tol=0.0,
            )


class TestScoringGeometryCurrentLayout:
    """Current 1+7+2: exact plane counts and active span (no symmetric mapping)."""

    def test_boundaries_active_span_and_spacer_length(self):
        domain_x_min_m = 0.0
        layout = CURRENT_LAYOUT
        assert layout == CURRENT_LAYOUT

        new = scoring_geometry_from_layout(layout, domain_x_min_m)

        assert len(new.unit_cell_boundary_x_m) == 11
        assert layout.n_total == 10
        expected_boundaries = [
            domain_x_min_m + k * CELL_LENGTH_X_M for k in range(11)
        ]
        _assert_close_sequence(new.unit_cell_boundary_x_m, expected_boundaries)

        assert math.isclose(new.spacer_x_in_m, 0.003465, rel_tol=1.0e-12)
        assert math.isclose(new.spacer_x_out_m, 0.02772, rel_tol=1.0e-12)
        assert new.spacer_cells == list(range(2, 9))
        assert new.spacer_cells == [2, 3, 4, 5, 6, 7, 8]
        assert math.isclose(
            new.spacer_length_m,
            7 * CELL_LENGTH_X_M,
            rel_tol=1.0e-12,
        )
        assert math.isclose(new.spacer_length_m, 0.024255, rel_tol=1.0e-12)
        assert math.isclose(new.domain_length_m, 0.03465, rel_tol=1.0e-12)

        # Symmetric formula would misplace spacer_x_out at 0.031185 — refuse that.
        fake_symmetric_out = 0.03465 - CELL_LENGTH_X_M
        assert not math.isclose(
            new.spacer_x_out_m,
            fake_symmetric_out,
            rel_tol=1.0e-9,
            abs_tol=0.0,
        )

    def test_periodic_uses_manifest_lead_window(self):
        layout = CURRENT_LAYOUT
        window = EvaluationWindow(n_lead_excluded=3, n_trail_excluded=0)
        assert window.evaluation_cell_numbers(layout) == [5, 6, 7, 8]

        metrics = {
            f"pp_pressure_drop_cell_{cell}": 100.0 + cell for cell in range(2, 9)
        }
        derived = derive_periodic_spacer_pressure_metrics_for_layout(
            metrics, layout, window
        )
        expected = (105.0 + 106.0 + 107.0 + 108.0) / (4 * CELL_LENGTH_X_M)
        assert derived["pp_pressure_drop_periodic_per_m"] == pytest.approx(
            expected
        )

        lead_one = derive_periodic_spacer_pressure_metrics_for_layout(
            metrics,
            layout,
            EvaluationWindow(n_lead_excluded=1, n_trail_excluded=0),
        )
        assert lead_one["pp_pressure_drop_periodic_per_m"] != pytest.approx(
            expected
        )


class TestResolveScoringLayoutFromConfig:
    def test_stock_post_config_has_no_layout_default(self):
        from helpers import load_post_config

        cfg = load_post_config()
        with pytest.raises(AttributeError, match="n_buffer_in"):
            resolve_scoring_layout_from_config(cfg)

    def test_stock_post_config_accepts_explicit_layout_overrides(self):
        from helpers import load_post_config

        cfg = load_post_config()
        cfg.apply_post_config_overrides(
            cfg,
            {
                "n_buffer_in": 1,
                "n_active": 7,
                "n_buffer_out": 2,
                "cell_length_x_m": CELL_LENGTH_X_M,
                "buffer_length_in_m": CELL_LENGTH_X_M,
                "buffer_length_out_m": 0.00693,
                "domain_length_m": 0.03465,
                "buffer_length_m": 0.003465,
                "n_unit_cells": 10,
                "n_buffer_cells_each_end": None,
            },
        )
        geo = resolve_scoring_layout_from_config(cfg)
        assert geo.spacer_cells == list(range(2, 9))
        assert math.isclose(geo.domain_length_m, 0.03465, rel_tol=1.0e-12)

    def test_missing_asymmetric_keys_raise(self):
        cfg = SimpleNamespace(domain_x_min_m=0.0)
        with pytest.raises(AttributeError, match="n_buffer_in"):
            resolve_scoring_layout_from_config(cfg)

    def test_contradictory_domain_length_raises(self):
        cfg = SimpleNamespace(
            domain_x_min_m=0.0,
            n_buffer_in=1,
            n_active=7,
            n_buffer_out=2,
            cell_length_x_m=CELL_LENGTH_X_M,
            buffer_length_in_m=CELL_LENGTH_X_M,
            buffer_length_out_m=0.00693,
            domain_length_m=0.017325,  # stale 5-cell length
        )
        with pytest.raises(ValueError, match="domain_length_m contradicts"):
            resolve_scoring_layout_from_config(cfg)

    def test_asymmetric_with_stale_each_end_raises(self):
        cfg = SimpleNamespace(
            domain_x_min_m=0.0,
            n_buffer_in=1,
            n_active=7,
            n_buffer_out=2,
            cell_length_x_m=CELL_LENGTH_X_M,
            buffer_length_in_m=CELL_LENGTH_X_M,
            buffer_length_out_m=0.00693,
            domain_length_m=0.03465,
            n_unit_cells=10,
            buffer_length_m=0.003465,
            n_buffer_cells_each_end=1,  # cannot describe 1+7+2
        )
        with pytest.raises(ValueError, match="n_buffer_cells_each_end contradicts"):
            resolve_scoring_layout_from_config(cfg)

    def test_asymmetric_with_cleared_each_end_ok(self):
        cfg = SimpleNamespace(
            domain_x_min_m=0.0,
            n_buffer_in=1,
            n_active=7,
            n_buffer_out=2,
            cell_length_x_m=CELL_LENGTH_X_M,
            buffer_length_in_m=CELL_LENGTH_X_M,
            buffer_length_out_m=0.00693,
            domain_length_m=0.03465,
            n_unit_cells=10,
            buffer_length_m=0.003465,
            n_buffer_cells_each_end=None,
        )
        geo = resolve_scoring_layout_from_config(cfg)
        assert geo.spacer_cells == list(range(2, 9))
        assert len(geo.unit_cell_boundary_x_m) == 11


class TestResolveEvaluationWindowFromConfig:
    def test_stock_post_config_has_no_window_default(self):
        from helpers import load_post_config

        cfg = load_post_config()
        with pytest.raises(AttributeError, match="n_lead_excluded"):
            resolve_evaluation_window_from_config(cfg)

    def test_accepts_explicit_window_overrides(self):
        from helpers import load_post_config

        cfg = load_post_config()
        cfg.apply_post_config_overrides(
            cfg,
            {
                "n_lead_excluded": 3,
                "n_trail_excluded": 0,
                "n_inlet_spacer_cells_excluded": 3,
            },
        )
        window = resolve_evaluation_window_from_config(cfg)
        assert window.n_lead_excluded == 3
        assert window.n_trail_excluded == 0

    def test_inlet_alias_without_lead_is_not_enough(self):
        cfg = SimpleNamespace(n_inlet_spacer_cells_excluded=3)
        with pytest.raises(AttributeError, match="n_lead_excluded"):
            resolve_evaluation_window_from_config(cfg)

    def test_contradictory_inlet_alias_raises(self):
        cfg = SimpleNamespace(
            n_lead_excluded=3,
            n_trail_excluded=0,
            n_inlet_spacer_cells_excluded=1,
        )
        with pytest.raises(ValueError, match="n_inlet_spacer_cells_excluded contradicts"):
            resolve_evaluation_window_from_config(cfg)
