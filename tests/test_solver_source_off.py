"""Fail-closed source-off diagnostic: isolation, disable, and readback."""

from __future__ import annotations

import json
import runpy
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import (
    REPO_ROOT,
    SCRIPTS_DIR,
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)

TERM_KEYS = ("mass", "species-0", "x-momentum", "y-momentum", "z-momentum")
PROFILE_UDF = "inlet_x_velocity_profile::libudf"
SOLVER_PATH = SCRIPTS_DIR / "solver_code_260616.py"


class EnableLeaf:
    def __init__(self, value):
        self._value = value

    def get_state(self):
        return self._value

    def is_active(self):
        return True


class Entry:
    def __init__(self, active=True):
        self._active = active

    def is_active(self):
        return self._active

    def resize(self, n):
        raise AssertionError("source-off must not call resize on a source entry")

    def get_state(self):
        return {"option": "udf"}


class Term:
    def __init__(self, n=1, active=True, entry_active=True):
        self._active = active
        self._entries = [Entry(entry_active) for _ in range(n)]

    def is_active(self):
        return self._active

    def __len__(self):
        return len(self._entries)

    def __getitem__(self, index):
        return self._entries[index]

    def resize(self, n):
        raise AssertionError("source-off must not call resize on a source term")


class Terms:
    def __init__(
        self,
        keys=TERM_KEYS,
        *,
        terms_active=True,
        term_active=True,
        entry_active=True,
        n=1,
    ):
        self._active = terms_active
        self._items = {
            key: Term(n, term_active, entry_active) for key in keys
        }

    def is_active(self):
        return self._active

    def get_state(self):
        return {key: {} for key in self._items}

    def __getitem__(self, key):
        return self._items[key]


class Sources:
    def __init__(self, enable=True, **term_kwargs):
        self._enable = EnableLeaf(enable)
        self.terms = Terms(**term_kwargs)

    @property
    def enable(self):
        return self._enable

    @enable.setter
    def enable(self, value):
        if hasattr(value, "get_state"):
            value = value.get_state()
        self._enable = EnableLeaf(False if value is False else value)

    def get_state(self):
        return {
            "enable": self._enable.get_state(),
            "terms": self.terms.get_state(),
        }


class FrozenEnableSources(Sources):
    @property
    def enable(self):
        return self._enable

    @enable.setter
    def enable(self, value):
        return


class BoomEnable:
    def get_state(self):
        raise RuntimeError("enable unreadable")


class BoomSources:
    def __init__(self):
        self.enable = BoomEnable()
        self.terms = Terms(terms_active=False)

    def get_state(self):
        return {"enable": True}


class FluidZone:
    def __init__(self, sources):
        self.sources = sources


class InletLeaf:
    def __init__(self, value):
        self._value = value

    def get_state(self):
        return self._value

    def __call__(self):
        return self._value


class InletComponent:
    def __init__(self, option, udf="", value=0.0):
        self.option = InletLeaf(option)
        self.udf = InletLeaf(udf)
        self.value = value


class InletComponents:
    def __init__(self, udf_name):
        self._items = [
            InletComponent("udf", udf=udf_name),
            InletComponent("value", value=0.0),
            InletComponent("value", value=0.0),
        ]

    def is_active(self):
        return True

    def __getitem__(self, index):
        return self._items[index]


def make_setup(zone_names=("fluid-1", "fluid-2"), sources_factory=None, **term_kwargs):
    factory = sources_factory or (lambda: Sources(**term_kwargs))
    zones = {name: FluidZone(factory()) for name in zone_names}
    return SimpleNamespace(cell_zone_conditions=SimpleNamespace(fluid=zones))


def make_inlet_setup(zone_names=("inlet",), udf_name=PROFILE_UDF):
    inlets = {
        name: SimpleNamespace(
            momentum=SimpleNamespace(
                velocity_specification_method=InletLeaf("Components"),
                velocity_components=InletComponents(udf_name),
            )
        )
        for name in zone_names
    }
    return SimpleNamespace(
        boundary_conditions=SimpleNamespace(velocity_inlet=inlets)
    )


def combined_setup(fluid_kwargs=None, inlet_kwargs=None):
    fluid = make_setup(**(fluid_kwargs or {}))
    inlet = make_inlet_setup(**(inlet_kwargs or {}))
    return SimpleNamespace(
        cell_zone_conditions=fluid.cell_zone_conditions,
        boundary_conditions=inlet.boundary_conditions,
    )


def test_membrane_source_term_keys_match_production_five_terms():
    solver_code = load_solver_code("source_off_keys")
    assert solver_code.membrane_source_term_keys(0) == TERM_KEYS


def test_disable_sets_enable_false_when_terms_inactive():
    solver_code = load_solver_code("source_off_disable_ok")
    setup = make_setup(terms_active=False)
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1", "fluid-2"], TERM_KEYS
    )
    for name in ("fluid-1", "fluid-2"):
        sources = setup.cell_zone_conditions.fluid[name].sources
        assert sources.enable.get_state() is False
        solver_code.require_membrane_sources_disabled(
            setup.cell_zone_conditions.fluid[name], name, TERM_KEYS
        )


def test_disable_accepts_absent_term_keys():
    solver_code = load_solver_code("source_off_absent_terms")
    setup = make_setup(keys=())
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1"], TERM_KEYS
    )
    assert (
        setup.cell_zone_conditions.fluid["fluid-1"].sources.enable.get_state()
        is False
    )


def test_disable_aborts_when_enable_stays_true():
    solver_code = load_solver_code("source_off_enable_stuck")
    setup = make_setup(sources_factory=FrozenEnableSources)
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="sources.enable readback"
    ):
        solver_code.disable_membrane_source_terms_on_fluid_zones(
            setup, ["fluid-1"], TERM_KEYS
        )


def test_disable_aborts_when_terms_remain_active_without_resize():
    solver_code = load_solver_code("source_off_terms_active")
    setup = make_setup(enable=True, terms_active=True, term_active=True)
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="still present and active"
    ):
        solver_code.disable_membrane_source_terms_on_fluid_zones(
            setup, ["fluid-1"], TERM_KEYS
        )


def test_readback_aborts_when_enable_get_state_fails():
    solver_code = load_solver_code("source_off_enable_unreadable")
    zone = FluidZone(BoomSources())
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="get_state failed"
    ):
        solver_code.require_membrane_sources_disabled(zone, "fluid-1", TERM_KEYS)


def test_every_fluid_zone_must_read_back_false():
    solver_code = load_solver_code("source_off_all_zones")
    setup = make_setup(terms_active=False)
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1", "fluid-2"], TERM_KEYS
    )
    setup.cell_zone_conditions.fluid["fluid-2"].sources.enable = True
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="fluid-2"
    ):
        solver_code.require_all_fluid_membrane_sources_disabled(
            setup, ["fluid-1", "fluid-2"], TERM_KEYS
        )


def test_empty_fluid_zone_list_is_fail_closed():
    solver_code = load_solver_code("source_off_no_zones")
    setup = make_setup()
    with pytest.raises(solver_code.MembraneSourceOffError, match="no fluid zones"):
        solver_code.disable_membrane_source_terms_on_fluid_zones(
            setup, [], TERM_KEYS
        )
    with pytest.raises(solver_code.MembraneSourceOffError, match="no fluid zones"):
        solver_code.require_all_fluid_membrane_sources_disabled(
            setup, [], TERM_KEYS
        )


def test_pre_iteration_readback_requires_sources_off_and_inlet_hooked(capfd):
    solver_code = load_solver_code("source_off_pre_iter")
    setup = combined_setup(fluid_kwargs={"terms_active": False})
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1", "fluid-2"], TERM_KEYS
    )
    solver_code.require_source_off_pre_iteration_state(
        setup,
        ["fluid-1", "fluid-2"],
        TERM_KEYS,
        ["inlet"],
        PROFILE_UDF,
    )
    captured = capfd.readouterr()
    assert "nonphysical" in captured.out
    assert "SOURCE-OFF DIAGNOSTIC" in captured.out


def test_pre_iteration_aborts_if_qoi_stop_objects_exist():
    solver_code = load_solver_code("source_off_qoi_objects")
    setup = combined_setup(fluid_kwargs={"terms_active": False})
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1", "fluid-2"], TERM_KEYS
    )
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="QoI stop objects"
    ):
        solver_code.require_source_off_pre_iteration_state(
            setup,
            ["fluid-1", "fluid-2"],
            TERM_KEYS,
            ["inlet"],
            PROFILE_UDF,
            qoi_convergence_object_names=["lmh_udm_avg"],
        )


def test_pre_iteration_aborts_if_inlet_profile_unhooked():
    solver_code = load_solver_code("source_off_inlet")
    setup = combined_setup(
        fluid_kwargs={"terms_active": False},
        inlet_kwargs={"udf_name": "wrong_profile::libudf"},
    )
    solver_code.disable_membrane_source_terms_on_fluid_zones(
        setup, ["fluid-1", "fluid-2"], TERM_KEYS
    )
    with pytest.raises(
        solver_code.MembraneSourceOffError, match="profile readback failed"
    ):
        solver_code.require_source_off_pre_iteration_state(
            setup,
            ["fluid-1", "fluid-2"],
            TERM_KEYS,
            ["inlet"],
            PROFILE_UDF,
        )


def test_source_off_validation_requires_qoi_stop_off():
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    assert cfg.disable_membrane_source_terms is False
    cfg.disable_membrane_source_terms = True
    cfg.enable_qoi_convergence_stop = True
    with pytest.raises(ValueError, match="disable_membrane_source_terms"):
        cfg.validate_for_solver()
    cfg.enable_qoi_convergence_stop = False
    cfg.validate_for_solver()


def test_early_main_config_block_raises_membrane_source_off_error(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path.resolve()))
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("PYFLUENT_SKIP_VALIDATION", "1")
    monkeypatch.delenv("PYFLUENT_RUN_CONFIG", raising=False)
    monkeypatch.setenv(
        "PYFLUENT_RUN_OVERRIDES",
        json.dumps(
            {
                "family": "diamond",
                "geo_id": "D0817_a30",
                "mesh_id": "max085_min006_cpg5_bl4_peel2",
                "run_id": "u0p3_p6M_src0",
                "inlet_velocity_value": 0.3,
                "disable_membrane_source_terms": True,
                "enable_qoi_convergence_stop": True,
                "use_inlet_velocity_profile": True,
            }
        ),
    )
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    saved_main = sys.modules.get("__main__")
    sys.modules.update(stubs)
    try:
        try:
            runpy.run_path(str(SOLVER_PATH), run_name="__main__")
        except NameError as exc:
            pytest.fail(
                "early __main__ config block raised NameError instead of "
                f"MembraneSourceOffError: {exc}"
            )
        except Exception as exc:
            assert type(exc).__name__ == "MembraneSourceOffError"
            assert "enable_qoi_convergence_stop=False" in str(exc)
        else:
            pytest.fail("early __main__ config block did not raise")
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        if saved_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = saved_main


def test_source_off_manifest_labels_udm_nonphysical():
    solver_code = load_solver_code("source_off_manifest")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.use_inlet_velocity_profile = True
    cfg.disable_membrane_source_terms = True
    payload = solver_code.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-09-26T00:00:00Z",
    )
    assert payload["solver_settings"]["disable_membrane_source_terms"] is True
    assert (
        payload["solver_settings"]["membrane_udm_lmh_cp"]
        == "nonphysical_source_off"
    )


def test_production_manifest_does_not_stamp_source_off():
    solver_code = load_solver_code("source_off_manifest_prod")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    payload = solver_code.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-09-26T00:00:00Z",
    )
    assert "disable_membrane_source_terms" not in payload["solver_settings"]
    assert "membrane_udm_lmh_cp" not in payload["solver_settings"]


def test_worker_requires_source_off_readback_before_setup_write_and_iterate():
    text = Path(SOLVER_PATH).read_text(encoding="utf-8")
    hook_section = text.split(
        "##### [13] Enable and Hook Cell Zone UDF Source Terms #####", 1
    )[1].split("##### [14]", 1)[0]
    write_section = text.split("##### [16] Save Setup Case #####", 1)[1].split(
        "##### [17] Run Calculation #####", 1
    )[0]
    iterate_prefix = text.split("##### [17] Run Calculation #####", 1)[1].split(
        "solution.run_calculation.iterate", 1
    )[0]
    assert "disable_membrane_source_terms_on_fluid_zones(" in hook_section
    assert "No resize(0) fallback." in hook_section
    assert "require_source_off_pre_iteration_state(" in write_section
    assert "require_source_off_pre_iteration_state(" in iterate_prefix
    assert "resize(0)" not in hook_section.replace("No resize(0) fallback.", "")
