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
from ro.paths import refuse_production_root
from ro.process_tree import (
    VERIFY_KILL_S,
    kill_verification_line,
    list_processes,
    snapshot_tree,
    wait_gone,
)

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
# After the loop, copy at most this long / this many queued lines.
FINAL_DRAIN_S = 2.0
FINAL_DRAIN_MAX_LINES = 100_000
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
    pipe_output=True,
    process_lister=list_processes,
    verify_kill_s=VERIFY_KILL_S,
):
    """Tee the child to ``log_path`` and stdout. Kill it when progress stops.

    ``pipe_output=False`` (console jobs: the queue does not redirect their
    handles because Discovery may not start) leaves the child's stdout on
    the console. ``log_path`` then only gets a header, and progress is the
    growth of ``extra_files`` (the meshing transcripts) and ``post/``.

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
    # The parent writes this log before the (guarded) worker starts.
    refuse_production_root(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    popen = popen_factory or subprocess.Popen
    if not pipe_output:
        return _wait_console_until_hang(
            cmd, env=env, cwd=cwd, log_path=log_path, stage=stage, idle_s=idle_s,
            extra_files=extra_files, popen=popen, killer=killer, poll_s=poll_s,
            wall_s=wall_s, process_lister=process_lister, verify_kill_s=verify_kill_s,
        )
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
        # Mark the hang before killing: the kill closes the pipe, and the
        # main thread must not take the killed worker's exit code as the
        # result while the verification below is still running.
        state["messages"] = [format_hang_line(stage, idle_s, pids), *reason_lines]
        state["hung"] = True
        state["messages"].extend(_kill_and_verify(
            pids, proc, killer=killer, lister=process_lister, verify_kill_s=verify_kill_s,
        ))

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
                # Checked before every line: a child that floods stdout
                # cannot keep the loop from seeing a hang or its own exit.
                if state["hung"]:
                    break
                if proc.poll() is not None:
                    exited_at = exited_at if exited_at is not None else time.monotonic()
                    if time.monotonic() - exited_at >= float(drain_s):
                        pipe_held = True
                        break
                try:
                    item = lines.get(timeout=interval)
                except queue.Empty:
                    continue
                if item is _EOF:
                    break
                sys.stdout.write(item)
                sys.stdout.flush()
                log_file.write(item)
                log_file.flush()
            # Bounded: whatever is already queued, for at most FINAL_DRAIN_S.
            drain_deadline = time.monotonic() + FINAL_DRAIN_S
            drained = 0
            while time.monotonic() < drain_deadline and drained < FINAL_DRAIN_MAX_LINES:
                try:
                    item = lines.get_nowait()
                except queue.Empty:
                    break
                if item is _EOF:
                    continue
                drained += 1
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
        # After a hang, wait for the kill verification (listing included).
        thread.join(
            timeout=float(verify_kill_s) + 180.0 if state["hung"] else max(2.0, interval * 2)
        )
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


def _kill_and_verify(pids, proc, *, killer, lister, verify_kill_s):
    """Kill the recorded PIDs and the worker; verify the whole tree is gone.

    The tree (those PIDs and every descendant) is listed before the kill,
    so a Fluent/MPI grandchild that survives ``taskkill /T`` is named in a
    ``KILL_INCOMPLETE`` line instead of being assumed dead.
    """
    tree, snapshot_error = snapshot_tree([*pids, proc.pid], lister=lister)
    messages = stop_job_pids(pids, killer=killer)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=min(5.0, float(verify_kill_s)))
    except subprocess.TimeoutExpired:
        pass
    survivors, verify_error = wait_gone(tree, lister=lister, timeout_s=verify_kill_s)
    messages.append(kill_verification_line(
        tree, survivors, snapshot_error=snapshot_error, verify_error=verify_error,
    ))
    return messages


def _evidence_text(paths) -> str:
    chunks = []
    for path in paths:
        try:
            chunks.append(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def _wait_console_until_hang(
    cmd, *, env, cwd, log_path, stage, idle_s, extra_files, popen, killer, poll_s, wall_s,
    process_lister=list_processes, verify_kill_s=VERIFY_KILL_S,
):
    """``tee_until_hang`` without a pipe: the child keeps the console.

    Progress is file growth only (``extra_files`` and ``post/``); the
    attempt log holds a header and, on a hang, the HANG lines.
    """
    log_path.write_text(
        f"Console job (no stdout pipe). Progress is file growth of: "
        f"{', '.join(str(path) for path in extra_files) or '(none)'}\n",
        encoding="utf-8",
    )
    proc = popen(cmd, env=env, cwd=cwd)
    post_dir = log_path.parent / "post"
    interval = poll_s if poll_s is not None else min(5.0, max(0.05, float(idle_s) / 20.0))
    print(
        f"Hang watchdog: stage={stage} idle_s={float(idle_s):g}"
        + ("" if wall_s is None else f" wall_s={float(wall_s):g}")
        + " console=1 (progress from log files)",
        flush=True,
    )
    started = time.monotonic()
    last = progress_signature(progress_paths(log_path, extra_files, post_dir))
    last_change = started
    reason_lines = None
    while proc.poll() is None:
        time.sleep(interval)
        now = time.monotonic()
        if wall_s is not None and now - started >= float(wall_s):
            reason_lines = [format_wall_cap_line(stage, wall_s)]
            break
        current = progress_signature(progress_paths(log_path, extra_files, post_dir))
        if current != last:
            last, last_change = current, now
        elif now - last_change >= float(idle_s):
            reason_lines = []
            break
    if reason_lines is None:
        return subprocess.CompletedProcess(cmd, proc.wait())
    evidence = _evidence_text([log_path, *extra_files])
    pids = pids_to_stop(evidence, proc.pid)
    messages = _kill_and_verify(
        pids, proc, killer=killer, lister=process_lister, verify_kill_s=verify_kill_s,
    )
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        for message in [format_hang_line(stage, idle_s, pids), *reason_lines, *messages]:
            log_file.write(message + "\n")
            print(message, flush=True)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return subprocess.CompletedProcess(cmd, HANG_EXIT_CODE)


def record_hang(run_directory, event, *, failed: bool) -> str:
    """Append one hang to the run manifest. Missing manifests are reported.

    ``failure_class`` is ``hang`` only when this stage is giving up.
    A retry keeps the event and leaves ``failure_class`` unset. Final
    case and data files are not touched. The gate (``convergence_quality``
    and its report generation) is cleared on every call: a worker killed
    after ``publish_extract`` wrote PASS must not leave that PASS behind.
    """
    from ro.manifest import ManifestError, read_run_manifest, write_run_manifest
    from ro.report_binding import GATE_GENERATION_FIELD

    directory = Path(run_directory)
    try:
        payload = read_run_manifest(directory)
    except (ManifestError, OSError, ValueError) as exc:
        return f"hang not recorded on run manifest: {exc}"
    events = payload.get("hang_events")
    if not isinstance(events, list):
        events = []
    payload["hang_events"] = [*events, dict(event)]
    payload["convergence_quality"] = None
    payload[GATE_GENERATION_FIELD] = None
    if failed:
        payload["failure_class"] = FAILURE_CLASS_HANG
    path = write_run_manifest(directory, payload)
    status = FAILURE_CLASS_HANG if failed else "recorded"
    return (
        f"Recorded hang on {path} failure_class={status} "
        f"killed_pids={list(event.get('killed_pids') or [])}."
    )
