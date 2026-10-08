"""Mesh phase wall times. No Fluent session."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR
from ro.manifest import (
    MESH_PHASE_NAMES,
    ManifestError,
    read_mesh_manifest,
    write_mesh_manifest,
)
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload
from test_worker_manifests import _matching_extent_metrics, load_meshing_code


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_unrun_phase_is_absent_and_a_failure_keeps_that_phase():
    monotonic = _Clock()
    clock = load_meshing_code().MeshPhaseClock(monotonic)
    with clock.phase("launch"):
        monotonic.advance(1.5)
    with pytest.raises(RuntimeError, match="import failed"):
        with clock.phase("geometry_import"):
            monotonic.advance(0.25)
            raise RuntimeError("import failed")
    assert "local_sizing" not in clock.phases
    assert "surface_mesh" not in clock.phases
    assert clock.phases["launch"] == pytest.approx(1.5)
    assert clock.phases["geometry_import"] == pytest.approx(0.25)
    timing = clock.timing_fields(50)
    assert timing["processor_count"] == 50
    assert timing["mesh_wall_time_s"] == pytest.approx(1.75)
    assert timing["mesh_phase_wall_time_s"] == {
        "launch": pytest.approx(1.5),
        "geometry_import": pytest.approx(0.25),
    }
    assert clock.timing_fields(50)["mesh_wall_time_s"] == pytest.approx(1.75)


def test_no_phase_omits_the_timing_fields():
    clock = load_meshing_code().MeshPhaseClock(_Clock())
    assert clock.timing_fields(8) is None


def test_worker_manifest_stores_timing_without_changing_existing_fields(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    meshing = load_meshing_code()
    from helpers import load_run_config, populate_valid_meshing_config

    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    paths = meshing.resolve_meshing_paths(cfg)
    paths["mesh_directory"].mkdir(parents=True)
    paths["mesh_file"].write_bytes(b"test mesh bytes")
    metrics = _matching_extent_metrics(cfg)
    created = "2026-08-21T08:00:00Z"
    meshing.write_worker_mesh_manifest(
        cfg,
        paths["mesh_directory"],
        paths["mesh_file"],
        metrics,
        created_utc=created,
    )
    original = read_mesh_manifest(paths["mesh_directory"])
    assert "mesh_wall_time_s" not in original
    assert "mesh_phase_wall_time_s" not in original

    timing = {
        "mesh_wall_time_s": 3.5,
        "mesh_phase_wall_time_s": {
            "launch": 1.0,
            "geometry_import": 2.5,
        },
        "processor_count": 50,
    }
    meshing.write_worker_mesh_manifest(
        cfg,
        paths["mesh_directory"],
        paths["mesh_file"],
        metrics,
        created_utc=created,
        timing=timing,
    )
    loaded = read_mesh_manifest(paths["mesh_directory"])
    assert loaded["mesh_wall_time_s"] == pytest.approx(3.5)
    assert loaded["mesh_phase_wall_time_s"]["launch"] == pytest.approx(1.0)
    assert "local_sizing" not in loaded["mesh_phase_wall_time_s"]
    assert loaded["processor_count"] == 50
    for key, value in original.items():
        assert loaded[key] == value


def test_mesh_manifest_rejects_a_phase_total_that_does_not_add_up(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    original = mesh_payload()
    write_mesh_manifest(directory, original)
    timed = dict(original)
    timed["mesh_wall_time_s"] = 5.0
    timed["mesh_phase_wall_time_s"] = {"launch": 1.0, "write": 1.0}
    timed["processor_count"] = 50

    with pytest.raises(ManifestError, match="sum"):
        write_mesh_manifest(directory, timed)

    rejected = dict(original)
    rejected["mesh_phase_wall_time_s"] = {"not_a_phase": 1.0}
    rejected["mesh_wall_time_s"] = 1.0
    with pytest.raises(ManifestError, match="unknown"):
        write_mesh_manifest(directory, rejected)

    zeroed = dict(original)
    zeroed["mesh_wall_time_s"] = 0.0
    zeroed["mesh_phase_wall_time_s"] = {}
    with pytest.raises(ManifestError, match="non-empty"):
        write_mesh_manifest(directory, zeroed)
    loaded = read_mesh_manifest(directory)
    assert "mesh_wall_time_s" not in loaded
    assert loaded["cell_count"] == original["cell_count"]


def test_workflow_records_every_phase_and_keeps_the_run_record_clock():
    source = (SCRIPTS_DIR / "meshing_code_260616.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "phase":
            continue
        names.append(node.args[0].value)
    assert set(names) == set(MESH_PHASE_NAMES)
    assert names.count("periodic_setup") == 2
    assert "wall_time_seconds=time.monotonic() - run_started" in source
    assert Path(SCRIPTS_DIR / "meshing_code_260616.py").name == "meshing_code_260616.py"
