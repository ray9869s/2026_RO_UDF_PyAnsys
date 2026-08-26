"""Migration and D2450_a45 regression tests for schema v2."""

from __future__ import annotations

import copy
import json

import pytest

from ro.campaign_geometry import (
    merge_geometry_into_mesh_manifest,
    merge_geometry_into_run_manifest,
)
from ro.manifest import (
    MANIFEST_SCHEMA_VERSION,
    read_mesh_manifest,
    read_run_manifest,
    upgrade_mesh_manifest_in_place,
    upgrade_run_manifest_in_place,
)
from ro.paths import mesh_dir, run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, mesh_payload, run_payload


def _legacy_mesh_payload_v1() -> dict:
    payload = mesh_payload()
    legacy = {
        key: payload[key]
        for key in payload
        if key
        not in {
            "unit_cell_xy_m",
            "Sigma_d_nominal_m",
            "membrane_trim_m",
            "membrane_contact_width_m",
            "membrane_blocked_area_frac",
            "porosity_eps",
            "periodic_shift_y_m",
            "periodic_shift_y_source",
            "layer_angles_deg",
            "layer_diameters_m",
            "layer_axis_z_m",
            "joint_sphere_z_m",
            "joint_sphere_R_m",
            "joint_sphere_R_ratio",
            "joint_sphere_r_min_m",
            "joint_sphere_count",
            "curvature_margin",
            "spacer_wall_zones",
        }
    }
    legacy["schema_version"] = 1
    return legacy


def _legacy_run_payload_v1() -> dict:
    payload = run_payload()
    skip = {
        "unit_cell_xy_m",
        "Sigma_d_nominal_m",
        "membrane_trim_m",
        "membrane_contact_width_m",
        "membrane_blocked_area_frac",
        "porosity_eps",
        "periodic_shift_y_m",
        "periodic_shift_y_source",
        "layer_angles_deg",
        "joint_sphere_R_m",
        "joint_sphere_R_ratio",
        "joint_sphere_r_min_m",
        "joint_sphere_count",
        "curvature_margin",
        "spacer_wall_zones",
        "u_mean_source_mesh_id",
        "needs_lead_recheck",
    }
    legacy = {key: payload[key] for key in payload if key not in skip}
    legacy["schema_version"] = 1
    return legacy


def test_migration_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    legacy = _legacy_mesh_payload_v1()
    (directory / "manifest.json").write_text(json.dumps(legacy), encoding="utf-8")

    migrated = merge_geometry_into_mesh_manifest(legacy, GEO_ID)
    migrated["schema_version"] = MANIFEST_SCHEMA_VERSION
    upgrade_mesh_manifest_in_place(directory, migrated)
    first = read_mesh_manifest(directory)

    upgrade_mesh_manifest_in_place(directory, copy.deepcopy(first))
    second = read_mesh_manifest(directory)
    assert second == first


def test_d2450_a45_run_output_fields_unchanged_by_migration(monkeypatch, tmp_path):
    """Run-relevant scalars must be bit-identical after v1→v2 migration."""
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)

    legacy_mesh = _legacy_mesh_payload_v1()
    legacy_mesh["inlet_profile_G"] = 1.00360939613
    legacy_run = _legacy_run_payload_v1()
    legacy_run["u_mean_ms"] = 0.199281
    legacy_run["u_target_ms"] = 0.2
    legacy_run["p_gauge_pa"] = 6.0e6
    legacy_run["inlet_bc_type"] = "parabolic"

    mesh_migrated = merge_geometry_into_mesh_manifest(legacy_mesh, GEO_ID)
    mesh_migrated["schema_version"] = MANIFEST_SCHEMA_VERSION
    run_migrated = merge_geometry_into_run_manifest(legacy_run, GEO_ID, mesh_id=MESH_ID)
    run_migrated["schema_version"] = MANIFEST_SCHEMA_VERSION

    upgrade_mesh_manifest_in_place(mesh_directory, mesh_migrated)
    upgrade_run_manifest_in_place(run_directory, run_migrated)

    mesh_after = read_mesh_manifest(mesh_directory)
    run_after = read_run_manifest(run_directory)

    assert mesh_after["inlet_profile_G"] == legacy_mesh["inlet_profile_G"]
    assert run_after["u_mean_ms"] == legacy_run["u_mean_ms"]
    assert run_after["u_target_ms"] == legacy_run["u_target_ms"]
    assert run_after["p_gauge_pa"] == legacy_run["p_gauge_pa"]
    assert run_after["inlet_bc_type"] == legacy_run["inlet_bc_type"]
    assert run_after["mesh_sha256"] == legacy_run["mesh_sha256"]
    assert run_after["membrane_blocked_area_frac"] == 0.0
