"""Pure artifact/transaction/command checks; never start Ansys or host commands."""

import csv
import json
import os
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from test_residual_measurement_report import HEADER_12, make_row

host = load_module("host_pending_checks_under_test", SCRIPTS_DIR / "host" / "pending_checks.py")
emitter = host.reextract


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_wide(leaf, values):
    path = leaf / "post" / "reports" / "summary_metrics_wide.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values))
        writer.writeheader()
        writer.writerow(values)


def plant_final(root, geo="REF_empty", *, status="PASS", run="u0p2_p6M"):
    leaf = host.run_leaf(root, geo, case=run)
    write_json(leaf / "manifest.json", {"convergence_quality": status, "iterations_run": 2000})
    for kind in ("cas", "dat"):
        (leaf / f"{geo}_{run}_final.{kind}.h5").write_bytes(b"synthetic final")
    write_wide(leaf, {"lmh_window_exposed": 25})
    return leaf


def plant_smoke(leaf, *, count=20, flux=2, checkpoints=(10, 20)):
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / "solver_log_synthetic.txt").write_text(
        HEADER_12 + "\n" + "\n".join(make_row(i, 1e-5) for i in range(1, count + 1)),
        encoding="utf-8",
    )
    for iteration in checkpoints:
        for kind in ("cas", "dat"):
            (leaf / f"REF_empty_u0p2_p6M_checkpoint_{iteration:06d}.{kind}.h5").write_bytes(b"synthetic checkpoint")
    write_wide(leaf, {"lmh_window_exposed": flux})
    (leaf / "cp_live.out").write_text('"Iteration" "cp_live"\n1 1.1\n20 1.2\n', encoding="utf-8")


def test_smoke_artifact_validation_is_strict(tmp_path):
    plant_smoke(tmp_path)
    assert host.validate_smoke(tmp_path)["residual_rows"] == 20
    write_wide(tmp_path, {"lmh_window_exposed": 0})
    with pytest.raises(ValueError, match="flux"):
        host.validate_smoke(tmp_path)
    write_wide(tmp_path, {"lmh_window_exposed": 2})
    (tmp_path / "REF_empty_u0p2_p6M_checkpoint_000010.dat.h5").write_bytes(b"")
    with pytest.raises(ValueError, match="checkpoint"):
        host.validate_smoke(tmp_path)
    plant_smoke(tmp_path, count=19)
    with pytest.raises(ValueError, match="iterations 1-20"):
        host.validate_smoke(tmp_path)


@pytest.mark.parametrize("command_fails", [False, True])
def test_smoke_restores_original_and_preserves_new_leaf_on_failure(monkeypatch, tmp_path, command_fails):
    checks = host.Checks(tmp_path)
    checks.attempt.mkdir(parents=True)
    original = plant_final(tmp_path)
    mesh = host.mesh_leaf(tmp_path, "REF_empty", host.BASE_MESH)
    write_json(mesh / "manifest.json", {"cell_count": 10})
    write_json(mesh / "mesh_run_record.json", {"status": "SUCCESS"})
    marker = original / "original_marker"
    marker.write_text("keep", encoding="utf-8")
    def operation(command, _name):
        assert not original.exists()
        assert "--no-cp-live" not in command
        assert "--keep-checkpoints" in command
        assert command[command.index("--max-iterations") + 1] == "20"
        plant_smoke(original)
        if command_fails:
            raise RuntimeError("synthetic command failure")
        return 0
    monkeypatch.setattr(checks, "run_logged", operation)
    details = {}
    if command_fails:
        with pytest.raises(RuntimeError, match="synthetic"):
            checks.smoke(details)
    else:
        checks.smoke(details)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert details["original_restored"] is True
    assert Path(details["smoke_archive"]).is_dir()
    assert not (original / "solver_log_synthetic.txt").exists()


def test_failed_initial_move_aborts_smoke_before_any_command(monkeypatch, tmp_path):
    checks = host.Checks(tmp_path)
    checks.attempt.mkdir(parents=True)
    original = plant_final(tmp_path)
    mesh = host.mesh_leaf(tmp_path, "REF_empty", host.BASE_MESH)
    write_json(mesh / "manifest.json", {})
    write_json(mesh / "mesh_run_record.json", {"status": "SUCCESS"})
    def denied(*_args):
        raise PermissionError("synthetic locked directory")
    monkeypatch.setattr(host, "move_absent", denied)
    monkeypatch.setattr(checks, "run_logged", lambda *_args: pytest.fail("must not launch"))
    with pytest.raises(PermissionError):
        checks.smoke({})
    assert original.is_dir()


def test_preflight_detects_queues_fluent_and_unreadable_commands(monkeypatch, tmp_path):
    for process in (
        {"ProcessId": 9001, "Name": "python.exe", "CommandLine": 'python C:/pyfluent/scripts/mfbo/job_queue.py run main4.json'},
        {"ProcessId": 9002, "Name": "fl_mpi2510.exe", "CommandLine": "Fluent worker"},
        {"ProcessId": 9003, "Name": "python.exe", "CommandLine": None},
    ):
        with pytest.raises(RuntimeError):
            host.require_idle(tmp_path, processes=[process])
    host.require_idle(tmp_path, processes=[])
    lock = tmp_path / "queues" / "other.json.lock"
    lock.parent.mkdir()
    lock.write_text("9004\n", encoding="ascii")
    monkeypatch.setattr(host.job_queue, "pid_is_alive", lambda _pid: True)
    with pytest.raises(RuntimeError, match="live queue lock"):
        host.require_idle(tmp_path, processes=[])


def test_step_failure_continues_and_summary_has_one_line_per_step(monkeypatch, tmp_path):
    checks = host.Checks(tmp_path)
    monkeypatch.setattr(host, "require_idle", lambda _root: None)
    def fail(_details):
        raise ValueError("first line\nsecond line")
    monkeypatch.setattr(checks, "smoke", fail)
    monkeypatch.setattr(checks, "mesh", lambda details, geo, **_kwargs: details.update(cells=12, max_AR=100, min_orthogonal_quality=0.1, gate="PASS"))
    monkeypatch.setattr(checks, "locate", fail)
    monkeypatch.setattr(checks, "timed_reextract", lambda details: details.update(extract_wall_time_s=123))
    assert checks.run() == 2
    lines = checks.summary.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7
    assert "FAIL 1_cp_live_smoke" in lines[0]
    assert "PASS 2_fs025" in lines[1]
    assert "FAIL 4_locate" in lines[5]
    assert "extract_wall_time_s" in lines[6]
    assert not (tmp_path / "queues" / "pending_checks.lock").exists()
    followups = (checks.attempt / "followup_commands.sh.txt").read_text(encoding="utf-8")
    assert "--no-cp-live" in followups


def test_followups_use_approved_representatives_and_selected_mesh_without_solving(tmp_path):
    text = host.followup_commands(tmp_path, [0.025], False, "STAMP")
    assert "P_p100_h00" not in text and "M_c160" not in text
    for geo in host.REPRESENTATIVES:
        assert geo in text
    assert text.index("--velocity-smoke") < text.index("--run-id u0p1_p6M")
    assert text.index("--run-id u0p1_p6M") < text.index("--run-id u0p15_p6M")
    assert '"$CHOSEN_FACE_SIZE"' in text
    assert "--no-cp-live" not in host.followup_commands(tmp_path, [0.025], True, "STAMP")


def test_emitted_finer_and_velocity_queues_keep_original_spec_provenance(tmp_path):
    fine = tmp_path / "fine.json"
    host.emit_finer_queue(fine, 0.025, True)
    jobs = json.loads(fine.read_text(encoding="utf-8"))
    assert len(jobs) == 4
    for job in jobs:
        argv = job["argv"]
        assert "--reuse-mesh" in argv
        assert argv[argv.index("--mesh-spec") + 1] == "configs/mesh_spec_v3.json"
        assert json.loads(argv[argv.index("--local-face-size") + 1])["size_mm"] == 0.025
    velocity = tmp_path / "velocity.json"
    host.emit_finer_queue(velocity, 0.025, True, geo_ids=None, run_ids=["u0p1_p6M"])
    jobs = json.loads(velocity.read_text(encoding="utf-8"))
    assert len(jobs) == 31
    assert sum("--local-face-size" in job["argv"] for job in jobs) == 4
    reps = tmp_path / "reps.json"
    host.emit_finer_queue(reps, 0.025, True, geo_ids=host.REPRESENTATIVES,
                          run_ids=["u0p05_p6M", "u0p15_p6M", "u0p25_p6M"])
    assert len(json.loads(reps.read_text(encoding="utf-8"))) == 15


def test_pass_final_emitter_excludes_failed_archived_and_iteration_snapshots(tmp_path):
    passed = plant_final(tmp_path)
    plant_final(tmp_path, "D2450_a45", status="FAIL")
    plant_final(tmp_path, "M_c267", run="u0p15_p6M")
    archived = plant_final(tmp_path, "P_p80_h15")
    archived.rename(archived.with_name(archived.name + ".prev_20261118T000000Z"))
    write_json(passed / "post" / "reports_iter2500" / "manifest.json", {"convergence_quality": "PASS"})
    jobs = emitter.pass_final_jobs(tmp_path)
    assert len(jobs) == 1
    assert jobs[0]["argv"][-1] == str(passed.resolve())
    assert "--run-leaf" in jobs[0]["argv"]
    assert "--resume" not in jobs[0]["argv"]
    assert (passed / "post" / "reports").is_dir()
    (passed / "REF_empty_u0p2_p6M_final.dat.h5").unlink()
    with pytest.raises(ValueError, match="final solution"):
        emitter.pass_final_jobs(tmp_path)


def test_pass_worker_rechecks_status_before_archiving(monkeypatch, tmp_path):
    leaf = plant_final(tmp_path, status="FAIL")
    monkeypatch.setattr(emitter.reextract_runs, "reextract_leaf", lambda *_a, **_kw: pytest.fail("must not extract"))
    with pytest.raises(ValueError, match="not PASS"):
        emitter.main(["--data-root", str(tmp_path), "--run-leaf", str(leaf)])
    assert (leaf / "post" / "reports").is_dir()


def test_failed_reextract_cannot_report_the_previous_extract_time(monkeypatch, tmp_path):
    leaf = plant_final(tmp_path, "P_p80_h15")
    timing = leaf / "post" / "reports" / "report_extract_timing.json"
    write_json(timing, {"total_seconds": 999})
    checks = host.Checks(tmp_path)
    monkeypatch.setattr(checks, "run_logged", lambda *_args: 1)
    details = {}
    with pytest.raises(RuntimeError, match="exited 1"):
        checks.timed_reextract(details)
    assert "extract_wall_time_s" not in details


def test_timed_reextract_validates_new_columns_and_records_fresh_time(monkeypatch, tmp_path):
    leaf = plant_final(tmp_path, "P_p80_h15")
    checks = host.Checks(tmp_path)
    def operation(command, _name):
        assert command[-1] == str(leaf)
        write_wide(leaf, {
            "cp_meanfirst_live_window_avg": 0.2, "cp_meanfirst_window_avg": 0.2,
            "cp_ref_area_live": 2, "c_b_window_mol_m3": 12,
            "cm_area_mean_window_live": 4, "live_membrane_area_window_m2": 3,
            "live_exclusion_threshold_mol_m3": 10,
            "live_exclusion_rule": "c_p >= min(c_0, c_b)", "cpc_window_avg_area": 0.2,
        })
        write_json(leaf / "post" / "reports" / "report_extract_timing.json", {"total_seconds": 123})
        return 0
    monkeypatch.setattr(checks, "run_logged", operation)
    details = {}
    checks.timed_reextract(details)
    assert details["extract_wall_time_s"] == 123
    assert details["cp_meanfirst_live_window_avg"] == pytest.approx(0.2)
    assert details["post_extract_gate"] == "PASS"
