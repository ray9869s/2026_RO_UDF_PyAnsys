"""Local-sizing override parsing, mesh_id tokens, and the D0817 queue."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from helpers import (
    SCRIPTS_DIR,
    apply_json_overrides,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)
from ro.local_sizing import (
    extra_local_sizing_arguments,
    local_sizing_suffix,
    parse_local_face_size,
    parse_local_proximity,
)
from ro.manifest import ManifestError, read_mesh_manifest, write_mesh_manifest
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, mesh_payload

driver = load_module(
    "mesh_study_local_sizing_under_test",
    SCRIPTS_DIR / "mfbo" / "mesh_study_case.py",
)
queue = load_module(
    "d0817_contact_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "d0817_contact_queue.py",
)
rebuild = load_module(
    "rebuild_mesh_manifest_local_sizing_under_test",
    SCRIPTS_DIR / "rebuild_mesh_manifest.py",
)

PROXIMITY = {
    "pairs": [
        {"a": ["wall_spacer"], "b": ["wall_top_mem", "wall_bottom_mem"]},
    ],
    "cells_per_gap": 9,
    "min_size_mm": 0.003,
}
FACE = {
    "labels": ["wall_top_mem", "wall_bottom_mem"],
    "size_mm": 0.020,
    "growth_rate": 1.2,
}


def _metrics():
    return {
        "min_orthogonal_quality": 0.12,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.78,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 123456,
    }


def test_unset_overrides_add_no_token_and_no_children():
    assert parse_local_proximity(None) is None
    assert parse_local_face_size(None) is None
    assert local_sizing_suffix(None, None) == ""
    assert extra_local_sizing_arguments(
        None,
        None,
        max_size_mm=0.045,
        growth_rate=1.2,
        curvature_normal_angle=18,
    ) == []


def test_proximity_and_face_size_parse_and_encode():
    proximity = parse_local_proximity(PROXIMITY)
    face = parse_local_face_size(FACE)
    assert proximity["cells_per_gap"] == 9
    assert proximity["min_size_mm"] == pytest.approx(0.003)
    assert proximity["pairs"][0]["a"] == ["wall_spacer"]
    assert face["size_mm"] == pytest.approx(0.020)
    assert face["growth_rate"] == pytest.approx(1.2)
    assert local_sizing_suffix(proximity, None) == "_lpx9m003"
    assert local_sizing_suffix(None, face) == "_fs020"
    assert local_sizing_suffix(proximity, face) == "_lpx9m003_fs020"
    finer = parse_local_proximity(
        {
            "pairs": PROXIMITY["pairs"],
            "cells_per_gap": 13,
            "min_size_mm": 0.002,
        }
    )
    assert local_sizing_suffix(finer, None) == "_lpx13m002"
    tight_growth = parse_local_face_size({**FACE, "growth_rate": 1.15})
    assert local_sizing_suffix(None, tight_growth) == "_fs020g115"
    suffixes = {
        local_sizing_suffix(proximity, None),
        local_sizing_suffix(finer, None),
        local_sizing_suffix(None, face),
        local_sizing_suffix(proximity, face),
    }
    assert len(suffixes) == 4


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"cells_per_gap": 9, "min_size_mm": 0.003}, "keys mismatch"),
        ({**PROXIMITY, "extra": 1}, "keys mismatch"),
        ({**PROXIMITY, "pairs": []}, "non-empty list"),
        ({**PROXIMITY, "cells_per_gap": 9.5}, "integer"),
        ({**PROXIMITY, "min_size_mm": 0.0035}, "integer token"),
        (
            {
                **PROXIMITY,
                "pairs": [
                    {"a": ["wall_spacer"], "b": ["wall_spacer"]},
                ],
            },
            "both sides",
        ),
        ("wall_spacer", "object"),
    ],
)
def test_proximity_override_rejects_bad_objects(value, message):
    with pytest.raises(ValueError, match=message):
        parse_local_proximity(value)


def test_face_size_override_rejects_a_missing_growth_rate():
    with pytest.raises(ValueError, match="keys mismatch"):
        parse_local_face_size(
            {"labels": ["wall_top_mem"], "size_mm": 0.020}
        )


def test_extra_children_are_proximity_then_face_size():
    children = extra_local_sizing_arguments(
        PROXIMITY,
        FACE,
        max_size_mm=0.045,
        growth_rate=1.2,
        curvature_normal_angle=18,
    )
    assert [child["BOIControlName"] for child in children] == [
        "proximity_contact_1",
        "facesize_local_1",
    ]
    proximity, face = children
    assert proximity["BOIExecution"] == "Proximity"
    assert proximity["BOIScopeTo"] == "faces"
    assert proximity["BOICellsPerGap"] == 9
    assert proximity["BOIMinSize"] == pytest.approx(0.003)
    assert proximity["BOIMaxSize"] == 0.045
    assert proximity["BOIFaceLabelList"] == [
        "wall_spacer",
        "wall_top_mem",
        "wall_bottom_mem",
    ]
    assert face["BOIExecution"] == "Face Size"
    assert face["BOISize"] == pytest.approx(0.020)
    assert face["BOIGrowthRate"] == pytest.approx(1.2)
    assert face["BOIFaceLabelList"] == ["wall_top_mem", "wall_bottom_mem"]
    assert "BOICellsPerGap" not in face


def test_study_mesh_id_appends_local_sizing_after_peel():
    template = {
        "family": "diamond",
        "geo_id": "D0817_a30",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "case_name": "max085_min006_cpg5_bl4_peel2",
        "m_max": 0.085,
        "m_min": 0.006,
        "m_cpg": 5,
        "bl_layers": 4,
        "peel_layers": 2,
        "bl_height_factor": 0.4,
    }
    proximity = driver.apply_study_overrides(
        template,
        {"m_max": 0.045, "local_proximity": PROXIMITY},
    )
    assert proximity["mesh_id"] == "max045_min006_cpg5_bl4_peel2_lpx9m003"
    assert proximity["local_proximity"]["cells_per_gap"] == 9
    face = driver.apply_study_overrides(
        template,
        {"m_max": 0.045, "local_face_size": FACE},
    )
    assert face["mesh_id"] == "max045_min006_cpg5_bl4_peel2_fs020"
    both = driver.apply_study_overrides(
        template,
        {"m_max": 0.045, "local_proximity": PROXIMITY, "local_face_size": FACE},
    )
    assert both["mesh_id"] == "max045_min006_cpg5_bl4_peel2_lpx9m003_fs020"
    assert driver.MESH_ID_RE.fullmatch(both["mesh_id"])
    unchanged = driver.apply_study_overrides(template, {"bl_layers": 8})
    assert unchanged["mesh_id"] == "max085_min006_cpg5_bl8_peel2"
    assert "local_proximity" not in unchanged


def test_run_config_override_rejects_a_mesh_id_without_the_token():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    assert cfg.local_proximity is None
    assert cfg.local_face_size is None
    cfg.validate_for_meshing()

    apply_json_overrides(
        cfg,
        {
            "local_proximity": PROXIMITY,
            "mesh_id": "max085_min005_cpg5_bl4_peel2_lpx9m003",
        },
    )
    cfg.validate_for_meshing()
    assert cfg.local_proximity["cells_per_gap"] == 9

    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2"
    with pytest.raises(ValueError, match="peel token must match"):
        cfg.validate_for_meshing()
    cfg.mesh_id = "max085_min005_cpg5_bl4_peel2_lpx9m003"
    cfg.local_proximity = {**PROXIMITY, "nope": 1}
    with pytest.raises(ValueError, match="local sizing is invalid"):
        cfg.validate_for_meshing()


def test_manifest_records_local_sizing_only_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.m_min = 0.006
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2"
    plain = build_mesh_manifest_payload(
        cfg, _metrics(), "a" * 64, created_utc="2026-10-19T00:00:00Z"
    )
    assert "local_proximity" not in plain
    assert "local_face_size" not in plain

    cfg.local_proximity = parse_local_proximity(PROXIMITY)
    cfg.local_face_size = parse_local_face_size(FACE)
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2_lpx9m003_fs020"
    recorded = build_mesh_manifest_payload(
        cfg, _metrics(), "b" * 64, created_utc="2026-10-19T00:00:00Z"
    )
    assert recorded["local_proximity"]["min_size_mm"] == pytest.approx(0.003)
    assert recorded["local_face_size"]["labels"] == FACE["labels"]

    directory = mesh_dir(FAMILY, GEO_ID, cfg.mesh_id)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["mesh_id"] = cfg.mesh_id
    payload["local_proximity"] = recorded["local_proximity"]
    payload["local_face_size"] = recorded["local_face_size"]
    payload["mesh_sha256"] = "b" * 64
    write_mesh_manifest(directory, payload)
    assert read_mesh_manifest(directory)["local_proximity"]["cells_per_gap"] == 9

    mismatched = dict(payload)
    mismatched["local_proximity"] = parse_local_proximity(
        {**PROXIMITY, "cells_per_gap": 13, "min_size_mm": 0.002}
    )
    with pytest.raises(ManifestError, match="peel token must match"):
        write_mesh_manifest(directory, mismatched)
    bare = dict(payload)
    del bare["local_proximity"]
    del bare["local_face_size"]
    with pytest.raises(ManifestError, match="peel token must match"):
        write_mesh_manifest(directory, bare)

    changed = dict(payload)
    changed["local_proximity"] = parse_local_proximity(
        {
            "pairs": [{"a": ["wall_spacer"], "b": ["wall_top_mem"]}],
            "cells_per_gap": 9,
            "min_size_mm": 0.003,
        }
    )
    with pytest.raises(ManifestError, match="local_proximity"):
        write_mesh_manifest(directory, changed)


def test_rebuild_copies_local_sizing_back_onto_the_config():
    payload = mesh_payload()
    assert "local_proximity" not in rebuild._manifest_layout_overrides(payload)
    payload["local_proximity"] = parse_local_proximity(PROXIMITY)
    payload["local_face_size"] = parse_local_face_size(FACE)
    overrides = rebuild._manifest_layout_overrides(payload)
    assert overrides["local_proximity"]["cells_per_gap"] == 9
    assert overrides["local_face_size"]["size_mm"] == pytest.approx(0.020)


def test_contact_queue_ids_and_mesh_only_flag():
    expected = {
        "lpx9m003": "max045_min006_cpg5_bl4_peel2_lpx9m003",
        "lpx13m002": "max045_min006_cpg5_bl4_peel2_lpx13m002",
        "fs020": "max045_min006_cpg5_bl4_peel2_fs020",
        "lpx9m003-fs020": "max045_min006_cpg5_bl4_peel2_lpx9m003_fs020",
    }
    studies = queue.contact_study_overrides()
    assert [name for name, _overrides in studies] == list(expected)
    mesh_ids = [overrides["mesh_id"] for _name, overrides in studies]
    assert mesh_ids == list(expected.values())
    assert len(set(mesh_ids)) == 4

    mesh_jobs = queue.contact_jobs(mesh_only=True)
    full_jobs = queue.contact_jobs(mesh_only=False)
    assert [job["id"] for job in mesh_jobs] == [
        "D0817_a30-lpx9m003-mesh",
        "D0817_a30-lpx13m002-mesh",
        "D0817_a30-fs020-mesh",
        "D0817_a30-lpx9m003-fs020-mesh",
    ]
    assert all(job["argv"][-1] == "--mesh-only" for job in mesh_jobs)
    assert all("--mesh-only" not in job["argv"] for job in full_jobs)
    for job in mesh_jobs:
        argv = job["argv"]
        assert argv[1] == "scripts/mfbo/mesh_study_case.py"
        assert argv[argv.index("--data-root") + 1] == queue.DATA_ROOT
        assert argv[argv.index("--geo-id") + 1] == "D0817_a30"
        assert argv[argv.index("--case") + 1] == "u0p2_p6M"
        assert argv[argv.index("--m-max") + 1] == "0.045"
        assert argv[argv.index("--bl-layers") + 1] == "4"
        assert argv[argv.index("--geometry-suffix") + 1] == ".pmdb"
        assert argv[argv.index("--geometry-root") + 1] == queue.GEOMETRY_ROOT
        assert queue.DATA_ROOT == "C:/ro_data_mfbo/meshstudy"
        assert queue.GEOMETRY_ROOT == "C:/ro_data_mfbo/geometries_pmdb"

    first = mesh_jobs[0]["argv"]
    parsed = json.loads(first[first.index("--local-proximity") + 1])
    assert parsed["cells_per_gap"] == 9
    assert parsed["min_size_mm"] == pytest.approx(0.003)
    assert parsed["pairs"][0]["a"] == ["wall_spacer"]
    assert parsed["pairs"][0]["b"] == ["wall_top_mem", "wall_bottom_mem"]
    face_job = mesh_jobs[2]["argv"]
    face = json.loads(face_job[face_job.index("--local-face-size") + 1])
    assert face["labels"] == ["wall_top_mem", "wall_bottom_mem"]
    assert face["size_mm"] == pytest.approx(0.020)
    assert face["growth_rate"] == pytest.approx(1.2)
    assert "--local-proximity" not in face_job
    both = mesh_jobs[3]["argv"]
    assert "--local-proximity" in both
    assert "--local-face-size" in both


def test_mesh_only_prints_the_cell_count_and_does_not_solve(
    monkeypatch, tmp_path, capsys
):
    template = {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "case_name": "max085_min006_cpg5_bl4_peel2",
        "m_max": 0.085,
        "m_min": 0.006,
        "m_cpg": 5,
        "bl_layers": 4,
        "peel_layers": 2,
        "bl_height_factor": 0.4,
    }
    monkeypatch.setattr(
        driver,
        "load_mesh_template",
        lambda geo_id: (dict(template), dict(template), 0),
    )

    def fake_mesh(overrides, data_root, mesh_directory, mesh_id, max_retries):
        mesh_directory.mkdir(parents=True)
        (mesh_directory / "manifest.json").write_text(
            json.dumps({"cell_count": 43210}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver, "launch_meshing", fake_mesh)

    def fail_solver(*args, **kwargs):
        raise AssertionError("mesh-only must not solve")

    monkeypatch.setattr(driver.campaign, "launch_solver", fail_solver)
    source = tmp_path / "prod" / "geometries" / "diamond" / "D2450_a45"
    source.mkdir(parents=True)
    (source / "D2450_a45.dsco").write_bytes(b"dsco")
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", tmp_path / "prod")

    code = driver.main(
        [
            "--data-root",
            str(tmp_path / "study"),
            "--geo-id",
            "D2450_a45",
            "--m-max",
            "0.045",
            "--local-proximity",
            json.dumps(PROXIMITY),
            "--mesh-only",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert (
        "Mesh cell count before solve: "
        "max045_min006_cpg5_bl4_peel2_lpx9m003 43210"
    ) in printed
    assert not (
        tmp_path / "study" / "runs" / "diamond" / "D2450_a45"
    ).exists()
