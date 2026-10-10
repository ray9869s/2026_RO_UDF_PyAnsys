"""Campaign .dsco to .pmdb conversion. Does not launch Discovery."""

from __future__ import annotations

import hashlib
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER

convert = load_module(
    "convert_campaign_dsco_to_pmdb_under_test",
    SCRIPTS_DIR / "mfbo" / "convert_campaign_dsco_to_pmdb.py",
)


class _Design:
    def __init__(self, name, payload):
        self.name = name
        self.payload = payload

    def export_to_pmdb(self, location):
        path = location / f"{self.name}.pmdb"
        path.write_bytes(self.payload)
        return path

    def export_to_scdocx(self, location):
        path = location / f"{self.name}.scdocx"
        path.write_bytes(self.payload + b"-scdocx")
        return path


class _Modeler:
    def __init__(self, events, name, payload=b"pmdb-body"):
        self.events = events
        self.name = name
        self.payload = payload
        self.opened = None

    def open_file(self, path, upload_to_server=False):
        assert upload_to_server is False
        self.opened = path
        self.events.append(("open", path))
        return _Design(self.name, self.payload)

    def close(self):
        self.events.append("close")


def _launch_factory(events, names):
    pending = list(names)

    def launch():
        events.append("launch")
        name, payload = pending.pop(0)
        return _Modeler(events, name, payload)

    return launch


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_one_session_per_geometry_records_hashes_and_continues(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "pmdb"
    events = []
    dsco = source / "pillar" / "P_p100_h30" / "P_p100_h30.dsco"
    dsco.parent.mkdir(parents=True)
    dsco.write_bytes(b"pillar-dsco")
    empty = source / "empty" / "REF_empty" / "REF_empty.dsco"
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"empty-dsco")
    launch = _launch_factory(
        events,
        [("renamed-design", b"pillar-pmdb"), ("REF_empty", b"empty-pmdb")],
    )

    records = convert.convert_all(
        ["P_p100_h30", "REF_empty"],
        source_root=source,
        output_root=output,
        launch=launch,
    )

    assert [record["status"] for record in records] == ["converted", "converted"]
    assert events[0] == "launch"
    assert events[1][0] == "open"
    assert events[2] == "close"
    assert events[3] == "launch"
    meta = json.loads(
        (output / "pillar" / "P_p100_h30" / "P_p100_h30_meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["source_sha256"] == _sha(b"pillar-dsco")
    assert meta["pmdb_sha256"] == _sha(b"pillar-pmdb")
    assert meta["scdocx_sha256"] == _sha(b"pillar-pmdb-scdocx")
    assert meta["design_name"] == "renamed-design"
    assert (output / "pillar" / "P_p100_h30" / "P_p100_h30.pmdb").read_bytes() == (
        b"pillar-pmdb"
    )
    assert not (source / "pillar" / "P_p100_h30" / "P_p100_h30.pmdb").exists()


def test_a_failed_geometry_is_recorded_and_the_next_runs(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "pmdb"
    events = []
    for geo_id, family in (("D2450_a45", "diamond"), ("D0817_a30", "diamond")):
        path = source / family / geo_id / f"{geo_id}.dsco"
        path.parent.mkdir(parents=True)
        path.write_bytes(geo_id.encode("ascii"))

    def launch():
        events.append("launch")
        if events.count("launch") == 1:
            raise RuntimeError("AttachAssembly failed")
        return _Modeler(events, "D0817_a30", b"diamond-pmdb")

    records = convert.convert_all(
        ["D2450_a45", "D0817_a30"],
        source_root=source,
        output_root=output,
        launch=launch,
    )

    assert records[0]["status"] == "failed"
    assert "AttachAssembly" in records[0]["error"]
    assert records[1]["status"] == "converted"
    assert events[0] == "launch"
    assert "close" in events
    error = json.loads(
        (output / "diamond" / "D2450_a45" / "D2450_a45_convert_error.json").read_text(
            encoding="utf-8"
        )
    )
    assert error["status"] == "failed"


def test_output_root_under_campaign_data_is_refused(tmp_path):
    with pytest.raises(ValueError, match="C:/ro_data"):
        convert.resolve_output_root("C:/ro_data/geometries_pmdb")
    with pytest.raises(ValueError, match="C:/ro_data"):
        convert.resolve_output_root("/mnt/c/ro_data/geometries")
    from pathlib import Path

    assert convert.resolve_output_root("C:/ro_data_mfbo/geometries_pmdb") == Path(
        "C:/ro_data_mfbo/geometries_pmdb"
    )
    with pytest.raises(ValueError, match="C:/ro_data"):
        convert.main(
            [
                "--source-root",
                str(tmp_path / "source"),
                "--output-root",
                "C:/ro_data/geometries",
                "--only",
                "REF_empty",
            ],
            launch=lambda: pytest.fail("Discovery must not launch"),
        )


def test_emit_queue_lists_every_campaign_geometry(tmp_path):
    path = tmp_path / "convert_queue.json"
    code = convert.main(["--emit-queue", str(path)])
    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0
    assert len(jobs) == len(CAMPAIGN_GEO_ID_ORDER) == 31
    assert jobs[0]["id"] == f"convert-{CAMPAIGN_GEO_ID_ORDER[0]}"
    assert jobs[-1]["argv"][-1] == CAMPAIGN_GEO_ID_ORDER[-1]
    assert "convert_campaign_dsco_to_pmdb.py" in jobs[0]["argv"][1]
    assert convert.production_meshing_overrides() == {
        "geometry_suffix": ".pmdb",
        "geometry_root": "C:/ro_data_mfbo/geometries_pmdb",
    }
