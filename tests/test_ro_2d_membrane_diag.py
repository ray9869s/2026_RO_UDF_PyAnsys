"""Membrane-length and flux-consistency checks. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.execute import run_case
from ro_2d_pilot.fluent_session import FluentUnavailable, _extract_reports
from ro_2d_pilot.membrane_diag import (
    divergence_phase,
    flux_consistency,
    format_membrane_comparison,
    membrane_geometry_diagnostics,
    source_ramp_factor,
)
from ro_2d_pilot.mesh_build import QuadMesh, build_quad_mesh
from ro_2d_pilot.record import build_result_record
from ro_2d_pilot.plan import build_plan

D_M = 4.0e-4
L_M = 4.0e-3


def _config(fidelity: str, n_pitches: int = 1) -> PilotConfig:
    return PilotConfig(
        d_m=D_M,
        L_m=L_M,
        fidelity=fidelity,
        n_pitches=n_pitches,
    )


def test_membrane_length_matches_both_walls_on_every_mesh() -> None:
    cases = (
        ("low", 1),
        ("high", 1),
        ("coarse", 3),
        ("medium", 3),
        ("fine", 3),
    )
    lengths = []
    for fidelity, pitches in cases:
        config = _config(fidelity, pitches)
        diagnostics = membrane_geometry_diagnostics(build_quad_mesh(config), config)
        expected = 2.0 * pitches * L_M
        assert diagnostics["membrane_length_top_m"] == pytest.approx(pitches * L_M)
        assert diagnostics["membrane_length_bottom_m"] == pytest.approx(pitches * L_M)
        assert diagnostics["membrane_length_total_m"] == pytest.approx(expected)
        assert diagnostics["membrane_length_minus_expected_m"] == pytest.approx(0.0, abs=1e-12)
        assert diagnostics["membrane_cells_with_multiple_faces"] == 0
        assert diagnostics["membrane_face_count"] == diagnostics["membrane_adjacent_cell_count"]
        assert diagnostics["area_over_volume_mean"] > 0.0
        assert diagnostics["inv_wall_distance_mean"] > diagnostics["area_over_volume_mean"]
        lengths.append(diagnostics["membrane_length_total_m"])
    assert lengths[2] == pytest.approx(lengths[3])
    assert lengths[3] == pytest.approx(lengths[4])
    assert lengths[2] == pytest.approx(3.0 * lengths[0])


def test_rectangle_source_factor_is_one_over_cell_height() -> None:
    mesh = QuadMesh(
        nodes=[(0.0, 0.0), (2.0, 0.0), (2.0, 0.4), (0.0, 0.4)],
        quads=[(0, 1, 2, 3)],
        boundaries={
            "wall_bottom_mem": [(0, 1)],
            "wall_top_mem": [(2, 3)],
        },
        interior=[],
        max_size_m=0.4,
        actual_max_edge_m=2.0,
        actual_min_edge_m=0.4,
        square_half_size_m=0.0,
        n_side=1,
        n_radial=1,
    )
    diagnostics = membrane_geometry_diagnostics(mesh, _config("low"))
    assert diagnostics["area_over_volume_min"] == pytest.approx(1.0 / 0.4)
    assert diagnostics["area_over_volume_max"] == pytest.approx(1.0 / 0.4)
    assert diagnostics["wall_distance_mean_m"] == pytest.approx(0.2)
    assert diagnostics["inv_wall_distance_mean"] == pytest.approx(5.0)
    assert diagnostics["area_over_volume_times_y1_mean"] == pytest.approx(0.5)
    assert diagnostics["membrane_cells_with_multiple_faces"] == 1


def test_two_membrane_edges_on_one_cell_are_counted() -> None:
    mesh = QuadMesh(
        nodes=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        quads=[(0, 1, 2, 3)],
        boundaries={
            "wall_bottom_mem": [(0, 1), (1, 2)],
            "wall_top_mem": [],
        },
        interior=[],
        max_size_m=1.0,
        actual_max_edge_m=1.0,
        actual_min_edge_m=1.0,
        square_half_size_m=0.0,
        n_side=1,
        n_radial=1,
    )
    diagnostics = membrane_geometry_diagnostics(mesh, _config("low"))
    assert diagnostics["membrane_face_count"] == 2
    assert diagnostics["membrane_adjacent_cell_count"] == 1
    assert diagnostics["membrane_cells_with_multiple_faces"] == 1


def test_flux_ratios_keep_sign_and_reject_a_zero_reference() -> None:
    signed = flux_consistency(
        mass_in_kg_s=1.0,
        mass_out_kg_s=-0.9,
        source_integral_kg_s=0.11,
        water_flux_avg_m_s=0.0,
        salt_flux_avg_kg_m2_s=0.09,
        membrane_length_m=1.0,
        density_kg_m3=1.0,
    )
    assert signed["mdot_perm_balance_kg_s"] == pytest.approx(0.1)
    assert signed["mdot_perm_source_kg_s"] == pytest.approx(0.11)
    assert signed["mdot_perm_surface_kg_s"] == pytest.approx(0.09)
    assert signed["source_vs_balance_rel"] == pytest.approx(0.1)
    assert signed["surface_vs_balance_rel"] == pytest.approx(-0.1)
    assert signed["surface_vs_source_rel"] == pytest.approx((0.09 - 0.11) / 0.11)

    negative_source = flux_consistency(
        mass_in_kg_s=1.0,
        mass_out_kg_s=-0.9,
        source_integral_kg_s=-0.1,
        water_flux_avg_m_s=None,
        salt_flux_avg_kg_m2_s=None,
        membrane_length_m=1.0,
        density_kg_m3=998.2,
    )
    assert negative_source["mdot_perm_source_kg_s"] == pytest.approx(-0.1)
    assert negative_source["mdot_perm_surface_kg_s"] is None
    assert negative_source["source_vs_balance_rel"] == pytest.approx(-2.0)

    zero_balance = flux_consistency(
        mass_in_kg_s=1.0,
        mass_out_kg_s=-1.0,
        source_integral_kg_s=0.2,
        water_flux_avg_m_s=1.0,
        salt_flux_avg_kg_m2_s=0.0,
        membrane_length_m=1.0,
        density_kg_m3=1.0,
    )
    assert zero_balance["source_vs_balance_rel"] is None
    assert zero_balance["surface_vs_source_rel"] == pytest.approx((1.0 - 0.2) / 0.2)


def test_completed_runs_stopped_before_the_full_source_ramp() -> None:
    assert source_ramp_factor(95) == pytest.approx(0.5)
    assert source_ramp_factor(109) == pytest.approx(0.8)
    assert source_ramp_factor(116) == pytest.approx(0.8)
    assert source_ramp_factor(150) == pytest.approx(1.0)
    assert source_ramp_factor(None) is None
    text = format_membrane_comparison(
        [
            {"mesh_level": "fine", "solver_iterations": 109, "lmh": 20.4794},
            {"mesh_level": "very_fine", "solver_iterations": 95, "lmh": 13.064},
        ]
    )
    assert "0.8" in text
    assert "0.5" in text


def test_divergence_phase_names_only_markers_present_in_the_transcript() -> None:
    text = (
        "Divergence detected in AMG solver: species-0\n"
        "Error: floating point exception\n"
    )
    assert divergence_phase(text) == (
        "species-0 AMG divergence; floating point exception"
    )
    assert divergence_phase("residual converged") is None


def test_missing_diagnostics_print_undefined_without_a_threshold() -> None:
    finished = {
        "mesh_level": "medium",
        "cell_count": 10,
        "lmh": 20.0,
        "cp_average": 1.06,
        "pressure_drop_per_length_pa_per_m": 25000.0,
        "membrane_diagnostics": {"membrane_length_total_m": 0.025},
    }
    failed = {
        "mesh_level": "coarse",
        "cell_count": 8,
        "lmh": None,
        "cp_average": None,
        "pressure_drop_per_length_pa_per_m": None,
        "membrane_diagnostics": {
            "membrane_length_total_m": 0.024,
            "divergence_phase": "species-0 AMG divergence; floating point exception",
        },
    }
    text = format_membrane_comparison([finished, failed])
    assert "undefined" in text
    assert "No acceptance threshold is applied." in text
    assert "differs across mesh levels" in text
    assert "mdot_balance" in text
    assert "species-0 AMG divergence" in text


def test_dry_run_and_failed_run_keep_geometry_without_flux(
    tmp_path: Path,
) -> None:
    record = run_case(_config("low"), tmp_path / "dry", dry_run=True)
    diagnostics = record["membrane_diagnostics"]
    assert diagnostics["membrane_length_total_m"] == pytest.approx(2.0 * L_M)
    assert "mdot_perm_balance_kg_s" not in diagnostics

    def launcher(**_kwargs):
        raise KeyError("AWP_ROOT251")

    with pytest.raises(FluentUnavailable):
        run_case(_config("low"), tmp_path / "failed", launcher=launcher)
    failed = json.loads(
        next((tmp_path / "failed").rglob("result.json")).read_text(encoding="utf-8")
    )
    assert failed["validity"] == "invalid"
    assert failed["membrane_diagnostics"]["membrane_length_total_m"] == pytest.approx(
        2.0 * L_M
    )
    assert "mdot_perm_source_kg_s" not in failed["membrane_diagnostics"]


def test_optional_surface_report_failure_keeps_the_required_qois() -> None:
    class _Node:
        def __init__(self, reject_min: bool) -> None:
            self.reject_min = reject_min
            self.report_type = self
            self.field = self
            self.surface_names = self
            self.boundaries = self
            self.cell_zones = self

        def set_state(self, value: object) -> None:
            if self.reject_min and value in {"surface-facetmin", "facet-min"}:
                raise RuntimeError("min report is not allowed")

    class _Collection:
        def __init__(self, reject_min: bool) -> None:
            self.reject_min = reject_min

        def create(self, _name: str) -> None:
            return None

        def __getitem__(self, name: str) -> _Node:
            return _Node("min" in name or "max" in name)

    class _Reports:
        def __init__(self) -> None:
            self.surface = _Collection(True)
            self.flux = _Collection(False)
            self.volume = _Collection(False)

        def compute(self, report_defs: list[str]) -> dict[str, float]:
            values = {
                "p_inlet": 6000100.0,
                "p_outlet": 6000000.0,
                "m_inlet": 0.20,
                "m_outlet": -0.19,
                "sink_total": 0.01,
                "cp_mem": 1.05,
                "jw_avg": 5.0e-6,
                "js_avg": 1.0e-8,
            }
            name = report_defs[0]
            if name not in values:
                raise RuntimeError(f"missing {name}")
            return {"value": values[name]}

    class _Solution:
        def __init__(self, reports: _Reports) -> None:
            self.report_definitions = reports

    class _Settings:
        def __init__(self, reports: _Reports) -> None:
            self.solution = _Solution(reports)

    class _Solver:
        def __init__(self, reports: _Reports) -> None:
            self.settings = _Settings(reports)

    metrics = _extract_reports(_Solver(_Reports()), _config("medium", 3))
    assert metrics["lmh"] > 0.0
    assert metrics["pressure_drop_pa"] == pytest.approx(100.0)
    solution = metrics["membrane_solution"]
    assert solution["jw_avg_m_s"] == pytest.approx(5.0e-6)
    assert solution["jw_min_m_s"] is None
    assert solution["jw_max_m_s"] is None
    assert solution["source_integral_kg_s"] == pytest.approx(0.01)
    assert solution["mass_in_kg_s"] == pytest.approx(0.20)
    assert solution["mass_out_kg_s"] == pytest.approx(-0.19)


def test_result_schema_accepts_the_diagnostic_mapping() -> None:
    plan = build_plan(_config("medium"))
    record = build_result_record(
        plan,
        {"membrane_diagnostics": {"membrane_length_total_m": 0.008}},
    )
    assert record["membrane_diagnostics"]["membrane_length_total_m"] == 0.008
    with pytest.raises(ValueError, match="membrane_diagnostics"):
        build_result_record(plan, {"membrane_diagnostics": 1})
