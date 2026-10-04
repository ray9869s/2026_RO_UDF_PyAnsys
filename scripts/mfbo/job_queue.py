"""Sequential queue for long MFBO workstation jobs.

The queue file is a JSON list. Each job has a unique filename-safe ``id``,
an ``argv`` list, and optional ``cwd`` and ``env``. The token ``{python}``
in argv is replaced with ``sys.executable``. Commands run with
``shell=False``. Omitted ``cwd`` keeps the runner's working directory.
``env`` is merged onto a copy of the runner environment.

Sidecars use the queue path ``<queue>`` as a prefix:

- ``<queue>.state.json`` — status, UTC times, return code, log path, git HEAD
- ``<queue>.stop`` — finish the current job, then stop before the next
- ``<queue>.lock`` — runner pid; a live pid refuses another start
- ``<queue>.summary.log`` — one line per finished attempt, also printed
- ``<queue dir>/logs/<id>.log`` — that job's stdout and stderr

``run`` records the repo HEAD when the state file is created. A later job
whose HEAD differs prints a warning and still runs. A job left ``running``
(interrupted runner) is marked failed on the next start and is not executed
again unless ``--retry-failed`` is set. Succeeded jobs are skipped. The
process exits 0 when no job is failed, 2 when any job is failed, and 1 for
a preflight refusal (bad queue, lock held by a live runner, git failure).

Importing this module does not launch Fluent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_JOB_FAILED = 2

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_JOB_REQUIRED = ("id", "argv")
_JOB_OPTIONAL = ("cwd", "env")
_RECORD_FIELDS = (
    "id",
    "status",
    "started_at",
    "ended_at",
    "return_code",
    "log_path",
    "git_head",
)
_STATUSES = frozenset({"pending", "running", "succeeded", "failed"})


class QueueError(Exception):
    """Operator-facing refusal. ``main`` prints it and exits 1."""


class QueueFiles(NamedTuple):
    queue: Path
    state: Path
    stop: Path
    lock: Path
    summary: Path
    logs: Path


def repo_root() -> Path:
    """Repository root (this file is ``scripts/mfbo/job_queue.py``)."""
    return Path(__file__).resolve().parents[2]


def queue_files(queue_path: Path) -> QueueFiles:
    """Sidecar paths for a resolved queue file."""
    prefix = str(queue_path)
    return QueueFiles(
        queue=queue_path,
        state=Path(prefix + ".state.json"),
        stop=Path(prefix + ".stop"),
        lock=Path(prefix + ".lock"),
        summary=Path(prefix + ".summary.log"),
        logs=queue_path.parent / "logs",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_git_head() -> str:
    """Current git HEAD of this repo. Raises QueueError if git cannot say."""
    result = subprocess.run(
        ["git", "-C", str(repo_root()), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise QueueError(f"git rev-parse HEAD failed: {detail}")
    sha = result.stdout.strip()
    if not sha:
        raise QueueError("git rev-parse HEAD returned an empty revision.")
    return sha


def pid_is_alive(pid: int) -> bool:
    """Return whether ``pid`` is a live process. Never signals the process."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise QueueError(f"runner pid must be a positive integer, got {pid!r}.")
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
        raise QueueError(f"could not check pid {pid}: {exc}") from exc
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
        raise QueueError(f"could not check pid {pid}: winerror {error}")
    try:
        code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(code)):
            error = ctypes.get_last_error()
            raise QueueError(
                f"could not read exit code for pid {pid}: winerror {error}"
            )
        return int(code.value) == still_active
    finally:
        close_handle(handle)


def resolve_queue_path(value) -> Path:
    if not isinstance(value, (str, Path)):
        raise QueueError(f"queue path must be a path, got {value!r}.")
    path = Path(value).expanduser()
    if not path.is_file():
        raise QueueError(f"queue file not found: {value}")
    return path.resolve()


def load_queue(queue_path: Path) -> list[dict]:
    """Validate the queue file. Does not replace ``{python}``."""
    payload = _read_json(queue_path)
    if not isinstance(payload, list) or not payload:
        raise QueueError(
            f"queue must be a non-empty JSON list of jobs: {queue_path}"
        )
    jobs = []
    seen = set()
    for index, item in enumerate(payload):
        label = f"job {index}"
        _require_exact_keys(item, _JOB_REQUIRED, _JOB_OPTIONAL, label)
        job_id = _validate_id(item["id"], label)
        if job_id in seen:
            raise QueueError(f"duplicate job id {job_id!r} in {queue_path}")
        seen.add(job_id)
        job = {"id": job_id, "argv": _validate_argv(item["argv"], label)}
        if "cwd" in item:
            cwd = item["cwd"]
            if not isinstance(cwd, str) or cwd.strip() == "":
                raise QueueError(f"{label} cwd must be a non-empty string.")
            job["cwd"] = cwd
        if "env" in item:
            job["env"] = _validate_env(item["env"], label)
        jobs.append(job)
    return jobs


def run_queue(queue, *, retry_failed: bool = False) -> int:
    """Run the queue. Returns 0 or 2. Raises QueueError on preflight refusal."""
    if not isinstance(retry_failed, bool):
        raise QueueError(
            f"retry_failed must be a bool, got {retry_failed!r}."
        )
    queue_path = resolve_queue_path(queue)
    jobs = load_queue(queue_path)
    files = queue_files(queue_path)
    _acquire_lock(files.lock)
    try:
        return _run_locked(jobs, files, retry_failed=retry_failed)
    finally:
        _release_lock(files.lock)


def status_report(queue) -> str:
    """Text table of jobs. Does not take the lock or start anything."""
    queue_path = resolve_queue_path(queue)
    jobs = load_queue(queue_path)
    files = queue_files(queue_path)
    if files.state.is_file():
        state = read_state(files.state)
        _ensure_ids_match(state, jobs)
        records = state["jobs"]
        head = state["queue_head"]
    else:
        records = [_blank_record(job["id"]) for job in jobs]
        head = None
    return _render_table(records) + f"\nqueue_head: {head or '-'}"


def read_state(path: Path) -> dict:
    payload = _read_json(path)
    label = f"state file {path}"
    if not isinstance(payload, dict):
        raise QueueError(f"{label} must be a JSON object.")
    _require_exact_keys(payload, ("queue_head", "jobs"), (), label)
    head = payload["queue_head"]
    if not isinstance(head, str) or head.strip() == "":
        raise QueueError(f"{label} queue_head must be a non-empty string.")
    jobs = payload["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise QueueError(f"{label} jobs must be a non-empty list.")
    records = [_validate_record(item, index) for index, item in enumerate(jobs)]
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise QueueError(f"{label} has duplicate job ids.")
    return {"queue_head": head, "jobs": records}


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            report = status_report(args.queue)
        elif args.command == "run":
            return run_queue(args.queue, retry_failed=args.retry_failed)
        else:
            raise QueueError(f"unknown command {args.command!r}.")
        print(report, flush=True)
        return EXIT_OK
    except QueueError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return EXIT_PREFLIGHT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a sequential queue of long MFBO jobs."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Run pending jobs in order.")
    run_parser.add_argument("queue", help="Queue JSON file.")
    run_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-run jobs whose status is failed.",
    )
    status_parser = sub.add_parser("status", help="Print the job table.")
    status_parser.add_argument("queue", help="Queue JSON file.")
    return parser


def _run_locked(jobs: list[dict], files: QueueFiles, *, retry_failed: bool) -> int:
    state = _load_or_create_state(files.state, jobs)
    _ensure_ids_match(state, jobs)
    for record in state["jobs"]:
        if record["status"] != "running":
            continue
        record["status"] = "failed"
        record["ended_at"] = utc_now()
        record["return_code"] = None
        _write_json_atomic(files.state, state)
        print(
            f"WARNING: job {record['id']} was left in running and is marked failed.",
            file=sys.stderr,
            flush=True,
        )
        _append_summary(files.summary, _summary_line(record))
    for job, record in zip(jobs, state["jobs"], strict=True):
        if files.stop.is_file():
            print(
                f"stop file present ({files.stop}); not starting further jobs.",
                flush=True,
            )
            break
        status = record["status"]
        if status == "succeeded":
            continue
        if status == "failed" and not retry_failed:
            continue
        if status not in ("pending", "failed"):
            raise QueueError(
                f"job {record['id']} has status {status!r}."
            )
        _execute_job(job, record, state, files)
    if any(record["status"] == "failed" for record in state["jobs"]):
        return EXIT_JOB_FAILED
    return EXIT_OK


def _load_or_create_state(path: Path, jobs: list[dict]) -> dict:
    if path.is_file():
        return read_state(path)
    state = {
        "queue_head": read_git_head(),
        "jobs": [_blank_record(job["id"]) for job in jobs],
    }
    _write_json_atomic(path, state)
    return state


def _ensure_ids_match(state: dict, jobs: list[dict]) -> None:
    state_ids = [record["id"] for record in state["jobs"]]
    queue_ids = [job["id"] for job in jobs]
    if state_ids != queue_ids:
        raise QueueError(
            "queue job ids do not match the state file: "
            f"queue={queue_ids}, state={state_ids}."
        )


def _execute_job(job: dict, record: dict, state: dict, files: QueueFiles) -> None:
    head = read_git_head()
    if head != state["queue_head"]:
        print(
            f"WARNING: git HEAD {head} differs from queue start "
            f"{state['queue_head']} (job {job['id']}). Continuing.",
            file=sys.stderr,
            flush=True,
        )
    log_path = files.logs / f"{job['id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record["status"] = "running"
    record["started_at"] = utc_now()
    record["ended_at"] = None
    record["return_code"] = None
    record["log_path"] = str(log_path.resolve())
    record["git_head"] = head
    _write_json_atomic(files.state, state)

    argv = [part.replace("{python}", sys.executable) for part in job["argv"]]
    env = os.environ.copy()
    if "env" in job:
        env.update(job["env"])
    try:
        with log_path.open("wb") as handle:
            completed = subprocess.run(
                argv,
                cwd=job.get("cwd"),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                shell=False,
                check=False,
            )
    except OSError as exc:
        with log_path.open("ab") as handle:
            message = f"job failed to start: {exc}\n"
            handle.write(message.encode("utf-8", "replace"))
        record["status"] = "failed"
        record["ended_at"] = utc_now()
        record["return_code"] = None
        _write_json_atomic(files.state, state)
        _append_summary(files.summary, _summary_line(record))
        return

    record["return_code"] = completed.returncode
    record["ended_at"] = utc_now()
    record["status"] = "succeeded" if completed.returncode == 0 else "failed"
    _write_json_atomic(files.state, state)
    _append_summary(files.summary, _summary_line(record))


def _summary_line(record: dict) -> str:
    when = record["ended_at"]
    if not isinstance(when, str) or not when:
        raise QueueError(
            f"job {record['id']} is missing ended_at for the summary."
        )
    rc = "-" if record["return_code"] is None else str(record["return_code"])
    git_head = record["git_head"] or "-"
    log_path = record["log_path"] or "-"
    return (
        f"{when} id={record['id']} status={record['status']} "
        f"rc={rc} git={git_head} log={log_path}"
    )


def _append_summary(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(line, flush=True)


def _render_table(records: list[dict]) -> str:
    headers = ["id", "status", "rc", "started_at", "ended_at", "git_head", "log"]
    rows = []
    for record in records:
        rc = record["return_code"]
        rows.append(
            [
                record["id"],
                record["status"],
                "-" if rc is None else str(rc),
                record["started_at"] or "-",
                record["ended_at"] or "-",
                record["git_head"] or "-",
                record["log_path"] or "-",
            ]
        )
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def _format(row: list[str]) -> str:
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(row)
        ).rstrip()

    lines = [_format(headers), _format(["-" * width for width in widths])]
    lines.extend(_format(row) for row in rows)
    return "\n".join(lines)


def _blank_record(job_id: str) -> dict:
    return {
        "id": job_id,
        "status": "pending",
        "started_at": None,
        "ended_at": None,
        "return_code": None,
        "log_path": None,
        "git_head": None,
    }


def _validate_record(item, index: int) -> dict:
    label = f"state job {index}"
    _require_exact_keys(item, _RECORD_FIELDS, (), label)
    rc = item["return_code"]
    if rc is not None and (
        isinstance(rc, bool) or not isinstance(rc, int)
    ):
        raise QueueError(f"{label} return_code must be an integer or null.")
    record = _blank_record(_validate_id(item["id"], label))
    if item["status"] not in _STATUSES:
        raise QueueError(f"{label} status {item['status']!r} is not valid.")
    record["status"] = item["status"]
    record["return_code"] = rc
    for key in ("started_at", "ended_at", "log_path", "git_head"):
        value = item[key]
        if value is not None and not isinstance(value, str):
            raise QueueError(f"{label} {key} must be a string or null.")
        record[key] = value
    return record


def _validate_id(value, label: str) -> str:
    if not isinstance(value, str) or _JOB_ID_RE.fullmatch(value) is None:
        raise QueueError(
            f"{label} id must be filename-safe "
            f"(match {_JOB_ID_RE.pattern}), got {value!r}."
        )
    return value


def _validate_argv(argv, label: str) -> list[str]:
    if not isinstance(argv, list) or not argv:
        raise QueueError(f"{label} argv must be a non-empty list of strings.")
    cleaned = []
    for item in argv:
        if not isinstance(item, str):
            raise QueueError(f"{label} argv entries must be strings.")
        cleaned.append(item)
    return cleaned


def _validate_env(env, label: str) -> dict:
    if not isinstance(env, dict):
        raise QueueError(f"{label} env must be an object of strings.")
    cleaned = {}
    for key, value in env.items():
        if not isinstance(key, str) or key == "":
            raise QueueError(f"{label} env keys must be non-empty strings.")
        if not isinstance(value, str):
            raise QueueError(
                f"{label} env[{key!r}] must be a string, got {value!r}."
            )
        cleaned[key] = value
    return cleaned


def _require_exact_keys(mapping, required, optional, label: str) -> None:
    if not isinstance(mapping, dict):
        raise QueueError(f"{label} must be a JSON object.")
    allowed = set(required) | set(optional)
    missing = [key for key in required if key not in mapping]
    unknown = sorted(set(mapping) - allowed)
    if missing or unknown:
        raise QueueError(
            f"{label} keys mismatch: missing={missing or '-'}, "
            f"unknown={unknown or '-'}."
        )


def _read_json(path: Path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise QueueError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise QueueError(f"could not read {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        if tmp.is_file():
            tmp.unlink()
        raise


def _acquire_lock(path: Path) -> None:
    payload = f"{os.getpid()}\n".encode("ascii")
    for _attempt in range(5):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _read_lock_pid(path)
            if pid_is_alive(holder):
                raise QueueError(
                    f"queue runner already active (pid {holder}); lock {path}"
                )
            path.unlink()
            continue
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(f"short write to lock {path}")
            os.fsync(fd)
        except OSError as exc:
            os.close(fd)
            if path.is_file():
                path.unlink()
            raise QueueError(f"could not write lock {path}: {exc}") from exc
        os.close(fd)
        return
    raise QueueError(f"could not acquire lock {path}")


def _release_lock(path: Path) -> None:
    if not path.is_file():
        raise QueueError(f"lock file disappeared before release: {path}")
    holder = _read_lock_pid(path)
    if holder != os.getpid():
        raise QueueError(
            f"refusing to remove lock {path} held by pid {holder}."
        )
    path.unlink()


def _read_lock_pid(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QueueError(f"could not read lock {path}: {exc}") from exc
    text = text.strip()
    if not text.isdigit():
        raise QueueError(f"lock file {path} does not hold a pid: {text!r}")
    pid = int(text)
    if pid <= 0:
        raise QueueError(f"lock file {path} does not hold a pid: {text!r}")
    return pid


if __name__ == "__main__":
    sys.exit(main())
