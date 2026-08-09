"""Pure-Python tests for asymmetric DomainLayout / EvaluationWindow helpers."""

from __future__ import annotations

import math

import pytest

from _domain_layout import (
    CELL_LENGTH_X_M,
    CURRENT_LAYOUT,
    DomainLayout,
    EvaluationWindow,
    LEGACY_LAYOUT,
    resolve_layout,
)
from _fluent_report_helpers import (
    spacer_cell_numbers,
    unit_cell_boundary_positions,
)


CURRENT_BOUNDARIES = [
    0.0,
    0.003465,
    0.00693,
    0.010395,
    0.01386,
    0.017325,
    0.02079,
    0.024255,
    0.02772,
    0.031185,
    0.03465,
]

CURRENT_SPAN_LABELS = [
    "buffer_in_1",
    "active_1",
    "active_2",
    "active_3",
    "active_4",
    "active_5",
    "active_6",
    "active_7",
    "buffer_out_1",
    "buffer_out_2",
]


def _assert_close_sequence(actual, expected, *, rel_tol=1.0e-12):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert math.isclose(got, want, rel_tol=rel_tol), (got, want)


class TestDomainLayoutCurrent:
    def test_current_boundaries_and_spans(self):
        layout = CURRENT_LAYOUT
        assert layout.n_buffer_in == 1
        assert layout.n_active == 7
        assert layout.n_buffer_out == 2
        assert math.isclose(layout.cell_length_x_m, CELL_LENGTH_X_M, rel_tol=1.0e-12)

        boundaries = layout.boundary_positions(0.0)
        assert len(boundaries) == 11
        _assert_close_sequence(boundaries, CURRENT_BOUNDARIES)

        spans = layout.spans(0.0)
        assert len(spans) == 10
        assert [label for label, _, _ in spans] == CURRENT_SPAN_LABELS
        for index, (label, x_min, x_max) in enumerate(spans):
            assert label == CURRENT_SPAN_LABELS[index]
            assert math.isclose(x_min, CURRENT_BOUNDARIES[index], rel_tol=1.0e-12)
            assert math.isclose(x_max, CURRENT_BOUNDARIES[index + 1], rel_tol=1.0e-12)

        active_min, active_max = layout.active_span(0.0)
        assert math.isclose(active_min, 0.003465, rel_tol=1.0e-12)
        assert math.isclose(active_max, 0.02772, rel_tol=1.0e-12)
        assert layout.active_cell_numbers() == [2, 3, 4, 5, 6, 7, 8]


class TestDomainLayoutLegacy:
    def test_legacy_boundaries(self):
        layout = LEGACY_LAYOUT
        boundaries = layout.boundary_positions(0.0)
        assert len(boundaries) == 6
        expected = [
            0.0,
            0.003465,
            0.00693,
            0.010395,
            0.01386,
            0.017325,
        ]
        _assert_close_sequence(boundaries, expected)
        assert len(layout.spans(0.0)) == 5

    def test_legacy_parity_with_fluent_report_helpers(self):
        layout = LEGACY_LAYOUT
        new_boundaries = layout.boundary_positions(0.0)
        old_boundaries = unit_cell_boundary_positions(0.0, 0.017325, 5)
        assert len(new_boundaries) == len(old_boundaries) == 6
        for got, want in zip(new_boundaries, old_boundaries):
            assert math.isclose(got, want, rel_tol=1.0e-12)

        assert layout.active_cell_numbers() == spacer_cell_numbers(5, 1) == [2, 3, 4]


class TestDomainLayoutX0Shift:
    def test_nonzero_x0_shifts_every_boundary(self):
        x0 = 0.001
        layout = CURRENT_LAYOUT
        shifted = layout.boundary_positions(x0)
        assert len(shifted) == 11
        for got, base in zip(shifted, CURRENT_BOUNDARIES):
            assert math.isclose(got, base + x0, rel_tol=1.0e-12)

        active_min, active_max = layout.active_span(x0)
        assert math.isclose(active_min, 0.003465 + x0, rel_tol=1.0e-12)
        assert math.isclose(active_max, 0.02772 + x0, rel_tol=1.0e-12)


class TestDomainLayoutValidation:
    def test_n_active_zero_raises(self):
        with pytest.raises(ValueError, match="n_active"):
            DomainLayout(1, 0, 1, CELL_LENGTH_X_M)

    def test_negative_counts_raise(self):
        with pytest.raises(ValueError, match="n_buffer_in"):
            DomainLayout(-1, 3, 1, CELL_LENGTH_X_M)
        with pytest.raises(ValueError, match="n_buffer_out"):
            DomainLayout(1, 3, -1, CELL_LENGTH_X_M)

    def test_nonpositive_cell_length_raises(self):
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(1, 3, 1, 0.0)
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(1, 3, 1, -0.003465)

    def test_window_consuming_active_span_raises(self):
        layout = CURRENT_LAYOUT
        window = EvaluationWindow(3, 4)  # 3 + 4 == 7
        with pytest.raises(ValueError, match="n_lead_excluded"):
            window.evaluation_cell_numbers(layout)
        with pytest.raises(ValueError, match="n_lead_excluded"):
            window.evaluation_local_indices(layout)


class TestEvaluationWindow:
    def test_lead_exclusion_on_current_and_shorter_active(self):
        window = EvaluationWindow(1, 0)

        assert window.evaluation_local_indices(CURRENT_LAYOUT) == [
            2,
            3,
            4,
            5,
            6,
            7,
        ]
        assert window.evaluation_cell_numbers(CURRENT_LAYOUT) == [
            3,
            4,
            5,
            6,
            7,
            8,
        ]

        shorter = DomainLayout(1, 5, 2, CELL_LENGTH_X_M)
        assert window.evaluation_local_indices(shorter) == [2, 3, 4, 5]
        assert window.evaluation_cell_numbers(shorter) == [3, 4, 5, 6]


class TestGeometryRegistry:
    def test_resolve_current_geometry(self):
        record = resolve_layout("D2450_a45_7c_brg110")
        assert record.layout == CURRENT_LAYOUT
        assert record.buffer_wall_base_names == (
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
        )
        assert record.membrane_wall_base_names == (
            "wall_top_mem",
            "wall_bottom_mem",
        )

    def test_resolve_unknown_geometry_raises(self):
        with pytest.raises(KeyError, match="Unknown geometry"):
            resolve_layout("not_a_real_geometry")
        with pytest.raises(KeyError, match="D2450_a45_7c_brg110"):
            resolve_layout("Empty")
