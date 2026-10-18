"""Shared Discovery session, batch CAD, and the geometry worker. No Discovery."""

from __future__ import annotations

import json
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.geometry_worker import (
    _install_stop_signals,
    _recover_inflight,
    serve,
    submit_generate,
)
from ro.mfbo_adapter import PillarDesign
from ro.mfbo_drivers import make_drivers
from ro.mfbo_fidelity_screen import load_designs, load_fidelity_table, run_from_leaves
from ro.pillar_cad import _BASE_LABELS, _HOLE_LABEL, generate_pillar_batch, generate_pillar_cad

DESIGN_A = (0.8, 0.0, 0.4)
DESIGN_B = (0.8, 0.15, 0.4)
DESIGN_C = (1.0, 0.0, 0.4)
TABLE = REPO_ROOT / "configs" / "mfbo_fidelity_table.3d_v1.json"
DESIGNS = REPO_ROOT / "configs" / "mfbo_3d_screen_designs.json"


class _Design:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class _Modeler:
    def __init__(self):
        self.designs = []
        self.closed = False
        self.client = SimpleNamespace(backend_version="test-backend")

    def create_design(self, name):
        design = _Design(name)
        self.designs.append(design)
        return design

    def close(self):
        self.closed = True


def _counts():
    return {label: 1 for label in (*_BASE_LABELS, _HOLE_LABEL)}


def _entry(directory: Path, diameters, n_active=7):
    d_p, d_h, d_f = diameters
    geo_id = format_mfbo_pillar_geo_id(d_p, d_h, d_f, n_active=n_active)
    return {
        "d_p_mm": d_p,
        "d_h_mm": d_h,
        "d_f_mm": d_f,
        "geo_id": geo_id,
        "out_dir": str(directory / geo_id),
        "n_active": n_active,
    }


def _bind(pillar_cad):
    pillar_cad.ApiVersions = SimpleNamespace(V_251="v251")


def _processes():
    """Pre-existing pid 10 stays. Pids this session starts are killed on drop."""
    born = []
    killed = []
    modelers = []

    def launch():
        modeler = _Modeler()
        modelers.append(modeler)
        born.append({"pid": 100 + len(modelers), "line": "Discovery.exe"})
        modeler.pid = born[-1]["pid"]
        return modeler

    def lister():
        return [{"pid": 10, "line": "discovery already running"}, *born]

    def killer(pid):
        killed.append(pid)
        born[:] = [item for item in born if item["pid"] != pid]

    return launch, lister, killer, killed, modelers


def _install(monkeypatch, pillar_cad, launch, fill):
    monkeypatch.setattr(pillar_cad, "_bind_geometry_symbols", lambda: _bind(pillar_cad))
    monkeypatch.setattr(pillar_cad, "_fill_and_export", fill)
    monkeypatch.setattr(pillar_cad, "launch_modeler_with_discovery", launch, raising=False)


def _fill_ok(design, modeler, spec):
    spec.paths["pmdb"].write_bytes(b"pmdb-" + spec.geo_id.encode())
    spec.paths["scdocx"].write_bytes(b"scdocx-" + spec.geo_id.encode())
    return {
        "geo_id": spec.geo_id,
        "inputs": {"geo_id": spec.geo_id},
        "_counts": _counts(),
    }


def test_batch_reuses_one_session_and_closes_each_design(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launch, lister, killer, killed, modelers = _processes()
    _install(monkeypatch, pillar_cad, launch, _fill_ok)
    entries = [_entry(tmp_path, DESIGN_A), _entry(tmp_path, DESIGN_B, n_active=3)]
    results = generate_pillar_batch(
        entries,
        launch=launch,
        process_lister=lister,
        process_killer=killer,
    )
    assert [item["status"] for item in results] == ["ok", "ok"]
    assert results[0]["session_reused"] is False
    assert results[1]["session_reused"] is True
    assert len(modelers) == 1
    assert all(design.closed for design in modelers[0].designs)
    assert modelers[0].closed
    assert killed == [modelers[0].pid]
    assert 10 not in killed
    assert results[0]["paths"]["pmdb"].endswith(entries[0]["geo_id"] + ".pmdb")
    first = json.loads(
        Path(entries[0]["out_dir"], entries[0]["geo_id"] + "_generate_wall_time.json").read_text(
            encoding="utf-8"
        )
    )
    second = json.loads(
        Path(entries[1]["out_dir"], entries[1]["geo_id"] + "_generate_wall_time.json").read_text(
            encoding="utf-8"
        )
    )
    assert first["session_reused"] is False
    assert second["session_reused"] is True
    assert first["generate_wall_time_s"] >= 0.0
    modelers_closed = killed  # placeholder to keep the name used below
    del modelers_closed


def test_batch_files_match_the_single_geometry_path(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    def lister():
        return []

    def launch(**_kwargs):
        return _Modeler()

    _install(monkeypatch, pillar_cad, launch, _fill_ok)
    single_dir = tmp_path / "single"
    batch_dir = tmp_path / "batch"
    entry = _entry(single_dir, DESIGN_A)
    batch_entry = _entry(batch_dir, DESIGN_A)
    generate_pillar_cad(
        d_p_mm=entry["d_p_mm"],
        d_h_mm=entry["d_h_mm"],
        d_f_mm=entry["d_f_mm"],
        geo_id=entry["geo_id"],
        out_dir=entry["out_dir"],
        n_active=7,
        process_lister=lister,
        process_killer=lambda pid: None,
    )
    results = generate_pillar_batch(
        [batch_entry],
        launch=launch,
        process_lister=lister,
        process_killer=lambda pid: None,
    )
    assert results[0]["status"] == "ok"
    for suffix in (".pmdb", ".scdocx", "_meta.json"):
        single = Path(entry["out_dir"], entry["geo_id"] + suffix).read_bytes()
        batched = Path(batch_entry["out_dir"], batch_entry["geo_id"] + suffix).read_bytes()
        assert single == batched


def test_a_failing_entry_is_recorded_and_the_loop_continues(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launches = []

    def launch():
        launches.append(_Modeler())
        return launches[-1]

    def fill(design, modeler, spec):
        if spec.geo_id == format_mfbo_pillar_geo_id(*DESIGN_B):
            raise RuntimeError("boolean failed")
        return _fill_ok(design, modeler, spec)

    _install(monkeypatch, pillar_cad, launch, fill)
    entries = [
        _entry(tmp_path, DESIGN_A),
        _entry(tmp_path, DESIGN_B),
        _entry(tmp_path, DESIGN_C),
    ]
    results = generate_pillar_batch(
        entries,
        launch=launch,
        process_lister=lambda: [],
        process_killer=lambda pid: None,
    )
    assert [item["status"] for item in results] == ["ok", "failed", "ok"]
    assert results[1]["failure_class"] == "geometry"
    assert results[1]["session_reused"] is True
    assert results[2]["session_reused"] is True
    assert len(launches) == 1
    assert all(design.closed for design in launches[0].designs)
    error = json.loads(
        (Path(entries[1]["out_dir"]) / f"{entries[1]['geo_id']}_error.json").read_text(
            encoding="utf-8"
        )
    )
    assert error["failure_class"] == "geometry"
    assert "boolean failed" in error["error"]
    assert (Path(entries[2]["out_dir"]) / f"{entries[2]['geo_id']}.pmdb").is_file()


def test_a_dying_session_is_restarted_and_only_new_pids_are_killed(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launch, lister, killer, killed, modelers = _processes()
    calls = {"n": 0}
    dying = format_mfbo_pillar_geo_id(*DESIGN_B)

    def fill(design, modeler, spec):
        spec.paths["pmdb"].write_bytes(b"partial")
        if spec.geo_id == dying and calls["n"] == 0:
            calls["n"] = 1
            raise ConnectionError("Timeout while waiting for backend to be ready")
        return _fill_ok(design, modeler, spec)

    _install(monkeypatch, pillar_cad, launch, fill)
    entries = [
        _entry(tmp_path, DESIGN_A),
        _entry(tmp_path, DESIGN_B),
        _entry(tmp_path, DESIGN_C),
    ]
    results = generate_pillar_batch(
        entries,
        launch=launch,
        process_lister=lister,
        process_killer=killer,
    )
    assert [item["status"] for item in results] == ["ok", "ok", "ok"]
    assert results[0]["session_reused"] is False
    assert results[1]["session_reused"] is False
    assert results[2]["session_reused"] is True
    assert len(modelers) == 2
    assert killed == [modeler.pid for modeler in modelers]
    assert 10 not in killed
    assert Path(entries[1]["out_dir"], entries[1]["geo_id"] + ".pmdb").read_bytes().startswith(b"pmdb-")


def test_infrastructure_failure_is_recorded_and_the_next_entry_runs(tmp_path, monkeypatch):
    import ro.pillar_cad as pillar_cad

    launches = []

    def launch():
        launches.append("launch")
        return _Modeler()

    first = format_mfbo_pillar_geo_id(*DESIGN_A)

    def fill(design, modeler, spec):
        if spec.geo_id == first:
            raise ConnectionError("Timeout while waiting for backend to be ready")
        return _fill_ok(design, modeler, spec)

    _install(monkeypatch, pillar_cad, launch, fill)
    entries = [_entry(tmp_path, DESIGN_A), _entry(tmp_path, DESIGN_B)]
    results = generate_pillar_batch(
        entries,
        launch=launch,
        process_lister=lambda: [],
        process_killer=lambda pid: None,
    )
    assert results[0]["status"] == "failed"
    assert results[0]["failure_class"] == "infrastructure"
    assert results[1]["status"] == "ok"
    assert results[1]["session_reused"] is False
    assert len(launches) == 3
    error = json.loads(
        (Path(entries[0]["out_dir"]) / f"{entries[0]['geo_id']}_error.json").read_text(
            encoding="utf-8"
        )
    )
    assert error["failure_class"] == "infrastructure"
    assert error["attempts"] == 2


def _wait_ready(directory: Path) -> None:
    for _ in range(200):
        path = directory / "health.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") == "ready":
                return
        time.sleep(0.01)
    raise AssertionError(f"worker did not become ready in {directory}")


def test_worker_request_response_shutdown_and_stop_file(tmp_path):
    closed = []
    seen = []

    def handler(request):
        seen.append(request["geo_id"])
        return {
            "status": "ok",
            "geo_id": request["geo_id"],
            "session_reused": len(seen) > 1,
        }

    worker = tmp_path / "worker"
    stop = {"flag": False}
    thread = threading.Thread(
        target=serve,
        kwargs={
            "directory": worker,
            "handler": handler,
            "session_open": lambda: bool(seen),
            "sleep_s": 0.01,
            "stop": stop,
            "on_stop": lambda: closed.append("closed"),
        },
        daemon=True,
    )
    thread.start()
    _wait_ready(worker)
    first = submit_generate(worker, {"geo_id": "MFP_d0800_h0000_f0400"}, poll_s=0.01)
    assert first["status"] == "ok"
    assert first["session_reused"] is False
    assert seen == ["MFP_d0800_h0000_f0400"]
    (worker / "shutdown").write_text("", encoding="utf-8")
    thread.join(2)
    assert not thread.is_alive()
    assert closed == ["closed"]
    health = json.loads((worker / "health.json").read_text(encoding="utf-8"))
    assert health["status"] == "stopped"

    other = tmp_path / "stop-file"
    stop_file = tmp_path / "queue.stop"
    again = []
    thread = threading.Thread(
        target=serve,
        kwargs={
            "directory": other,
            "handler": lambda request: again.append(request) or {"status": "ok"},
            "session_open": lambda: False,
            "sleep_s": 0.01,
            "stop_file": stop_file,
            "on_stop": lambda: again.append("stopped"),
        },
        daemon=True,
    )
    thread.start()
    _wait_ready(other)
    stop_file.write_text("", encoding="utf-8")
    thread.join(2)
    assert not thread.is_alive()
    assert "stopped" in again
    assert all(item == "stopped" for item in again)


def test_keep_alive_logs_a_dead_session_and_health_says_closed(tmp_path):
    worker = tmp_path / "geometry_worker"
    state = {"open": True}

    def keepalive():
        state["open"] = False
        return False

    def reason():
        return (
            "session dropped: TimeoutError: Timeout while waiting for backend to be ready"
            "; discovery_exit_code=1"
        )

    stop = {"flag": False}
    thread = threading.Thread(
        target=serve,
        kwargs={
            "directory": worker,
            "handler": lambda request: {"status": "ok"},
            "session_open": lambda: state["open"],
            "session_keepalive": keepalive,
            "session_reason": reason,
            "keepalive_s": 0,
            "sleep_s": 0.01,
            "stop": stop,
        },
        daemon=True,
    )
    thread.start()
    _wait_ready(worker)
    health = {}
    for _ in range(100):
        health = json.loads((worker / "health.json").read_text(encoding="utf-8"))
        if health.get("session_open") is False:
            break
        time.sleep(0.01)
    stop["flag"] = True
    thread.join(2)
    assert health["session_open"] is False
    log = (worker / "worker.log").read_text(encoding="utf-8")
    assert "discovery_exit_code=1" in log
    assert "session_open=false" in log


def test_sigint_sets_the_stop_flag_the_worker_watches():
    stop = {"flag": False}
    installed = _install_stop_signals(stop)
    assert installed
    try:
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert stop["flag"] is True
    finally:
        for sig, previous in installed:
            signal.signal(sig, previous)


def test_submit_restarts_a_dead_worker_once(tmp_path):
    class _Supervisor:
        def __init__(self):
            self.restarts = 0
            self._alive = False

        def healthy(self):
            return self._alive

        def restart(self):
            self.restarts += 1
            self._alive = True
            for folder in ("requests", "inflight"):
                for path in (tmp_path / folder).glob("*.json"):
                    (tmp_path / "responses" / path.name).parent.mkdir(parents=True, exist_ok=True)
                    (tmp_path / "responses" / path.name).write_text(
                        json.dumps({"status": "ok", "session_reused": False}) + "\n",
                        encoding="utf-8",
                    )

    supervisor = _Supervisor()
    response = submit_generate(
        tmp_path,
        {"geo_id": "MFP_d0800_h0000_f0400"},
        supervisor=supervisor,
        poll_s=0.01,
    )
    assert response["status"] == "ok"
    assert supervisor.restarts == 1

    class _Dead:
        def __init__(self):
            self.restarts = 0

        def healthy(self):
            return False

        def restart(self):
            self.restarts += 1

    dead = _Dead()
    with pytest.raises(RuntimeError, match="died"):
        submit_generate(
            tmp_path / "dead",
            {"geo_id": "MFP_d0800_h0000_f0400"},
            supervisor=dead,
            poll_s=0.01,
        )
    assert dead.restarts == 1


def test_inflight_request_is_requeued_when_the_worker_restarts(tmp_path):
    inflight = tmp_path / "inflight"
    inflight.mkdir()
    (inflight / "0001-MFP.json").write_text("{}\n", encoding="utf-8")
    _recover_inflight(tmp_path)
    assert (tmp_path / "requests" / "0001-MFP.json").is_file()
    assert not (inflight / "0001-MFP.json").exists()


def test_default_generate_path_is_unchanged(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write(b"ok\n")
        return SimpleNamespace(returncode=0)

    drivers = make_drivers(tmp_path, runner=runner)
    geo_id = format_mfbo_pillar_geo_id(*DESIGN_A)
    out_dir = tmp_path / "geometries" / "pillar" / geo_id
    drivers.generate(design=PillarDesign(*DESIGN_A), geo_id=geo_id, out_dir=out_dir)
    assert calls[0][1].endswith("scripts/generate_pillar_cad.py")
    assert "--batch" not in calls[0]
    assert "--serve" not in calls[0]
    sidecar = json.loads((out_dir / f"{geo_id}_generate_wall_time.json").read_text(encoding="utf-8"))
    assert list(sidecar) == ["generate_wall_time_s"]

    def refuse(argv, **kwargs):
        raise AssertionError(argv)

    worker = tmp_path / "worker"
    ready = threading.Event()

    def handler(request):
        ready.set()
        out = Path(request["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "session_reused": True, "geo_id": request["geo_id"]}

    thread = threading.Thread(
        target=serve,
        kwargs={
            "directory": worker,
            "handler": handler,
            "session_open": lambda: True,
            "sleep_s": 0.01,
        },
        daemon=True,
    )
    thread.start()
    _wait_ready(worker)
    via_worker = make_drivers(tmp_path, runner=refuse, geometry_worker=worker)
    via_worker.generate(design=PillarDesign(*DESIGN_A), geo_id=geo_id, out_dir=out_dir)
    assert ready.wait(2)
    thread_stop = worker / "shutdown"
    thread_stop.write_text("", encoding="utf-8")
    thread.join(2)


def test_from_leaves_does_not_evaluate(monkeypatch, tmp_path):
    def explode(*_args, **_kwargs):
        raise AssertionError("from-leaves must not evaluate")

    monkeypatch.setattr("ro.mfbo_fidelity_screen.evaluate", explode)
    report = run_from_leaves(
        load_designs(DESIGNS),
        load_fidelity_table(TABLE),
        run_id="u0p2_p6M",
        data_root=tmp_path,
    )
    assert report["n_designs"] == 9
    assert report["n_comparable"] == 0
    assert report["rows"][0]["status_lf"] == "execution_failed"

    script = load_module(
        "run_3d_fidelity_screen_from_leaves",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    out = tmp_path / "screen"
    code = script.main(
        [
            "--designs",
            str(DESIGNS),
            "--fidelity-table",
            str(TABLE),
            "--data-root",
            str(tmp_path),
            "--run-id",
            "u0p2_p6M",
            "--from-leaves",
            "--out-dir",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "3d_fidelity_screen_v1.csv").is_file()
    assert (out / "3d_fidelity_screen_v1.md").is_file()


def test_geometry_worker_flag_is_opt_in(tmp_path, monkeypatch):
    screen = load_module(
        "run_3d_fidelity_screen_worker_flag",
        SCRIPTS_DIR / "mfbo" / "run_3d_fidelity_screen.py",
    )
    opened = []

    class _Supervisor:
        def __init__(self):
            self.directory = tmp_path / "geometry_worker"
            self.shutdowns = 0

        def shutdown(self):
            self.shutdowns += 1

    supervisor = _Supervisor()

    def open_worker(root, *, enabled, stop_file=None):
        assert enabled is True
        opened.append(stop_file)
        return supervisor

    monkeypatch.setattr("ro.geometry_worker.open_geometry_worker", open_worker)
    report = {
        "n_comparable": 0,
        "n_designs": 9,
        "cost_ratio": {"median": None},
        "cost_proxy_ratio": {"median": None},
        "cp_excess_bias_mean": None,
        "cp_excess_bias_spread": None,
    }
    screen.run_v1_screen = lambda *args, **kwargs: report
    screen.write_summary = lambda out_dir, payload: {"csv": out_dir / "c", "markdown": out_dir / "m"}
    stop = tmp_path / "STOP"
    code = screen.main(
        [
            "--designs",
            str(DESIGNS),
            "--fidelity-table",
            str(TABLE),
            "--data-root",
            str(tmp_path),
            "--run-id",
            "u0p2_p6M",
            "--out-dir",
            str(tmp_path / "out"),
            "--geometry-worker",
            "--stop-file",
            str(stop),
        ]
    )
    assert code == 0
    assert opened == [stop]
    assert supervisor.shutdowns == 1

    mfbo = load_module(
        "run_3d_mfbo_worker_flag",
        SCRIPTS_DIR / "mfbo" / "run_3d_mfbo.py",
    )
    refused = mfbo.main(
        [
            "run",
            "--config",
            str(REPO_ROOT / "configs" / "mfbo_3d_experiment.example.json"),
            "--data-root",
            str(tmp_path),
            "--geometry-worker",
        ]
    )
    assert refused == 1
