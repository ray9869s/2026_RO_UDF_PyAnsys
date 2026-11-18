"""B4-03: one Fluent job per host across queue files; refuse on orphan Fluent."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro import host_lock

job_queue = load_module("job_queue_host_lock", SCRIPTS_DIR / "mfbo" / "job_queue.py")


def _queue(tmp_path, name, marker):
    queue = (tmp_path / name).resolve()
    queue.write_text(json.dumps([{
        "id": "touch",
        "argv": ["{python}", "-c", f"open({str(marker)!r}, 'w').write('ran')"],
    }]), encoding="utf-8")
    return queue


def test_second_queue_file_is_refused_while_host_lock_is_live(tmp_path, monkeypatch):
    monkeypatch.setattr(job_queue, "read_git_head", lambda: "a" * 40)
    lock = host_lock.host_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("4242\n", encoding="ascii")
    monkeypatch.setattr(job_queue, "pid_is_alive", lambda pid: pid == 4242)
    marker = tmp_path / "ran.txt"
    queue = _queue(tmp_path, "other.json", marker)
    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_PREFLIGHT
    assert not marker.exists()
    # Its own queue lock was released; the foreign host lock is untouched.
    assert not Path(str(queue) + ".lock").exists()
    assert lock.read_text(encoding="ascii").strip() == "4242"


def test_host_lock_is_taken_and_released(tmp_path, monkeypatch):
    monkeypatch.setattr(job_queue, "read_git_head", lambda: "a" * 40)
    marker = tmp_path / "ran.txt"
    queue = _queue(tmp_path, "q.json", marker)
    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert marker.read_text() == "ran"
    assert not host_lock.host_lock_path().exists()


def test_live_fluent_refuses_the_next_job(tmp_path, monkeypatch):
    monkeypatch.setattr(job_queue, "read_git_head", lambda: "a" * 40)
    monkeypatch.setattr(job_queue, "wait_for_no_fluent", lambda: ["pid=77 fl_mpi2510.exe"])
    marker = tmp_path / "ran.txt"
    queue = _queue(tmp_path, "q.json", marker)
    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_PREFLIGHT
    assert not marker.exists()
    state = json.loads(Path(str(queue) + ".state.json").read_text(encoding="utf-8"))
    assert state["jobs"][0]["status"] == "pending"
    assert not host_lock.host_lock_path().exists()


def test_fluent_process_filter():
    processes = [
        {"ProcessId": 1, "Name": "fl_mpi2510.exe"},
        {"ProcessId": 2, "Name": "cortex.exe"},
        {"ProcessId": 3, "Name": "Fluent.exe"},
        {"ProcessId": 4, "Name": "python.exe"},
        {"ProcessId": 5, "Name": "fl2510.exe"},
    ]
    assert host_lock.fluent_processes(processes) == [
        "pid=1 fl_mpi2510.exe", "pid=2 cortex.exe", "pid=3 Fluent.exe", "pid=5 fl2510.exe",
    ]


def test_grace_waits_for_previous_solver_to_exit():
    calls = iter([["pid=1 fl_mpi.exe"], ["pid=1 fl_mpi.exe"], []])
    now = [0.0]
    found = host_lock.wait_for_no_fluent(
        lister=lambda: next(calls), grace_s=60, sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
    )
    assert found == []
    stuck = host_lock.wait_for_no_fluent(
        lister=lambda: ["pid=9 cortex.exe"], grace_s=25, sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
    )
    assert stuck == ["pid=9 cortex.exe"]


def test_pending_checks_sees_the_host_lock(tmp_path, monkeypatch):
    host = load_module("pending_checks_host_lock", SCRIPTS_DIR / "host" / "pending_checks.py")
    lock = host_lock.host_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("4243\n", encoding="ascii")
    monkeypatch.setattr(host.job_queue, "pid_is_alive", lambda pid: True)
    with pytest.raises(RuntimeError, match="live queue lock"):
        host.require_idle(tmp_path, processes=[])
