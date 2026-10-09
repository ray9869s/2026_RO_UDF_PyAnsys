"""summarize_results turns a data root into one pasteable table. No Fluent."""

from __future__ import annotations

import csv
import json
import math
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
        "mesh_wall_time_s": 100.0,
    }
    payload.update(overrides)
    return payload


def _run(**overrides):
    payload = {
        "stop_reason": "qoi_converged",
        "continuity_final": 1.0e-6,
        "convergence_quality": "PASS",
        "solver_settings": {"viscous_model": "laminar"},
        "solver_wall_time_s": 10.0,
        "extraction_wall_time_s": 2.0,
        "processor_count": 50,
    }
    payload.update(overrides)
    return payload


def _summary(**overrides):
    row = {
        "y1_window_median_um": "12.5",
        "lmh_mass_balance": "25",
        "lmh_window_exposed": "20",
        "lmh_window_module": "18",
        "n_lead_excluded": "3",
        "excluded_length_m": "0.010395",
        "window_length_m": "0.01386",
        "window_table_version": "2026-10-09",
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
    assert complete["mesh_wall_time_s"] == "100"
    assert complete["solver_wall_time_s"] == "10"
    assert complete["extraction_wall_time_s"] == "2"
    assert complete["processor_count"] == "50"
    assert "lmh_module_area" not in complete["notes"]
    assert "active_window_fluid_volume_m3" in complete["notes"]
    assert "active_window_membrane_area_m2" in complete["notes"]
    assert "active_window_spacer_area_m2" in complete["notes"]
    assert "u_target_ms" in complete["notes"]
    assert complete["sc"] != summarize_results.MISSING
    assert complete["hydraulic_diameter_m"] == summarize_results.MISSING
    assert complete["hydraulic_diameter_schock_miquel_m"] == summarize_results.MISSING
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
    assert pillar["hydraulic_diameter_m"] == summarize_results.MISSING
    assert pillar["hydraulic_diameter_schock_miquel_m"] == summarize_results.MISSING
    assert "active_window_fluid_volume_m3" in pillar["notes"]

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


def test_processor_count_falls_back_to_the_mesh_manifest(tmp_path):
    run = _run()
    del run["processor_count"]
    _plant(
        tmp_path,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(processor_count=8),
        run=run,
        summary=_summary(),
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path, out_dir=tmp_path / "out"
    )
    assert rows[0]["processor_count"] == "8"
    assert rows[0]["mesh_wall_time_s"] == "100"
    assert "processor_count missing" not in rows[0]["notes"]


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
    assert row["mesh_wall_time_s"] == summarize_results.MISSING
    assert row["solver_wall_time_s"] == summarize_results.MISSING
    assert row["extraction_wall_time_s"] == summarize_results.MISSING
    assert row["processor_count"] == summarize_results.MISSING
    assert row["hydraulic_diameter_m"] == summarize_results.MISSING
    assert row["sc"] != summarize_results.MISSING


def _active_window(porosity, membrane_area_m2, spacer_area_m2, height_m=7.7e-4):
    length = PILLAR_N * PILLAR_CELL_M
    width = PILLAR_CELL_M
    box = length * width * height_m
    return {
        "active_window_fluid_volume_m3": str(porosity * box),
        "active_window_membrane_area_m2": str(membrane_area_m2),
        "active_window_spacer_area_m2": str(spacer_area_m2),
        "active_window_box_volume_m3": str(box),
        "active_window_porosity": str(porosity),
    }


def test_dimensionless_groups_match_hand_values(tmp_path):
    """Geometric d_h, Schock cross-check, and the film, friction, and power groups."""
    rho = 998.2
    mu = 8.93e-4
    diffusivity = 2.0e-9
    height = 7.7e-4
    porosity = 0.8
    velocity = 0.2
    lmh = 25.0
    cp_modulus = 1.2
    gradient = 10000.0
    length = PILLAR_N * PILLAR_CELL_M
    width = PILLAR_CELL_M
    box = length * width * height
    volume = porosity * box
    membrane = 0.9 * 2.0 * length * width
    spacer = 1600.0 * box
    assert summarize_results.RHO_KG_M3 == rho
    assert summarize_results.MU_PA_S == mu
    assert summarize_results.DIFFUSIVITY_M2_S == diffusivity

    diameter = 4.0 * volume / (membrane + spacer)
    schock = 4.0 * porosity / (2.0 / height + spacer / box)
    assert diameter != pytest.approx(schock)
    reynolds = rho * velocity * diameter / mu
    schmidt = mu / (rho * diffusivity)
    coefficient = (lmh / 3.6e6) / math.log(cp_modulus)
    sherwood = coefficient * diameter / diffusivity
    fanning = gradient * diameter / (2.0 * rho * velocity * velocity)
    darcy = 4.0 * fanning
    power = velocity * gradient / rho

    assert summarize_results.hydraulic_diameter_m(
        volume, membrane, spacer
    ) == pytest.approx(diameter)
    assert summarize_results.hydraulic_diameter_schock_miquel_m(
        porosity, height, spacer, box
    ) == pytest.approx(schock)
    assert summarize_results.reynolds_h(velocity, diameter) == pytest.approx(reynolds)
    assert summarize_results.schmidt_number() == pytest.approx(schmidt)
    assert summarize_results.sherwood_number(
        lmh, cp_modulus, diameter
    ) == pytest.approx(sherwood)
    assert summarize_results.fanning_friction_factor(
        gradient, diameter, velocity
    ) == pytest.approx(fanning)
    assert summarize_results.darcy_friction_factor(
        gradient, diameter, velocity
    ) == pytest.approx(darcy)
    assert summarize_results.specific_power_dissipation_w_per_kg(
        gradient, velocity
    ) == pytest.approx(power)

    _plant(
        tmp_path,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(
            porosity_eps=0.1,
            domain_extent_z_m=0.01,
            specific_surface_per_solid_volume_1_per_m=8000.0,
        ),
        run=_run(u_target_ms=velocity, u_mean_ms=velocity / 1.0036),
        summary=_summary(
            lmh_mass_balance=str(lmh),
            cpc_window_avg_flux=str(cp_modulus),
            pressure_drop_spacer_per_m=str(gradient),
            **_active_window(porosity, membrane, spacer, height),
        ),
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path, out_dir=tmp_path / "out"
    )
    row = rows[0]
    assert float(row["hydraulic_diameter_m"]) == pytest.approx(diameter)
    assert float(row["hydraulic_diameter_schock_miquel_m"]) == pytest.approx(schock)
    assert float(row["re_h"]) == pytest.approx(reynolds)
    assert float(row["sc"]) == pytest.approx(schmidt)
    assert float(row["sh_cpc_flux"]) == pytest.approx(sherwood)
    assert float(row["fanning_friction_factor"]) == pytest.approx(fanning)
    assert float(row["darcy_friction_factor"]) == pytest.approx(darcy)
    assert float(row["specific_power_dissipation_w_per_kg"]) == pytest.approx(power)
    assert "u_mean_ms" not in row["notes"]
    assert row["notes"] == ""


def test_empty_channel_friction_factor_is_twenty_four_over_re():
    rho = 998.2
    mu = 8.93e-4
    height = 7.7e-4
    velocity = 0.2
    length = 1.0
    width = 1.0
    volume = length * width * height
    membrane = 2.0 * length * width
    diameter = summarize_results.hydraulic_diameter_m(volume, membrane, 0.0)
    schock = summarize_results.hydraulic_diameter_schock_miquel_m(
        1.0, height, 0.0, volume
    )
    assert diameter == pytest.approx(2.0 * height)
    assert schock == pytest.approx(2.0 * height)
    gradient = 12.0 * mu * velocity / height**2
    reynolds = rho * velocity * diameter / mu
    assert summarize_results.fanning_friction_factor(
        gradient, diameter, velocity
    ) == pytest.approx(24.0 / reynolds)
    assert summarize_results.darcy_friction_factor(
        gradient, diameter, velocity
    ) == pytest.approx(96.0 / reynolds)


def test_geometric_diameter_differs_from_schock_when_membrane_is_not_projected():
    volume = 1.0e-6
    membrane = 1.5e-3
    spacer = 2.0e-3
    box = volume / 0.8
    height = 7.7e-4
    diameter = summarize_results.hydraulic_diameter_m(volume, membrane, spacer)
    schock = summarize_results.hydraulic_diameter_schock_miquel_m(
        0.8, height, spacer, box
    )
    assert diameter == pytest.approx(4.0 * volume / (membrane + spacer))
    assert schock == pytest.approx(4.0 * 0.8 / (2.0 / height + spacer / box))
    assert diameter != pytest.approx(schock)


def test_spacer_without_surface_or_wetted_area_leaves_diameter_missing(tmp_path):
    _plant(
        tmp_path,
        "pillar",
        "P_p80_h15",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(porosity_eps=0.9, domain_extent_z_m=7.7e-4),
        run=_run(u_target_ms=0.2, u_mean_ms=0.5),
        summary=_summary(),
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path, out_dir=tmp_path / "out"
    )
    row = rows[0]
    assert row["hydraulic_diameter_m"] == summarize_results.MISSING
    assert row["hydraulic_diameter_schock_miquel_m"] == summarize_results.MISSING
    assert row["re_h"] == summarize_results.MISSING
    assert row["sh_cpc_flux"] == summarize_results.MISSING
    assert row["fanning_friction_factor"] == summarize_results.MISSING
    assert float(row["specific_power_dissipation_w_per_kg"]) == pytest.approx(
        0.2 * 1000.0 / 998.2
    )
    assert "active_window_fluid_volume_m3" in row["notes"]
    assert "domain_extent_z_m" not in row["notes"]
    assert "u_mean_ms" not in row["notes"]


def test_sherwood_is_missing_when_the_modulus_is_not_above_one(tmp_path):
    _plant(
        tmp_path,
        "empty",
        "REF_empty",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(porosity_eps=1.0, domain_extent_z_m=7.7e-4),
        run=_run(u_target_ms=0.2),
        summary=_summary(
            cpc_window_avg_flux="1",
            **_active_window(
                1.0,
                2.0 * PILLAR_N * PILLAR_CELL_M * PILLAR_CELL_M,
                0.0,
            ),
        ),
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path, out_dir=tmp_path / "out"
    )
    row = rows[0]
    assert float(row["hydraulic_diameter_m"]) == pytest.approx(2.0 * 7.7e-4)
    assert row["sh_cpc_flux"] == summarize_results.MISSING
    assert "sh_cpc_flux undefined" in row["notes"]
    assert row["re_h"] != summarize_results.MISSING


def _window_table(path, geo_id, mesh_id, n_lead, dx, n_active):
    path.write_text(
        json.dumps(
            {
                geo_id: {
                    "n_lead_excluded": n_lead,
                    "excluded_length_m": n_lead * dx,
                    "window_length_m": (n_active - n_lead) * dx,
                    "mesh_id": mesh_id,
                    "date": "2026-10-09",
                    "source_data_root": "C:/ro_data",
                    "short_window": False,
                }
            }
        ),
        encoding="utf-8",
    )


def test_stored_window_lmh_is_copied_without_recompute(tmp_path):
    _plant(
        tmp_path,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
        mesh=_mesh(),
        run=_run(),
        summary=_summary(),
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path,
        out_dir=tmp_path / "out",
        window_table_path=tmp_path / "missing_table.json",
    )
    row = rows[0]
    assert row["lmh_window_exposed"] == "20"
    assert row["lmh_window_module"] == "18"
    assert row["n_lead_excluded"] == "3"
    assert row["window_table_version"] == "2026-10-09"
    assert "recomputed_from_cells" not in row["notes"]


def test_old_leaf_window_lmh_is_recomputed_from_cells(tmp_path):
    geo_id = "D2450_a45"
    mesh_id = "max085_min006_cpg5_bl4_peel2"
    dx = 0.001
    n_active = 4
    n_lead = 1
    table = tmp_path / "evaluation_window_table.json"
    _window_table(table, geo_id, mesh_id, n_lead, dx, n_active)
    summary = _summary()
    for key in (
        "lmh_window_exposed",
        "lmh_window_module",
        "n_lead_excluded",
        "excluded_length_m",
        "window_length_m",
        "window_table_version",
    ):
        del summary[key]
    summary.update(
        {
            "pp_jw_m_per_s_cell_3": "1e-6",
            "pp_jw_m_per_s_cell_4": "2e-6",
            "pp_jw_m_per_s_cell_5": "3e-6",
            "pp_membrane_area_cell_3_m2": "0.01",
            "pp_membrane_area_cell_4_m2": "0.01",
            "pp_membrane_area_cell_5_m2": "0.02",
        }
    )
    _plant(
        tmp_path,
        "diamond",
        geo_id,
        mesh_id,
        "u0p2_p6M",
        mesh=_mesh(
            n_active_cells=n_active,
            n_buffer_in=1,
            cell_length_x_m=dx,
            periodic_shift_y_m=0.002,
        ),
        run=_run(),
        summary=summary,
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path,
        out_dir=tmp_path / "out",
        window_table_path=table,
    )
    row = rows[0]
    assert float(row["lmh_window_exposed"]) == pytest.approx(8.1)
    assert float(row["lmh_window_module"]) == pytest.approx(27000.0)
    assert row["n_lead_excluded"] == "1"
    assert float(row["excluded_length_m"]) == pytest.approx(0.001)
    assert float(row["window_length_m"]) == pytest.approx(0.003)
    assert row["window_table_version"] == "2026-10-09"
    assert "recomputed_from_cells" in row["notes"]


def test_old_leaf_without_a_cell_column_stays_missing(tmp_path):
    geo_id = "D2450_a45"
    mesh_id = "max085_min006_cpg5_bl4_peel2"
    table = tmp_path / "evaluation_window_table.json"
    _window_table(table, geo_id, mesh_id, 1, 0.001, 4)
    summary = _summary()
    for key in (
        "lmh_window_exposed",
        "lmh_window_module",
        "n_lead_excluded",
        "excluded_length_m",
        "window_length_m",
        "window_table_version",
    ):
        del summary[key]
    summary["pp_jw_m_per_s_cell_3"] = "1e-6"
    _plant(
        tmp_path,
        "diamond",
        geo_id,
        mesh_id,
        "u0p2_p6M",
        mesh=_mesh(
            n_active_cells=4,
            n_buffer_in=1,
            cell_length_x_m=0.001,
            periodic_shift_y_m=0.002,
        ),
        run=_run(),
        summary=summary,
    )
    _destination, rows, _markdown = summarize_results.summarize(
        tmp_path,
        out_dir=tmp_path / "out",
        window_table_path=table,
    )
    row = rows[0]
    assert row["lmh_window_exposed"] == summarize_results.MISSING
    assert row["lmh_window_module"] == summarize_results.MISSING
    assert "recomputed_from_cells" not in row["notes"]
    assert "pp_membrane_area_cell_3_m2" in row["notes"]
