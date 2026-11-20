"""Host re-extract at ae887ed: job ids, hang root, extract cap, repair, staging."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.job_ids import JOB_ID_RE, job_id_valid, require_job_id
from ro.report_binding import report_binding_reason

REEXTRACT = load_module("campaign_v3_reextract_followups", SCRIPTS_DIR / "campaign_v3_reextract.py")
CAMPAIGN = REEXTRACT.campaign
QUEUE = load_module("job_queue_followups", SCRIPTS_DIR / "mfbo" / "job_queue.py")
REPAIR = load_module("repair_final_followups", SCRIPTS_DIR / "repair_final_from_iter.py")

# The mesh id the host's refused job carried.
LONG_MESH = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
RUN = "u0p2_p6M"
CAS, DAT = b"case-2500", b"data-2500"


def _leaf(root, geo, family, *, mesh=LONG_MESH, gate="PASS", hashes=True, sidecar=None):
    leaf = root / "runs" / family / geo / mesh / RUN
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text("cp\n1.0\n", encoding="utf-8")
    final = {
        "final_case_sha256": hashlib.sha256(CAS).hexdigest(),
        "final_data_sha256": hashlib.sha256(DAT).hexdigest(),
    }
    (reports / "extract_source.json").write_text(json.dumps(sidecar or final), encoding="utf-8")
    manifest = {"convergence_quality": gate, "geo_id": geo, "family": family}
    if hashes:
        manifest.update(final)
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (leaf / f"{geo}_{RUN}_final.cas.h5").write_bytes(CAS)
    (leaf / f"{geo}_{RUN}_final.dat.h5").write_bytes(DAT)
    return leaf


def _snapshot(leaf, geo, iteration, cas=b"x", dat=b"y"):
    stem = leaf / f"{geo}_{RUN}_iter_{iteration:06d}"
    Path(f"{stem}.cas.h5").write_bytes(cas)
    Path(f"{stem}.dat.h5").write_bytes(dat)


# 1. Emitted ids always pass job_queue validation ---------------------------

def test_host_refused_id_is_invalid_and_the_new_ones_are_short():
    old = f"reextract-S_a072_l3465-{LONG_MESH}-{RUN}-iter2000-meanfirst"
    assert not job_id_valid(old)
    with pytest.raises(ValueError, match="filename-safe"):
        CAMPAIGN._job(old, ["x"])


def test_every_emitted_id_validates_in_job_queue(tmp_path):
    from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
    from ro.mesh_spec import default_spec_path, load_mesh_spec

    for geo in CAMPAIGN_GEO_ID_ORDER:
        leaf = _leaf(tmp_path, geo, family_for_geo_id(geo))
        _snapshot(leaf, geo, 2000)
        _snapshot(leaf, geo, 12500)
    jobs = list(REEXTRACT.pass_final_jobs(tmp_path, report=lambda line: None))
    jobs += REEXTRACT.snapshot_jobs(
        tmp_path, [(geo, n) for geo in CAMPAIGN_GEO_ID_ORDER for n in (2000, 12500)],
        extract_wall_cap_hours=8,
    )
    spec_path = default_spec_path()
    spec = load_mesh_spec(spec_path)
    jobs += CAMPAIGN.mesh_jobs(spec, spec_path)
    jobs += CAMPAIGN.mesh_jobs(spec, spec_path, max_aspect_ratio=150)
    jobs += CAMPAIGN.solve_jobs(spec, spec_path, max_aspect_ratio=150, keep_checkpoints=True)
    assert len(jobs) > 4 * len(CAMPAIGN_GEO_ID_ORDER)
    for job in jobs:
        assert JOB_ID_RE.fullmatch(job["id"]), job["id"]
        assert QUEUE._validate_id(job["id"], "job") == job["id"]
    ids = [job["id"] for job in jobs]
    assert len(set(ids)) == len(ids)
    assert "snap-S_a072_l3465-2000" in ids and "rx-S_a072_l3465" in ids
    queue = tmp_path / "queue.json"
    CAMPAIGN.write_queue(queue, jobs)
    assert [job["id"] for job in QUEUE.load_queue(queue)] == ids  # the runner accepts it


@pytest.mark.parametrize("name", [
    "campaign_v3.py", "mfbo/d2450_bulk_queue.py", "mfbo/d2450_y1_queue.py",
    "mfbo/d2450_mmax_y1_queue.py", "mfbo/locate_mass_imbalance.py",
    "mfbo/meshstudy_reextract_queue.py", "mfbo/d0817_contact_queue.py",
    "mfbo/convert_campaign_dsco_to_pmdb.py", "mfbo/parity_campaign_pmdb.py",
])
def test_every_queue_writer_refuses_an_invalid_or_duplicate_id(name, tmp_path):
    module = load_module(f"writer_{name.replace('/', '_')[:-3]}", SCRIPTS_DIR / name)
    long_id = "x" * 81
    with pytest.raises(ValueError, match="filename-safe"):
        module.write_queue(tmp_path / "q.json", [{"id": long_id, "argv": ["a"]}])
    with pytest.raises(ValueError, match="duplicate"):
        module.write_queue(tmp_path / "q.json", [{"id": "a", "argv": ["a"]}] * 2)
    assert not (tmp_path / "q.json").exists()


def test_two_leaves_of_one_geometry_get_distinct_valid_ids(tmp_path):
    _leaf(tmp_path, "D2450_a60", "diamond")
    _leaf(tmp_path, "D2450_a60", "diamond", mesh="max030_min006_cpg5_bl4_peel2")
    ids = [job["id"] for job in REEXTRACT.pass_final_jobs(tmp_path, report=lambda line: None)]
    assert len(set(ids)) == 2 and all(i.startswith("rx-D2450_a60-m") for i in ids)
    assert all(job_id_valid(i) for i in ids)
    assert require_job_id("rx-D2450_a60") == "rx-D2450_a60"


# 2. record_hang uses the leaf's own data root -------------------------------

def test_record_hang_uses_the_leaf_root_not_the_driver_env(monkeypatch, tmp_path):
    from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, run_payload

    from ro.hang_watchdog import record_hang
    from ro.manifest import read_run_manifest, write_run_manifest
    from ro.paths import leaf_data_root, run_dir, using_data_root

    campaign_root = tmp_path / "ro_data_mfbo" / "campaign_v3"
    with using_data_root(campaign_root):
        leaf = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
        leaf.mkdir(parents=True)
        payload = run_payload() | {"convergence_quality": "PASS"}
        write_run_manifest(leaf, payload)
    assert leaf_data_root(leaf) == campaign_root
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path / "ro_data"))  # the driver's root
    message = record_hang(
        leaf, {"stage": "extract", "attempt": 2, "idle_s": 1800, "killed_pids": [7]}, failed=True
    )
    assert message.startswith("Recorded hang"), message
    assert os.environ["RO_DATA_ROOT"] == str(tmp_path / "ro_data")
    with using_data_root(campaign_root):
        after = read_run_manifest(leaf)
    assert after["failure_class"] == "hang" and after["convergence_quality"] is None
    assert leaf_data_root(tmp_path / "not" / "a" / "leaf") is None


# 3. Extract wall cap: 6 h default, per-job override, logged -----------------

def test_extract_wall_cap_default_override_and_log_line(monkeypatch):
    from ro.hang_watchdog import WALL_CAP_ENV, format_wall_cap_setting_line, wall_cap_seconds

    monkeypatch.delenv(WALL_CAP_ENV["extract"], raising=False)
    assert wall_cap_seconds("extract") == 6 * 3600
    assert "source=run_config extract_wall_cap_hours" in format_wall_cap_setting_line("extract")
    monkeypatch.setenv("RO_EXTRACT_WALL_CAP_HOURS", "9")
    assert wall_cap_seconds("extract") == 9 * 3600
    assert format_wall_cap_setting_line("extract") == (
        "WALL_CAP stage=extract hours=9 wall_s=32400 source=env RO_EXTRACT_WALL_CAP_HOURS"
    )
    assert wall_cap_seconds("solve") == 48 * 3600
    for bad in ("0", "-1", "nan", "six"):
        monkeypatch.setenv("RO_EXTRACT_WALL_CAP_HOURS", bad)
        with pytest.raises(ValueError, match="RO_EXTRACT_WALL_CAP_HOURS"):
            wall_cap_seconds("extract")


def test_extract_driver_logs_the_cap(monkeypatch, capsys):
    driver = load_module("batch_report_extract_cap", SCRIPTS_DIR / "batch_report_extract.py")
    seen = {}

    def fake_tee(cmd, **kwargs):
        seen.update(kwargs)
        return "done"

    monkeypatch.setattr(driver, "tee_until_hang", fake_tee)
    monkeypatch.setenv("RO_EXTRACT_WALL_CAP_HOURS", "7.5")
    assert driver.run_extract_worker_tee(["x"], env={}, log_path="a.log") == "done"
    assert seen["wall_s"] == 7.5 * 3600
    assert "WALL_CAP stage=extract hours=7.5" in capsys.readouterr().out


def test_reextract_cli_sets_the_cap_for_its_extract_and_emits_it(monkeypatch, tmp_path):
    leaf = _leaf(tmp_path, "S_a072_l3465", "sin")
    _snapshot(leaf, "S_a072_l3465", 2000)
    # setenv first so monkeypatch restores "absent" after the worker sets it.
    monkeypatch.setenv("RO_EXTRACT_WALL_CAP_HOURS", "1")
    monkeypatch.delenv("RO_EXTRACT_WALL_CAP_HOURS")
    queue = tmp_path / "q.json"
    assert REEXTRACT._main([
        "--data-root", str(tmp_path), "--emit-queue", str(queue),
        "--snapshot", "S_a072_l3465:2000", "--extract-wall-cap-hours", "8",
    ]) == 0
    job = json.loads(queue.read_text(encoding="utf-8"))[0]
    assert job["id"] == "snap-S_a072_l3465-2000"
    assert job["argv"][-2:] == ["--extract-wall-cap-hours", "8"]
    assert "RO_EXTRACT_WALL_CAP_HOURS" not in os.environ  # emitting sets nothing

    seen = {}
    monkeypatch.setattr(
        REEXTRACT, "reextract_snapshot",
        lambda *a: seen.setdefault("cap", os.environ.get("RO_EXTRACT_WALL_CAP_HOURS"))
        and type("R", (), {"returncode": 3})(),
    )
    assert REEXTRACT._main([
        "--data-root", str(tmp_path), "--run-leaf", str(leaf),
        "--snapshot-iteration", "2000", "--extract-wall-cap-hours", "8",
    ]) == 3
    assert seen["cap"] == "8"


def _timing_namespace(tmp_path):
    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    names = {"_timing_payload", "_write_timing_progress", "_begin_extract_phase",
             "_end_extract_phase", "_reset_extract_timing", "_write_report_extract_timing"}
    namespace = {
        "time": time, "json": json, "os": os, "Path": Path,
        "REPORT_EXTRACT_TIMING_BUCKET_BOUNDARIES": {}, "PROFILE_MFBO": "mfbo",
        "_extract_profile_name": "full", "_extract_phase_seconds": {},
        "_segmented_membrane_cp_subphase_seconds": {}, "_completed_extract_phases": [],
        "_active_extract_phase": None, "_active_extract_phase_start": None,
        "_failed_extract_phase": None, "_extract_timing_started_at": None,
        "_extract_timing_progress_dir": tmp_path,
    }
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "extract", "exec"), namespace)
    return namespace


def test_timing_json_is_written_after_every_phase_and_names_the_slowest(tmp_path, capsys):
    ns = _timing_namespace(tmp_path)
    ns["_reset_extract_timing"]()
    ns["_begin_extract_phase"]("fluent_launch")
    ns["_begin_extract_phase"]("read_case_data")
    ns["_extract_phase_seconds"]["fluent_launch"] = 40.0
    ns["_begin_extract_phase"]("cell_8_compute")
    ns["_extract_phase_seconds"]["read_case_data"] = 900.0
    ns["_write_timing_progress"]()
    # Killed here (wall cap): the staging dir still shows how far it got.
    progress = json.loads((tmp_path / "report_extract_timing.json").read_text(encoding="utf-8"))
    assert progress["in_progress"] is True
    assert progress["active_phase"] == "cell_8_compute"
    assert progress["completed_phases"] == ["fluent_launch", "read_case_data"]
    assert progress["slowest_phase"] == "read_case_data"
    out = capsys.readouterr().out
    assert "EXTRACT_PHASE end name=fluent_launch" in out
    assert "EXTRACT_PHASE begin name=cell_8_compute" in out
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    total = ns["_write_report_extract_timing"](final_dir)
    final = json.loads((final_dir / "report_extract_timing.json").read_text(encoding="utf-8"))
    assert "in_progress" not in final and final["total_seconds"] == total
    assert final["phase_order"] == ["fluent_launch", "read_case_data", "cell_8_compute"]
    assert "EXTRACT_PHASE_TOTAL name=read_case_data seconds=900.0" in capsys.readouterr().out


# 4. Repair a leaf whose final equals a saved *_iter_<N> pair ---------------

def _stopped_continue_leaf(root):
    """D2450_a60 as on the host: RUNNING, no final hashes, final == iter 2500."""
    leaf = _leaf(root, "D2450_a60", "diamond", gate=None, hashes=False,
                 sidecar={"final_case_sha256": "1" * 64, "final_data_sha256": "2" * 64})
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    manifest.update({
        "stop_reason": "RUNNING", "iterations_run": 2500,
        "continue": {"start": 2000, "target": 4000, "continue_every": 500,
                     "chunks": [{"iterations": 2500, "stop_reason": "max_iter_reached"}]},
    })
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _snapshot(leaf, "D2450_a60", 2000, b"case-2000", b"data-2000")
    _snapshot(leaf, "D2450_a60", 2500, CAS, DAT)
    _snapshot(leaf, "D2450_a60", 1500, b"case-xxxx", b"data-xxxx")  # same sizes, other bytes
    return leaf


def test_repair_dry_run_writes_nothing_then_apply_and_emitter_picks_it_up(tmp_path, capsys):
    leaf = _stopped_continue_leaf(tmp_path)
    before = (leaf / "manifest.json").read_text(encoding="utf-8")
    seen = []
    assert REEXTRACT.pass_final_jobs(tmp_path, report=seen.append) == []
    assert any(line.startswith("SKIP") for line in seen)

    assert REPAIR.main(["--run-leaf", str(leaf)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "iter 002500" in out and "MATCH" in out
    assert (leaf / "manifest.json").read_text(encoding="utf-8") == before

    assert REPAIR.main(["--run-leaf", str(leaf), "--apply"]) == 0
    after = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    assert after["final_case_sha256"] == hashlib.sha256(CAS).hexdigest()
    assert after["final_data_sha256"] == hashlib.sha256(DAT).hexdigest()
    assert after["iterations_run"] == 2500
    assert after["stop_reason"] == "max_iter_reached"
    assert after["final_provenance"] == "repaired_from_iter_2500"
    assert after["final_repair"]["previous_stop_reason"] == "RUNNING"
    assert after["convergence_quality"] is None
    assert (leaf / "D2450_a60_u0p2_p6M_final.dat.h5").read_bytes() == DAT

    seen = []
    jobs = REEXTRACT.pass_final_jobs(tmp_path, report=seen.append)
    assert [job["id"] for job in jobs] == ["rx-D2450_a60"]
    assert any(line.startswith("STALE") and "different final" in line for line in seen)
    REEXTRACT.require_reextract_final(leaf, tmp_path)  # worker hash check passes
    assert REPAIR.main(["--run-leaf", str(leaf)]) == 2  # already repaired
    assert "nothing to repair" in capsys.readouterr().out


def test_repair_refuses_without_a_matching_pair_or_stop_reason(tmp_path, capsys):
    leaf = _stopped_continue_leaf(tmp_path)
    (leaf / "D2450_a60_u0p2_p6M_iter_002500.dat.h5").write_bytes(b"data-9999")
    with pytest.raises(REPAIR.RepairError, match="equals the final pair by hash"):
        REPAIR.plan_repair(leaf, report=lambda line: None)
    (leaf / "D2450_a60_u0p2_p6M_iter_002500.dat.h5").write_bytes(DAT)
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    manifest["continue"]["chunks"] = []
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(REPAIR.RepairError, match="pass --stop-reason"):
        REPAIR.plan_repair(leaf, report=lambda line: None)
    with pytest.raises(REPAIR.RepairError, match="solver stop reason"):
        REPAIR.plan_repair(leaf, stop_reason="RUNNING", report=lambda line: None)
    _leaf_, updated, n = REPAIR.plan_repair(leaf, stop_reason="max_iter_reached",
                                            report=lambda line: None)
    assert n == 2500 and updated["final_repair"]["stop_reason_source"] == "--stop-reason"
    for path in leaf.glob("*_iter_*"):
        path.unlink()
    assert REPAIR.main(["--run-leaf", str(leaf), "--apply"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_repair_writes_a_schema_valid_manifest(monkeypatch, tmp_path):
    from test_manifest import write_test_run

    from ro.manifest import read_run_manifest

    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    leaf = write_test_run(stop_reason="RUNNING")
    geo, run = leaf.parent.parent.name, leaf.name
    for ext, data in (("cas", CAS), ("dat", DAT)):
        (leaf / f"{geo}_{run}_final.{ext}.h5").write_bytes(data)
        (leaf / f"{geo}_{run}_iter_002500.{ext}.h5").write_bytes(data)
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path / "elsewhere"))
    leaf_, updated, _n = REPAIR.plan_repair(leaf, stop_reason="max_iter_reached",
                                            report=lambda line: None)
    REPAIR.apply_repair(leaf_, updated)
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    payload = read_run_manifest(leaf)
    assert payload["stop_reason"] == "max_iter_reached"
    assert payload["final_provenance"] == "repaired_from_iter_2500"


# 5. reports.tmp_* staging dirs are ignored ----------------------------------

def test_staging_dirs_are_ignored_by_binding_emitter_and_scans(tmp_path):
    from ro.atomic_reports import is_staging_path

    leaf = _leaf(tmp_path, "D0817_a30", "diamond", gate=None)
    staging = leaf / "post" / "reports.tmp_20261119T120000Z_4242"
    staging.mkdir()
    (staging / "summary_metrics_wide.csv").write_text("cp\n9.9\n", encoding="utf-8")
    (staging / "extract_source.json").write_text("{}", encoding="utf-8")
    assert report_binding_reason(leaf) is None
    assert REEXTRACT.pass_final_jobs(tmp_path, report=lambda line: None) == []
    assert is_staging_path(staging / "summary_metrics_wide.csv")
    assert not is_staging_path(leaf / "post" / "reports" / "summary_metrics_wide.csv")

    validator = load_module("validate_csvs_staging", SCRIPTS_DIR / "validate_load_bearing_summary_csvs.py")
    seen = []
    validator.classify_file = lambda path: seen.append(path) or ("PASS", {"missing": [],
                                                                          "legacy_exempt_missing": []}, [])
    try:
        validator.main([str(leaf)])
    except Exception:  # noqa: BLE001 - only the file list matters here
        pass
    assert seen and all(not is_staging_path(path) for path in seen)

    inventory = load_module("case_inventory_staging", SCRIPTS_DIR / "case_inventory.py")
    (staging / "solver_log_x.txt").write_text("x", encoding="utf-8")
    (leaf / "solver_log_u0p2_p6M.txt").write_text("x", encoding="utf-8")
    candidates, _transcripts, _latest = inventory.find_log_files(leaf)
    assert all(not is_staging_path(path) for path in candidates)


def test_follow_up_queue_with_geo_and_snapshots(tmp_path, capsys):
    """The host follow-up: D0817_a30 + repaired D2450_a60 + two S_a072 snapshots."""
    done = _leaf(tmp_path, "D1225_a45", "diamond")  # already PASS and bound: not emitted
    d0817 = _leaf(tmp_path, "D0817_a30", "diamond", gate=None)
    import shutil

    shutil.rmtree(d0817 / "post" / "reports")  # archived by the hung attempt
    (d0817 / "post" / "reports.tmp_20261119T101500Z_111").mkdir()
    a60 = _stopped_continue_leaf(tmp_path)
    assert REPAIR.main(["--run-leaf", str(a60), "--apply"]) == 0
    s072 = _leaf(tmp_path, "S_a072_l3465", "sin")
    _snapshot(s072, "S_a072_l3465", 2000)
    _snapshot(s072, "S_a072_l3465", 2500)
    queue = tmp_path / "followup.json"
    assert REEXTRACT._main([
        "--data-root", str(tmp_path), "--emit-queue", str(queue),
        "--geo", "D0817_a30", "--geo", "D2450_a60",
        "--snapshot", "S_a072_l3465:2000", "--snapshot", "S_a072_l3465:2500",
    ]) == 0
    jobs = QUEUE.load_queue(queue)
    assert [job["id"] for job in jobs] == [
        "rx-D0817_a30", "rx-D2450_a60", "snap-S_a072_l3465-2000", "snap-S_a072_l3465-2500",
    ]
    assert str(done) not in json.dumps(jobs)
    assert "PASS/stale-final + snapshot" in capsys.readouterr().out
    with pytest.raises(ValueError, match="no PASS or stale final"):
        REEXTRACT.pass_final_jobs(tmp_path, geo_ids=["D2450_a45"], report=lambda line: None)
    with pytest.raises(ValueError, match="go with --emit-queue"):
        REEXTRACT._main(["--data-root", str(tmp_path), "--run-leaf", str(a60), "--geo", "D0817_a30"])


# The repaired leaf's transcript runs past N (the stopped chunk) -------------

_HEADER = ("iter  continuity  x-velocity  y-velocity  z-velocity  nacl  "
           "lmh  m_out  m_in  area_mem  time/iter\n")


def _rows(first, last, continuity):
    return "".join(
        f"{i}  {continuity:.4e}  1.0000e-03  2.0000e-03  3.0000e-03  4.0000e-03  "
        f"1.0000e+02  -1.9090e-04  2.6629e-04  6.2423e-05  0:13:30  {9999 - i}\n"
        for i in range(first, last + 1)
    )


def test_repaired_final_takes_continuity_at_its_iteration_not_the_tail(tmp_path):
    from ro.convergence_quality import (
        continuity_final_from_case_dir,
        repaired_final_iteration,
    )

    log = tmp_path / "solver_log_u0p2_p6M.txt"
    log.write_text(_HEADER + _rows(2490, 2500, 2.0e-5) + _HEADER + _rows(2501, 2620, 9.0e-3),
                   encoding="utf-8")
    assert continuity_final_from_case_dir(tmp_path) == pytest.approx(9.0e-3)  # the tail
    assert continuity_final_from_case_dir(tmp_path, max_iteration=2500) == pytest.approx(2.0e-5)
    assert continuity_final_from_case_dir(tmp_path, max_iteration=3000) is None  # gate UNKNOWN, not PASS
    assert repaired_final_iteration({"final_provenance": "repaired_from_iter_2500"}) == 2500
    assert repaired_final_iteration({"final_provenance": None}) is None
    assert repaired_final_iteration({}) is None


def test_extract_bounds_continuity_and_blanks_monitors_for_a_repaired_final():
    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    assert "_repaired_iteration = repaired_final_iteration(run_manifest)" in source
    assert "max_iteration=_repaired_iteration" in source
    assert "report_binds_manifest(cfg) and _repaired_iteration is None" in source
