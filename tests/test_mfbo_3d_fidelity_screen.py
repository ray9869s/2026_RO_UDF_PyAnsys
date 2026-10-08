"""3D LF/HF screen. Evaluations are fakes; nothing here launches Fluent."""

from __future__ import annotations

import json

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_adapter import LF_MESH_ID, LF_MESH_SETTINGS, PillarDesign
from ro.mfbo_fidelity_screen import (
    build_queue,
    load_fidelity_table,
    require_runnable_fidelity,
    require_screen_table,
    run_screen,
    summarize,
    write_queue,
    write_summary,
)

EXAMPLE = REPO_ROOT / "configs" / "mfbo_fidelity_table.example.json"
RUN_ID = "u0p2_p6M"
HF_SETTINGS = {
    "m_max": 0.045,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 8,
    "peel_layers": 2,
}
DESIGNS = (
    PillarDesign(0.8, 0.0, 0.4),
    PillarDesign(0.9, 0.0, 0.4),
    PillarDesign(1.0, 0.0, 0.4),
)


def _table():
    return {"LF": dict(LF_MESH_SETTINGS), "HF": dict(HF_SETTINGS)}


def _record(design, fidelity, *, lmh, module, pressure, cp, wall, status="valid"):
    mesh_id = LF_MESH_ID if fidelity == "LF" else "max045_min006_cpg5_bl8_peel2"
    return {
        "geo_id": format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm),
        "mesh_id": mesh_id,
        "run_id": RUN_ID,
        "fidelity": fidelity,
        "d_p_mm": design.d_p_mm,
        "d_h_mm": design.d_h_mm,
        "d_f_mm": design.d_f_mm,
        "status": status,
        "failure_reason": None if status == "valid" else "diverged",
        "lmh": lmh,
        "lmh_module_area": module,
        "pressure_drop_per_length_pa_per_m": pressure,
        "cp_average": cp,
        "mesh_wall_time_s": 1.0,
        "solver_wall_time_s": wall - 2.0,
        "extraction_wall_time_s": 1.0,
    }


def _linear_pairs():
    """LF = half of HF for LMH, module LMH, dP/L, and CP-1. Walls 10/20/30 vs 100."""
    lows = (1.0, 2.0, 3.0)
    highs = (2.0, 4.0, 6.0)
    walls = (10.0, 20.0, 30.0)
    pairs = []
    for design, low, high, wall in zip(DESIGNS, lows, highs, walls, strict=True):
        pairs.append(
            (
                _record(
                    design,
                    "LF",
                    lmh=low,
                    module=low,
                    pressure=low,
                    cp=1.0 + low,
                    wall=wall,
                ),
                _record(
                    design,
                    "HF",
                    lmh=high,
                    module=high,
                    pressure=high,
                    cp=1.0 + high,
                    wall=100.0,
                ),
            )
        )
    return pairs


def test_example_table_is_production_lf_and_tbd_hf():
    payload = load_fidelity_table(EXAMPLE)
    assert payload["HF"] == "TBD"
    assert require_runnable_fidelity(payload, "LF")["m_max"] == pytest.approx(0.085)
    assert require_runnable_fidelity(payload, "LF")["peel_layers"] == 2
    from ro.mfbo_adapter import mesh_id_for_settings

    assert mesh_id_for_settings(payload["LF"]) == LF_MESH_ID
    with pytest.raises(ValueError, match="TBD"):
        require_runnable_fidelity(payload, "HF")
    with pytest.raises(ValueError, match="TBD"):
        require_screen_table(payload)


def test_summary_uses_the_2d_definitions():
    report = summarize(_linear_pairs())
    assert report["n_comparable"] == 3
    for name in (
        "lmh",
        "lmh_module_area",
        "pressure_drop_per_length_pa_per_m",
        "cp_average",
    ):
        summary = report["qoi"][name]
        assert summary["n"] == 3
        assert summary["pearson"] == pytest.approx(1.0)
        assert summary["spearman"] == pytest.approx(1.0)
        assert summary["mean_signed_relative_lf_bias"] == pytest.approx(-0.5)
        assert summary["rank_reversals"] == 0
        assert summary["rank_comparisons"] == 3
    assert report["qoi"]["cp_average"]["compared_as"] == "cp_average - 1"
    assert report["qoi"]["lmh"]["larger_is_better"] is True
    assert report["qoi"]["pressure_drop_per_length_pa_per_m"]["larger_is_better"] is False
    assert report["qoi"]["cp_average"]["larger_is_better"] is False
    assert report["cost_ratio"]["n"] == 3
    assert report["cost_ratio"]["median"] == pytest.approx(0.2)


def test_rank_reversal_counts_strict_disagreements():
    designs = DESIGNS
    lf_lmh = (1.0, 2.0, 3.0)
    hf_lmh = (3.0, 2.0, 1.0)
    pairs = []
    for design, low, high in zip(designs, lf_lmh, hf_lmh, strict=True):
        pairs.append(
            (
                _record(design, "LF", lmh=low, module=low, pressure=10.0, cp=1.2, wall=10.0),
                _record(design, "HF", lmh=high, module=high, pressure=10.0, cp=1.2, wall=20.0),
            )
        )
    report = summarize(pairs)
    assert report["qoi"]["lmh"]["pearson"] == pytest.approx(-1.0)
    assert report["qoi"]["lmh"]["rank_reversals"] == 3
    assert report["qoi"]["lmh"]["rank_comparisons"] == 3
    assert report["qoi"]["pressure_drop_per_length_pa_per_m"]["rank_reversals"] == 0
    assert report["qoi"]["cp_average"]["pearson"] is None


def test_invalid_pair_is_excluded_from_the_summary():
    pairs = _linear_pairs()
    low, high = pairs[0]
    pairs[0] = (dict(low, status="diverged"), high)
    report = summarize(pairs)
    assert report["n_designs"] == 3
    assert report["n_comparable"] == 2
    assert report["rows"][0]["comparable"] is False
    assert report["qoi"]["lmh"]["pearson"] is None
    assert "fewer than 3" in report["qoi"]["lmh"]["reason"]


def test_write_summary_csv_and_markdown(tmp_path):
    report = summarize(_linear_pairs())
    paths = write_summary(tmp_path, report)
    csv_text = paths["csv"].read_text(encoding="utf-8")
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "row_type" in csv_text
    assert "MFP_d0800_h0000_f0400" in csv_text
    assert "summary" in csv_text
    assert "mean_signed_relative_lf_bias" in csv_text
    assert "cost_ratio" in csv_text
    assert "CP-1" in markdown
    assert "(LF - HF) / HF" in markdown
    assert "rank_reversals=0/3" in markdown
    assert "median=0.2" in markdown


def test_emit_queue_matches_the_job_runner(tmp_path):
    table_path = tmp_path / "fidelity.json"
    table_path.write_text(json.dumps(_table()), encoding="utf-8")
    designs_path = tmp_path / "designs.json"
    designs_path.write_text(
        json.dumps(
            [
                {"d_p_mm": design.d_p_mm, "d_h_mm": design.d_h_mm, "d_f_mm": design.d_f_mm}
                for design in DESIGNS
            ]
        ),
        encoding="utf-8",
    )
    jobs = build_queue(
        DESIGNS,
        run_id=RUN_ID,
        data_root=tmp_path / "data",
        fidelity_table=table_path,
    )
    assert [job["id"][:2] for job in jobs] == ["LF", "HF", "LF", "HF", "LF", "HF"]
    assert jobs[0]["argv"][0] == "{python}"
    assert jobs[0]["argv"][1] == "scripts/mfbo/run_3d_evaluate.py"
    assert "--fidelity" in jobs[0]["argv"]
    assert jobs[0]["env"]["RO_DATA_ROOT"]
    queue_path = write_queue(tmp_path / "jobs.json", jobs)
    job_queue = load_module(
        "job_queue_for_screen",
        SCRIPTS_DIR / "mfbo" / "job_queue.py",
    )
    loaded = job_queue.load_queue(queue_path)
    assert [job["id"] for job in loaded] == [job["id"] for job in jobs]
    screen = load_module(
        "run_3d_fidelity_screen_under_test",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    out = tmp_path / "screen_queue.json"
    code = screen.main(
        [
            "--designs",
            str(designs_path),
            "--fidelity-table",
            str(table_path),
            "--data-root",
            str(tmp_path / "data"),
            "--run-id",
            RUN_ID,
            "--emit-queue",
            str(out),
        ]
    )
    assert code == 0
    assert out.is_file()
    assert job_queue.load_queue(out)


def test_emit_queue_refuses_tbd_and_writes_nothing(tmp_path):
    designs_path = tmp_path / "designs.json"
    designs_path.write_text(
        json.dumps([{"d_p_mm": 0.8, "d_h_mm": 0.0, "d_f_mm": 0.4}]),
        encoding="utf-8",
    )
    screen = load_module(
        "run_3d_fidelity_screen_tbd",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    queue_path = tmp_path / "should_not_exist.json"
    with pytest.raises(ValueError, match="TBD"):
        screen.main(
            [
                "--designs",
                str(designs_path),
                "--fidelity-table",
                str(EXAMPLE),
                "--data-root",
                str(tmp_path / "data"),
                "--run-id",
                RUN_ID,
                "--emit-queue",
                str(queue_path),
            ]
        )
    assert not queue_path.exists()


def test_run_screen_evaluates_lf_then_hf(monkeypatch, tmp_path):
    calls = []
    pairs = _linear_pairs()
    by_design = {
        pairs[index][0]["geo_id"]: pairs[index] for index in range(len(pairs))
    }

    def fake_evaluate(design, fidelity, **kwargs):
        calls.append((format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm), fidelity))
        low, high = by_design[
            format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm)
        ]
        assert kwargs["drivers"] is drivers
        return low if fidelity == "LF" else high

    import ro.mfbo_fidelity_screen as screen

    monkeypatch.setattr(screen, "evaluate", fake_evaluate)
    drivers = object()
    report = run_screen(
        DESIGNS,
        _table(),
        run_id=RUN_ID,
        data_root=tmp_path,
        drivers=drivers,
    )
    assert calls == [
        (format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm), level)
        for design in DESIGNS
        for level in ("LF", "HF")
    ]
    assert report["cost_ratio"]["median"] == pytest.approx(0.2)


def test_evaluate_cli_prints_json_and_refuses_tbd(tmp_path, capsys):
    script = load_module(
        "run_3d_evaluate_under_test",
        SCRIPTS_DIR / "mfbo" / "run_3d_evaluate.py",
    )
    calls = []

    def fake_evaluate(design, fidelity, **kwargs):
        calls.append(fidelity)
        return {"status": "valid", "fidelity": fidelity, "lmh": 1.5}

    script.evaluate = fake_evaluate
    script.make_drivers = lambda root: object()
    code = script.main(
        [
            "--d-p-mm",
            "0.8",
            "--d-h-mm",
            "0.0",
            "--d-f-mm",
            "0.4",
            "--fidelity",
            "LF",
            "--run-id",
            RUN_ID,
            "--data-root",
            str(tmp_path),
            "--fidelity-table",
            str(EXAMPLE),
        ]
    )
    assert code == 0
    assert calls == ["LF"]
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "valid"
    assert printed["lmh"] == 1.5

    with pytest.raises(ValueError, match="TBD"):
        script.main(
            [
                "--d-p-mm",
                "0.8",
                "--d-h-mm",
                "0.0",
                "--d-f-mm",
                "0.4",
                "--fidelity",
                "HF",
                "--run-id",
                RUN_ID,
                "--data-root",
                str(tmp_path),
                "--fidelity-table",
                str(EXAMPLE),
            ]
        )
    assert calls == ["LF"]


def test_screen_requires_out_dir_when_it_evaluates(tmp_path):
    screen = load_module(
        "run_3d_fidelity_screen_out_dir",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    table_path = tmp_path / "fidelity.json"
    table_path.write_text(json.dumps(_table()), encoding="utf-8")
    designs_path = tmp_path / "designs.json"
    designs_path.write_text(
        json.dumps([{"d_p_mm": 0.8, "d_h_mm": 0.0, "d_f_mm": 0.4}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="out-dir"):
        screen.main(
            [
                "--designs",
                str(designs_path),
                "--fidelity-table",
                str(table_path),
                "--data-root",
                str(tmp_path / "data"),
                "--run-id",
                RUN_ID,
            ]
        )
