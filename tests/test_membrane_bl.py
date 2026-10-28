"""Membrane first-height override, transcript y1, and the D2450 queue."""

from __future__ import annotations

import json

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)
from ro.cp_concentration_stats import concentration_cp_summary_rows
from ro.fluent_report_helpers import mass_fraction_to_molar_concentration
from ro.manifest import ManifestError, read_mesh_manifest, read_run_manifest, write_mesh_manifest, write_run_manifest
from ro.membrane_bl import (
    derived_membrane_layer_count,
    geometric_total_thickness_mm,
    membrane_bl_stack,
    membrane_layer_override_suffix,
    membrane_bl_suffix,
    membrane_bl_summary_lines,
    parse_y1_area_weighted_mean_um,
    resolve_membrane_bl,
    y1_diagnostic_fields,
    y1_diagnostic_summary_rows,
    y1_um_from_mesh_id,
    format_y1_request_flag,
    first_y1_transcript_text,
)
from ro.membrane_y1 import raw_cell_concentration_mean
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.paths import mesh_dir, run_dir
from test_manifest import FAMILY, GEO_ID, RUN_ID, mesh_payload, run_payload

LABELS = ["wall_top_mem", "wall_bottom_mem"]
QUEUE = load_module(
    "d2450_y1_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "d2450_y1_queue.py",
)
REBUILD = load_module(
    "rebuild_mesh_manifest_y1_under_test",
    SCRIPTS_DIR / "rebuild_mesh_manifest.py",
)

WINDOW = {
    "cm_area_mean": 1100.0,
    "cm_q999": 1200.0,
    "cm_q99": 1150.0,
    "cp_ref_area": 1000.0,
    "cp_ref_flux": 1001.0,
    "n_faces_jw_nonpositive": 0,
    "area_jw_nonpositive": 0.0,
    "cpc_avg_area": 1.05,
    "cpc_avg_flux": 1.06,
    "cp_q999_area": 1.2,
    "cp_q999_flux": 1.21,
    "cp_q99_area": 1.1,
    "cp_q99_flux": 1.11,
}


def _metrics():
    return {
        "min_orthogonal_quality": 0.12,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.78,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 123456,
    }


def _stack(y1_um, *, size_mm=0.085, bl_layers=4, growth_rate=1.2):
    return membrane_bl_stack(
        y1_um,
        size_mm=size_mm,
        bl_layers=bl_layers,
        growth_rate=growth_rate,
        labels=LABELS,
    )


def test_stack_keeps_total_thickness_near_the_smooth_transition_reference():
    expected_layers = {5.7: 4, 3.0: 7, 2.0: 8, 1.4: 10, 1.0: 12}
    assert membrane_bl_summary_lines(None) == []
    assert membrane_bl_suffix(None) == ""
    for y1_um, layers in expected_layers.items():
        spec = _stack(y1_um)
        assert membrane_bl_suffix(y1_um) == f"_y1h{int(round(y1_um * 10)):03d}"
        assert spec["layers"] == layers
        assert spec["offset_method"] == "last-ratio"
        assert spec["first_height_mm"] == pytest.approx(2.0 * y1_um * 1.0e-3)
        assert spec["growth_rate"] == pytest.approx(1.2)
        assert spec["labels"] == LABELS
        reference = spec["reference_total_thickness_mm"]
        own = abs(spec["total_thickness_mm"] - reference)
        for neighbor in (layers - 1, layers + 1):
            other = geometric_total_thickness_mm(
                spec["first_height_mm"],
                neighbor,
                spec["growth_rate"],
            )
            assert own < abs(other - reference)
        assert membrane_bl_summary_lines(spec)[0].startswith(
            "Membrane BL y1 request"
        )


def test_resolve_rejects_a_stack_that_does_not_match_the_request():
    spec = _stack(2.0)
    assert resolve_membrane_bl(
        spec,
        size_mm=0.085,
        bl_layers=4,
        growth_rate=1.2,
        labels=LABELS,
    )["layers"] == 8
    changed = dict(spec)
    changed["layers"] = 4
    with pytest.raises(ValueError, match="does not match"):
        resolve_membrane_bl(
            changed,
            size_mm=0.085,
            bl_layers=4,
            growth_rate=1.2,
            labels=LABELS,
        )
    assert resolve_membrane_bl(
        None,
        size_mm=0.085,
        bl_layers=4,
        growth_rate=1.2,
        labels=LABELS,
    ) is None
    derived = derived_membrane_layer_count(
        2.0, size_mm=0.035, bl_layers=4, growth_rate=1.2
    )
    assert derived == 5
    pinned = membrane_bl_stack(
        2.0,
        size_mm=0.035,
        bl_layers=4,
        growth_rate=1.2,
        labels=LABELS,
        layers=8,
    )
    assert pinned["layers"] == 8
    assert pinned["first_height_mm"] == pytest.approx(0.004)
    assert resolve_membrane_bl(
        pinned,
        size_mm=0.035,
        bl_layers=4,
        growth_rate=1.2,
        labels=LABELS,
    )["total_thickness_mm"] == pytest.approx(pinned["total_thickness_mm"])
    assert membrane_layer_override_suffix(8, derived) == "_mbl8"
    assert membrane_layer_override_suffix(5, derived) == ""
    assert membrane_layer_override_suffix(None, derived) == ""


def test_manifest_records_the_stack_and_requires_the_mesh_id_token(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.m_min = 0.006
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2"
    plain = build_mesh_manifest_payload(
        cfg, _metrics(), "a" * 64, created_utc="2026-10-21T00:00:00Z"
    )
    assert "membrane_bl_y1" not in plain

    spec = _stack(2.0)
    cfg.membrane_bl_y1 = spec
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2_y1h020"
    recorded = build_mesh_manifest_payload(
        cfg, _metrics(), "b" * 64, created_utc="2026-10-21T00:00:00Z"
    )
    assert recorded["membrane_bl_y1"]["layers"] == 8
    assert recorded["membrane_bl_y1"]["first_height_mm"] == pytest.approx(0.004)
    assert recorded["membrane_bl_y1"]["offset_method"] == "last-ratio"

    directory = mesh_dir(FAMILY, GEO_ID, cfg.mesh_id)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["mesh_id"] = cfg.mesh_id
    payload["membrane_bl_y1"] = recorded["membrane_bl_y1"]
    payload["mesh_sha256"] = "b" * 64
    write_mesh_manifest(directory, payload)
    assert read_mesh_manifest(directory)["membrane_bl_y1"]["y1_um"] == pytest.approx(
        2.0
    )

    wrong_layers = dict(payload)
    wrong_layers["membrane_bl_y1"] = dict(recorded["membrane_bl_y1"])
    wrong_layers["membrane_bl_y1"]["layers"] = 4
    with pytest.raises(ManifestError, match="membrane_bl_y1"):
        write_mesh_manifest(directory, wrong_layers)

    missing_token = dict(payload)
    missing_token["mesh_id"] = "max085_min006_cpg5_bl4_peel2"
    with pytest.raises(ManifestError, match="peel token"):
        write_mesh_manifest(directory, missing_token)

    bulk_id = "max085_min006_cpg5_bl4_peel2_y1h020_bms034"
    bulk_payload = dict(payload)
    bulk_payload["mesh_id"] = bulk_id
    bulk_payload["bulk_max_size_mm"] = 0.034
    bulk_dir = mesh_dir(FAMILY, GEO_ID, bulk_id)
    bulk_dir.mkdir(parents=True)
    write_mesh_manifest(bulk_dir, bulk_payload)
    assert read_mesh_manifest(bulk_dir)["bulk_max_size_mm"] == pytest.approx(0.034)
    null_bulk = dict(bulk_payload)
    null_bulk["bulk_max_size_mm"] = None
    with pytest.raises(ManifestError, match="bulk_max_size_mm"):
        write_mesh_manifest(bulk_dir, null_bulk)

    copied = REBUILD._manifest_layout_overrides(payload)
    assert copied["membrane_bl_y1"]["layers"] == 8


def test_meshing_validation_requires_the_y1_token():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.membrane_bl_y1 = 2.0
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2"
    with pytest.raises(ValueError, match="membrane y1 suffix"):
        cfg.validate_for_meshing()
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2_y1h020"
    cfg.validate_for_meshing()
    cfg.bulk_max_size_mm = 0.034
    with pytest.raises(ValueError, match="bulk suffix"):
        cfg.validate_for_meshing()
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2_y1h020_bms034"
    cfg.validate_for_meshing()
    cfg.membrane_bl_y1 = membrane_bl_stack(
        2.0,
        size_mm=cfg.m_max,
        bl_layers=cfg.bl_layers,
        growth_rate=cfg.bl_growth_rate,
        labels=list(cfg.active_membrane_wall_labels),
        layers=4,
    )
    with pytest.raises(ValueError, match="membrane layer suffix"):
        cfg.validate_for_meshing()
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2_y1h020_mbl4_bms034"
    cfg.validate_for_meshing()
    cfg.bulk_max_size_mm = None
    cfg.membrane_bl_y1 = None
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2"


def test_transcript_y1_flags_a_miss_larger_than_twenty_percent(tmp_path):
    mesh_id = "max085_min006_cpg5_bl4_peel2_y1h020"
    text = "  y1 area-weighted mean        = 2.10 um\n"
    assert parse_y1_area_weighted_mean_um(text) == pytest.approx(2.10)
    assert y1_um_from_mesh_id(mesh_id) == pytest.approx(2.0)
    close = y1_diagnostic_fields(mesh_id, text)
    assert close["y1_request_mismatch"] is False
    assert close["y1_request_rel_error"] == pytest.approx(0.05)
    assert format_y1_request_flag(close) is None

    missed = y1_diagnostic_fields(
        mesh_id,
        "y1 area-weighted mean = 2.5 um\n",
    )
    assert missed["y1_request_mismatch"] is True
    assert missed["y1_request_rel_error"] == pytest.approx(0.25)
    assert "FLAG y1 request" in format_y1_request_flag(missed)

    absent = y1_diagnostic_fields(mesh_id, "iteration 1\n")
    assert absent["y1_request_mismatch"] is True
    assert "y1_udf_area_mean_um" not in absent
    assert "no 'y1 area-weighted mean'" in format_y1_request_flag(absent)

    unrequested = y1_diagnostic_fields(
        "max085_min006_cpg5_bl4_peel2",
        text,
    )
    assert unrequested == {"y1_udf_area_mean_um": pytest.approx(2.10)}
    rows = y1_diagnostic_summary_rows(close)
    assert [row["metric"] for row in rows] == [
        "y1_udf_area_mean_um",
        "y1_request_um",
        "y1_request_rel_error",
        "y1_request_mismatch",
    ]

    older = tmp_path / "solver_log_case.txt"
    newer = tmp_path / "fluent-2.trn"
    older.write_text("y1 area-weighted mean = 1.90 um\n", encoding="utf-8")
    newer.write_text("no diagnostic yet\n", encoding="utf-8")
    # Newest file has no line, so the parser keeps the earlier transcript.
    import os
    import time

    now = time.time()
    os.utime(older, (now - 50, now - 50))
    os.utime(newer, (now, now))
    found = first_y1_transcript_text(tmp_path)
    assert "1.90" in found


def test_run_manifest_stores_the_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_id = "max085_min006_cpg5_bl4_peel2_y1h020"
    directory = run_dir(FAMILY, GEO_ID, mesh_id, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    payload["mesh_id"] = mesh_id
    payload["u_mean_source_mesh_id"] = mesh_id
    payload.update(
        y1_diagnostic_fields(mesh_id, "y1 area-weighted mean = 2.5 um\n")
    )
    write_run_manifest(directory, payload)
    loaded = read_run_manifest(directory)
    assert loaded["y1_udf_area_mean_um"] == pytest.approx(2.5)
    assert loaded["y1_request_mismatch"] is True

    quiet = dict(payload)
    quiet["y1_request_mismatch"] = False
    with pytest.raises(ManifestError, match="y1_request_mismatch"):
        write_run_manifest(directory, quiet)

    missing = dict(payload)
    del missing["y1_udf_area_mean_um"]
    del missing["y1_request_rel_error"]
    missing["y1_request_mismatch"] = True
    write_run_manifest(directory, missing)
    nulled = dict(missing)
    nulled["y1_request_um"] = None
    with pytest.raises(ManifestError, match="not null"):
        write_run_manifest(directory, nulled)


def test_raw_cell_concentration_sits_beside_reconstructed_cm():
    faces = [
        {"yi": 0.035, "area": 2.0},
        {"yi": 0.070, "area": 2.0},
    ]
    mean = raw_cell_concentration_mean(
        faces,
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
    )
    assert mean == pytest.approx(
        mass_fraction_to_molar_concentration(0.0525, 998.2, 0.05844)
    )
    bare = concentration_cp_summary_rows({"window": WINDOW, "cells": {}})
    with_c1 = concentration_cp_summary_rows(
        {"window": WINDOW, "cells": {}},
        c1_area_mean_window=mean,
    )
    bare_names = [row["metric"] for row in bare]
    c1_names = [row["metric"] for row in with_c1]
    assert bare_names[0] == "cm_area_mean_window"
    assert "c1_area_mean_window" not in bare_names
    assert c1_names[:2] == ["cm_area_mean_window", "c1_area_mean_window"]
    assert c1_names[2:] == bare_names[1:]
    assert with_c1[1]["value"] == pytest.approx(mean)
    assert with_c1[1]["unit"] == "mol/m3"


def test_d2450_queue_requests_the_control_and_three_first_heights():
    studies = QUEUE.y1_study_overrides()
    assert [name for name, _overrides in studies] == [
        "y1h057",
        "y1h030",
        "y1h020",
        "y1h014",
    ]
    mesh_ids = [overrides["mesh_id"] for _name, overrides in studies]
    assert mesh_ids == [
        "max085_min006_cpg5_bl4_peel2_y1h057",
        "max085_min006_cpg5_bl4_peel2_y1h030",
        "max085_min006_cpg5_bl4_peel2_y1h020",
        "max085_min006_cpg5_bl4_peel2_y1h014",
    ]
    assert "y1h010" not in [name for name, _overrides in studies]
    assert "178.6" in QUEUE.__doc__
    assert "FirstHeight 0.002 mm" in QUEUE.__doc__
    assert [overrides["membrane_bl_y1"]["layers"] for _name, overrides in studies] == [
        4,
        7,
        8,
        10,
    ]
    assert [overrides["membrane_bl_y1"]["y1_um"] for _name, overrides in studies] == [
        5.7,
        3.0,
        2.0,
        1.4,
    ]
    assert all(
        overrides["membrane_bl_y1"]["offset_method"] == "last-ratio"
        for _name, overrides in studies
    )
    assert studies[0][1]["membrane_bl_y1"]["first_height_mm"] == pytest.approx(0.0114)
    jobs = QUEUE.y1_jobs(mesh_only=True)
    assert [job["id"] for job in jobs] == [
        "D2450_a45-y1h057-mesh",
        "D2450_a45-y1h030-mesh",
        "D2450_a45-y1h020-mesh",
        "D2450_a45-y1h014-mesh",
    ]
    for job, y1_um in zip(jobs, (5.7, 3.0, 2.0, 1.4)):
        argv = job["argv"]
        assert argv[1] == "scripts/mfbo/mesh_study_case.py"
        assert argv[argv.index("--data-root") + 1] == "C:/ro_data_mfbo/meshstudy"
        assert argv[argv.index("--geometry-root") + 1] == (
            "C:/ro_data_mfbo/geometries_pmdb"
        )
        assert argv[argv.index("--geometry-suffix") + 1] == ".pmdb"
        assert argv[argv.index("--geo-id") + 1] == "D2450_a45"
        assert argv[argv.index("--case") + 1] == "u0p2_p6M"
        assert argv[argv.index("--m-max") + 1] == "0.085"
        assert argv[argv.index("--bl-layers") + 1] == "4"
        assert float(argv[argv.index("--membrane-bl-y1-um") + 1]) == y1_um
        assert "--mesh-only" in argv
        assert "--reuse-mesh" not in argv
    full = QUEUE.y1_jobs(mesh_only=False)
    assert [job["id"] for job in full] == [
        "D2450_a45-y1h057",
        "D2450_a45-y1h030",
        "D2450_a45-y1h020",
        "D2450_a45-y1h014",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in full)
    assert all("--mesh-only" not in job["argv"] for job in full)


def _plant_mesh_manifest(root, family, mesh_id, cell_count):
    leaf = root / "meshes" / family / "D2450_a45" / mesh_id
    leaf.mkdir(parents=True)
    (leaf / "manifest.json").write_text(
        json.dumps({"cell_count": cell_count}),
        encoding="utf-8",
    )
    return leaf


def test_full_emitter_reuses_mesh_only_leaves(tmp_path, capsys):
    destination = tmp_path / "full.json"
    code = QUEUE.main(["--emit-queue", str(destination)])
    assert code == 0
    jobs = json.loads(destination.read_text(encoding="utf-8"))
    assert [job["id"] for job in jobs] == [
        "D2450_a45-y1h057",
        "D2450_a45-y1h030",
        "D2450_a45-y1h020",
        "D2450_a45-y1h014",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in jobs)
    assert all("--mesh-only" not in job["argv"] for job in jobs)
    printed = capsys.readouterr().out
    assert "y1h057: max085_min006_cpg5_bl4_peel2_y1h057" in printed
    assert "Wrote 4 jobs" in printed
    assert "y1h010" not in printed


def test_max_cells_drops_variants_over_the_mesh_only_count(tmp_path, capsys, monkeypatch):
    studies = QUEUE.y1_study_overrides()
    counts = {
        "y1h057": 800_000,
        "y1h030": 1_200_000,
        "y1h020": 2_000_000,
        "y1h014": 900_000,
    }
    for name, overrides in studies:
        _plant_mesh_manifest(
            tmp_path,
            overrides["family"],
            overrides["mesh_id"],
            counts[name],
        )
    kept, dropped = QUEUE.variants_under_cell_cap(studies, 1_000_000, tmp_path)
    assert [name for name, _overrides in kept] == ["y1h057", "y1h014"]
    assert [item[0] for item in dropped] == ["y1h030", "y1h020"]
    jobs = QUEUE.y1_jobs(studies=kept, data_root=tmp_path)
    assert [job["id"] for job in jobs] == [
        "D2450_a45-y1h057",
        "D2450_a45-y1h014",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in jobs)
    with pytest.raises(FileNotFoundError, match="mesh-only manifest is missing"):
        QUEUE.variants_under_cell_cap(studies, 1_000_000, tmp_path / "empty")

    monkeypatch.setattr(QUEUE, "DATA_ROOT", str(tmp_path))
    destination = tmp_path / "capped.json"
    code = QUEUE.main(
        ["--emit-queue", str(destination), "--max-cells", "1000000"]
    )
    assert code == 0
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert [job["id"] for job in written] == [
        "D2450_a45-y1h057",
        "D2450_a45-y1h014",
    ]
    printed = capsys.readouterr().out
    assert "dropped y1h030" in printed
    assert "cell_count 1200000 > 1000000" in printed
    with pytest.raises(ValueError, match="every variant exceeds"):
        QUEUE.main(
            ["--emit-queue", str(tmp_path / "empty.json"), "--max-cells", "1"]
        )
    assert not (tmp_path / "empty.json").exists()


def test_max_cells_does_not_apply_to_mesh_only(tmp_path):
    with pytest.raises(ValueError, match="Omit --mesh-only"):
        QUEUE.main(
            [
                "--emit-queue",
                str(tmp_path / "queue.json"),
                "--mesh-only",
                "--max-cells",
                "1000",
            ]
        )
    assert not (tmp_path / "queue.json").exists()
