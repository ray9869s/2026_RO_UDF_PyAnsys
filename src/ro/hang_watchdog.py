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
import queue
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
    r"^HANG failure_class=hang stage=(extract|solve|mesh) "
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


_IDLE_CONFIG = {
    "extract": ("extract_hang_idle_minutes", 30.0),
    "solve": ("solve_hang_idle_minutes", 60.0),
    "mesh": ("mesh_hang_idle_minutes", 90.0),
}
# Per-attempt wall-clock caps. Any output resets the idle timer, so a
# child that loops while printing is only stopped by this cap.
_WALL_CONFIG = {
    "extract": ("extract_wall_cap_hours", 6.0),
    "solve": ("solve_wall_cap_hours", 48.0),
    "mesh": ("mesh_wall_cap_hours", 12.0),
}
# After the worker exits, stop reading a pipe a grandchild still holds.
DRAIN_AFTER_EXIT_S = 30.0
_EOF = object()


def _run_config_value(stage: str, table):
    from ro.paths import project_root
    import importlib.util

    if stage not in table:
        raise ValueError(f"unknown watchdog stage {stage!r}")
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        f"hang_run_config_{stage}", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    name, default = table[stage]
    return float(getattr(module, name, default))


def idle_seconds(stage: str) -> float:
    """Run-config minutes for this stage, converted to seconds."""
    return _run_config_value(stage, _IDLE_CONFIG) * 60.0


def wall_cap_seconds(stage: str) -> float:
    """Run-config per-attempt wall-clock cap for this stage, in seconds."""
    return _run_config_value(stage, _WALL_CONFIG) * 3600.0


def format_wall_cap_line(stage: str, wall_s: float) -> str:
    return f"HANG_REASON wall_cap stage={stage} wall_s={float(wall_s):g}"


def _close_quietly(stream):
    try:
        stream.close()
    except Exception:  # noqa: BLE001 - best effort after a kill
        pass


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
    wall_s=None,
    drain_s=DRAIN_AFTER_EXIT_S,
):
    """Tee the child to ``log_path`` and stdout. Kill it when progress stops.

    Progress is the attempt log, ``extra_files`` (Fluent transcripts), and
    any file under ``post/`` next to the log. ``wall_s`` caps the whole
    attempt even while output keeps flowing. The returned code is
    ``HANG_EXIT_CODE`` when either fires.

    The child's stdout is read on a separate thread. A Fluent/MPI
    grandchild that inherited the pipe can keep it open after the worker
    is killed or exits, so the main loop never blocks on it: it returns
    after a kill, or ``drain_s`` after the worker exited, and closes the
    pipe on a daemon thread.
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
    lines = queue.Queue()
    started = time.monotonic()

    def read():
        try:
            for line in proc.stdout:
                lines.put(line)
        except (ValueError, OSError):
            pass
        finally:
            lines.put(_EOF)

    def fire(reason_lines):
        evidence = ""
        try:
            evidence = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            evidence = ""
        pids = pids_to_stop(evidence, proc.pid)
        messages = stop_job_pids(pids, killer=killer)
        line = format_hang_line(stage, idle_s, pids)
        state["messages"] = [line, *reason_lines, *messages]
        state["hung"] = True
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def watch():
        last = progress_signature(
            progress_paths(log_path, extra_files, post_dir)
        )
        last_change = time.monotonic()
        while not stop.is_set():
            if stop.wait(interval):
                return
            now = time.monotonic()
            if wall_s is not None and now - started >= float(wall_s):
                fire([format_wall_cap_line(stage, wall_s)])
                return
            current = progress_signature(
                progress_paths(log_path, extra_files, post_dir)
            )
            if current != last:
                last = current
                last_change = now
                continue
            if now - last_change < float(idle_s) or stop.is_set():
                continue
            fire([])
            return

    print(
        f"Hang watchdog: stage={stage} idle_s={float(idle_s):g}"
        + ("" if wall_s is None else f" wall_s={float(wall_s):g}"),
        flush=True,
    )
    reader = threading.Thread(target=read, name=f"hang-read-{stage}", daemon=True)
    reader.start()
    thread = threading.Thread(target=watch, name=f"hang-watch-{stage}", daemon=True)
    thread.start()
    returncode = 1
    error = None
    pipe_held = False
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            exited_at = None
            while True:
                try:
                    item = lines.get(timeout=interval)
                except queue.Empty:
                    item = None
                if item is _EOF:
                    break
                if item is not None:
                    sys.stdout.write(item)
                    sys.stdout.flush()
                    log_file.write(item)
                    log_file.flush()
                    continue
                if state["hung"]:
                    break
                if proc.poll() is not None:
                    exited_at = exited_at if exited_at is not None else time.monotonic()
                    if time.monotonic() - exited_at >= float(drain_s):
                        pipe_held = True
                        break
            while True:
                try:
                    item = lines.get_nowait()
                except queue.Empty:
                    break
                if item is _EOF:
                    continue
                sys.stdout.write(item)
                log_file.write(item)
            if pipe_held:
                note = (
                    "WARNING: worker exited but its stdout is still open "
                    f"after {float(drain_s):g} s (a grandchild holds the pipe); "
                    "stopped reading.\n"
                )
                sys.stdout.write(note)
                log_file.write(note)
            if not state["hung"]:
                returncode = proc.wait()
    except Exception as exc:
        error = exc
    finally:
        stop.set()
        thread.join(timeout=max(2.0, interval * 2))
    if state["hung"] or pipe_held:
        threading.Thread(target=_close_quietly, args=(proc.stdout,), daemon=True).start()
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
