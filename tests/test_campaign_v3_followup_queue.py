"""Follow-up queue: P_p100_h00 continue and D2450_a30 init-from solve."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module

FOLLOW = load_module("campaign_v3_followup_queue_test", SCRIPTS_DIR / "campaign_v3_followup_queue.py")
QUEUE = load_module("job_queue_followup_queue", SCRIPTS_DIR / "mfbo" / "job_queue.py")
STANDARD = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


def test_emit_two_jobs_that_load_and_refuse_overwrite(tmp_path):
    path = tmp_path / "followup.json"
    assert FOLLOW.main(["--emit-queue", str(path), "--data-root", str(tmp_path / "root")]) == 0
    jobs = QUEUE.load_queue(path)
    assert [job["id"] for job in jobs] == ["cont-P_p100_h00", "init-D2450_a30-u0p2"]
    cont, init = (job["argv"] for job in jobs)
    assert _arg(cont, "--geo-id") == "P_p100_h00" and _arg(cont, "--case") == "u0p2_p6M"
    assert _arg(cont, "--continue-iterations") == "2000" and _arg(cont, "--continue-every") == "500"
    assert "--reuse-mesh" in cont and "--max-iterations" not in cont and "--init-from" not in cont
    assert "--max-aspect-ratio" not in cont
    assert _arg(init, "--geo-id") == "D2450_a30" and _arg(init, "--case") == "u0p2_p6M"
    assert "--require-mesh" in init and _arg(init, "--max-iterations") == "2000"
    assert "--continue-iterations" not in init and "--cp-live" not in init
    root = _arg(init, "--data-root")
    assert _arg(init, "--init-from") == f"{root}/runs/diamond/D2450_a30/{STANDARD}/u0p1_p6M"
    assert all(job["timeout_s"] > 0 for job in jobs)
    assert jobs[0]["timeout_s"] == (48 + 5 * 6 + 1) * 3600.0
    with pytest.raises(SystemExit, match="existing queue"):
        FOLLOW.main(["--emit-queue", str(path)])


def test_dry_run_shows_leaf_states(tmp_path, capsys):
    root = tmp_path / "root"
    leaf = root / "runs" / "diamond" / "D2450_a30" / STANDARD / "u0p1_p6M"
    leaf.mkdir(parents=True)
    (leaf / "manifest.json").write_text(json.dumps(
        {"convergence_quality": "PASS", "iterations_run": 1400, "stop_reason": "qoi_converged"}),
        encoding="utf-8")
    assert FOLLOW.main(["--dry-run", "--data-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "source u0p1_p6M: gate=PASS iterations_run=1400" in out
    assert "DRY RUN" in out and not list(tmp_path.glob("*.json"))
    assert FOLLOW.continue_timeout_hours(6.5) == 44.0  # ceil(13 + 30 + 1)
    pillar = root / "runs" / "pillar" / "P_p100_h00" / STANDARD / "u0p2_p6M"
    pillar.mkdir(parents=True)
    (pillar / "manifest.json").write_text(json.dumps(
        {"convergence_quality": "FAIL", "iterations_run": 2000,
         "qoi_stop_reports": ["lmh", "dp"], "qoi_stop_include_cp": False}), encoding="utf-8")
    assert FOLLOW.main(["--dry-run", "--data-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "leaf: gate=FAIL iterations_run=2000" in out
    assert "stop rule: qoi_stop_reports=['lmh', 'dp'] qoi_stop_include_cp=False" in out
