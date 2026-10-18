"""One Discovery session with a backend timeout and a single retry.

Importing this module does not launch Discovery. Process listing is loaded
when a job asks for it. A pid that was already running is left alone.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

from ro.paths import project_root

BACKEND_START_TIMEOUT_S = 300
FAILURE_INFRASTRUCTURE = "infrastructure"
FAILURE_GEOMETRY = "geometry"

_INFRASTRUCTURE_NAMES = {
    "connectionerror",
    "connecttimeout",
    "connectionabortederror",
    "timeouterror",
    "timeout",
    "readtimeout",
}
_INFRASTRUCTURE_TEXT = (
    "timeout while waiting for backend",
    "backend to be ready",
    "waiting for backend",
    "connectionerror",
)


class DiscoveryAttemptError(RuntimeError):
    """The session failed. ``failure_class`` is infrastructure or geometry."""

    def __init__(self, failure_class: str, errors: list[str], note: dict | None):
        self.failure_class = failure_class
        self.errors = list(errors)
        self.note = note
        super().__init__("; ".join(self.errors))


def discovery_launch_arguments(api_version) -> dict:
    """Keywords for one hidden Discovery 25.1 session. Does not launch it."""
    return {
        "version": 251,
        "api_version": api_version,
        "hidden": True,
        "timeout": BACKEND_START_TIMEOUT_S,
    }


def failure_class_for_exception(exc: BaseException) -> str:
    """Backend timeout and Discovery start are infrastructure. Boolean and
    validation failures are geometry and are not retried.
    """
    names = {cls.__name__.casefold() for cls in type(exc).mro()}
    text = f"{type(exc).__name__}: {exc}".casefold()
    if names & _INFRASTRUCTURE_NAMES:
        return FAILURE_INFRASTRUCTURE
    if any(marker in text for marker in _INFRASTRUCTURE_TEXT):
        return FAILURE_INFRASTRUCTURE
    return FAILURE_GEOMETRY


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_discovery_job(
    build: Callable[[Any], dict],
    *,
    launch: Callable[[], Any],
    partial_paths: tuple[Path, ...] = (),
    process_lister: Callable[[], list[dict]] | None = None,
    process_killer: Callable[[int], None] | None = None,
) -> dict:
    """Run ``build(modeler)`` once, and once more after cleanup on infrastructure failure.

    ``build`` returns the meta payload. This function adds the process note
    and the backend timeout. A geometry failure is not retried.
    """
    processes = _discovery_processes()
    lister = processes.list_discovery_processes if process_lister is None else process_lister
    killer = processes.terminate_pid if process_killer is None else process_killer
    errors: list[str] = []
    note = None
    started_before = None
    terminated_all: list[int] = []
    for attempt in (1, 2):
        before = lister()
        if started_before is None:
            started_before = before
        modeler = None
        try:
            modeler = launch()
            payload = build(modeler)
        except Exception as exc:
            _close_quietly(modeler)
            after = lister()
            terminated_all.extend(processes.terminate_started(before, after, killer))
            note = processes.process_note(started_before, after, terminated_all, attempt)
            errors.append(f"{type(exc).__name__}: {exc}")
            kind = failure_class_for_exception(exc)
            if attempt == 2 or kind != FAILURE_INFRASTRUCTURE:
                raise DiscoveryAttemptError(kind, errors, note) from exc
            _delete_partials(partial_paths)
            continue
        close_error = _close_report(modeler)
        after = lister()
        terminated_all.extend(processes.terminate_started(before, after, killer))
        note = processes.process_note(started_before, after, terminated_all, attempt)
        if close_error is not None:
            errors.append(f"{type(close_error).__name__}: {close_error}")
            if attempt == 2:
                raise DiscoveryAttemptError(FAILURE_INFRASTRUCTURE, errors, note) from close_error
            _delete_partials(partial_paths)
            continue
        record = dict(payload)
        record.update(note)
        record["backend_start_timeout_s"] = BACKEND_START_TIMEOUT_S
        return record
    raise DiscoveryAttemptError(FAILURE_INFRASTRUCTURE, errors, note)


class DiscoverySession:
    """One Discovery process reused across builds.

    A launch or a build that loses the backend is cleaned up and retried
    once, the same as ``run_discovery_job``. A geometry failure leaves the
    process open. ``close`` is the only path that stops a live session.
    """

    def __init__(
        self,
        launch: Callable[[], Any],
        *,
        process_lister: Callable[[], list[dict]] | None = None,
        process_killer: Callable[[int], None] | None = None,
    ):
        processes = _discovery_processes()
        self._launch = launch
        self._lister = (
            processes.list_discovery_processes if process_lister is None else process_lister
        )
        self._killer = processes.terminate_pid if process_killer is None else process_killer
        self._processes = processes
        self.modeler = None
        self.note = None
        self._before = None
        self._terminated: list[int] = []
        self._completed = 0
        self._closed = False
        self.close_reason = None

    def run(
        self,
        build: Callable[[Any], dict],
        *,
        partial_paths: tuple[Path, ...] = (),
    ) -> tuple[dict, bool]:
        """Run ``build(modeler)``. Return the meta and whether this build reused a session.

        Infrastructure failures retry once after the existing cleanup. Geometry
        failures are not retried and do not close the session.
        """
        if self._closed:
            raise RuntimeError("Discovery session is closed.")
        errors: list[str] = []
        for attempt in (1, 2):
            reused = self.modeler is not None and self._completed > 0
            try:
                if self.modeler is None:
                    reused = False
                    self._open(attempt)
                payload = build(self.modeler)
            except Exception as exc:
                kind = failure_class_for_exception(exc)
                if self.modeler is None:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    if attempt == 2 or kind != FAILURE_INFRASTRUCTURE:
                        failure = DiscoveryAttemptError(kind, errors, self.note)
                        failure.session_reused = False
                        raise failure from exc
                    continue
                if kind != FAILURE_INFRASTRUCTURE:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    failure = DiscoveryAttemptError(kind, errors, self.note)
                    failure.session_reused = reused
                    raise failure from exc
                self._drop(errors, exc, attempt)
                if attempt == 2:
                    failure = DiscoveryAttemptError(FAILURE_INFRASTRUCTURE, errors, self.note)
                    failure.session_reused = False
                    raise failure from exc
                _delete_partials(partial_paths)
                continue
            if not isinstance(payload, dict):
                raise TypeError(
                    f"build must return a dict, got {type(payload).__name__}."
                )
            alive = payload.pop("_session_alive", True)
            self._completed += 1
            record = dict(payload)
            if self.note is not None:
                record.update(self.note)
            record["backend_start_timeout_s"] = BACKEND_START_TIMEOUT_S
            if alive is False:
                self._drop([], RuntimeError("design close failed"), attempt)
                self._completed = 0
            return record, reused
        failure = DiscoveryAttemptError(FAILURE_INFRASTRUCTURE, errors, self.note)
        failure.session_reused = False
        raise failure

    def touch(self) -> bool:
        """Cheap call so an idle session stays up. False when it has died."""
        if self._closed or self.modeler is None:
            return False
        try:
            _probe_modeler(self.modeler)
        except Exception as exc:
            self._drop([], exc, 1)
            return False
        return True

    def close(self) -> None:
        """Close the modeler and stop processes this session started."""
        if self._closed:
            return
        self._closed = True
        modeler = self.modeler
        self.modeler = None
        self._completed = 0
        if modeler is not None:
            self.close_reason = _close_text("shutdown", None, modeler)
        elif self.close_reason is None:
            self.close_reason = "shutdown"
        _close_quietly(modeler)
        if self._before is None:
            return
        after = self._lister()
        self._terminated.extend(
            self._processes.terminate_started(self._before, after, self._killer)
        )
        self.note = self._processes.process_note(
            self._before, after, self._terminated, 1
        )

    def _open(self, attempt: int) -> None:
        before = self._lister()
        if self._before is None:
            self._before = before
        try:
            self.modeler = self._launch()
        except Exception:
            after = self._lister()
            self._terminated.extend(
                self._processes.terminate_started(before, after, self._killer)
            )
            self.note = self._processes.process_note(
                self._before, after, self._terminated, attempt
            )
            self.modeler = None
            raise
        after = self._lister()
        self.note = self._processes.process_note(
            self._before, after, self._terminated, attempt
        )

    def _drop(self, errors: list[str], exc: BaseException, attempt: int) -> None:
        modeler = self.modeler
        self.modeler = None
        self._completed = 0
        self.close_reason = _close_text("session dropped", exc, modeler)
        _close_quietly(modeler)
        before = self._before if self._before is not None else []
        after = self._lister()
        self._terminated.extend(
            self._processes.terminate_started(before, after, self._killer)
        )
        self.note = self._processes.process_note(
            before, after, self._terminated, attempt
        )
        errors.append(f"{type(exc).__name__}: {exc}")


def error_record(geo_id: str, exc: DiscoveryAttemptError) -> dict:
    record = {
        "geo_id": geo_id,
        "status": "failed",
        "failure_class": exc.failure_class,
        "error": str(exc),
        "backend_start_timeout_s": BACKEND_START_TIMEOUT_S,
    }
    if exc.note is not None:
        record.update(exc.note)
    return record


def _discovery_processes():
    path = project_root() / "scripts" / "mfbo" / "discovery_processes.py"
    spec = importlib.util.spec_from_file_location("discovery_processes_for_cad", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Discovery process helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe_modeler(modeler) -> None:
    """One inexpensive backend call. ``ping`` wins when the modeler has it."""
    probe = getattr(modeler, "ping", None)
    if callable(probe):
        probe()
        return
    client = getattr(modeler, "client", None)
    if client is None:
        return
    getattr(client, "backend_version")


def _modeler_exit_code(modeler):
    if modeler is None:
        return None
    for name in ("returncode", "exit_code"):
        value = getattr(modeler, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    process = getattr(modeler, "process", None)
    if process is None:
        return None
    value = getattr(process, "returncode", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _close_text(prefix: str, exc: BaseException | None, modeler) -> str:
    text = prefix
    if exc is not None:
        text = f"{prefix}: {type(exc).__name__}: {exc}"
    code = _modeler_exit_code(modeler)
    if code is not None:
        text = f"{text}; discovery_exit_code={code}"
    return text


def _close_quietly(modeler) -> None:
    if modeler is None:
        return
    try:
        modeler.close()
    except Exception:
        return


def _close_report(modeler):
    if modeler is None:
        return None
    try:
        modeler.close()
    except Exception as exc:
        return exc
    return None


def _delete_partials(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()
