"""Screen landscape, v1 experiment cost, and the offline plan. No Fluent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_3d import (
    BUDGET_11H,
    SCREEN_MEDIAN,
    LiveEvaluator,
    format_plan,
    format_recorded_initial_charges,
    load_experiment,
    mesh_id_for_settings,
)
from ro.mfbo_fidelity import parse_fidelity_level
from ro.mfbo_landscape import (
    SWEEP_CP_EXCESS,
    SWEEP_DP_PA_PER_M,
    Box,
    LevelObs,
    ScreenDesign,
    _pof,
    _search,
    analyze,
    cost_medians,
    fit_level,
    read_screen_rows,
    write_contours,
)

TABLE = REPO_ROOT / "configs" / "mfbo_fidelity_table.3d_v1.json"
V1 = REPO_ROOT / "configs" / "mfbo_3d_experiment.v1.json"
BOX = Box(0.6, 1.0, 0.0, 0.3)
RUN_ID = "u0p2_p6M"


class _Surface:
    def __init__(self, function):
        self.function = function

    def predict_high(self, x):
        rows = np.atleast_2d(np.asarray(x, dtype=float))
        mean = np.array([self.function(row[0], row[1]) for row in rows], dtype=float)
        return mean, np.full(len(rows), 1.0e-18)


def test_linear_surface_optimum_sits_on_the_tighter_constraint():
    hf = {
        "lmh": _Surface(lambda x1, x2: 10.0 + 10.0 * x1 + x2),
        "dp": _Surface(lambda x1, _x2: 40000.0 + 50000.0 * x1),
        "cp_excess": _Surface(lambda x1, _x2: 0.02 + 0.04 * x1),
    }
    found = _search(hf, BOX, cp_excess_max=0.04, dp_max_pa_per_m=71000.0)
    optimum = found["optimum"]
    assert optimum["d_p_mm"] == pytest.approx(0.8, abs=0.01)
    assert optimum["d_h_mm"] == pytest.approx(0.3, abs=1.0e-9)
    assert optimum["active"] == ["cp_excess"]
    assert optimum["cp_excess"] <= 0.04 + 1.0e-8
    assert optimum["dp_per_l"] <= 71000.0
    assert _pof(hf, 0.6, 0.0, BOX, cp_excess_max=0.04, dp_max_pa_per_m=71000.0) == pytest.approx(1.0)
    assert _pof(hf, 1.0, 0.3, BOX, cp_excess_max=0.04, dp_max_pa_per_m=71000.0) == pytest.approx(0.0)


def test_gp_interpolates_valid_hf_points_and_drops_the_invalid_one():
    designs = _corners()
    designs.append(
        _design(1.0, 0.15, lmh=500.0, dp=1.0, cp=1.01, status_hf="invalid", wall_hf=9000.0)
    )
    fitted = fit_level(designs, "HF", BOX)
    assert fitted["n"] == 4
    assert (1.0, 0.15) not in fitted["points"]
    mean, _var = fitted["lmh"].predict_high(np.asarray([[0.0, 0.0]], dtype=float))
    assert float(mean[0]) == pytest.approx(10.0, abs=1.0e-4)


def test_screen_csv_skips_summary_rows_and_reports_medians(tmp_path: Path):
    path = tmp_path / "screen.csv"
    path.write_text(
        "row_type,d_p_mm,d_h_mm,d_f_mm,status_lf,status_hf,"
        "lmh_lf,lmh_hf,pressure_drop_per_length_pa_per_m_lf,pressure_drop_per_length_pa_per_m_hf,"
        "cp_average_lf,cp_average_hf,wall_s_lf,wall_s_hf,cost_ratio,cost_proxy_ratio\n"
        "design,0.6,0.0,0.4,valid,valid,10,11,1000,1100,1.02,1.03,100,200,0.5,0.2\n"
        "design,0.8,0.15,0.4,valid,invalid,12,,1000,,1.02,,,800,,\n"
        "summary,,,,,,,,,,,,,\n",
        encoding="utf-8",
    )
    designs = read_screen_rows(path)
    assert len(designs) == 2
    assert designs[1].high.trains is False
    medians = cost_medians(designs)
    assert medians["hf_wall_median_s"] == pytest.approx(500.0)
    assert medians["wall_cost_ratio_median"] == pytest.approx(0.5)
    assert medians["n_hf_wall"] == 2


def test_report_sweeps_nine_thresholds_and_writes_a_png(tmp_path: Path):
    report = analyze(_corners(), BOX, cp_excess_max=0.04, dp_max_pa_per_m=71000.0)
    assert len(report["sweep"]) == len(SWEEP_CP_EXCESS) * len(SWEEP_DP_PA_PER_M)
    assert report["primary"]["grid_pof"]
    destination = write_contours(report, tmp_path / "landscape_3d_lmh.png")
    header = destination.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n"
    assert destination.stat().st_size > 1000


def test_v1_config_shares_seed_initials_and_resolves_an_11_hour_budget(tmp_path: Path):
    payload = json.loads(V1.read_text(encoding="utf-8"))
    assert payload["seed"] == 0
    assert payload["mode"] == "live"
    assert payload["run_id"] == RUN_ID
    assert payload["d_f_mm"] == 0.4
    assert payload["design_box"] == {"d_p_mm": [0.6, 1.0], "d_h_mm": [0.0, 0.3]}
    assert payload["cp_excess_max"] == 0.04
    assert payload["dp_max_pa_per_m"] == 71000
    assert payload["reference_hf_wall_s"] == SCREEN_MEDIAN
    assert payload["budget_hf_equivalent"] == BUDGET_11H
    assert payload["lf_over_hf_cost_ratio"] == 0.54
    assert payload["cost_proxy_ratio"] == 0.25
    assert payload["fidelity_table"] == "configs/mfbo_fidelity_table.3d_v1.json"
    assert payload["replay_table"] is None
    assert payload["initial_hf"] == payload["initial_lf"]
    assert [(item["d_p_mm"], item["d_h_mm"]) for item in payload["initial_hf"]] == [
        (0.8, 0.15),
        (0.6, 0.0),
        (1.0, 0.3),
        (0.6, 0.3),
    ]
    csv_path = tmp_path / "screen.csv"
    _write_uniform_screen(csv_path, wall_hf=3600.0, wall_lf=1944.0)
    payload["screen_csv"] = csv_path.name
    config_path = tmp_path / "v1.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_experiment(config_path, repo_root=REPO_ROOT, data_root=tmp_path)
    assert loaded.reference_hf_wall_s == pytest.approx(3600.0)
    assert loaded.budget_hf_equivalent * loaded.reference_hf_wall_s / 3600.0 == pytest.approx(11.0)
    assert loaded.fidelity_settings["HF"]["run_id_suffix"] == "hfm"
    assert loaded.fidelity_settings["LF"]["run_id_suffix"] == "lfd"
    assert loaded.cost_proxy_ratio == pytest.approx(0.25)
    assert "cost_proxy_ratio" in loaded.problem
    text = format_plan(loaded)
    assert "HF d_p_mm=0.8 d_h_mm=0.15" in text
    assert "HF d_p_mm=0.6 d_h_mm=0.0" in text
    assert "HF d_p_mm=1.0 d_h_mm=0.0" not in text
    assert "budget_wall_h=11.0" in text or "budget_wall_h=11" in text


def test_dry_run_prints_the_plan_without_importing_drivers(tmp_path: Path, capsys):
    config_path = _v1_with_screen(tmp_path)
    sys.modules.pop("ro.mfbo_drivers", None)
    script = load_module("run_3d_mfbo_dry", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    code = script.main(
        [
            "run",
            "--config",
            str(config_path),
            "--data-root",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert code == 0
    assert "ro.mfbo_drivers" not in sys.modules
    out = capsys.readouterr().out
    assert "hf_bo initial" in out
    assert "mfbo initial" in out
    assert "d_p_mm=0.6 d_h_mm=0.0" in out
    assert "d_p_mm=0.6 d_h_mm=0.3" in out
    assert "d_p_mm=1.0 d_h_mm=0.0" not in out
    assert "budget_wall_h=11" in out
    assert "recorded=absent" in out


def test_emit_queue_is_hf_bo_then_mfbo_with_the_geometry_worker(tmp_path: Path):
    config_path = _v1_with_screen(tmp_path)
    script = load_module("run_3d_mfbo_emit", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    queue = tmp_path / "v1_queue.json"
    code = script.main(
        [
            "emit-queue",
            "--config",
            str(config_path),
            "--data-root",
            str(tmp_path),
            "--out",
            str(queue),
        ]
    )
    assert code == 0
    job_queue = load_module("job_queue_v1_plan", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    jobs = job_queue.load_queue(queue)
    assert [job["id"] for job in jobs] == ["v1-hf_bo", "v1-mfbo"]
    assert all("--geometry-worker" in job["argv"] for job in jobs)
    assert jobs[0]["argv"][jobs[0]["argv"].index("--method") + 1] == "hf_bo"
    assert jobs[1]["argv"][jobs[1]["argv"].index("--method") + 1] == "mfbo"


def test_emit_queue_ids_follow_experiment_id(tmp_path: Path):
    config_path = _v1_with_screen(tmp_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["experiment_id"] = "v1_seed1"
    payload["seed"] = 1
    seed_path = tmp_path / "v1_seed1.json"
    seed_path.write_text(json.dumps(payload), encoding="utf-8")
    script = load_module("run_3d_mfbo_emit_seed", SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py")
    queue = tmp_path / "v1_seed1_queue.json"
    code = script.main(
        [
            "emit-queue",
            "--config",
            str(seed_path),
            "--data-root",
            str(tmp_path),
            "--out",
            str(queue),
        ]
    )
    assert code == 0
    job_queue = load_module("job_queue_seed_ids", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    jobs = job_queue.load_queue(queue)
    assert [job["id"] for job in jobs] == ["v1_seed1-hf_bo", "v1_seed1-mfbo"]
    logs = job_queue.queue_files(queue).logs
    assert [logs / f"{job['id']}.log" for job in jobs] == [
        logs / "v1_seed1-hf_bo.log",
        logs / "v1_seed1-mfbo.log",
    ]
    assert all(str(seed_path) in job["argv"] for job in jobs)


def test_recorded_invalid_leaf_is_charged_and_a_missing_leaf_is_not(tmp_path: Path, monkeypatch):
    config = load_experiment(_v1_with_screen(tmp_path), repo_root=REPO_ROOT, data_root=tmp_path)

    def fake(design, fidelity, **_kwargs):
        wall = (10.0, 20.0, 30.0) if (design.d_p_mm, design.d_h_mm) == (0.6, 0.0) else None
        status = "invalid" if wall else "execution_failed"
        return {
            "status": status,
            "mesh_wall_time_s": None if wall is None else wall[0],
            "solver_wall_time_s": None if wall is None else wall[1],
            "extraction_wall_time_s": None if wall is None else wall[2],
        }

    monkeypatch.setattr("ro.mfbo_3d.record_from_leaves", fake)
    text = format_recorded_initial_charges(config, tmp_path)
    assert "HF d_p_mm=0.6 d_h_mm=0.0 status=invalid wall_s=60.0" in text
    assert "recorded=absent" in text
    assert "recorded_hf_equivalent hf_bo=" in text


def test_live_evaluator_looks_up_the_suffixed_hf_leaf(tmp_path: Path, monkeypatch):
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    seen = {}

    def fake_state(path: Path):
        seen["path"] = path
        return "absent"

    monkeypatch.setattr("ro.mfbo_3d._run_state", fake_state)
    monkeypatch.setattr(
        "ro.mfbo_3d.evaluate",
        lambda *_args, **_kwargs: {
            "status": "valid",
            "d_p_mm": 0.8,
            "d_h_mm": 0.15,
            "fidelity": "HF",
            "lmh": 10.0,
            "cp_average": 1.01,
            "pressure_drop_per_length_pa_per_m": 1000.0,
            "mesh_wall_time_s": 1.0,
            "solver_wall_time_s": 2.0,
            "extraction_wall_time_s": 3.0,
        },
    )
    evaluator = LiveEvaluator(tmp_path, {"HF": table["HF_m"]}, RUN_ID, 0.4, drivers=object())
    outcome = evaluator(type("Point", (), {"d_p_mm": 0.8, "d_h_mm": 0.15})(), "HF")
    parsed = parse_fidelity_level(table["HF_m"])
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=parsed["n_active"])
    assert seen["path"] == (
        tmp_path / "runs" / "pillar" / geo_id / mesh_id_for_settings(parsed) / "u0p2_p6M_hfm"
    )
    assert outcome.reused_existing is False
    assert outcome.measured_wall_s() == pytest.approx(6.0)


def _corners() -> list[ScreenDesign]:
    return [
        _design(0.6, 0.0, lmh=10.0, dp=40000.0, cp=1.02, wall_hf=1000.0),
        _design(0.6, 0.3, lmh=12.0, dp=42000.0, cp=1.03, wall_hf=1100.0),
        _design(1.0, 0.0, lmh=20.0, dp=90000.0, cp=1.06, wall_hf=1200.0),
        _design(1.0, 0.3, lmh=22.0, dp=95000.0, cp=1.07, wall_hf=1300.0),
    ]


def _design(
    d_p: float,
    d_h: float,
    *,
    lmh: float,
    dp: float,
    cp: float,
    wall_hf: float,
    status_hf: str = "valid",
) -> ScreenDesign:
    return ScreenDesign(
        d_p_mm=d_p,
        d_h_mm=d_h,
        d_f_mm=0.4,
        low=LevelObs("valid", lmh, dp, cp, wall_s=wall_hf * 0.54),
        high=LevelObs(status_hf, None if status_hf != "valid" else lmh, dp, cp, wall_s=wall_hf),
        cost_ratio=0.54,
        cost_proxy_ratio=0.25,
    )


def _write_uniform_screen(path: Path, *, wall_hf: float, wall_lf: float) -> None:
    rows = ["row_type,d_p_mm,d_h_mm,d_f_mm,status_hf,wall_s_hf,wall_s_lf,cost_ratio,cost_proxy_ratio"]
    for d_p in (0.6, 0.8, 1.0):
        for d_h in (0.0, 0.15, 0.3):
            rows.append(
                f"design,{d_p},{d_h},0.4,valid,{wall_hf},{wall_lf},"
                f"{wall_lf / wall_hf},0.25"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _v1_with_screen(tmp_path: Path) -> Path:
    csv_path = tmp_path / "screen.csv"
    _write_uniform_screen(csv_path, wall_hf=3600.0, wall_lf=1944.0)
    payload = json.loads(V1.read_text(encoding="utf-8"))
    payload["screen_csv"] = csv_path.name
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
