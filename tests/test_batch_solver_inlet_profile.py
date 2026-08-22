"""Sweep must not inherit run_config's plug default for the inlet BC."""

from __future__ import annotations

import pytest

from helpers import CONFIGS_DIR, load_batch_solver_sweep, load_module


def test_live_common_solver_settings_sets_parabolic_inlet():
    batchcfg = load_module("batch_config_inlet_profile", CONFIGS_DIR / "batch_config.py")
    assert batchcfg.common_solver_settings["use_inlet_velocity_profile"] is True


def test_sweep_refuses_when_inlet_profile_flag_is_unset():
    sweep = load_batch_solver_sweep()
    with pytest.raises(ValueError, match="use_inlet_velocity_profile"):
        sweep.require_explicit_inlet_velocity_profile(
            {"max_iterations": 2000},
            [{"geo_id": "D2450_a45", "run_id": "u0p2_p6M"}],
        )


def test_sweep_refuses_when_common_and_cases_are_empty():
    sweep = load_batch_solver_sweep()
    with pytest.raises(ValueError, match="use_inlet_velocity_profile"):
        sweep.require_explicit_inlet_velocity_profile({}, [])


def test_sweep_accepts_explicit_true_in_common():
    sweep = load_batch_solver_sweep()
    sweep.require_explicit_inlet_velocity_profile(
        {"use_inlet_velocity_profile": True},
        [{"geo_id": "D2450_a45", "run_id": "u0p2_p6M"}],
    )


def test_sweep_accepts_explicit_false_on_every_case():
    sweep = load_batch_solver_sweep()
    sweep.require_explicit_inlet_velocity_profile(
        {},
        [{"geo_id": "D2450_a45", "run_id": "u0p2_p6M", "use_inlet_velocity_profile": False}],
    )


def test_live_batch_config_passes_the_sweep_gate():
    batchcfg = load_module("batch_config_inlet_gate", CONFIGS_DIR / "batch_config.py")
    sweep = load_batch_solver_sweep()
    sweep.require_explicit_inlet_velocity_profile(
        batchcfg.common_solver_settings,
        batchcfg.solver_sweep_cases,
    )
