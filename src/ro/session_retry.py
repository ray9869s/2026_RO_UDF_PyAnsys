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
import subprocess
from typing import Optional

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
                text=True,
                check=False,
            )
        else:
            result = runner(
                ["ps", "-eo", "pid=,args="],
                capture_output=True,
                text=True,
                check=False,
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
