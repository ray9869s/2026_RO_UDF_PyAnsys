"""Current-attempt stop-reason finalize and canonical vs isolated writes."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from helpers import (
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)
from ro.manifest import read_run_manifest, write_mesh_manifest
from ro.paths import mesh_dir, run_dir
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    STOP_REASON_DETERMINATION_FAILED,
    sha256_file,
)
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload


class _FakeFileApi:
    def __init__(self, writes, *, fail=False):
        self.writes = writes
        self.fail = fail

    def write_case_data(self, file_name):
        if self.fail:
            raise OSError("isolated write failed")
        self.writes.append(file_name)
        path = Path(file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cas")
        Path(str(path).replace(".cas.h5", ".dat.h5")).write_bytes(b"dat")


def _fake_solver(writes, *, fail=False):
    return types.SimpleNamespace(
        settings=types.SimpleNamespace(file=_FakeFileApi(writes, fail=fail))
    )


def _prepared_run(monkeypatch, tmp_path, solver, *, parabolic=False):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.family = FAMILY
    cfg.geo_id = GEO_ID
    cfg.mesh_id = MESH_ID
    cfg.run_id = "u0p1_p6M"
    cfg.outlet_gauge_pressure = 6.0e6
    cfg.use_inlet_velocity_profile = parabolic
    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    mesh_directory.mkdir(parents=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    run_directory = run_dir(cfg.family, cfg.geo_id, cfg.mesh_id, cfg.run_id)
    run_directory.mkdir(parents=True)
    solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)
    return cfg, run_directory


def test_determination_failed_finalizes_without_g_and_skips_canonical(
    monkeypatch, tmp_path
):
    solver = load_solver_code("r02_determination_failed")
    cfg, run_directory = _prepared_run(
        monkeypatch, tmp_path, solver, parabolic=True
    )
    writes = []
    canonical = run_directory / f"{cfg.geo_id}_{cfg.run_id}_final.cas.h5"
    with pytest.raises(RuntimeError, match="stop_reason_determination_failed"):
        solver.publish_stop_reason_and_finals(
            _fake_solver(writes),
            run_directory,
            None,
            inlet_profile_g=None,
            final_case_file=str(canonical),
            as_fluent_path=str,
        )
    payload = read_run_manifest(run_directory)
    assert payload["stop_reason"] == STOP_REASON_DETERMINATION_FAILED
    assert payload["u_mean_ms"] is None
    assert FINAL_CASE_SHA256_FIELD not in payload
    assert writes
    assert all("_final.cas.h5" not in name for name in writes)
    assert any("failed_attempt_" in name for name in writes)
    assert not canonical.is_file()


def test_isolated_write_failure_does_not_mask_determination_failed(
    monkeypatch, tmp_path
):
    solver = load_solver_code("r02_isolate_fails")
    cfg, run_directory = _prepared_run(monkeypatch, tmp_path, solver)
    canonical = run_directory / f"{cfg.geo_id}_{cfg.run_id}_final.cas.h5"
    with pytest.raises(RuntimeError, match="stop_reason_determination_failed"):
        solver.publish_stop_reason_and_finals(
            _fake_solver([], fail=True),
            run_directory,
            None,
            inlet_profile_g=None,
            final_case_file=str(canonical),
            as_fluent_path=str,
        )
    assert read_run_manifest(run_directory)["stop_reason"] == (
        STOP_REASON_DETERMINATION_FAILED
    )
    assert not canonical.is_file()


def test_successful_reason_writes_canonical_and_stamps_hashes(
    monkeypatch, tmp_path
):
    solver = load_solver_code("r02_success_stamp")
    cfg, run_directory = _prepared_run(monkeypatch, tmp_path, solver)
    writes = []
    canonical = run_directory / f"{cfg.geo_id}_{cfg.run_id}_final.cas.h5"
    solver.publish_stop_reason_and_finals(
        _fake_solver(writes),
        run_directory,
        "residual_converged",
        inlet_profile_g=None,
        final_case_file=str(canonical),
        as_fluent_path=str,
    )
    data = Path(str(canonical).replace(".cas.h5", ".dat.h5"))
    assert canonical.is_file()
    assert data.is_file()
    assert writes == [str(canonical)]
    solver.stamp_run_manifest_final_artifact_hashes(
        run_directory, canonical, data
    )
    payload = read_run_manifest(run_directory)
    assert payload["stop_reason"] == "residual_converged"
    assert payload[FINAL_CASE_SHA256_FIELD] == sha256_file(canonical)
    assert payload[FINAL_DATA_SHA256_FIELD] == sha256_file(data)


def test_new_attempt_rewrites_manifest_without_final_hashes(
    monkeypatch, tmp_path
):
    solver = load_solver_code("r02_new_attempt")
    cfg, run_directory = _prepared_run(monkeypatch, tmp_path, solver)
    canonical = run_directory / f"{cfg.geo_id}_{cfg.run_id}_final.cas.h5"
    solver.publish_stop_reason_and_finals(
        _fake_solver([]),
        run_directory,
        "max_iter_reached",
        inlet_profile_g=None,
        final_case_file=str(canonical),
        as_fluent_path=str,
    )
    data = Path(str(canonical).replace(".cas.h5", ".dat.h5"))
    solver.stamp_run_manifest_final_artifact_hashes(
        run_directory, canonical, data
    )
    first = read_run_manifest(run_directory)
    old_attempt = first[SOLVER_ATTEMPT_ID_FIELD]
    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)
    second = read_run_manifest(run_directory)
    assert second["stop_reason"] == "RUNNING"
    assert second[SOLVER_ATTEMPT_ID_FIELD] != old_attempt
    assert FINAL_CASE_SHA256_FIELD not in second
    assert FINAL_DATA_SHA256_FIELD not in second
    assert canonical.is_file()
