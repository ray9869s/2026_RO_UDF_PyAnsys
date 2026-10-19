"""Exported 3D leaves and the synthetic cost-ratio benchmark. No Fluent."""

from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

import pytest

from helpers import REPO_ROOT, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_3d import (
    STATUS_VALID,
    Dataset,
    PillarOutcome,
    PillarPoint,
    _run_validity_model,
    _x,
    load_experiment,
    probit_validity,
)
from ro.mfbo_dataset import (
    HF_M,
    LF_D,
    collect_observations,
    read_dataset,
    write_dataset,
)
from ro.mfbo_synthetic import (
    break_even,
    fit_surrogate,
    in_live_optimum_region,
    run_benchmark,
)

MESH = "max085_min006_cpg5_bl4_peel2"


def test_export_reads_module_window_lmh_and_keeps_sub_micrometre_bores(tmp_path: Path) -> None:
    manifest = {
        "stop_reason": "hf_monitor_stable",
        "convergence_quality": "PASS",
        "solver_wall_time_s": 70.0,
        "extraction_wall_time_s": 10.0,
    }
    text = json.dumps(manifest)
    geo = format_mfbo_pillar_geo_id(0.75, 0.05625, 0.4, n_active=7)
    _leaf(
        tmp_path,
        geo,
        "u0p2_p6M_hfm",
        manifest,
        lmh="24.414",
        cp="1.039",
        dp="71000",
        meta_d_h=0.05625,
    )
    before = (tmp_path / "runs" / "pillar" / geo / MESH / "u0p2_p6M_hfm" / "manifest.json").read_text()
    assert before == text
    _leaf(
        tmp_path,
        format_mfbo_pillar_geo_id(1.0, 0.0, 0.4, n_active=7),
        "u0p2_p6M_hfm",
        {
            "stop_reason": "max_iter_reached",
            "convergence_quality": "FAIL",
            "convergence_quality_failures": ["continuity_final"],
            "solver_wall_time_s": 370.0,
        },
        lmh="20.0",
        cp="1.05",
        dp="90000",
    )
    _leaf(
        tmp_path,
        format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=3),
        "u0p2_p6M_lfd",
        {"stop_reason": "diverged", "convergence_quality": "FAIL"},
        summary=False,
    )
    archived = (
        tmp_path
        / "runs"
        / "pillar"
        / geo
        / MESH
        / "u0p2_p6M_hfm_run_failed_20261019T000000Z"
    )
    archived.mkdir(parents=True)
    plain = tmp_path / "runs" / "pillar" / geo / MESH / "u0p2_p6M"
    plain.mkdir()
    (plain / "manifest.json").write_text("{}\n", encoding="utf-8")

    rows = collect_observations(tmp_path, run_id="u0p2_p6M")
    after = (tmp_path / "runs" / "pillar" / geo / MESH / "u0p2_p6M_hfm" / "manifest.json").read_text()
    assert after == text
    by_status = {row.status for row in rows}
    assert by_status == {"valid", "gate_fail", "crash"}
    valid = next(row for row in rows if row.status == "valid")
    assert valid.fidelity == HF_M
    assert valid.d_h_mm == pytest.approx(0.05625)
    assert valid.lmh == pytest.approx(24.414)
    assert valid.cp_excess == pytest.approx(0.039)
    assert valid.dp_per_l == pytest.approx(71000)
    assert valid.wall_s == pytest.approx(80.0)
    assert all(row.run_id != "u0p2_p6M" for row in rows)
    destination = write_dataset(tmp_path / "dataset.csv", rows)
    reread = read_dataset(destination)
    assert len(reread) == len(rows)
    assert reread[0].d_h_mm == pytest.approx(rows[0].d_h_mm)


def test_export_script_writes_the_csv_and_does_not_import_fluent(tmp_path: Path) -> None:
    source = (REPO_ROOT / "scripts" / "mfbo" / "export_3d_dataset.py").read_text(encoding="utf-8")
    benchmark = (REPO_ROOT / "scripts" / "mfbo" / "synthetic_3d_benchmark.py").read_text(encoding="utf-8")
    for text in (source, benchmark):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "mfbo_drivers" not in node.module
                assert "ansys" not in node.module.casefold()
    geo = format_mfbo_pillar_geo_id(0.6, 0.0, 0.4)
    _leaf(
        tmp_path,
        geo,
        "u0p2_p6M_hfm",
        {
            "stop_reason": "residual_converged",
            "convergence_quality": "PASS",
            "solver_wall_time_s": 10.0,
            "extraction_wall_time_s": 1.0,
        },
        lmh="18.0",
        cp="1.02",
        dp="40000",
    )
    script = load_module("export_3d_dataset_under_test", REPO_ROOT / "scripts" / "mfbo" / "export_3d_dataset.py")
    out = tmp_path / "leaves.csv"
    assert script.main(["--data-root", str(tmp_path), "--out", str(out)]) == 0
    with out.open(encoding="utf-8", newline="") as handle:
        body = list(csv.DictReader(handle))
    assert body[0]["status"] == "valid"
    assert body[0]["fidelity"] == "HF_m"
    assert float(body[0]["lmh"]) == pytest.approx(18.0)


def test_surrogate_keeps_the_planted_peak_and_charges_a_gate_failure(tmp_path: Path) -> None:
    rows = _planted_rows()
    surrogate = fit_surrogate(rows, repo_root=REPO_ROOT)
    best = surrogate.best_feasible()
    assert best is not None
    assert in_live_optimum_region(best["d_p_mm"], best["d_h_mm"], box=surrogate.box, grid=surrogate.grid)
    failed = surrogate.outcome(1.0, 0.0, "HF", cost_ratio=0.54)
    assert failed.status == "invalid"
    assert failed.solver_wall_time_s == pytest.approx(370.0)
    assert failed.trains_gp is False
    low = surrogate.outcome(0.75, 0.05625, "LF", cost_ratio=0.54)
    assert low.status == "valid"
    assert low.measured_wall_s() == pytest.approx(0.54 * 100.0)
    assert surrogate.reference_hf_wall_s == pytest.approx(100.0)
    assert surrogate.failure_hf_wall_s == pytest.approx(370.0)


def test_failure_aware_probability_is_low_on_a_run_failure(tmp_path: Path) -> None:
    config = _small_experiment(tmp_path)
    dataset = Dataset()
    dataset.add(
        PillarOutcome(
            d_p_mm=0.75,
            d_h_mm=0.05625,
            fidelity="HF",
            status=STATUS_VALID,
            lmh=24.0,
            cp_average=1.03,
            dp_per_l=70000.0,
        )
    )
    dataset.add(
        PillarOutcome(
            d_p_mm=1.0,
            d_h_mm=0.0,
            fidelity="HF",
            status="invalid",
            failure_reason="convergence_quality",
        )
    )
    model = _run_validity_model(config, dataset)
    assert model is not None
    failed = _probability(model, config, 1.0, 0.0)
    held = _probability(model, config, 0.75, 0.05625)
    assert failed < 0.5
    assert held > 0.5
    assert probit_validity(1.0, 0.0) == 1.0
    assert probit_validity(0.0, 0.0) == 0.0
    assert _run_validity_model(config, Dataset()) is None


def test_benchmark_writes_medians_and_the_live_comparison(tmp_path: Path) -> None:
    rows = _planted_rows()
    result = run_benchmark(
        rows,
        tmp_path / "out",
        repo_root=REPO_ROOT,
        seeds=(0, 1),
        ratios=(0.54, 0.25),
        budget_hf_equivalent=5.5,
        max_sequential=2,
    )
    summary = result["summary"]
    plain = [
        row
        for row in summary
        if row["metric"] == "cost_to_near_optimum" and row["failure_aware"] is False
    ]
    assert {row["method"] for row in plain} == {"hf_bo", "mfbo"}
    assert all(row["n_runs"] == 2 for row in plain)
    assert (tmp_path / "out" / "summary.csv").is_file()
    assert (tmp_path / "out" / "cost_ratio.png").stat().st_size > 0
    text = (tmp_path / "out" / "validation.md").read_text(encoding="utf-8")
    assert "0.75" in text
    assert "0.05625" in text
    assert "same optimum region" in text.casefold() or "Same optimum region" in text
    validation = result["validation"]
    assert validation["truth_best"]["d_p_mm"] == pytest.approx(0.75, abs=0.025)
    seed0 = next(
        run
        for run in result["runs"]
        if run["method"] == "hf_bo" and run["seed"] == 0 and run["failure_aware"] is False and run["cost_ratio"] == 0.54
    )
    assert seed0["cost_to_first_feasible"] is not None
    aware = json.loads(
        (
            tmp_path
            / "out"
            / "runs"
            / "hf_bo_aware_r0p54_s0"
            / "final_summary.json"
        ).read_text(encoding="utf-8")
    )
    plain_summary = json.loads(
        (
            tmp_path
            / "out"
            / "runs"
            / "hf_bo_plain_r0p54_s0"
            / "final_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert aware["failure_aware"] is True
    assert "P(valid HF)" in aware["acquisition"]
    assert plain_summary["failure_aware"] is False
    assert "P(valid HF)" not in plain_summary["acquisition"]


def test_break_even_interpolates_the_crossing() -> None:
    hf = {0.54: 10.0, 0.4: 10.0, 0.25: 10.0, 0.1: 10.0}
    mf = {0.54: 14.0, 0.4: 11.0, 0.25: 8.0, 0.1: 5.0}
    assert break_even(hf, mf) == pytest.approx(0.35)
    assert break_even(hf, {ratio: 12.0 for ratio in hf}) is None


def _probability(model, config, d_p_mm: float, d_h_mm: float) -> float:
    import numpy as np

    x = np.asarray([_x(config, PillarPoint(d_p_mm, d_h_mm))], dtype=float)
    mean, variance = model.predict(x)
    return probit_validity(float(mean[0]), float(variance[0]))


def _small_experiment(tmp_path: Path):
    payload = json.loads((REPO_ROOT / "configs" / "mfbo_3d_experiment.v2.json").read_text(encoding="utf-8"))
    payload["reference_hf_wall_s"] = 100.0
    payload["mode"] = "live"
    payload["replay_table"] = None
    payload.pop("screen_csv", None)
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_experiment(path, repo_root=REPO_ROOT, data_root=tmp_path)


def _fields(d_p: float, d_h: float) -> tuple[float, float, float]:
    """Smooth peak at the live v2 point. Feasible only in that neighborhood."""
    radius_p = d_p - 0.75
    radius_h = d_h - 0.05625
    lmh = 24.414 - 30.0 * radius_p**2 - 180.0 * radius_h**2
    cp_excess = 0.030 + 0.8 * radius_p**2 + 4.0 * radius_h**2
    dp_per_l = 68000.0 + 250000.0 * radius_p**2 + 1200000.0 * radius_h**2
    return lmh, cp_excess, dp_per_l


def _planted_rows():
    from ro.mfbo_dataset import Observation

    designs = [(0.75, 0.05625)]
    for index_p in range(5):
        for index_h in range(5):
            designs.append((0.6 + 0.1 * index_p, 0.075 * index_h))
    for index_p in range(3):
        for index_h in range(3):
            designs.append((0.725 + 0.025 * index_p, 0.0375 + 0.01875 * index_h))
    seen: set[tuple[float, float]] = set()
    rows = []
    for d_p, d_h in designs:
        key = (round(d_p, 6), round(d_h, 6))
        if key in seen or key in {(1.0, 0.0), (1.0, 0.01875)}:
            continue
        seen.add(key)
        lmh, cp_excess, dp_per_l = _fields(d_p, d_h)
        for fidelity, shift in ((HF_M, 0.0), (LF_D, -0.4)):
            rows.append(
                Observation(
                    d_p_mm=d_p,
                    d_h_mm=d_h,
                    d_f_mm=0.4,
                    fidelity=fidelity,
                    status="valid",
                    lmh=lmh + shift,
                    cp_excess=cp_excess,
                    dp_per_l=dp_per_l,
                    mesh_wall_time_s=20.0,
                    solver_wall_time_s=70.0,
                    extraction_wall_time_s=10.0,
                    wall_s=100.0,
                    recorded_cost_s=100.0,
                )
            )
    for d_h in (0.0, 0.01875):
        rows.append(
            Observation(
                d_p_mm=1.0,
                d_h_mm=d_h,
                d_f_mm=0.4,
                fidelity=HF_M,
                status="gate_fail",
                solver_wall_time_s=370.0,
                wall_s=370.0,
                recorded_cost_s=370.0,
                failure_reason="convergence_quality",
            )
        )
        lmh, cp_excess, dp_per_l = _fields(1.0, d_h)
        rows.append(
            Observation(
                d_p_mm=1.0,
                d_h_mm=d_h,
                d_f_mm=0.4,
                fidelity=LF_D,
                status="valid",
                lmh=lmh - 0.4,
                cp_excess=cp_excess,
                dp_per_l=dp_per_l,
                wall_s=54.0,
                recorded_cost_s=54.0,
            )
        )
    return rows


def _leaf(
    root: Path,
    geo_id: str,
    run_id: str,
    manifest: dict,
    *,
    lmh: str = "18.0",
    cp: str = "1.04",
    dp: str = "50000",
    summary: bool = True,
    meta_d_h: float | None = None,
) -> None:
    run_dir = root / "runs" / "pillar" / geo_id / MESH / run_id
    mesh_dir = root / "meshes" / "pillar" / geo_id / MESH
    run_dir.mkdir(parents=True, exist_ok=True)
    mesh_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (mesh_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mesh_wall_time_s": 0.0,
                "cell_count": 10,
                "n_active_cells": 7,
                "cell_length_x_m": 0.003465,
                "periodic_shift_y_m": 0.003465,
            }
        ),
        encoding="utf-8",
    )
    if summary:
        row = {
            "lmh_mass_balance": "25.0",
            "lmh_window_module": lmh,
            "area_mem": "1.0e-5",
            "pressure_drop_spacer_per_m": dp,
            "cpc_window_avg_flux": cp,
        }
        path = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    if meta_d_h is not None:
        geo_dir = root / "geometries" / "pillar" / geo_id
        geo_dir.mkdir(parents=True, exist_ok=True)
        (geo_dir / f"{geo_id}_meta.json").write_text(
            json.dumps(
                {
                    "geo_id": geo_id,
                    "inputs": {"d_p_mm": 0.75, "d_h_mm": meta_d_h, "d_f_mm": 0.4},
                }
            ),
            encoding="utf-8",
        )
