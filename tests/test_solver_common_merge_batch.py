"""Parity tests for merge_batch_case_overrides (campaign case resolution)."""

from __future__ import annotations

from typing import Any

import pytest

from helpers import load_solver_common


def legacy_merge(common_settings: Any, case_dict: Any) -> dict[str, Any]:
    """Inline copy of batch_solver_sweep merge before extraction."""
    return {**common_settings, **case_dict}


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


class TestMergeBatchCaseOverrides:
    def test_parity_with_inline_starstar(self, common):
        cases = [
            ({}, {"a": 1}),
            ({"a": 1, "b": 2}, {"b": 9, "c": 3}),
            (
                {"operating_pressure": 101325.0, "max_iterations": 2000},
                {"inlet_velocity_value": 0.1, "outlet_gauge_pressure": 4.0e6},
            ),
            ({"x": 1}, {}),
        ]
        for common_settings, case_dict in cases:
            assert common.merge_batch_case_overrides(
                common_settings, case_dict
            ) == legacy_merge(common_settings, case_dict)

    def test_none_common_treated_as_empty(self, common):
        assert common.merge_batch_case_overrides(None, {"a": 1}) == {"a": 1}

    def test_case_wins_over_common(self, common):
        merged = common.merge_batch_case_overrides(
            {"operating_pressure": 1.0, "max_iterations": 2000},
            {"operating_pressure": 2.0},
        )
        assert merged["operating_pressure"] == 2.0
        assert merged["max_iterations"] == 2000

    def test_rejects_non_mapping_case(self, common):
        with pytest.raises(TypeError, match="case_dict"):
            common.merge_batch_case_overrides({}, ["not", "a", "dict"])
