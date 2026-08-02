"""Tests for verified under-relaxation controls in the fresh-solve worker."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import load_solver_code


class StateLeaf:
    def __init__(self, **values):
        object.__setattr__(self, "_state", dict(values))

    def get_state(self):
        return dict(self._state)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._state[name] = value


class IgnoringStateLeaf(StateLeaf):
    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)


class DictState:
    def __init__(self, **values):
        self.state = dict(values)

    def get_state(self):
        return dict(self.state)

    def __setitem__(self, key, value):
        self.state[key] = value


def make_solver():
    p_v_controls = StateLeaf(
        explicit_pressure_under_relaxation=0.5,
        explicit_momentum_under_relaxation=0.75,
    )
    species_implicit = StateLeaf(implicit_under_relaxation_factor=1.0)
    species_relaxation = DictState(nacl=1.0, **{"species-0": 1.0})

    class GlobalDt:
        def __init__(self):
            self._items = {
                "species-0": species_implicit,
                "nacl": StateLeaf(implicit_under_relaxation_factor=1.0),
            }

        def get_state(self):
            return {
                key: leaf.get_state()
                for key, leaf in self._items.items()
            }

        def __getitem__(self, key):
            return self._items[key]

    solution = SimpleNamespace(
        controls=SimpleNamespace(
            p_v_controls=p_v_controls,
            pseudo_time_explicit_relaxation_factor=SimpleNamespace(
                global_dt_pseudo_relax=species_relaxation
            ),
            advanced=SimpleNamespace(
                expert=SimpleNamespace(
                    pseudo_time_method_usage=SimpleNamespace(
                        global_dt=GlobalDt()
                    )
                )
            ),
        )
    )
    solver = SimpleNamespace(
        settings=SimpleNamespace(solution=solution)
    )
    return (
        solver,
        p_v_controls,
        species_relaxation,
        solution.controls.advanced.expert.pseudo_time_method_usage.global_dt,
    )

def test_verified_leaf_reports_confirmed_readback():
    solver_code = load_solver_code("solver_relaxation_leaf")
    parent = StateLeaf(explicit_pressure_under_relaxation=0.5)

    outcome = solver_code.set_and_verify_leaf(
        parent,
        "explicit_pressure_under_relaxation",
        0.2,
        "pressure",
    )

    assert outcome == {
        "label": "pressure",
        "requested": 0.2,
        "status": "APPLIED_CONFIRMED",
        "before": 0.5,
        "after": 0.2,
    }


def test_verified_leaf_warns_when_setter_is_silently_ignored(capsys):
    solver_code = load_solver_code("solver_relaxation_ignored")
    parent = IgnoringStateLeaf(explicit_pressure_under_relaxation=0.5)

    outcome = solver_code.set_and_verify_leaf(
        parent,
        "explicit_pressure_under_relaxation",
        0.2,
        "pressure",
    )

    assert outcome["status"] == "WARN_APPLY_URF_FAILED"
    assert outcome["after"] == 0.5
    assert "WARN_APPLY_URF_FAILED" in capsys.readouterr().out


def test_baseline_profile_does_not_access_solver_settings():
    solver_code = load_solver_code("solver_relaxation_baseline")
    result = solver_code.apply_real_under_relaxation(
        solver=object(),
        profile="baseline",
        species_name="nacl",
    )
    assert result == {"profile": "baseline", "applied": []}


def test_conservative_profile_applies_and_confirms_all_controls():
    solver_code = load_solver_code("solver_relaxation_conservative")
    solver, p_v_controls, species_relaxation, _global_dt = make_solver()

    result = solver_code.apply_real_under_relaxation(
        solver,
        "conservative",
        "nacl",
    )

    assert p_v_controls.get_state() == {
        "explicit_pressure_under_relaxation": 0.2,
        "explicit_momentum_under_relaxation": 0.3,
    }
    assert species_relaxation.get_state()["nacl"] == 0.5
    assert [item["status"] for item in result["applied"]] == [
        "APPLIED_CONFIRMED",
        "APPLIED_CONFIRMED",
        "APPLIED_CONFIRMED",
    ]


def test_strong_profile_values_match_rerun_presets():
    solver_code = load_solver_code("solver_relaxation_strong")
    assert solver_code.RELAXATION_PROFILES["strong"] == {
        "explicit_pressure_under_relaxation": 0.1,
        "explicit_momentum_under_relaxation": 0.2,
        "species_pseudo_relaxation": 0.3,
    }


def test_species_implicit_preserve_does_not_touch_leaf():
    solver_code = load_solver_code("solver_relaxation_implicit_preserve")
    _solver, _p_v, _explicit, global_dt = make_solver()
    before = global_dt["species-0"].get_state()
    outcome = solver_code.apply_species_implicit_under_relaxation(
        solution=SimpleNamespace(
            controls=SimpleNamespace(
                advanced=SimpleNamespace(
                    expert=SimpleNamespace(
                        pseudo_time_method_usage=SimpleNamespace(
                            global_dt=global_dt
                        )
                    )
                )
            )
        ),
        species_name="nacl",
        value="preserve",
    )
    assert outcome["status"] == "PRESERVED"
    assert global_dt["species-0"].get_state() == before


def test_species_implicit_applies_species0_key_with_readback():
    solver_code = load_solver_code("solver_relaxation_implicit_apply")
    _solver, _p_v, _explicit, global_dt = make_solver()
    solution = SimpleNamespace(
        controls=SimpleNamespace(
            advanced=SimpleNamespace(
                expert=SimpleNamespace(
                    pseudo_time_method_usage=SimpleNamespace(global_dt=global_dt)
                )
            )
        )
    )
    outcome = solver_code.apply_species_implicit_under_relaxation(
        solution=solution,
        species_name="nacl",
        value=0.5,
    )
    assert outcome["status"] == "APPLIED_CONFIRMED"
    assert outcome["matched_key"] == "species-0"
    assert global_dt["species-0"].get_state()[
        "implicit_under_relaxation_factor"
    ] == 0.5


def test_species_implicit_falls_back_to_configured_species_name():
    solver_code = load_solver_code("solver_relaxation_implicit_fallback")

    class GlobalDtNaclOnly:
        def __init__(self):
            self._items = {
                "nacl": StateLeaf(implicit_under_relaxation_factor=1.0),
            }

        def get_state(self):
            return {
                key: leaf.get_state()
                for key, leaf in self._items.items()
            }

        def __getitem__(self, key):
            return self._items[key]

    global_dt = GlobalDtNaclOnly()
    solution = SimpleNamespace(
        controls=SimpleNamespace(
            advanced=SimpleNamespace(
                expert=SimpleNamespace(
                    pseudo_time_method_usage=SimpleNamespace(global_dt=global_dt)
                )
            )
        )
    )
    outcome = solver_code.apply_species_implicit_under_relaxation(
        solution=solution,
        species_name="nacl",
        value=0.5,
    )
    assert outcome["matched_key"] == "nacl"
    assert outcome["status"] == "APPLIED_CONFIRMED"
