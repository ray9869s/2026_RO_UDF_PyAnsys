"""Fail-closed PBNS blending-0 warm-up: apply point, readback, schemes."""

from __future__ import annotations

import json
import runpy
import sys
import types
from types import SimpleNamespace

import pytest

from helpers import (
    REPO_ROOT,
    SCRIPTS_DIR,
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)

SOLVER_PATH = SCRIPTS_DIR / "solver_code_260616.py"
PRODUCTION_SCHEMES = {
    "flow_scheme": "Coupled",
    "coupled_solver": "global-time-step",
    "gradient_scheme": "least-square-cell-based",
    "mom": "second-order-upwind",
    "pressure": "second-order",
    "species-0": "second-order-upwind",
}


class Leaf:
    def __init__(self, value, active=True):
        self._value = value
        self._active = active

    def is_active(self):
        return self._active

    def get_state(self):
        return self._value


class StateObj:
    def __init__(self, state):
        self._state = state

    def get_state(self):
        return self._state


class NumericsPbns:
    def __init__(self, blending=1.0, active=True, mutate_mom=None, after_readback=None):
        self._blending = blending
        self._mutate_mom = mutate_mom
        self._after_readback = after_readback
        self.first_to_second_order_blending = Leaf(blending, active)

    def get_state(self):
        return {"first_to_second_order_blending": self._blending}

    def __setattr__(self, name, value):
        if name in ("_blending", "_mutate_mom", "_after_readback"):
            object.__setattr__(self, name, value)
            return
        if name == "first_to_second_order_blending":
            existing = self.__dict__.get("first_to_second_order_blending")
            if isinstance(existing, Leaf) and not isinstance(value, Leaf):
                stored = (
                    self._after_readback
                    if self._after_readback is not None
                    else value
                )
                existing._value = stored
                self._blending = stored
                if self._mutate_mom is not None:
                    self._mutate_mom()
                return
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)


class FakeSolution:
    def __init__(
        self,
        blending=1.0,
        active=True,
        schemes=None,
        mutate_mom_to=None,
        after_readback=None,
    ):
        schemes = dict(PRODUCTION_SCHEMES if schemes is None else schemes)
        disc_state = {
            "mom": schemes["mom"],
            "pressure": schemes["pressure"],
        }
        if "species-0" in schemes:
            disc_state["species-0"] = schemes["species-0"]

        def mutate_mom():
            if mutate_mom_to is not None:
                disc_state["mom"] = mutate_mom_to

        self.methods = SimpleNamespace(
            p_v_coupling=StateObj({"flow_scheme": schemes["flow_scheme"]}),
            pseudo_time_method=StateObj(
                {"formulation": {"coupled_solver": schemes["coupled_solver"]}}
            ),
            spatial_discretization=SimpleNamespace(
                gradient_scheme=StateObj(schemes["gradient_scheme"]),
                discretization_scheme=StateObj(disc_state),
            ),
            expert=SimpleNamespace(
                numerics_pbns=NumericsPbns(
                    blending=blending,
                    active=active,
                    mutate_mom=mutate_mom if mutate_mom_to is not None else None,
                    after_readback=after_readback,
                )
            ),
        )


def test_preserve_does_not_write_blending():
    solver_code = load_solver_code("pbns_blend_preserve")
    solution = FakeSolution(blending=1.0)
    outcome = solver_code.apply_pbns_first_to_second_order_blending(
        solution, "preserve"
    )
    assert outcome["status"] == "PRESERVED"
    assert solution.methods.expert.numerics_pbns._blending == 1.0


def test_apply_zero_readback_and_schemes_unchanged():
    solver_code = load_solver_code("pbns_blend_apply")
    solution = FakeSolution(blending=1.0)
    outcome = solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)
    assert outcome["status"] == "APPLIED_CONFIRMED"
    assert outcome["before"] == pytest.approx(1.0)
    assert outcome["after"] == pytest.approx(0.0)
    assert outcome["schemes"] == PRODUCTION_SCHEMES
    assert solver_code.require_pbns_blending_pre_iteration(
        solution, 0.0
    ) == pytest.approx(0.0)


def test_inactive_leaf_is_fail_closed():
    solver_code = load_solver_code("pbns_blend_inactive")
    solution = FakeSolution(active=False)
    with pytest.raises(solver_code.PbnsBlendingError, match="inactive"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)


def test_missing_species_scheme_is_fail_closed():
    solver_code = load_solver_code("pbns_blend_species")
    schemes = dict(PRODUCTION_SCHEMES)
    schemes.pop("species-0")
    solution = FakeSolution(schemes=schemes)
    with pytest.raises(solver_code.PbnsBlendingError, match="species-0"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)


def test_wrong_flow_scheme_is_fail_closed():
    solver_code = load_solver_code("pbns_blend_simple")
    schemes = dict(PRODUCTION_SCHEMES)
    schemes["flow_scheme"] = "SIMPLE"
    solution = FakeSolution(schemes=schemes)
    with pytest.raises(solver_code.PbnsBlendingError, match="flow_scheme"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)


def test_scheme_change_after_set_is_fail_closed():
    solver_code = load_solver_code("pbns_blend_mutate")
    solution = FakeSolution(mutate_mom_to="first-order-upwind")
    with pytest.raises(solver_code.PbnsBlendingError, match="mom"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)


def test_nonzero_requested_is_fail_closed():
    solver_code = load_solver_code("pbns_blend_one")
    solution = FakeSolution()
    with pytest.raises(solver_code.PbnsBlendingError, match="must be 0.0"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 1.0)


@pytest.mark.parametrize("nonfinite", (float("nan"), float("inf"), float("-inf")))
def test_post_set_nonfinite_readback_is_fail_closed(nonfinite):
    solver_code = load_solver_code(f"pbns_blend_post_nonfinite_{nonfinite}")
    solution = FakeSolution(blending=1.0, after_readback=nonfinite)
    with pytest.raises(solver_code.PbnsBlendingError, match="not finite"):
        solver_code.apply_pbns_first_to_second_order_blending(solution, 0.0)


@pytest.mark.parametrize("nonfinite", (float("nan"), float("inf"), float("-inf")))
def test_pre_iteration_nonfinite_readback_is_fail_closed(nonfinite):
    solver_code = load_solver_code(f"pbns_blend_pre_nonfinite_{nonfinite}")
    solution = FakeSolution(blending=nonfinite)
    with pytest.raises(solver_code.PbnsBlendingError, match="not finite"):
        solver_code.require_pbns_blending_pre_iteration(solution, 0.0)


def test_apply_is_after_source_hooks_init_and_gts_before_write_and_iterate():
    text = SOLVER_PATH.read_text(encoding="utf-8")
    source_hook = text.find("fluid_zone_object.sources.enable = True")
    hybrid = text.find("solution.initialization.hybrid_initialize()")
    gts = text.find("gts_scale_result = apply_gts_time_step_size_scale_factor(")
    blend = text.find("blending_result = apply_pbns_first_to_second_order_blending(")
    write_case = text.find("solver.settings.file.write_case(")
    pre_iter = text.find("require_pbns_blending_pre_iteration(solution, 0.0)")
    iterate = text.find("solution.run_calculation.iterate(")
    assert source_hook != -1
    assert hybrid != -1
    assert gts != -1
    assert blend != -1
    assert write_case != -1
    assert pre_iter != -1
    assert iterate != -1
    assert source_hook < hybrid < gts < blend < write_case < pre_iter < iterate


def test_worker_does_not_import_batch_solver_rerun():
    text = SOLVER_PATH.read_text(encoding="utf-8")
    assert "batch_solver_rerun" not in text
    assert "UNAVAILABLE_CONTINUING_WITH_HOTR_ONLY" not in text


def test_blend0_manifest_stamps_blending_value():
    solver_code = load_solver_code("pbns_blend_manifest")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.first_to_second_order_blending = 0.0
    cfg.enable_qoi_convergence_stop = False
    payload = solver_code.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-09-28T00:00:00Z",
    )
    assert payload["solver_settings"]["first_to_second_order_blending"] == 0.0
    assert "disable_membrane_source_terms" not in payload["solver_settings"]


def test_production_manifest_does_not_stamp_blending():
    solver_code = load_solver_code("pbns_blend_manifest_prod")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    payload = solver_code.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-09-28T00:00:00Z",
    )
    assert "first_to_second_order_blending" not in payload["solver_settings"]


def test_early_main_config_block_raises_pbns_blending_error(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path.resolve()))
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("PYFLUENT_SKIP_VALIDATION", "1")
    monkeypatch.delenv("PYFLUENT_RUN_CONFIG", raising=False)
    monkeypatch.setenv(
        "PYFLUENT_RUN_OVERRIDES",
        json.dumps(
            {
                "family": "pillar",
                "geo_id": "P_p100_h00",
                "mesh_id": "max085_min006_cpg5_bl4_peel2",
                "run_id": "u0p2_p6M_blend0",
                "inlet_velocity_value": 0.2,
                "first_to_second_order_blending": 0.0,
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
                f"PbnsBlendingError: {exc}"
            )
        except Exception as exc:
            assert type(exc).__name__ == "PbnsBlendingError"
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
