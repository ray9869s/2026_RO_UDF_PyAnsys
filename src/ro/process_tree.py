"""Snapshot a process tree before a kill and check that it is gone after.

``taskkill /T /F`` and ``SIGKILL`` report what they attempted, not what
died. A watchdog or queue timeout that kills a Fluent job lists the tree
first (the root PIDs and every descendant), kills, then polls until none
of those PIDs is alive, and says which survived. Listing uses the Windows
CIM process table on the host and ``/proc`` elsewhere. A listing that
fails is reported, never treated as "all gone".
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

VERIFY_KILL_S = 30.0
VERIFY_POLL_S = 0.5
# taskkill /T walks the tree and can stall on a hung process or a busy
# WMI; a kill path must never wait on it without limit.
TASKKILL_TIMEOUT_S = 60.0
TASKKILL_TIMEOUT_RETURNCODE = -9


def run_taskkill(pid, *, runner=None, timeout_s=TASKKILL_TIMEOUT_S):
    """``taskkill /PID <pid> /T /F`` bounded by ``timeout_s``.

    Returns the completed process. On a timeout the result has return code
    ``TASKKILL_TIMEOUT_RETURNCODE`` and says so in ``stderr``; it does not
    raise. Callers still verify the tree with ``wait_gone``.
    """
    from ro.child_output import subprocess_text_kwargs

    argv = ["taskkill", "/PID", str(pid), "/T", "/F"]
    runner = subprocess.run if runner is None else runner
    try:
        return runner(
            argv,
            capture_output=True,
            check=False,
            timeout=float(timeout_s),
            **subprocess_text_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv,
            TASKKILL_TIMEOUT_RETURNCODE,
            stdout="",
            stderr=f"taskkill pid {pid} timed out after {float(timeout_s):g} s",
        )


def _posix_processes():
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # pid (comm) state ppid ...; comm may contain spaces/parentheses.
        tail = stat.rsplit(")", 1)[-1].split()
        if len(tail) < 2:
            continue
        if tail[0] in ("Z", "X"):
            continue  # exited, waiting to be reaped
        processes.append({"ProcessId": int(entry.name), "ParentProcessId": int(tail[1])})
    return processes


def list_processes():
    """``[{ProcessId, ParentProcessId, ...}]``; raises when it cannot list."""
    if os.name == "nt":
        from ro.host_lock import windows_processes

        return windows_processes()
    if not Path("/proc").is_dir():
        raise RuntimeError("no /proc: cannot list processes")
    return _posix_processes()


def _pid(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def tree_pids(roots, processes) -> set[int]:
    """``roots`` plus every live descendant in ``processes``."""
    children = {}
    for process in processes:
        pid, parent = _pid(process.get("ProcessId")), _pid(process.get("ParentProcessId"))
        if pid is not None and parent is not None:
            children.setdefault(parent, set()).add(pid)
    found = {int(pid) for pid in roots}
    frontier = list(found)
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    found.discard(os.getpid())
    return found


def snapshot_tree(roots, *, lister=list_processes):
    """``(pids, error)``. On a listing failure, the roots and the error text."""
    try:
        return tree_pids(roots, lister()), None
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        pids = {int(pid) for pid in roots}
        pids.discard(os.getpid())
        return pids, f"{type(exc).__name__}: {exc}"


def wait_gone(pids, *, lister=list_processes, timeout_s=VERIFY_KILL_S,
              poll_s=VERIFY_POLL_S, sleep=time.sleep, clock=time.monotonic):
    """``(survivors, error)`` after waiting up to ``timeout_s`` for ``pids`` to exit."""
    pids = {int(pid) for pid in pids}
    deadline = clock() + float(timeout_s)
    while True:
        try:
            live = {_pid(process.get("ProcessId")) for process in lister()}
        except Exception as exc:  # noqa: BLE001
            return sorted(pids), f"{type(exc).__name__}: {exc}"
        survivors = sorted(pids & live)
        if not survivors or clock() >= deadline:
            return survivors, None
        sleep(min(float(poll_s), max(0.0, deadline - clock())))


def kill_verification_line(tree, survivors, *, snapshot_error=None, verify_error=None) -> str:
    """``KILL_VERIFIED pids=...`` or ``KILL_INCOMPLETE survivors=... pids=...``.

    Verified only when the pre-kill tree listing succeeded (``tree`` is the
    whole tree, not just the roots) and every listed pid is gone. A failed
    listing before or after the kill is ``KILL_INCOMPLETE``.
    """
    pids = ",".join(str(pid) for pid in sorted(tree))
    notes = ""
    if snapshot_error:
        notes += f" tree_listing_error={snapshot_error!r}"
    if verify_error:
        notes += f" verify_listing_error={verify_error!r}"
    if snapshot_error or verify_error or survivors:
        alive = ",".join(str(pid) for pid in survivors)
        return f"KILL_INCOMPLETE survivors={alive} pids={pids}{notes}"
    return f"KILL_VERIFIED pids={pids}{notes}"
