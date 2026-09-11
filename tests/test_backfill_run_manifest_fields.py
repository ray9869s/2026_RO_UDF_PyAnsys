"""Tests for run manifest field backfill."""

from __future__ import annotations

import copy
import json

import pytest

from ro.manifest import ManifestError, RUN_MANIFEST_REQUIRED_FIELDS, read_run_manifest
from ro.paths import run_dir

from helpers import load_module

backfill = load_module(
    "backfill_run_manifest_fields_under_test",
    "scripts/backfill_run_manifest_fields.py",
)

FAMILY = "diamond"
GEO_ID = "D2450_a45"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
RUN_ID = "u0p2_p6M"


def _full_run_payload() -> dict:
    from tests.test_manifest import run_payload

    return run_payload()


def test_backfill_adds_missing_geometry_field_only(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = _full_run_payload()
    del payload["membrane_blocked_area_frac_geometric"]
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    additions = backfill.backfill_run_manifest(directory, apply=False)

    assert additions == {"membrane_blocked_area_frac_geometric": None}


def test_backfill_refuses_to_overwrite_existing_field(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = _full_run_payload()
    payload["membrane_blocked_area_frac_geometric"] = 0.123
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    additions = backfill.backfill_run_manifest(directory, apply=False)

    assert additions == {}


def test_backfill_apply_writes_valid_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = _full_run_payload()
    del payload["membrane_blocked_area_frac_geometric"]
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    backfill.backfill_run_manifest(directory, apply=True)

    read_back = read_run_manifest(directory)
    assert read_back["membrane_blocked_area_frac_geometric"] is None


def test_backfill_ref_empty_adds_missing_geometry_field(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir("empty", "REF_empty", MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    base = {
        "schema_version": 1,
        "family": "empty",
        "geo_id": "REF_empty",
        "mesh_id": MESH_ID,
        "mesh_sha256": "a" * 64,
        "run_id": RUN_ID,
        "u_mean_ms": 0.199281,
        "p_gauge_pa": 6.0e6,
        "u_target_ms": 0.2,
        "inlet_bc_type": "parabolic",
        "udf_version": "260822_RO_UDF.c",
        "analytic_cwall": 1,
        "solver_settings": {
            "max_iterations": 2000,
            "residual_target": 1.0e-7,
            "operating_pressure": 101325.0,
        },
        "stop_reason": "residual_converged",
        "created_utc": "2026-09-08T00:00:00Z",
    }
    from ro.campaign_geometry import merge_geometry_into_run_manifest

    payload = merge_geometry_into_run_manifest(
        base,
        "REF_empty",
        mesh_id=MESH_ID,
    )
    payload["schema_version"] = 2
    del payload["membrane_blocked_area_frac_geometric"]
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    additions = backfill.backfill_run_manifest(directory, apply=False)

    assert additions == {"membrane_blocked_area_frac_geometric": None}


def test_backfill_aborts_on_missing_solver_field(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = _full_run_payload()
    del payload["u_mean_ms"]
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="preserved"):
        backfill.backfill_run_manifest(directory, apply=False)
