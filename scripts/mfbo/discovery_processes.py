"""Discovery and SpaceClaim processes started by one conversion or parity job.

Listing and terminating happen only when a job asks. Importing this module
does not launch Discovery, SpaceClaim, or Fluent, and it does not read a
data root. A pid that was already running is left alone.
"""

from __future__ import annotations

import csv
import io
import os
import signal
import subprocess

_MARKERS = ("discovery", "spaceclaim")


def _matches(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _MARKERS)


def processes_from_tasklist(text: str) -> list[dict]:
    """Parse ``tasklist /FO CSV /NH`` and keep Discovery and SpaceClaim rows."""
    found = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not _matches(" ".join(row)):
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        found.append({"pid": pid, "line": ",".join(row)})
    return found


def processes_from_ps(text: str) -> list[dict]:
    """Parse ``ps -eo pid=,args=`` and keep Discovery and SpaceClaim rows."""
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not _matches(stripped):
            continue
        pid_text = stripped.split(None, 1)[0]
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        found.append({"pid": pid, "line": stripped})
    return found


def list_discovery_processes(runner=None) -> list[dict]:
    """Currently running Discovery and SpaceClaim processes."""
    if runner is None:
        runner = subprocess.run
    try:
        if os.name == "nt":
            result = runner(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return processes_from_tasklist(result.stdout or "")
        result = runner(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
        return processes_from_ps(result.stdout or "")
    except OSError as exc:
        raise RuntimeError(f"Could not list Discovery processes: {exc}") from exc


def terminate_pid(pid: int) -> None:
    """Stop one process this job started. An already-exited pid is not an error."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError(f"pid must be a positive int, got {pid!r}.")
    if pid == os.getpid():
        raise RuntimeError(f"Refusing to terminate this process, pid {pid}.")
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        message = ((completed.stderr or "") + (completed.stdout or "")).casefold()
        if completed.returncode != 0 and "not found" not in message:
            raise RuntimeError(
                f"taskkill pid {pid} returned {completed.returncode}: "
                f"{(completed.stderr or completed.stdout or '').strip()}"
            )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def terminate_started(before, after, killer=None) -> list[int]:
    """Terminate pids present in ``after`` and absent from ``before``."""
    if killer is None:
        killer = terminate_pid
    already = {item["pid"] for item in before}
    killed = []
    for item in after:
        pid = item["pid"]
        if pid in already or pid in killed:
            continue
        killer(pid)
        killed.append(pid)
    return killed


def process_note(before, after, terminated, attempts: int) -> dict:
    return {
        "attempts": attempts,
        "processes_before": [item["line"] for item in before],
        "processes_after": [item["line"] for item in after],
        "terminated_pids": list(terminated),
    }
