"""Pure-Python checks for the 2D RO pilot planner. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.campaign_geometry import CAMPAIGN_H_M
from ro.convergence_quality import MASS_BALANCE_REL_ABS_MAX
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.paths import DATA_ROOT_ENV, assert_data_root_allowed, data_root
from ro_2d_pilot.plan import build_plan, materialize, plans_for_fidelities, shared_design_payload
from ro_2d_pilot.record import RESULT_FIELDS, lmh_from_mass_balance
from ro_2d_pilot.udf_gate import (
    BLOCKER_INLET_Z,
    BLOCKER_RP_3D_FENCE,
    BLOCKER_Y1_THIRD_COMPONENT,
    assess_udf_source,
)


D_M = 4.0e-4
L_M = 4.0e-3


def _quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.delenv(DATA_ROOT_ENV, raising=False)


def test_package_imports_without_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet_env(monkeypatch)
    import ro_2d_pilot
    from ro_2d_pilot.plan import build_plan as build

    assert ro_2d_pilot.__version__ == "0.1.0"
    plan = build(PilotConfig(d_m=D_M, L_m=L_M, fidelity="low"))
    assert plan["run_id"] == "u0p2_p6M"


def test_geometry_rejects_impossible_sizes() -> None:
    with pytest.raises(ValueError, match="channel height"):
        PilotConfig(d_m=CAMPAIGN_H_M, L_m=L_M, fidelity="low")
    with pytest.raises(ValueError, match="overlap"):
        PilotConfig(d_m=D_M, L_m=D_M, fidelity="high")
    with pytest.raises(ValueError, match="fidelity"):
        PilotConfig(d_m=D_M, L_m=L_M, fidelity="medium")
    with pytest.raises(ValueError, match="n_pitches"):
        PilotConfig(d_m=D_M, L_m=L_M, fidelity="low", n_pitches=True)  # type: ignore[arg-type]


def test_geo_id_is_deterministic_and_encodes_lengths() -> None:
    first = build_plan(PilotConfig(d_m=D_M, L_m=L_M, fidelity="low"))
    second = build_plan(PilotConfig(d_m=D_M, L_m=L_M, fidelity="low"))
    assert first["geo_id"] == second["geo_id"] == (
        "d0p400000mm_L4p000000mm_h0p770000mm_n1"
    )
    geometry = first["geometry"]
    assert geometry["obstacles"] == (
        {"center_x_m": 0.5 * L_M, "center_y_m": 0.0, "diameter_m": D_M},
    )
    assert geometry["y_min_m"] == pytest.approx(-0.5 * CAMPAIGN_H_M)
    assert geometry["membrane_blocked_area_frac"] == 0.0


def test_low_and_high_share_physics_and_differ_in_mesh() -> None:
    plans = plans_for_fidelities(d_m=D_M, L_m=L_M)
    assert set(plans) == {"low", "high"}
    assert shared_design_payload(plans["low"]) == shared_design_payload(plans["high"])
    low_mesh = plans["low"]["mesh"]
    high_mesh = plans["high"]["mesh"]
    assert low_mesh["max_size_m"] > high_mesh["max_size_m"]
    assert low_mesh["boundary_layers"] == 0
    assert high_mesh["boundary_layers"] == 0
    assert high_mesh["boundary_layers_requested"] == 4
    assert low_mesh["cell_count"] is None
    assert plans["low"]["stages"]["generate_mesh"] == "ready"
    assert plans["low"]["stages"]["compile_udf"] == "fluent_only"
    assert plans["low"]["stages"]["solve"] == "fluent_only"
    assert plans["low"]["physics"]["udf_2d_status"] == (
        "source_ready_fluent_unverified"
    )
    blockers = plans["low"]["physics"]["udf_2d_blockers"]
    assert BLOCKER_RP_3D_FENCE in blockers
    assert BLOCKER_Y1_THIRD_COMPONENT in blockers
    assert BLOCKER_INLET_Z in blockers


def test_udf_gate_ignores_an_unrelated_source() -> None:
    assert assess_udf_source("int main(){ return 0; }") == ()


def test_result_record_schema_and_verdicts() -> None:
    plan = build_plan(PilotConfig(d_m=D_M, L_m=L_M, fidelity="high"))
    empty = plan["result"]
    assert tuple(empty) == RESULT_FIELDS
    assert empty["validity"] == "not_run"
    assert empty["lmh"] is None
    assert empty["pressure_drop_per_length_pa_per_m"] is None
    assert empty["convergence_status"] == "not_run"

    from ro_2d_pilot.record import build_result_record

    valid = build_result_record(
        plan,
        {
            "cell_count": 1000,
            "lmh": 20.0,
            "cp_average": None,
            "pressure_drop_pa": 1000.0,
            "solver_iterations": 40,
            "solver_wall_time_s": 1.5,
            "total_wall_time_s": 2.0,
            "convergence_status": "max_iter_reached",
            "mass_balance_rel": MASS_BALANCE_REL_ABS_MAX / 2,
        },
    )
    assert valid["validity"] == "valid"
    assert valid["cp_average"] is None
    assert valid["convergence_status"] == "max_iter_reached"
    assert valid["pressure_drop_per_length_pa_per_m"] == pytest.approx(
        1000.0 / L_M
    )

    invalid = build_result_record(
        plan,
        {
            "cell_count": 1000,
            "lmh": 20.0,
            "pressure_drop_pa": 1000.0,
            "convergence_status": "residual_converged",
            "mass_balance_rel": MASS_BALANCE_REL_ABS_MAX,
        },
    )
    assert invalid["validity"] == "invalid"
    diverged = build_result_record(
        plan,
        {
            "cell_count": 1000,
            "lmh": 20.0,
            "pressure_drop_pa": 1000.0,
            "convergence_status": "diverged",
            "mass_balance_rel": 0.0,
        },
    )
    assert diverged["validity"] == "invalid"
    unknown = build_result_record(
        plan,
        {
            "cell_count": 1000,
            "lmh": 20.0,
            "pressure_drop_pa": 1000.0,
            "convergence_status": "iteration_unknown",
            "mass_balance_rel": 0.0,
        },
    )
    assert unknown["validity"] == "invalid"


def test_lmh_helper_uses_campaign_mass_balance() -> None:
    lmh = lmh_from_mass_balance(
        mass_in_kg_s=1.0,
        mass_out_kg_s=-0.9,
        density_kg_m3=998.2,
        membrane_area_m2=1.0,
    )
    assert lmh > 0.0


def test_data_root_refuses_repo_campaign_tree_and_relative_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quiet_env(monkeypatch)
    with pytest.raises(ValueError, match="must be set"):
        data_root()
    with pytest.raises(ValueError, match="absolute"):
        assert_data_root_allowed(Path("cases"))
    with pytest.raises(ValueError, match="Windows drive"):
        assert_data_root_allowed(Path("C:/ro_2d_data"), host_os_name="posix")
    with pytest.raises(ValueError, match="git repository"):
        assert_data_root_allowed(REPO_ROOT / "ro_2d_pilot_out")

    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="3D RO_DATA_ROOT"):
        data_root()
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "runs" / "pilot"))
    with pytest.raises(ValueError, match="runs/"):
        data_root()

    allowed = tmp_path / "sibling_ro_2d"
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path / "campaign"))
    monkeypatch.setenv(DATA_ROOT_ENV, str(allowed))
    assert data_root() == allowed


def test_materialize_writes_only_under_the_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quiet_env(monkeypatch)
    plans = plans_for_fidelities(d_m=D_M, L_m=L_M)
    with pytest.raises(ValueError, match="git repository"):
        materialize(plans["low"], REPO_ROOT / "ro_2d_pilot_out")
    assert not (REPO_ROOT / "ro_2d_pilot_out").exists()

    written_low = materialize(plans["low"], tmp_path)
    written_high = materialize(plans["high"], tmp_path)
    assert set(path.name for path in tmp_path.iterdir()) == {
        "geometries",
        "meshes",
        "runs",
    }
    assert written_low["geometry"] == written_high["geometry"]
    assert written_low["geometry"].read_text(encoding="utf-8") == (
        written_high["geometry"].read_text(encoding="utf-8")
    )
    low_result = json.loads(written_low["result"].read_text(encoding="utf-8"))
    high_mesh = json.loads(written_high["mesh"].read_text(encoding="utf-8"))
    assert low_result["fidelity"] == "low"
    assert low_result["validity"] == "not_run"
    assert high_mesh["fidelity"] == "high"
    assert high_mesh["boundary_layers"] == 0
    assert high_mesh["boundary_layers_requested"] == 4
    for path in (*written_low.values(), *written_high.values()):
        text = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text
        assert "C:" not in text
        assert "C:\\" not in text


def test_plan_script_prints_json_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _quiet_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    script = load_module("ro_2d_plan_under_test", SCRIPTS_DIR / "ro_2d_plan.py")
    assert script.main(["--d-m", str(D_M), "--l-m", str(L_M), "--fidelity", "low"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fidelity"] == "low"
    assert payload["relative_paths"]["run_dir"].startswith("runs/")
    assert list(tmp_path.iterdir()) == []
