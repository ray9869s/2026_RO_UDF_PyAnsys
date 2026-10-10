"""Campaign .pmdb parity against .dsco. Does not launch Fluent."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER

parity = load_module(
    "parity_campaign_pmdb_under_test",
    SCRIPTS_DIR / "mfbo" / "parity_campaign_pmdb.py",
)


def _dump(*, face_count=4, volume=12.5, area=2.5, box=None):
    if box is None:
        box = [[0.0, -1.0, -0.385], [10.0, 1.0, 0.385]]
    return {
        "all_face_zone_ids": [1, 2],
        "labels": {
            "fluid": {
                "face_zone_ids": [1, 2],
                "bounding_box_mm": box,
                "face_zone_area": area + 1.0,
                "face_count": face_count + 1,
            },
            "wall_spacer": {
                "face_zone_ids": [2],
                "bounding_box_mm": box,
                "face_zone_area": area,
                "face_count": face_count,
            },
        },
        "bodies": [
            {
                "name": "fluid",
                "regions": ["fluid"],
                "volumes": [volume],
                "error": None,
            }
        ],
        "overall_bounding_box_mm": box,
    }


def test_matching_dumps_pass_within_pillar_tolerances():
    reference = _dump()
    current = _dump(area=2.5 * 1.0005, volume=12.5 * 1.0005)
    judgement = parity.judge_dumps(reference, current)
    assert judgement["passed"] is True
    assert judgement["volume"]["status"] == "compared"
    assert judgement["volume"]["rel_diff"] <= parity.VOLUME_RTOL


def test_face_count_area_box_and_volume_failures():
    reference = _dump()
    faces = _dump(face_count=9)
    assert parity.judge_dumps(reference, faces)["passed"] is False
    shifted = _dump(box=[[0.0, -1.0, -0.385], [10.0, 1.0, 0.385 + 0.01]])
    assert parity.judge_dumps(reference, shifted)["passed"] is False
    volume = _dump(volume=20.0)
    judged = parity.judge_dumps(reference, volume)
    assert judged["passed"] is False
    assert judged["volume"]["rel_diff"] > parity.VOLUME_RTOL
    unread = _dump()
    unread["bodies"] = [{"name": "fluid", "error": "unread", "volumes": None}]
    assert parity.judge_dumps(reference, unread)["passed"] is False


def test_run_one_uses_the_probe_and_continues(tmp_path):
    source = tmp_path / "source"
    pmdb_root = tmp_path / "pmdb"
    work = tmp_path / "work"
    for geo_id, family in (("P_p100_h30", "pillar"), ("REF_empty", "empty")):
        dsco = source / family / geo_id / f"{geo_id}.dsco"
        pmdb = pmdb_root / family / geo_id / f"{geo_id}.pmdb"
        dsco.parent.mkdir(parents=True)
        pmdb.parent.mkdir(parents=True)
        dsco.write_bytes(b"dsco")
        pmdb.write_bytes(b"pmdb")

    def runner(command):
        work_dir = command[command.index("--work-dir") + 1]
        cad = command[command.index("--cad-path") + 1]
        payload = _dump(volume=1.0 if cad.endswith("REF_empty.pmdb") else 12.5)
        destination = __import__("pathlib").Path(work_dir) / parity.SUMMARY_NAME
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    records = parity.run_all(
        ["P_p100_h30", "REF_empty"],
        source_root=source,
        pmdb_root=pmdb_root,
        work_root=work,
        runner=runner,
    )
    assert [record["status"] for record in records] == ["passed", "failed"]
    assert "--family" in parity.probe_command("a.dsco", "work")
    assert "campaign" in parity.probe_command("a.dsco", "work")


def test_emit_queue_lists_every_campaign_geometry(tmp_path):
    path = tmp_path / "parity_queue.json"
    code = parity.main(["--emit-queue", str(path)])
    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0
    assert len(jobs) == len(CAMPAIGN_GEO_ID_ORDER) == 31
    assert jobs[0]["id"] == f"parity-{CAMPAIGN_GEO_ID_ORDER[0]}"
    assert jobs[0]["argv"][1].endswith("parity_campaign_pmdb.py")


def test_work_root_under_manual_cad_is_refused():
    with pytest.raises(ValueError, match="manual CAD"):
        parity.resolve_work_root("C:/ro_data/geometries/pillar")
