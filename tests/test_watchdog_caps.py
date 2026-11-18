"""B2-03: the watchdog never blocks on a held pipe; wall caps; queue timeouts."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.hang_watchdog import HANG_EXIT_CODE, parse_hang_line, tee_until_hang

GRANDCHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
open(sys.argv[1], "w").write(str(child.pid))
print("started", flush=True)
{tail}
"""


def _reap(pid_file):
    if pid_file.is_file():
        pid = int(pid_file.read_text())
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, AttributeError):
            pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX inheritance fixture")
def test_kill_returns_even_when_a_grandchild_holds_stdout(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(GRANDCHILD.format(tail="time.sleep(30)"), encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)],
            env=None,
            log_path=tmp_path / "attempt.log",
            stage="extract",
            idle_s=0.8,
            poll_s=0.05,
            killer=lambda pid: f"killed {pid}",
        )
        elapsed = time.monotonic() - started
        assert result.returncode == HANG_EXIT_CODE
        assert elapsed < 10, elapsed
        assert parse_hang_line((tmp_path / "attempt.log").read_text(encoding="utf-8"))
    finally:
        _reap(pid_file)


@pytest.mark.skipif(os.name == "nt", reason="POSIX inheritance fixture")
def test_normal_exit_with_a_held_pipe_drains_and_returns(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(GRANDCHILD.format(tail="print('done', flush=True)"), encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)],
            env=None,
            log_path=tmp_path / "attempt.log",
            stage="extract",
            idle_s=20,
            poll_s=0.05,
            drain_s=0.5,
        )
        assert result.returncode == 0
        assert time.monotonic() - started < 10
        text = (tmp_path / "attempt.log").read_text(encoding="utf-8")
        assert "done" in text and "grandchild holds the pipe" in text
    finally:
        _reap(pid_file)


def test_wall_cap_stops_a_worker_that_keeps_printing(tmp_path):
    script = tmp_path / "chatty.py"
    script.write_text(
        "import time\nwhile True:\n    print('still here', flush=True)\n    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = tee_until_hang(
        [sys.executable, str(script)],
        env=None,
        log_path=tmp_path / "attempt.log",
        stage="solve",
        idle_s=60,
        poll_s=0.05,
        wall_s=0.8,
        killer=lambda pid: f"killed {pid}",
    )
    assert result.returncode == HANG_EXIT_CODE
    assert time.monotonic() - started < 10
    text = (tmp_path / "attempt.log").read_text(encoding="utf-8")
    assert "HANG_REASON wall_cap stage=solve" in text


def test_wall_caps_and_mesh_idle_come_from_run_config():
    from ro.hang_watchdog import idle_seconds, wall_cap_seconds

    assert idle_seconds("mesh") == 90 * 60
    assert wall_cap_seconds("extract") == 6 * 3600
    assert wall_cap_seconds("solve") == 48 * 3600
    assert wall_cap_seconds("mesh") == 12 * 3600
    for name in ("batch_report_extract.py", "batch_solver_sweep.py"):
        assert "wall_s=wall_cap_seconds(" in (SCRIPTS_DIR / name).read_text(encoding="utf-8")


def test_campaign_meshing_runs_under_the_watchdog(monkeypatch, tmp_path):
    driver = load_module("mesh_study_watchdog", SCRIPTS_DIR / "mfbo" / "mesh_study_case.py")
    seen = {}

    def fake_attempts(**kwargs):
        seen.update(kwargs)
        return type("R", (), {"returncode": 0})(), 1, []

    monkeypatch.setattr(driver.batch_meshing, "run_meshing_attempts", fake_attempts)
    driver.launch_meshing({}, tmp_path, tmp_path / "mesh", "m1", 0)
    assert seen["runner"] is not None
    assert seen["runner"].__qualname__.startswith("guarded_meshing_runner")


def test_guarded_meshing_runner_tees_with_mesh_stage(monkeypatch, tmp_path):
    batch = load_module("batch_meshing_watchdog", SCRIPTS_DIR / "batch_meshing.py")
    import ro.hang_watchdog as watchdog

    calls = []
    monkeypatch.setattr(watchdog, "tee_until_hang", lambda cmd, **kw: calls.append(kw) or type("R", (), {"returncode": 0})())
    run = batch.guarded_meshing_runner(tmp_path / "mesh_log_m1.txt")
    run(["x"], env={}, cwd=None, check=False)
    run(["x"], env={}, cwd=None, check=False)
    assert [Path(call["log_path"]).name for call in calls] == ["mesh_attempt1.log", "mesh_attempt2.log"]
    assert calls[0]["stage"] == "mesh"
    assert calls[0]["wall_s"] == 12 * 3600
    assert tmp_path / "mesh_log_m1.txt" in calls[0]["extra_files"]


def test_queue_job_timeout_kills_and_fails_the_job(tmp_path, monkeypatch):
    job_queue = load_module("job_queue_timeout", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    monkeypatch.setattr(job_queue, "read_git_head", lambda: "a" * 40)
    queue = (tmp_path / "q.json").resolve()
    queue.write_text(json.dumps([
        {"id": "slow", "argv": ["{python}", "-c", "import time; time.sleep(30)"], "timeout_s": 0.5},
        {"id": "fast", "argv": ["{python}", "-c", "pass"]},
    ]), encoding="utf-8")
    started = time.monotonic()
    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert time.monotonic() - started < 20
    state = json.loads(Path(str(queue) + ".state.json").read_text(encoding="utf-8"))
    assert [job["status"] for job in state["jobs"]] == ["failed", "succeeded"]
    assert state["jobs"][0]["return_code"] is None
    assert "JOB_TIMEOUT" in (tmp_path / "logs" / "slow.log").read_text(encoding="utf-8")


def test_queue_timeout_must_be_positive(tmp_path):
    job_queue = load_module("job_queue_timeout_bad", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    queue = (tmp_path / "q.json").resolve()
    queue.write_text(json.dumps([{"id": "a", "argv": ["x"], "timeout_s": 0}]), encoding="utf-8")
    with pytest.raises(job_queue.QueueError, match="timeout_s"):
        job_queue.load_queue(queue)
