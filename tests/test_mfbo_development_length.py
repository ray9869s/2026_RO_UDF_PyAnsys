"""Synthetic CP-excess profiles for the development-length window.

Does not launch Fluent and does not read a campaign data root.
"""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import family_for_geo_id

dev = load_module(
    "development_length_under_test",
    SCRIPTS_DIR / "mfbo" / "development_length.py",
)

MESH = "max085_min006_cpg5_bl4_peel2"
MESH_FINE = "max060_min006_cpg5_bl4_peel2"


def _excess_from_profile(profile):
    return list(profile)


def _plant(root, geo_id, mesh_id, runs, *, n_active, dx, n_buffer_in=1):
    family = family_for_geo_id(geo_id)
    mesh_dir = root / "meshes" / family / geo_id / mesh_id
    mesh_dir.mkdir(parents=True)
    (mesh_dir / "manifest.json").write_text(
        json.dumps(
            {
                "geo_id": geo_id,
                "mesh_id": mesh_id,
                "n_active_cells": n_active,
                "n_buffer_in": n_buffer_in,
                "cell_length_x_m": dx,
            }
        ),
        encoding="utf-8",
    )
    for run_id, quality, excess in runs:
        leaf = root / "runs" / family / geo_id / mesh_id / run_id
        reports = leaf / "post" / "reports"
        reports.mkdir(parents=True)
        (leaf / "manifest.json").write_text(
            json.dumps(
                {
                    "geo_id": geo_id,
                    "mesh_id": mesh_id,
                    "run_id": run_id,
                    "convergence_quality": quality,
                }
            ),
            encoding="utf-8",
        )
        if excess is None:
            continue
        _write_wide(reports / "summary_metrics_wide.csv", n_buffer_in, excess)


def _write_wide(path, n_buffer_in, excess):
    headers = []
    values = []
    for local, value in enumerate(excess, start=1):
        cell = n_buffer_in + local
        headers.extend(
            [
                f"pp_cm_mol_m3_cell_{cell}",
                f"pp_cp_perm_mol_m3_cell_{cell}",
                f"pp_c_b_midplane_cell_{cell}_mol_m3",
            ]
        )
        # cp = 0, c_b = 1 => excess = cm - 1.
        values.extend([str(value + 1.0), "0", "1"])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        writer.writerow(values)


def test_reference_cell_count_follows_6_93_mm_and_at_least_two():
    assert dev.reference_cell_count(0.003465, 7) == 2
    assert dev.reference_cell_count(0.001, 20) == 6
    assert dev.reference_cell_count(0.0049, 5) == 2
    with pytest.raises(ValueError, match="n_active=1"):
        dev.reference_cell_count(0.0049, 1)


def test_fast_profile_stops_on_the_boundary_just_below_l_base():
    excess = _excess_from_profile([1.5] + [1.0] * 19)
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["developed"] is True
    assert distance["d_m"] == pytest.approx(0.001)
    assert distance["reference_cell_count"] == 6
    exclusion = dev.exclusion_for_distance(20, 0.001, distance["d_m"])
    assert exclusion["rule"] == "boundary_below_l_base"
    assert exclusion["n_lead_excluded"] == 10
    assert exclusion["excluded_length_m"] == pytest.approx(0.010)
    assert exclusion["window_length_m"] == pytest.approx(0.010)
    assert exclusion["short_window"] is False
    assert dev.relative_bias(excess, exclusion["n_lead_excluded"], 1.0) == pytest.approx(
        0.0
    )


def test_slow_profile_excludes_out_to_max_l_base_and_d():
    excess = _excess_from_profile([1.5] * 12 + [1.0] * 18)
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["d_m"] == pytest.approx(0.012)
    exclusion = dev.exclusion_for_distance(30, 0.001, distance["d_m"])
    assert exclusion["rule"] == "at_least_max_l_base_d"
    assert exclusion["n_lead_excluded"] == 12
    assert exclusion["excluded_length_m"] == pytest.approx(0.012)
    assert exclusion["short_window"] is False
    assert dev.relative_bias(excess, 3, 1.0) == pytest.approx((9 * 1.5 + 18.0) / 27.0 - 1.0)
    assert dev.relative_bias(excess, 12, 1.0) == pytest.approx(0.0)


def test_never_developing_profile_falls_back_to_l_base_and_keeps_a_window():
    excess = _excess_from_profile([float(value) for value in range(1, 21)])
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["developed"] is False
    assert distance["d_m"] is None
    assert distance["reference_excess"] == pytest.approx(17.5)
    exclusion = dev.exclusion_for_distance(20, 0.001, None)
    assert exclusion["rule"] == "not_developed_fallback"
    assert exclusion["n_lead_excluded"] == 11
    assert exclusion["window_length_m"] == pytest.approx(0.009)
    assert exclusion["window_length_m"] > 0.0
    assert exclusion["short_window"] is False
    assert dev.relative_bias(excess, exclusion["n_lead_excluded"], distance["reference_excess"]) == (
        pytest.approx((16.0 - 17.5) / 17.5)
    )


def test_noisy_spike_after_l_base_pushes_exclusion_past_it():
    excess = _excess_from_profile([1.0] * 20)
    excess[11] = 1.5
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["d_m"] == pytest.approx(0.012)
    assert distance["max_rel_dev_after_d"] == pytest.approx(0.0)
    assert distance["max_out_of_band_before_d"] == pytest.approx(1.0 / 6.0 - 0.03)
    exclusion = dev.exclusion_for_distance(20, 0.001, distance["d_m"])
    assert exclusion["rule"] == "at_least_max_l_base_d"
    assert exclusion["n_lead_excluded"] == 12
    assert exclusion["window_length_m"] == pytest.approx(0.008)
    assert exclusion["short_window"] is False
    assert dev.relative_bias(excess, 3, 1.0) == pytest.approx(0.5 / 17.0)
    assert dev.relative_bias(excess, 12, 1.0) == pytest.approx(0.0)


def test_small_late_spike_keeps_a_quiet_tail_and_a_small_breach():
    excess = _excess_from_profile([1.0] * 20)
    excess[11] = 1.0903
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["developed"] is True
    assert distance["d_m"] == pytest.approx(0.012)
    assert distance["max_rel_dev_after_d"] == pytest.approx(0.0)
    mean = (2.0 + 1.0903) / 3.0
    assert distance["max_out_of_band_before_d"] == pytest.approx(abs(mean - 1.0) - 0.03)


def test_exact_3_percent_stays_inside_and_a_larger_gap_does_not():
    inside = _excess_from_profile([1.09] + [1.0] * 19)
    outside = _excess_from_profile([1.0903] + [1.0] * 19)
    assert dev.development_distance_m(inside, 0.001)["d_m"] == pytest.approx(0.0)
    assert dev.development_distance_m(outside, 0.001)["d_m"] == pytest.approx(0.001)


def test_distance_equal_to_the_lower_boundary_does_not_step_past_it():
    excess = _excess_from_profile([1.0] * 20)
    excess[9] = 1.5
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["d_m"] == pytest.approx(0.010)
    exclusion = dev.exclusion_for_distance(20, 0.001, distance["d_m"])
    assert exclusion["rule"] == "boundary_below_l_base"
    assert exclusion["n_lead_excluded"] == 10


def test_coarse_flat_profile_excludes_two_cells_not_three():
    excess = _excess_from_profile([1.0] * 6)
    distance = dev.development_distance_m(excess, 0.0049)
    assert distance["developed"] is True
    assert distance["d_m"] == pytest.approx(0.0)
    assert distance["reference_cell_count"] == 2
    exclusion = dev.exclusion_for_distance(6, 0.0049, distance["d_m"])
    assert exclusion["n_lead_excluded"] == 2
    assert exclusion["excluded_length_m"] == pytest.approx(0.0098)
    assert exclusion["short_window"] is False


def test_window_shorter_than_three_spacer_periods_is_flagged():
    three = dev.exclusion_for_distance(13, 0.001, 0.001)
    assert three["window_length_m"] == pytest.approx(0.003)
    assert three["short_window"] is False
    two = dev.exclusion_for_distance(12, 0.001, 0.001)
    assert two["window_length_m"] == pytest.approx(0.002)
    assert two["short_window"] is True
    two_periods = dev.exclusion_for_distance(7, 0.003465, 0.0, period_cells=2)
    assert two_periods["window_length_m"] == pytest.approx(4 * 0.003465)
    assert two_periods["short_window"] is True


def test_reference_pitch_flat_profile_excludes_the_boundary_on_l_base():
    assert 3 * 0.003465 > dev.L_BASE_M
    assert abs(3 * 0.003465 - dev.L_BASE_M) <= dev.LENGTH_TOL_M
    excess = _excess_from_profile([1.0] * 7)
    distance = dev.development_distance_m(excess, 0.003465)
    assert distance["d_m"] == pytest.approx(0.0)
    exclusion = dev.exclusion_for_distance(7, 0.003465, distance["d_m"])
    assert exclusion["rule"] == "boundary_below_l_base"
    assert exclusion["n_lead_excluded"] == 3
    assert exclusion["excluded_length_m"] == pytest.approx(0.010395)
    assert exclusion["window_length_m"] == pytest.approx(4 * 0.003465)


def test_two_cell_period_profile_develops_with_an_even_window():
    oscillating = [1.2, 0.8] * 3 + [1.2]
    assert dev.development_distance_m(oscillating, 0.003465)["developed"] is False
    period = dev.spacer_period("S_a072_l6930", 0.003465)
    assert period["period_m"] == pytest.approx(0.00693)
    assert period["period_cells"] == 2
    assert period["period_source"] == "wavelength"
    measured = dev.development_distance_m(
        oscillating, 0.003465, period_cells=period["period_cells"]
    )
    assert measured["developed"] is True
    assert measured["d_m"] == pytest.approx(0.0)
    assert measured["rolling_cells"] == 4
    assert measured["reference_cell_count"] == 2
    assert measured["max_rel_dev_after_d"] == pytest.approx(0.0)
    exclusion = dev.exclusion_for_distance(
        7, 0.003465, measured["d_m"], period_cells=2
    )
    assert exclusion["n_lead_excluded"] == 3
    assert (7 - exclusion["n_lead_excluded"]) % 2 == 0
    assert dev.spacer_period("S_a144_l3465", 0.003465)["period_cells"] == 1
    assert dev.spacer_period("S_a193_l1733", 0.003465)["period_cells"] == 1
    unit = dev.spacer_period("P_p100_h00", 0.003465)
    assert unit["period_cells"] == 1
    assert unit["period_source"] == "unit_cell"
    odd_window = dev.exclusion_for_distance(20, 0.003465, 0.0, period_cells=2)
    assert odd_window["n_lead_excluded"] == 4
    assert odd_window["rule"] == "boundary_below_l_base_period_aligned"
    assert (20 - odd_window["n_lead_excluded"]) % 2 == 0


def test_cell_excess_rejects_a_zero_denominator_and_a_length_mismatch():
    with pytest.raises(ValueError, match="c_b - c_p is 0"):
        dev.cell_excesses([1.0], [1.0], [1.0])
    with pytest.raises(ValueError, match="one value per active cell"):
        dev.cell_excesses([1.0, 1.0], [0.0], [1.0])
    with pytest.raises(ValueError, match="positive"):
        dev.development_distance_m([0.0, 0.0, 0.0, 0.0], 0.001)


def test_d_max_uses_pass_runs_only_and_records_both_biases(tmp_path):
    root = tmp_path / "campaign"
    fast = [1.5] + [1.0] * 19
    noisy = [1.0] * 20
    noisy[11] = 1.5
    _plant(
        root,
        "D2450_a45",
        MESH,
        [
            ("u0p1_p6M", "PASS", fast),
            ("u0p2_p6M", "PASS", noisy),
            ("u0p3_p6M", "FAIL", None),
        ],
        n_active=20,
        dx=0.001,
    )
    results = dev.evaluate([root], ["D2450_a45"], MESH, {})
    result = results[0]
    assert result["d_max_m"] == pytest.approx(0.012)
    assert result["n_lead_excluded"] == 12
    assert result["window_length_m"] == pytest.approx(0.008)
    assert result["short_window"] is False
    assert result["excluded_global_cells"] == list(range(2, 14))
    assert result["window_global_cells"] == list(range(14, 22))
    by_run = {point["run_id"]: point for point in result["operating_points"]}
    assert set(by_run) == {"u0p1_p6M", "u0p2_p6M"}
    assert by_run["u0p1_p6M"]["d_m"] == pytest.approx(0.001)
    assert by_run["u0p1_p6M"]["sets_d_max"] is False
    assert by_run["u0p1_p6M"]["bias_new"] == pytest.approx(0.0)
    assert by_run["u0p2_p6M"]["sets_d_max"] is True
    assert by_run["u0p2_p6M"]["max_rel_dev_after_d"] == pytest.approx(0.0)
    assert by_run["u0p2_p6M"]["max_out_of_band_before_d"] == pytest.approx(
        1.0 / 6.0 - 0.03
    )
    assert by_run["u0p2_p6M"]["bias_old"] == pytest.approx(0.5 / 17.0)
    assert by_run["u0p2_p6M"]["bias_new"] == pytest.approx(0.0)
    assert result["period_cells"] == 1
    assert result["period_source"] == "unit_cell"
    assert result["skipped"] == [{"run_id": "u0p3_p6M", "reason": "FAIL"}]


def test_two_cell_period_that_never_develops_matches_the_l_base_window():
    developed = dev.exclusion_for_distance(7, 0.003465, 0.0, period_cells=2)
    fallback = dev.exclusion_for_distance(7, 0.003465, None, period_cells=2)
    assert fallback["rule"] == "not_developed_fallback"
    assert fallback["n_lead_excluded"] == developed["n_lead_excluded"] == 3
    assert fallback["window_length_m"] == pytest.approx(4 * 0.003465)
    assert fallback["window_length_m"] > 0.0
    assert (7 - fallback["n_lead_excluded"]) % 2 == 0
    assert fallback["short_window"] is True
    for geo_id in ("S_a072_l6930", "S_a193_l6930", "S_a144_l6930"):
        period = dev.spacer_period(geo_id, 0.003465)
        assert period["period_cells"] == 2
        assert period["period_m"] == pytest.approx(0.00693)


def test_never_developing_leaf_uses_the_l_base_fallback(tmp_path):
    root = tmp_path / "campaign"
    _plant(
        root,
        "D2450_a60",
        MESH,
        [("u0p2_p6M", "PASS", [float(value) for value in range(1, 21)])],
        n_active=20,
        dx=0.001,
    )
    result = dev.evaluate([root], ["D2450_a60"], MESH, {})[0]
    assert result["developed"] is False
    assert result["d_max_m"] is None
    assert result["rule"] == "not_developed_fallback"
    assert result["n_lead_excluded"] == 11
    assert result["window_length_m"] == pytest.approx(0.009)
    assert result["window_global_cells"]
    point = result["operating_points"][0]
    assert point["bias_new"] == pytest.approx((16.0 - 17.5) / 17.5)
    assert point["bias_old"] == pytest.approx((12.0 - 17.5) / 17.5)


def test_inlet_buffer_count_shifts_global_cell_numbers(tmp_path):
    root = tmp_path / "campaign"
    _plant(
        root,
        "D1225_a45",
        MESH,
        [("u0p2_p6M", "PASS", [1.0] * 20)],
        n_active=20,
        dx=0.001,
        n_buffer_in=2,
    )
    result = dev.evaluate([root], ["D1225_a45"], MESH, {})[0]
    assert result["n_lead_excluded"] == 10
    assert result["excluded_global_cells"][0] == 3
    assert result["window_global_cells"][0] == 13


def test_production_override_selects_the_d0817_a60_mesh(tmp_path):
    root = tmp_path / "campaign"
    _plant(
        root,
        "D0817_a60",
        MESH_FINE,
        [("u0p1_p6M", "PASS", [1.0] * 15)],
        n_active=15,
        dx=0.001633,
    )
    overrides = dev.parse_mesh_overrides(None)
    result = dev.evaluate([root], ["D0817_a60"], MESH, overrides)[0]
    assert result["mesh_id"] == MESH_FINE
    with pytest.raises(FileNotFoundError, match="D0817_a60"):
        dev.evaluate([root], ["D0817_a60"], MESH, {})


def test_duplicate_geo_across_roots_and_a_relative_root_fail(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    for root in (first, second):
        _plant(
            root,
            "P_p80_h15",
            MESH,
            [("u0p1_p6M", "PASS", [1.0] * 12)],
            n_active=12,
            dx=0.001,
        )
    with pytest.raises(RuntimeError, match="more than one data root"):
        dev.evaluate([first, second], ["P_p80_h15"], MESH, {})
    with pytest.raises(ValueError, match="absolute"):
        dev.evaluate(["campaign"], ["P_p80_h15"], MESH, {})


def test_no_pass_run_and_a_missing_column_fail_loudly(tmp_path):
    root = tmp_path / "campaign"
    _plant(
        root,
        "M_c267",
        MESH,
        [
            ("u0p1_p6M", "FAIL", None),
            ("u0p2_p6M", "UNKNOWN", None),
        ],
        n_active=12,
        dx=0.001,
    )
    with pytest.raises(RuntimeError, match="no PASS run"):
        dev.evaluate([root], ["M_c267"], MESH, {})

    _plant(
        root,
        "S_a144_l3465",
        MESH,
        [("u0p2_p6M", "PASS", [1.0] * 8)],
        n_active=8,
        dx=0.003465,
    )
    csv_path = (
        root
        / "runs"
        / "sin"
        / "S_a144_l3465"
        / MESH
        / "u0p2_p6M"
        / "post"
        / "reports"
        / "summary_metrics_wide.csv"
    )
    csv_path.write_text("pp_cm_mol_m3_cell_2\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        dev.evaluate([root], ["S_a144_l3465"], MESH, {})


def test_cli_writes_the_table_and_the_report(tmp_path):
    root = tmp_path / "campaign"
    _plant(
        root,
        "REF_empty",
        MESH,
        [("u0p2_p6M", "PASS", [1.0] * 20)],
        n_active=20,
        dx=0.001,
    )
    table = tmp_path / "out" / "evaluation_window_table.json"
    report = tmp_path / "out" / "evaluation_window.md"
    code = dev.main(
        [
            "--data-root",
            str(root),
            "--geo-id",
            "REF_empty",
            "--table",
            str(table),
            "--report",
            str(report),
            "--date",
            "2026-10-09",
        ]
    )
    assert code == 0
    payload = json.loads(table.read_text(encoding="utf-8"))
    record = payload["REF_empty"]
    assert tuple(record) == (
        "n_lead_excluded",
        "excluded_length_m",
        "window_length_m",
        "source_data_root",
        "mesh_id",
        "date",
        "short_window",
        "rule",
    )
    assert record["rule"] == "boundary_below_l_base"
    assert record["n_lead_excluded"] == 10
    assert record["mesh_id"] == MESH
    assert record["date"] == "2026-10-09"
    assert record["short_window"] is False
    assert record["source_data_root"] == root.as_posix()
    text = report.read_text(encoding="utf-8")
    assert "do not hand-edit" in text
    assert "REF_empty" in text
    assert "No geometry has a window shorter than 3 spacer periods." in text
    assert "u0p1_p6M" in text and "not used (missing)" in text
    assert "family_defaults" not in payload


def _synthetic_row(
    geo_id,
    d_max_m,
    n_lead=2,
    excluded=0.00693,
    speed="u0p2_p6M",
):
    return {
        "geo_id": geo_id,
        "n_lead_excluded": n_lead,
        "excluded_length_m": excluded,
        "window_length_m": 0.02,
        "source_data_root": "/data",
        "mesh_id": MESH,
        "short_window": False,
        "rule": "boundary_below_l_base",
        "d_max_m": d_max_m,
        "operating_points": [
            {
                "run_id": speed,
                "d_m": d_max_m,
                "sets_d_max": d_max_m is not None,
            }
        ],
    }


def _campaign_pillars():
    return [
        geo_id
        for geo_id in dev.CAMPAIGN_GEO_ID_ORDER
        if dev.family_for_geo_id(geo_id) == "pillar"
    ]


def test_family_default_is_the_most_conservative_campaign_pillar():
    pillars = _campaign_pillars()
    assert len(pillars) == 9
    developed = [_synthetic_row(geo_id, 0.003) for geo_id in pillars]
    for row in developed:
        if row["geo_id"] == "P_p60_h00":
            row["n_lead_excluded"] = 4
            row["excluded_length_m"] = 0.01386
            row["d_max_m"] = 0.01386
            row["operating_points"] = [
                {"run_id": "u0p1_p6M", "d_m": 0.01386, "sets_d_max": True},
                {"run_id": "u0p2_p6M", "d_m": 0.003465, "sets_d_max": False},
            ]
    developed.append(_synthetic_row("D2450_a45", 0.02, n_lead=6, excluded=0.02))
    payload = dev.table_payload(developed, "2026-10-09")
    default = payload["family_defaults"]["pillar"]
    assert tuple(default) == (
        "n_lead_excluded",
        "excluded_length_m",
        "basis",
        "date",
    )
    assert default["n_lead_excluded"] == 4
    assert default["excluded_length_m"] == pytest.approx(0.01386)
    assert default["basis"] == "most conservative campaign pillar P_p60_h00 at u0p1_p6M"
    assert default["date"] == "2026-10-09"
    assert "diamond" not in payload["family_defaults"]
    assert payload["D2450_a45"]["n_lead_excluded"] == 6
    assert payload["P_p60_h00"]["n_lead_excluded"] == 4

    missing_one = [_synthetic_row(geo_id, 0.003) for geo_id in pillars[1:]]
    with pytest.raises(RuntimeError, match=pillars[0]):
        dev.family_default_records(missing_one, "2026-10-09")
    assert dev.family_default_records([], "2026-10-09") == {}
    undeveloped = [_synthetic_row(geo_id, 0.003) for geo_id in pillars]
    undeveloped[0]["d_max_m"] = None
    undeveloped[0]["operating_points"][0]["sets_d_max"] = False
    with pytest.raises(RuntimeError, match="did not develop"):
        dev.family_default_records(undeveloped, "2026-10-09")
