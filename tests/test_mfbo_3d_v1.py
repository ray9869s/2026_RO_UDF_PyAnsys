"""LF_d / HF_m table, nine-design screen, and run accounting. No Fluent."""

from __future__ import annotations

import json

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.hf_monitor_stop import STOP_REASON_HF_MONITOR_STABLE
from ro.mfbo_adapter import (
    FAMILY,
    _finish,
    mesh_id_for_settings,
    opening_gap_mm,
)
from ro.mfbo_fidelity import (
    WINDOW_SOURCE_FAMILY_DEFAULT,
    WINDOW_SOURCE_FIDELITY_TABLE,
    extraction_cli_args,
    fidelity_settings_used,
    parse_fidelity_level,
    run_id_for_level,
    solver_cli_args,
)
from ro.mfbo_fidelity_screen import (
    V1_LEVELS,
    build_named_level_queue,
    load_designs,
    load_fidelity_table as load_screen_table,
    require_designs_clear_opening_gap,
    summarize_v1,
)
from ro.solver_common import cost_proxy, iterations_run_from_case

TABLE_PATH = REPO_ROOT / "configs" / "mfbo_fidelity_table.3d_v1.json"
LF_SHORT_PATH = REPO_ROOT / "configs" / "mfbo_fidelity_table.lf_short.example.json"
DESIGNS_PATH = REPO_ROOT / "configs" / "mfbo_3d_screen_designs.json"
RUN_ID = "u0p2_p6M"


def _table():
    return load_screen_table(TABLE_PATH)


def _levels():
    table = _table()
    return parse_fidelity_level(table["LF_d"]), parse_fidelity_level(table["HF_m"])


def test_lf_d_shares_the_lf_a_mesh_and_uses_the_lf_stop():
    short = json.loads(LF_SHORT_PATH.read_text(encoding="utf-8"))
    lf_a = parse_fidelity_level(short["LF_a"])
    lf_d, _hf_m = _levels()
    assert mesh_id_for_settings(lf_d) == mesh_id_for_settings(lf_a)
    assert mesh_id_for_settings(lf_d) == "max085_min006_cpg5_bl4_peel2"
    assert lf_d["n_active"] == 3
    assert lf_d["n_lead_excluded"] == 1
    assert lf_d["extract_profile"] == "mfbo"
    assert run_id_for_level(RUN_ID, lf_d) == "u0p2_p6M_lfd"
    assert lf_d["solver_overrides"]["max_iterations"] == 600
    assert lf_d["solver_overrides"]["lf_monitor_stop"] == {
        "tol_lmh": 0.001,
        "tol_dp": 0.005,
        "n": 100,
    }
    assert lf_d["convergence_gate"] == {"mode": "lf_monitor"}
    used = fidelity_settings_used(lf_d)
    assert used["window_source"] == WINDOW_SOURCE_FIDELITY_TABLE


def test_hf_m_uses_the_hf_mesh_and_the_pillar_family_window():
    short = json.loads(LF_SHORT_PATH.read_text(encoding="utf-8"))
    hf = parse_fidelity_level(short["HF"])
    _lf_d, hf_m = _levels()
    assert mesh_id_for_settings(hf_m) == mesh_id_for_settings(hf)
    assert mesh_id_for_settings(hf_m) == "max085_min006_cpg5_bl12s4_peel2"
    assert hf_m["n_active"] == 7
    assert hf_m["n_lead_excluded"] is None
    assert hf_m["window_source"] == WINDOW_SOURCE_FAMILY_DEFAULT
    assert hf_m["extract_profile"] == "mfbo"
    assert run_id_for_level(RUN_ID, hf_m) == "u0p2_p6M_hfm"
    assert "--fidelity-n-lead-excluded" not in extraction_cli_args(hf_m)
    assert "--window-source" in extraction_cli_args(hf_m)
    assert "family_default" in extraction_cli_args(hf_m)
    args = solver_cli_args(hf_m)
    assert "--hf-monitor-stop" in args
    spec = json.loads(args[args.index("--hf-monitor-stop") + 1])
    assert spec == {
        "continuity_max": 1e-5,
        "n": 200,
        "tol_dp": 1e-4,
        "tol_lmh": 1e-4,
    }
    used = fidelity_settings_used(hf_m)
    assert used["window_source"] == WINDOW_SOURCE_FAMILY_DEFAULT
    assert used["convergence_gate"]["lmh_rel_abs_max"]["source"] == "production"
    assert used["convergence_gate"]["continuity_final_max"]["source"] == "production"
    assert used["solver"]["residual_target"]["source"] == "production"
    assert used["solver"]["qoi_stop_criterion"]["source"] == "production"
    assert "convergence_gate" not in _table()["HF_m"]


def test_hf_monitor_stop_refuses_the_lf_gate():
    payload = _table()["HF_m"]
    broken = json.loads(json.dumps(payload))
    broken["convergence_gate"] = {"mode": "lf_monitor"}
    with pytest.raises(ValueError, match="production convergence gate"):
        parse_fidelity_level(broken)


def test_nine_designs_clear_the_opening_gap():
    designs = load_designs(DESIGNS_PATH)
    assert len(designs) == 9
    pairs = {(design.d_p_mm, design.d_h_mm) for design in designs}
    assert pairs == {
        (0.6, 0.0),
        (0.6, 0.15),
        (0.6, 0.3),
        (0.8, 0.0),
        (0.8, 0.15),
        (0.8, 0.3),
        (1.0, 0.0),
        (1.0, 0.15),
        (1.0, 0.3),
    }
    assert {design.d_f_mm for design in designs} == {0.4}
    gaps = [
        opening_gap_mm(design.d_p_mm, design.d_h_mm, design.d_f_mm)
        for design in designs
    ]
    assert all(not (0.0 < gap < 0.006) for gap in gaps)
    positive = [gap for gap in gaps if gap > 0.0]
    assert positive
    assert min(positive) >= 0.006
    require_designs_clear_opening_gap(designs, _table())


def test_v1_queue_is_eighteen_jobs_and_does_not_evaluate(monkeypatch, tmp_path):
    def explode(*_args, **_kwargs):
        raise AssertionError("emit must not evaluate")

    monkeypatch.setattr("ro.mfbo_fidelity_screen.evaluate", explode)
    designs = load_designs(DESIGNS_PATH)
    jobs = build_named_level_queue(
        designs,
        V1_LEVELS,
        _table(),
        run_id=RUN_ID,
        data_root=tmp_path,
        fidelity_table=TABLE_PATH,
    )
    assert len(jobs) == 18
    assert jobs[0]["id"].startswith("LF_d-")
    assert jobs[1]["id"].startswith("HF_m-")
    assert "MFP_d0600_h0000_f0400_n3" in jobs[0]["id"]
    assert jobs[1]["id"].endswith("MFP_d0600_h0000_f0400")
    assert "--fidelity" in jobs[0]["argv"]
    screen = load_module(
        "run_3d_fidelity_screen_v1",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    queue = tmp_path / "queue.json"
    code = screen.main(
        [
            "--designs",
            str(DESIGNS_PATH),
            "--fidelity-table",
            str(TABLE_PATH),
            "--data-root",
            str(tmp_path),
            "--run-id",
            RUN_ID,
            "--emit-queue",
            str(queue),
        ]
    )
    assert code == 0
    loaded = json.loads(queue.read_text(encoding="utf-8"))
    assert len(loaded) == 18


def test_v1_summary_reports_correlation_bias_and_both_cost_ratios():
    def record(level, d_p, d_h, lmh, cp, cells, iterations):
        return {
            "status": "valid",
            "fidelity": level,
            "geo_id": f"{level}-{d_p}-{d_h}",
            "d_p_mm": d_p,
            "d_h_mm": d_h,
            "d_f_mm": 0.4,
            "run_id": "u0p2_p6M_lfd" if level == "LF_d" else "u0p2_p6M_hfm",
            "lmh": lmh,
            "lmh_module_area": lmh * 2.0,
            "pressure_drop_per_length_pa_per_m": 1000.0 / lmh,
            "cp_average": cp,
            "cell_count": cells,
            "iterations_run": iterations,
            "cost_proxy": cells * iterations,
            "generate_wall_time_s": 1.0,
            "mesh_wall_time_s": 2.0,
            "solver_wall_time_s": 3.0,
            "extraction_wall_time_s": 4.0,
            "failure_reason": None,
        }

    pairs = []
    excess_rels = []
    for index, (d_p, d_h) in enumerate(((0.6, 0.0), (0.8, 0.15), (1.0, 0.3))):
        high_lmh = 20.0 + index
        low_cp = 1.10 + index * 0.01
        high_cp = 1.20 + index * 0.02
        excess_rels.append(((low_cp - 1.0) - (high_cp - 1.0)) / (high_cp - 1.0))
        pairs.append(
            (
                record("LF_d", d_p, d_h, high_lmh * 0.99, low_cp, 100, 400),
                record("HF_m", d_p, d_h, high_lmh, high_cp, 1000, 600),
            )
        )
    report = summarize_v1(pairs)
    assert report["n_comparable"] == 3
    assert report["qoi"]["lmh"]["pearson"] == pytest.approx(1.0)
    assert report["qoi"]["lmh"]["spearman"] == pytest.approx(1.0)
    assert report["qoi"]["cp_average"]["pearson"] is not None
    assert report["qoi"]["pressure_drop_per_length_pa_per_m"]["spearman"] is not None
    assert report["cp_excess_bias_mean"] == pytest.approx(sum(excess_rels) / len(excess_rels))
    assert report["cp_excess_bias_spread"] is not None
    assert report["cost_ratio"]["median"] == pytest.approx(10.0 / 10.0)
    assert report["cost_proxy_ratio"]["median"] == pytest.approx((100 * 400) / (1000 * 600))
    row = report["rows"][0]
    assert row["lmh_rel"] == pytest.approx((20.0 * 0.99 - 20.0) / 20.0)
    assert row["iterations_run_lf"] == 400
    assert row["cell_count_hf"] == 1000
    assert row["cost_proxy_lf"] == 40000


def test_finish_records_iterations_cells_wall_time_and_cost_proxy(tmp_path, monkeypatch):
    from test_manifest import write_test_run

    given = tmp_path / "mfbo"
    given.mkdir()
    monkeypatch.setenv("RO_DATA_ROOT", str(given))
    directory = write_test_run()
    other = tmp_path / "process_default"
    other.mkdir()
    monkeypatch.setenv("RO_DATA_ROOT", str(other))
    geo_id = "MFP_d0800_h0000_f0400"
    sidecar = given / "geometries" / FAMILY / geo_id / f"{geo_id}_generate_wall_time.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps({"generate_wall_time_s": 3.5}) + "\n",
        encoding="utf-8",
    )
    record = _finish(
        {"geo_id": geo_id},
        status="valid",
        failure_reason=None,
        run_dir=directory,
        data_root=given,
        mesh_manifest={"cell_count": 100},
        run_manifest={
            "iterations_run": 40,
            "stop_reason": "residual_converged",
            "solver_wall_time_s": 2.0,
        },
    )
    assert record["iterations_run"] == 40
    assert record["cell_count"] == 100
    assert record["stop_reason"] == "residual_converged"
    assert record["generate_wall_time_s"] == pytest.approx(3.5)
    assert record["cost_proxy"] == cost_proxy(100, 40)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert payload["iterations_run"] == 40
    assert payload["cell_count"] == 100
    assert payload["generate_wall_time_s"] == pytest.approx(3.5)
    assert payload["cost_proxy"] == 4000
    import os

    assert os.environ["RO_DATA_ROOT"] == str(other)


def test_iterations_run_comes_from_the_lmh_report(tmp_path):
    report = tmp_path / "lmh_udm_avg.out"
    report.write_text('"iteration" "lmh"\n1 1.0\n594 1.1\n', encoding="utf-8")
    assert iterations_run_from_case(tmp_path) == 594


def test_hf_monitor_stable_is_a_completed_stop():
    assert STOP_REASON_HF_MONITOR_STABLE == "hf_monitor_stable"
