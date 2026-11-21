"""campaign_v3_main_queue: order, mesh choice, ids, timeouts, --dry-run."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.hang_watchdog import WALL_CAP_ENV, wall_cap_setting

MAIN = load_module("campaign_v3_main_queue_test", SCRIPTS_DIR / "campaign_v3_main_queue.py")
CAMPAIGN = MAIN.campaign
QUEUE = load_module("job_queue_main_queue", SCRIPTS_DIR / "mfbo" / "job_queue.py")
STANDARD = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
AR = STANDARD + "_ar250"


def _plant_mesh(root, geo_id, mesh_id, cells):
    path = CAMPAIGN.mesh_manifest_path(root, geo_id, mesh_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"cell_count": cells}), encoding="utf-8")


def _rows(root):
    spec_path = MAIN.resolve_spec_path(MAIN.default_spec_path())
    return MAIN.plan(root, MAIN.load_mesh_spec(spec_path), spec_path)


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


def test_order_and_mesh_choice(tmp_path):
    rows = _rows(tmp_path)
    assert len(rows) == 2 + 1 + 4 + 14 + 31
    assert [row["step"] for row in rows] == sorted(row["step"] for row in rows)
    keys = [(row["kind"], row["geo_id"], row["run_id"]) for row in rows]
    assert keys[:7] == [
        ("mesh", "S_a072_l1733", "-"), ("mesh", "S_a144_l1733", "-"),
        ("solve", "P_p80_h15", "u0p05_p6M"),
        ("solve", "D1225_a30", "u0p2_p6M"), ("solve", "S_a072_l1733", "u0p2_p6M"),
        ("solve", "S_a144_l1733", "u0p2_p6M"), ("solve", "S_a193_l1733", "u0p2_p6M"),
    ]
    step4 = [(row["run_id"], row["geo_id"]) for row in rows if row["step"] == 4]
    assert step4[:4] == [("u0p05_p6M", g) for g in ("REF_empty", "D2450_a45", "M_c267", "S_a144_l3465")]
    assert ("u0p05_p6M", "P_p80_h15") not in step4
    assert step4[4:9] == [("u0p15_p6M", g) for g in MAIN.EXTRA_VELOCITY_GEO_IDS]
    assert step4[9:] == [("u0p25_p6M", g) for g in MAIN.EXTRA_VELOCITY_GEO_IDS]
    assert [row["geo_id"] for row in rows if row["step"] == 5] == list(CAMPAIGN_GEO_ID_ORDER)
    assert all(row["run_id"] == "u0p1_p6M" for row in rows if row["step"] == 5)
    for row in rows:
        ar = row["geo_id"] in MAIN.AR250_GEO_IDS
        assert row["mesh_id"] == (AR if ar else STANDARD)
        argv = row["job"]["argv"]
        if ar:
            assert _arg(argv, "--max-aspect-ratio") == "250" and "--max-prism-aspect-ratio" not in argv
        else:
            assert "--max-aspect-ratio" not in argv
        assert _arg(argv, "--geo-id") == row["geo_id"]


def test_solves_require_mesh_cap_2000_cp_live_off(tmp_path):
    for row in _rows(tmp_path):
        argv = row["job"]["argv"]
        if row["kind"] == "mesh":
            assert "--mesh-only" in argv and "--require-mesh" not in argv
            continue
        assert "--require-mesh" in argv and "--reuse-mesh" not in argv
        assert _arg(argv, "--max-iterations") == "2000"
        assert _arg(argv, "--case") == row["run_id"]
        assert "--cp-live" not in argv
        assert _arg(argv, "--convergence-profile") == "campaign_v3"


def test_emitted_queue_loads_and_every_job_has_a_timeout(tmp_path):
    path = tmp_path / "queues" / "main.json"
    assert MAIN.main(["--emit-queue", str(path), "--data-root", str(tmp_path / "root")]) == 0
    jobs = QUEUE.load_queue(path)
    assert len(jobs) == 52 and all(job["timeout_s"] > 0 for job in jobs)
    with pytest.raises(SystemExit, match="existing queue"):
        MAIN.main(["--emit-queue", str(path), "--data-root", str(tmp_path / "root")])
    with pytest.raises(FileExistsError):
        MAIN.write_queue_exclusive(path, jobs)


def test_dry_run_writes_nothing_and_prints_states(tmp_path, capsys):
    root = tmp_path / "root"
    _plant_mesh(root, "REF_empty", STANDARD, 4_340_000)
    _plant_mesh(root, "S_a193_l1733", AR, 8_115_516)
    _plant_mesh(root, "S_a072_l1733", STANDARD, 1_000_000)
    leaf = CAMPAIGN.run_leaf(root, "REF_empty", STANDARD, "u0p1_p6M")
    leaf.mkdir(parents=True)
    assert MAIN.main(["--dry-run", "--data-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN: nothing written." in out and not list(tmp_path.glob("*.json"))
    rows = {row["job"]["id"]: row for row in _rows(root)}
    ref = rows["u0p05_p6M-REF_empty"]
    assert ref["cells"] == 4_340_000 and ref["state"] == "mesh ok"
    assert ref["estimate_h"] == pytest.approx(6.5 + MAIN.EXTRACT_ESTIMATE_HOURS)
    assert ref["timeout_h"] == 20.0  # ceil(2 x 6.5 + 6 + 1)
    assert "RUN LEAF EXISTS" in rows["u0p1_p6M-REF_empty"]["state"]
    big = rows["u0p2_p6M-S_a193_l1733_ar250"]
    assert big["timeout_h"] == 32.0 and big["job"]["timeout_s"] == 32.0 * 3600
    proxy = rows["u0p2_p6M-S_a072_l1733_ar250"]
    assert proxy["state"] == "mesh from step 1 (cells from standard mesh)"
    assert proxy["cells"] == 1_000_000 and proxy["timeout_h"] == MAIN.SOLVE_TIMEOUT_MIN_HOURS
    missing = rows["u0p1_p6M-D2450_a30"]
    assert missing["state"].startswith("MESH MISSING") and missing["estimate_h"] is None
    assert missing["timeout_h"] == MAIN.SOLVE_TIMEOUT_MAX_HOURS
    mesh = rows["mesh-S_a144_l1733_ar250"]
    assert mesh["timeout_h"] == MAIN.MESH_WALL_CAP_HOURS + 1 and mesh["state"] == "new mesh"
    for job_id in rows:
        assert job_id in out
    with pytest.raises(SystemExit, match="dry-run or --emit-queue"):
        MAIN.main([])


def test_timeouts_track_the_watchdog_caps(monkeypatch):
    for name in WALL_CAP_ENV.values():
        monkeypatch.delenv(name, raising=False)
    assert wall_cap_setting("mesh")[0] == MAIN.MESH_WALL_CAP_HOURS
    assert wall_cap_setting("solve")[0] == MAIN.SOLVE_WALL_CAP_HOURS
    assert wall_cap_setting("extract")[0] == MAIN.EXTRACT_WALL_CAP_HOURS


def test_ar250_geometries_are_the_contact_four():
    assert MAIN.AR250_GEO_IDS == ("D1225_a30", "S_a072_l1733", "S_a144_l1733", "S_a193_l1733")
    assert {family_for_geo_id(g) for g in MAIN.EXTRA_VELOCITY_GEO_IDS} == set(CAMPAIGN.FAMILIES)
