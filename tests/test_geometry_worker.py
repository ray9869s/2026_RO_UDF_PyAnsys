"""Geometry worker readiness. No Discovery."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ro.discovery_session import BACKEND_START_TIMEOUT_S, DiscoverySession
from ro.geometry_worker import (
    DISCOVERY_START_RETRIES,
    FIRST_REQUEST_MARGIN_S,
    READY_TIMEOUT_S,
    RECYCLE_MARKER,
    GeometryWorkerSupervisor,
    first_request_timeout_s,
    serve,
    submit_generate,
)

_LAUNCHER = """\
import json
import os
import subprocess
import sys
import time
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
token = os.environ["RO_GEOMETRY_WORKER_TOKEN"]
marker = directory / "interpreter.pid"
env = os.environ.copy()
env["INTERPRETER_PID_FILE"] = str(marker)
subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import os, time\\n"
        "from pathlib import Path\\n"
        "Path(os.environ['INTERPRETER_PID_FILE']).write_text(str(os.getpid()), encoding='utf-8')\\n"
        "time.sleep(60)\\n",
    ],
    env=env,
)
deadline = time.monotonic() + 5
while not marker.is_file():
    if time.monotonic() > deadline:
        raise SystemExit("interpreter did not write its pid")
    time.sleep(0.01)
pid = int(marker.read_text(encoding="utf-8"))
payload = {"pid": pid, "token": token, "status": "ready", "session_open": False}
(directory / "health.json").write_text(json.dumps(payload) + "\\n", encoding="utf-8")
time.sleep(60)
"""

_STALE = """\
import json
import subprocess
import sys
import time
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
payload = {"pid": child.pid, "token": "stale", "status": "ready", "session_open": False}
(directory / "health.json").write_text(json.dumps(payload) + "\\n", encoding="utf-8")
(directory / "child.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""


def _running(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    try:
        text = stat.read_text(encoding="utf-8")
    except OSError:
        return False
    state = text.rsplit(")", 1)[-1].split()[0]
    return state != "Z"


def _wait_dead(pid: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not _running(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} is still alive")


def _command(script: Path):
    def command(self):
        return [sys.executable, str(script), str(self.directory)]

    return command


def test_ready_timeout_default_is_120_seconds(tmp_path: Path) -> None:
    supervisor = GeometryWorkerSupervisor(tmp_path / "worker")
    assert supervisor.ready_timeout_s == READY_TIMEOUT_S == 120


def test_first_request_timeout_covers_discovery_start_and_one_retry() -> None:
    assert DISCOVERY_START_RETRIES == 1
    assert first_request_timeout_s() == (
        BACKEND_START_TIMEOUT_S * (1 + DISCOVERY_START_RETRIES) + FIRST_REQUEST_MARGIN_S
    )
    assert first_request_timeout_s() == 660


def test_health_pid_can_differ_from_the_launcher_pid(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "launcher.py"
    script.write_text(_LAUNCHER, encoding="utf-8")
    monkeypatch.setattr(GeometryWorkerSupervisor, "_command", _command(script))
    worker = tmp_path / "worker"
    supervisor = GeometryWorkerSupervisor(worker, ready_timeout_s=5)
    supervisor.start()
    launcher = supervisor._process.pid
    health = json.loads((worker / "health.json").read_text(encoding="utf-8"))
    try:
        assert health["status"] == "ready"
        assert health["session_open"] is False
        assert health["token"] == supervisor._token
        assert health["pid"] != launcher
        assert supervisor.healthy()
        assert _running(health["pid"])
        assert _running(launcher)
    finally:
        supervisor.stop_started()
    _wait_dead(launcher)
    _wait_dead(health["pid"])


def test_exited_launcher_is_ready_when_the_interpreter_pid_is_alive(tmp_path: Path) -> None:
    interpreter = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    supervisor = GeometryWorkerSupervisor(tmp_path / "worker", ready_timeout_s=2)
    supervisor._token = "launch-token"
    supervisor.directory.mkdir()
    try:
        health = {
            "pid": interpreter.pid,
            "token": "launch-token",
            "status": "ready",
            "session_open": False,
        }
        (supervisor.directory / "health.json").write_text(
            json.dumps(health) + "\n",
            encoding="utf-8",
        )

        class _Exited:
            def __init__(self):
                self.pid = interpreter.pid + 1
                self.returncode = 0

            def poll(self):
                return self.returncode

        supervisor._process = _Exited()
        assert supervisor._process.pid != health["pid"]
        assert supervisor.healthy()
        health["token"] = "stale"
        (supervisor.directory / "health.json").write_text(
            json.dumps(health) + "\n",
            encoding="utf-8",
        )
        assert supervisor.healthy() is False
    finally:
        os.kill(interpreter.pid, signal.SIGKILL)
        interpreter.wait(timeout=5)


def test_timeout_reports_health_and_stops_the_launcher_tree(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "stale.py"
    script.write_text(_STALE, encoding="utf-8")
    monkeypatch.setattr(GeometryWorkerSupervisor, "_command", _command(script))
    worker = tmp_path / "worker"
    supervisor = GeometryWorkerSupervisor(worker, ready_timeout_s=1.5)
    with pytest.raises(RuntimeError, match="did not become ready") as caught:
        supervisor.start()
    message = str(caught.value)
    assert "health.json:" in message
    assert '"token": "stale"' in message
    assert '"status": "ready"' in message
    health = json.loads((worker / "health.json").read_text(encoding="utf-8"))
    _wait_dead(supervisor._process.pid)
    _wait_dead(health["pid"])


def test_closed_session_times_out_and_stops_the_worker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ro.geometry_worker.first_request_timeout_s", lambda: 0.2)
    stopped = []

    class _Supervisor:
        def healthy(self):
            return True

        def stop_started(self):
            stopped.append(True)

    health = {"pid": 1, "token": "t", "status": "ready", "session_open": False}
    (tmp_path / "health.json").write_text(json.dumps(health) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="timed out") as caught:
        submit_generate(
            tmp_path,
            {"geo_id": "MFP_d0800_h0000_f0400"},
            supervisor=_Supervisor(),
            poll_s=0.01,
        )
    assert "health.json:" in str(caught.value)
    assert '"session_open": false' in str(caught.value)
    assert stopped == [True]


def test_open_session_is_not_limited_by_the_first_request_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ro.geometry_worker.first_request_timeout_s", lambda: 0.05)
    health = {"pid": os.getpid(), "token": "t", "status": "ready", "session_open": True}
    (tmp_path / "health.json").write_text(json.dumps(health) + "\n", encoding="utf-8")

    def answer() -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            pending = list((tmp_path / "requests").glob("*.json"))
            if pending:
                time.sleep(0.25)
                name = pending[0].name
                (tmp_path / "responses").mkdir(exist_ok=True)
                (tmp_path / "responses" / name).write_text(
                    json.dumps({"status": "ok", "session_reused": True}) + "\n",
                    encoding="utf-8",
                )
                return
        raise AssertionError("request was not written")

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()

    class _Supervisor:
        def healthy(self):
            return True

        def stop_started(self):
            raise AssertionError("an open session must not hit the first-request timeout")

    response = submit_generate(
        tmp_path,
        {"geo_id": "MFP_d0800_h0000_f0400"},
        supervisor=_Supervisor(),
        poll_s=0.02,
    )
    thread.join(2)
    assert response["status"] == "ok"


_RECYCLE_WORKER = """\
import os
import signal
import subprocess
import sys
from pathlib import Path

from ro.geometry_worker import serve

directory = Path(sys.argv[1])
retry = os.environ.get("RO_GEOMETRY_WORKER_RECYCLE") == "1"


def _gone(pid):
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return True
        kernel.CloseHandle(handle)
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


backend = None
if retry:
    backend_pid = int((directory / "backend-first.pid").read_text(encoding="utf-8"))
    worker_pid = int((directory / "worker-first.pid").read_text(encoding="utf-8"))
    gone = _gone(backend_pid) and _gone(worker_pid)
    (directory / "previous-gone").write_text("yes" if gone else "no", encoding="utf-8")
else:
    (directory / "worker-first.pid").write_text(str(os.getpid()), encoding="utf-8")
    backend = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (directory / "backend-first.pid").write_text(str(backend.pid), encoding="utf-8")


def handler(request):
    if not retry:
        return {
            "status": "failed",
            "failure_class": "infrastructure",
            "recycle_worker": True,
            "seconds_since_close": 42.5,
            "session_reused": False,
        }
    return {"status": "ok", "session_reused": False, "geo_id": request.get("geo_id")}


def on_stop():
    if backend is None or backend.poll() is not None:
        return
    os.kill(backend.pid, signal.SIGKILL)
    backend.wait(timeout=5)


serve(
    directory,
    handler=handler,
    session_open=lambda: False,
    sleep_s=0.02,
    on_stop=on_stop,
)
"""


def test_shutdown_log_is_the_worker_stop_path(tmp_path: Path) -> None:
    """``session closed: shutdown`` is ``serve``'s finally, not keep-alive.

    ``on_stop`` calls ``DiscoverySession.close`` (discovery_session.py), and
    that method sets the reason to ``shutdown``. Keep-alive logs
    ``session dropped`` and leaves the worker ``ready``. The Discovery exit
    code is read after ``close``: it is still unset when the worker decides
    to stop.
    """

    class _Process:
        def __init__(self):
            self.returncode = None

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    class _Modeler:
        def __init__(self):
            self.process = _Process()

        def ping(self):
            return None

        def close(self):
            return None

    modeler = _Modeler()
    session = DiscoverySession(
        lambda: modeler,
        process_lister=lambda: [],
        process_killer=lambda _pid: None,
    )
    session.modeler = modeler
    worker = tmp_path / "geometry_worker"
    thread = threading.Thread(
        target=serve,
        kwargs={
            "directory": worker,
            "handler": lambda request: {"status": "ok"},
            "session_open": lambda: session.modeler is not None,
            "session_keepalive": session.touch,
            "session_reason": lambda: session.close_reason,
            "keepalive_s": 0,
            "sleep_s": 0.01,
            "on_stop": session.close,
        },
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        log_path = worker / "worker.log"
        if log_path.is_file() and "keep-alive result=ok" in log_path.read_text(encoding="utf-8"):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("keep-alive did not report ok")
    (worker / "shutdown").write_text("", encoding="utf-8")
    thread.join(2)
    assert not thread.is_alive()
    log = (worker / "worker.log").read_text(encoding="utf-8")
    assert "session dropped" not in log
    assert "session closed: shutdown" in log
    assert "stop=shutdown_file" in log
    assert "discovery_exit_code=0" in log
    assert "discovery_exit_at=" in log
    assert "last_keepalive=ok" in log
    assert modeler.process.returncode == 0
    health = json.loads((worker / "health.json").read_text(encoding="utf-8"))
    assert health["status"] == "stopped"
    assert health["session_open"] is False


def test_failed_reopen_restarts_a_fresh_interpreter_before_backoff(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed reopen exits, the old backend is gone, then a new interpreter answers.

    ``submit_generate`` does that retry itself. The infrastructure backoff
    has not started: this call returns the fresh-process success.
    """
    script = tmp_path / "recycle_worker.py"
    script.write_text(_RECYCLE_WORKER, encoding="utf-8")
    monkeypatch.setattr(GeometryWorkerSupervisor, "_command", _command(script))
    worker = tmp_path / "worker"
    supervisor = GeometryWorkerSupervisor(worker, ready_timeout_s=5)
    supervisor.start()
    try:
        response = submit_generate(
            worker,
            {"geo_id": "MFP_d0800_h0000_f0400"},
            supervisor=supervisor,
            poll_s=0.02,
        )
        assert response["status"] == "ok"
        assert (worker / "previous-gone").read_text(encoding="utf-8") == "yes"
        log = (worker / "worker.log").read_text(encoding="utf-8")
        assert "reopen failed path=in-process seconds_since_close=42.500" in log
        assert "recycling worker" in log
        assert "restarting worker recycle=true" in log
        assert "session open path=fresh-process" in log
        assert not (worker / RECYCLE_MARKER).exists()
    finally:
        supervisor.shutdown()

