"""3D pillar BO. Offline tables and fake drivers; nothing here launches Fluent."""

from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

import pytest

from helpers import REPO_ROOT, load_module
from ro.mfbo_3d import (
    REQUIRED_FIELDS,
    STATUS_DIVERGED,
    build_campaign_replay_table,
    campaign_pillar_diameter_mm,
    campaign_pillar_geo_ids,
    experiment_dir,
    geometry_block_reason,
    load_experiment,
    run_optimization,
)
from ro.mfbo_adapter import EXAMPLE_FIDELITY_TABLE, LF_MESH_ID

MESH = dict(EXAMPLE_FIDELITY_TABLE["LF"])
RUN_ID = "u0p2_p6M"
CP_LIMIT = 0.05
DP_LIMIT = 20000.0
REFERENCE = 100.0


def test_campaign_pillar_tokens_are_the_nine_diameters() -> None:
    assert campaign_pillar_diameter_mm("P_p60_h00") == (60 / 100, 0 / 100)
    assert campaign_pillar_diameter_mm("P_p80_h15") == (80 / 100, 15 / 100)
    assert campaign_pillar_diameter_mm("P_p100_h30") == (100 / 100, 30 / 100)
    assert len(campaign_pillar_geo_ids()) == 9


def test_opening_gap_sliver_is_blocked_and_a_campaign_bore_is_not() -> None:
    assert geometry_block_reason(0.8, 0.2, 0.4, 0.006) is not None
    assert geometry_block_reason(0.8, 0.15, 0.4, 0.006) is None
    assert geometry_block_reason(0.8, 0.9, 0.4, 0.006) is not None


def test_example_config_has_every_field_and_refuses_placeholders(tmp_path: Path) -> None:
    path = REPO_ROOT / "configs" / "mfbo_3d_experiment.example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == set(REQUIRED_FIELDS)
    assert payload["cp_excess_max"] == "TBD"
    assert payload["dp_max_pa_per_m"] == "TBD"
    with pytest.raises(ValueError, match="cp_excess_max"):
        load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    payload["cp_excess_max"] = CP_LIMIT
    payload["dp_max_pa_per_m"] = DP_LIMIT
    payload.pop("budget_hf_equivalent")
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="budget_hf_equivalent"):
        load_experiment(broken, repo_root=REPO_ROOT, data_root=tmp_path)


def test_offline_replay_reports_a_feasible_hf_point_and_skips_a_sliver(tmp_path: Path) -> None:
    for method in ("hf_bo", "mfbo"):
        config = _load(tmp_path, method=method, name=method)
        calls: list[tuple[float, float, str]] = []

        def evaluator(design, fidelity, table=config.replay, seen=calls):
            seen.append((design.d_p_mm, design.d_h_mm, fidelity))
            return table(design, fidelity)

        destination = tmp_path / "studies" / method
        summary = run_optimization(config, evaluator, destination)
        final = summary["final_hf_validated"]
        assert final["hf_lmh"] == pytest.approx(30.0)
        assert final["d_p_mm"] == pytest.approx(0.6)
        assert final["d_h_mm"] == pytest.approx(0.0)
        assert final["hf_cp_excess"] <= CP_LIMIT
        assert final["hf_pressure_drop_per_length_pa_per_m"] <= DP_LIMIT
        history = json.loads((destination / "history.json").read_text(encoding="utf-8"))
        assert (destination / "config.json").is_file()
        assert (destination / "history.csv").is_file()
        assert (destination / "final_summary.json").is_file()
        diverged = [row for row in history if row["status"] == STATUS_DIVERGED]
        assert len(diverged) == 1
        assert diverged[0]["lmh"] == 999.0
        assert diverged[0]["feasible"] is False
        assert all(row["fidelity"] != "LF" for row in history) or method == "mfbo"
        if method == "hf_bo":
            assert all(row["fidelity"] == "HF" for row in history)
        else:
            assert any(row["fidelity"] == "LF" for row in history)
        assert not any(row["d_h_mm"] == pytest.approx(0.2) for row in history)
        assert not any(abs(call[1] - 0.2) < 1.0e-9 for call in calls)
        keys = [(round(row["d_p_mm"], 6), round(row["d_h_mm"], 6), row["fidelity"]) for row in history]
        assert len(keys) == len(set(keys))
        assert summary["cumulative_hf_equivalent"] > 0.0
        assert all(row["reused_existing"] for row in history)


def test_replay_refuses_a_design_outside_the_table(tmp_path: Path) -> None:
    config = _load(tmp_path, method="hf_bo", name="miss")
    with pytest.raises(KeyError, match="offline_replay"):
        config.replay.lookup(0.7, 0.1, "HF")
    payload = json.loads((tmp_path / "miss.json").read_text(encoding="utf-8"))
    payload["initial_hf"] = [{"d_p_mm": 0.7, "d_h_mm": 0.1}]
    path = tmp_path / "outside.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    outside = load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    with pytest.raises(KeyError, match="0.7"):
        run_optimization(outside, outside.replay, tmp_path / "outside_run")


def test_resume_reloads_history_and_a_stop_file_halts(tmp_path: Path) -> None:
    config = _load(tmp_path, method="hf_bo", name="resume", max_sequential=4)
    destination = tmp_path / "resume_run"
    calls = {"n": 0}

    def evaluator(design, fidelity):
        calls["n"] += 1
        if calls["n"] == len(config.initial_hf):
            (destination / "STOP").write_text("stop\n", encoding="utf-8")
        return config.replay(design, fidelity)

    first = run_optimization(config, evaluator, destination)
    history = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    assert first["stopped"] is True
    assert len(history) == len(config.initial_hf)
    assert calls["n"] == len(config.initial_hf)
    second = run_optimization(config, evaluator, destination)
    assert second["stopped"] is True
    assert calls["n"] == len(config.initial_hf)
    (destination / "STOP").unlink()
    third = run_optimization(config, evaluator, destination)
    resumed = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    assert len(resumed) > len(history)
    assert resumed[: len(history)] == history
    keys = [(round(row["d_p_mm"], 6), round(row["d_h_mm"], 6), row["fidelity"]) for row in resumed]
    assert len(keys) == len(set(keys))
    assert [row["step"] for row in resumed] == list(range(1, len(resumed) + 1))
    assert third["final_hf_validated"]["hf_lmh"] == pytest.approx(30.0)
    assert third["stopped"] is False


def test_stop_file_before_start_writes_nothing(tmp_path: Path) -> None:
    config = _load(tmp_path, method="hf_bo", name="idle")
    destination = tmp_path / "idle_run"
    destination.mkdir()
    (destination / "STOP").write_text("stop\n", encoding="utf-8")

    def evaluator(design, fidelity):
        raise AssertionError("stop file should prevent evaluation")

    summary = run_optimization(config, evaluator, destination)
    assert summary["wrote"] is False
    assert not (destination / "history.json").is_file()


def test_changed_limit_refuses_resume(tmp_path: Path) -> None:
    config = _load(tmp_path, method="hf_bo", name="lock", max_sequential=0)
    destination = tmp_path / "lock_run"
    run_optimization(config, config.replay, destination)
    payload = json.loads((tmp_path / "lock.json").read_text(encoding="utf-8"))
    payload["cp_excess_max"] = 0.2
    path = tmp_path / "lock_changed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    changed = load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)
    with pytest.raises(ValueError, match="cp_excess_max"):
        run_optimization(changed, changed.replay, destination)


def test_builder_reads_campaign_leaves_and_omits_missing_ones(tmp_path: Path) -> None:
    _write_campaign_leaf(tmp_path, "P_p60_h00", diverged=False)
    _write_campaign_leaf(tmp_path, "P_p80_h15", diverged=True)
    table = build_campaign_replay_table(
        tmp_path,
        run_id=RUN_ID,
        mesh_id=LF_MESH_ID,
        fidelity="LF",
    )
    by_id = {row["geo_id"]: row for row in table["rows"]}
    assert set(by_id) == {"P_p60_h00", "P_p80_h15"}
    assert by_id["P_p60_h00"]["d_p_mm"] == pytest.approx(0.6)
    assert by_id["P_p60_h00"]["d_h_mm"] == pytest.approx(0.0)
    assert by_id["P_p60_h00"]["d_f_mm"] == pytest.approx(0.4)
    assert by_id["P_p60_h00"]["fidelity"] == "LF"
    assert by_id["P_p60_h00"]["status"] == "valid"
    assert by_id["P_p60_h00"]["lmh"] == pytest.approx(18.0)
    assert by_id["P_p80_h15"]["d_p_mm"] == pytest.approx(0.8)
    assert by_id["P_p80_h15"]["d_h_mm"] == pytest.approx(0.15)
    assert by_id["P_p80_h15"]["status"] == "diverged"
    assert by_id["P_p80_h15"]["lmh"] is None
    assert "P_p100_h30" in table["absent_geo_ids"]
    with pytest.raises(ValueError, match="No campaign pillar"):
        build_campaign_replay_table(
            tmp_path / "empty",
            run_id=RUN_ID,
            mesh_id=LF_MESH_ID,
            fidelity="LF",
        )


def test_live_evaluator_records_a_driver_failure(tmp_path: Path, monkeypatch) -> None:
    from ro.mfbo_3d import LiveEvaluator

    def boom(*_args, **_kwargs):
        raise RuntimeError("solver down")

    monkeypatch.setattr("ro.mfbo_3d.evaluate", boom)
    evaluator = LiveEvaluator(
        tmp_path,
        {"HF": MESH},
        RUN_ID,
        0.4,
        drivers=object(),
    )
    outcome = evaluator(type("Point", (), {"d_p_mm": 0.8, "d_h_mm": 0.0})(), "HF")
    assert outcome.status == "execution_failed"
    assert outcome.lmh is None
    assert "solver down" in (outcome.failure_reason or "")


def test_script_import_does_not_import_drivers_and_example_run_refuses(tmp_path: Path) -> None:
    source = (REPO_ROOT / "src" / "ro" / "mfbo_3d.py").read_text(encoding="utf-8")
    script_source = (REPO_ROOT / "scripts" / "mfbo" / "run_3d_mfbo.py").read_text(encoding="utf-8")
    assert "mfbo_drivers" not in source
    assert "ansys" not in source.casefold()
    tree = ast.parse(script_source)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "mfbo_drivers" not in node.module
            assert "ansys" not in node.module.casefold()
    script = load_module("run_3d_mfbo_under_test", REPO_ROOT / "scripts" / "mfbo" / "run_3d_mfbo.py")
    code = script.main(
        [
            "run",
            "--config",
            str(REPO_ROOT / "configs" / "mfbo_3d_experiment.example.json"),
            "--data-root",
            str(tmp_path),
        ]
    )
    assert code == 1
    out = tmp_path / "replay.json"
    root = tmp_path / "campaign"
    root.mkdir()
    _write_campaign_leaf(root, "P_p60_h00", diverged=False)
    built = script.main(
        [
            "build-replay",
            "--data-root",
            str(root),
            "--run-id",
            RUN_ID,
            "--mesh-id",
            LF_MESH_ID,
            "--fidelity",
            "LF",
            "--out",
            str(out),
        ]
    )
    assert built == 0
    table = json.loads(out.read_text(encoding="utf-8"))
    assert table["rows"][0]["geo_id"] == "P_p60_h00"
    assert experiment_dir(tmp_path, "hf_bo", "example") == (
        tmp_path / "studies" / "optimization" / "3d" / "hf_bo" / "example"
    )


def _load(directory: Path, *, method: str, name: str, max_sequential: int = 4):
    fidelity = directory / f"{name}_fidelity.json"
    replay = directory / f"{name}_replay.json"
    if not fidelity.is_file():
        fidelity.write_text(json.dumps({"LF": MESH, "HF": MESH}), encoding="utf-8")
        replay.write_text(json.dumps({"rows": _rows()}), encoding="utf-8")
    payload = {
        "experiment_id": "pilot3d",
        "method": method,
        "mode": "offline_replay",
        "seed": 0,
        "run_id": RUN_ID,
        "d_f_mm": 0.4,
        "design_box": {"d_p_mm": [0.6, 1.0], "d_h_mm": [0.0, 0.3]},
        "cp_excess_max": CP_LIMIT,
        "dp_max_pa_per_m": DP_LIMIT,
        "budget_hf_equivalent": 8.0,
        "max_sequential": max_sequential,
        "reference_hf_wall_s": REFERENCE,
        "lf_over_hf_cost_ratio": 0.25,
        "candidate_grid": 3,
        "initial_hf": [
            {"d_p_mm": 0.6, "d_h_mm": 0.0},
            {"d_p_mm": 0.6, "d_h_mm": 0.3},
            {"d_p_mm": 0.8, "d_h_mm": 0.15},
        ],
        "initial_lf": [
            {"d_p_mm": 0.6, "d_h_mm": 0.0},
            {"d_p_mm": 0.6, "d_h_mm": 0.3},
        ]
        if method == "mfbo"
        else [],
        "fidelity_table": str(fidelity),
        "replay_table": str(replay),
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_experiment(path, repo_root=REPO_ROOT, data_root=directory)


def _rows() -> list[dict[str, object]]:
    specs = [
        (0.6, 0.0, "HF", "valid", 30.0, 1.02, 10000.0, None),
        (0.6, 0.0, "LF", "valid", 31.0, 1.01, 9000.0, None),
        (0.6, 0.3, "HF", "valid", 40.0, 1.20, 10000.0, None),
        (0.6, 0.3, "LF", "valid", 41.0, 1.15, 9000.0, None),
        (0.8, 0.15, "HF", "diverged", 999.0, 1.01, 1000.0, "diverged"),
        (1.0, 0.0, "HF", "valid", 28.0, 1.02, 50000.0, None),
        (1.0, 0.3, "HF", "valid", 1.0, 1.50, 90000.0, None),
        (0.8, 0.2, "HF", "valid", 80.0, 1.01, 1000.0, None),
        (0.8, 0.2, "LF", "valid", 80.0, 1.01, 1000.0, None),
    ]
    rows = []
    for d_p, d_h, fidelity, status, lmh, cp, dp, reason in specs:
        if fidelity == "HF":
            mesh, solver, extract = 10.0, 80.0, 10.0
        else:
            mesh, solver, extract = 5.0, 15.0, 5.0
        rows.append(
            {
                "d_p_mm": d_p,
                "d_h_mm": d_h,
                "d_f_mm": 0.4,
                "fidelity": fidelity,
                "status": status,
                "failure_reason": reason,
                "lmh": lmh,
                "cp_average": cp,
                "pressure_drop_per_length_pa_per_m": dp,
                "mesh_wall_time_s": mesh,
                "solver_wall_time_s": solver,
                "extraction_wall_time_s": extract,
            }
        )
    return rows


def _write_campaign_leaf(root: Path, geo_id: str, *, diverged: bool) -> None:
    run_dir = root / "runs" / "pillar" / geo_id / LF_MESH_ID / RUN_ID
    mesh_dir = root / "meshes" / "pillar" / geo_id / LF_MESH_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    mesh_dir.mkdir(parents=True, exist_ok=True)
    if diverged:
        manifest = {"stop_reason": "diverged", "convergence_quality": "FAIL"}
    else:
        manifest = {
            "stop_reason": "residual_converged",
            "convergence_quality": "PASS",
            "solver_wall_time_s": 10.0,
            "extraction_wall_time_s": 2.0,
        }
        row = {
            "lmh_mass_balance": "25.0",
            "lmh_window_module": "18.0",
            "area_mem": "1.0e-5",
            "pressure_drop_spacer_per_m": "30000",
            "cpc_window_avg_flux": "1.08",
        }
        path = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        (mesh_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "mesh_wall_time_s": 3.0,
                    "cell_count": 10,
                    "n_active_cells": 7,
                    "cell_length_x_m": 0.003465,
                    "periodic_shift_y_m": 0.003465,
                }
            ),
            encoding="utf-8",
        )
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
