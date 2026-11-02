"""Read-only cpc versus canon survey. No Fluent."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module

SURVEY = load_module(
    "cp_definition_survey_under_test",
    SCRIPTS_DIR / "mfbo" / "cp_definition_survey.py",
)


def _leaf(root, family, geo_id, mesh_id, run_id, *, gate, cpc, canon, l2, cm_max):
    run = root / "runs" / family / geo_id / mesh_id / run_id
    reports = run / "post" / "reports"
    reports.mkdir(parents=True)
    with (reports / "summary_metrics_wide.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cpc_window_avg_flux",
                "cp_canon_window_avg",
                "cp_L2_window_avg",
                "cm_max",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "cpc_window_avg_flux": cpc,
                "cp_canon_window_avg": canon,
                "cp_L2_window_avg": l2,
                "cm_max": cm_max,
            }
        )
    (run / "manifest.json").write_text(
        json.dumps({"convergence_quality": gate}),
        encoding="utf-8",
    )


def test_survey_keeps_pass_leaves_and_flags_the_gap(tmp_path):
    campaign = tmp_path / "campaign"
    study = tmp_path / "study"
    _leaf(
        campaign,
        "sin",
        "S_a144_l3465",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        gate="PASS",
        cpc=1.2587,
        canon=1.108,
        l2=1.108,
        cm_max=17081,
    )
    _leaf(
        campaign,
        "empty",
        "REF_empty",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        gate="PASS",
        cpc=1.1551,
        canon=1.1550,
        l2=1.1550,
        cm_max=900,
    )
    _leaf(
        campaign,
        "pillar",
        "P_p100_h00",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        gate="FAIL",
        cpc=2.0,
        canon=1.0,
        l2=1.0,
        cm_max=5000,
    )
    _leaf(
        study,
        "diamond",
        "D2450_a45",
        "max035_min006_cpg5_bl4_peel2_y1h020_bms034",
        "u0p2_p6M",
        gate="PASS",
        cpc=1.0854,
        canon=1.0840,
        l2=1.0840,
        cm_max=1100,
    )
    _leaf(
        study,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        gate="PASS",
        cpc=1.5,
        canon=1.0,
        l2=1.0,
        cm_max=2000,
    )
    output = tmp_path / "survey.csv"
    assert SURVEY.main(
        [
            "--campaign-root",
            str(campaign),
            "--meshstudy-root",
            str(study),
            "--output",
            str(output),
        ]
    ) == 0
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_geo = {(row["group"], row["geo_id"], row["mesh_id"]): row for row in rows}
    assert ("campaign_pass", "P_p100_h00", "max085_min006_cpg5_bl4_peel2") not in by_geo
    flagged = by_geo[
        ("campaign_pass", "S_a144_l3465", "max085_min006_cpg5_bl4_peel2")
    ]
    assert flagged["flag"] == "FLAG"
    assert float(flagged["abs_cpc_minus_canon"]) == pytest.approx(abs(1.2587 - 1.108))
    assert float(flagged["cm_max"]) == 17081
    quiet = by_geo[("campaign_pass", "REF_empty", "max085_min006_cpg5_bl4_peel2")]
    assert quiet["flag"] == ""
    y1 = by_geo[
        (
            "y1_2um",
            "D2450_a45",
            "max035_min006_cpg5_bl4_peel2_y1h020_bms034",
        )
    ]
    assert y1["flag"] == ""
    assert (
        "y1_2um",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
    ) not in by_geo
