"""Characterization tests for run_config validation gaps (F-04).

These document current acceptance behavior that later refactors should tighten.
They must not import meshing_code_260616.py or solver_code_260616.py (Ansys).
"""

from __future__ import annotations

import json

import pytest

from helpers import (
    apply_json_overrides,
    load_run_config,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)


@pytest.fixture
def cfg():
    module = load_run_config()
    populate_valid_meshing_config(module)
    populate_valid_solver_config(module)
    return module


class TestNumericHelperGaps:
    def test_true_rejected_by_positive_number_check(self, cfg):
        with pytest.raises(TypeError, match="must be numeric, not bool"):
            cfg._require_positive_number("processor_count", True)

    def test_false_rejected_by_nonnegative_number_check(self, cfg):
        with pytest.raises(TypeError, match="must be numeric, not bool"):
            cfg._require_nonnegative_number("periodic_shift_x", False)


class TestMeshingValidationGaps:
    def test_m_min_greater_than_m_max_is_accepted(self, cfg):
        cfg.m_max = 0.01
        cfg.m_min = 0.10
        cfg.validate_for_meshing()

    def test_negative_mesh_sizes_are_rejected(self, cfg):
        cfg.m_min = -0.005
        with pytest.raises(ValueError, match="must be positive"):
            cfg.validate_for_meshing()


class TestSolverValidationGaps:
    def test_negative_inlet_velocity_is_accepted(self, cfg):
        cfg.inlet_velocity_value = -0.1
        cfg.validate_for_solver()

    def test_string_numeric_inlet_velocity_is_accepted(self, cfg):
        cfg.inlet_velocity_value = "0.15"
        cfg.validate_for_solver()

    def test_outlet_gauge_pressure_is_not_validated(self, cfg):
        cfg.outlet_gauge_pressure = -1.0e6
        cfg.validate_for_solver()

    def test_operating_pressure_is_not_validated(self, cfg):
        cfg.operating_pressure = -500.0
        cfg.validate_for_solver()

    def test_salt_mass_fraction_is_not_validated(self, cfg):
        cfg.salt_mass_fraction = 1.5
        cfg.validate_for_solver()


class TestOverrideApplicationGaps:
    def test_unknown_override_keys_are_accepted(self, cfg):
        apply_json_overrides(
            cfg,
            {
                "totally_unknown_key": 123,
                "another_typo": "value",
            },
        )
        assert cfg.totally_unknown_key == 123
        assert cfg.another_typo == "value"

    def test_override_can_replace_validation_function(self, cfg):
        sentinel = object()
        apply_json_overrides(cfg, {"validate_for_meshing": sentinel})
        assert cfg.validate_for_meshing is sentinel

    def test_json_override_roundtrip_matches_worker_pattern(self, cfg):
        overrides = {
            "m_max": 0.1,
            "m_min": 0.006,
            "max_iterations": 1000,
            "run_label": "preliminary_deadline_1000iter",
        }
        apply_json_overrides(cfg, json.loads(json.dumps(overrides)))
        assert cfg.m_max == 0.1
        assert cfg.m_min == 0.006
        assert cfg.max_iterations == 1000
        assert cfg.run_label == "preliminary_deadline_1000iter"
        cfg.validate_for_meshing()
        cfg.validate_for_solver()
