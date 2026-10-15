"""Discovery retry and infrastructure leaf archive. Does not launch Discovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.discovery_session import (
    BACKEND_START_TIMEOUT_S,
    DiscoveryAttemptError,
    discovery_launch_arguments,
    run_discovery_job,
)
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_adapter import (
    FAILURE_GEOMETRY,
    FAILURE_INFRASTRUCTURE,
    PillarDesign,
    archive_infrastructure_leaves,
    evaluate,
    summarize_mfbo_leaves,
)
from ro.mfbo_drivers import DriverFailed, make_drivers
from ro.solver_common import sha256_file


class _ConnectionError(Exception):
    pass


def _lister(script):
    def lister():
        script.append(len(script))
        if len(script) == 1:
            return [{"pid": 10, "line": "discovery already running"}]
        return [
            {"pid": 10, "line": "discovery already running"},
            {"pid": 99, "line": "Discovery.exe new"},
        ]

    return lister


def test_launch_arguments_wait_300_seconds():
    arguments = discovery_launch_arguments("v251")
    assert arguments["timeout"] == 300
    assert arguments["timeout"] == BACKEND_START_TIMEOUT_S
    assert arguments["hidden"] is True
    assert arguments["version"] == 251


def test_backend_timeout_is_retried_once_and_only_new_pids_are_killed():
    launches = []
    killed = []

    def launch():
        launches.append("launch")
        if len(launches) == 1:
            raise _ConnectionError("Timeout while waiting for backend to be ready")
        return SimpleNamespace(value=1, close=lambda: None)

    def build(modeler):
        return {"built": modeler.value}

    seen = []
    record = run_discovery_job(
        build,
        launch=launch,
        process_lister=_lister(seen),
        process_killer=killed.append,
    )
    assert len(launches) == 2
    assert killed == [99]
    assert record["attempts"] == 2
    assert record["terminated_pids"] == [99]
    assert record["backend_start_timeout_s"] == 300
    assert record["built"] == 1
    assert "discovery already running" in record["processes_before"][0]


def test_boolean_failure_is_not_retried(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launches = []

    def _bind():
        pillar_cad.ApiVersions = SimpleNamespace(V_251="v251")

    def _launch(**kwargs):
        launches.append(kwargs)
        raise RuntimeError("boolean failed")

    monkeypatch.setattr(pillar_cad, "_bind_geometry_symbols", _bind)
    monkeypatch.setattr(
        pillar_cad, "launch_modeler_with_discovery", _launch, raising=False
    )
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4)
    with pytest.raises(RuntimeError, match="boolean failed"):
        pillar_cad.generate_pillar_cad(
            d_p_mm=0.8,
            d_h_mm=0.15,
            d_f_mm=0.4,
            geo_id=geo_id,
            out_dir=tmp_path,
            process_lister=lambda: [],
            process_killer=lambda pid: None,
        )
    assert len(launches) == 1
    assert launches[0]["timeout"] == 300
    error = json.loads((tmp_path / f"{geo_id}_error.json").read_text(encoding="utf-8"))
    assert error["failure_class"] == "geometry"
    assert error["attempts"] == 1
    assert error["backend_start_timeout_s"] == 300
    assert not (tmp_path / f"{geo_id}.pmdb").exists()


def test_pillar_backend_timeout_retries_and_records_the_error(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launches = []

    def _bind():
        pillar_cad.ApiVersions = SimpleNamespace(V_251="v251")

    def _launch(**kwargs):
        launches.append(kwargs)
        raise _ConnectionError("Timeout while waiting for backend to be ready")

    monkeypatch.setattr(pillar_cad, "_bind_geometry_symbols", _bind)
    monkeypatch.setattr(
        pillar_cad, "launch_modeler_with_discovery", _launch, raising=False
    )
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.0, 0.4)
    killed = []
    with pytest.raises(DiscoveryAttemptError, match="backend to be ready"):
        pillar_cad.generate_pillar_cad(
            d_p_mm=0.8,
            d_h_mm=0.0,
            d_f_mm=0.4,
            geo_id=geo_id,
            out_dir=tmp_path,
            process_lister=_lister([]),
            process_killer=killed.append,
        )
    assert len(launches) == 2
    assert killed == [99]
    error = json.loads((tmp_path / f"{geo_id}_error.json").read_text(encoding="utf-8"))
    assert error["failure_class"] == "infrastructure"
    assert error["attempts"] == 2
    assert error["terminated_pids"] == [99]


def test_diamond_backend_timeout_uses_the_same_launch(tmp_path, monkeypatch):
    import ro.diamond_cad as diamond_cad

    launches = []

    def _bind():
        diamond_cad.ApiVersions = SimpleNamespace(V_251="v251")

    def _launch(**kwargs):
        launches.append(kwargs)
        raise RuntimeError("boolean failed")

    monkeypatch.setattr(diamond_cad, "_bind_geometry_symbols", _bind)
    monkeypatch.setattr(
        diamond_cad, "launch_modeler_with_discovery", _launch, raising=False
    )
    with pytest.raises(RuntimeError, match="boolean failed"):
        diamond_cad.generate_diamond_cad(
            geo_id="D2450_a45",
            out_dir=tmp_path,
            process_lister=lambda: [],
            process_killer=lambda pid: None,
        )
    assert len(launches) == 1
    assert launches[0]["timeout"] == 300
    error = json.loads((tmp_path / "D2450_a45_error.json").read_text(encoding="utf-8"))
    assert error["failure_class"] == "geometry"


def test_geometry_class_stays_failed_and_empty_mesh_is_retried(tmp_path):
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.0, 0.4)
    design = PillarDesign(d_p_mm=0.8, d_h_mm=0.0, d_f_mm=0.4)
    geo_dir = tmp_path / "geometries" / "pillar" / geo_id
    geo_dir.mkdir(parents=True)
    (geo_dir / f"{geo_id}_error.json").write_text(
        json.dumps({"geo_id": geo_id, "failure_class": "geometry", "error": "boolean"}),
        encoding="utf-8",
    )
    calls = []

    class _Drivers:
        def generate(self, **kwargs):
            calls.append("generate")

        def mesh(self, **kwargs):
            calls.append("mesh")

        def solve(self, **kwargs):
            calls.append("solve")

        def extract(self, **kwargs):
            calls.append("extract")

    table = {
        "LF": {
            "m_max": 0.085,
            "m_min": 0.006,
            "m_cpg": 5,
            "bl_layers": 4,
            "peel_layers": 2,
        }
    }
    record = evaluate(
        design,
        "LF",
        run_id="u0p2_p6M",
        data_root=tmp_path,
        fidelity_table=table,
        drivers=_Drivers(),
    )
    assert calls == []
    assert record["failure_class"] == FAILURE_GEOMETRY
    assert record["status"] == "execution_failed"
    assert geo_dir.is_dir()

    mesh_dir = tmp_path / "meshes" / "pillar" / geo_id / "max085_min006_cpg5_bl4_peel2"
    mesh_dir.mkdir(parents=True)
    # Replace the geometry failure with a success so the mesh leaf is reached.
    pmdb = geo_dir / f"{geo_id}.pmdb"
    pmdb.write_bytes(b"pmdb")
    (geo_dir / f"{geo_id}_error.json").unlink()
    (geo_dir / f"{geo_id}_meta.json").write_text(
        json.dumps(
            {
                "geo_id": geo_id,
                "inputs": {
                    "d_p_mm": 0.8,
                    "d_h_mm": 0.0,
                    "d_f_mm": 0.4,
                    "geo_id": geo_id,
                },
                "pmdb_sha256": sha256_file(pmdb),
            }
        ),
        encoding="utf-8",
    )

    class _MeshDrivers(_Drivers):
        def mesh(self, **kwargs):
            calls.append("mesh")
            mesh = Path(kwargs["mesh_dir"])
            mesh.mkdir(parents=True, exist_ok=True)
            (mesh / "mesh_run_record.json").write_text(
                json.dumps({"status": "SUCCESS", "wall_time_seconds": 1}),
                encoding="utf-8",
            )
            (mesh / "manifest.json").write_text(
                json.dumps(
                    {
                        "cell_count": 1,
                        "n_active_cells": 7,
                        "cell_length_x_m": 0.003465,
                        "periodic_shift_y_m": 0.003465,
                    }
                ),
                encoding="utf-8",
            )

        def solve(self, **kwargs):
            calls.append("solve")
            run = Path(kwargs["run_dir"])
            run.mkdir(parents=True, exist_ok=True)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "stop_reason": "residual_converged",
                        "convergence_quality": "PASS",
                        "solver_wall_time_s": 1,
                        "extraction_wall_time_s": 1,
                    }
                ),
                encoding="utf-8",
            )

        def extract(self, **kwargs):
            calls.append("extract")
            path = Path(kwargs["run_dir"]) / "post" / "reports" / "summary_metrics_wide.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "lmh_mass_balance,lmh_window_module,area_mem,"
                "pressure_drop_spacer_per_m,cpc_window_avg_flux,"
                "cp_q999_window_flux,cp_canon_window_avg\n"
                "25,18,1e-5,30000,1.08,1.2,1.05\n",
                encoding="utf-8",
            )

    calls.clear()
    record = evaluate(
        design,
        "LF",
        run_id="u0p2_p6M",
        data_root=tmp_path,
        fidelity_table=table,
        drivers=_MeshDrivers(),
    )
    assert calls[0] == "mesh"
    assert record["status"] == "valid"
    assert "mesh_failed_" in Path(record["archived_leaves"][0]).name


def test_archive_script_moves_infrastructure_and_leaves_success(tmp_path):
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    success = tmp_path / "geometries" / "pillar" / geo_id
    success.mkdir(parents=True)
    pmdb = success / f"{geo_id}.pmdb"
    pmdb.write_bytes(b"ok")
    (success / f"{geo_id}_meta.json").write_text(
        json.dumps({"geo_id": geo_id, "pmdb_sha256": sha256_file(pmdb)}),
        encoding="utf-8",
    )
    empty_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=3)
    empty = tmp_path / "geometries" / "pillar" / empty_id
    empty.mkdir(parents=True)
    failed_mesh = (
        tmp_path / "meshes" / "pillar" / geo_id / "max085_min006_cpg5_bl4_peel2"
    )
    failed_mesh.mkdir(parents=True)
    (failed_mesh / "mesh_run_record.json").write_text(
        json.dumps({"status": "FAILED"}),
        encoding="utf-8",
    )
    (failed_mesh / "manifest.json").write_text("{}", encoding="utf-8")
    campaign = tmp_path / "geometries" / "pillar" / "P_p100_h30"
    campaign.mkdir()

    moment = datetime(2026, 10, 15, 3, 12, tzinfo=timezone.utc)
    script = load_module(
        "archive_failed_leaves_under_test",
        SCRIPTS_DIR / "mfbo" / "archive_failed_leaves.py",
    )
    listed = summarize_mfbo_leaves(tmp_path)
    assert listed["success_count"] == 1
    assert {row["class"] for row in listed["failed"]} == {
        FAILURE_INFRASTRUCTURE,
        "mesh",
    }
    code = script.main(["--data-root", str(tmp_path), "--list-only"])
    assert code == 0
    assert empty.is_dir()
    archived = archive_infrastructure_leaves(tmp_path, now=moment)
    assert len(archived) == 1
    assert not empty.exists()
    destination = Path(archived[0]["archived"])
    assert destination.name == f"{empty_id}_geometry_failed_20261015T031200Z"
    assert success.is_dir()
    assert failed_mesh.is_dir()
    assert campaign.is_dir()
    assert (failed_mesh / "mesh_run_record.json").is_file()


def test_driver_reads_the_infrastructure_class(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        geo_id = "MFP_d0800_h0000_f0400"
        error = tmp_path / "geo" / f"{geo_id}_error.json"
        error.parent.mkdir(parents=True, exist_ok=True)
        error.write_text(
            json.dumps({"failure_class": "infrastructure", "error": "timeout"}),
            encoding="utf-8",
        )
        kwargs["stdout"].write(b"ConnectionError: Timeout while waiting for backend\n")
        return SimpleNamespace(returncode=1)

    drivers = make_drivers(tmp_path, runner=runner)
    with pytest.raises(DriverFailed, match="exited 1") as caught:
        drivers.generate(
            design=PillarDesign(0.8, 0.0, 0.4),
            geo_id="MFP_d0800_h0000_f0400",
            out_dir=tmp_path / "geo",
        )
    assert caught.value.failure_class == FAILURE_INFRASTRUCTURE
