"""Synthetic mesh-convergence sequences. Does not launch Fluent."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module

convergence = load_module(
    "mesh_convergence_under_test",
    SCRIPTS_DIR / "mfbo" / "mesh_convergence.py",
)

# φ = 10 + h^2 with h = 4, 2, 1 and r = 2. Order 2, continuum value 10.
_MONOTONE = (26.0, 14.0, 11.0)
# Coarser point on the same curve, h = 8. The estimate still uses 26, 14, 11.
_MONOTONE_FOUR = (74.0, 26.0, 14.0, 11.0)


def test_monotone_series_reports_order_two_and_gci():
    ratios = [2.0, 2.0]
    report = convergence.analyze_series(_MONOTONE, ratios)
    assert report["status"] == "monotone"
    assert report["apparent_order"] == pytest.approx(2.0)
    assert report["richardson_extrapolated"] == pytest.approx(10.0)
    assert report["gci_fine"] == pytest.approx(1.25 / 11.0)
    assert report["differences"] == pytest.approx((-12.0, -3.0))
    assert report["difference_ratios"] == pytest.approx((0.25,))


def test_four_levels_use_the_three_finest():
    report = convergence.analyze_series(_MONOTONE_FOUR, [2.0, 2.0, 2.0])
    assert report["status"] == "monotone"
    assert report["apparent_order"] == pytest.approx(2.0)
    assert report["richardson_extrapolated"] == pytest.approx(10.0)
    # A fit of the first three values is not order 2.
    coarse = convergence.analyze_series((100.0, 26.0, 14.0, 11.0), [2.0, 2.0, 2.0])
    assert coarse["richardson_extrapolated"] == pytest.approx(10.0)


def test_unequal_ratios_recover_the_prescribed_order():
    # φ = 5 + 0.1 * h^2, r21 = 2, r32 = 1.5.
    report = convergence.analyze_series((5.9, 5.4, 5.1), [1.5, 2.0])
    assert report["status"] == "monotone"
    assert report["apparent_order"] == pytest.approx(2.0)
    assert report["richardson_extrapolated"] == pytest.approx(5.0)
    assert report["gci_fine"] == pytest.approx(1.25 * 0.3 / 5.1 / 3.0)


def test_oscillatory_series_is_not_extrapolated():
    report = convergence.analyze_series((1.0, 1.2, 1.05), [2.0, 2.0])
    assert report["status"] == "oscillatory"
    assert report["apparent_order"] is None
    assert report["richardson_extrapolated"] is None
    assert report["gci_fine"] is None


def test_divergent_series_is_not_extrapolated():
    report = convergence.analyze_series((1.0, 1.1, 1.3), [2.0, 2.0])
    assert report["status"] == "divergent"
    assert report["apparent_order"] is None
    assert report["richardson_extrapolated"] is None
    assert report["gci_fine"] is None


def test_equal_steps_and_a_zero_step_are_flagged():
    flat = convergence.analyze_series((1.0, 2.0, 3.0), [2.0, 2.0])
    assert flat["status"] == "not_contracting"
    assert flat["apparent_order"] is None
    stalled = convergence.analyze_series((1.0, 2.0, 2.0), [2.0, 2.0])
    assert stalled["status"] == "zero_difference"
    assert stalled["gci_fine"] is None


def test_cell_count_ratio_is_the_cube_root_and_must_increase():
    assert convergence.cell_count_ratios([1000, 8000, 64000]) == pytest.approx(
        (2.0, 2.0)
    )
    with pytest.raises(ValueError, match="Cell count must increase"):
        convergence.cell_count_ratios([8000, 1000])


def test_supplied_ratio_must_exceed_one():
    with pytest.raises(ValueError, match="must be > 1"):
        convergence.constant_ratios(2, 1.0)
    with pytest.raises(ValueError, match="must be > 1"):
        convergence.constant_ratios(2, 0.5)


def test_relative_gci_is_blank_when_the_finest_value_is_zero():
    report = convergence.analyze_series((3.0, 1.0, 0.0), [2.0, 2.0])
    assert report["status"] == "monotone"
    assert report["apparent_order"] == pytest.approx(1.0)
    assert report["richardson_extrapolated"] == pytest.approx(-1.0)
    assert report["gci_fine"] is None
    assert "finest value is 0" in report["note"]


def _level(mesh_id, cell_count, lmh):
    return {
        "mesh_id": mesh_id,
        "cell_count": cell_count,
        "n_active_cells": 7,
        "cell_length_x_m": 0.01,
        "periodic_shift_y_m": 0.01,
        "lmh_mass_balance": lmh,
        "area_mem": 0.001,
        "pressure_drop_spacer_per_m": 1000.0 + lmh,
        "cpc_window_avg_flux": 1.2 + lmh / 1000.0,
        "cp_q999_window_flux": 1.4 + lmh / 1000.0,
    }


def _write_level(root, mesh_id, cell_count, lmh):
    level = _level(mesh_id, cell_count, lmh)
    manifest = root / "meshes" / "pillar" / "P_p80_h15" / mesh_id / "manifest.json"
    summary = (
        root
        / "runs"
        / "pillar"
        / "P_p80_h15"
        / mesh_id
        / "u0p2_p6M"
        / "post"
        / "reports"
        / "summary_metrics_wide.csv"
    )
    manifest.parent.mkdir(parents=True)
    summary.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "cell_count": cell_count,
                "n_active_cells": level["n_active_cells"],
                "cell_length_x_m": level["cell_length_x_m"],
                "periodic_shift_y_m": level["periodic_shift_y_m"],
            }
        ),
        encoding="utf-8",
    )
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lmh_mass_balance",
                "area_mem",
                "pressure_drop_spacer_per_m",
                "cpc_window_avg_flux",
                "cp_q999_window_flux",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "lmh_mass_balance": lmh,
                "area_mem": level["area_mem"],
                "pressure_drop_spacer_per_m": level["pressure_drop_spacer_per_m"],
                "cpc_window_avg_flux": level["cpc_window_avg_flux"],
                "cp_q999_window_flux": level["cp_q999_window_flux"],
            }
        )


def test_report_reads_leaves_and_writes_csv_and_markdown(tmp_path):
    meshes = (
        ("max085_min006_cpg5_bl4_peel2", 1000, 26.0),
        ("max060_min006_cpg5_bl4_peel2", 8000, 14.0),
        ("max045_min006_cpg5_bl4_peel2", 64000, 11.0),
    )
    for mesh_id, count, lmh in meshes:
        _write_level(tmp_path, mesh_id, count, lmh)
    out = tmp_path / "out"
    result = convergence.converge(
        tmp_path,
        "P_p80_h15",
        "u0p2_p6M",
        [item[0] for item in meshes],
        out,
        mode="cell_count",
    )
    lmh = result["analyses"]["lmh_mass_balance"]
    assert lmh["apparent_order"] == pytest.approx(2.0)
    assert lmh["richardson_extrapolated"] == pytest.approx(10.0)
    module = result["analyses"]["lmh_module_area"]
    area = 2.0 * 7 * 0.01 * 0.01
    scaled = [value * 0.001 / area for value in _MONOTONE]
    assert module["values"] == pytest.approx(scaled)
    cp = result["analyses"]["cpc_window_avg_flux_minus_1"]
    assert cp["values"] == pytest.approx(
        [1.2 + value / 1000.0 - 1.0 for value in _MONOTONE]
    )
    text = (out / "mesh_convergence.csv").read_text(encoding="utf-8")
    assert "gci_fine" in text
    markdown = (out / "mesh_convergence.md").read_text(encoding="utf-8")
    assert "Apparent order" in markdown
    assert "cell count" in markdown

    flagged = convergence.converge(
        tmp_path,
        "P_p80_h15",
        "u0p2_p6M",
        [item[0] for item in meshes],
        tmp_path / "ratio-out",
        mode="ratio",
        ratio=2.0,
    )
    assert "supplied constant ratio" in flagged["markdown"]
    assert flagged["analyses"]["lmh_mass_balance"]["gci_fine"] == pytest.approx(
        1.25 / 11.0
    )


def test_cli_requires_exactly_one_refinement_option():
    with pytest.raises(SystemExit):
        convergence.main(
            [
                "--data-root",
                "D:/ro_data_mfbo/meshstudy",
                "--geo-id",
                "P_p80_h15",
                "--run-id",
                "u0p2_p6M",
                "--mesh-id",
                "max085_min006_cpg5_bl4_peel2",
                "max060_min006_cpg5_bl4_peel2",
                "max045_min006_cpg5_bl4_peel2",
                "--out-dir",
                "out",
            ]
        )


def test_missing_summary_names_the_path(tmp_path):
    mesh_id = "max085_min006_cpg5_bl4_peel2"
    manifest = (
        tmp_path / "meshes" / "pillar" / "P_p80_h15" / mesh_id / "manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "cell_count": 1000,
                "n_active_cells": 7,
                "cell_length_x_m": 0.01,
                "periodic_shift_y_m": 0.01,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="summary_metrics_wide.csv"):
        convergence.load_levels(
            tmp_path,
            "P_p80_h15",
            "u0p2_p6M",
            [
                mesh_id,
                "max060_min006_cpg5_bl4_peel2",
                "max045_min006_cpg5_bl4_peel2",
            ],
        )
