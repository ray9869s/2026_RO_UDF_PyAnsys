"""Re-extract queue for the five mesh-study leaves whose extract failed."""

from __future__ import annotations

import json

from helpers import SCRIPTS_DIR, load_module

queue = load_module(
    "meshstudy_reextract_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "meshstudy_reextract_queue.py",
)
reextract_runs = load_module(
    "reextract_runs_for_meshstudy_queue",
    SCRIPTS_DIR / "mfbo" / "reextract_runs.py",
)

EXPECTED = (
    (
        "D2450_a45-y1h057-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h057",
    ),
    (
        "D2450_a45-y1h030-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h030",
    ),
    (
        "D2450_a45-y1h020-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h020",
    ),
    (
        "D2450_a45-y1h014-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h014",
    ),
    (
        "D0817_a30-lpx13m002-reextract",
        "D0817_a30",
        "max045_min006_cpg5_bl4_peel2_lpx13m002",
    ),
)


def test_queue_reextracts_exactly_the_five_finished_leaves(tmp_path, capsys):
    jobs = queue.reextract_jobs()
    assert [job["id"] for job in jobs] == [item[0] for item in EXPECTED]
    assert len(jobs) == 5
    for job, (job_id, geo_id, mesh_id) in zip(jobs, EXPECTED):
        argv = job["argv"]
        assert argv[:2] == ["{python}", "scripts/mfbo/reextract_runs.py"]
        assert "--profile" not in argv
        assert "--fidelity-n-lead-excluded" not in argv
        assert "--window-source" not in argv
        assert "mesh_study_case.py" not in " ".join(argv)
        assert "--reuse-mesh" not in argv
        assert "--mesh-only" not in argv
        leaf, data_root, family, parsed_geo, parsed_mesh, run_id = (
            reextract_runs.parse_run_leaf(argv[-1])
        )
        assert job_id == job["id"]
        assert str(data_root).replace("\\", "/") == "C:/ro_data_mfbo/meshstudy"
        assert family == "diamond"
        assert parsed_geo == geo_id
        assert parsed_mesh == mesh_id
        assert run_id == "u0p2_p6M"
        assert leaf.name == "u0p2_p6M"
    assert "y1h010" not in json.dumps(jobs)

    destination = tmp_path / "reextract.json"
    code = queue.main(["--emit-queue", str(destination)])
    assert code == 0
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert [job["id"] for job in written] == [job["id"] for job in jobs]
    printed = capsys.readouterr().out
    assert "Wrote 5 jobs" in printed
    assert "max045_min006_cpg5_bl4_peel2_lpx13m002" in printed
