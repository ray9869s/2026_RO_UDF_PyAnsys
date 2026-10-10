"""Compare each converted campaign .pmdb with its .dsco.

Uses ``scripts/_probe_reference_geometry.py --family campaign``. Labels,
per-label face-zone counts, areas, and bounding boxes use the pillar-parity
tolerances (bbox 1e-3 mm, area relative 1e-3). Per-label ``face_count``
values must match exactly. Body volume uses the same relative tolerance
the diamond parity script uses (1e-3); an unread volume fails. A failed
geometry is recorded and the loop continues.

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
from pathlib import Path

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id

PROBE_PATH = Path(__file__).resolve().parents[1] / "_probe_reference_geometry.py"
DEFAULT_SOURCE_ROOT = "C:/ro_data/geometries"
DEFAULT_PMDB_ROOT = "C:/ro_data_mfbo/geometries_pmdb"
DEFAULT_WORK_ROOT = "C:/ro_data_mfbo/geometries_pmdb_parity"
SUMMARY_NAME = "reference_geometry.json"
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


def compare_body_volumes(current_bodies, reference_bodies):
    """Same unread-versus-compared split as the diamond parity script."""
    current = _summed_body_volume_mm3(current_bodies)
    reference = _summed_body_volume_mm3(reference_bodies)
    if current is None or reference is None:
        return {
            "status": "unread",
            "current_mm3": current,
            "reference_mm3": reference,
            "rel_diff": None,
        }
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


def judge_dumps(reference, current, *, probe=None) -> dict:
    """Label, area, box, face-count, and volume parity. Does not launch Fluent."""
    compare = probe.compare_reference if probe is not None else _probe_module().compare_reference
    comparison = compare(current, reference, BBOX_TOL_MM, area_rtol=AREA_RTOL)
    faces = face_count_rows(reference, current)
    volume = compare_body_volumes(current.get("bodies"), reference.get("bodies"))
    volume_ok = (
        volume["status"] == "compared"
        and isinstance(volume["rel_diff"], (int, float))
        and not isinstance(volume["rel_diff"], bool)
        and volume["rel_diff"] <= VOLUME_RTOL
    )
    return {
        "comparison_passed": bool(comparison["passed"]),
        "comparison": comparison,
        "face_counts": faces,
        "volume": volume,
        "passed": bool(comparison["passed"])
        and all(row["status"] == "PASS" for row in faces)
        and volume_ok,
    }


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


def subprocess_runner(command: list[str]) -> int:
    argv = [sys.executable if item == "{python}" else item for item in command]
    completed = subprocess.run(
        argv,
        check=False,
        shell=False,
    )
    return int(completed.returncode)


def run_one(geo_id, *, source_root, pmdb_root, work_root, runner, probe=None) -> dict:
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
    if not dsco.is_file() or not pmdb.is_file():
        record["status"] = "failed"
        record["error"] = (
            f"dsco exists={dsco.is_file()} pmdb exists={pmdb.is_file()}."
        )
        _write_json(work_root / geo_id / "parity.json", record)
        return record
    dsco_dump_path = dsco_work / SUMMARY_NAME
    pmdb_dump_path = pmdb_work / SUMMARY_NAME
    dsco_code = runner(probe_command(dsco, dsco_work))
    pmdb_code = runner(probe_command(pmdb, pmdb_work, compare_to=dsco_dump_path))
    reference = _read_dump(dsco_dump_path)
    current = _read_dump(pmdb_dump_path)
    record["probe_return_codes"] = {"dsco": dsco_code, "pmdb": pmdb_code}
    if reference is None or current is None:
        record["status"] = "failed"
        record["error"] = (
            f"Probe dump missing: dsco={dsco_dump_path.is_file()} "
            f"pmdb={pmdb_dump_path.is_file()}."
        )
        _write_json(work_root / geo_id / "parity.json", record)
        return record
    judgement = judge_dumps(reference, current, probe=probe)
    record["status"] = "passed" if judgement["passed"] else "failed"
    record["passed"] = judgement["passed"]
    record["volume"] = judgement["volume"]
    record["face_counts"] = judgement["face_counts"]
    record["comparison_passed"] = judgement["comparison_passed"]
    _write_json(work_root / geo_id / "parity.json", record)
    return record


def run_all(geo_ids, *, source_root, pmdb_root, work_root, runner, probe=None) -> list[dict]:
    return [
        run_one(
            geo_id,
            source_root=source_root,
            pmdb_root=pmdb_root,
            work_root=work_root,
            runner=runner,
            probe=probe,
        )
        for geo_id in geo_ids
    ]


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
    return parser


def main(argv=None, runner=None, probe=None) -> int:
    args = build_parser().parse_args(argv)
    if args.emit_queue is not None:
        if args.only is not None:
            raise ValueError("--emit-queue does not take --only.")
        path = write_queue(args.emit_queue, parity_jobs())
        print(path)
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
    failed = [record["geo_id"] for record in records if record["status"] != "passed"]
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
