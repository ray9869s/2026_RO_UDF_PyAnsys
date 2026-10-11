"""Compare each converted campaign .pmdb with its .dsco.

Uses ``scripts/_probe_reference_geometry.py --family campaign``. Labels,
per-label face-zone counts, areas, and bounding boxes use the pillar-parity
tolerances (bbox 1e-3 mm, area relative 1e-3). Per-label ``face_count``
values must match exactly. Body volume uses the same relative tolerance
the diamond parity script uses (1e-3). An unread volume on both probes,
with the other checks passing, is ``passed_without_volume``. A volume
read on one side only, or read on both and outside tolerance, fails. A failed
geometry is recorded and the loop continues. A probe that writes no dump
is closed, Discovery and SpaceClaim processes that probe started are
terminated, and that probe is tried once more. The parity record names
the probe and the CAD-import text.

Importing this module does not launch Fluent. ``--emit-queue`` writes a
job-queue JSON file and does not probe.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from discovery_processes import (  # noqa: E402
    list_discovery_processes,
    process_note,
    terminate_pid,
    terminate_started,
)

PROBE_PATH = Path(__file__).resolve().parents[1] / "_probe_reference_geometry.py"
DEFAULT_SOURCE_ROOT = "C:/ro_data/geometries"
DEFAULT_PMDB_ROOT = "C:/ro_data_mfbo/geometries_pmdb"
DEFAULT_WORK_ROOT = "C:/ro_data_mfbo/geometries_pmdb_parity"
SUMMARY_NAME = "reference_geometry.json"
TRANSCRIPT_NAME = "probe_reference_geometry_transcript.txt"
# scripts/_probe_reference_geometry.py DEFAULT_BBOX_TOL_MM
BBOX_TOL_MM = 1e-3
# scripts/mfbo/run_geometry_parity_all.py AREA_RTOL
AREA_RTOL = 1e-3
# scripts/mfbo/run_diamond_geometry_parity.py VOLUME_RTOL
VOLUME_RTOL = 1e-3


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _folded(text: str) -> str:
    return text.replace("\\", "/").casefold().rstrip("/")


def _is_windows_absolute(text: str) -> bool:
    folded = text.replace("\\", "/")
    return len(folded) >= 3 and folded[1] == ":" and folded[2] == "/"


def _is_absolute(text: str) -> bool:
    return _is_windows_absolute(text) or Path(text).is_absolute()


def _under_manual_cad(path) -> bool:
    texts = [str(path), Path(path).as_posix()]
    candidate = Path(path)
    if candidate.is_absolute() and not _is_windows_absolute(str(path)):
        try:
            texts.append(candidate.resolve().as_posix())
        except OSError:
            pass
    for text in texts:
        folded = _folded(text)
        if folded == "c:/ro_data/geometries" or folded.startswith("c:/ro_data/geometries/"):
            return True
        if folded == "/mnt/c/ro_data/geometries" or folded.startswith(
            "/mnt/c/ro_data/geometries/"
        ):
            return True
    return False


def resolve_root(value: str, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required.")
    text = value.strip()
    if not _is_absolute(text):
        raise ValueError(f"{name} must be absolute, got {value!r}.")
    return Path(text)


def resolve_work_root(value: str) -> Path:
    path = resolve_root(value, "work root")
    if _under_manual_cad(value):
        raise ValueError(
            f"work root {value} is under C:/ro_data/geometries. "
            "That tree is reserved for the manual CAD."
        )
    return path


def select_geo_ids(only: str | None) -> list[str]:
    if only is None:
        return list(CAMPAIGN_GEO_ID_ORDER)
    if only not in CAMPAIGN_GEO_ID_ORDER:
        raise ValueError(
            f"Unknown campaign geo_id {only!r}. "
            f"Expected one of {', '.join(CAMPAIGN_GEO_ID_ORDER)}."
        )
    return [only]


def parity_jobs() -> list[dict]:
    root = str(repo_root())
    return [
        {
            "id": f"parity-{geo_id}",
            "argv": [
                "{python}",
                "scripts/mfbo/parity_campaign_pmdb.py",
                "--only",
                geo_id,
            ],
            "cwd": root,
        }
        for geo_id in CAMPAIGN_GEO_ID_ORDER
    ]


def write_queue(path, jobs) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    return destination


def probe_command(cad_path, work_dir, compare_to=None) -> list[str]:
    command = [
        "{python}",
        str(PROBE_PATH),
        "--family",
        "campaign",
        "--cad-path",
        str(cad_path),
        "--work-dir",
        str(work_dir),
        "--bbox-tol-mm",
        str(BBOX_TOL_MM),
        "--area-rtol",
        str(AREA_RTOL),
    ]
    if compare_to is not None:
        command.extend(["--compare-to", str(compare_to)])
    return command


def _probe_module():
    spec = importlib.util.spec_from_file_location(
        "campaign_pmdb_reference_probe",
        PROBE_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load the reference probe: {PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summed_body_volume_mm3(bodies):
    if not isinstance(bodies, list) or not bodies:
        return None
    total = 0.0
    for body in bodies:
        if not isinstance(body, dict) or body.get("error"):
            return None
        volumes = body.get("volumes")
        if not isinstance(volumes, list) or not volumes:
            return None
        for volume in volumes:
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                return None
            if not math.isfinite(float(volume)):
                return None
            total += float(volume)
    return total


def _volume_read_errors(bodies):
    if not isinstance(bodies, list):
        return []
    errors = []
    for body in bodies:
        if not isinstance(body, dict) or not body.get("error"):
            continue
        name = body.get("name")
        text = str(body["error"])
        errors.append(f"{name}: {text}" if name else text)
    return errors


def compare_body_volumes(current_bodies, reference_bodies):
    """Same unread-versus-compared split as the diamond parity script."""
    current = _summed_body_volume_mm3(current_bodies)
    reference = _summed_body_volume_mm3(reference_bodies)
    if current is None or reference is None:
        errors = []
        if current is None:
            errors.extend(_volume_read_errors(current_bodies))
        if reference is None:
            errors.extend(_volume_read_errors(reference_bodies))
        unread = {
            "status": "unread",
            "current_mm3": current,
            "reference_mm3": reference,
            "rel_diff": None,
        }
        if errors:
            unread["read_error"] = "; ".join(errors)
        return unread
    if reference == 0.0:
        rel_diff = 0.0 if current == 0.0 else None
    else:
        rel_diff = abs(current - reference) / abs(reference)
    return {
        "status": "compared",
        "current_mm3": current,
        "reference_mm3": reference,
        "rel_diff": rel_diff,
    }


def _face_count(record, label):
    if not isinstance(record, dict) or "face_count" not in record:
        raise ValueError(f"Label {label!r} has no face_count.")
    value = record["face_count"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Label {label!r} face_count must be an int, got {value!r}.")
    return value


def face_count_rows(reference, current) -> list[dict]:
    reference_labels = reference.get("labels")
    current_labels = current.get("labels")
    if not isinstance(reference_labels, dict) or not isinstance(current_labels, dict):
        raise TypeError("Probe dumps must contain a labels object.")
    rows = []
    for label in sorted(set(reference_labels) | set(current_labels)):
        if label not in reference_labels or label not in current_labels:
            rows.append({"label": label, "status": "FAIL"})
            continue
        reference_count = _face_count(reference_labels[label], label)
        current_count = _face_count(current_labels[label], label)
        rows.append(
            {
                "label": label,
                "reference": reference_count,
                "current": current_count,
                "status": "PASS" if reference_count == current_count else "FAIL",
            }
        )
    return rows


def _volume_within_tolerance(volume) -> bool:
    rel_diff = volume.get("rel_diff")
    return (
        volume.get("status") == "compared"
        and isinstance(rel_diff, (int, float))
        and not isinstance(rel_diff, bool)
        and rel_diff <= VOLUME_RTOL
    )


def status_from_parity_record(record) -> str:
    """Status from labels, face counts, areas, boxes, and volume already recorded.

    Both sides unread, with the other checks passing, is
    ``passed_without_volume``. A volume present on one side only, or
    present on both and outside tolerance, is ``failed``.
    """
    if record.get("comparison_passed") is not True:
        return "failed"
    faces = record.get("face_counts")
    if not isinstance(faces, list) or not faces:
        return "failed"
    if any(not isinstance(row, dict) or row.get("status") != "PASS" for row in faces):
        return "failed"
    volume = record.get("volume")
    if not isinstance(volume, dict):
        return "failed"
    if volume.get("status") == "unread":
        if volume.get("current_mm3") is None and volume.get("reference_mm3") is None:
            return "passed_without_volume"
        return "failed"
    if _volume_within_tolerance(volume):
        return "passed"
    return "failed"


def judge_dumps(reference, current, *, probe=None) -> dict:
    """Label, area, box, face-count, and volume parity. Does not launch Fluent."""
    compare = probe.compare_reference if probe is not None else _probe_module().compare_reference
    comparison = compare(current, reference, BBOX_TOL_MM, area_rtol=AREA_RTOL)
    faces = face_count_rows(reference, current)
    volume = compare_body_volumes(current.get("bodies"), reference.get("bodies"))
    judgement = {
        "comparison_passed": bool(comparison["passed"]),
        "comparison": comparison,
        "face_counts": faces,
        "volume": volume,
    }
    judgement["status"] = status_from_parity_record(judgement)
    judgement["passed"] = judgement["status"] == "passed"
    return judgement


def _read_dump(path: Path):
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Probe dump must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _utc_stamp(now) -> str:
    moment = datetime.now(timezone.utc) if now is None else now
    if moment.tzinfo is None:
        raise ValueError("archive timestamp must be timezone-aware.")
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def archive_failed_record(path: Path, prefix: str, now=None) -> Path:
    """Move ``path`` to ``<prefix>_<UTC>.json`` beside it."""
    destination = path.with_name(f"{prefix}_{_utc_stamp(now)}.json")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}.")
    path.replace(destination)
    return destination


_ACCEPTED_STATUSES = ("passed", "passed_without_volume")


def write_parity_record(path: Path, record: dict, now=None) -> Path | None:
    """Write ``parity.json``. A failed record is archived. An accepted record is kept."""
    archived = None
    if path.is_file():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            raise ValueError(f"{path} is not a JSON object.")
        status = previous.get("status")
        if status in _ACCEPTED_STATUSES:
            raise FileExistsError(
                f"Refusing to overwrite {status} parity record: {path}"
            )
        if status != "failed":
            raise ValueError(
                f"{path} status {status!r} is not an accepted or failed parity status."
            )
        archived = archive_failed_record(path, "parity_failed", now)
    _write_json(path, record)
    return archived


def subprocess_runner(command: list[str]) -> dict:
    argv = [sys.executable if item == "{python}" else item for item in command]
    completed = subprocess.run(
        argv,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(
            completed.stderr,
            file=sys.stderr,
            end="" if completed.stderr.endswith("\n") else "\n",
        )
    return {"returncode": int(completed.returncode), "output": output}


def _probe_result(value) -> dict:
    if isinstance(value, int):
        return {"returncode": value, "output": ""}
    if isinstance(value, dict) and "returncode" in value:
        return {
            "returncode": int(value["returncode"]),
            "output": str(value.get("output") or ""),
        }
    raise TypeError(
        "Probe runner must return an int or a returncode mapping, "
        f"got {type(value).__name__}."
    )


def _tail(text: str, limit: int = 2000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def probe_failure_text(label: str, result: dict, work_dir: Path) -> str:
    """Which probe failed, and the CAD-import or launch text it printed."""
    parts = [
        f"{label} probe failed with return code {result['returncode']}."
    ]
    output = _tail(result.get("output") or "")
    if output:
        parts.append(output)
    transcript = work_dir / TRANSCRIPT_NAME
    if transcript.is_file():
        text = _tail(transcript.read_text(encoding="utf-8", errors="replace"))
        if text:
            parts.append(text)
    dump = work_dir / SUMMARY_NAME
    if not dump.is_file():
        parts.append(f"No {SUMMARY_NAME} was written in {work_dir}.")
    return "\n".join(parts)


def _run_probe(label, command, work_dir, runner, lister, killer) -> tuple[dict | None, dict, str | None]:
    """Run one probe. On failure, stop processes this attempt started and retry once."""
    failure = None
    note = None
    started_before = None
    terminated_all = []
    for attempt in (1, 2):
        before = lister()
        if started_before is None:
            started_before = before
        result = _probe_result(runner(command))
        after = lister()
        terminated_all.extend(terminate_started(before, after, killer))
        note = process_note(started_before, after, terminated_all, attempt)
        note["returncode"] = result["returncode"]
        dump = _read_dump(work_dir / SUMMARY_NAME)
        if dump is not None:
            return dump, note, None
        failure = probe_failure_text(label, result, work_dir)
        print(failure, file=sys.stderr)
    return None, note, failure


def run_one(
    geo_id,
    *,
    source_root,
    pmdb_root,
    work_root,
    runner,
    probe=None,
    process_lister=None,
    process_killer=None,
    now=None,
) -> dict:
    family = family_for_geo_id(geo_id)
    dsco = source_root / family / geo_id / f"{geo_id}.dsco"
    pmdb = pmdb_root / family / geo_id / f"{geo_id}.pmdb"
    dsco_work = work_root / geo_id / "dsco"
    pmdb_work = work_root / geo_id / "pmdb"
    record = {
        "geo_id": geo_id,
        "family": family,
        "source_dsco": str(dsco),
        "pmdb": str(pmdb),
    }
    parity_path = work_root / geo_id / "parity.json"
    if parity_path.is_file():
        previous = json.loads(parity_path.read_text(encoding="utf-8"))
        if isinstance(previous, dict) and previous.get("status") in _ACCEPTED_STATUSES:
            raise FileExistsError(
                f"Refusing to overwrite {previous.get('status')} parity record: {parity_path}"
            )
    if not dsco.is_file() or not pmdb.is_file():
        record["status"] = "failed"
        record["error"] = (
            f"dsco exists={dsco.is_file()} pmdb exists={pmdb.is_file()}."
        )
        write_parity_record(work_root / geo_id / "parity.json", record, now)
        return record
    lister = list_discovery_processes if process_lister is None else process_lister
    killer = terminate_pid if process_killer is None else process_killer
    reference, dsco_note, dsco_error = _run_probe(
        "dsco",
        probe_command(dsco, dsco_work),
        dsco_work,
        runner,
        lister,
        killer,
    )
    current, pmdb_note, pmdb_error = _run_probe(
        "pmdb",
        probe_command(pmdb, pmdb_work, compare_to=dsco_work / SUMMARY_NAME),
        pmdb_work,
        runner,
        lister,
        killer,
    )
    record["probes"] = {"dsco": dsco_note, "pmdb": pmdb_note}
    record["probe_return_codes"] = {
        "dsco": dsco_note["returncode"],
        "pmdb": pmdb_note["returncode"],
    }
    failures = [text for text in (dsco_error, pmdb_error) if text]
    if failures:
        record["status"] = "failed"
        record["error"] = "\n".join(failures)
        write_parity_record(work_root / geo_id / "parity.json", record, now)
        return record
    judgement = judge_dumps(reference, current, probe=probe)
    record["status"] = judgement["status"]
    record["passed"] = judgement["passed"]
    record["volume"] = judgement["volume"]
    record["face_counts"] = judgement["face_counts"]
    record["comparison_passed"] = judgement["comparison_passed"]
    write_parity_record(work_root / geo_id / "parity.json", record, now)
    return record


def run_all(
    geo_ids,
    *,
    source_root,
    pmdb_root,
    work_root,
    runner,
    probe=None,
    process_lister=None,
    process_killer=None,
    now=None,
) -> list[dict]:
    return [
        run_one(
            geo_id,
            source_root=source_root,
            pmdb_root=pmdb_root,
            work_root=work_root,
            runner=runner,
            probe=probe,
            process_lister=process_lister,
            process_killer=process_killer,
            now=now,
        )
        for geo_id in geo_ids
    ]


def _dump_volume_errors(geo_dir: Path) -> list[str]:
    errors = []
    for side in ("dsco", "pmdb"):
        path = geo_dir / side / SUMMARY_NAME
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        for text in _volume_read_errors(payload.get("bodies")):
            errors.append(f"{side}: {text}")
    return errors


def reevaluate_one(work_root, geo_id, now=None) -> dict:
    """Recompute one parity status from the record on disk. Does not probe."""
    path = Path(work_root) / geo_id / "parity.json"
    if not path.is_file():
        raise FileNotFoundError(f"Parity record not found: {path}")
    previous = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(previous, dict):
        raise ValueError(f"{path} is not a JSON object.")
    before = previous.get("status")
    archive_failed_record(path, "parity_reevaluated", now)
    record = dict(previous)
    record["status"] = status_from_parity_record(record)
    record["passed"] = record["status"] == "passed"
    if record["status"] == "passed_without_volume":
        volume = dict(record.get("volume") or {})
        if not volume.get("read_error"):
            errors = _dump_volume_errors(path.parent)
            if errors:
                volume["read_error"] = "; ".join(errors)
        record["volume"] = volume
    _write_json(path, record)
    print(f"{geo_id} {before} -> {record['status']}")
    if record["status"] == "passed_without_volume":
        print(f"  volume unread: {record['volume'].get('read_error')}")
    return record


def reevaluate_all(geo_ids, work_root, now=None) -> list[dict]:
    return [reevaluate_one(work_root, geo_id, now) for geo_id in geo_ids]


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare converted campaign .pmdb files with their .dsco files. "
            "--emit-queue writes a job queue and does not launch Fluent."
        ),
    )
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--pmdb-root", default=DEFAULT_PMDB_ROOT)
    parser.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    parser.add_argument("--only", default=None, help="One campaign geo_id.")
    parser.add_argument(
        "--emit-queue",
        type=Path,
        help="Write the parity job queue and do not probe.",
    )
    parser.add_argument(
        "--reevaluate",
        action="store_true",
        help=(
            "Recompute status from existing parity.json comparison data. "
            "Does not launch Discovery or Fluent."
        ),
    )
    return parser


def main(argv=None, runner=None, probe=None, now=None) -> int:
    args = build_parser().parse_args(argv)
    if args.emit_queue is not None and args.reevaluate:
        raise ValueError("--reevaluate does not take --emit-queue.")
    if args.emit_queue is not None:
        if args.only is not None:
            raise ValueError("--emit-queue does not take --only.")
        path = write_queue(args.emit_queue, parity_jobs())
        print(path)
        return 0
    if args.reevaluate:
        if runner is not None or probe is not None:
            raise ValueError("--reevaluate does not launch a probe.")
        work_root = resolve_work_root(args.work_root)
        records = reevaluate_all(select_geo_ids(args.only), work_root, now)
        failed = [
            record["geo_id"]
            for record in records
            if record["status"] not in _ACCEPTED_STATUSES
        ]
        if failed:
            print("Failed: " + ", ".join(failed), file=sys.stderr)
            return 2
        return 0
    source_root = resolve_root(args.source_root, "source root")
    pmdb_root = resolve_root(args.pmdb_root, "pmdb root")
    work_root = resolve_work_root(args.work_root)
    records = run_all(
        select_geo_ids(args.only),
        source_root=source_root,
        pmdb_root=pmdb_root,
        work_root=work_root,
        runner=subprocess_runner if runner is None else runner,
        probe=probe,
    )
    if args.only is None:
        _write_json(work_root / "parity_summary.json", {"records": records})
    failed = [
        record["geo_id"]
        for record in records
        if record["status"] not in _ACCEPTED_STATUSES
    ]
    if failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"PARITY FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
