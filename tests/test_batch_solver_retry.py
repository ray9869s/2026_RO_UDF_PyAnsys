"""Solver-stage transient retry: session deaths only, unique attempt logs."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import load_batch_solver_sweep
from ro.manifest import write_run_manifest
from ro.paths import mesh_dir, run_dir
from ro.session_retry import RETRY_KIND_LAUNCH_SPAWN, RETRY_KIND_SOCKET_RESET
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, run_payload

SOCKET_RESET = (
    "RuntimeError: IOCP/Socket: Connection reset "
    "(An existing connection was forcibly closed by the remote host, 10054)"
)
LAUNCH_SPAWN = (
    "LaunchFluentError: Fluent Launch command: fluent 3ddp "
    "-sifile=C:/tmp/serverinfo-retry1.txt\n"
    "Deadline Exceeded\nFailed to construct hwtree\nAborting:"
)
INLET_READBACK = (
    "RuntimeError: Inlet BC readback failed on inlet: "
    "x-velocity UDF '' != requested RO_inlet_profile"
)
G_MARKER = "RuntimeError: Missing required transcript marker RO_UDF_INLET_G"
UDF_COMPILE = "RuntimeError: UDF compilation failed for libudf"
QOI_FAIL = "QoI monitor did not meet qoi_stop_criterion"
RESIDUAL_FAIL = "residuals diverged; stop_reason=diverged"
CASE_NAME = RUN_ID


def _paths(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    (mesh_directory / f"{GEO_ID}_{MESH_ID}.msh.h5").write_bytes(b"mesh")
    return run_directory


def _run_attempts(sweep, run_directory, runner, **kwargs):
    sleeps = []
    return sweep.run_solver_attempts(
        cmd=["python", "solver_code_260616.py"],
        env={},
        cwd=str(run_directory),
        run_directory=run_directory,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        case_name=CASE_NAME,
        max_retries=2,
        settle_s=15.0,
        runner=runner,
        sleeper=sleeps.append,
        **kwargs,
    ), sleeps


def test_attempt_log_path_includes_mesh_id_and_attempt():
    sweep = load_batch_solver_sweep()
    path = sweep.solver_attempt_log_path(
        "/tmp/run", GEO_ID, MESH_ID, RUN_ID, 2
    )
    assert path.name == f"{GEO_ID}__{MESH_ID}__{RUN_ID}__solver_attempt2.log"
    other = sweep.solver_attempt_log_path(
        "/tmp/run", GEO_ID, "max085_min006_cpg5_bl6_peel2", RUN_ID, 2
    )
    assert path != other


def test_socket_reset_retries_on_new_subprocess(monkeypatch, tmp_path, capsys):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(str(log_path))
        log_path.write_text(
            SOCKET_RESET if len(calls) == 1 else "ok", encoding="utf-8"
        )
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    (result, attempts, kinds, logs), sleeps = _run_attempts(
        sweep, run_directory, runner
    )
    assert result.returncode == 0
    assert attempts == 2
    assert kinds == [RETRY_KIND_SOCKET_RESET]
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert all(MESH_ID in name for name in calls)
    assert all(path.is_file() and path.parent == run_directory for path in logs)
    assert sleeps == [15.0]
    captured = capsys.readouterr().out
    assert "transient session_socket_reset on attempt 1" in captured


def test_launch_spawn_is_retryable(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(1)
        log_path.write_text(
            LAUNCH_SPAWN if len(calls) == 1 else "ok", encoding="utf-8"
        )
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    (result, attempts, kinds, _), _ = _run_attempts(sweep, run_directory, runner)
    assert result.returncode == 0
    assert attempts == 2
    assert kinds == [RETRY_KIND_LAUNCH_SPAWN]


def test_non_retryable_failures_attempt_once(monkeypatch, tmp_path, capsys):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    for text in (INLET_READBACK, G_MARKER, UDF_COMPILE, QOI_FAIL, RESIDUAL_FAIL):
        calls = []

        def runner(cmd, env=None, cwd=None, log_path=None, _text=text, _calls=calls):
            _calls.append(1)
            log_path.write_text(_text, encoding="utf-8")
            return SimpleNamespace(returncode=1)

        (result, attempts, kinds, _), sleeps = _run_attempts(
            sweep, run_directory, runner
        )
        assert result.returncode == 1
        assert attempts == 1
        assert kinds == []
        assert sleeps == []
        assert len(calls) == 1
    captured = capsys.readouterr().out
    assert "non-retryable failure on attempt 1" in captured


def test_attempt_two_does_not_overwrite_attempt_one_log(monkeypatch, tmp_path):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(log_path)
        log_path.write_text(
            f"attempt-{len(calls)} {SOCKET_RESET}", encoding="utf-8"
        )
        worker = run_directory / f"solver_log_{CASE_NAME}.txt"
        worker.write_text(f"worker-attempt-{len(calls)}\n", encoding="utf-8")
        return SimpleNamespace(returncode=1)

    (result, attempts, kinds, logs), _ = _run_attempts(
        sweep, run_directory, runner
    )
    assert result.returncode == 1
    assert attempts == 3
    assert kinds == [RETRY_KIND_SOCKET_RESET, RETRY_KIND_SOCKET_RESET]
    assert logs[0].read_text(encoding="utf-8").startswith("attempt-1")
    assert logs[1].read_text(encoding="utf-8").startswith("attempt-2")
    assert logs[2].read_text(encoding="utf-8").startswith("attempt-3")
    archived_1 = run_directory / f"solver_log_{CASE_NAME}__attempt1.txt"
    archived_2 = run_directory / f"solver_log_{CASE_NAME}__attempt2.txt"
    archived_3 = run_directory / f"solver_log_{CASE_NAME}__attempt3.txt"
    assert archived_1.read_text(encoding="utf-8") == "worker-attempt-1\n"
    assert archived_2.read_text(encoding="utf-8") == "worker-attempt-2\n"
    assert archived_3.read_text(encoding="utf-8") == "worker-attempt-3\n"


def test_leftover_finals_do_not_skip_retry_attempt(
    monkeypatch, tmp_path, capsys
):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    mesh_file = mesh_dir(FAMILY, GEO_ID, MESH_ID) / f"{GEO_ID}_{MESH_ID}.msh.h5"
    final_case = run_directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5"
    final_data = run_directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5"
    skip_checks = []
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(1)
        if len(calls) == 1:
            final_case.write_bytes(b"stale-cas")
            final_data.write_bytes(b"stale-dat")
            payload = run_payload()
            payload["stop_reason"] = "RUNNING"
            payload.pop("final_case_sha256", None)
            payload.pop("final_data_sha256", None)
            write_run_manifest(run_directory, payload)
            log_path.write_text(SOCKET_RESET, encoding="utf-8")
            skip_checks.append(
                sweep.classify_solver_pre_execution(
                    skip_existing_final_data=True,
                    dry_run=False,
                    run_directory=run_directory,
                    final_case_path=final_case,
                    final_data_path=final_data,
                    mesh_file_path=mesh_file,
                )
            )
            return SimpleNamespace(returncode=1)
        log_path.write_text("ok", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    (result, attempts, kinds, _), _ = _run_attempts(sweep, run_directory, runner)
    assert result.returncode == 0
    assert attempts == 2
    assert kinds == [RETRY_KIND_SOCKET_RESET]
    assert skip_checks == [("run", "stop_reason 'RUNNING' is not a skip-complete reason")]
    assert "transient session_socket_reset" in capsys.readouterr().out


def test_old_attempt_socket_log_does_not_classify_current_udf_failure(
    monkeypatch, tmp_path
):
    sweep = load_batch_solver_sweep()
    run_directory = _paths(monkeypatch, tmp_path)
    stale = sweep.solver_attempt_log_path(
        run_directory, GEO_ID, MESH_ID, RUN_ID, 1
    )
    stale.write_text(SOCKET_RESET, encoding="utf-8")
    calls = []

    def runner(cmd, env=None, cwd=None, log_path=None):
        calls.append(str(log_path))
        log_path.write_text(UDF_COMPILE, encoding="utf-8")
        return SimpleNamespace(returncode=1)

    (result, attempts, kinds, _), _ = _run_attempts(sweep, run_directory, runner)
    assert result.returncode == 1
    assert attempts == 1
    assert kinds == []
    assert len(calls) == 1
