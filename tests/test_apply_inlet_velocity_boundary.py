"""Inlet BC success is logged only after Settings readback matches the request."""

from __future__ import annotations

import pytest

from helpers import load_solver_code

UDF_NAME = "RO_inlet_profile"
ZONE = "inlet"
U_MEAN = 0.2
# Fluent 25.1.0 velocity_magnitude get_state from the D0817_a60/a30 logs.
PILOT_VELOCITY_MAGNITUDE = {"option": "value", "value": 0.2}


def fluent_leaf(value):
    """Wrap a scalar the way Fluent 25.1.0 settings get_state does."""
    if isinstance(value, dict) and "option" in value:
        return value
    return {"option": "value", "value": value}


class _State:
    def __init__(self, state):
        self._state = fluent_leaf(state)

    def get_state(self):
        return self._state

    def set_state(self, state):
        self._state = fluent_leaf(state)

    def __call__(self):
        return self._state


class _Component:
    def __init__(self):
        self.option = _State("value")
        self.udf = _State("")
        self._numeric = fluent_leaf(0.0)

    @property
    def value(self):
        return self._numeric

    @value.setter
    def value(self, new):
        self._numeric = fluent_leaf(new)


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
        return fluent_leaf(self._state)

    def get_state(self):
        return fluent_leaf(self._state)

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
        self._payload = fluent_leaf(value)

    def get_state(self):
        return dict(self._payload)

    def __call__(self):
        return self.get_state()

    @property
    def value(self):
        return dict(self._payload)

    @value.setter
    def value(self, new):
        self._payload = fluent_leaf(new)


class _FrozenMagnitude:
    def __init__(self, value):
        object.__setattr__(self, "_payload", fluent_leaf(float(value)))

    def get_state(self):
        return dict(self._payload)

    @property
    def value(self):
        return dict(self._payload)

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
    assert vin.momentum.velocity_magnitude.value == PILOT_VELOCITY_MAGNITUDE
    assert (
        mod.unwrap_fluent_setting(vin.momentum.velocity_magnitude.value)
        == U_MEAN
    )


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


def test_inlet_fixtures_are_fluent_wrapped_dicts_not_scalars():
    vin = _Vin(magnitude=0.2)
    mag = vin.momentum.velocity_magnitude
    assert mag.value == PILOT_VELOCITY_MAGNITUDE
    assert mag.get_state() == PILOT_VELOCITY_MAGNITUDE
    assert isinstance(mag.value, dict)
    comps = vin.momentum.velocity_components
    assert comps[1].value == {"option": "value", "value": 0.0}
    assert comps[0].option.get_state() == {"option": "value", "value": "value"}
    assert vin.momentum.velocity_specification_method.get_state() == {
        "option": "value",
        "value": "Magnitude and Direction",
    }


def test_unwrap_fluent_setting_pilot_log_shape():
    mod = load_solver_code("unwrap_pilot_log")
    assert mod.unwrap_fluent_setting(PILOT_VELOCITY_MAGNITUDE) == U_MEAN
    nested = {"option": "value", "value": PILOT_VELOCITY_MAGNITUDE}
    assert mod.unwrap_fluent_setting(nested) == U_MEAN
    assert mod.unwrap_fluent_setting(U_MEAN) == U_MEAN


def test_read_inlet_plug_magnitude_from_pilot_wrapped_dict():
    mod = load_solver_code("plug_wrapped_read")
    vin = _Vin(magnitude=0.2)
    assert vin.momentum.velocity_magnitude.value == PILOT_VELOCITY_MAGNITUDE
    assert mod.read_inlet_plug_magnitude(vin) == pytest.approx(U_MEAN)


class _GetStateMagnitude:
    """Live path: .value is a callable child; get_state returns the wrapped dict."""

    def __init__(self, value):
        self._payload = fluent_leaf(value)

    def get_state(self):
        return dict(self._payload)

    def value(self):
        return dict(self._payload)


def test_read_inlet_plug_magnitude_via_get_state_wrapped_dict():
    mod = load_solver_code("plug_get_state_wrapped")
    vin = _Vin(magnitude=0.2)
    vin.momentum.velocity_magnitude = _GetStateMagnitude(0.2)
    assert callable(vin.momentum.velocity_magnitude.value)
    assert mod.read_inlet_plug_magnitude(vin) == pytest.approx(U_MEAN)


def test_read_inlet_profile_state_from_wrapped_components():
    mod = load_solver_code("profile_wrapped_read")
    vin = _Vin()
    _stamp_profile(vin)
    observed = mod.read_inlet_profile_state(vin)
    assert observed["specification"] == "Components"
    assert observed["x_option"] == "udf"
    assert observed["x_udf"] == UDF_NAME
    assert observed["y_value"] == pytest.approx(0.0)
    assert observed["z_value"] == pytest.approx(0.0)
    assert mod.inlet_profile_readback_error(observed, UDF_NAME) is None
    assert vin.momentum.velocity_components[0].option.get_state() == {
        "option": "value",
        "value": "udf",
    }
    assert vin.momentum.velocity_components[0].udf.get_state() == {
        "option": "value",
        "value": UDF_NAME,
    }
    assert vin.momentum.velocity_components[1].value == {
        "option": "value",
        "value": 0.0,
    }


def test_read_inlet_profile_state_udf_form_component_dict():
    mod = load_solver_code("profile_udf_form")
    vin = _Vin()
    spec = vin.momentum.velocity_specification_method
    spec._state = "Components"
    comps = vin.momentum.velocity_components
    comps._active = True
    udf_form = {"option": "udf", "udf": UDF_NAME}
    comps[0].option = _State(udf_form)
    comps[0].udf = _State(udf_form)
    comps[1].value = fluent_leaf(0.0)
    comps[2].value = fluent_leaf(0.0)
    observed = mod.read_inlet_profile_state(vin)
    assert observed["x_option"] == "udf"
    assert observed["x_udf"] == UDF_NAME
    assert observed["y_value"] == pytest.approx(0.0)
    assert observed["z_value"] == pytest.approx(0.0)
    assert mod.inlet_profile_readback_error(observed, UDF_NAME) is None

