"""Tests for run_config validation control (F-04 commit 3)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
    module.mesh_id = "max085_min005_cpg5_bl4_peel2"
    module.allow_legacy_mesh_case_name_mismatch = True
    return module


def run_post_override_validation(cfg, validate_name: str) -> str:
    """Mirror meshing/solver worker validation gate after overrides."""
    if cfg.run_config_validation_skipped():
        return "skipped"
    getattr(cfg, validate_name)()
    return "validated"


class TestRunConfigValidationSkipped:
    def test_default_does_not_skip_validation(self, cfg, monkeypatch):
        monkeypatch.delenv("PYFLUENT_SKIP_VALIDATION", raising=False)
        assert cfg.run_config_validation_skipped() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " Yes "])
    def test_skip_env_var_is_truthy(self, cfg, monkeypatch, value):
        monkeypatch.setenv("PYFLUENT_SKIP_VALIDATION", value)
        assert cfg.run_config_validation_skipped() is True


class TestRunConfigValidationControl:
    def test_default_validates_populated_config_after_overrides(self, cfg, monkeypatch):
        monkeypatch.delenv("PYFLUENT_SKIP_VALIDATION", raising=False)
        apply_json_overrides(cfg, {"max_iterations": 1000, "run_label": "test"})
        assert run_post_override_validation(cfg, "validate_for_solver") == "validated"

    def test_custom_config_path_does_not_imply_skip(self, cfg, monkeypatch):
        monkeypatch.setenv("PYFLUENT_RUN_CONFIG", "/tmp/custom_run_config.py")
        monkeypatch.delenv("PYFLUENT_SKIP_VALIDATION", raising=False)
        assert cfg.run_config_validation_skipped() is False
        assert run_post_override_validation(cfg, "validate_for_meshing") == "validated"

    def test_skip_env_var_prevents_validation(self, cfg, monkeypatch):
        monkeypatch.setenv("PYFLUENT_SKIP_VALIDATION", "1")
        cfg.validate_for_solver = MagicMock()
        assert run_post_override_validation(cfg, "validate_for_solver") == "skipped"
        cfg.validate_for_solver.assert_not_called()
