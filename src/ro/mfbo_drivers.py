"""Subprocess drivers for one pillar evaluation.

Importing this module does not import Fluent or launch a solver. Each
driver runs one existing script and waits for it. A module lock and a
lock file under the data root keep those jobs from overlapping, including
across processes. ``RO_DATA_ROOT`` is set on the child environment only.
A non-zero exit raises ``DriverFailed`` with the child's log path.

MFP ids need no campaign geometry root. ``generate`` writes ``.pmdb`` into
this data root, and ``mesh`` calls ``mesh_mfbo_case``, which already sets
``geometry_suffix`` to ``.pmdb``. Campaign ``--geometry-root`` is not applied
here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ro.mfbo_adapter import Drivers, PillarDesign, resolve_data_root

_LOCK = threading.Lock()
_MESH_KNOBS = ("m_max", "m_min", "m_cpg", "bl_layers", "peel_layers")


class DriverFailed(RuntimeError):
    """A driver script exited non-zero, or did not start. ``log_path`` is the child log."""

    def __init__(self, stage: str, returncode: int | None, log_path: Path):
        self.stage = stage
        self.returncode = returncode
        self.log_path = log_path
        if returncode is None:
            detail = "did not start"
        else:
            detail = f"exited {returncode}"
        super().__init__(f"{stage} {detail}. Child log: {log_path}")


def repo_root() -> Path:
    """Repository root (this file is ``src/ro/mfbo_drivers.py``)."""
    return Path(__file__).resolve().parents[2]


def make_drivers(
    data_root: str | Path,
    *,
    runner: Callable[..., Any] | None = None,
    log_root: str | Path | None = None,
) -> Drivers:
    """Drivers that call the CAD, mesh, solve, and re-extract scripts."""
    root = resolve_data_root(data_root)
    logs = None if log_root is None else Path(log_root)
    worker = _Worker(root, runner if runner is not None else subprocess.run, logs)
    return Drivers(
        generate=worker.generate,
        mesh=worker.mesh,
        solve=worker.solve,
        extract=worker.extract,
    )


class _Worker:
    def __init__(self, data_root: Path, runner: Callable[..., Any], log_root: Path | None):
        self.data_root = data_root
        self.runner = runner
        self.log_root = log_root

    def generate(self, *, design: PillarDesign, geo_id: str, out_dir: Path) -> None:
        script = _script("scripts/generate_pillar_cad.py")
        argv = [
            sys.executable,
            str(script),
            "--d-p-mm",
            _cli_number(design.d_p_mm),
            "--d-h-mm",
            _cli_number(design.d_h_mm),
            "--d-f-mm",
            _cli_number(design.d_f_mm),
            "--geo-id",
            geo_id,
            "--out-dir",
            str(out_dir),
        ]
        self._run("generate", argv, geo_id)

    def mesh(
        self,
        *,
        design: PillarDesign,
        geo_id: str,
        mesh_id: str,
        mesh_settings: Mapping[str, Any],
        mesh_dir: Path,
    ) -> None:
        """Mesh one MFP id from the ``.pmdb`` already in this data root.

        ``mesh_mfbo_case`` sets ``geometry_suffix`` to ``.pmdb``. Campaign
        geometries that live under a separate ``geometry_root`` are not
        selected here.
        """
        del design, mesh_dir
        script = _script("scripts/mfbo/mesh_mfbo_case.py")
        settings = _mesh_settings(mesh_settings)
        argv = [
            sys.executable,
            str(script),
            "--data-root",
            _root_text(self.data_root),
            "--geo-id",
            geo_id,
            "--mesh-id",
            mesh_id,
            "--m-max",
            _cli_number(settings["m_max"]),
            "--m-min",
            _cli_number(settings["m_min"]),
            "--m-cpg",
            str(settings["m_cpg"]),
            "--bl-layers",
            str(settings["bl_layers"]),
            "--peel-layers",
            str(settings["peel_layers"]),
        ]
        if "spacer_bl_layers" in settings:
            argv.extend(["--spacer-bl-layers", str(settings["spacer_bl_layers"])])
        self._run("mesh", argv, geo_id, mesh_id)

    def solve(
        self,
        *,
        design: PillarDesign,
        geo_id: str,
        mesh_id: str,
        run_id: str,
        run_dir: Path,
    ) -> None:
        del design, run_dir
        script = _script("scripts/mfbo/solve_mfbo_case.py")
        argv = [
            sys.executable,
            str(script),
            "--data-root",
            _root_text(self.data_root),
            "--geo-id",
            geo_id,
            "--mesh-id",
            mesh_id,
            "--case",
            run_id,
        ]
        self._run("solve", argv, geo_id, mesh_id, run_id)

    def extract(
        self,
        *,
        design: PillarDesign,
        geo_id: str,
        mesh_id: str,
        run_id: str,
        run_dir: Path,
    ) -> None:
        del design, geo_id, mesh_id, run_id
        script = _script("scripts/mfbo/reextract_runs.py")
        argv = [sys.executable, str(script), str(run_dir)]
        self._run("extract", argv, run_dir.name)

    def _run(self, stage: str, argv: list[str], *log_parts: str) -> None:
        log_path = self._log_path(stage, *log_parts)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["RO_DATA_ROOT"] = _root_text(self.data_root)
        with _LOCK:
            with _job_slot(log_path.parent / "job.lock"):
                try:
                    with log_path.open("wb") as handle:
                        completed = self.runner(
                            argv,
                            cwd=str(repo_root()),
                            env=env,
                            stdout=handle,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            shell=False,
                            check=False,
                        )
                except OSError as exc:
                    with log_path.open("ab") as handle:
                        message = f"failed to start: {exc}\n"
                        handle.write(message.encode("utf-8", "replace"))
                    raise DriverFailed(stage, None, log_path) from exc
        code = getattr(completed, "returncode", None)
        if code != 0:
            raise DriverFailed(stage, code, log_path)

    def _log_path(self, stage: str, *parts: str) -> Path:
        directory = self.log_root if self.log_root is not None else self.data_root / "driver_logs"
        name = "__".join(_safe(part) for part in (stage, *parts)) + ".log"
        return directory / name


def _script(relative: str) -> Path:
    path = repo_root() / relative
    if not path.is_file():
        raise FileNotFoundError(f"driver script not found: {path}")
    return path


def _mesh_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise TypeError(
            f"mesh_settings must be a mapping, got {type(settings).__name__}."
        )
    missing = [key for key in _MESH_KNOBS if key not in settings]
    if missing:
        raise KeyError(f"mesh_settings missing {missing}.")
    checked = {key: settings[key] for key in _MESH_KNOBS}
    if "spacer_bl_layers" in settings and settings["spacer_bl_layers"] is not None:
        checked["spacer_bl_layers"] = settings["spacer_bl_layers"]
    return checked


def _cli_number(value: float) -> str:
    return format(float(value), ".12g")


def _root_text(root: Path) -> str:
    return root.as_posix()


def _safe(value: str) -> str:
    text = str(value)
    cleaned = []
    for char in text:
        if char.isalnum() or char in "._-":
            cleaned.append(char)
        else:
            cleaned.append("_")
    token = "".join(cleaned).strip("._")
    if not token:
        raise ValueError(f"log name part is empty, got {value!r}.")
    return token


class _JobSlot:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> _JobSlot:
        _acquire_lock(self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _release_lock(self.path)


def _job_slot(path: Path) -> _JobSlot:
    return _JobSlot(path)


def _acquire_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{os.getpid()}\n".encode("ascii")
    for _attempt in range(5):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _read_lock_pid(path)
            if _pid_is_alive(holder):
                raise RuntimeError(
                    "another Fluent/Discovery driver job is running "
                    f"(pid {holder}). Lock: {path}"
                )
            path.unlink()
            continue
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(f"short write to lock {path}")
        except OSError:
            os.close(fd)
            if path.is_file():
                path.unlink()
            raise
        os.close(fd)
        return
    raise RuntimeError(f"could not acquire driver lock {path}")


def _release_lock(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"driver lock disappeared before release: {path}")
    holder = _read_lock_pid(path)
    if holder != os.getpid():
        raise RuntimeError(
            f"refusing to remove driver lock {path} held by pid {holder}."
        )
    path.unlink()


def _read_lock_pid(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"could not read driver lock {path}: {exc}") from exc
    if not text.isdigit():
        raise RuntimeError(f"driver lock {path} does not hold a pid: {text!r}")
    pid = int(text)
    if pid <= 0:
        raise RuntimeError(f"driver lock {path} does not hold a pid: {text!r}")
    return pid


def _pid_is_alive(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError(f"pid must be a positive integer, got {pid!r}.")
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError as exc:
        raise RuntimeError(f"could not check pid {pid}: {exc}") from exc
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False
    process_query_limited_information = 0x1000
    still_active = 259
    error_invalid_parameter = 87
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = ctypes.WINFUNCTYPE(
        wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
    )(("OpenProcess", kernel32))
    get_exit_code = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
    )(("GetExitCodeProcess", kernel32))
    close_handle = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(
        ("CloseHandle", kernel32)
    )
    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return False
        if error == error_access_denied:
            return True
        raise RuntimeError(f"could not check pid {pid}: winerror {error}")
    try:
        code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(code)):
            error = ctypes.get_last_error()
            raise RuntimeError(
                f"could not read exit code for pid {pid}: winerror {error}"
            )
        return int(code.value) == still_active
    finally:
        close_handle(handle)
