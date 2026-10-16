"""One local geometry worker. Request and response are JSON files.

Importing this module does not launch Discovery. The worker process is
``scripts/generate_pillar_cad.py --serve``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ro.paths import project_root

_READY = ("ready", "busy")
_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def serve(
    directory,
    *,
    handler,
    stop_file=None,
    session_open,
    sleep_s=0.2,
    stop=None,
    on_stop=None,
):
    """Handle one request at a time until stop, shutdown, or ``stop_file``.

    SIGINT and SIGTERM set the stop flag. The request already running finishes,
    then the loop exits. ``handler`` returns the response object.
    """
    root = Path(directory)
    _prepare(root)
    _recover_inflight(root)
    flag = {"flag": False} if stop is None else stop
    installed = _install_stop_signals(flag)
    try:
        _write_health(root, status="ready", session_open=bool(session_open()))
        while not _stop_requested(flag, root, stop_file):
            claimed = _claim(root)
            if claimed is None:
                time.sleep(sleep_s)
                continue
            _write_health(root, status="busy", session_open=bool(session_open()))
            try:
                payload = json.loads(claimed.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("geometry request must be a JSON object.")
                response = handler(payload)
                if not isinstance(response, dict):
                    raise TypeError("geometry handler must return an object.")
            except Exception as exc:
                response = {
                    "status": "failed",
                    "failure_class": "geometry",
                    "error": f"{type(exc).__name__}: {exc}",
                    "session_reused": False,
                }
            response.setdefault("request_id", claimed.name[: -len(".json")])
            _atomic_json(root / "responses" / claimed.name, response)
            claimed.unlink()
            _write_health(root, status="ready", session_open=bool(session_open()))
    finally:
        for sig, previous in installed:
            signal.signal(sig, previous)
        if on_stop is not None:
            on_stop()
        _write_health(root, status="stopped", session_open=False)


def submit_generate(directory, request, *, supervisor=None, poll_s=0.05):
    """Write one request and wait for its response. Restart a dead worker once."""
    if not isinstance(request, dict):
        raise TypeError(f"geometry request must be an object, got {type(request).__name__}.")
    root = Path(directory)
    (root / "requests").mkdir(parents=True, exist_ok=True)
    request_id = f"{time.time_ns():020d}-{_safe(str(request.get('geo_id', 'geometry')))}"
    body = dict(request)
    body["request_id"] = request_id
    name = request_id + ".json"
    _atomic_json(root / "requests" / name, body)
    restarted = False
    while True:
        response_path = root / "responses" / name
        if response_path.is_file():
            payload = _read_json(response_path)
            if isinstance(payload, dict):
                return payload
        if supervisor is not None and not supervisor.healthy():
            if restarted:
                geo_id = request.get("geo_id")
                raise RuntimeError(
                    f"geometry worker died while generating {geo_id!r}."
                )
            supervisor.restart()
            restarted = True
            continue
        time.sleep(poll_s)


class GeometryWorkerSupervisor:
    """Start ``generate_pillar_cad.py --serve`` and stop it on shutdown."""

    def __init__(self, directory, *, stop_file=None):
        self.directory = Path(directory)
        self.stop_file = None if stop_file is None else Path(stop_file)
        self._process = None

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        shutdown = self.directory / "shutdown"
        if shutdown.exists():
            shutdown.unlink()
        _recover_inflight(self.directory)
        self._process = self._spawn()
        self._wait_ready()

    def healthy(self) -> bool:
        if self._process is None or self._process.poll() is not None:
            return False
        health = _read_json(self.directory / "health.json")
        if not isinstance(health, dict):
            return False
        if health.get("pid") != self._process.pid:
            return False
        return health.get("status") in _READY

    def restart(self) -> None:
        self._stop_process(5)
        shutdown = self.directory / "shutdown"
        if shutdown.exists():
            shutdown.unlink()
        _recover_inflight(self.directory)
        self._process = self._spawn()
        self._wait_ready()

    def shutdown(self) -> None:
        if self._process is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "shutdown").write_text("", encoding="utf-8")
        self._stop_process(30)
        self._process = None

    def _spawn(self):
        script = project_root() / "scripts" / "generate_pillar_cad.py"
        argv = [sys.executable, str(script), "--serve", str(self.directory)]
        if self.stop_file is not None:
            argv.extend(["--stop-file", str(self.stop_file)])
        return subprocess.Popen(
            argv,
            cwd=str(project_root()),
            stdin=subprocess.DEVNULL,
        )

    def _wait_ready(self, timeout_s=30) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.healthy():
                return
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"geometry worker exited {self._process.returncode} before it was ready."
                )
            time.sleep(0.05)
        raise RuntimeError("geometry worker did not become ready.")

    def _stop_process(self, grace_s: float) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def open_geometry_worker(data_root, *, enabled, stop_file=None):
    """Start the worker when ``enabled``. Return None when it is not."""
    if not enabled:
        return None
    supervisor = GeometryWorkerSupervisor(
        Path(data_root) / "geometry_worker",
        stop_file=stop_file,
    )
    supervisor.start()
    _chain_stop_signals(supervisor)
    return supervisor


def _install_stop_signals(stop: dict) -> list:
    if threading.current_thread() is not threading.main_thread():
        return []
    installed = []
    for sig in _SIGNALS:
        try:
            previous = signal.signal(sig, lambda signum, frame, stop=stop: stop.__setitem__("flag", True))
        except (ValueError, OSError):
            continue
        installed.append((sig, previous))
    return installed


def _chain_stop_signals(supervisor) -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def _handler(signum, frame, previous):
        try:
            supervisor.shutdown()
        finally:
            if callable(previous) and previous not in (signal.SIG_DFL, signal.SIG_IGN):
                previous(signum, frame)
            else:
                raise KeyboardInterrupt

    for sig in _SIGNALS:
        previous = signal.getsignal(sig)
        signal.signal(
            sig,
            lambda signum, frame, previous=previous: _handler(signum, frame, previous),
        )


def _stop_requested(stop: dict, directory: Path, stop_file) -> bool:
    if stop.get("flag"):
        return True
    if (directory / "shutdown").is_file():
        return True
    return stop_file is not None and Path(stop_file).is_file()


def _prepare(directory: Path) -> None:
    for name in ("requests", "responses", "inflight"):
        (directory / name).mkdir(parents=True, exist_ok=True)


def _recover_inflight(directory: Path) -> None:
    inflight = directory / "inflight"
    if not inflight.is_dir():
        return
    requests = directory / "requests"
    responses = directory / "responses"
    requests.mkdir(parents=True, exist_ok=True)
    for path in inflight.glob("*.json"):
        if (responses / path.name).is_file():
            path.unlink()
        else:
            path.replace(requests / path.name)


def _claim(directory: Path):
    requests = directory / "requests"
    pending = sorted(requests.glob("*.json"))
    if not pending:
        return None
    path = pending[0]
    destination = directory / "inflight" / path.name
    try:
        path.replace(destination)
    except OSError:
        return None
    return destination


def _write_health(directory: Path, *, status: str, session_open: bool) -> None:
    _atomic_json(
        directory / "health.json",
        {
            "pid": os.getpid(),
            "status": status,
            "session_open": bool(session_open),
        },
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload


def _safe(value: str) -> str:
    cleaned = []
    for char in value:
        if char.isalnum() or char in "._-":
            cleaned.append(char)
        else:
            cleaned.append("_")
    token = "".join(cleaned).strip("._")
    return token or "geometry"
