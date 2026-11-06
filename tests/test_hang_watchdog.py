"""Hang watchdog: stall detection, one extract retry, no solve retry."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_batch_report_extract,
    load_batch_solver_sweep,
    load_module,
    load_run_config,
)
from ro.hang_watchdog import (
    FAILURE_CLASS_HANG,
    HANG_EXIT_CODE,
    format_hang_line,
    parse_hang_line,
    pids_to_stop,
    record_hang,
    tee_until_hang,
)
from ro.manifest import ManifestError, read_run_manifest, write_run_manifest
from ro.paths import run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, run_payload

_SLEEP = (
    "import time, sys\n"
    "print(sys.argv[1], flush=True)\n"
    "time.sleep(30)\n"
)


def test_run_config_hang_defaults_are_30_and_60_minutes():
    from ro.hang_watchdog import idle_seconds

    cfg = load_run_config()
    assert cfg.extract_hang_idle_minutes == 30.0
    assert cfg.solve_hang_idle_minutes == 60.0
    assert idle_seconds("extract") == 1800.0
    assert idle_seconds("solve") == 3600.0


def test_pids_to_stop_include_the_launch_line_and_the_worker_only():
    evidence = (
        "FLUENT_LAUNCH pid=4242\n"
        "fluent-20261019-114250-19328.trn\n"
        "ORPHAN noise pid=99999\n"
    )
    assert pids_to_stop(evidence, 50) == [4242, 50]
    assert 99999 not in pids_to_stop(evidence, 50)
    assert 19328 not in pids_to_stop(evidence, 50)


def test_parse_hang_line_reads_the_last_record():
    text = (
        format_hang_line("extract", 1800, [4242])
        + "\n"
        + format_hang_line("extract", 1800, [4242, 77])
        + "\n"
    )
    parsed = parse_hang_line(text)
    assert parsed["failure_class"] == FAILURE_CLASS_HANG
    assert parsed["stage"] == "extract"
    assert parsed["idle_s"] == 1800
    assert parsed["killed_pids"] == [4242, 77]
    assert parse_hang_line("FLUENT_LAUNCH pid=4242\n") is None


def _sleeper():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )


def test_tee_kills_the_recorded_pid_and_leaves_another_process(tmp_path):
    target = _sleeper()
    bystander = _sleeper()
    script = tmp_path / "worker.py"
    script.write_text(_SLEEP, encoding="utf-8")
    log_path = tmp_path / "attempt.log"
    try:
        result = tee_until_hang(
            [sys.executable, str(script), f"FLUENT_LAUNCH pid={target.pid}"],
            env=None,
            log_path=log_path,
            stage="extract",
            idle_s=0.8,
            poll_s=0.05,
        )
        assert result.returncode == HANG_EXIT_CODE
        target.wait(timeout=5)
        assert bystander.poll() is None
        text = log_path.read_text(encoding="utf-8")
        hang = parse_hang_line(text)
        assert target.pid in hang["killed_pids"]
        assert bystander.pid not in hang["killed_pids"]
    finally:
        for proc in (target, bystander):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def test_growing_transcript_is_progress(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(
        "import time\nprint('start', flush=True)\ntime.sleep(1.2)\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "solver_log.txt"
    transcript.write_text("iter 1\n", encoding="utf-8")
    stop = False

    def grow():
        while not stop:
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write("iter\n")
            time.sleep(0.05)

    import threading

    thread = threading.Thread(target=grow, daemon=True)
    thread.start()
    try:
        result = tee_until_hang(
            [sys.executable, str(script)],
            env=None,
            log_path=tmp_path / "attempt.log",
            stage="solve",
            idle_s=0.5,
            poll_s=0.05,
            extra_files=(transcript,),
        )
        assert result.returncode == 0
    finally:
        stop = True
        thread.join(timeout=2)


def test_extract_retries_one_hang_then_fails(tmp_path, capsys):
    batch = load_batch_report_extract()
    calls = []

    def runner(cmd, env, log_path):
        calls.append(Path(log_path).name)
        Path(log_path).write_text(
            "FLUENT_LAUNCH pid=4242\n"
            + format_hang_line("extract", 1800, [4242])
            + "\n",
            encoding="utf-8",
        )
        return type("R", (), {"returncode": HANG_EXIT_CODE})()

    killed = []
    result, attempts, kinds, _logs = batch.run_extract_attempts(
        cmd=["python", "extract.py"],
        env={},
        run_directory=tmp_path,
        geo_id="D2450_a45",
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        max_retries=0,
        settle_s=0.0,
        sleeper=lambda _s: None,
        runner=runner,
        killer=lambda pid: killed.append(pid) or f"killed {pid}",
    )
    assert result.returncode == HANG_EXIT_CODE
    assert attempts == 2
    assert kinds == []
    assert len(calls) == 2
    assert killed == [4242, 4242]
    output = capsys.readouterr().out
    assert "failure_class=hang" in output
    assert "killed_pids=[4242]" in output
    assert "retrying once" in output
    assert "not retrying" in output


def test_extract_hang_then_success_does_not_set_failure_class(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    write_run_manifest(directory, run_payload())
    batch = load_batch_report_extract()
    calls = []

    def runner(cmd, env, log_path):
        calls.append(1)
        if len(calls) == 1:
            Path(log_path).write_text(
                format_hang_line("extract", 1800, [11, 22]) + "\n",
                encoding="utf-8",
            )
            return type("R", (), {"returncode": HANG_EXIT_CODE})()
        Path(log_path).write_text("ok\n", encoding="utf-8")
        return type("R", (), {"returncode": 0})()

    result, attempts, kinds, _logs = batch.run_extract_attempts(
        cmd=["python", "extract.py"],
        env={},
        run_directory=directory,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        max_retries=0,
        settle_s=0.0,
        sleeper=lambda _s: None,
        runner=runner,
    )
    assert result.returncode == 0
    assert attempts == 2
    assert kinds == []
    payload = read_run_manifest(directory)
    assert "failure_class" not in payload
    assert payload["hang_events"][0]["killed_pids"] == [11, 22]
    assert payload["hang_events"][0]["stage"] == "extract"
    assert payload["u_target_ms"] == run_payload()["u_target_ms"]


def test_solve_hang_fails_without_a_retry(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    final_case = directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5"
    final_data = directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5"
    final_case.write_bytes(b"case")
    final_data.write_bytes(b"data")
    write_run_manifest(directory, run_payload())
    sweep = load_batch_solver_sweep()
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(1)
        Path(log_path).write_text(
            "FLUENT_LAUNCH pid=4242\n"
            + format_hang_line("solve", 3600, [4242])
            + "\n",
            encoding="utf-8",
        )
        return type("R", (), {"returncode": HANG_EXIT_CODE})()

    result, attempts, kinds, _logs = sweep.run_solver_attempts(
        cmd=["python", "solver.py"],
        env={},
        cwd=str(directory),
        run_directory=directory,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        case_name=RUN_ID,
        max_retries=2,
        settle_s=0.0,
        sleeper=lambda _s: None,
        runner=runner,
    )
    assert result.returncode == HANG_EXIT_CODE
    assert attempts == 1
    assert kinds == []
    assert calls == [1]
    payload = read_run_manifest(directory)
    assert payload["failure_class"] == FAILURE_CLASS_HANG
    assert payload["hang_events"][0]["killed_pids"] == [4242]
    assert final_case.read_bytes() == b"case"
    assert final_data.read_bytes() == b"data"
    output = capsys.readouterr().out
    assert "failure_class=hang" in output
    assert "not retrying" in output


def test_reextract_archives_partial_reports_and_keeps_finals(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    final_case = directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5"
    final_data = directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5"
    final_case.write_bytes(b"case")
    final_data.write_bytes(b"data")
    write_run_manifest(directory, run_payload())
    reports = directory / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "partial.txt").write_text("hung mid-report\n", encoding="utf-8")
    message = record_hang(
        directory,
        {
            "stage": "extract",
            "attempt": 2,
            "idle_s": 1800,
            "killed_pids": [4242, 90],
        },
        failed=True,
    )
    assert "failure_class=hang" in message
    reextract_runs = load_module(
        "reextract_runs_hang",
        SCRIPTS_DIR / "mfbo" / "reextract_runs.py",
    )

    archived = reextract_runs.archive_reports(directory)
    assert archived is not None
    assert archived.name.startswith("reports_prev_")
    assert not reports.exists()
    assert (archived / "partial.txt").read_text(encoding="utf-8") == (
        "hung mid-report\n"
    )
    case = reextract_runs.operating_point_case(tmp_path, directory)
    assert case["inlet_velocity_value"] == run_payload()["u_target_ms"]
    assert case["outlet_gauge_pressure"] == run_payload()["p_gauge_pa"]
    assert final_case.read_bytes() == b"case"
    assert final_data.read_bytes() == b"data"
    payload = read_run_manifest(directory)
    assert payload["failure_class"] == "hang"
    assert payload["hang_events"][0]["killed_pids"] == [4242, 90]


def test_manifest_rejects_a_bad_hang_event(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    directory.mkdir(parents=True)
    payload = run_payload()
    payload["failure_class"] = "socket"
    with pytest.raises(ManifestError, match="failure_class"):
        write_run_manifest(directory, payload)
