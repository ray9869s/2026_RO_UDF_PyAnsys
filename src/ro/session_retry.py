"""Shared classification of Fluent session-death failures.

Socket reset, Scheme heap corruption, and launch/spawn death are retryable
because they kill the session. Residual/QoI/UDF/G/inlet-readback/manifest
failures are not. A session signature anywhere in the combined output wins
over a wrapped non-retryable message: extract re-raises Scheme faults inside
Canonical CP / load-bearing errors, so the outermost exception is not the
classifier input.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from typing import Optional

from ro.child_output import subprocess_text_kwargs

RETRY_KIND_SOCKET_RESET = "session_socket_reset"
RETRY_KIND_SCHEME_HEAP = "scheme_heap_corruption"
RETRY_KIND_LAUNCH_SPAWN = "fluent_launch_spawn"

SESSION_SOCKET_RESET_PATTERNS = (
    re.compile(r"IOCP/Socket", re.IGNORECASE),
    re.compile(r"Connection reset", re.IGNORECASE),
    re.compile(r"\b10054\b"),
    re.compile(r"forcibly closed", re.IGNORECASE),
)
SCHEME_HEAP_CORRUPTION_PATTERNS = (
    re.compile(r"wta\(1st\) to string->symbol", re.IGNORECASE),
    re.compile(r"#\[free", re.IGNORECASE),
    re.compile(r"Attempt to mark a free block", re.IGNORECASE),
    re.compile(r"Error encountered in critical code section", re.IGNORECASE),
)
FLUENT_LAUNCH_SPAWN_PATTERNS = (
    re.compile(r"LaunchFluentError"),
    re.compile(r"Deadline Exceeded", re.IGNORECASE),
    re.compile(r"Failed to construct hwtree", re.IGNORECASE),
    re.compile(r"Aborting:"),
)

SIFILE_RE = re.compile(r"-sifile(?:=|\s+)\"?([^\s\"]+)\"?", re.IGNORECASE)
FLUENT_LAUNCH_LINE_RE = re.compile(r"FLUENT_LAUNCH\b([^\n]*)")
FLUENT_LAUNCH_FIELD_RE = re.compile(r"(?<![A-Za-z_])(?:pid|cortex_pid|fluent_host_pid)=(\d+)\b")
# Image names a recorded root pid must have before it is killed (pid reuse
# guard). Descendants of a matching root are killed whatever their name.
FLUENT_ROOT_IMAGE_RE = re.compile(r"^(?:fluent|fl_mpi|cortex|cx\d|fl\d).*\.exe$", re.IGNORECASE)


def _any_pattern(text: str, patterns) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def is_session_socket_reset_failure(text: str) -> bool:
    if not text:
        return False
    return _any_pattern(text, SESSION_SOCKET_RESET_PATTERNS)


def classify_retryable_session_crash(text: str) -> Optional[str]:
    """Return a retry kind for a Fluent session death, else None.

    Searches the whole blob. Socket, Scheme heap, and launch/spawn signatures
    win even when wrapped in a higher-level RuntimeError.
    """
    if not text:
        return None
    if _any_pattern(text, SESSION_SOCKET_RESET_PATTERNS):
        return RETRY_KIND_SOCKET_RESET
    if _any_pattern(text, SCHEME_HEAP_CORRUPTION_PATTERNS):
        return RETRY_KIND_SCHEME_HEAP
    if _any_pattern(text, FLUENT_LAUNCH_SPAWN_PATTERNS):
        return RETRY_KIND_LAUNCH_SPAWN
    return None


def _positive_pid(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def session_process_ids(session) -> dict:
    """``{pid, cortex_pid, fluent_host_pid}`` for one PyFluent session.

    ``pid`` is the launcher handle PyFluent kept (on Windows it can exit at
    once, which is why a kill of it alone left fluent.exe / fl2510.exe
    running at 3ba17ec). ``cortex_pid`` and ``fluent_host_pid`` are Fluent's
    own processes from the connection properties. Missing values are None.
    """
    def attribute(obj, name):
        try:
            return getattr(obj, name, None)
        except Exception:  # noqa: BLE001 - a retired session raises on access
            return None

    process = attribute(session, "_process")
    connection = attribute(session, "_fluent_connection")
    properties = attribute(connection, "connection_properties") if connection is not None else None
    return {
        "pid": _positive_pid(attribute(process, "pid") if process is not None else None),
        "cortex_pid": _positive_pid(attribute(properties, "cortex_pid") if properties is not None else None),
        "fluent_host_pid": _positive_pid(
            attribute(properties, "fluent_host_pid") if properties is not None else None
        ),
    }


def format_fluent_launch_line(session) -> str:
    """One line naming the Fluent processes this session launched.

    ``pid`` (launcher handle), ``cortex_pid`` and ``fluent_host_pid``; each
    is ``unknown`` when PyFluent does not expose it. A failed attempt stops
    the process trees rooted at these PIDs only.
    """
    ids = session_process_ids(session)
    return "FLUENT_LAUNCH " + " ".join(
        f"{key}={ids[key] if ids[key] is not None else 'unknown'}"
        for key in ("pid", "cortex_pid", "fluent_host_pid")
    )


# Fluent PIDs launched by this worker process, for the cleanup after exit().
_LAUNCHED_PIDS: list[int] = []


def print_fluent_launch_identity(session) -> None:
    """Write the launch line to stdout before any Scheme command.

    The PIDs are also kept for ``stop_launched_fluent`` in the worker's
    cleanup.
    """
    print(format_fluent_launch_line(session), flush=True)
    for pid in session_process_ids(session).values():
        if pid is not None and pid not in _LAUNCHED_PIDS:
            _LAUNCHED_PIDS.append(pid)


def stop_launched_fluent(*, grace_s=30.0, lister=None, runner=None) -> list[str]:
    """After ``exit()``: give this worker's Fluent trees ``grace_s`` to go, then kill and verify.

    Prints and returns the lines. Never raises: cleanup must not mask the
    worker's own error.
    """
    if not _LAUNCHED_PIDS:
        return []
    try:
        lines = stop_recorded_fluent_tree(
            list(_LAUNCHED_PIDS), lister=lister, runner=runner, grace_s=grace_s,
            label="FLUENT_EXIT",
        )
    except Exception as exc:  # noqa: BLE001
        lines = [f"FLUENT_EXIT: cleanup check failed: {type(exc).__name__}: {exc}"]
    for line in lines:
        print(line, flush=True)
    return lines


def parse_fluent_launch_pids(text: str) -> list[int]:
    """PIDs from ``FLUENT_LAUNCH`` lines. Other numbers are ignored."""
    pids: list[int] = []
    seen: set[int] = set()
    found = [
        field.group(1)
        for line in FLUENT_LAUNCH_LINE_RE.finditer(text or "")
        for field in FLUENT_LAUNCH_FIELD_RE.finditer(line.group(1))
    ]
    for value in found:
        pid = int(value)
        if pid <= 0 or pid == os.getpid() or pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    return pids


def stop_recorded_fluent_tree(pids, *, lister=None, runner=None, verify_kill_s=None,
                              grace_s=0.0, label="ORPHAN") -> list[str]:
    """Stop the process trees rooted at recorded Fluent PIDs; verify they are gone.

    The process table is read once: a recorded PID that is not running is
    skipped, and a running one whose image is not Fluent-like (PID reuse) is
    left alone and named. Each remaining root's tree (every descendant) is
    listed, given ``grace_s`` to exit on its own, then killed (``taskkill
    /T /F`` per member on Windows, SIGKILL elsewhere) and checked with
    ``ro.process_tree.wait_gone``. The last line is ``KILL_VERIFIED`` or
    ``KILL_INCOMPLETE`` from that check, never taskkill's own text.
    """
    from ro.process_tree import (
        VERIFY_KILL_S,
        kill_verification_line,
        list_processes,
        run_taskkill,
        tree_pids,
        wait_gone,
    )

    lister = lister or list_processes
    verify_kill_s = VERIFY_KILL_S if verify_kill_s is None else verify_kill_s
    pids = [pid for pid in (_positive_pid(value) for value in pids) if pid and pid != os.getpid()]
    if not pids:
        return []
    try:
        table = lister()
    except Exception as exc:  # noqa: BLE001
        return [
            f"{label}: recorded Fluent pids {pids}: process list failed "
            f"({type(exc).__name__}: {exc}); KILL_INCOMPLETE survivors=unknown "
            f"pids={','.join(map(str, pids))} tree_listing_error={str(exc)!r}"
        ]
    by_pid = {}
    for process in table:
        try:
            by_pid[int(process.get("ProcessId"))] = process
        except (TypeError, ValueError):
            continue
    roots, messages = [], []
    for pid in pids:
        process = by_pid.get(pid)
        if process is None:
            continue
        name = str(process.get("Name") or "")
        if name and FLUENT_ROOT_IMAGE_RE.match(name) is None:
            messages.append(f"{label}: recorded pid {pid} is now {name!r}, not Fluent; left alone.")
            continue
        roots.append(pid)
    if not roots:
        messages.append(f"{label}: recorded Fluent pids {pids} are not running.")
        return messages
    tree = tree_pids(roots, table)
    if grace_s and grace_s > 0:
        survivors, error = wait_gone(tree, lister=lister, timeout_s=grace_s)
        if not survivors and error is None:
            messages.append(
                f"{label}: recorded Fluent tree exited on its own; "
                + kill_verification_line(tree, [])
            )
            return messages
        tree = set(survivors) if error is None else tree
    for pid in sorted(tree):
        if os.name == "nt":
            run_taskkill(pid, runner=runner)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    survivors, error = wait_gone(tree, lister=lister, timeout_s=verify_kill_s)
    messages.append(
        f"{label}: stopped recorded Fluent roots {roots} and their tree; "
        + kill_verification_line(tree, survivors, verify_error=error)
    )
    return messages


def terminate_recorded_fluent_pids(
    evidence: str,
    *,
    killer=None,
    runner=None,
    lister=None,
    verify_kill_s=None,
) -> list[str]:
    """Stop Fluent processes this attempt recorded at launch.

    PIDs that are not on a ``FLUENT_LAUNCH`` line are left alone, including
    other Fluent sessions and a PID that appears only in a transcript name.
    Without ``killer`` this is ``stop_recorded_fluent_tree`` (tree kill plus
    verification).
    """
    messages: list[str] = []
    if killer is None:
        return stop_recorded_fluent_tree(
            parse_fluent_launch_pids(evidence),
            lister=lister,
            runner=runner,
            verify_kill_s=verify_kill_s,
        )
    for pid in parse_fluent_launch_pids(evidence):
        outcome = killer(pid)
        if isinstance(outcome, str):
            messages.append(outcome)
        else:
            messages.append(f"ORPHAN: terminated Fluent pid {pid}.")
    return messages


def _kill_recorded_fluent_pid(pid: int, *, runner=None) -> str:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return f"ORPHAN: refused to terminate pid {pid}."
    if runner is None:
        runner = subprocess.run
    if os.name == "nt":
        from ro.process_tree import run_taskkill

        result = run_taskkill(pid, runner=runner)
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        suffix = f" {detail}" if detail else ""
        return f"ORPHAN: terminated Fluent pid {pid} with taskkill /T.{suffix}"
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return f"ORPHAN: Fluent pid {pid} was already gone."
    except PermissionError as exc:
        return f"ORPHAN: could not terminate Fluent pid {pid}: {exc}"
    return f"ORPHAN: terminated Fluent pid {pid}."


def parse_sifile_names(text: str) -> list[str]:
    """Unique server-info filenames from a Fluent launch command blob."""
    names: list[str] = []
    seen: set[str] = set()
    for match in SIFILE_RE.finditer(text or ""):
        token = match.group(1).strip().strip('"')
        name = token.replace("\\", "/").rsplit("/", 1)[-1]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def list_processes_matching_sifile(sifile_name: str, *, runner=None) -> tuple[list[str], Optional[str]]:
    """Return (matching process lines, scan_error).

    scan_error is set when the process list could not be read. That is not
    the same as an empty match list.
    """
    if runner is None:
        runner = subprocess.run
    sifile_name = str(sifile_name)
    try:
        if os.name == "nt":
            result = runner(
                ["wmic", "process", "get", "ProcessId,CommandLine", "/FORMAT:LIST"],
                capture_output=True,
                check=False,
                **subprocess_text_kwargs(),
            )
        else:
            result = runner(
                ["ps", "-eo", "pid=,args="],
                capture_output=True,
                check=False,
                **subprocess_text_kwargs(),
            )
    except OSError as exc:
        return [], f"{type(exc).__name__}: {exc}"
    raw = (result.stdout or "") + "\n" + (result.stderr or "")
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and sifile_name in stripped:
            lines.append(stripped)
    return lines, None


def describe_attempt_orphans(evidence: str, *, process_lister=None) -> list[str]:
    """Log lines for Fluent leftover from this attempt only.

    Targeting is the unique -sifile name in this attempt's output. A
    hand-opened Fluent session does not share that server-info file, so it
    is not listed. This helper does not kill anything.
    """
    messages: list[str] = []
    names = parse_sifile_names(evidence)
    if not names and parse_fluent_launch_pids(evidence):
        return messages  # the recorded FLUENT_LAUNCH pids are stopped and verified
    if not names:
        messages.append(
            "ORPHAN: this attempt's Fluent may still be running, but the "
            "output has no -sifile token, so the PID cannot be targeted "
            "without risking other Fluent sessions. Leave cleanup manual."
        )
        return messages
    for name in names:
        if process_lister is None:
            matches, scan_error = list_processes_matching_sifile(name)
        else:
            try:
                listed = process_lister(name)
                if isinstance(listed, tuple):
                    matches, scan_error = listed
                else:
                    matches, scan_error = list(listed or []), None
            except OSError as exc:
                matches, scan_error = [], f"{type(exc).__name__}: {exc}"
        if scan_error:
            messages.append(
                f"ORPHAN: -sifile {name} is in this attempt's output, but the "
                f"process list could not be read ({scan_error}). Not treating "
                "that as 'no process'."
            )
            continue
        if not matches:
            messages.append(
                f"ORPHAN: no running process command line contains this "
                f"attempt's -sifile {name}. If fluent.exe is still visible, "
                "leave cleanup manual."
            )
            continue
        messages.append(
            f"ORPHAN: {len(matches)} process(es) match this attempt's "
            f"-sifile {name}. Terminate only these PIDs; do not kill other "
            "fluent.exe sessions:"
        )
        for line in matches:
            messages.append(f"  ORPHAN-PID: {line}")
    return messages
