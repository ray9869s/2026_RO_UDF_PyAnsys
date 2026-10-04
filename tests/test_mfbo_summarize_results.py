"""summarize_results turns a data root into one pasteable table. No Fluent."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from helpers import SCRIPTS_DIR, load_module

summarize_results = load_module(
    "summarize_results_under_test",
    SCRIPTS_DIR / "mfbo" / "summarize_results.py",
)

PILLAR_N = 7
PILLAR_CELL_M = 0.003465
AREA_MEM = 0.00012
LMH = 25.0


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)


def _mesh(**overrides):
    payload = {
        "cell_count": 1000,
        "skewness_max": 0.5,
        "ortho_min": 0.2,
        "AR_max": 10.0,
        "porosity_eps": 0.9,
        "n_active_cells": PILLAR_N,
        "cell_length_x_m": PILLAR_CELL_M,
        "periodic_shift_y_m": PILLAR_CELL_M,
    }
    payload.update(overrides)
    return payload


def _run(**overrides):
    payload = {
        "stop_reason": "qoi_converged",
        "continuity_final": 1.0e-6,
        "convergence_quality": "PASS",
        "solver_settings": {"viscous_model": "laminar"},
    }
    payload.update(overrides)
    return payload


def _summary(**overrides):
    row = {
        "y1_window_median_um": "12.5",
        "lmh_mass_balance": "25",
        "area_mem": "0.00012",
        "pressure_drop_spacer_per_m": "1000",
        "cp_canon_window_avg": "1.1",
        "cpc_window_avg_flux": "1.2",
        "cp_q99_window_flux": "1.3",
        "cp_q999_window_flux": "1.4",
        "viscosity_ratio_volavg": "1.5",
        "diff_ratio_volavg": "1.6",
    }
    row.update(overrides)
    return row


def _plant(root, family, geo_id, mesh_id, run_id, *, mesh, run, summary, write_csv=True):
    leaf = root / "runs" / family / geo_id / mesh_id / run_id
    mesh_dir = root / "meshes" / family / geo_id / mesh_id
    if run is not None:
        _write_json(leaf / "manifest.json", run)
    else:
        leaf.mkdir(parents=True, exist_ok=True)
    if mesh is not None:
        _write_json(mesh_dir / "manifest.json", mesh)
    if write_csv and summary is not None:
        _write_csv(leaf / "post" / "reports" / "summary_metrics_wide.csv", summary)
    return leaf


def _index(rows):
    return {(row["family"], row["geo_id"], row["mesh_id"], row["run_id"]): row for row in rows}


def _tree(root):
    _plant(
        root,
        "pillar",
        "P_p100_h30",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(cell_count=796009, porosity_eps=0.91),
        run=_run(solver_settings={"viscous_model": "k-omega-sst"}),
        summary=_summary(),
    )
    _plant(
        root,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(),
        run=_run(),
        summary=_summary(),
    )
    _plant(
        root,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p1_p6M",
        mesh=_mesh(),
        run=_run(),
        summary=None,
        write_csv=False,
    )
    summary = _summary()
    del summary["cp_q99_window_flux"]
    _plant(
        root,
        "diamond",
        "D2450_a45",
        "max060_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(),
        run=_run(solver_settings={"residual_target": 1.0e-7}),
        summary=summary,
    )


def test_docstring_states_lmh_is_per_exposed_membrane_area():
    text = summarize_results.__doc__
    assert "A_module = 2 * n_active_cells * cell_length_x_m * periodic_shift_y_m" in text
    assert "lmh_module_area = lmh_mass_balance * area_mem / A_module" in text
    assert "per exposed membrane area" in text
    assert "area_mem" in text


def test_pillar_module_area_rescales_exposed_membrane_lmh():
    area = summarize_results.module_area_m2(PILLAR_N, PILLAR_CELL_M, PILLAR_CELL_M)
    assert area == pytest.approx(2 * PILLAR_N * PILLAR_CELL_M * PILLAR_CELL_M)
    scaled = summarize_results.lmh_per_module_area(
        LMH, AREA_MEM, PILLAR_N, PILLAR_CELL_M, PILLAR_CELL_M
    )
    assert scaled == pytest.approx(LMH * AREA_MEM / area)


def test_table_keeps_every_leaf_and_marks_gaps(tmp_path, capsys):
    _tree(tmp_path)
    moment = datetime(2026, 10, 4, 10, 11, 12, tzinfo=timezone.utc)
    out_dir, rows, markdown = summarize_results.summarize(tmp_path, now=moment)
    assert out_dir == tmp_path / "reports" / "20261004T101112Z"
    assert [tuple(row[key] for key in ("family", "geo_id", "mesh_id", "run_id")) for row in rows] == [
        (
            "diamond",
            "D2450_a45",
            "max060_min006_cpg5_bl4_peel2",
            "u0p2_p6M",
        ),
        (
            "diamond",
            "D2450_a45",
            "max085_min006_cpg5_bl4_peel2",
            "u0p1_p6M",
        ),
        (
            "diamond",
            "D2450_a45",
            "max085_min006_cpg5_bl4_peel2",
            "u0p2_p6M",
        ),
        (
            "pillar",
            "P_p100_h30",
            "max085_min006_cpg5_bl4_peel2",
            "u0p2_p6M",
        ),
    ]

    by_id = _index(rows)
    complete = by_id[("diamond", "D2450_a45", "max085_min006_cpg5_bl4_peel2", "u0p2_p6M")]
    assert complete["cell_count"] == "1000"
    assert complete["skewness_max"] == "0.5"
    assert complete["ortho_min"] == "0.2"
    assert complete["AR_max"] == "10"
    assert complete["porosity_eps"] == "0.9"
    assert complete["y1_window_median_um"] == "12.5"
    assert complete["stop_reason"] == "qoi_converged"
    assert complete["continuity_final"] == "1e-06"
    assert complete["convergence_quality"] == "PASS"
    assert complete["viscous_model"] == "laminar"
    assert complete["lmh_mass_balance"] == "25"
    assert complete["pressure_drop_spacer_per_m"] == "1000"
    assert complete["cp_canon_window_avg"] == "1.1"
    assert complete["cpc_window_avg_flux"] == "1.2"
    assert complete["cp_q99_window_flux"] == "1.3"
    assert complete["cp_q999_window_flux"] == "1.4"
    assert complete["viscosity_ratio_volavg"] == "1.5"
    assert complete["diff_ratio_volavg"] == "1.6"
    assert complete["notes"] == ""
    assert complete["lmh_module_area"] != summarize_results.MISSING

    missing_csv = by_id[("diamond", "D2450_a45", "max085_min006_cpg5_bl4_peel2", "u0p1_p6M")]
    assert missing_csv["lmh_mass_balance"] == summarize_results.MISSING
    assert missing_csv["y1_window_median_um"] == summarize_results.MISSING
    assert missing_csv["lmh_module_area"] == summarize_results.MISSING
    assert missing_csv["stop_reason"] == "qoi_converged"
    assert "summary_metrics_wide.csv missing" in missing_csv["notes"]

    missing_column = by_id[("diamond", "D2450_a45", "max060_min006_cpg5_bl4_peel2", "u0p2_p6M")]
    assert missing_column["cp_q99_window_flux"] == summarize_results.MISSING
    assert missing_column["cp_canon_window_avg"] == "1.1"
    assert missing_column["viscous_model"] == summarize_results.NOT_RECORDED
    assert "summary_metrics_wide.csv missing column cp_q99_window_flux" in missing_column["notes"]

    pillar = by_id[("pillar", "P_p100_h30", "max085_min006_cpg5_bl4_peel2", "u0p2_p6M")]
    area = 2 * PILLAR_N * PILLAR_CELL_M * PILLAR_CELL_M
    assert float(pillar["lmh_module_area"]) == pytest.approx(LMH * AREA_MEM / area)
    assert pillar["cell_count"] == "796009"
    assert pillar["viscous_model"] == "k-omega-sst"
    assert pillar["notes"] == ""

    csv_path = out_dir / "summary.csv"
    md_path = out_dir / "summary.md"
    assert md_path.read_text(encoding="utf-8") == markdown
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(summarize_results.COLUMNS)
        written = list(reader)
    assert [row["run_id"] for row in written] == [row["run_id"] for row in rows]
    assert written[3]["lmh_module_area"] == pillar["lmh_module_area"]

    code = summarize_results.main(
        ["--data-root", str(tmp_path), "--out-dir", str(tmp_path / "printed")]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert printed == (tmp_path / "printed" / "summary.md").read_text(encoding="utf-8")
    assert printed.startswith("| family | geo_id |")


def test_filters_accept_exact_ids_and_globs(tmp_path):
    _tree(tmp_path)
    out_dir = tmp_path / "filtered"
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path,
        family="pillar",
        geo_id="P_*",
        mesh_id="max085_min006_cpg5_bl4_peel2",
        run_id="u0p2*",
        out_dir=out_dir,
    )
    assert [(row["family"], row["run_id"]) for row in rows] == [("pillar", "u0p2_p6M")]


def test_leaf_without_manifests_is_still_a_row(tmp_path):
    leaf = tmp_path / "runs" / "sin" / "S1" / "mesh_s" / "u0p3_p6M"
    leaf.mkdir(parents=True)
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path, out_dir=tmp_path / "out"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["family"] == "sin"
    assert row["run_id"] == "u0p3_p6M"
    assert row["cell_count"] == summarize_results.MISSING
    assert row["stop_reason"] == summarize_results.MISSING
    assert row["viscous_model"] == summarize_results.MISSING
    assert row["lmh_mass_balance"] == summarize_results.MISSING
    assert "run manifest.json missing" in row["notes"]
    assert "mesh manifest.json missing" in row["notes"]
    assert "summary_metrics_wide.csv missing" in row["notes"]
