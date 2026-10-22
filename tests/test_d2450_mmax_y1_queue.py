"""D2450_a45 m_max queue at fixed membrane y1 2.0 um."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module

QUEUE = load_module(
    "d2450_mmax_y1_queue_under_test",
    SCRIPTS_DIR / "mfbo" / "d2450_mmax_y1_queue.py",
)


def test_queue_fixes_y1_and_varies_m_max():
    studies = QUEUE.mmax_study_overrides()
    assert [name for name, _overrides in studies] == [
        "max060",
        "max045",
        "max035",
    ]
    mesh_ids = [overrides["mesh_id"] for _name, overrides in studies]
    assert mesh_ids == [
        "max060_min006_cpg5_bl4_peel2_y1h020",
        "max045_min006_cpg5_bl4_peel2_y1h020",
        "max035_min006_cpg5_bl4_peel2_y1h020",
    ]
    assert QUEUE.EXISTING_MESH_ID not in mesh_ids
    assert [overrides["m_max"] for _name, overrides in studies] == [
        0.060,
        0.045,
        0.035,
    ]
    assert [overrides["membrane_bl_y1"]["y1_um"] for _name, overrides in studies] == [
        2.0,
        2.0,
        2.0,
    ]
    assert [overrides["membrane_bl_y1"]["layers"] for _name, overrides in studies] == [
        7,
        6,
        5,
    ]
    assert all(
        overrides["membrane_bl_y1"]["offset_method"] == "last-ratio"
        for _name, overrides in studies
    )
    assert all(
        overrides["membrane_bl_y1"]["first_height_mm"] == pytest.approx(0.004)
        for _name, overrides in studies
    )

    jobs = QUEUE.mmax_jobs(mesh_only=True)
    assert [job["id"] for job in jobs] == [
        "D2450_a45-max060-y1h020-mesh",
        "D2450_a45-max045-y1h020-mesh",
        "D2450_a45-max035-y1h020-mesh",
    ]
    for job, m_max in zip(jobs, ("0.060", "0.045", "0.035")):
        argv = job["argv"]
        assert argv[1] == "scripts/mfbo/mesh_study_case.py"
        assert argv[argv.index("--data-root") + 1] == "C:/ro_data_mfbo/meshstudy"
        assert argv[argv.index("--geometry-root") + 1] == (
            "C:/ro_data_mfbo/geometries_pmdb"
        )
        assert argv[argv.index("--geometry-suffix") + 1] == ".pmdb"
        assert argv[argv.index("--geo-id") + 1] == "D2450_a45"
        assert argv[argv.index("--case") + 1] == "u0p2_p6M"
        assert argv[argv.index("--m-max") + 1] == m_max
        assert argv[argv.index("--bl-layers") + 1] == "4"
        assert argv[argv.index("--membrane-bl-y1-um") + 1] == "2"
        assert "--mesh-only" in argv
        assert "--reuse-mesh" not in argv

    full = QUEUE.mmax_jobs(mesh_only=False)
    assert [job["id"] for job in full] == [
        "D2450_a45-max060-y1h020",
        "D2450_a45-max045-y1h020",
        "D2450_a45-max035-y1h020",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in full)
    assert all("--mesh-only" not in job["argv"] for job in full)


def test_full_emitter_reuses_mesh_only_leaves(tmp_path, capsys):
    destination = tmp_path / "full.json"
    code = QUEUE.main(["--emit-queue", str(destination)])
    assert code == 0
    jobs = json.loads(destination.read_text(encoding="utf-8"))
    assert [job["id"] for job in jobs] == [
        "D2450_a45-max060-y1h020",
        "D2450_a45-max045-y1h020",
        "D2450_a45-max035-y1h020",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in jobs)
    printed = capsys.readouterr().out
    assert "max060: max060_min006_cpg5_bl4_peel2_y1h020" in printed
    assert "Wrote 3 jobs" in printed
    assert QUEUE.EXISTING_MESH_ID not in printed


def _plant_mesh_manifest(root, family, mesh_id, cell_count):
    leaf = root / "meshes" / family / "D2450_a45" / mesh_id
    leaf.mkdir(parents=True)
    (leaf / "manifest.json").write_text(
        json.dumps({"cell_count": cell_count}),
        encoding="utf-8",
    )


def test_max_cells_drops_variants_over_the_mesh_only_count(tmp_path, capsys, monkeypatch):
    studies = QUEUE.mmax_study_overrides()
    counts = {"max060": 4_000_000, "max045": 5_500_000, "max035": 7_000_000}
    for name, overrides in studies:
        _plant_mesh_manifest(
            tmp_path,
            overrides["family"],
            overrides["mesh_id"],
            counts[name],
        )
    kept, dropped = QUEUE.variants_under_cell_cap(studies, 6_000_000, tmp_path)
    assert [name for name, _overrides in kept] == ["max060", "max045"]
    assert [item[0] for item in dropped] == ["max035"]
    jobs = QUEUE.mmax_jobs(studies=kept, data_root=tmp_path)
    assert [job["id"] for job in jobs] == [
        "D2450_a45-max060-y1h020",
        "D2450_a45-max045-y1h020",
    ]
    assert all(job["argv"][-1] == "--reuse-mesh" for job in jobs)

    monkeypatch.setattr(QUEUE, "DATA_ROOT", str(tmp_path))
    destination = tmp_path / "capped.json"
    code = QUEUE.main(
        ["--emit-queue", str(destination), "--max-cells", "6000000"]
    )
    assert code == 0
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert [job["id"] for job in written] == [
        "D2450_a45-max060-y1h020",
        "D2450_a45-max045-y1h020",
    ]
    printed = capsys.readouterr().out
    assert "dropped max035" in printed
    assert "cell_count 7000000 > 6000000" in printed
    with pytest.raises(ValueError, match="every variant exceeds"):
        QUEUE.main(
            ["--emit-queue", str(tmp_path / "empty.json"), "--max-cells", "1"]
        )
    assert not (tmp_path / "empty.json").exists()


def test_max_cells_does_not_apply_to_mesh_only(tmp_path):
    with pytest.raises(ValueError, match="Omit --mesh-only"):
        QUEUE.main(
            [
                "--emit-queue",
                str(tmp_path / "queue.json"),
                "--mesh-only",
                "--max-cells",
                "1000",
            ]
        )
    assert not (tmp_path / "queue.json").exists()
