"""Manifest-backed path resolution for 07 solver reruns."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.manifest import ManifestError, write_run_manifest
from ro.paths import run_dir


FAMILY = "diamond"
GEO_ID = "D2450_a45"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
RUN_ID = "u0p2_p6M"
GEO_NAME = "D2450_a45_7c_brg110"
CASE_NAME = "u0p2_p6M__mesh_max085_min006_cpg5_bl4"


@pytest.fixture
def rerun07():
    return load_module(
        "batch_solver_rerun_manifest_paths",
        SCRIPTS_DIR / "batch_solver_rerun.py",
    )


def run_payload() -> dict:
    return {
        "schema_version": 1,
        "family": FAMILY,
        "geo_id": GEO_ID,
        "mesh_id": MESH_ID,
        "mesh_sha256": "a" * 64,
        "run_id": RUN_ID,
        "u_mean_ms": 0.2,
        "p_gauge_pa": 6.0e6,
        "u_target_ms": 0.2,
        "inlet_bc_type": "plug",
        "udf_version": "260816_RO_UDF.c",
        "solver_settings": {
            "max_iterations": 2000,
            "residual_target": 1.0e-7,
            "operating_pressure": 101325.0,
        },
        "stop_reason": "max_iter_reached",
        "created_utc": "2026-08-22T00:00:00Z",
    }


def candidate(latest_log_file: str = "") -> dict[str, str]:
    return {
        "selected_index": "1",
        "source_row_number": "2",
        "geo_name": GEO_NAME,
        "case_name": CASE_NAME,
        "convergence_status_before": "MAX_ITER_REACHED",
        "case_status_before": "NEEDS_SOLVER_RERUN",
        "latest_log_file": latest_log_file,
    }


def create_manifested_run(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    write_run_manifest(directory, run_payload())
    log_path = directory / "post" / "logs" / "solver.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("solver transcript", encoding="utf-8")
    return directory, log_path


def test_candidate_reads_ids_from_manifest_and_uses_run_dir(
    rerun07,
    monkeypatch,
    tmp_path,
):
    directory, log_path = create_manifested_run(monkeypatch, tmp_path)
    row = candidate(str(log_path))

    resolved = rerun07.resolve_candidate_run_directory(row)

    assert resolved == directory
    assert row["_run_directory"] == str(directory)
    case_dir, final_case, final_data = rerun07.final_pair_for_candidate(row)
    assert case_dir == directory
    assert final_case == directory / f"{GEO_NAME}_{CASE_NAME}_final.cas.h5"
    assert final_data == directory / f"{GEO_NAME}_{CASE_NAME}_final.dat.h5"


def test_candidate_without_manifest_is_refused(rerun07, monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    log_path = directory / "solver.log"
    log_path.write_text("solver transcript", encoding="utf-8")

    with pytest.raises(ManifestError, match="no manifest.json.*Refusing"):
        rerun07.build_plan_rows([candidate(str(log_path))], object())


def test_candidate_without_log_locator_has_no_name_fallback(
    rerun07,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))

    with pytest.raises(ManifestError, match="no latest_log_file.*fallback"):
        rerun07.resolve_candidate_run_directory(candidate())


def test_candidate_outside_runs_tree_is_refused(
    rerun07,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path / "data"))
    log_path = tmp_path / "legacy-results" / "solver.log"
    log_path.parent.mkdir()
    log_path.write_text("legacy", encoding="utf-8")

    with pytest.raises(ManifestError, match="outside the canonical runs tree"):
        rerun07.resolve_candidate_run_directory(candidate(str(log_path)))


def test_stale_manifest_ids_are_refused(rerun07, monkeypatch, tmp_path):
    directory, log_path = create_manifested_run(monkeypatch, tmp_path)
    manifest_path = directory / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["run_id"] = "u0p3_p8M"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ManifestError, match="ids do not match"):
        rerun07.resolve_candidate_run_directory(candidate(str(log_path)))
