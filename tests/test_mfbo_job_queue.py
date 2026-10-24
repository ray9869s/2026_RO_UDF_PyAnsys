"""Queue runner tests. Dummy commands only; nothing here launches Fluent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module

job_queue = load_module(
    "job_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "job_queue.py",
)

_HEAD = "a" * 40
_STAMP = "2026-10-04T00:00:00Z"


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _pin_head(monkeypatch, sha=_HEAD) -> None:
    monkeypatch.setattr(job_queue, "read_git_head", lambda: sha)


def _py(source: str) -> list[str]:
    return ["{python}", "-c", source]


def _record(job_id, **overrides) -> dict:
    record = {
        "id": job_id,
        "status": "pending",
        "started_at": None,
        "ended_at": None,
        "return_code": None,
        "log_path": None,
        "git_head": None,
    }
    record.update(overrides)
    return record


def _read_state(queue: Path) -> dict:
    return json.loads(
        Path(str(queue) + ".state.json").read_text(encoding="utf-8")
    )


def test_success_writes_state_log_and_summary(tmp_path, monkeypatch, capsys):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    marker = tmp_path / "marker.txt"
    state_path = Path(str(queue) + ".state.json")
    lock_path = Path(str(queue) + ".lock")
    source = (
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"state = json.loads(Path({str(state_path)!r}).read_text(encoding='utf-8'))\n"
        "job = state['jobs'][0]\n"
        "if job['status'] != 'running' or not job['started_at'] or not job['git_head']:\n"
        "    raise SystemExit('state was not running before the command')\n"
        f"lock = Path({str(lock_path)!r}).read_text(encoding='utf-8').strip()\n"
        "if lock != str(os.getppid()):\n"
        "    raise SystemExit('lock pid ' + lock + ' != parent ' + str(os.getppid()))\n"
        f"Path({str(marker)!r}).write_text(sys.executable, encoding='utf-8')\n"
        "sys.stdout.write('hello-log\\n')\n"
    )
    _write(queue, [{"id": "ok_job", "argv": _py(source)}])

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK

    assert marker.read_text(encoding="utf-8") == sys.executable
    state = _read_state(queue.resolve())
    record = state["jobs"][0]
    assert state["queue_head"] == _HEAD
    assert record["status"] == "succeeded"
    assert record["return_code"] == 0
    assert record["git_head"] == _HEAD
    assert record["started_at"].endswith("Z")
    assert record["ended_at"].endswith("Z")
    log_path = Path(record["log_path"])
    assert log_path == (tmp_path / "logs" / "ok_job.log").resolve()
    assert "hello-log" in log_path.read_text(encoding="utf-8")
    summary = Path(str(queue.resolve()) + ".summary.log").read_text(encoding="utf-8")
    assert "id=ok_job status=succeeded rc=0" in summary
    assert f"git={_HEAD}" in summary
    assert not lock_path.exists()
    assert not Path(str(state_path) + ".tmp").exists()
    assert "id=ok_job status=succeeded rc=0" in capsys.readouterr().out

    assert job_queue.main(["status", str(queue)]) == job_queue.EXIT_OK
    status = capsys.readouterr().out
    assert "ok_job" in status
    assert "succeeded" in status
    assert f"queue_head: {_HEAD}" in status


def test_failure_continues_and_retries_only_when_asked(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    fails = tmp_path / "fails.txt"
    oks = tmp_path / "oks.txt"

    def _count(path: Path, code: str) -> str:
        return (
            "import sys\n"
            "from pathlib import Path\n"
            f"path = Path({str(path)!r})\n"
            "n = int(path.read_text(encoding='utf-8')) if path.exists() else 0\n"
            "path.write_text(str(n + 1), encoding='utf-8')\n"
            "sys.stderr.write('boom\\n')\n"
            f"raise SystemExit({code})\n"
        )

    _write(
        queue,
        [
            {"id": "bad_job", "argv": _py(_count(fails, "3"))},
            {"id": "good_job", "argv": _py(_count(oks, "0"))},
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert fails.read_text(encoding="utf-8") == "1"
    assert oks.read_text(encoding="utf-8") == "1"
    state = _read_state(queue.resolve())
    assert state["jobs"][0]["status"] == "failed"
    assert state["jobs"][0]["return_code"] == 3
    assert state["jobs"][1]["status"] == "succeeded"
    log = (tmp_path / "logs" / "bad_job.log").read_text(encoding="utf-8")
    assert "boom" in log
    summary_path = Path(str(queue.resolve()) + ".summary.log")
    assert len(summary_path.read_text(encoding="utf-8").splitlines()) == 2

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert fails.read_text(encoding="utf-8") == "1"
    assert oks.read_text(encoding="utf-8") == "1"

    assert (
        job_queue.main(["run", str(queue), "--retry-failed"])
        == job_queue.EXIT_JOB_FAILED
    )
    assert fails.read_text(encoding="utf-8") == "2"
    assert oks.read_text(encoding="utf-8") == "1"
    lines = summary_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert "id=bad_job status=failed rc=3" in lines[-1]


def test_resume_skips_succeeded_and_runs_pending(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    first_count = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    stop = Path(str(queue) + ".stop")
    first_source = (
        "from pathlib import Path\n"
        f"path = Path({str(first_count)!r})\n"
        "n = int(path.read_text(encoding='utf-8')) if path.exists() else 0\n"
        "path.write_text(str(n + 1), encoding='utf-8')\n"
        f"Path({str(stop)!r}).write_text('stop\\n', encoding='utf-8')\n"
    )
    second_source = (
        "from pathlib import Path\n"
        f"Path({str(second)!r}).write_text('ran', encoding='utf-8')\n"
    )
    _write(
        queue,
        [
            {"id": "first_job", "argv": _py(first_source)},
            {"id": "second_job", "argv": _py(second_source)},
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert first_count.read_text(encoding="utf-8") == "1"
    assert not second.exists()
    paused = _read_state(queue.resolve())
    assert paused["jobs"][0]["status"] == "succeeded"
    assert paused["jobs"][1]["status"] == "pending"

    stop.unlink()
    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert first_count.read_text(encoding="utf-8") == "1"
    assert second.read_text(encoding="utf-8") == "ran"
    resumed = _read_state(queue.resolve())
    assert [job["status"] for job in resumed["jobs"]] == ["succeeded", "succeeded"]


def test_stop_file_before_start_runs_nothing(tmp_path, monkeypatch, capsys):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    marker = tmp_path / "marker.txt"
    stop = Path(str(queue) + ".stop")
    stop.write_text("stop\n", encoding="utf-8")
    _write(
        queue,
        [
            {
                "id": "would_run",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('no', encoding='utf-8')\n"
                ),
            }
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert not marker.exists()
    state = _read_state(queue.resolve())
    assert state["jobs"][0]["status"] == "pending"
    assert f"stop file present ({stop})" in capsys.readouterr().out


def test_stop_file_created_during_a_job_lets_that_job_finish(
    tmp_path, monkeypatch, capsys
):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    finished = tmp_path / "finished.txt"
    skipped = tmp_path / "skipped.txt"
    stop = Path(str(queue) + ".stop")
    _write(
        queue,
        [
            {
                "id": "current_job",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(finished)!r}).write_text('done', encoding='utf-8')\n"
                    f"Path({str(stop)!r}).write_text('stop\\n', encoding='utf-8')\n"
                ),
            },
            {
                "id": "next_job",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(skipped)!r}).write_text('no', encoding='utf-8')\n"
                ),
            },
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert finished.read_text(encoding="utf-8") == "done"
    assert not skipped.exists()
    state = _read_state(queue.resolve())
    assert [job["status"] for job in state["jobs"]] == ["succeeded", "pending"]
    assert "not starting further jobs" in capsys.readouterr().out


def test_live_lock_refuses_to_start(tmp_path, monkeypatch, capsys):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    marker = tmp_path / "marker.txt"
    lock = Path(str(queue) + ".lock")
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    _write(
        queue,
        [
            {
                "id": "locked_out",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('no', encoding='utf-8')\n"
                ),
            }
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_PREFLIGHT
    assert not marker.exists()
    assert not Path(str(queue) + ".state.json").exists()
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert str(os.getpid()) in capsys.readouterr().err


def test_stale_lock_from_a_dead_pid_is_removed(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    marker = tmp_path / "marker.txt"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()
    assert job_queue.pid_is_alive(dead_pid) is False
    lock = Path(str(queue) + ".lock")
    lock.write_text(f"{dead_pid}\n", encoding="utf-8")
    _write(
        queue,
        [
            {
                "id": "after_stale",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('yes', encoding='utf-8')\n"
                ),
            }
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert marker.read_text(encoding="utf-8") == "yes"
    assert not lock.exists()


def test_pid_is_alive_for_this_process_and_a_finished_one():
    assert job_queue.pid_is_alive(os.getpid()) is True
    assert job_queue.pid_is_alive(2**63) is False
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()
    assert job_queue.pid_is_alive(dead_pid) is False


def test_head_change_warns_and_continues(tmp_path, monkeypatch, capsys):
    started = "a" * 40
    changed = "b" * 40
    sequence = [started, started, changed]
    seen = []

    def _head():
        sha = sequence[len(seen)]
        seen.append(sha)
        return sha

    monkeypatch.setattr(job_queue, "read_git_head", _head)
    queue = (tmp_path / "jobs.json").resolve()
    second = tmp_path / "second.txt"
    _write(
        queue,
        [
            {"id": "before_pull", "argv": _py("pass")},
            {
                "id": "after_pull",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(second)!r}).write_text('ran', encoding='utf-8')\n"
                ),
            },
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    assert seen == sequence
    assert second.read_text(encoding="utf-8") == "ran"
    captured = capsys.readouterr()
    assert captured.err.count("WARNING") == 1
    assert started in captured.err
    assert changed in captured.err
    assert "differs from queue start" in captured.err
    assert "Continuing" in captured.err
    state = _read_state(queue.resolve())
    assert state["queue_head"] == started
    assert state["jobs"][0]["git_head"] == started
    assert state["jobs"][1]["git_head"] == changed
    assert [job["status"] for job in state["jobs"]] == ["succeeded", "succeeded"]


def test_interrupted_running_is_failed_until_retry(tmp_path, monkeypatch, capsys):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    _write(
        queue,
        [
            {
                "id": "was_running",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(first)!r}).write_text('retried', encoding='utf-8')\n"
                ),
            },
            {
                "id": "still_pending",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(second)!r}).write_text('ran', encoding='utf-8')\n"
                ),
            },
        ],
    )
    state_path = Path(str(queue.resolve()) + ".state.json")
    _write(
        state_path,
        {
            "queue_head": _HEAD,
            "jobs": [
                _record(
                    "was_running",
                    status="running",
                    started_at=_STAMP,
                    git_head=_HEAD,
                    log_path=str(tmp_path / "logs" / "was_running.log"),
                ),
                _record("still_pending"),
            ],
        },
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "ran"
    state = _read_state(queue.resolve())
    interrupted = state["jobs"][0]
    assert interrupted["status"] == "failed"
    assert interrupted["return_code"] is None
    assert interrupted["started_at"] == _STAMP
    assert interrupted["ended_at"].endswith("Z")
    assert state["jobs"][1]["status"] == "succeeded"
    interrupted_err = capsys.readouterr().err
    assert "was_running" in interrupted_err
    assert "left in running" in interrupted_err

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert not first.exists()

    assert (
        job_queue.main(["run", str(queue), "--retry-failed"]) == job_queue.EXIT_OK
    )
    assert first.read_text(encoding="utf-8") == "retried"
    assert _read_state(queue.resolve())["jobs"][0]["status"] == "succeeded"


def test_resume_retries_a_failed_solve_and_leaves_pending_argv(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    solve_argv = tmp_path / "solve_argv.txt"
    mesh_argv = tmp_path / "mesh_argv.txt"
    pending_argv = tmp_path / "pending_argv.txt"

    def _dump(path: Path) -> str:
        return (
            "import sys\n"
            "from pathlib import Path\n"
            f"Path({str(path)!r}).write_text('\\n'.join(sys.argv), encoding='utf-8')\n"
        )

    _write(
        queue,
        [
            {
                "id": "solve",
                "argv": [
                    *_py(_dump(solve_argv)),
                    "scripts/mfbo/mesh_study_case.py",
                    "--checkpoint-every",
                    "200",
                    "--reuse-mesh",
                ],
            },
            {
                "id": "mesh_only",
                "argv": [
                    *_py(_dump(mesh_argv)),
                    "scripts/mfbo/mesh_study_case.py",
                    "--mesh-only",
                ],
            },
            {
                "id": "later",
                "argv": [*_py(_dump(pending_argv)), "scripts/mfbo/mesh_study_case.py"],
            },
        ],
    )
    state_path = Path(str(queue) + ".state.json")
    _write(
        state_path,
        {
            "queue_head": _HEAD,
            "jobs": [
                _record("solve", status="failed", return_code=1, ended_at=_STAMP),
                _record("mesh_only", status="failed", return_code=1, ended_at=_STAMP),
                _record("later"),
            ],
        },
    )

    assert job_queue.main(["run", str(queue), "--resume"]) == job_queue.EXIT_OK
    solve_text = solve_argv.read_text(encoding="utf-8")
    assert "--resume" in solve_text.splitlines()
    assert "--checkpoint-every" in solve_text
    mesh_text = mesh_argv.read_text(encoding="utf-8")
    assert "--mesh-only" in mesh_text.splitlines()
    assert "--resume" not in mesh_text.splitlines()
    assert "--resume" not in pending_argv.read_text(encoding="utf-8").splitlines()
    assert [job["status"] for job in _read_state(queue)["jobs"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]


def test_cwd_and_env_are_passed_to_the_command(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    queue = (tmp_path / "jobs.json").resolve()
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path('where.txt').write_text("
        "os.getcwd() + '\\n' + os.environ['MFBO_SENTINEL'], encoding='utf-8')\n"
    )
    _write(
        queue,
        [
            {
                "id": "with_env",
                "argv": _py(source),
                "cwd": str(work),
                "env": {"MFBO_SENTINEL": "from-queue"},
            }
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_OK
    text = (work / "where.txt").read_text(encoding="utf-8")
    cwd, sentinel = text.splitlines()
    assert Path(cwd).resolve() == work.resolve()
    assert sentinel == "from-queue"


def test_missing_command_fails_that_job_and_continues(tmp_path, monkeypatch):
    _pin_head(monkeypatch)
    queue = (tmp_path / "jobs.json").resolve()
    marker = tmp_path / "marker.txt"
    _write(
        queue,
        [
            {"id": "missing_bin", "argv": ["no-such-mfbo-binary-xyz"]},
            {
                "id": "after_missing",
                "argv": _py(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('yes', encoding='utf-8')\n"
                ),
            },
        ],
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_JOB_FAILED
    assert marker.read_text(encoding="utf-8") == "yes"
    state = _read_state(queue.resolve())
    assert state["jobs"][0]["status"] == "failed"
    assert state["jobs"][0]["return_code"] is None
    log = (tmp_path / "logs" / "missing_bin.log").read_text(encoding="utf-8")
    assert "failed to start" in log
    assert state["jobs"][1]["status"] == "succeeded"


def test_queue_id_mismatch_refuses_and_releases_the_lock(tmp_path, capsys):
    queue = (tmp_path / "jobs.json").resolve()
    _write(queue, [{"id": "only_job", "argv": ["{python}", "-c", "pass"]}])
    _write(
        Path(str(queue) + ".state.json"),
        {
            "queue_head": _HEAD,
            "jobs": [_record("other_job")],
        },
    )

    assert job_queue.main(["run", str(queue)]) == job_queue.EXIT_PREFLIGHT
    assert not Path(str(queue) + ".lock").exists()
    err = capsys.readouterr().err
    assert "do not match" in err
    saved = json.loads(
        Path(str(queue) + ".state.json").read_text(encoding="utf-8")
    )
    assert saved["jobs"][0]["id"] == "other_job"
    assert saved["jobs"][0]["status"] == "pending"


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"jobs": []}, "JSON list"),
        ([], "non-empty"),
        ([{"argv": ["x"]}], "missing"),
        ([{"id": "ok", "argv": []}], "argv"),
        ([{"id": "ok", "argv": "python"}], "argv"),
        ([{"id": "ok", "argv": [1]}], "strings"),
        ([{"id": "../x", "argv": ["x"]}], "filename-safe"),
        ([{"id": "a", "argv": ["x"]}, {"id": "a", "argv": ["y"]}], "duplicate"),
        ([{"id": "ok", "argv": ["x"], "env": {"A": 1}}], "string"),
        ([{"id": "ok", "argv": ["x"], "cwd": ""}], "cwd"),
        ([{"id": "ok", "argv": ["x"], "extra": 1}], "unknown"),
        ([{"id": "ok", "argv": ["x"], "console": False}], "console"),
    ],
)
def test_invalid_queue_is_refused(tmp_path, payload, match):
    queue = (tmp_path / "jobs.json").resolve()
    _write(queue, payload)
    with pytest.raises(job_queue.QueueError, match=match):
        job_queue.load_queue(queue)


def test_console_job_gets_a_discovery_console_and_a_plain_job_keeps_the_log(
    tmp_path, monkeypatch
):
    _pin_head(monkeypatch)
    from ro.discovery_session import (
        CREATE_NEW_CONSOLE,
        CREATE_NEW_PROCESS_GROUP,
        CREATE_NO_WINDOW,
        DETACHED_PROCESS,
        discovery_stdio_kwargs,
    )

    seen = []

    def fake_run(argv, **kwargs):
        del argv
        seen.append(kwargs)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(job_queue.subprocess, "run", fake_run)
    plain = (tmp_path / "plain.json").resolve()
    _write(plain, [{"id": "plain", "argv": _py("print(1)")}])
    assert job_queue.main(["run", str(plain)]) == job_queue.EXIT_OK
    plain_kwargs = seen[0]
    assert plain_kwargs["stdout"] is not None
    assert plain_kwargs["stderr"] is subprocess.STDOUT
    assert plain_kwargs.get("stdin") is None
    assert "creationflags" not in plain_kwargs
    assert "RO_DISCOVERY_LOG" not in plain_kwargs["env"]

    seen.clear()
    console = (tmp_path / "console.json").resolve()
    _write(
        console,
        [{"id": "convert-one", "argv": _py("print(1)"), "console": True}],
    )
    assert job_queue.main(["run", str(console)]) == job_queue.EXIT_OK
    kwargs = seen[0]
    expected = discovery_stdio_kwargs()
    assert kwargs["stdin"] is None
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None
    assert kwargs["shell"] is False
    if os.name == "nt":
        flags = kwargs["creationflags"]
        assert flags == expected["creationflags"] == CREATE_NEW_CONSOLE
        assert flags & CREATE_NO_WINDOW == 0
        assert flags & DETACHED_PROCESS == 0
        assert flags & CREATE_NEW_PROCESS_GROUP == 0
    else:
        assert "creationflags" not in kwargs
    log = Path(kwargs["env"]["RO_DISCOVERY_LOG"])
    assert log.name == "convert-one.log"
    assert kwargs["env"]["RO_DISCOVERY_CONSOLE"] == "1"
    assert "Discovery job" in log.read_text(encoding="utf-8")
