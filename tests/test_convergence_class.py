"""convergence_class: continuity_boundary label and its table filter."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER
from ro.convergence_quality import convergence_class

CAMPAIGN = load_module("campaign_v3_convergence_class", SCRIPTS_DIR / "campaign_v3.py")
MESH = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"

# P_p100_h00 u0p2_p6M as the host reported it.
BOUNDARY = {
    "convergence_quality": "FAIL",
    "convergence_quality_failures": ["continuity_final"],
    "lmh_relative_difference": -2.7e-4,
    "mass_balance_relative_error": -3.9e-4,
    "continuity_final": 1.2754e-4,
    "monitor_trend_last500_lmh": 0.0,
    "monitor_trend_last500_dp": 0.0,
}


def test_boundary_case_and_its_neighbours():
    assert convergence_class(BOUNDARY) == "continuity_boundary"
    assert convergence_class({"convergence_quality": "PASS"}) == "pass"
    assert convergence_class({"convergence_quality": "UNKNOWN"}) == "unknown"
    assert convergence_class({}) == "unknown"
    # D2450_a30 u0p2: fails all three gates.
    assert convergence_class(dict(
        BOUNDARY, convergence_quality_failures=[
            "lmh_relative_difference", "mass_balance_relative_error", "continuity_final"],
        lmh_relative_difference=-0.058, mass_balance_relative_error=-0.055,
        continuity_final=1.3e-2)) == "fail"


@pytest.mark.parametrize("change", [
    {"continuity_final": 3e-4},
    {"continuity_final": 9e-5},
    {"lmh_relative_difference": 1.5e-3},
    {"mass_balance_relative_error": None},
    {"monitor_trend_last500_dp": 2e-4},
    {"monitor_trend_last500_lmh": None},
    {"convergence_quality_failures": []},
])
def test_boundary_needs_every_condition(change):
    assert convergence_class(dict(BOUNDARY, **change)) == "fail"


def _leaf(root, geo, family, manifest, cp=1.2):
    leaf = root / "runs" / family / geo / MESH / "u0p2_p6M"
    (leaf / "post" / "reports").mkdir(parents=True)
    with (leaf / "post" / "reports" / "summary_metrics_wide.csv").open("w", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=["cp_meanfirst_live_window_avg", "lmh_window_exposed"])
        writer.writeheader()
        writer.writerow({"cp_meanfirst_live_window_avg": cp, "lmh_window_exposed": 24.0})
    hashes = {"final_case_sha256": "c" * 64, "final_data_sha256": "d" * 64}
    (leaf / "post" / "reports" / "extract_source.json").write_text(json.dumps(hashes))
    (leaf / "manifest.json").write_text(json.dumps({**manifest, **hashes}))
    return leaf


def test_seminar_and_compare_blank_boundary_rows_unless_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(CAMPAIGN, "report_binding_reason", lambda leaf: None)
    root = tmp_path / "v3"
    _leaf(root, "P_p100_h00", "pillar", BOUNDARY)
    _leaf(root, "REF_empty", "empty", {"convergence_quality": "PASS"})
    mesh_ids = {geo: MESH for geo in CAMPAIGN_GEO_ID_ORDER}

    def row(rows, geo):
        return next(r for r in rows if r["geo_id"] == geo and r["run_id"] == "u0p2_p6M")

    default = CAMPAIGN.seminar_rows(root, mesh_ids)
    boundary = row(default, "P_p100_h00")
    assert boundary["convergence_class"] == "continuity_boundary"
    assert boundary["convergence_quality"] == "FAIL"
    assert boundary["cp_meanfirst_live_window_avg"] is None and boundary["lmh_window_exposed"] is None
    assert row(default, "REF_empty")["cp_meanfirst_live_window_avg"] == pytest.approx(1.2)
    included = row(CAMPAIGN.seminar_rows(root, mesh_ids, include_continuity_boundary=True),
                   "P_p100_h00")
    assert included["cp_meanfirst_live_window_avg"] == pytest.approx(1.2)

    v2 = tmp_path / "v2"
    compare = CAMPAIGN.comparison_rows(v2, root, mesh_ids)
    p = row(compare, "P_p100_h00")
    assert p["v3_convergence_class"] == "continuity_boundary" and p["v3_cp"] is None
    assert "continuity_boundary=1" in CAMPAIGN.compare_binding_summary(compare)
    p = row(CAMPAIGN.comparison_rows(v2, root, mesh_ids, include_continuity_boundary=True),
            "P_p100_h00")
    assert p["v3_cp"] == pytest.approx(1.2)
    assert "v3_convergence_class" in CAMPAIGN.COMPARE_COLUMNS
    assert "convergence_class" in CAMPAIGN.SEMINAR_CASES_COLUMNS


def test_flag_needs_a_table():
    with pytest.raises(ValueError, match="requires --compare or --seminar-csv"):
        CAMPAIGN.main(["--mesh-table", "--include-continuity-boundary"])
