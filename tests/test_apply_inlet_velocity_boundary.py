"""Inlet BC success is logged only after Settings readback matches the request."""

from __future__ import annotations

import pytest

from helpers import load_solver_code

UDF_NAME = "RO_inlet_profile"
ZONE = "inlet"
U_MEAN = 0.2


class _State:
    def __init__(self, state):
        self._state = state

    def get_state(self):
        return self._state

    def set_state(self, state):
        self._state = state

    def __call__(self):
        return self._state


class _Component:
    def __init__(self):
        self.option = _State("value")
        self.udf = _State("")
        self.value = 0.0


class _Components(list):
    def __init__(self):
        super().__init__([_Component(), _Component(), _Component()])
        self._active = False

    def is_active(self):
        return self._active

    def get_state(self):
        return {}


class _Spec:
    def __init__(self, components, *, activate_on_set=True, fail_set_state=False):
        self._state = "Magnitude and Direction"
        self._components = components
        self._allowed = [
            "Magnitude and Direction",
            "Components",
            "Magnitude, Normal to Boundary",
        ]
        self.activate_on_set = activate_on_set
        self.fail_set_state = fail_set_state

    def __call__(self):
        return self._state

    def get_state(self):
        return self._state

    def set_state(self, state):
        if self.fail_set_state:
            raise RuntimeError("set_state failed")
        self._state = state
        if (
            self.activate_on_set
            and isinstance(state, str)
            and "omponent" in state.lower()
        ):
            self._components._active = True

    def allowed_values(self):
        return list(self._allowed)

    def get_attr(self, name):
        if name == "allowed-values":
            return list(self._allowed)
        raise AttributeError(name)


class _Magnitude:
    def __init__(self, value=0.1):
        self.value = value


class _FrozenMagnitude:
    def __init__(self, value):
        object.__setattr__(self, "_value", float(value))

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new):
        pass


class _Momentum:
    def __init__(
        self,
        *,
        magnitude=0.1,
        frozen_magnitude=False,
        activate_on_set=True,
        fail_set_state=False,
    ):
        self.velocity_components = _Components()
        self.velocity_specification_method = _Spec(
            self.velocity_components,
            activate_on_set=activate_on_set,
            fail_set_state=fail_set_state,
        )
        if frozen_magnitude:
            self.velocity_magnitude = _FrozenMagnitude(magnitude)
        else:
            self.velocity_magnitude = _Magnitude(magnitude)

    def __call__(self):
        return {}


class _Vin:
    def __init__(self, **kwargs):
        self.momentum = _Momentum(**kwargs)


def _stamp_profile(vin, udf_name=UDF_NAME):
    spec = vin.momentum.velocity_specification_method
    spec._state = "Components"
    comps = vin.momentum.velocity_components
    comps._active = True
    comps[0].option.set_state("udf")
    comps[0].udf.set_state(udf_name)
    comps[1].option.set_state("value")
    comps[1].value = 0.0
    comps[2].option.set_state("value")
    comps[2].value = 0.0


class _Solver:
    def __init__(self, vin, *, tui_exc=None, apply_on_tui=False):
        self.vin = vin
        self.tui_exc = tui_exc
        self.apply_on_tui = apply_on_tui
        self.commands = []

    def execute_tui(self, cmd):
        self.commands.append(cmd)
        if self.tui_exc is not None:
            raise self.tui_exc
        if self.apply_on_tui:
            _stamp_profile(self.vin)


def _apply(mod, vin, solver=None, *, use_profile=True):
    return mod.apply_inlet_velocity_boundary(
        vin,
        ZONE,
        U_MEAN,
        use_profile=use_profile,
        profile_udf_name=UDF_NAME,
        solver=solver,
    )


def _success_logged(capsys):
    return "Inlet BC set" in capsys.readouterr().out


def test_settings_and_tui_failures_raise_without_success_log(capsys):
    mod = load_solver_code("inlet_bc_all_fail")
    vin = _Vin(fail_set_state=True, activate_on_set=False)
    solver = _Solver(vin, tui_exc=RuntimeError("tui refused"))
    with pytest.raises(RuntimeError, match="TUI fallback failed"):
        _apply(mod, vin, solver)
    assert not _success_logged(capsys)


def test_tui_return_without_matching_readback_raises(capsys):
    mod = load_solver_code("inlet_bc_tui_stale")
    vin = _Vin(activate_on_set=False)
    solver = _Solver(vin, apply_on_tui=False)
    with pytest.raises(RuntimeError, match="readback failed"):
        _apply(mod, vin, solver)
    assert not _success_logged(capsys)


def test_settings_path_logs_success_only_after_matching_readback(capsys):
    mod = load_solver_code("inlet_bc_settings_ok")
    vin = _Vin()
    _apply(mod, vin)
    out = capsys.readouterr().out
    assert f"Inlet BC set on {ZONE}: Components" in out
    assert f"x-velocity UDF={UDF_NAME}" in out
    observed = mod.read_inlet_profile_state(vin)
    assert mod.inlet_profile_readback_error(observed, UDF_NAME) is None


def test_tui_path_logs_success_only_after_matching_readback(capsys):
    mod = load_solver_code("inlet_bc_tui_ok")
    vin = _Vin(activate_on_set=False)
    solver = _Solver(vin, apply_on_tui=True)
    _apply(mod, vin, solver)
    out = capsys.readouterr().out
    assert "Inlet BC set" in out
    assert "TUI Components+UDF fallback" in out
    observed = mod.read_inlet_profile_state(vin)
    assert mod.inlet_profile_readback_error(observed, UDF_NAME) is None


def test_plug_matching_magnitude_logs_success(capsys):
    mod = load_solver_code("inlet_bc_plug_ok")
    vin = _Vin(magnitude=0.05)
    _apply(mod, vin, use_profile=False)
    out = capsys.readouterr().out
    assert f"velocity_magnitude={U_MEAN} m/s" in out
    assert "Inlet BC set" in out
    assert vin.momentum.velocity_magnitude.value == U_MEAN


def test_plug_stale_readback_raises_without_success_log(capsys):
    mod = load_solver_code("inlet_bc_plug_stale")
    vin = _Vin(magnitude=0.05, frozen_magnitude=True)
    with pytest.raises(RuntimeError, match="readback failed"):
        _apply(mod, vin, use_profile=False)
    assert not _success_logged(capsys)


def test_missing_solver_on_tui_fallback_raises(capsys):
    mod = load_solver_code("inlet_bc_no_solver")
    vin = _Vin(activate_on_set=False)
    with pytest.raises(RuntimeError, match="solver session was not passed"):
        _apply(mod, vin, solver=None)
    assert not _success_logged(capsys)
