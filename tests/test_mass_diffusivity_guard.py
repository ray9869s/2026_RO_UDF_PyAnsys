"""Fluent mixture diffusivity is read from the template, not written."""

from __future__ import annotations

import pytest

from helpers import load_solver_code


def test_fluent_diffusivity_matches_config():
    solver_code = load_solver_code("diffusivity_ok")
    state = {"option": "constant-dilute-appx", "value": 2e-09}
    actual = solver_code.assert_fluent_mass_diffusivity_matches_config(
        state,
        2.0e-9,
    )
    assert actual == pytest.approx(2.0e-9)


def test_fluent_diffusivity_mismatch_raises():
    solver_code = load_solver_code("diffusivity_mismatch")
    state = {"option": "constant-dilute-appx", "value": 1e-09}
    with pytest.raises(ValueError, match="does not match"):
        solver_code.assert_fluent_mass_diffusivity_matches_config(state, 2.0e-9)


def test_fluent_diffusivity_wrong_option_raises():
    solver_code = load_solver_code("diffusivity_option")
    state = {"option": "kinetic-theory", "value": 2e-09}
    with pytest.raises(ValueError, match="constant-dilute-appx"):
        solver_code.assert_fluent_mass_diffusivity_matches_config(state, 2.0e-9)


def test_fluent_diffusivity_missing_value_raises():
    solver_code = load_solver_code("diffusivity_missing")
    with pytest.raises(ValueError, match="no 'value' key"):
        solver_code.assert_fluent_mass_diffusivity_matches_config({}, 2.0e-9)
