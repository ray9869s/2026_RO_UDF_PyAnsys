"""Benchmark c_b from x-normal boundary mixing cups (ro.bulk_cup)."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, cup_columns, load_module
from ro import bulk_cup
from ro.cp_concentration_stats import (
    concentration_cp_report,
    concentration_cp_summary_rows,
    meanfirst_cp_metrics,
)
from ro.cp_metrics import window_area_weighted_average
from ro.extract_profile import mfbo_keeps_summary_metric

REEXTRACT = load_module("campaign_v3_reextract_bulk_cup", SCRIPTS_DIR / "campaign_v3_reextract.py")
CAMPAIGN = REEXTRACT.campaign

# u0p05_p6M P_p80_h15 (2026-11-22): boundaries 6/7/8 and mid-plane cells 7/8.
BOUNDARY_CUPS = {6: 601.205, 7: 601.868, 8: 602.538}
MIDPLANE = {7: 598.163, 8: 598.407}
AREAS = {7: 1.0e-6, 8: 1.02e-6}


def test_host_case_cup_per_cell_and_window():
    cups = bulk_cup.cup_by_cell(BOUNDARY_CUPS, [7, 8])
    assert cups[7] == pytest.approx(601.5365)
    assert cups[8] == pytest.approx(602.203)
    window = window_area_weighted_average(cups, AREAS, [7, 8])
    mid = window_area_weighted_average(MIDPLANE, AREAS, [7, 8])
    diag = bulk_cup.cup_midplane_diagnostic(
        MIDPLANE, cups, [7, 8], c_b_midplane_window=mid, c_b_cup_window=window)
    assert diag["c_b_cup_window_mol_m3"] == pytest.approx(window)
    assert diag["c_b_midplane_minus_cup_rel"] == pytest.approx((mid - window) / window)
    assert diag["c_b_midplane_cup_max_cell_abs_rel"] == pytest.approx((602.203 - 598.407) / 602.203)
    status = diag["c_b_cup_midplane_status"]
    assert status.startswith("warn:") and "cell 7" in status and "cell 8" in status
    assert diag["c_b_benchmark_definition"] == "boundary_mixing_cup"
    close = bulk_cup.cup_midplane_diagnostic(
        {7: 601.5}, {7: 601.5365}, [7], c_b_midplane_window=601.5, c_b_cup_window=601.5365)
    assert close["c_b_cup_midplane_status"].startswith("ok:")


@pytest.mark.parametrize("cups", [{6: 601.0}, {6: 601.0, 7: 0.0}, {6: None, 7: 601.0}])
def test_missing_or_nonpositive_boundary_cup_refuses(cups):
    with pytest.raises(bulk_cup.CupError):
        bulk_cup.cup_by_cell(cups, [7])


def _faces():
    # cm, jw, area. c_p = B cm / (jw + B); B = 1.53e-8 by default.
    return [
        {"cm": 650.0, "jw": 1.0e-5, "area": 2.0},
        {"cm": 700.0, "jw": 0.8e-5, "area": 1.0},
        {"cm": 620.0, "jw": 1.2e-5, "area": 1.0},
    ]


def test_report_uses_cup_for_meanfirst_only_and_keeps_midplane_diagnostics():
    faces = _faces()
    report = concentration_cp_report(faces, {}, 598.3, {}, c_b_meanfirst=601.9)
    cup = meanfirst_cp_metrics(faces, 601.9)
    mid = meanfirst_cp_metrics(faces, 598.3)
    assert report["cp_meanfirst_live_window_avg"] == pytest.approx(cup["cp_meanfirst_live_window_avg"])
    assert report["cp_meanfirst_window_avg"] == pytest.approx(cup["cp_meanfirst_window_avg"])
    assert report["cp_meanfirst_live_window_avg_midplane"] == pytest.approx(
        mid["cp_meanfirst_live_window_avg"])
    assert report["cp_meanfirst_live_window_avg"] < report["cp_meanfirst_live_window_avg_midplane"]
    # Window distribution metrics stay on the mid-plane c_b.
    assert report["c_b_window_mol_m3"] == pytest.approx(598.3)
    assert report["window"]["c_b"] == pytest.approx(598.3)
    assert report["c_b_meanfirst_mol_m3"] == pytest.approx(601.9)
    names = {row["metric"] for row in concentration_cp_summary_rows(report)}
    for name in ("cp_meanfirst_live_window_avg_midplane", "cp_meanfirst_window_avg_midplane",
                 "cm_area_mean_window_live_meanfirst",
                 "cp_meanfirst_live_exclusion_threshold_mol_m3"):
        assert name in names
    # Without the cup the old behaviour and columns are unchanged.
    plain = concentration_cp_report(faces, {}, 598.3, {})
    assert plain["cp_meanfirst_live_window_avg"] == pytest.approx(mid["cp_meanfirst_live_window_avg"])
    assert "cp_meanfirst_live_window_avg_midplane" not in plain


def test_new_columns_survive_the_mfbo_profile():
    for name in (*bulk_cup.SUMMARY_METRICS, "pp_c_b_cup_cell_7_mol_m3", "pp_midplane_area_cell_7_m2"):
        assert mfbo_keeps_summary_metric(name), name


def test_extract_no_longer_raises_on_the_midplane_guard():
    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    assert "assert_midplane_c_b_matches_boundary_mixing_cup(" not in source
    assert "c_b_meanfirst=c_b_cup_window_mol_per_m3" in source


# --- recompute from saved columns -------------------------------------------

def _two_cell_wide(cp_live=1.08, cp_all=1.09):
    mid = window_area_weighted_average(MIDPLANE, {7: 1.0, 8: 1.0}, [7, 8])
    cp_ref = 10.0
    wide = {
        "c_b_window_mol_m3": mid,
        "c_inlet_ref_mol_m3": 597.8,
        "cp_meanfirst_live_window_avg": cp_live,
        "cp_ref_area_live": cp_ref,
        "cm_area_mean_window_live": cp_ref + cp_live * (mid - cp_ref),
        "cpc_window_avg_area": cp_all,
        "cp_meanfirst_window_avg": cp_all,
        "cp_ref_area": cp_ref,
        "cm_area_mean_window": cp_ref + cp_all * (mid - cp_ref),
    }
    for cell, value in MIDPLANE.items():
        wide[bulk_cup.midplane_cell_name(cell)] = value
    for index, value in BOUNDARY_CUPS.items():
        wide[bulk_cup.boundary_cup_name(index)] = value
    return {key: str(value) for key, value in wide.items()}


def _recompute(wide, live=True, cells=None):
    if live:
        return bulk_cup.recompute_meanfirst_cup(
            wide, cm_key="cm_area_mean_window_live", cp_ref_key="cp_ref_area_live",
            saved_cp_key="cp_meanfirst_live_window_avg", live=True, evaluation_cells=cells)
    return bulk_cup.recompute_meanfirst_cup(
        wide, cm_key="cm_area_mean_window", cp_ref_key="cp_ref_area",
        saved_cp_key="cpc_window_avg_area", live=False, evaluation_cells=cells)


def test_recompute_old_leaf_gives_cup_and_midplane_side_by_side():
    wide = _two_cell_wide()
    out = _recompute(wide, cells=[7, 8])
    cup = 0.5 * (601.5365 + 602.203)
    mid = float(wide["c_b_window_mol_m3"])
    cm = float(wide["cm_area_mean_window_live"])
    assert out["cp_midplane"] == pytest.approx(1.08)
    assert out["c_b_cup"] == pytest.approx(cup)
    assert out["cp_cup"] == pytest.approx((cm - 10.0) / (cup - 10.0))
    assert out["cp_cup"] < out["cp_midplane"]
    assert out["status"].startswith("ok: recomputed") and "equal" in out["status"]
    assert mid < cup
    v2 = _recompute(wide, live=False)  # cells from the columns
    assert v2["cp_midplane"] == pytest.approx(1.09) and v2["cp_cup"] < 1.09


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda w: w.pop(bulk_cup.boundary_cup_name(8)), "boundary_8_massavg"),
        (lambda w: w.pop("cp_ref_area_live"), "no cp_ref_area_live"),
        (lambda w: w.update(c_b_window_mol_m3="598.0"), "saved c_b_window"),
        (lambda w: w.update(cp_meanfirst_live_window_avg="1.2"), "give"),
        (lambda w: w.update(c_inlet_ref_mol_m3="600.0"), "live face set changes"),
        (lambda w: w.pop("c_inlet_ref_mol_m3"), "no c_inlet_ref_mol_m3"),
    ],
)
def test_recompute_blanks_with_reason(mutate, fragment):
    wide = _two_cell_wide()
    mutate(wide)
    out = _recompute(wide, cells=[7, 8])
    assert out["cp_cup"] is None and out["status"].startswith("blank:")
    assert fragment in out["status"]


def test_saved_midplane_areas_replace_equal_weights():
    wide = _two_cell_wide()
    mid = window_area_weighted_average(MIDPLANE, AREAS, [7, 8])
    wide["c_b_window_mol_m3"] = str(mid)
    wide["cm_area_mean_window_live"] = str(10.0 + 1.08 * (mid - 10.0))
    for cell, area in AREAS.items():
        wide[bulk_cup.midplane_area_cell_name(cell)] = str(area)
    out = _recompute(wide, cells=[7, 8])
    assert "midplane_area" in out["status"]
    cups = bulk_cup.cup_by_cell(BOUNDARY_CUPS, [7, 8])
    assert out["c_b_cup"] == pytest.approx(window_area_weighted_average(cups, AREAS, [7, 8]))


def test_cup_based_extract_reads_both_saved_values():
    wide = {
        bulk_cup.C_B_CUP_WINDOW: "601.9", bulk_cup.C_B_MIDPLANE_WINDOW: "598.3",
        "cp_meanfirst_live_window_avg": "1.07", "cp_meanfirst_live_window_avg_midplane": "1.08",
    }
    out = _recompute(wide)
    assert (out["cp_cup"], out["cp_midplane"]) == (1.07, 1.08)
    assert out["status"] == "ok: cup-based extract"


def test_evaluation_cells_from_mesh_manifest():
    manifest = {"n_buffer_in": 2, "n_active_cells": 8, "n_lead_excluded": 4, "n_trail_excluded": 2}
    assert bulk_cup.evaluation_cells_from_mesh_manifest(manifest) == [7, 8]
    assert bulk_cup.evaluation_cells_from_mesh_manifest({}) is None


# --- campaign_v3 --compare / seminar ----------------------------------------

def _write(leaf, row, manifest=None):
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    with (reports / "summary_metrics_wide.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    hashes = {"final_case_sha256": "c" * 64, "final_data_sha256": "d" * 64}
    (leaf / "manifest.json").write_text(
        json.dumps({"convergence_quality": "PASS", **hashes, **(manifest or {})}), encoding="utf-8")
    (reports / "extract_source.json").write_text(json.dumps(hashes), encoding="utf-8")


def test_compare_reports_cup_benchmark_and_midplane_side_by_side(tmp_path):
    geo, run = "D2450_a45", "u0p2_p6M"
    mesh = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
    mesh_ids = dict.fromkeys(CAMPAIGN.CAMPAIGN_GEO_ID_ORDER, mesh)
    v2_leaf = CAMPAIGN.run_leaf(tmp_path / "v2", geo, CAMPAIGN.production_mesh_id(geo), run)
    # Full-profile v2 leaf: mid-plane c_b on a non-window cell too. Its own
    # mesh manifest is absent, so it borrows the v3 window (cell 7).
    _write(v2_leaf, {**cup_columns(None, 1.10, cup=606.0), "cp_q999_window_flux": 1.5,
                     "pp_c_b_midplane_cell_5_mol_m3": 590.0})
    v3_leaf = CAMPAIGN.run_leaf(tmp_path / "v3", geo, mesh, run)
    _write(v3_leaf, {**cup_columns(1.20, 1.21, cup=606.0), "cp_q999_window_flux": 1.6})
    mesh_manifest = CAMPAIGN.mesh_manifest_path(tmp_path / "v3", geo, mesh)
    mesh_manifest.parent.mkdir(parents=True)
    mesh_manifest.write_text(json.dumps(
        {"n_buffer_in": 2, "n_active_cells": 6, "n_lead_excluded": 4, "n_trail_excluded": 1}),
        encoding="utf-8")
    rows = CAMPAIGN.comparison_rows(tmp_path / "v2", tmp_path / "v3", mesh_ids)
    row = next(r for r in rows if r["geo_id"] == geo and r["run_id"] == run)
    expected_v3 = (10.0 + 1.20 * 590.0 - 10.0) / (606.0 - 10.0)
    expected_v2 = (10.0 + 1.10 * 590.0 - 10.0) / (606.0 - 10.0)
    assert row["v3_cp"] == pytest.approx(expected_v3)
    assert row["v2_cp"] == pytest.approx(expected_v2)
    assert row["v3_cp_midplane"] == pytest.approx(1.20)
    assert row["v2_cp_midplane"] == pytest.approx(1.10)
    assert row["cp_rel_change"] == pytest.approx((expected_v3 - expected_v2) / expected_v2)
    assert row["cp_rel_change_midplane"] == pytest.approx(0.1 / 1.1)
    assert row["v3_c_b_cup"] == pytest.approx(606.0) and row["v3_c_b_midplane"] == pytest.approx(600.0)
    assert row["v3_cp_cup_status"].startswith("ok: recomputed")
    assert set(CAMPAIGN.COMPARE_COLUMNS) >= {"v2_cp_midplane", "v3_cp_midplane", "v3_cp_cup_status"}

    seminar = CAMPAIGN.seminar_rows(tmp_path / "v3", mesh_ids)
    srow = next(r for r in seminar if r["geo_id"] == geo and r["run_id"] == run)
    assert srow["cp_meanfirst_live_window_avg"] == pytest.approx(expected_v3)
    assert srow["cp_meanfirst_live_window_avg_midplane"] == pytest.approx(1.20)
    assert srow["c_b_midplane_minus_cup_rel"] == pytest.approx((600.0 - 606.0) / 606.0)
    assert set(srow) == set(CAMPAIGN.SEMINAR_CASES_COLUMNS)


def test_compare_blanks_cup_value_with_reason_when_a_column_is_missing(tmp_path):
    geo, run = "D2450_a45", "u0p2_p6M"
    mesh = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
    row = cup_columns(1.20, 1.21)
    row.pop("pp_salt_molar_concentration_unit_cell_boundary_7_massavg_mol_m3")
    _write(CAMPAIGN.run_leaf(tmp_path, geo, mesh, run), row)
    metrics = CAMPAIGN.leaf_metrics(CAMPAIGN.run_leaf(tmp_path, geo, mesh, run))
    assert metrics["cp"] is None and metrics["cp_excess"] is None
    assert metrics["cp_cup_status"].startswith("blank:") and "boundary_7" in metrics["cp_cup_status"]


# --- re-extract validation ----------------------------------------------------

def _validated(tmp_path, row):
    _write(tmp_path / "leaf", row)
    return REEXTRACT.validate_meanfirst_summary(tmp_path / "leaf")


def _cup_extract_row():
    faces = _faces()
    report = concentration_cp_report(faces, {}, 598.3, {}, c_b_meanfirst=601.9)
    row = {r["metric"]: r["value"] for r in concentration_cp_summary_rows(report)}
    row.update({
        "c_b_window_mol_m3": 598.3,
        bulk_cup.C_B_CUP_WINDOW: 601.9,
        "c_inlet_ref_mol_m3": report["c_inlet_ref_mol_m3"],
    })
    return row


def test_reextract_validation_accepts_cup_extract_and_rejects_tampering(tmp_path):
    row = _cup_extract_row()
    values = _validated(tmp_path / "a", row)
    assert values[bulk_cup.C_B_CUP_WINDOW] == pytest.approx(601.9)
    bad = dict(row, c_b_cup_window_mol_m3=605.0)
    with pytest.raises(ValueError, match="mean-first"):
        _validated(tmp_path / "b", bad)


def test_reextract_validation_old_extract_still_uses_midplane(tmp_path):
    row = {**cup_columns(1.2, 1.21), "live_membrane_area_window_m2": 1e-6,
           "live_exclusion_threshold_mol_m3": 597.8, "live_exclusion_rule": "c_p >= min(c_0, c_b)"}
    assert _validated(tmp_path, row)["c_b_window_mol_m3"] == pytest.approx(600.0)
