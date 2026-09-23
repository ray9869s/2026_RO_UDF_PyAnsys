"""Fail-closed Coupled GTS scale-factor pilot: isolation and readback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import load_solver_code
from test_solver_relaxation import IgnoringStateLeaf, StateLeaf


class FactorLeaf:
    def __init__(self, value, active=True):
        self._value = value
        self._active = active

    def is_active(self):
        return self._active

    def get_state(self):
        return self._value


class FormulationOnly:
    """Live methods.pseudo_time_method: formulation only.

    Accessing time_step_method here must fail the test: that parent does
    not hold automatic / length_scale_methods / scale-factor.
    """

    def get_state(self):
        return {"formulation": {"coupled_solver": "global-time-step"}}

    def is_active(self):
        return True

    def __getattr__(self, name):
        if name == "time_step_method":
            raise AssertionError(
                "must not look under solution.methods.pseudo_time_method "
                "for time-step settings"
            )
        raise AttributeError(name)


def _attach_factor_leaf(parent, value, active=True):
    object.__setattr__(
        parent, "time_step_size_scale_factor", FactorLeaf(value, active)
    )
    return parent


def make_gts_solution(*, factor=1.0, factor_active=True, method="automatic"):
    time_step_method = _attach_factor_leaf(
        StateLeaf(
            time_step_method=method,
            length_scale_methods="conservative",
            time_step_size_scale_factor=factor,
        ),
        factor,
        active=factor_active,
    )
    return SimpleNamespace(
        methods=SimpleNamespace(
            p_v_coupling=StateLeaf(flow_scheme="Coupled"),
            pseudo_time_method=FormulationOnly(),
        ),
        run_calculation=SimpleNamespace(
            pseudo_time_settings=SimpleNamespace(
                verbosity=0,
                time_step_method=time_step_method,
            )
        ),
    )


def _tsm(solution):
    return solution.run_calculation.pseudo_time_settings.time_step_method


def test_preserve_does_not_touch_gts_leaf():
    solver_code = load_solver_code("gts_scale_preserve")
    solution = make_gts_solution(factor=1.0)
    outcome = solver_code.apply_gts_time_step_size_scale_factor(
        solution, "preserve"
    )
    assert outcome["status"] == "PRESERVED"
    assert _tsm(solution).get_state() == {
        "time_step_method": "automatic",
        "length_scale_methods": "conservative",
        "time_step_size_scale_factor": 1.0,
    }


def test_gts_scale_factor_writes_three_on_run_calculation_leaf():
    solver_code = load_solver_code("gts_scale_apply")
    solution = make_gts_solution(factor=1.0)
    outcome = solver_code.apply_gts_time_step_size_scale_factor(solution, 3.0)
    assert outcome["status"] == "APPLIED_CONFIRMED"
    assert outcome["after"] == pytest.approx(3.0)
    state = _tsm(solution).get_state()
    assert state["time_step_method"] == "automatic"
    assert state["length_scale_methods"] == "conservative"
    assert state["time_step_size_scale_factor"] == pytest.approx(3.0)
    assert solution.methods.pseudo_time_method.get_state() == {
        "formulation": {"coupled_solver": "global-time-step"}
    }


def test_does_not_read_time_step_settings_from_methods_pseudo_time_method():
    solver_code = load_solver_code("gts_scale_methods_trap")
    solution = make_gts_solution(factor=1.0)
    solver_code.apply_gts_time_step_size_scale_factor(solution, 3.0)
    assert _tsm(solution).get_state()["time_step_size_scale_factor"] == (
        pytest.approx(3.0)
    )


def test_inactive_scale_factor_aborts_without_writing():
    solver_code = load_solver_code("gts_scale_inactive")
    solution = make_gts_solution(factor=1.0, factor_active=False)
    with pytest.raises(
        solver_code.GtsTimeStepScaleFactorError,
        match="inactive",
    ):
        solver_code.apply_gts_time_step_size_scale_factor(solution, 3.0)
    assert _tsm(solution).get_state()["time_step_size_scale_factor"] == 1.0
    assert _tsm(solution).get_state()["time_step_method"] == "automatic"


def test_scale_factor_readback_mismatch_aborts():
    solver_code = load_solver_code("gts_scale_readback")
    time_step_method = _attach_factor_leaf(
        IgnoringStateLeaf(
            time_step_method="automatic",
            length_scale_methods="conservative",
            time_step_size_scale_factor=1.0,
        ),
        1.0,
        active=True,
    )
    solution = SimpleNamespace(
        methods=SimpleNamespace(
            p_v_coupling=StateLeaf(flow_scheme="Coupled"),
            pseudo_time_method=FormulationOnly(),
        ),
        run_calculation=SimpleNamespace(
            pseudo_time_settings=SimpleNamespace(
                verbosity=0,
                time_step_method=time_step_method,
            )
        ),
    )
    with pytest.raises(
        solver_code.GtsTimeStepScaleFactorError,
        match="readback failed",
    ):
        solver_code.apply_gts_time_step_size_scale_factor(solution, 3.0)
    assert time_step_method.get_state()["time_step_size_scale_factor"] == 1.0
    assert time_step_method.get_state()["time_step_method"] == "automatic"


def test_user_specified_time_step_method_aborts_without_switching():
    solver_code = load_solver_code("gts_scale_user_dt")
    solution = make_gts_solution(factor=1.0, method="user-specified")
    with pytest.raises(
        solver_code.GtsTimeStepScaleFactorError,
        match="not automatic",
    ):
        solver_code.apply_gts_time_step_size_scale_factor(solution, 3.0)
    state = _tsm(solution).get_state()
    assert state["time_step_method"] == "user-specified"
    assert state["time_step_size_scale_factor"] == 1.0
    assert state["length_scale_methods"] == "conservative"
