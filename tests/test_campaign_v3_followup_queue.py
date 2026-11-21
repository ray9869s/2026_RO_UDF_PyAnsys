"""Follow-up queue: the D2450_a30 init-from solve only."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module

FOLLOW = load_module("campaign_v3_followup_queue_test", SCRIPTS_DIR / "campaign_v3_followup_queue.py")
QUEUE = load_module("job_queue_followup_queue", SCRIPTS_DIR / "mfbo" / "job_queue.py")
STANDARD = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


def test_emit_one_init_job_that_loads_and_refuses_overwrite(tmp_path):
    path = tmp_path / "followup.json"
    assert FOLLOW.main(["--emit-queue", str(path), "--data-root", str(tmp_path / "root")]) == 0
    jobs = QUEUE.load_queue(path)
    assert [job["id"] for job in jobs] == ["init-D2450_a30-u0p2"]
    init = jobs[0]["argv"]
    assert not any("P_p100_h00" in token for token in init)
    assert _arg(init, "--geo-id") == "D2450_a30" and _arg(init, "--case") == "u0p2_p6M"
    assert "--require-mesh" in init and _arg(init, "--max-iterations") == "2000"
    assert "--continue-iterations" not in init and "--cp-live" not in init
    root = _arg(init, "--data-root")
    assert _arg(init, "--init-from") == f"{root}/runs/diamond/D2450_a30/{STANDARD}/u0p1_p6M"
    assert jobs[0]["timeout_s"] > 0
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
    assert "DRY RUN" in out and "cont-" not in out and not list(tmp_path.glob("*.json"))
