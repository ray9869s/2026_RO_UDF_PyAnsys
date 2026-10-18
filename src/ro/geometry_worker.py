"""One local geometry worker. Request and response are JSON files.

Importing this module does not launch Discovery. The worker process is
``scripts/generate_pillar_cad.py --serve``. A supervisor start gives that
process its own console, so it does not inherit a queue log, and tees
its output onto ``worker.log`` in the worker directory.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ro.discovery_session import (
    BACKEND_START_TIMEOUT_S,
    attach_discovery_log,
    discovery_stdio_kwargs,
)
from ro.paths import project_root

_READY = ("ready", "busy")
_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_TOKEN_ENV = "RO_GEOMETRY_WORKER_TOKEN"
# Importing Discovery and the CAD stack can take longer than a few seconds.
READY_TIMEOUT_S = 120
# DiscoverySession retries a backend start once (attempts 1 and 2).
DISCOVERY_START_RETRIES = 1
# Pad after the worst-case Discovery open so the geometry build can finish.
FIRST_REQUEST_MARGIN_S = 60
# Idle Discovery sessions die. Creating a design on this period is licence
# use; ping and backend_version are not. Ten minutes is the default.
KEEPALIVE_S = 600.0
WORKER_LOG = "worker.log"
RECYCLE_MARKER = "recycle"
_RECYCLE_ENV = "RO_GEOMETRY_WORKER_RECYCLE"
_LOG_ENV = "RO_GEOMETRY_WORKER_LOG"


def serve(
    directory,
    *,
    handler,
    stop_file=None,
    session_open,
    sleep_s=0.2,
    stop=None,
    on_stop=None,
    token=None,
    session_keepalive=None,
    keepalive_s=KEEPALIVE_S,
    session_reason=None,
):
    """Handle one request at a time until stop, shutdown, or ``stop_file``.

    SIGINT and SIGTERM set the stop flag. The request already running finishes,
    then the loop exits. ``handler`` returns the response object.
    ``token`` is the supervisor's per-launch id. The environment variable is
    the same id when this process was spawned by the supervisor.
    ``session_keepalive`` is called while idle. A false result or an
    exception is the session closing; the reason is appended to ``worker.log``.
    A response with ``recycle_worker`` true means this interpreter failed to
    open Discovery again. The request stays in ``inflight`` and this process
    exits so the supervisor can start a fresh one before any backoff.
    """
    root = Path(directory)
    log_path = os.environ.get(_LOG_ENV)
    if log_path:
        attach_discovery_log(log_path)
    _prepare(root)
    _recover_inflight(root)
    flag = {"flag": False} if stop is None else stop
    installed = _install_stop_signals(flag)
    launch_token = os.environ.get(_TOKEN_ENV) if token is None else token
    last_keepalive = time.monotonic()
    last_keepalive_text = "none"
    recycling = False
    # A process started to retry one failed reopen reports that retry.
    # A later failure in the same process may recycle again.
    suppress_recycle = os.environ.get(_RECYCLE_ENV) == "1"
    fresh_process = suppress_recycle
    try:
        _write_health(
            root,
            status="ready",
            session_open=bool(session_open()),
            token=launch_token,
            session_reason=session_reason,
        )
        while not _stop_requested(flag, root, stop_file):
            claimed = _claim(root)
            if claimed is None:
                due = time.monotonic() - last_keepalive >= float(keepalive_s)
                if session_keepalive is not None and session_open() and due:
                    last_keepalive = time.monotonic()
                    last_keepalive_text = _keepalive(
                        root, session_keepalive, session_reason, launch_token
                    )
                time.sleep(sleep_s)
                continue
            _write_health(
                root,
                status="busy",
                session_open=bool(session_open()),
                token=launch_token,
                session_reason=session_reason,
            )
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
                _log(root, f"request failed: {type(exc).__name__}: {exc}")
            response.setdefault("request_id", claimed.name[: -len(".json")])
            # The geometry itself is licence use. The idle timer starts over.
            last_keepalive = time.monotonic()
            recycle = response.pop("recycle_worker", False) is True
            if recycle:
                seconds = response.get("seconds_since_close")
                shown = "none" if seconds is None else f"{float(seconds):.3f}"
                if suppress_recycle:
                    _log(
                        root,
                        "reopen failed path=fresh-process "
                        f"seconds_since_close={shown}",
                    )
                    suppress_recycle = False
                else:
                    _log(
                        root,
                        "reopen failed path=in-process "
                        f"seconds_since_close={shown}; recycling worker",
                    )
                    (root / RECYCLE_MARKER).write_text("", encoding="utf-8")
                    recycling = True
                    break
            if response.get("status") == "ok" and response.get("session_reused") is False:
                kind = "fresh-process" if fresh_process else "in-process"
                _log(root, f"session open path={kind}")
                fresh_process = False
                suppress_recycle = False
            _atomic_json(root / "responses" / claimed.name, response)
            claimed.unlink()
            _write_health(
                root,
                status="ready",
                session_open=bool(session_open()),
                token=launch_token,
                session_reason=session_reason,
            )
    finally:
        for sig, previous in installed:
            signal.signal(sig, previous)
        if on_stop is not None:
            try:
                on_stop()
            except Exception as exc:
                _log(root, f"session closed: {type(exc).__name__}: {exc}")
        reason = session_reason() if callable(session_reason) else None
        # This is the only writer of "session closed: shutdown". on_stop is
        # DiscoverySession.close, which sets that reason. Keep-alive logs
        # "session dropped" above and leaves this loop running.
        cause = "recycle" if recycling else _stop_cause(flag, root, stop_file)
        if reason:
            _log(
                root,
                f"session closed: {reason} stop={cause} "
                f"last_keepalive={last_keepalive_text}",
            )
        _write_health(
            root,
            status="stopped",
            session_open=False,
            token=launch_token,
            session_reason=session_reason,
        )


def first_request_timeout_s(
    start_timeout_s: float = BACKEND_START_TIMEOUT_S,
    *,
    retries: int = DISCOVERY_START_RETRIES,
    margin_s: float = FIRST_REQUEST_MARGIN_S,
) -> float:
    """Worst-case wait for the first generate, which opens Discovery lazily.

    The backend start timeout is tried once and then retried once. The margin
    is for the geometry build after that open.
    """
    return float(start_timeout_s) * (1 + retries) + float(margin_s)


def submit_generate(directory, request, *, supervisor=None, poll_s=0.05, timeout_s=None):
    """Write one request and wait for its response. Restart a dead worker once.

    When Discovery is not open yet, the wait is limited to
    ``first_request_timeout_s`` unless ``timeout_s`` is set. A timeout stops
    the worker and anything that launch started.
    """
    if not isinstance(request, dict):
        raise TypeError(f"geometry request must be an object, got {type(request).__name__}.")
    root = Path(directory)
    (root / "requests").mkdir(parents=True, exist_ok=True)
    request_id = f"{time.time_ns():020d}-{_safe(str(request.get('geo_id', 'geometry')))}"
    body = dict(request)
    body["request_id"] = request_id
    name = request_id + ".json"
    _atomic_json(root / "requests" / name, body)
    if timeout_s is None and supervisor is not None and not _session_open(root):
        timeout_s = first_request_timeout_s()
    deadline = None if timeout_s is None else time.monotonic() + float(timeout_s)
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
            recycle = (root / RECYCLE_MARKER).is_file()
            if recycle:
                (root / RECYCLE_MARKER).unlink()
            # The fresh interpreter runs before the caller's backoff wait.
            supervisor.restart(recycle=recycle)
            restarted = True
            if timeout_s is not None:
                deadline = time.monotonic() + float(timeout_s)
            continue
        if deadline is not None and time.monotonic() >= deadline:
            if supervisor is not None:
                supervisor.stop_started()
            raise RuntimeError(_first_request_timeout_message(root))
        time.sleep(poll_s)


class GeometryWorkerSupervisor:
    """Start ``generate_pillar_cad.py --serve`` and stop it on shutdown."""

    def __init__(self, directory, *, stop_file=None, ready_timeout_s=READY_TIMEOUT_S):
        if isinstance(ready_timeout_s, bool) or not isinstance(ready_timeout_s, (int, float)):
            raise TypeError(
                f"ready_timeout_s must be a number of seconds, got {ready_timeout_s!r}."
            )
        if ready_timeout_s <= 0:
            raise ValueError(f"ready_timeout_s must be > 0, got {ready_timeout_s!r}.")
        self.directory = Path(directory)
        self.stop_file = None if stop_file is None else Path(stop_file)
        self.ready_timeout_s = float(ready_timeout_s)
        self._process = None
        self._token = None
        self._recycle_launch = False

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        shutdown = self.directory / "shutdown"
        if shutdown.exists():
            shutdown.unlink()
        marker = self.directory / RECYCLE_MARKER
        if marker.exists():
            marker.unlink()
        self._recycle_launch = False
        _recover_inflight(self.directory)
        self._launch()

    def healthy(self) -> bool:
        """True when this launch's token is in health.json and the worker is alive.

        The pid in health.json is the interpreter. A Windows venv ``python.exe``
        is a launcher, so that pid is not ``Popen.pid``. Either the launcher or
        the interpreter being alive counts.
        """
        health = _read_json(self.directory / "health.json")
        return self._matches(health) and self._worker_alive(health)

    def restart(self, *, recycle: bool = False) -> None:
        """Stop this worker and its backends, then start a new interpreter.

        ``recycle`` marks the new process as the retry of a failed reopen.
        That one request is logged ``fresh-process`` and is not recycled
        again. A later failure in that process may recycle. The old process
        is waited out first so its ``on_stop`` can kill the Discovery it
        started; if it is still alive, the process group is killed before
        the new interpreter starts.
        """
        self._recycle_launch = bool(recycle)
        self._stop_process(5)
        shutdown = self.directory / "shutdown"
        if shutdown.exists():
            shutdown.unlink()
        _recover_inflight(self.directory)
        _log(
            self.directory,
            f"restarting worker recycle={str(bool(recycle)).lower()}",
        )
        self._launch()

    def shutdown(self) -> None:
        if self._process is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "shutdown").write_text("", encoding="utf-8")
        self._stop_process(30)
        self._process = None

    def stop_started(self) -> None:
        """Kill this launch and processes it started."""
        process = self._process
        health = _read_json(self.directory / "health.json")
        if process is not None:
            _kill_group(process.pid)
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        self._kill_interpreter(health)

    def _launch(self) -> None:
        self._token = secrets.token_hex(16)
        self._process = self._spawn()
        self._wait_ready()

    def _matches(self, health) -> bool:
        return (
            isinstance(health, dict)
            and bool(self._token)
            and health.get("token") == self._token
            and health.get("status") in _READY
        )

    def _worker_alive(self, health) -> bool:
        process = self._process
        if process is not None and process.poll() is None:
            return True
        if not isinstance(health, dict) or health.get("token") != self._token:
            return False
        return _pid_alive(health.get("pid"))

    def _command(self) -> list[str]:
        script = project_root() / "scripts" / "generate_pillar_cad.py"
        argv = [sys.executable, str(script), "--serve", str(self.directory)]
        if self.stop_file is not None:
            argv.extend(["--stop-file", str(self.stop_file)])
        return argv

    def _spawn(self):
        env = os.environ.copy()
        env[_TOKEN_ENV] = self._token
        env[_LOG_ENV] = str(self.directory / WORKER_LOG)
        if self._recycle_launch:
            env[_RECYCLE_ENV] = "1"
        else:
            env.pop(_RECYCLE_ENV, None)
        kwargs = discovery_stdio_kwargs()
        kwargs["cwd"] = str(project_root())
        kwargs["env"] = env
        if os.name != "nt":
            kwargs["start_new_session"] = True
        return subprocess.Popen(self._command(), **kwargs)

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout_s
        while time.monotonic() < deadline:
            health = _read_json(self.directory / "health.json")
            if self._matches(health) and self._worker_alive(health):
                return
            if not self._worker_alive(health):
                self._fail_ready("exited before it was ready", health)
            time.sleep(0.05)
        health = _read_json(self.directory / "health.json")
        self._fail_ready("did not become ready", health)

    def _fail_ready(self, reason: str, health) -> None:
        code = None if self._process is None else self._process.poll()
        shown = health if isinstance(health, dict) else _read_json(self.directory / "health.json")
        self.stop_started()
        body = json.dumps(shown, sort_keys=True) if isinstance(shown, dict) else "missing"
        detail = reason
        if reason.startswith("exited") and code is not None:
            detail = f"{reason} (exit {code})"
        raise RuntimeError(f"geometry worker {detail}. health.json: {body}")

    def _stop_process(self, grace_s: float) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is not None:
            self._kill_interpreter(_read_json(self.directory / "health.json"))
            return
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            self.stop_started()

    def _kill_interpreter(self, health) -> None:
        if not isinstance(health, dict) or health.get("token") != self._token:
            return
        pid = health.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            return
        if self._process is not None and pid == self._process.pid:
            return
        children = _descendant_pids(pid)
        _kill_pid(pid)
        for child in children:
            _kill_pid(child)


def open_geometry_worker(data_root, *, enabled, stop_file=None, ready_timeout_s=READY_TIMEOUT_S):
    """Start the worker when ``enabled``. Return None when it is not."""
    if not enabled:
        return None
    supervisor = GeometryWorkerSupervisor(
        Path(data_root) / "geometry_worker",
        stop_file=stop_file,
        ready_timeout_s=ready_timeout_s,
    )
    supervisor.start()
    _chain_stop_signals(supervisor)
    return supervisor


def _install_stop_signals(stop: dict) -> list:
    if threading.current_thread() is not threading.main_thread():
        return []

    def _handler(signum, frame, stop=stop):
        stop["flag"] = True
        stop["signal"] = int(signum)

    installed = []
    for sig in _SIGNALS:
        try:
            previous = signal.signal(sig, _handler)
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


def _stop_cause(stop: dict, directory: Path, stop_file) -> str:
    """Why ``serve`` left its loop. ``shutdown`` in the log is not this string."""
    if stop.get("flag"):
        number = stop.get("signal")
        if number is None:
            return "stop_flag"
        return f"signal:{number}"
    if (directory / "shutdown").is_file():
        return "shutdown_file"
    if stop_file is not None and Path(stop_file).is_file():
        return "stop_file"
    return "loop_exit"


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


def _session_open(directory: Path) -> bool:
    health = _read_json(directory / "health.json")
    return isinstance(health, dict) and health.get("session_open") is True


def _first_request_timeout_message(directory: Path) -> str:
    health = _read_json(directory / "health.json")
    body = json.dumps(health, sort_keys=True) if isinstance(health, dict) else "missing"
    return f"geometry worker timed out waiting for the first request. health.json: {body}"


def _keepalive(directory: Path, session_keepalive, session_reason, token) -> str:
    try:
        alive = bool(session_keepalive())
    except Exception as exc:
        _log(directory, f"session closed: {type(exc).__name__}: {exc}")
        alive = False
        text = f"down: {type(exc).__name__}: {exc}"
    else:
        previous = _read_json(directory / "health.json")
        already_down = isinstance(previous, dict) and previous.get("session_open") is False
        if alive:
            text = "ok"
        else:
            reported = session_reason() if callable(session_reason) else None
            if not reported:
                reported = "keep-alive reported the session down"
            text = reported
            if not already_down:
                _log(directory, f"session closed: {reported}")
    _log(directory, f"keep-alive result={text}")
    _write_health(
        directory,
        status="ready",
        session_open=alive,
        token=token,
        session_reason=session_reason,
    )
    return text


def _write_health(directory: Path, *, status: str, session_open: bool, token, session_reason=None) -> None:
    previous = _read_json(directory / "health.json")
    _atomic_json(
        directory / "health.json",
        {
            "pid": os.getpid(),
            "token": token,
            "status": status,
            "session_open": bool(session_open),
        },
    )
    changed = (
        not isinstance(previous, dict)
        or previous.get("status") != status
        or previous.get("session_open") is not bool(session_open)
    )
    if not changed:
        return
    extra = ""
    if isinstance(previous, dict) and previous.get("session_open") is True and not session_open:
        if callable(session_reason):
            reason = session_reason()
            if reason:
                extra = f" reason={reason}"
    _log(directory, f"health status={status} session_open={str(bool(session_open)).lower()}{extra}")


def _log(directory: Path, message: str) -> None:
    path = Path(directory) / WORKER_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _pid_alive(pid) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _descendant_pids(pid: int) -> list[int]:
    """Child pids of ``pid``. Windows uses ``taskkill /T`` instead."""
    if os.name == "nt" or pid <= 0:
        return []
    found = []
    path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for part in text.split():
        try:
            child = int(part)
        except ValueError:
            continue
        found.append(child)
        found.extend(_descendant_pids(child))
    return found


def _kill_pid(pid: int) -> None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def _kill_group(pid: int) -> None:
    """Stop ``pid`` and processes started under it. A foreign group is left alone."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        if pid == os.getpgrp():
            return
    except OSError:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        _kill_pid(pid)


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
