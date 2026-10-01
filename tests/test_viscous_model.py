"""Selectable viscous model. Laminar stays the production default."""

from __future__ import annotations

import pytest

from helpers import (
    load_run_config,
    load_solver_code,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)
from ro.manifest import ManifestError, read_run_manifest, require_viscous_model
from test_manifest import write_test_run
from test_mfbo_parity_solve import load_solve


def _solver_cfg():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    populate_valid_solver_config(cfg)
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2"
    cfg.allow_legacy_mesh_case_name_mismatch = True
    return cfg


class _NearWall:
    def __init__(self, store):
        self._store = store

    def __setattr__(self, name, value):
        if name == "_store":
            object.__setattr__(self, name, value)
            return
        wall = self._store.setdefault("near_wall_treatment", {})
        wall[name] = value


class _Viscous:
    def __init__(self, state):
        object.__setattr__(self, "state", dict(state))
        object.__setattr__(self, "near_wall_treatment", _NearWall(self.state))
        object.__setattr__(self, "assigned", [])

    def get_state(self):
        return dict(self.state)

    def __setattr__(self, name, value):
        if name in ("state", "near_wall_treatment", "assigned"):
            raise AttributeError(name)
        self.assigned.append((name, value))
        self.state[name] = value


class _Models:
    def __init__(self, viscous):
        self.viscous = viscous


class _Setup:
    def __init__(self, viscous):
        self.models = _Models(viscous)


class _SchemeChild:
    def __init__(self, store, name, allowed):
        self.store = store
        self.name = name
        self.allowed = allowed

    def allowed_values(self):
        return list(self.allowed)

    def set_state(self, value):
        self.store[self.name] = value


class _Schemes:
    def __init__(self, state, allowed):
        self.state = dict(state)
        self.allowed = allowed

    def get_state(self):
        return dict(self.state)

    def __getitem__(self, name):
        return _SchemeChild(self.state, name, self.allowed[name])


class _Spatial:
    def __init__(self, schemes):
        self.discretization_scheme = schemes


class _Methods:
    def __init__(self, schemes):
        self.spatial_discretization = _Spatial(schemes)


class _Solution:
    def __init__(self, schemes, equations):
        self.methods = _Methods(schemes)
        self.monitor = type("M", (), {})()
        self.monitor.residual = type("R", (), {"equations": equations})()


class _Eq:
    def __init__(self):
        self.monitor = None
        self.check_convergence = None
        self.absolute_criteria = None
        self.relative_criteria = None
        self._state = {"absolute_criteria": 1.0}

    def get_state(self):
        return dict(self._state)


class _Equations:
    def __init__(self, names):
        self.eqs = {name: _Eq() for name in names}

    def get_state(self):
        return {name: eq.get_state() for name, eq in self.eqs.items()}

    def __getitem__(self, name):
        return self.eqs[name]


def _schemes():
    state = {
        "pressure": "second-order",
        "momentum": "second-order-upwind",
        "k": "first-order-upwind",
        "omega": "first-order-upwind",
        "epsilon": "first-order-upwind",
        "nacl": "second-order-upwind",
    }
    allowed = {name: ["first-order-upwind", "second-order-upwind"] for name in state}
    allowed["pressure"] = ["second-order", "body-force-weighted"]
    return _Schemes(state, allowed)


def test_laminar_validation_accepts_the_production_default():
    cfg = _solver_cfg()
    assert cfg.viscous_model == "laminar"
    assert not hasattr(cfg, "turbulence_residual_target")
    cfg.validate_for_solver()


def test_laminar_forbids_turbulence_suffixes_and_residual_target():
    cfg = _solver_cfg()
    cfg.run_id = "u0p1_p4M_sst"
    with pytest.raises(ValueError, match="_sst"):
        cfg.validate_for_solver()
    cfg.run_id = "u0p1_p4M"
    cfg.turbulence_residual_target = 1.0e-4
    with pytest.raises(ValueError, match="must not be set"):
        cfg.validate_for_solver()


def test_non_laminar_requires_matching_suffix_and_residual_target():
    cfg = _solver_cfg()
    cfg.viscous_model = "k-omega-sst"
    cfg.run_id = "u0p1_p4M"
    with pytest.raises(ValueError, match="_sst"):
        cfg.validate_for_solver()
    cfg.run_id = "u0p1_p4M_sst"
    with pytest.raises(ValueError, match="turbulence_residual_target is required"):
        cfg.validate_for_solver()
    cfg.turbulence_residual_target = 1.0e-4
    cfg.validate_for_solver()

    cfg.viscous_model = "k-epsilon-realizable-ewt"
    cfg.run_id = "u0p1_p4M_sst"
    with pytest.raises(ValueError, match="_rke"):
        cfg.validate_for_solver()
    cfg.run_id = "u0p1_p4M_rke"
    cfg.validate_for_solver()


def test_unknown_viscous_model_is_rejected():
    cfg = _solver_cfg()
    cfg.viscous_model = "les"
    with pytest.raises(ValueError, match="viscous_model"):
        cfg.validate_for_solver()


def test_laminar_setup_sets_nothing_and_reads_schemes():
    solver = load_solver_code()
    viscous = _Viscous({"model": "laminar", "k_omega_model": "sst"})
    schemes = _schemes()
    state, read_back = solver.apply_viscous_setup(
        _Setup(viscous),
        _Solution(schemes, _Equations([])),
        "laminar",
        set_model=True,
    )
    assert viscous.assigned == []
    assert state["model"] == "laminar"
    assert read_back["k"] == "first-order-upwind"
    assert read_back["momentum"] == "second-order-upwind"


def test_laminar_template_that_is_not_laminar_raises():
    solver = load_solver_code()
    viscous = _Viscous({"model": "k-omega", "k_omega_model": "sst"})
    with pytest.raises(RuntimeError, match="laminar"):
        solver.configure_viscous_model(_Setup(viscous), "laminar")
    assert viscous.assigned == []


def test_sst_and_rke_set_then_reread():
    solver = load_solver_code()
    viscous = _Viscous({"model": "laminar"})
    schemes = _schemes()
    state, read_back = solver.apply_viscous_setup(
        _Setup(viscous),
        _Solution(schemes, _Equations([])),
        "k-omega-sst",
        set_model=True,
    )
    assert state["model"] == "k-omega"
    assert state["k_omega_model"] == "sst"
    assert read_back["k"] == "second-order-upwind"
    assert read_back["omega"] == "second-order-upwind"
    assert read_back["epsilon"] == "first-order-upwind"
    assert read_back["momentum"] == "second-order-upwind"

    viscous = _Viscous({"model": "laminar", "near_wall_treatment": {}})
    schemes = _schemes()
    state, read_back = solver.apply_viscous_setup(
        _Setup(viscous),
        _Solution(schemes, _Equations([])),
        "k-epsilon-realizable-ewt",
        set_model=True,
    )
    assert state["model"] == "k-epsilon"
    assert state["k_epsilon_model"] == "realizable"
    assert state["near_wall_treatment"]["wall_treatment"] == "enhanced-wall-treatment"
    assert read_back["k"] == "second-order-upwind"
    assert read_back["epsilon"] == "second-order-upwind"
    assert read_back["omega"] == "first-order-upwind"


def test_scheme_allowed_values_are_asserted_before_setting():
    solver = load_solver_code()
    schemes = _schemes()
    schemes.allowed["k"] = ["first-order-upwind"]
    with pytest.raises(AssertionError, match="second-order-upwind"):
        solver.set_turbulence_schemes_second_order(
            _Solution(schemes, _Equations([])),
            ("k",),
        )
    assert schemes.state["k"] == "first-order-upwind"


def test_restart_asserts_without_setting():
    solver = load_solver_code()
    viscous = _Viscous(
        {
            "model": "k-omega",
            "k_omega_model": "sst",
        }
    )
    schemes = _schemes()
    state, _read_back = solver.apply_viscous_setup(
        _Setup(viscous),
        _Solution(schemes, _Equations([])),
        "k-omega-sst",
        set_model=False,
    )
    assert viscous.assigned == []
    assert state["k_omega_model"] == "sst"
    assert schemes.state["k"] == "first-order-upwind"
    with pytest.raises(RuntimeError, match="k-omega-sst"):
        solver.apply_viscous_setup(
            _Setup(_Viscous({"model": "laminar"})),
            _Solution(_schemes(), _Equations([])),
            "k-omega-sst",
            set_model=False,
        )


def test_residual_criteria_are_unchanged_for_laminar_and_append_turbulence():
    solver = load_solver_code()
    names = [
        "continuity",
        "x-velocity",
        "y-velocity",
        "z-velocity",
        "nacl",
        "k",
        "omega",
    ]
    laminar = _Equations(names)
    solver.apply_residual_criteria(
        _Solution(_schemes(), laminar),
        "nacl",
        1.0e-7,
    )
    assert laminar["nacl"].absolute_criteria == 1.0e-7
    assert laminar["k"].absolute_criteria is None
    assert laminar["k"].check_convergence is None

    turbulent = _Equations(names)
    solver.apply_residual_criteria(
        _Solution(_schemes(), turbulent),
        "nacl",
        1.0e-7,
        ("k", "omega"),
        1.0e-5,
    )
    assert turbulent["continuity"].absolute_criteria == 1.0e-7
    assert turbulent["nacl"].check_convergence is True
    assert turbulent["k"].absolute_criteria == 1.0e-5
    assert turbulent["omega"].absolute_criteria == 1.0e-5
    assert turbulent["k"].check_convergence is True


def test_manifest_payload_records_viscous_model_and_schmidt():
    solver = load_solver_code()
    cfg = _solver_cfg()
    mesh = {"mesh_sha256": "a" * 64}
    laminar = solver.build_run_manifest_payload(cfg, mesh)
    assert laminar["solver_settings"]["viscous_model"] == "laminar"
    assert laminar["solver_settings"]["turbulent_schmidt_number"] is None
    assert "viscous_state" not in laminar["solver_settings"]
    solver.require_viscous_model(laminar)

    cfg.viscous_model = "k-omega-sst"
    turbulent = solver.build_run_manifest_payload(cfg, mesh)
    assert turbulent["solver_settings"]["turbulent_schmidt_number"] == {
        "value": None,
        "reason": "not readable via Fluent 25.1 settings API",
    }


def test_readers_accept_manifests_without_viscous_fields(monkeypatch, tmp_path):
    from ro.manifest import write_run_manifest
    from ro.paths import run_dir
    from test_manifest import FAMILY, GEO_ID, MESH_ID, run_payload

    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    old = write_test_run()
    payload = read_run_manifest(old)
    assert "viscous_model" not in payload["solver_settings"]
    with pytest.raises(ManifestError, match="missing viscous_model"):
        require_viscous_model(payload)

    filled_old = dict(payload)
    old_settings = dict(filled_old["solver_settings"])
    old_settings["viscous_state"] = {"model": "laminar"}
    old_settings["discretization_schemes"] = {"momentum": "second-order-upwind"}
    filled_old["solver_settings"] = old_settings
    write_run_manifest(old, filled_old)
    assert (
        read_run_manifest(old)["solver_settings"]["viscous_state"]["model"]
        == "laminar"
    )

    directory = run_dir(FAMILY, GEO_ID, MESH_ID, "u0p2_p6M_vm")
    directory.mkdir(parents=True)
    first = run_payload()
    first["run_id"] = "u0p2_p6M_vm"
    first["solver_settings"] = dict(first["solver_settings"])
    first["solver_settings"]["viscous_model"] = "laminar"
    first["solver_settings"]["turbulent_schmidt_number"] = None
    write_run_manifest(directory, first)
    assert require_viscous_model(read_run_manifest(directory)) == "laminar"
    second = read_run_manifest(directory)
    second_settings = dict(second["solver_settings"])
    second_settings["viscous_state"] = {"model": "laminar"}
    second_settings["discretization_schemes"] = {"momentum": "second-order-upwind"}
    second["solver_settings"] = second_settings
    write_run_manifest(directory, second)
    filled = read_run_manifest(directory)
    assert filled["solver_settings"]["discretization_schemes"]["momentum"] == (
        "second-order-upwind"
    )
    changed = dict(filled)
    changed_settings = dict(changed["solver_settings"])
    changed_settings["residual_target"] = 1.0e-3
    changed["solver_settings"] = changed_settings
    with pytest.raises(ManifestError, match="solver_settings"):
        write_run_manifest(directory, changed)


def test_parity_laminar_overrides_are_unchanged_and_non_laminar_suffixes_run_id():
    solve = load_solve()
    _case, overrides, _retries, _settle = solve.load_source_case()
    laminar = solve.apply_viscous_model_overrides(overrides, "laminar", None)
    assert laminar is overrides
    assert laminar["case_name"] == solve.SOURCE_RUN_ID
    assert "viscous_model" not in laminar

    with pytest.raises(ValueError, match="only allowed"):
        solve.apply_viscous_model_overrides(overrides, "laminar", 1.0e-4)
    with pytest.raises(ValueError, match="required"):
        solve.apply_viscous_model_overrides(overrides, "k-omega-sst", None)

    sst = solve.apply_viscous_model_overrides(overrides, "k-omega-sst", 1.0e-4)
    assert sst["run_id"] == "u0p2_p6M_sst"
    assert sst["case_name"] == "u0p2_p6M_sst"
    assert sst["viscous_model"] == "k-omega-sst"
    assert sst["turbulence_residual_target"] == 1.0e-4
    assert overrides["case_name"] == solve.SOURCE_RUN_ID

    rke = solve.apply_viscous_model_overrides(
        overrides, "k-epsilon-realizable-ewt", 1.0e-5
    )
    assert rke["run_id"] == "u0p2_p6M_rke"
    assert solve.run_id_for_viscous_model("laminar") == "u0p2_p6M"
