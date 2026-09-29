"""Medium/very_fine screening plan and pair statistics. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geometry import CAMPAIGN_H_M
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.fidelity_screen import (
    LEVEL_MEDIUM,
    LEVEL_VERY_FINE,
    SCREEN_D_M,
    SCREEN_L_M,
    SCREEN_N_PITCHES,
    analyze_rows,
    average_ranks,
    format_analysis,
    format_plan,
    missing_run_commands,
    pearson,
    plan_rows,
    rank_reversal_count,
    screening_geometries,
    spearman,
    write_summary,
)
from ro_2d_pilot.geometry import geo_id_for, length_token
from ro_2d_pilot.mesh import mesh_spec, resolution_layout
from ro_2d_pilot.mesh_build import build_quad_mesh
from ro_2d_pilot.paths import DATA_ROOT_ENV


def test_screening_grid_is_the_nine_explicit_points() -> None:
    points = screening_geometries()
    assert len(points) == 9
    assert len({point.pair_id for point in points}) == 9
    assert [point.d_m for point in points] == [
        d_m for d_m in SCREEN_D_M for _level in SCREEN_L_M
    ]
    assert [point.L_m for point in points] == list(SCREEN_L_M) * len(SCREEN_D_M)
    center = points[4]
    assert center.d_m == pytest.approx(4.0e-4)
    assert center.L_m == pytest.approx(4.0e-3)
    assert center.pair_id == "d0p40_L4p00"
    assert length_token(SCREEN_D_M[0], "d") == "d0p300000mm"
    assert length_token(SCREEN_L_M[0], "L") == "L3p000000mm"
    assert points[0].geo_id == (
        "d0p300000mm_L3p000000mm_h0p770000mm_n3"
    )
    assert points[-1].geo_id == (
        "d0p500000mm_L5p000000mm_h0p770000mm_n3"
    )


def test_screening_points_stay_inside_the_channel_and_mesh() -> None:
    tight_gap_m = 0.5 * (CAMPAIGN_H_M - max(SCREEN_D_M))
    assert tight_gap_m == pytest.approx(1.35e-4)
    assert min(SCREEN_L_M) / max(SCREEN_D_M) == pytest.approx(6.0)
    for point in screening_geometries():
        assert point.d_m < CAMPAIGN_H_M
        assert point.L_m > point.d_m
        for level in (LEVEL_MEDIUM, LEVEL_VERY_FINE):
            config = PilotConfig(
                d_m=point.d_m,
                L_m=point.L_m,
                fidelity=level,
                n_pitches=SCREEN_N_PITCHES,
            )
            layout = resolution_layout(config)
            assert int(layout["n_radial"]) >= 2
            assert int(layout["n_gap"]) >= 2
            assert int(layout["n_stream"]) >= 2
            spec = mesh_spec(config)
            assert spec["obstacle_resolution_ok"] is True
            assert geo_id_for(config) == point.geo_id
        build_quad_mesh(
            PilotConfig(
                d_m=point.d_m,
                L_m=point.L_m,
                fidelity=LEVEL_MEDIUM,
                n_pitches=SCREEN_N_PITCHES,
            )
        )


def test_plan_marks_existing_validity_and_skips_valid_commands(tmp_path: Path) -> None:
    points = screening_geometries()
    first = points[0]
    center = next(point for point in points if point.pair_id == "d0p40_L4p00")
    _write_result(tmp_path, first, LEVEL_MEDIUM, _metrics(lmh=20.0), validity="invalid")
    _write_result(tmp_path, center, LEVEL_MEDIUM, _metrics(lmh=25.0))
    _write_result(tmp_path, center, LEVEL_VERY_FINE, _metrics(lmh=24.0, solver_s=800.0))
    rows = plan_rows(tmp_path)
    by_id = {row["pair_id"]: row for row in rows}
    assert by_id[first.pair_id]["medium_exists"] is True
    assert by_id[first.pair_id]["medium_validity"] == "invalid"
    assert by_id[first.pair_id]["very_fine_exists"] is False
    assert by_id[first.pair_id]["very_fine_validity"] is None
    assert by_id[center.pair_id]["medium_validity"] == "valid"
    assert by_id[center.pair_id]["very_fine_validity"] == "valid"
    commands = missing_run_commands(rows)
    joined = "\n".join(commands)
    assert f"# {center.pair_id} {LEVEL_MEDIUM}" not in joined
    assert f"# {center.pair_id} {LEVEL_VERY_FINE}" not in joined
    assert f"# {first.pair_id} {LEVEL_MEDIUM} (invalid)" in joined
    assert f"# {first.pair_id} {LEVEL_VERY_FINE} (missing)" in joined
    assert "--mesh-level medium" in joined
    assert "--mesh-level very_fine" in joined
    assert "--n-pitches 3" in joined
    assert "run_ro_2d_case.py" in joined
    text = format_plan(rows)
    assert "expected_runs=18" in text
    assert "does not launch Fluent" in text


def test_analysis_correlates_ranks_costs_and_skips_bad_pairs(tmp_path: Path) -> None:
    points = screening_geometries()[:3]
    lmh_medium = [10.0, 20.0, 30.0]
    lmh_fine = [11.0, 21.0, 31.0]
    cp_medium = [1.10, 1.05, 1.02]
    cp_fine = [1.12, 1.06, 1.03]
    dp_medium = [20000.0, 25000.0, 30000.0]
    dp_fine = [40000.0, 50000.0, 60000.0]
    for index, point in enumerate(points):
        _write_result(
            tmp_path,
            point,
            LEVEL_MEDIUM,
            _metrics(
                lmh=lmh_medium[index],
                cp=cp_medium[index],
                dp=dp_medium[index],
                solver_s=100.0,
                wall_s=150.0,
            ),
        )
        _write_result(
            tmp_path,
            point,
            LEVEL_VERY_FINE,
            _metrics(
                lmh=lmh_fine[index],
                cp=cp_fine[index],
                dp=dp_fine[index],
                solver_s=800.0,
                wall_s=900.0,
            ),
        )
    mismatched = screening_geometries()[3]
    _write_result(tmp_path, mismatched, LEVEL_MEDIUM, _metrics(lmh=1.0))
    _write_result(
        tmp_path,
        mismatched,
        LEVEL_VERY_FINE,
        _metrics(lmh=1.0),
        d_m=mismatched.d_m + 1.0e-5,
    )
    rows = plan_rows(tmp_path)
    report = analyze_rows(rows)
    assert report["n_comparable_pairs"] == 3
    assert report["decision"] == "not_made"
    assert any(item["pair_id"] == mismatched.pair_id for item in report["skipped"])
    lmh = report["correlation"]["lmh"]
    assert lmh["n"] == 3
    assert lmh["pearson"] == pytest.approx(1.0)
    assert lmh["spearman"] == pytest.approx(1.0)
    assert lmh["mean_signed_relative_discrepancy"] == pytest.approx(
        ((10 - 11) / 11 + (20 - 21) / 21 + (30 - 31) / 31) / 3
    )
    cpex = report["correlation"]["cp_excess"]
    assert cpex["spearman"] == pytest.approx(1.0)
    pressure = report["correlation"]["pressure_drop_per_length_pa_per_m"]
    assert pressure["pearson"] == pytest.approx(1.0)
    reversals = report["rank_reversals"]["lmh"]
    assert reversals["comparisons"] == 3
    assert reversals["reversals"] == 0
    assert reversals["fraction"] == 0.0
    cost = report["solver_cost"]
    assert cost["n"] == 3
    assert cost["median"] == pytest.approx(100.0 / 800.0)
    assert cost["min"] == pytest.approx(100.0 / 800.0)
    assert cost["max"] == pytest.approx(100.0 / 800.0)
    wall = report["total_wall_cost"]
    assert wall["median"] == pytest.approx(150.0 / 900.0)
    text = format_analysis(report)
    assert "No low-fidelity acceptance decision" in text
    assert "solver_cost_ratio" in text
    assert "total_wall_cost_ratio" in text
    written = write_summary(tmp_path, report)
    payload = json.loads(written["json"].read_text(encoding="utf-8"))
    assert payload["n_comparable_pairs"] == 3
    assert "studies" not in written["json"].parts[:1]
    csv_text = written["csv"].read_text(encoding="utf-8")
    assert "relative_cp_excess" in csv_text
    assert mismatched.pair_id in csv_text


def test_rank_reversal_and_correlation_helpers() -> None:
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert spearman([10.0, 20.0, 30.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    assert average_ranks([5.0, 5.0, 7.0]) == pytest.approx([1.5, 1.5, 3.0])
    reversed_ranks = rank_reversal_count(
        [1.0, 2.0, 3.0],
        [30.0, 20.0, 10.0],
        larger_is_better=True,
    )
    assert reversed_ranks["reversals"] == 3
    assert reversed_ranks["comparisons"] == 3
    assert reversed_ranks["fraction"] == pytest.approx(1.0)
    tied = rank_reversal_count(
        [1.0, 1.0],
        [2.0, 3.0],
        larger_is_better=False,
    )
    assert tied["reversals"] == 0
    assert tied["comparisons"] == 1
    with pytest.raises(ValueError, match="equal series"):
        pearson([1.0], [1.0, 2.0])


def test_fewer_than_three_pairs_skip_correlation() -> None:
    point = screening_geometries()[4]
    row = {
        "pair_id": point.pair_id,
        "d_m": point.d_m,
        "L_m": point.L_m,
        "geo_id": point.geo_id,
        "medium_validity": "valid",
        "very_fine_validity": "valid",
        "medium_record": _metrics(lmh=25.0, d_m=point.d_m, L_m=point.L_m, geo_id=point.geo_id),
        "very_fine_record": _metrics(
            lmh=24.0,
            solver_s=800.0,
            d_m=point.d_m,
            L_m=point.L_m,
            geo_id=point.geo_id,
        ),
    }
    report = analyze_rows([row])
    assert report["n_comparable_pairs"] == 1
    assert report["correlation"]["lmh"]["pearson"] is None
    assert "fewer than 3" in report["correlation"]["lmh"]["reason"]
    assert report["rank_reversals"]["cp_excess"]["comparisons"] == 0
    assert report["rank_reversals"]["cp_excess"]["fraction"] is None


def test_scripts_do_not_launch_and_require_the_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path))
    point = screening_geometries()[4]
    _write_result(tmp_path, point, LEVEL_MEDIUM, _metrics(lmh=25.0, d_m=point.d_m, L_m=point.L_m, geo_id=point.geo_id))
    _write_result(
        tmp_path,
        point,
        LEVEL_VERY_FINE,
        _metrics(lmh=24.0, solver_s=800.0, d_m=point.d_m, L_m=point.L_m, geo_id=point.geo_id),
    )
    planner = load_module(
        "plan_ro_2d_fidelity_pairs_under_test",
        SCRIPTS_DIR / "plan_ro_2d_fidelity_pairs.py",
    )
    analysis = load_module(
        "analyze_ro_2d_fidelity_pairs_under_test",
        SCRIPTS_DIR / "analyze_ro_2d_fidelity_pairs.py",
    )
    assert planner.main([]) == 0
    plan_text = capsys.readouterr().out
    assert "d0p40_L4p00" in plan_text
    assert "does not launch Fluent" in plan_text
    assert analysis.main([]) == 0
    analysis_text = capsys.readouterr().out
    assert "medium_very_fine_summary.json" in analysis_text
    assert (tmp_path / "studies" / "fidelity_pairs" / "medium_very_fine_summary.json").is_file()
    monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
    assert planner.main([]) == 1


def _metrics(
    *,
    lmh: float,
    cp: float = 1.08,
    dp: float = 25000.0,
    solver_s: float = 100.0,
    wall_s: float = 140.0,
    d_m: float = 4.0e-4,
    L_m: float = 4.0e-3,
    geo_id: str | None = None,
) -> dict[str, object]:
    config = PilotConfig(
        d_m=d_m,
        L_m=L_m,
        fidelity=LEVEL_MEDIUM,
        n_pitches=SCREEN_N_PITCHES,
    )
    return {
        "geo_id": geo_id or geo_id_for(config),
        "d_m": d_m,
        "L_m": L_m,
        "channel_height_m": CAMPAIGN_H_M,
        "n_pitches": SCREEN_N_PITCHES,
        "inlet_velocity_m_s": 0.2,
        "outlet_gauge_pressure_pa": 6.0e6,
        "lmh": lmh,
        "cp_average": cp,
        "pressure_drop_per_length_pa_per_m": dp,
        "solver_wall_time_s": solver_s,
        "total_wall_time_s": wall_s,
        "validity": "valid",
        "validity_reason": "",
        "convergence_status": "residual_converged",
        "source_ramp_final": 1.0,
        "full_source_reached": True,
        "full_source_iterations": 52,
        "total_iterations": 201,
        "solver_iterations": 201,
    }


def _write_result(
    root: Path,
    point,
    level: str,
    metrics: dict[str, object],
    *,
    validity: str = "valid",
    d_m: float | None = None,
) -> None:
    from ro_2d_pilot.fidelity_screen import result_path

    payload = dict(metrics)
    payload["mesh_level"] = level
    payload["fidelity"] = level
    payload["validity"] = validity
    payload["geo_id"] = point.geo_id
    payload["d_m"] = point.d_m if d_m is None else d_m
    payload["L_m"] = point.L_m
    path = result_path(root, point, level)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
