"""Compare generated Diamond CAD with the nine manual Discovery files.

Import only. Each case probes the manual ``.dsco``, runs
``generate_diamond_cad``, then probes the ``.pmdb`` with ``--compare-to``.
Body volume is compared only when both sides recorded a number. An unread
volume stays unread and is not treated as zero. A failed case is recorded
and the loop continues.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import subprocess
import sys
import traceback
from pathlib import Path

from ro.diamond_cad import BOUNDARY_LABELS, generate_diamond_cad
from ro.paths import project_root

PROBE_PATH = Path(__file__).resolve().parents[1] / "_probe_reference_geometry.py"
REFERENCE_JSON_NAME = "reference_geometry.json"
META_JSON_SUFFIX = "_meta.json"
AREA_RTOL = 1e-3
VOLUME_RTOL = 1e-3
_STEPS = ("reference", "generate", "compare")


def production_cases():
    """The nine manual Diamond ``.dsco`` paths, in campaign order."""
    probe = _load_probe()
    return probe.production_diamond_cases()


def resolve_out_root(value):
    """Return an absolute output root that is not C:/ro_data or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("out root is required.")
    text = value.strip().replace("\\", "/")
    folded = text.casefold().rstrip("/")
    if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"out root must be absolute, got {value!r}.")
        path = path.resolve()
    resolved = path.as_posix().casefold().rstrip("/")
    if resolved == "c:/ro_data" or resolved.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if resolved == "/mnt/c/ro_data" or resolved.startswith("/mnt/c/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    return path


def _is_windows_absolute(text):
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def case_directories(out_root, geo_id):
    return {
        "ref": out_root / "ref" / geo_id,
        "gen": out_root / "gen" / geo_id,
        "cmp": out_root / "cmp" / geo_id,
    }


def refuse_existing_case_dirs(out_root, geo_ids):
    """Refuse when any per-case ref, gen, or cmp directory already exists."""
    existing = []
    for geo_id in geo_ids:
        for path in case_directories(out_root, geo_id).values():
            if path.exists():
                existing.append(str(path))
    if existing:
        raise FileExistsError(
            "Refusing to reuse existing case subfolders: " + ", ".join(existing)
        )


def summed_body_volume_mm3(bodies):
    """Sum recorded region volumes. Return None when any body is unread."""
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
    """Compare body volumes when both sides have numbers.

    A missing or failed read is ``unread``. It is not replaced with zero.
    """
    current = summed_body_volume_mm3(current_bodies)
    reference = summed_body_volume_mm3(reference_bodies)
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


def aggregate_case(*, geo_id, steps, comparison, volume, face_counts):
    """One summary row. ``comparison`` is the probe comparison object or None."""
    label_set_match = None
    max_bbox_abs_diff_mm = None
    zone_count_match = None
    area_max_rel_diff = None
    if isinstance(comparison, dict):
        only_current = comparison.get("labels_only_in_current") or []
        only_reference = comparison.get("labels_only_in_reference") or []
        label_set_match = not only_current and not only_reference
        diffs = []
        zone_flags = []
        labels = comparison.get("labels")
        if isinstance(labels, list):
            for row in labels:
                if not isinstance(row, dict):
                    continue
                if isinstance(row.get("max_abs_diff_mm"), (int, float)) and not isinstance(
                    row.get("max_abs_diff_mm"), bool
                ):
                    diffs.append(float(row["max_abs_diff_mm"]))
                if "zone_count" in row:
                    zone_flags.append(row["zone_count"] == "PASS")
        overall = comparison.get("overall")
        if isinstance(overall, dict) and isinstance(
            overall.get("max_abs_diff_mm"), (int, float)
        ) and not isinstance(overall.get("max_abs_diff_mm"), bool):
            diffs.append(float(overall["max_abs_diff_mm"]))
        if diffs:
            max_bbox_abs_diff_mm = max(diffs)
        if zone_flags:
            zone_count_match = all(zone_flags)
        if "area_max_rel_diff" in comparison:
            area_max_rel_diff = comparison["area_max_rel_diff"]
    volume_status = None
    volume_rel_diff = None
    if isinstance(volume, dict):
        volume_status = volume.get("status")
        volume_rel_diff = volume.get("rel_diff")
    return {
        "geo_id": geo_id,
        "label_set_match": label_set_match,
        "max_bbox_abs_diff_mm": max_bbox_abs_diff_mm,
        "zone_count_match": zone_count_match,
        "area_max_rel_diff": area_max_rel_diff,
        "volume_status": volume_status,
        "volume_rel_diff": volume_rel_diff,
        "generated_face_counts": face_counts,
        "boundary_labels": list(BOUNDARY_LABELS),
        "steps": steps,
    }


def case_failed(summary):
    for name in _STEPS:
        step = summary["steps"][name]
        if step["return_code"] != 0:
            return True
    if summary.get("volume_status") == "compared":
        rel_diff = summary.get("volume_rel_diff")
        if rel_diff is None or rel_diff > VOLUME_RTOL:
            return True
    return False


def _write_log(log_path, text):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(text, encoding="utf-8")
    return log_path


def run_subprocess_step(cmd, log_path):
    """Run a command and store its full stdout and stderr. Do not raise on exit."""
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        traced = traceback.format_exc()
        _write_log(log_path, traced)
        print(traced, file=sys.stderr)
        return 1
    _write_log(
        log_path,
        f"command: {subprocess.list2cmdline(cmd)}\n"
        f"returncode: {completed.returncode}\n"
        "----- stdout -----\n"
        f"{completed.stdout}"
        "----- stderr -----\n"
        f"{completed.stderr}",
    )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
    return int(completed.returncode)


def run_generate_step(*, geo_id, out_dir, log_path):
    """Call the generator in-process and store its output. Record failures."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            written = generate_diamond_cad(geo_id=geo_id, out_dir=out_dir)
    except Exception:
        traced = traceback.format_exc()
        _write_log(log_path, buffer.getvalue() + traced)
        print(traced, file=sys.stderr)
        return 1
    _write_log(
        log_path,
        buffer.getvalue() + json.dumps(written, indent=2) + "\n",
    )
    return 0


def _read_json_object(path):
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON object required: {path}")
    return payload


def select_cases(cases, only):
    """Restrict cases to ``--only`` ids. Unknown ids raise."""
    if only is None:
        return list(cases)
    known = {case["geo_id"]: case for case in cases}
    unknown = [geo_id for geo_id in only if geo_id not in known]
    if unknown:
        raise ValueError(
            "Unknown geo_id(s) for --only: "
            + ", ".join(repr(geo_id) for geo_id in unknown)
            + ". Expected one of "
            + ", ".join(repr(geo_id) for geo_id in known)
            + "."
        )
    return [known[geo_id] for geo_id in only]


def _probe_cmd(cad_path, work_dir, compare_to=None):
    cmd = [
        sys.executable,
        str(PROBE_PATH),
        "--family",
        "diamond",
        "--cad-path",
        str(cad_path),
        "--work-dir",
        str(work_dir),
    ]
    if compare_to is not None:
        cmd.extend(
            [
                "--compare-to",
                str(compare_to),
                "--area-rtol",
                str(AREA_RTOL),
            ]
        )
    return cmd


def run_case(case, *, out_root):
    geo_id = case["geo_id"]
    dirs = case_directories(out_root, geo_id)
    log_dir = out_root / "logs" / geo_id
    steps = {}

    ref_log = log_dir / "reference.log"
    ref_code = run_subprocess_step(
        _probe_cmd(case["cad_path"], dirs["ref"]),
        ref_log,
    )
    steps["reference"] = {"return_code": ref_code, "log": str(ref_log)}

    gen_log = log_dir / "generate.log"
    gen_code = run_generate_step(geo_id=geo_id, out_dir=dirs["gen"], log_path=gen_log)
    steps["generate"] = {"return_code": gen_code, "log": str(gen_log)}

    cmp_log = log_dir / "compare.log"
    ref_json = dirs["ref"] / REFERENCE_JSON_NAME
    pmdb = dirs["gen"] / f"{geo_id}.pmdb"
    if ref_code != 0 or gen_code != 0 or not ref_json.is_file() or not pmdb.is_file():
        reason = (
            f"compare skipped: reference return {ref_code}, generate return {gen_code}, "
            f"reference json exists={ref_json.is_file()}, pmdb exists={pmdb.is_file()}.\n"
        )
        _write_log(cmp_log, reason)
        print(reason, file=sys.stderr)
        cmp_code = 1
    else:
        cmp_code = run_subprocess_step(
            _probe_cmd(pmdb, dirs["cmp"], compare_to=ref_json),
            cmp_log,
        )
    steps["compare"] = {"return_code": cmp_code, "log": str(cmp_log)}

    comparison = None
    face_counts = None
    volume = None
    cmp_json = dirs["cmp"] / REFERENCE_JSON_NAME
    meta_path = dirs["gen"] / f"{geo_id}{META_JSON_SUFFIX}"
    try:
        cmp_payload = _read_json_object(cmp_json)
        ref_payload = _read_json_object(ref_json)
        if isinstance(cmp_payload, dict):
            comparison = cmp_payload.get("comparison")
        if isinstance(cmp_payload, dict) and isinstance(ref_payload, dict):
            volume = compare_body_volumes(
                cmp_payload.get("bodies"),
                ref_payload.get("bodies"),
            )
        meta = _read_json_object(meta_path)
        if isinstance(meta, dict):
            face_counts = meta.get("face_counts")
    except Exception:
        traced = traceback.format_exc()
        print(traced, file=sys.stderr)
        note = cmp_log.read_text(encoding="utf-8") if cmp_log.is_file() else ""
        _write_log(cmp_log, note + traced)
        steps["compare"] = {"return_code": 1, "log": str(cmp_log)}

    return aggregate_case(
        geo_id=geo_id,
        steps=steps,
        comparison=comparison,
        volume=volume,
        face_counts=face_counts,
    )


def write_summary(out_root, payload):
    path = out_root / "diamond_geometry_parity_summary.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def print_summary(cases):
    print(
        f"{'geo_id':<16} {'labels':>8} {'max_|bbox|_mm':>14} "
        f"{'zones':>8} {'area_rel':>12} {'volume':>10} "
        f"{'ref':>4} {'gen':>4} {'cmp':>4}"
    )
    for case in cases:
        area = case["area_max_rel_diff"]
        area_text = "" if area is None else f"{area:.6g}"
        bbox = case["max_bbox_abs_diff_mm"]
        bbox_text = "" if bbox is None else f"{bbox:.6g}"
        volume = case["volume_status"] or ""
        if volume == "compared" and isinstance(case["volume_rel_diff"], float):
            volume = f"{case['volume_rel_diff']:.3g}"
        print(
            f"{case['geo_id']:<16} {_flag(case['label_set_match']):>8} "
            f"{bbox_text:>14} {_flag(case['zone_count_match']):>8} "
            f"{area_text:>12} {volume:>10} "
            f"{case['steps']['reference']['return_code']:>4} "
            f"{case['steps']['generate']['return_code']:>4} "
            f"{case['steps']['compare']['return_code']:>4}"
        )


def _flag(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return ""


def _load_probe():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "diamond_parity_probe",
        PROBE_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load probe: {PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_parser():
    parser = argparse.ArgumentParser(
        description="Import-only parity of generated Diamond CAD against the nine manual files.",
    )
    parser.add_argument(
        "--out-root",
        required=True,
        help="Absolute output root. Not C:/ro_data. Case subfolders must not exist.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="GEO_ID",
        help="Run only these Diamond geo_ids.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_root = resolve_out_root(args.out_root)
    if out_root.exists() and not out_root.is_dir():
        raise ValueError(f"out root is not a directory: {out_root}")
    cases = production_cases()
    if len(cases) != 9:
        raise RuntimeError(f"Expected 9 Diamond geometries, found {len(cases)}.")
    cases = select_cases(cases, args.only)
    refuse_existing_case_dirs(out_root, [case["geo_id"] for case in cases])
    out_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for case in cases:
        print(f"CASE {case['geo_id']}")
        rows.append(run_case(case, out_root=out_root))
    payload = {
        "out_root": str(out_root),
        "area_rtol": AREA_RTOL,
        "volume_rtol": VOLUME_RTOL,
        "project_root": str(project_root()),
        "cases": rows,
    }
    print_summary(rows)
    write_summary(out_root, payload)
    if any(case_failed(case) for case in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
