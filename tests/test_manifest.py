"""Tests for validated mesh and run manifests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ro.manifest import (
    MESH_MANIFEST_REQUIRED_FIELDS,
    RUN_MANIFEST_REQUIRED_FIELDS,
    ManifestError,
    assert_mesh_file_overwrite_allowed,
    iter_mesh_manifests,
    iter_run_manifests,
    read_mesh_manifest,
    read_run_manifest,
    write_mesh_manifest,
    write_run_manifest,
)
from ro.paths import mesh_dir, run_dir


FAMILY = "diamond"
GEO_ID = "D2450_a45"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
RUN_ID = "u0p2_p6M"


def mesh_payload():
    return {
        "schema_version": 1,
        "family": FAMILY,
        "geo_id": GEO_ID,
        "mesh_id": MESH_ID,
        "spacing_code": "D2450",
        "attack_angle_deg": 45,
        "filament_d_m": 4.0e-4,
        "bridge_radius_m": 1.10e-4,
        "overlap_m": 0.0,
        "n_active_cells": 7,
        "n_buffer_in": 1,
        "n_buffer_out": 3,
        "cell_length_x_m": 0.003465,
        "membrane_wall_base_names": ["wall_top_mem", "wall_bottom_mem"],
        "buffer_wall_base_names": [
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
        ],
        "n_lead_excluded": 3,
        "n_trail_excluded": 0,
        "max_size": 0.085,
        "min_size": 0.006,
        "cpg": 5,
        "bl": 4,
        "peel": 2,
        "ortho_min": 0.12,
        "AR_max": 42.0,
        "skewness_max": 0.78,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 123456,
        "inlet_profile_G": None,
        "mesh_sha256": "a" * 64,
        "created_utc": "2026-08-21T00:00:00Z",
        "generator_version": "meshing_code_260616.py",
    }


def run_payload():
    return {
        "schema_version": 1,
        "family": FAMILY,
        "geo_id": GEO_ID,
        "mesh_id": MESH_ID,
        "mesh_sha256": "a" * 64,
        "run_id": RUN_ID,
        "u_mean_ms": 0.199281,
        "p_gauge_pa": 6.0e6,
        "u_target_ms": 0.2,
        "inlet_bc_type": "parabolic",
        "udf_version": "260816_RO_UDF.c",
        "solver_settings": {
            "max_iterations": 2000,
            "residual_target": 1.0e-7,
            "operating_pressure": 101325.0,
        },
        "stop_reason": "qoi_converged",
        "created_utc": "2026-08-21T00:00:00Z",
    }


def write_test_run(*, run_id: str = RUN_ID, **updates) -> Path:
    """Write a valid run tree + manifest. Caller must set RO_DATA_ROOT first."""
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = run_payload()
    payload["run_id"] = run_id
    payload.update(updates)
    write_run_manifest(directory, payload)
    return directory


def test_required_field_tuples_match_schema_payloads():
    assert set(MESH_MANIFEST_REQUIRED_FIELDS) == set(mesh_payload())
    assert set(RUN_MANIFEST_REQUIRED_FIELDS) == set(run_payload())


def test_mesh_manifest_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()

    path = write_mesh_manifest(directory, payload)

    assert path == directory / "manifest.json"
    assert read_mesh_manifest(directory) == payload
    assert list(iter_mesh_manifests()) == [(path, payload)]


def test_run_manifest_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()

    path = write_run_manifest(directory, payload)

    assert path == directory / "manifest.json"
    assert read_run_manifest(directory) == payload
    assert list(iter_run_manifests()) == [(path, payload)]


def test_iter_run_manifests_refuses_leaf_without_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)

    with pytest.raises(ManifestError, match="Could not read manifest"):
        list(iter_run_manifests())


def test_iter_run_manifests_refuses_ids_that_disagree_with_path(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = write_test_run()
    payload = run_payload()
    payload["geo_id"] = "D1225_a45"
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="ids do not match"):
        list(iter_run_manifests())


def test_parabolic_run_allows_null_mean_until_profile_g_is_known(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    payload["u_mean_ms"] = None
    payload["stop_reason"] = "RUNNING"

    write_run_manifest(directory, payload)

    assert read_run_manifest(directory)["u_mean_ms"] is None


def test_plug_run_rejects_null_mean(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    payload["inlet_bc_type"] = "plug"
    payload["u_mean_ms"] = None

    with pytest.raises(ManifestError, match="may not be null for plug"):
        write_run_manifest(directory, payload)


def test_run_rejects_stop_reason_outside_solver_enum(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    payload["stop_reason"] = "CONVERGED"

    with pytest.raises(ManifestError, match="solver stop reason"):
        write_run_manifest(directory, payload)


def test_lazy_manifest_values_fill_once(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    mesh = mesh_payload()
    mesh["inlet_profile_G"] = None
    write_mesh_manifest(mesh_directory, mesh)
    mesh["inlet_profile_G"] = 1.003609
    write_mesh_manifest(mesh_directory, mesh)
    mesh["inlet_profile_G"] = 1.1
    with pytest.raises(ManifestError, match="changed parameters.*inlet_profile_G"):
        write_mesh_manifest(mesh_directory, mesh)

    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    run = run_payload()
    run["u_mean_ms"] = None
    write_run_manifest(run_directory, run)
    run["u_mean_ms"] = run["u_target_ms"] / 1.003609
    write_run_manifest(run_directory, run)
    run["u_mean_ms"] = 0.1
    with pytest.raises(ManifestError, match="changed parameters.*u_mean_ms"):
        write_run_manifest(run_directory, run)


def test_manifest_ids_must_match_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["geo_id"] = "D1225_a45"

    with pytest.raises(ManifestError, match="ids do not match"):
        write_mesh_manifest(directory, payload)


def test_mesh_manifest_peel_must_match_mesh_id(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["peel"] = 0

    with pytest.raises(ManifestError, match="peel token must match"):
        write_mesh_manifest(directory, payload)


def test_mesh_overwrite_guard_rejects_changed_parameter(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    write_mesh_manifest(directory, payload)
    changed = copy.deepcopy(payload)
    changed["overlap_m"] = 1.0e-5

    with pytest.raises(ManifestError, match="changed parameters.*overlap_m"):
        write_mesh_manifest(directory, changed)


def test_identical_mesh_rewrite_is_silent_and_atomic(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()

    write_mesh_manifest(directory, payload)
    write_mesh_manifest(directory, copy.deepcopy(payload))

    assert read_mesh_manifest(directory) == payload
    assert [path.name for path in directory.iterdir()] == ["manifest.json"]


def test_run_overwrite_guard_rejects_changed_parameter(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    write_run_manifest(directory, payload)
    changed = copy.deepcopy(payload)
    changed["p_gauge_pa"] = 4.0e6

    with pytest.raises(ManifestError, match="changed parameters.*p_gauge_pa"):
        write_run_manifest(directory, changed)


def test_mesh_quality_is_required_when_sha_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["ortho_min"] = None

    with pytest.raises(ManifestError, match="may not be null"):
        write_mesh_manifest(directory, payload)


def test_mesh_sha256_change_allowed_when_no_runs_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    write_mesh_manifest(directory, payload)
    rewritten = copy.deepcopy(payload)
    rewritten["mesh_sha256"] = "b" * 64

    write_mesh_manifest(directory, rewritten)

    assert read_mesh_manifest(directory)["mesh_sha256"] == "b" * 64


def test_mesh_sha256_change_refused_when_runs_reference_old_hash(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, run_payload())
    rewritten = copy.deepcopy(mesh_payload())
    rewritten["mesh_sha256"] = "b" * 64

    with pytest.raises(ManifestError, match="change mesh_sha256"):
        write_mesh_manifest(mesh_directory, rewritten)


def test_hashed_mesh_file_overwrite_requires_force(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    write_mesh_manifest(directory, mesh_payload())
    mesh_file = directory / "mesh.msh.h5"
    mesh_file.write_bytes(b"mesh")

    with pytest.raises(ManifestError, match="Refusing to overwrite existing mesh"):
        assert_mesh_file_overwrite_allowed(mesh_file, directory, force=False)

    assert_mesh_file_overwrite_allowed(mesh_file, directory, force=True)


def test_force_mesh_overwrite_refused_when_runs_reference_hash(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    write_mesh_manifest(directory, mesh_payload())
    mesh_file = directory / "mesh.msh.h5"
    mesh_file.write_bytes(b"mesh")
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, run_payload())

    with pytest.raises(ManifestError, match="Refusing --force"):
        assert_mesh_file_overwrite_allowed(mesh_file, directory, force=True)


def test_incomplete_mesh_file_without_hashed_manifest_is_allowed(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    mesh_file = directory / "mesh.msh.h5"
    mesh_file.write_bytes(b"partial")

    assert_mesh_file_overwrite_allowed(mesh_file, directory, force=False)
