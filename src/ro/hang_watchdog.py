"""Stop a Fluent job whose log and transcript have stopped growing.

The queue waits on the driver process. A stalled extract or solve never
returns, so the next campaign job never starts. This watcher treats a
quiet attempt log, Fluent transcript, and ``post/`` tree as a hang, kills
only the PIDs that job recorded, and returns a distinct exit code so the
driver can fail the stage and the queue can move on.

Extract is retried once by the driver. Solve is not.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from ro.child_output import subprocess_text_kwargs

from ro.session_retry import (
    _kill_recorded_fluent_pid,
    parse_fluent_launch_pids,
)

HANG_EXIT_CODE = 75
FAILURE_CLASS_HANG = "hang"
_HANG_LINE_RE = re.compile(
    r"^HANG failure_class=hang stage=(extract|solve) "
    r"idle_s=(\S+) killed_pids=([0-9,]*)\s*$",
    re.MULTILINE,
)


def format_hang_line(stage: str, idle_s: float, killed_pids) -> str:
    pids = ",".join(str(int(pid)) for pid in killed_pids)
    return (
        f"HANG failure_class=hang stage={stage} "
        f"idle_s={float(idle_s):g} killed_pids={pids}"
    )


def parse_hang_line(text: str):
    """The last HANG line in a driver log, or None."""
    matches = list(_HANG_LINE_RE.finditer(text or ""))
    if not matches:
        return None
    match = matches[-1]
    raw_pids = match.group(3)
    pids = [int(part) for part in raw_pids.split(",") if part]
    return {
        "failure_class": FAILURE_CLASS_HANG,
        "stage": match.group(1),
        "idle_s": float(match.group(2)),
        "killed_pids": pids,
    }


def progress_signature(paths) -> tuple:
    """Size and mtime of each path. A missing file is its own signature."""
    items = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            items.append((str(path), None))
            continue
        try:
            stat = path.stat()
        except OSError:
            items.append((str(path), None))
            continue
        items.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(items)


def progress_paths(log_path, extra_files=(), post_dir=None):
    """Attempt log, transcripts, and files under ``post/``."""
    paths = [Path(log_path)]
    paths.extend(Path(path) for path in extra_files)
    if post_dir is not None and Path(post_dir).is_dir():
        paths.extend(path for path in Path(post_dir).rglob("*") if path.is_file())
    return paths


def pids_to_stop(evidence: str, worker_pid) -> list[int]:
    """Fluent PIDs this attempt printed, plus the worker process.

    The current process is never included. A number that is not on a
    ``FLUENT_LAUNCH`` line is not included, apart from ``worker_pid``.
    """
    pids = parse_fluent_launch_pids(evidence)
    if (
        isinstance(worker_pid, int)
        and not isinstance(worker_pid, bool)
        and worker_pid > 0
        and worker_pid != os.getpid()
        and worker_pid not in pids
    ):
        pids.append(worker_pid)
    return pids


def stop_job_pids(pids, *, killer=None) -> list[str]:
    """Kill each pid. The default killer is the per-PID Fluent tree kill."""
    messages = []
    for pid in pids:
        if killer is None:
            messages.append(_kill_recorded_fluent_pid(pid))
        else:
            outcome = killer(pid)
            messages.append(
                outcome
                if isinstance(outcome, str)
                else f"ORPHAN: terminated pid {pid}."
            )
    return messages


def idle_seconds(stage: str) -> float:
    """Run-config minutes for this stage, converted to seconds."""
    from ro.paths import project_root
    import importlib.util

    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        f"hang_run_config_{stage}", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    name = (
        "extract_hang_idle_minutes"
        if stage == "extract"
        else "solve_hang_idle_minutes"
    )
    default = 30.0 if stage == "extract" else 60.0
    minutes = getattr(module, name, default)
    return float(minutes) * 60.0


def tee_until_hang(
    cmd,
    *,
    env,
    log_path,
    stage: str,
    idle_s: float,
    cwd=None,
    extra_files=(),
    popen_factory=None,
    killer=None,
    poll_s=None,
):
    """Tee the child to ``log_path`` and stdout. Kill it when progress stops.

    Progress is the attempt log, ``extra_files`` (Fluent transcripts), and
    any file under ``post/`` next to the log. The returned code is
    ``HANG_EXIT_CODE`` when the watcher fires.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    popen = popen_factory or subprocess.Popen
    proc = popen(
        cmd,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **subprocess_text_kwargs(),
    )
    assert proc.stdout is not None
    post_dir = log_path.parent / "post"
    interval = poll_s if poll_s is not None else min(5.0, max(0.05, float(idle_s) / 20.0))
    stop = threading.Event()
    state = {"hung": False, "messages": []}

    def watch():
        last = progress_signature(
            progress_paths(log_path, extra_files, post_dir)
        )
        last_change = time.monotonic()
        while not stop.is_set():
            if stop.wait(interval):
                return
            current = progress_signature(
                progress_paths(log_path, extra_files, post_dir)
            )
            now = time.monotonic()
            if current != last:
                last = current
                last_change = now
                continue
            if now - last_change < float(idle_s) or stop.is_set():
                continue
            evidence = ""
            try:
                evidence = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                evidence = ""
            pids = pids_to_stop(evidence, proc.pid)
            messages = stop_job_pids(pids, killer=killer)
            line = format_hang_line(stage, idle_s, pids)
            state["messages"] = [line, *messages]
            state["hung"] = True
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            return

    print(
        f"Hang watchdog: stage={stage} idle_s={float(idle_s):g}",
        flush=True,
    )
    thread = threading.Thread(target=watch, name=f"hang-watch-{stage}", daemon=True)
    thread.start()
    returncode = 1
    error = None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
            returncode = proc.wait()
    except Exception as exc:
        error = exc
    finally:
        stop.set()
        thread.join(timeout=max(2.0, interval * 2))
    if state["hung"]:
        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            for message in state["messages"]:
                log_file.write(message + "\n")
                print(message, flush=True)
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return subprocess.CompletedProcess(cmd, HANG_EXIT_CODE)
    if error is not None:
        raise error
    return subprocess.CompletedProcess(cmd, returncode)


def record_hang(run_directory, event, *, failed: bool) -> str:
    """Append one hang to the run manifest. Missing manifests are reported.

    ``failure_class`` is ``hang`` only when this stage is giving up.
    A retry keeps the event and leaves ``failure_class`` unset. Final
    case and data files are not touched.
    """
    from ro.manifest import ManifestError, read_run_manifest, write_run_manifest

    directory = Path(run_directory)
    try:
        payload = read_run_manifest(directory)
    except (ManifestError, OSError, ValueError) as exc:
        return f"hang not recorded on run manifest: {exc}"
    events = payload.get("hang_events")
    if not isinstance(events, list):
        events = []
    payload["hang_events"] = [*events, dict(event)]
    if failed:
        payload["failure_class"] = FAILURE_CLASS_HANG
    path = write_run_manifest(directory, payload)
    status = FAILURE_CLASS_HANG if failed else "recorded"
    return (
        f"Recorded hang on {path} failure_class={status} "
        f"killed_pids={list(event.get('killed_pids') or [])}."
    )
