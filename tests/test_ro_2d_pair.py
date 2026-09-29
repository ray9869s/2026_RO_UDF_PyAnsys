"""LF/HF mesh specification and result comparison. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.mesh import mesh_spec
from ro_2d_pilot.mesh_build import build_quad_mesh
from ro_2d_pilot.pair import PairMismatch, compare_results, format_comparison

D_M = 4.0e-4
L_M = 4.0e-3


def _config(fidelity: str) -> PilotConfig:
    return PilotConfig(d_m=D_M, L_m=L_M, fidelity=fidelity, n_pitches=1)


def test_low_and_high_discretization_for_the_pilot_geometry() -> None:
    low = mesh_spec(_config("low"))
    high = mesh_spec(_config("high"))
    assert low["height_divisions"] == 24
    assert low["cells_across_channel_height"] == 24
    assert low["circumferential_segments"] == 72
    assert low["streamwise_intervals_per_half_pitch"] == 53
    assert low["near_membrane_intervals"] == 3
    assert low["n_side"] == 18
    assert low["n_radial"] == 3
    assert low["topology_cell_count"] == 2868
    assert low["cells_across_diameter_estimate"] == pytest.approx(12.467532467532468)
    assert low["boundary_layers"] == 0
    assert low["boundary_layers_actual"] == 0
    assert low["boundary_layers_requested"] == 0
    assert low["first_layer_height_m"] is None
    assert low["min_size_used_by_mesher"] is False

    assert high["height_divisions"] == 96
    assert high["cells_across_channel_height"] == 97
    assert high["circumferential_segments"] == 292
    assert high["streamwise_intervals_per_half_pitch"] == 213
    assert high["near_membrane_intervals"] == 12
    assert high["n_side"] == 73
    assert high["n_radial"] == 12
    assert high["topology_cell_count"] == 46578
    assert high["cells_across_diameter_estimate"] == pytest.approx(49.87012987012987)
    assert high["near_membrane_spacing_m"] < low["near_membrane_spacing_m"]
    assert high["boundary_layers"] == 0
    assert high["boundary_layers_actual"] == 0
    assert high["boundary_layers_requested"] == 4
    assert high["first_layer_height_m"] is None
    assert high["boundary_layer_treatment"] == "uniform_structured_no_prism_layers"
    assert high["mesh_role"] == (
        "fine reference mesh for initial LF/HF qualification"
    )
    assert high["fidelity_study_status"] == "unvalidated_execution_floor"
    assert high["min_size_used_by_mesher"] is False

    low_mesh = build_quad_mesh(_config("low"))
    high_mesh = build_quad_mesh(_config("high"))
    assert low_mesh.n_cells == 2868
    assert high_mesh.n_cells == 46578
    assert low_mesh.n_cells == low["topology_cell_count"]
    assert high_mesh.n_cells == high["topology_cell_count"]
    assert high_mesh.actual_max_edge_m < low_mesh.actual_max_edge_m
    assert high_mesh.actual_min_edge_m < low_mesh.actual_min_edge_m


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "geo_id": "d0p400000mm_L4p000000mm_h0p770000mm_n1",
        "fidelity": "low",
        "d_m": D_M,
        "L_m": L_M,
        "channel_height_m": 7.7e-4,
        "n_pitches": 1,
        "inlet_velocity_m_s": 0.2,
        "outlet_gauge_pressure_pa": 6.0e6,
        "cell_count": 2868,
        "lmh": 25.6,
        "cp_average": 1.10,
        "pressure_drop_pa": 93.4,
        "pressure_drop_per_length_pa_per_m": 23350.0,
        "mass_balance_rel": 1.0e-4,
        "solver_iterations": 176,
        "solver_wall_time_s": 10.0,
        "total_wall_time_s": 12.0,
    }
    record.update(overrides)
    return record


def test_pair_comparison_reports_cp_excess_and_cost_ratio() -> None:
    report = compare_results(
        _record(),
        _record(
            fidelity="high",
            cell_count=46578,
            lmh=20.0,
            cp_average=1.05,
            pressure_drop_pa=80.0,
            pressure_drop_per_length_pa_per_m=20000.0,
            solver_iterations=400,
            solver_wall_time_s=40.0,
            total_wall_time_s=48.0,
        ),
    )
    assert report["metrics"]["cp_excess"]["low"] == pytest.approx(0.10)
    assert report["metrics"]["cp_excess"]["high"] == pytest.approx(0.05)
    assert report["relative_difference"]["lmh"] == pytest.approx((25.6 - 20.0) / 20.0)
    assert report["relative_difference"]["cp_excess"] == pytest.approx(
        (0.10 - 0.05) / 0.05
    )
    assert report["relative_difference"]["pressure_drop_per_length_pa_per_m"] == (
        pytest.approx((23350.0 - 20000.0) / 20000.0)
    )
    assert report["cost_ratio_solver"] == pytest.approx(10.0 / 40.0)
    assert report["cost_ratio_total_wall"] == pytest.approx(12.0 / 48.0)
    text = format_comparison(report)
    assert "cp_excess" in text
    assert "No acceptance threshold is applied." in text
    assert "good" not in text.lower()
    assert "bad" not in text.lower()


def test_pair_comparison_rejects_a_different_physical_case() -> None:
    with pytest.raises(PairMismatch, match="d_m"):
        compare_results(_record(), _record(fidelity="high", d_m=5.0e-4))
    with pytest.raises(PairMismatch, match="inlet_velocity_m_s"):
        compare_results(
            _record(),
            _record(fidelity="high", inlet_velocity_m_s=0.3),
        )
    with pytest.raises(PairMismatch, match="fidelity"):
        compare_results(_record(fidelity="high"), _record())


def test_compare_script_reads_result_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    low_path = tmp_path / "low.json"
    high_path = tmp_path / "high.json"
    low_path.write_text(json.dumps(_record()), encoding="utf-8")
    high_path.write_text(
        json.dumps(
            _record(
                fidelity="high",
                cp_average=1.05,
                solver_wall_time_s=40.0,
                total_wall_time_s=48.0,
            )
        ),
        encoding="utf-8",
    )
    script = load_module(
        "compare_ro_2d_pair_under_test",
        SCRIPTS_DIR / "compare_ro_2d_pair.py",
    )
    assert script.main(["--low", str(low_path), "--high", str(high_path)]) == 0
    out = capsys.readouterr().out
    assert "cp_excess" in out
    assert "cost_ratio_solver=0.25" in out
    assert script.main(
        ["--low", str(high_path), "--high", str(low_path)]
    ) == 1
