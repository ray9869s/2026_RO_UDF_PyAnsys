"""Campaign .dsco to .pmdb conversion. Does not launch Discovery."""

from __future__ import annotations

import hashlib
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER

processes = load_module(
    "discovery_processes_under_test",
    SCRIPTS_DIR / "mfbo" / "discovery_processes.py",
)

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

    remaining = {"failures": 2}

    def launch():
        events.append("launch")
        if remaining["failures"]:
            remaining["failures"] -= 1
            raise RuntimeError("AttachAssembly failed")
        return _Modeler(events, "D0817_a30", b"diamond-pmdb")

    records = convert.convert_all(
        ["D2450_a45", "D0817_a30"],
        source_root=source,
        output_root=output,
        launch=launch,
    )

    assert records[0]["status"] == "failed"
    assert records[0]["attempts"] == 2
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


def test_backend_timeout_is_longer_than_the_library_default():
    arguments = convert.discovery_launch_arguments("V_251")
    assert arguments["timeout"] == 300
    assert arguments["timeout"] > 150
    assert arguments["hidden"] is True
    assert arguments["version"] == 251


def test_timeout_retries_once_and_kills_only_the_process_this_job_started(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "pmdb"
    path = source / "empty" / "REF_empty" / "REF_empty.dsco"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"empty-dsco")
    events = []
    calls = {"n": 0}
    killed = []

    def launch():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("Timeout while waiting for backend to be ready")
        return _Modeler(events, "REF_empty", b"empty-pmdb")

    listings = {"n": 0}

    def lister():
        listings["n"] += 1
        if listings["n"] == 2:
            return [
                {"pid": 7, "line": "Discovery.exe,7"},
                {"pid": 99, "line": "Discovery.exe,99"},
            ]
        return [{"pid": 7, "line": "Discovery.exe,7"}]

    records = convert.convert_all(
        ["REF_empty"],
        source_root=source,
        output_root=output,
        launch=launch,
        process_lister=lister,
        process_killer=killed.append,
    )
    assert records[0]["status"] == "converted"
    assert records[0]["attempts"] == 2
    assert records[0]["backend_start_timeout_s"] == 300
    assert killed == [99]
    assert records[0]["terminated_pids"] == [99]
    assert records[0]["processes_before"] == ["Discovery.exe,7"]
    assert 7 not in records[0]["terminated_pids"]


def test_process_listings_keep_discovery_and_spaceclaim_only():
    tasklist = (
        '"fluent.exe","10","Console","1","1 K"\n'
        '"Discovery.exe","99","Console","1","1 K"\n'
        '"SpaceClaim.exe","7","Console","1","1 K"\n'
    )
    parsed = processes.processes_from_tasklist(tasklist)
    assert [item["pid"] for item in parsed] == [99, 7]
    ps_text = "10 fluent.exe meshing\n55 Discovery.exe -hidden\n4 SpaceClaim.exe\n"
    assert [item["pid"] for item in processes.processes_from_ps(ps_text)] == [55, 4]
    killed = []
    terminated = processes.terminate_started(
        [{"pid": 7, "line": "SpaceClaim.exe,7"}],
        [
            {"pid": 7, "line": "SpaceClaim.exe,7"},
            {"pid": 99, "line": "Discovery.exe,99"},
        ],
        killer=killed.append,
    )
    assert terminated == [99]
    assert killed == [99]
