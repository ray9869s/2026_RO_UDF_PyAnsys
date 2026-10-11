"""Campaign .pmdb parity against .dsco. Does not launch Fluent."""

from __future__ import annotations

import json
from datetime import datetime, timezone

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
    one_sided = parity.judge_dumps(reference, unread)
    assert one_sided["passed"] is False
    assert one_sided["status"] == "failed"
    both = _dump()
    both["bodies"] = [
        {
            "name": "origin-p_p80_h15-solid",
            "regions": None,
            "volumes": None,
            "error": "TypeError: 'NoneType' object is not iterable",
        }
    ]
    unread_both = parity.judge_dumps(both, both)
    assert unread_both["comparison_passed"] is True
    assert unread_both["status"] == "passed_without_volume"
    assert unread_both["passed"] is False
    assert "origin-p_p80_h15-solid" in unread_both["volume"]["read_error"]
    assert "NoneType" in unread_both["volume"]["read_error"]


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


def test_a_failed_dsco_probe_names_the_cad_import_error_and_retries_once(tmp_path):
    source = tmp_path / "source"
    pmdb_root = tmp_path / "pmdb"
    work = tmp_path / "work"
    geo_id = "P_p100_h30"
    dsco = source / "pillar" / geo_id / f"{geo_id}.dsco"
    pmdb = pmdb_root / "pillar" / geo_id / f"{geo_id}.pmdb"
    dsco.parent.mkdir(parents=True)
    pmdb.parent.mkdir(parents=True)
    dsco.write_bytes(b"dsco")
    pmdb.write_bytes(b"pmdb")
    calls = {"dsco": 0}
    killed = []
    listings = {"n": 0}

    def lister():
        listings["n"] += 1
        if listings["n"] == 2:
            return [
                {"pid": 4, "line": "SpaceClaim.exe,4"},
                {"pid": 55, "line": "Discovery.exe,55"},
            ]
        return [{"pid": 4, "line": "SpaceClaim.exe,4"}]

    def runner(command):
        cad = command[command.index("--cad-path") + 1]
        work_dir = __import__("pathlib").Path(command[command.index("--work-dir") + 1])
        work_dir.mkdir(parents=True, exist_ok=True)
        if cad.endswith(".dsco"):
            calls["dsco"] += 1
            (work_dir / parity.TRANSCRIPT_NAME).write_text(
                "Error in CAD Import: Discovery plug-in could not open the file\n",
                encoding="utf-8",
            )
            return {
                "returncode": 1,
                "output": "PROBE FAILED: RuntimeError: Error in CAD Import",
            }
        payload = _dump()
        (work_dir / parity.SUMMARY_NAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return 0

    record = parity.run_one(
        geo_id,
        source_root=source,
        pmdb_root=pmdb_root,
        work_root=work,
        runner=runner,
        process_lister=lister,
        process_killer=killed.append,
    )
    assert record["status"] == "failed"
    assert calls["dsco"] == 2
    assert "dsco probe failed" in record["error"]
    assert "Error in CAD Import" in record["error"]
    assert "Probe dump missing" not in record["error"]
    assert killed == [55]
    assert record["probes"]["dsco"]["attempts"] == 2
    assert record["probes"]["dsco"]["processes_before"] == ["SpaceClaim.exe,4"]


def test_failed_parity_is_archived_and_passed_parity_is_kept(tmp_path):
    now = datetime(2026, 10, 11, 4, 1, tzinfo=timezone.utc)
    source = tmp_path / "source"
    pmdb_root = tmp_path / "pmdb"
    work = tmp_path / "work"
    geo_id = "P_p100_h30"
    dsco = source / "pillar" / geo_id / f"{geo_id}.dsco"
    pmdb = pmdb_root / "pillar" / geo_id / f"{geo_id}.pmdb"
    dsco.parent.mkdir(parents=True)
    pmdb.parent.mkdir(parents=True)
    dsco.write_bytes(b"dsco")
    pmdb.write_bytes(b"pmdb")
    previous = work / geo_id / "parity.json"
    previous.parent.mkdir(parents=True)
    previous.write_text(
        json.dumps({"geo_id": geo_id, "status": "failed", "error": "Error in CAD Import"}),
        encoding="utf-8",
    )
    calls = {"n": 0}

    def runner(command):
        calls["n"] += 1
        work_dir = __import__("pathlib").Path(command[command.index("--work-dir") + 1])
        destination = work_dir / parity.SUMMARY_NAME
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(_dump()), encoding="utf-8")
        return 0

    record = parity.run_one(
        geo_id,
        source_root=source,
        pmdb_root=pmdb_root,
        work_root=work,
        runner=runner,
        process_lister=lambda: [],
        process_killer=lambda pid: None,
        now=now,
    )
    archived = work / geo_id / "parity_failed_20261011T040100Z.json"
    assert json.loads(archived.read_text(encoding="utf-8"))["error"] == "Error in CAD Import"
    assert record["status"] == "passed"
    assert json.loads(previous.read_text(encoding="utf-8"))["status"] == "passed"
    calls["n"] = 0
    with pytest.raises(FileExistsError, match="passed"):
        parity.run_one(
            geo_id,
            source_root=source,
            pmdb_root=pmdb_root,
            work_root=work,
            runner=runner,
            process_lister=lambda: [],
            process_killer=lambda pid: None,
            now=now,
        )
    assert calls["n"] == 0


def test_reevaluate_uses_the_record_and_does_not_probe(tmp_path, capsys):
    now = datetime(2026, 10, 11, 5, 0, tzinfo=timezone.utc)
    work = tmp_path / "work"
    geo_id = "P_p80_h15"
    geo_dir = work / geo_id
    unread = {
        "name": "origin-p_p80_h15-solid",
        "regions": None,
        "volumes": None,
        "error": "TypeError: 'NoneType' object is not iterable",
    }
    for side in ("dsco", "pmdb"):
        dump = geo_dir / side / parity.SUMMARY_NAME
        dump.parent.mkdir(parents=True)
        dump.write_text(
            json.dumps({"bodies": [unread], "labels": {}}),
            encoding="utf-8",
        )
    record = {
        "geo_id": geo_id,
        "status": "failed",
        "passed": False,
        "comparison_passed": True,
        "face_counts": [
            {"label": "body", "reference": 14823, "current": 14823, "status": "PASS"},
            {"label": "wall_spacer_pillar", "reference": 7366, "current": 7366, "status": "PASS"},
        ],
        "volume": {
            "status": "unread",
            "current_mm3": None,
            "reference_mm3": None,
            "rel_diff": None,
        },
    }
    path = geo_dir / "parity.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    def runner(_command):
        raise AssertionError("reevaluate must not probe")

    with pytest.raises(ValueError, match="probe"):
        parity.main(
            ["--reevaluate", "--only", geo_id, "--work-root", str(work)],
            runner=runner,
        )
    code = parity.main(
        ["--reevaluate", "--only", geo_id, "--work-root", str(work)],
        now=now,
    )
    assert code == 0
    archived = geo_dir / "parity_reevaluated_20261011T050000Z.json"
    assert json.loads(archived.read_text(encoding="utf-8"))["status"] == "failed"
    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["status"] == "passed_without_volume"
    assert "TypeError: 'NoneType' object is not iterable" in updated["volume"]["read_error"]
    assert "origin-p_p80_h15-solid" in updated["volume"]["read_error"]
    printed = capsys.readouterr().out
    assert f"{geo_id} failed -> passed_without_volume" in printed

    calls = {"n": 0}

    def counting_runner(_command):
        calls["n"] += 1
        return 0

    with pytest.raises(FileExistsError, match="passed_without_volume"):
        parity.run_one(
            geo_id,
            source_root=tmp_path / "source",
            pmdb_root=tmp_path / "pmdb",
            work_root=work,
            runner=counting_runner,
            process_lister=lambda: [],
            process_killer=lambda pid: None,
        )
    assert calls["n"] == 0

    one_sided = geo_dir / "parity.json"
    one_sided.write_text(
        json.dumps(
            {
                **record,
                "volume": {
                    "status": "unread",
                    "current_mm3": 12.5,
                    "reference_mm3": None,
                    "rel_diff": None,
                },
            }
        ),
        encoding="utf-8",
    )
    later = datetime(2026, 10, 11, 5, 1, tzinfo=timezone.utc)
    code = parity.main(
        ["--reevaluate", "--only", geo_id, "--work-root", str(work)],
        now=later,
    )
    assert code == 2
    assert json.loads(one_sided.read_text(encoding="utf-8"))["status"] == "failed"
    assert (geo_dir / "parity_reevaluated_20261011T050100Z.json").is_file()
