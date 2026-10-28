"""D2450_a45 bulk-only queue at fixed m_max 0.035 and y1 2.0 um."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module

QUEUE = load_module(
    "d2450_bulk_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "d2450_bulk_queue.py",
)


def test_queue_fixes_surface_and_varies_bulk():
    studies = QUEUE.bulk_study_overrides()
    assert [name for name, _overrides in studies] == [
        "bms034",
        "bms026",
        "bms020",
    ]
    mesh_ids = [overrides["mesh_id"] for _name, overrides in studies]
    assert mesh_ids == [
        "max035_min006_cpg5_bl4_peel2_y1h020_bms034",
        "max035_min006_cpg5_bl4_peel2_y1h020_bms026",
        "max035_min006_cpg5_bl4_peel2_y1h020_bms020",
    ]
    assert [overrides["m_max"] for _name, overrides in studies] == [
        0.035,
        0.035,
        0.035,
    ]
    assert [overrides["bulk_max_size_mm"] for _name, overrides in studies] == [
        0.034,
        0.026,
        0.020,
    ]
    assert [overrides["membrane_bl_y1"]["layers"] for _name, overrides in studies] == [
        5,
        5,
        5,
    ]
    assert all("membrane_bl_layers" not in overrides for _name, overrides in studies)
    assert QUEUE.expected_cell_counts() == {
        "bms034": 6_300_000,
        "bms026": 12_000_000,
        "bms020": 25_000_000,
    }

    jobs = QUEUE.bulk_jobs(mesh_only=True, studies=studies)
    assert [job["id"] for job in jobs] == [
        "D2450_a45-bms034-y1h020-mesh",
        "D2450_a45-bms026-y1h020-mesh",
        "D2450_a45-bms020-y1h020-mesh",
    ]
    for job, size_mm in zip(jobs, ("0.034", "0.026", "0.020")):
        argv = job["argv"]
        assert argv[1] == "scripts/mfbo/mesh_study_case.py"
        assert argv[argv.index("--data-root") + 1] == "C:/ro_data_mfbo/meshstudy"
        assert argv[argv.index("--geometry-root") + 1] == (
            "C:/ro_data_mfbo/geometries_pmdb"
        )
        assert argv[argv.index("--geometry-suffix") + 1] == ".pmdb"
        assert argv[argv.index("--geo-id") + 1] == "D2450_a45"
        assert argv[argv.index("--case") + 1] == "u0p2_p6M"
        assert argv[argv.index("--m-max") + 1] == "0.035"
        assert argv[argv.index("--bl-layers") + 1] == "4"
        assert argv[argv.index("--membrane-bl-y1-um") + 1] == "2"
        assert argv[argv.index("--bulk-max-size") + 1] == size_mm
        assert argv[-1] == "--mesh-only"
        assert "--reuse-mesh" not in argv
        assert "--checkpoint-every" not in argv
        assert "expected_cells" not in json.dumps(job)

    full = QUEUE.bulk_jobs(mesh_only=False, studies=studies)
    assert [job["id"] for job in full] == [
        "D2450_a45-bms034-y1h020",
        "D2450_a45-bms026-y1h020",
        "D2450_a45-bms020-y1h020",
    ]
    for job in full:
        argv = job["argv"]
        assert "--mesh-only" not in argv
        assert argv[argv.index("--reuse-mesh") - 0] == "--reuse-mesh"
        assert argv[argv.index("--checkpoint-every") + 1] == "200"


def test_emitter_prints_layers_and_expected_cells(tmp_path, capsys):
    path = tmp_path / "queue.json"
    assert QUEUE.main(["--emit-queue", str(path), "--mesh-only"]) == 0
    printed = capsys.readouterr().out
    assert (
        "bms034: max035_min006_cpg5_bl4_peel2_y1h020_bms034 "
        "membrane_bl_layers=5 expected_cells=6300000"
    ) in printed
    assert "expected_cells=12000000" in printed
    assert "expected_cells=25000000" in printed
    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert jobs[0]["argv"][-1] == "--mesh-only"
    assert "6300000" not in path.read_text(encoding="utf-8")
