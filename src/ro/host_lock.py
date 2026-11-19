"""One Fluent job per host, across every queue file.

``<queue>.lock`` only stops a second runner of the same queue. The host
lock is one file per machine (``RO_HOST_LOCK``, default
``~/.pyfluent/fluent_host.lock``) that every queue runner holds for its
lifetime. Before each job the runner also lists live Fluent processes
and refuses to start while one exists (an orphan from a killed job, or a
session started outside the queue). Importing this module starts nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

HOST_LOCK_ENV = "RO_HOST_LOCK"
ORPHAN_GRACE_ENV = "RO_ORPHAN_GRACE_S"
DEFAULT_ORPHAN_GRACE_S = 120.0
ORPHAN_POLL_S = 10.0
PROCESS_SCAN_ENV = "RO_PROCESS_SCAN"
FLUENT_PROCESS_RE = re.compile(r"^(?:fluent|fl_mpi|cortex|fl\d).*\.exe$", re.IGNORECASE)


def host_lock_path() -> Path:
    override = os.environ.get(HOST_LOCK_ENV)
    if override:
        return Path(override)
    return Path.home() / ".pyfluent" / "fluent_host.lock"


def windows_processes():
    """``[{ProcessId, ParentProcessId, Name, CommandLine}, ...]`` from Win32_Process."""
    if os.name != "nt":
        raise RuntimeError("Process listing requires the Windows host Python, not WSL.")
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine) | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        check=True,
        timeout=60,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(result.stdout.lstrip("﻿") or "[]")
    return payload if isinstance(payload, list) else [payload]


class ProcessScanError(RuntimeError):
    """The Windows process table could not be read."""


def scan_processes(lister=None):
    """``windows_processes()`` with a clear error, or ``[]`` under the override.

    A failing lister (PowerShell missing, CIM error, timeout, bad JSON) used
    to surface as a raw traceback. It now raises ``ProcessScanError`` that
    says what failed and how to override. ``RO_PROCESS_SCAN=skip`` returns
    ``[]`` with a printed warning: only after checking by hand (Task
    Manager) that no Fluent, cortex or fl_mpi process is running.
    """
    lister = windows_processes if lister is None else lister
    try:
        return lister()
    except Exception as exc:  # noqa: BLE001 - re-raised with context below
        detail = f"{type(exc).__name__}: {exc}"
        stderr = getattr(exc, "stderr", None)
        if stderr:
            detail += f" stderr={str(stderr).strip()[:500]!r}"
        if os.environ.get(PROCESS_SCAN_ENV, "").strip().lower() == "skip":
            print(
                f"WARNING: {PROCESS_SCAN_ENV}=skip: process listing failed ({detail}); "
                "continuing WITHOUT the live-Fluent check.",
                flush=True,
            )
            return []
        raise ProcessScanError(
            "Could not list Windows processes to check for a live Fluent session "
            f"({detail}). Check that powershell.exe and Get-CimInstance Win32_Process "
            "work in this shell. If they cannot, confirm by hand that no fluent/cortex/"
            f"fl_mpi process is running and set {PROCESS_SCAN_ENV}=skip for this run."
        ) from exc


def fluent_processes(processes=None):
    """Live Fluent/MPI/Cortex processes. Off Windows nothing is listed."""
    if processes is None:
        if os.name != "nt":
            return []
        processes = scan_processes()
    found = []
    for process in processes:
        name = str(process.get("Name") or "")
        if FLUENT_PROCESS_RE.match(name):
            found.append(f"pid={process.get('ProcessId')} {name}")
    return found


def orphan_grace_s() -> float:
    text = os.environ.get(ORPHAN_GRACE_ENV)
    if text in (None, ""):
        return DEFAULT_ORPHAN_GRACE_S
    value = float(text)
    if value < 0:
        raise ValueError(f"{ORPHAN_GRACE_ENV} must be >= 0, got {text!r}.")
    return value


def wait_for_no_fluent(*, lister=fluent_processes, grace_s=None, sleep=time.sleep, clock=time.monotonic):
    """Return [] once no Fluent process is live; else the list after ``grace_s``.

    The grace lets the previous job's solver finish exiting. A process
    still alive after it is an orphan, and the caller refuses to start.
    """
    grace = orphan_grace_s() if grace_s is None else grace_s
    deadline = clock() + grace
    while True:
        found = lister()
        if not found or clock() >= deadline:
            return found
        sleep(min(ORPHAN_POLL_S, max(0.0, deadline - clock())))
