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
            verify_kill_s=0.3,
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
        verify_kill_s=0.3,
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


def test_console_meshing_does_not_pipe_stdout(monkeypatch, tmp_path):
    """job_queue console jobs keep their handles; progress is the mesh log."""
    batch = load_module("batch_meshing_console", SCRIPTS_DIR / "batch_meshing.py")
    import ro.hang_watchdog as watchdog

    calls = []
    monkeypatch.setattr(watchdog, "tee_until_hang", lambda cmd, **kw: calls.append(kw) or type("R", (), {"returncode": 0})())
    run = batch.guarded_meshing_runner(tmp_path / "mesh_log_m1.txt")
    monkeypatch.setenv("RO_DISCOVERY_CONSOLE", "1")
    run(["x"], env={}, cwd=None, check=False)
    monkeypatch.delenv("RO_DISCOVERY_CONSOLE")
    run(["x"], env={}, cwd=None, check=False)
    assert [call["pipe_output"] for call in calls] == [False, True]


class _ConsoleProc:
    """Popen stand-in that records its kwargs; never exits on its own."""

    pid = 424242

    def __init__(self, cmd, **kwargs):
        self.kwargs = kwargs
        self.killed = False

    def poll(self):
        return -9 if self.killed else None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -9


def test_console_mode_uses_log_growth_and_no_pipe(tmp_path):
    procs = []

    def factory(cmd, **kwargs):
        procs.append(_ConsoleProc(cmd, **kwargs))
        return procs[-1]

    mesh_log = tmp_path / "mesh_log.txt"
    mesh_log.write_text("start\n", encoding="utf-8")
    started = time.monotonic()
    result = tee_until_hang(
        ["mesh"], env=None, log_path=tmp_path / "mesh_attempt1.log", stage="mesh",
        idle_s=0.5, poll_s=0.05, extra_files=(mesh_log,), popen_factory=factory,
        killer=lambda pid: f"killed {pid}", pipe_output=False, verify_kill_s=0.3,
    )
    assert result.returncode == HANG_EXIT_CODE
    assert time.monotonic() - started < 10
    assert "stdout" not in procs[0].kwargs and "stderr" not in procs[0].kwargs
    text = (tmp_path / "mesh_attempt1.log").read_text(encoding="utf-8")
    assert "Console job (no stdout pipe)" in text
    assert parse_hang_line(text)["stage"] == "mesh"


def test_console_mode_growth_resets_idle(tmp_path):
    script = tmp_path / "grow.py"
    mesh_log = tmp_path / "mesh_log.txt"
    script.write_text(
        "import sys, time\n"
        "for i in range(12):\n"
        "    open(sys.argv[1], 'a').write('line\\n')\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    result = tee_until_hang(
        [sys.executable, str(script), str(mesh_log)], env=None,
        log_path=tmp_path / "mesh_attempt1.log", stage="mesh", idle_s=0.6, poll_s=0.05,
        extra_files=(mesh_log,), pipe_output=False,
    )
    assert result.returncode == 0


FLOODER = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c",
    "import sys, time\\nwhile True:\\n    print('flood', flush=True)\\n    time.sleep(0.001)"])
open(sys.argv[1], "w").write(str(child.pid))
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX inheritance fixture")
def test_exit_is_seen_while_a_grandchild_floods_the_pipe(tmp_path):
    """Exit/drain is checked before each line, so a flood cannot hold the stage."""
    script = tmp_path / "flood.py"
    script.write_text(FLOODER, encoding="utf-8")
    pid_file = tmp_path / "flooder.pid"
    started = time.monotonic()
    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)], env=None,
            log_path=tmp_path / "attempt.log", stage="extract", idle_s=60,
            poll_s=0.05, drain_s=0.5,
        )
        assert result.returncode == 0
        assert time.monotonic() - started < 15
        assert "grandchild holds the pipe" in (tmp_path / "attempt.log").read_text(encoding="utf-8")
    finally:
        _reap(pid_file)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_kill_verification_names_a_surviving_grandchild(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(GRANDCHILD.format(tail="time.sleep(30)"), encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"
    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)], env=None,
            log_path=tmp_path / "attempt.log", stage="extract", idle_s=0.8, poll_s=0.05,
            killer=lambda pid: f"pretend-killed {pid}",  # kills nothing; the worker is proc.kill()ed
            verify_kill_s=0.5,
        )
        assert result.returncode == HANG_EXIT_CODE
        text = (tmp_path / "attempt.log").read_text(encoding="utf-8")
        grandchild = pid_file.read_text().strip()
        assert "KILL_INCOMPLETE" in text and grandchild in text.split("KILL_INCOMPLETE")[1]
    finally:
        _reap(pid_file)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_kill_verification_confirms_a_killed_tree(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(GRANDCHILD.format(tail="time.sleep(30)"), encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"

    def kill_tree(pid):
        _reap(pid_file)
        os.kill(pid, signal.SIGKILL)
        return f"killed {pid}"

    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)], env=None,
            log_path=tmp_path / "attempt.log", stage="extract", idle_s=0.8, poll_s=0.05,
            killer=kill_tree, verify_kill_s=5,
        )
        assert result.returncode == HANG_EXIT_CODE
        assert "KILL_VERIFIED" in (tmp_path / "attempt.log").read_text(encoding="utf-8")
    finally:
        _reap(pid_file)


def test_process_tree_helpers():
    from ro.process_tree import kill_verification_line, snapshot_tree, tree_pids, wait_gone

    table = [
        {"ProcessId": 10, "ParentProcessId": 1},
        {"ProcessId": 11, "ParentProcessId": 10},
        {"ProcessId": 12, "ParentProcessId": 11},
        {"ProcessId": 20, "ParentProcessId": 1},
    ]
    assert tree_pids([10], table) == {10, 11, 12}
    survivors, error = wait_gone({10, 11, 12}, lister=lambda: table[2:], timeout_s=0)
    assert survivors == [12] and error is None
    assert kill_verification_line({10, 11, 12}, survivors).startswith("KILL_INCOMPLETE survivors=12")
    assert kill_verification_line({10}, []).startswith("KILL_VERIFIED pids=10")

    def broken():
        raise OSError("lister down")

    pids, snap_error = snapshot_tree([10], lister=broken)
    assert pids == {10} and "lister down" in snap_error
    survivors, error = wait_gone({10}, lister=broken, timeout_s=0)
    assert survivors == [10] and "lister down" in error
    # A failed listing is never reported as verified, before or after the kill.
    assert kill_verification_line({10}, [], verify_error=error).startswith("KILL_INCOMPLETE")
    unlisted = kill_verification_line(pids, [], snapshot_error=snap_error)
    assert unlisted.startswith("KILL_INCOMPLETE") and "tree_listing_error" in unlisted


@pytest.mark.skipif(os.name == "nt", reason="POSIX kill path")
def test_queue_kill_process_tree_reports_verification(tmp_path):
    job_queue = load_module("job_queue_kill_verify", SCRIPTS_DIR / "mfbo" / "job_queue.py")
    import subprocess as sp

    proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    message = job_queue.kill_process_tree(proc, verify_kill_s=5)
    assert "KILL_VERIFIED" in message

    class Ghost:
        pid = 99999999

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    survivor = job_queue.kill_process_tree(
        Ghost(), lister=lambda: [{"ProcessId": 99999999, "ParentProcessId": 1}], verify_kill_s=0
    )
    assert "KILL_INCOMPLETE survivors=99999999" in survivor


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_hang_result_waits_for_kill_verification(tmp_path):
    """The kill closes the pipe; the result must still be the HANG, not the
    killed worker's exit code, while verification is running."""
    script = tmp_path / "worker.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "open(sys.argv[1], 'w').write(str(c.pid))\n"
        "print('started', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    pid_file = tmp_path / "grandchild.pid"
    try:
        result = tee_until_hang(
            [sys.executable, str(script), str(pid_file)], env=None,
            log_path=tmp_path / "attempt.log", stage="extract", idle_s=0.8, poll_s=0.05,
            killer=lambda pid: "noop", verify_kill_s=3,
        )
        assert result.returncode == HANG_EXIT_CODE
        text = (tmp_path / "attempt.log").read_text(encoding="utf-8")
        assert parse_hang_line(text) is not None
        assert "KILL_INCOMPLETE" in text
    finally:
        _reap(pid_file)


def test_extract_does_not_retry_after_kill_incomplete(tmp_path):
    batch = load_module("batch_report_extract_kill_incomplete", SCRIPTS_DIR / "batch_report_extract.py")
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    calls = []

    def runner(cmd, env, log_path):
        calls.append(log_path)
        Path(log_path).write_text(
            "HANG failure_class=hang stage=extract idle_s=1 killed_pids=5\n"
            "KILL_INCOMPLETE survivors=6 pids=5,6\n", encoding="utf-8")
        return type("R", (), {"returncode": HANG_EXIT_CODE})()

    env = {"PYFLUENT_POST_OVERRIDES": json.dumps({"report_snapshot_iteration": 1})}
    result, attempts, _kinds, _logs = batch.run_extract_attempts(
        cmd=["x"], env=env, run_directory=leaf, geo_id="g", mesh_id="m", run_id="r",
        runner=runner, sleeper=lambda s: None, killer=lambda pid: "", process_lister=lambda: [])
    assert len(calls) == 1 and attempts == 1
    assert result.returncode == HANG_EXIT_CODE


@pytest.mark.parametrize("kill_line", ["", "KILL_INCOMPLETE survivors= pids=5 tree_listing_error='x'\n"])
def test_extract_retries_a_hang_only_after_kill_verified(tmp_path, kill_line):
    batch = load_module("batch_report_extract_kill_verified", SCRIPTS_DIR / "batch_report_extract.py")
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    calls = []

    def runner(cmd, env, log_path):
        calls.append(log_path)
        Path(log_path).write_text(
            "HANG failure_class=hang stage=extract idle_s=1 killed_pids=5\n" + kill_line,
            encoding="utf-8")
        return type("R", (), {"returncode": HANG_EXIT_CODE})()

    env = {"PYFLUENT_POST_OVERRIDES": json.dumps({"report_snapshot_iteration": 1})}
    _result, attempts, _kinds, _logs = batch.run_extract_attempts(
        cmd=["x"], env=env, run_directory=leaf, geo_id="g", mesh_id="m", run_id="r",
        runner=runner, sleeper=lambda s: None, killer=lambda pid: "", process_lister=lambda: [])
    assert len(calls) == 1 and attempts == 1


def test_taskkill_is_bounded_and_a_timeout_is_reported_not_raised():
    import subprocess as sp

    from ro.process_tree import TASKKILL_TIMEOUT_RETURNCODE, TASKKILL_TIMEOUT_S, run_taskkill

    seen = {}

    def hangs(argv, **kwargs):
        seen.update(kwargs)
        raise sp.TimeoutExpired(argv, kwargs["timeout"])

    result = run_taskkill(4242, runner=hangs)
    assert seen["timeout"] == TASKKILL_TIMEOUT_S
    assert result.returncode == TASKKILL_TIMEOUT_RETURNCODE
    assert "timed out" in result.stderr

    session_retry = __import__("ro.session_retry", fromlist=["_kill_recorded_fluent_pid"])
    if os.name == "nt":
        message = session_retry._kill_recorded_fluent_pid(4242, runner=hangs)
        assert "timed out" in message
