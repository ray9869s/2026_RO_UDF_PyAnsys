"""3-pitch mesh ladder specification and comparison. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.execute import phase_times
from ro_2d_pilot.ladder import GCI_NOT_APPLIED, LadderMismatch, compare_ladder, format_ladder
from ro_2d_pilot.mesh import mesh_spec
from ro_2d_pilot.mesh_build import build_quad_mesh

D_M = 4.0e-4
L_M = 4.0e-3


def _config(label: str, n_pitches: int = 3) -> PilotConfig:
    return PilotConfig(
        d_m=D_M,
        L_m=L_M,
        fidelity=label,
        n_pitches=n_pitches,
    )


def test_three_pitch_ladder_counts_and_has_no_prism_layers() -> None:
    expected = {
        "coarse": (24, 24, 8604),
        "medium": (48, 48, 34416),
        "fine": (96, 97, 139734),
        "very_fine": (192, 192, 551196),
    }
    previous = 0
    for level, (divisions, height_cells, cells) in expected.items():
        spec = mesh_spec(_config(level))
        assert spec["mesh_level"] == level
        assert spec["channel_height_divisions"] == divisions
        assert spec["cells_across_channel_height"] == height_cells
        assert spec["estimated_cell_count"] == cells
        assert spec["topology_cell_count"] == cells
        assert spec["boundary_layers"] == 0
        assert spec["boundary_layers_actual"] == 0
        assert spec["boundary_layers_requested"] == 0
        assert spec["first_layer_height_m"] is None
        assert spec["estimated_cell_count"] > previous
        previous = int(spec["estimated_cell_count"])
    coarse_mesh = build_quad_mesh(_config("coarse"))
    assert coarse_mesh.n_cells == 8604


def test_low_and_high_labels_keep_their_spacing_targets() -> None:
    low = mesh_spec(_config("low", n_pitches=1))
    high = mesh_spec(_config("high", n_pitches=1))
    coarse = mesh_spec(_config("coarse", n_pitches=1))
    fine = mesh_spec(_config("fine", n_pitches=1))
    assert low["mesh_level"] == "coarse"
    assert high["mesh_level"] == "fine"
    assert low["height_divisions"] == coarse["height_divisions"] == 24
    assert high["height_divisions"] == fine["height_divisions"] == 96
    assert low["topology_cell_count"] == 2868
    assert high["topology_cell_count"] == 46578
    assert low["boundary_layers_requested"] == 0
    assert high["boundary_layers_requested"] == 4
    assert coarse["boundary_layers_actual"] == fine["boundary_layers_actual"] == 0


def test_phase_times_sum_measured_startup_or_fall_back() -> None:
    measured = phase_times(
        python_prep_s=1.0,
        launch_time_s=2.0,
        mesh_read_time_s=3.0,
        setup_time_s=4.0,
        solve_time_s=8.0,
        extraction_time_s=0.5,
        total_wall_time_s=20.0,
    )
    assert measured["startup_overhead_s"] == pytest.approx(10.0)
    fallback = phase_times(
        python_prep_s=None,
        launch_time_s=None,
        mesh_read_time_s=None,
        setup_time_s=None,
        solve_time_s=8.0,
        extraction_time_s=2.0,
        total_wall_time_s=20.0,
    )
    assert fallback["startup_overhead_s"] == pytest.approx(10.0)
    assert fallback["launch_time_s"] is None


def _record(level: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "geo_id": "d0p400000mm_L4p000000mm_h0p770000mm_n3",
        "fidelity": level,
        "mesh_level": level,
        "d_m": D_M,
        "L_m": L_M,
        "channel_height_m": 7.7e-4,
        "n_pitches": 3,
        "inlet_velocity_m_s": 0.2,
        "outlet_gauge_pressure_pa": 6.0e6,
        "cell_count": 1000,
        "cells_across_channel_height": 24,
        "lmh": 25.0,
        "cp_average": 1.10,
        "pressure_drop_per_length_pa_per_m": 23000.0,
        "mass_balance_rel": 1.0e-4,
        "solver_iterations": 100,
        "solver_wall_time_s": 10.0,
        "total_wall_time_s": 100.0,
        "startup_overhead_s": 90.0,
        "extraction_time_s": 1.0,
    }
    record.update(overrides)
    return record


def test_ladder_reports_refinement_change_and_skips_gci() -> None:
    report = compare_ladder(
        [
            _record("fine", cell_count=139734, lmh=21.0, cp_average=1.04,
                    pressure_drop_per_length_pa_per_m=25000.0,
                    solver_wall_time_s=40.0, total_wall_time_s=160.0),
            _record("coarse", cell_count=8604, lmh=26.0, cp_average=1.10,
                    pressure_drop_per_length_pa_per_m=23000.0),
            _record("medium", cell_count=34416, lmh=23.0, cp_average=1.06,
                    pressure_drop_per_length_pa_per_m=24000.0,
                    solver_wall_time_s=20.0, total_wall_time_s=120.0),
        ]
    )
    assert report["levels"] == ["coarse", "medium", "fine"]
    first = report["adjacent"][0]
    assert first["coarser"] == "coarse"
    assert first["finer"] == "medium"
    assert first["relative_discrepancy"]["lmh"] == pytest.approx((26.0 - 23.0) / 23.0)
    assert first["relative_discrepancy"]["cp_excess"] == pytest.approx(
        (0.10 - 0.06) / 0.06
    )
    assert first["solver_time_ratio"] == pytest.approx(10.0 / 20.0)
    assert first["total_wall_time_ratio"] == pytest.approx(100.0 / 120.0)
    assert report["trend_with_refinement"]["lmh"] == "non-increasing with refinement"
    assert report["formal_gci"] == GCI_NOT_APPLIED
    text = format_ladder(report)
    assert "relative discrepancy with respect to the finer mesh" in text
    assert "No acceptance threshold is applied." in text
    assert "LF" in text
    assert "GCI" in text
    assert "error" not in text.lower()


def test_ladder_rejects_a_different_case_or_a_repeated_level() -> None:
    with pytest.raises(LadderMismatch, match="d_m"):
        compare_ladder(
            [
                _record("coarse"),
                _record("medium", d_m=5.0e-4),
            ]
        )
    with pytest.raises(LadderMismatch, match="duplicate"):
        compare_ladder([_record("coarse"), _record("coarse", cell_count=9000)])
    with pytest.raises(LadderMismatch, match="Unrecognized"):
        compare_ladder(
            [
                _record("coarse"),
                _record("medium", fidelity="ultra", mesh_level="ultra"),
            ]
        )


def test_legacy_low_high_records_join_the_ladder_without_new_timing() -> None:
    low = _record("low", mesh_level=None, startup_overhead_s=None, extraction_time_s=None)
    high = _record(
        "high",
        mesh_level=None,
        lmh=20.0,
        cp_average=1.05,
        solver_wall_time_s=40.0,
        startup_overhead_s=None,
        extraction_time_s=None,
    )
    report = compare_ladder([high, low])
    assert report["levels"] == ["coarse", "fine"]
    text = format_ladder(report)
    assert "undefined" in text


def test_ladder_script_reads_a_results_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = "u0p2_p6M"
    for level in ("coarse", "very_fine"):
        path = tmp_path / level / run_id / "result.json"
        path.parent.mkdir(parents=True)
        payload = _record(
            level,
            lmh=25.0 if level == "coarse" else 20.0,
            cp_average=1.08 if level == "coarse" else 1.04,
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
    script = load_module(
        "compare_ro_2d_mesh_ladder_under_test",
        SCRIPTS_DIR / "compare_ro_2d_mesh_ladder.py",
    )
    assert script.main(["--results-dir", str(tmp_path), "--run-id", run_id]) == 0
    assert "coarse -> very_fine" in capsys.readouterr().out
    assert script.main(
        [
            "--results-dir",
            str(tmp_path),
            "--result",
            str(tmp_path / "coarse" / run_id / "result.json"),
        ]
    ) == 1


def test_run_script_accepts_mesh_level_and_keeps_fidelity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load_module(
        "run_ro_2d_case_mesh_level_under_test",
        SCRIPTS_DIR / "run_ro_2d_case.py",
    )
    args = script.build_parser().parse_args(
        ["--d-m", "4e-4", "--l-m", "4e-3", "--mesh-level", "very_fine", "--n-pitches", "3"]
    )
    assert args.mesh_level == "very_fine"
    assert args.fidelity is None
    legacy = script.build_parser().parse_args(
        ["--d-m", "4e-4", "--l-m", "4e-3", "--fidelity", "high"]
    )
    assert legacy.fidelity == "high"
    assert script.main(
        [
            "--d-m",
            "4e-4",
            "--l-m",
            "4e-3",
            "--fidelity",
            "low",
            "--mesh-level",
            "coarse",
        ]
    ) == 1
    assert "only one" in capsys.readouterr().err
