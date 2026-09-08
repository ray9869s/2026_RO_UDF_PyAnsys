"""CAD AttachAssembly retry and multi-case batch meshing loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.mesh_common import load_mesh_run_record, write_mesh_run_record


def _load_batch_meshing():
    return load_module(
        "batch_meshing_cad_retry_under_test",
        SCRIPTS_DIR / "batch_meshing.py",
    )


ATTACH_ASSEMBLY_ERROR = (
    "RuntimeError: Error in CAD Import / "
    "pIPartMgr->AttachAssembly() failed "
    "(attaching to assembly failed after 82.345 [s])"
)


def test_is_cad_attach_assembly_failure_matches_signature():
    batch = _load_batch_meshing()
    assert batch.is_cad_attach_assembly_failure(ATTACH_ASSEMBLY_ERROR)
    assert batch.is_cad_attach_assembly_failure(
        "Error in CAD Import\nAttachAssembly failed"
    )
    assert not batch.is_cad_attach_assembly_failure(
        "RuntimeError: Surface mesh quality failed: maximum skewness"
    )
    assert not batch.is_cad_attach_assembly_failure("")


def test_run_meshing_attempts_retries_attach_assembly_only(tmp_path, capsys):
    batch = _load_batch_meshing()
    mesh_log = tmp_path / "mesh_log.txt"
    record_path = tmp_path / "mesh_run_record.json"
    calls = []

    def runner(cmd, env=None, cwd=None, check=False):
        calls.append(1)
        if len(calls) == 1:
            write_mesh_run_record(
                record_path,
                {"error_summary": ATTACH_ASSEMBLY_ERROR, "status": "FAILED"},
            )
            mesh_log.write_text(
                "attaching to assembly failed after 82.345 [s]\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1)
        write_mesh_run_record(
            record_path,
            {
                "status": "SUCCESS",
                "error_summary": "",
                "cell_count": 12345,
            },
        )
        return SimpleNamespace(returncode=0)

    result, attempts, retry_kinds = batch.run_meshing_attempts(
        cmd=["python", "meshing_code_260616.py"],
        env={},
        cwd=str(tmp_path),
        mesh_log_path=mesh_log,
        mesh_run_record_path=record_path,
        max_retries=1,
        runner=runner,
    )
    assert result.returncode == 0
    assert attempts == 2
    assert len(calls) == 2
    assert retry_kinds == [batch.RETRY_KIND_CAD_ATTACH]
    captured = capsys.readouterr().out
    assert "CAD AttachAssembly / Import failure on attempt 1" in captured


def test_run_meshing_attempts_does_not_retry_non_cad_error(tmp_path, capsys):
    batch = _load_batch_meshing()
    mesh_log = tmp_path / "mesh_log.txt"
    record_path = tmp_path / "mesh_run_record.json"
    calls = []

    def runner(cmd, env=None, cwd=None, check=False):
        calls.append(1)
        write_mesh_run_record(
            record_path,
            {
                "error_summary": (
                    "RuntimeError: Surface mesh quality failed: "
                    "maximum skewness 0.96"
                ),
                "status": "FAILED",
            },
        )
        mesh_log.write_text("Surface mesh quality failed\n", encoding="utf-8")
        return SimpleNamespace(returncode=1)

    result, attempts, retry_kinds = batch.run_meshing_attempts(
        cmd=["python", "meshing_code_260616.py"],
        env={},
        cwd=str(tmp_path),
        mesh_log_path=mesh_log,
        mesh_run_record_path=record_path,
        max_retries=1,
        runner=runner,
    )
    assert result.returncode == 1
    assert attempts == 1
    assert len(calls) == 1
    assert retry_kinds == []
    captured = capsys.readouterr().out
    assert "Non-retryable meshing failure" in captured
    assert "CAD AttachAssembly" not in captured
    assert "Session socket-reset" not in captured

def test_multi_case_loop_retries_then_starts_next_case(
    tmp_path, monkeypatch, capsys
):
    """Two-case batch: case1 fails AttachAssembly once then succeeds; case2 runs."""
    batch = _load_batch_meshing()
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))

    family = "diamond"
    mesh_id = "max085_min006_cpg5_bl4_peel2"
    cases = [
        {
            "family": family,
            "geo_id": "D0817_a45",
            "mesh_id": mesh_id,
            "spacing_code": "D0817",
            "attack_angle_deg": 45,
            "n_active_cells": 15,
            "cell_length_x_m": 0.001,
            "periodic_shift_y": 0.001,
        },
        {
            "family": family,
            "geo_id": "D0817_a60",
            "mesh_id": mesh_id,
            "spacing_code": "D0817",
            "attack_angle_deg": 60,
            "n_active_cells": 15,
            "cell_length_x_m": 0.001,
            "periodic_shift_y": 0.001,
        },
    ]

    config_path = tmp_path / "batch_config_under_test.py"
    config_path.write_text(
        "\n".join(
            [
                "dry_run = False",
                "continue_on_failure = True",
                "skip_existing_mesh = False",
                "inter_case_delay_s = 0.0",
                "post_failure_settle_s = 0.0",
                "transient_failure_max_retries = 1",
                "clean_fm_scratch_on_success = True",
                "common_mesh_settings = {}",
                f"mesh_batch_cases = {cases!r}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Minimal base config: only attributes _resolved_mesh_parameters needs.
    base_cfg_path = tmp_path / "base_run_config_under_test.py"
    base_cfg_path.write_text(
        "m_max = 0.085\nm_min = 0.006\nm_cpg = 5\n"
        "bl_layers = 4\npeel_layers = 2\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(batch, "BATCH_CONFIG_PATH", config_path)
    monkeypatch.setattr(batch, "BASE_RUN_CONFIG_PATH", base_cfg_path)

    worker_calls = []

    def fake_runner(cmd, env=None, cwd=None, check=False):
        overrides = json.loads(env["PYFLUENT_RUN_OVERRIDES"])
        geo_id = overrides["geo_id"]
        mesh_directory = (
            tmp_path / "meshes" / family / geo_id / mesh_id
        )
        mesh_directory.mkdir(parents=True, exist_ok=True)
        record_path = mesh_directory / "mesh_run_record.json"
        log_path = mesh_directory / f"mesh_log_{mesh_id}.txt"
        scratch = mesh_directory / "FM_HOST_12345"
        scratch.mkdir(exist_ok=True)
        (scratch / "junk.txt").write_text("x", encoding="utf-8")

        worker_calls.append(geo_id)
        attempt_for_geo = worker_calls.count(geo_id)

        if geo_id == "D0817_a45" and attempt_for_geo == 1:
            write_mesh_run_record(
                record_path,
                {
                    "error_summary": ATTACH_ASSEMBLY_ERROR,
                    "status": "FAILED",
                    "cell_count": None,
                },
            )
            log_path.write_text(
                "attaching to assembly failed after 82.345 [s]\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1)

        # Success path (retry of case1, or first try of case2).
        mesh_file = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
        mesh_file.write_bytes(b"mesh")
        write_mesh_run_record(
            record_path,
            {
                "status": "SUCCESS",
                "error_summary": "",
                "cell_count": 1000 + len(worker_calls),
            },
        )
        log_path.write_text(
            "Mesh Quality:\nMinimum Orthogonal Quality = 0.2\n"
            "Maximum Aspect Ratio = 40.0\n"
            "cell count = 1000\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", fake_runner)
    monkeypatch.setattr(batch, "list_leftover_meshing_processes", lambda: [])
    monkeypatch.setattr(sys, "argv", ["batch_meshing.py"])

    batch.main()

    # case1: fail + retry success; case2: success => 3 worker invocations
    assert worker_calls == ["D0817_a45", "D0817_a45", "D0817_a60"]

    case1_record = load_mesh_run_record(
        tmp_path
        / "meshes"
        / family
        / "D0817_a45"
        / mesh_id
        / "mesh_run_record.json"
    )
    assert case1_record["cad_import_attempts"] == 2
    assert case1_record["transient_failure_attempts"] == 2
    assert case1_record["succeeded_on_cad_import_retry"] is True
    assert case1_record["succeeded_on_transient_retry"] is True
    assert case1_record["retry_reason"] == batch.RETRY_KIND_CAD_ATTACH
    assert case1_record["retry_reasons"] == [batch.RETRY_KIND_CAD_ATTACH]

    case2_record = load_mesh_run_record(
        tmp_path
        / "meshes"
        / family
        / "D0817_a60"
        / mesh_id
        / "mesh_run_record.json"
    )
    assert case2_record["cad_import_attempts"] == 1
    assert case2_record["succeeded_on_cad_import_retry"] is False

    # Successful cases clean FM_ scratch dirs.
    assert not (
        tmp_path / "meshes" / family / "D0817_a45" / mesh_id / "FM_HOST_12345"
    ).exists()
    assert not (
        tmp_path / "meshes" / family / "D0817_a60" / mesh_id / "FM_HOST_12345"
    ).exists()

    captured = capsys.readouterr().out
    assert "SUCCESS_AFTER_RETRY" in captured
    assert "attempts=2" in captured
    assert "D0817_a60" in captured


def test_multi_case_non_cad_failure_does_not_retry_but_next_case_runs(
    tmp_path, monkeypatch
):
    batch = _load_batch_meshing()
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))

    family = "diamond"
    mesh_id = "max085_min006_cpg5_bl4_peel2"
    cases = [
        {
            "family": family,
            "geo_id": "D0817_a45",
            "mesh_id": mesh_id,
            "spacing_code": "D0817",
            "attack_angle_deg": 45,
            "n_active_cells": 15,
            "cell_length_x_m": 0.001,
            "periodic_shift_y": 0.001,
        },
        {
            "family": family,
            "geo_id": "D0817_a60",
            "mesh_id": mesh_id,
            "spacing_code": "D0817",
            "attack_angle_deg": 60,
            "n_active_cells": 15,
            "cell_length_x_m": 0.001,
            "periodic_shift_y": 0.001,
        },
    ]

    config_path = tmp_path / "batch_config_non_cad.py"
    config_path.write_text(
        "\n".join(
            [
                "dry_run = False",
                "continue_on_failure = True",
                "skip_existing_mesh = False",
                "inter_case_delay_s = 0.0",
                "post_failure_settle_s = 0.0",
                "transient_failure_max_retries = 1",
                "clean_fm_scratch_on_success = False",
                "common_mesh_settings = {}",
                f"mesh_batch_cases = {cases!r}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    base_cfg_path = tmp_path / "base_run_config_non_cad.py"
    base_cfg_path.write_text(
        "m_max = 0.085\nm_min = 0.006\nm_cpg = 5\n"
        "bl_layers = 4\npeel_layers = 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "BATCH_CONFIG_PATH", config_path)
    monkeypatch.setattr(batch, "BASE_RUN_CONFIG_PATH", base_cfg_path)

    worker_calls = []

    def fake_runner(cmd, env=None, cwd=None, check=False):
        overrides = json.loads(env["PYFLUENT_RUN_OVERRIDES"])
        geo_id = overrides["geo_id"]
        mesh_directory = tmp_path / "meshes" / family / geo_id / mesh_id
        mesh_directory.mkdir(parents=True, exist_ok=True)
        record_path = mesh_directory / "mesh_run_record.json"
        log_path = mesh_directory / f"mesh_log_{mesh_id}.txt"
        worker_calls.append(geo_id)

        if geo_id == "D0817_a45":
            write_mesh_run_record(
                record_path,
                {
                    "error_summary": (
                        "RuntimeError: Surface mesh quality failed"
                    ),
                    "status": "FAILED",
                },
            )
            log_path.write_text("quality failed\n", encoding="utf-8")
            return SimpleNamespace(returncode=1)

        write_mesh_run_record(
            record_path,
            {"status": "SUCCESS", "error_summary": "", "cell_count": 99},
        )
        log_path.write_text("ok\n", encoding="utf-8")
        (mesh_directory / f"{geo_id}_{mesh_id}.msh.h5").write_bytes(b"m")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(batch.subprocess, "run", fake_runner)
    monkeypatch.setattr(batch, "list_leftover_meshing_processes", lambda: [])
    monkeypatch.setattr(sys, "argv", ["batch_meshing.py"])

    with pytest.raises(SystemExit) as exc_info:
        batch.main()
    assert exc_info.value.code == 1

    # Non-CAD failure: no retry; second case still starts.
    assert worker_calls == ["D0817_a45", "D0817_a60"]
    case1_record = load_mesh_run_record(
        tmp_path
        / "meshes"
        / family
        / "D0817_a45"
        / mesh_id
        / "mesh_run_record.json"
    )
    assert case1_record["cad_import_attempts"] == 1
    assert case1_record["succeeded_on_cad_import_retry"] is False
    assert case1_record["retry_reason"] is None
    assert case1_record["retry_reasons"] == []

SOCKET_RESET_ERROR = (
    "RuntimeError: IOCP/Socket: Connection reset (10054) "
    "An existing connection was forcibly closed by the remote host"
)


def test_socket_reset_is_retryable_and_distinct_from_cad():
    batch = _load_batch_meshing()
    assert batch.is_session_socket_reset_failure(SOCKET_RESET_ERROR)
    assert (
        batch.classify_retryable_session_failure(SOCKET_RESET_ERROR)
        == batch.RETRY_KIND_SOCKET_RESET
    )
    assert (
        batch.classify_retryable_session_failure(ATTACH_ASSEMBLY_ERROR)
        == batch.RETRY_KIND_CAD_ATTACH
    )
    assert batch.classify_retryable_session_failure(
        "RuntimeError: Surface mesh quality failed"
    ) is None


def test_run_meshing_attempts_retries_socket_reset(tmp_path, capsys):
    batch = _load_batch_meshing()
    mesh_log = tmp_path / "mesh_log.txt"
    record_path = tmp_path / "mesh_run_record.json"
    calls = []

    def runner(cmd, env=None, cwd=None, check=False):
        calls.append(1)
        if len(calls) == 1:
            write_mesh_run_record(
                record_path,
                {"error_summary": SOCKET_RESET_ERROR, "status": "FAILED"},
            )
            mesh_log.write_text("IOCP/Socket connection reset\n", encoding="utf-8")
            return SimpleNamespace(returncode=1)
        write_mesh_run_record(
            record_path,
            {"status": "SUCCESS", "error_summary": "", "cell_count": 99},
        )
        return SimpleNamespace(returncode=0)

    result, attempts, retry_kinds = batch.run_meshing_attempts(
        cmd=["python", "meshing_code_260616.py"],
        env={},
        cwd=str(tmp_path),
        mesh_log_path=mesh_log,
        mesh_run_record_path=record_path,
        max_retries=2,
        runner=runner,
    )
    assert result.returncode == 0
    assert attempts == 2
    assert retry_kinds == [batch.RETRY_KIND_SOCKET_RESET]
    captured = capsys.readouterr().out
    assert "Session socket-reset failure on attempt 1" in captured
    assert "CAD AttachAssembly" not in captured


def test_collect_evidence_excludes_watchdog_err(tmp_path):
    batch = _load_batch_meshing()
    mesh_log = tmp_path / "mesh_log.txt"
    mesh_log.write_text("log\n", encoding="utf-8")
    record_path = tmp_path / "mesh_run_record.json"
    write_mesh_run_record(record_path, {"error_summary": "RuntimeError: boom"})
    (tmp_path / "pyfluent_watchdog.err").write_text(
        "CalledProcessError: cleanup-fluent-HOST-131932.bat\n",
        encoding="utf-8",
    )
    evidence = batch.collect_session_failure_evidence(mesh_log, record_path)
    assert "RuntimeError: boom" in evidence
    assert "cleanup-fluent-HOST-131932.bat" not in evidence


def test_watchdog_stderr_recorded_separately_from_error_summary(tmp_path):
    """Worker stores watchdog noise under watchdog_stderr, not error_summary."""
    import sys
    import types

    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        meshing = load_module(
            "meshing_watchdog_record_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    (tmp_path / "pyfluent_watchdog.err").write_text(
        "CalledProcessError: cleanup bat exit 1\n",
        encoding="utf-8",
    )
    assert meshing.read_pyfluent_watchdog_err(tmp_path).startswith(
        "CalledProcessError"
    )
    # Simulate the record-write path: error_summary stays clean.
    from ro.mesh_common import build_mesh_ledger_record, write_mesh_run_record

    record = build_mesh_ledger_record(
        geo_name="D2450_a60",
        mesh_case_name="max085_min006_cpg5_bl4_peel2",
        mesh_parameters={},
        status="FAILED",
        exit_code=1,
        wall_time_seconds=1.0,
        metrics={},
        mesh_log_path=tmp_path / "mesh_log.txt",
        mesh_file_path=tmp_path / "mesh.msh.h5",
        error_summary="RuntimeError: IOCP/Socket: Connection reset (10054)",
    )
    watchdog = meshing.read_pyfluent_watchdog_err(tmp_path)
    if watchdog:
        record["watchdog_stderr"] = watchdog
    write_mesh_run_record(tmp_path / "mesh_run_record.json", record)
    loaded = load_mesh_run_record(tmp_path / "mesh_run_record.json")
    assert "IOCP/Socket" in loaded["error_summary"]
    assert "cleanup bat" not in loaded["error_summary"]
    assert "cleanup bat" in loaded["watchdog_stderr"]
