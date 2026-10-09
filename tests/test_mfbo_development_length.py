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


def test_never_developing_profile_excludes_the_active_zone_and_flags_it():
    excess = _excess_from_profile([float(value) for value in range(1, 21)])
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["developed"] is False
    assert distance["d_m"] is None
    assert distance["reference_excess"] == pytest.approx(17.5)
    exclusion = dev.exclusion_for_distance(20, 0.001, None)
    assert exclusion["rule"] == "not_developed"
    assert exclusion["n_lead_excluded"] == 20
    assert exclusion["window_length_m"] == pytest.approx(0.0)
    assert exclusion["short_window"] is True
    assert dev.relative_bias(excess, 20, distance["reference_excess"]) is None


def test_noisy_spike_after_l_base_pushes_exclusion_past_it():
    excess = _excess_from_profile([1.0] * 20)
    excess[11] = 1.5
    distance = dev.development_distance_m(excess, 0.001)
    assert distance["d_m"] == pytest.approx(0.012)
    exclusion = dev.exclusion_for_distance(20, 0.001, distance["d_m"])
    assert exclusion["rule"] == "at_least_max_l_base_d"
    assert exclusion["n_lead_excluded"] == 12
    assert exclusion["window_length_m"] == pytest.approx(0.008)
    assert exclusion["short_window"] is True
    assert dev.relative_bias(excess, 3, 1.0) == pytest.approx(0.5 / 17.0)
    assert dev.relative_bias(excess, 12, 1.0) == pytest.approx(0.0)


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


def test_window_of_exactly_9_mm_is_not_short():
    at_limit = dev.exclusion_for_distance(19, 0.001, 0.001)
    assert at_limit["window_length_m"] == pytest.approx(0.009)
    assert at_limit["short_window"] is False
    under = dev.exclusion_for_distance(18, 0.001, 0.001)
    assert under["window_length_m"] == pytest.approx(0.008)
    assert under["short_window"] is True


def test_reference_pitch_flat_profile_excludes_the_boundary_under_l_base():
    excess = _excess_from_profile([1.0] * 7)
    distance = dev.development_distance_m(excess, 0.003465)
    assert distance["d_m"] == pytest.approx(0.0)
    exclusion = dev.exclusion_for_distance(7, 0.003465, distance["d_m"])
    assert exclusion["n_lead_excluded"] == 2
    assert exclusion["excluded_length_m"] == pytest.approx(0.00693)
    assert exclusion["window_length_m"] == pytest.approx(5 * 0.003465)


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
    assert result["short_window"] is True
    assert result["excluded_global_cells"] == list(range(2, 14))
    assert result["window_global_cells"] == list(range(14, 22))
    by_run = {point["run_id"]: point for point in result["operating_points"]}
    assert set(by_run) == {"u0p1_p6M", "u0p2_p6M"}
    assert by_run["u0p1_p6M"]["d_m"] == pytest.approx(0.001)
    assert by_run["u0p1_p6M"]["bias_new"] == pytest.approx(0.0)
    assert by_run["u0p2_p6M"]["bias_old"] == pytest.approx(0.5 / 17.0)
    assert by_run["u0p2_p6M"]["bias_new"] == pytest.approx(0.0)
    assert result["skipped"] == [{"run_id": "u0p3_p6M", "reason": "FAIL"}]


def test_never_developing_leaf_flags_an_empty_window(tmp_path):
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
    assert result["n_lead_excluded"] == 20
    assert result["window_length_m"] == pytest.approx(0.0)
    assert result["short_window"] is True
    assert result["window_global_cells"] == []
    point = result["operating_points"][0]
    assert point["bias_new"] is None
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
        dx=0.001,
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
    )
    assert record["n_lead_excluded"] == 10
    assert record["mesh_id"] == MESH
    assert record["date"] == "2026-10-09"
    assert record["short_window"] is False
    assert record["source_data_root"] == root.as_posix()
    text = report.read_text(encoding="utf-8")
    assert "do not hand-edit" in text
    assert "REF_empty" in text
    assert "No geometry has a window shorter than 9.0 mm." in text
    assert "u0p1_p6M" in text and "not used (missing)" in text
    assert "family_defaults" not in payload


def _synthetic_row(geo_id, d_max_m):
    return {
        "geo_id": geo_id,
        "n_lead_excluded": 2,
        "excluded_length_m": 0.00693,
        "window_length_m": 0.02,
        "source_data_root": "/data",
        "mesh_id": MESH,
        "short_window": False,
        "d_max_m": d_max_m,
    }


def _campaign_pillars():
    return [
        geo_id
        for geo_id in dev.CAMPAIGN_GEO_ID_ORDER
        if dev.family_for_geo_id(geo_id) == "pillar"
    ]


def test_family_default_is_written_only_when_all_pillars_develop_within_3_5_mm():
    pillars = _campaign_pillars()
    assert len(pillars) == 9
    developed = [_synthetic_row(geo_id, 0.003) for geo_id in pillars]
    developed.append(_synthetic_row("D2450_a45", 0.02))
    payload = dev.table_payload(developed, "2026-10-09")
    default = payload["family_defaults"]["pillar"]
    assert tuple(default) == (
        "n_lead_excluded",
        "excluded_length_m",
        "basis",
        "date",
    )
    assert default["n_lead_excluded"] == 3
    assert default["excluded_length_m"] == pytest.approx(0.010395)
    assert default["basis"] == "all 9 campaign pillars develop within 3.5 mm"
    assert default["date"] == "2026-10-09"
    assert "diamond" not in payload["family_defaults"]
    assert payload["D2450_a45"]["n_lead_excluded"] == 2

    slow = [_synthetic_row(geo_id, 0.003) for geo_id in pillars]
    slow[0]["d_max_m"] = 0.004
    assert dev.family_default_records(slow, "2026-10-09") == {}
    assert dev.family_default_records(slow[1:], "2026-10-09") == {}
    undeveloped = [_synthetic_row(geo_id, 0.003) for geo_id in pillars]
    undeveloped[0]["d_max_m"] = None
    assert dev.family_default_records(undeveloped, "2026-10-09") == {}
