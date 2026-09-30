"""Compare generated Pillar CAD with the nine manual Discovery files.

Import only. Each case runs the reference-geometry probe, then
``generate_pillar_cad``, then the probe again with ``--compare-to``.
A failed case is recorded and the loop continues.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import math
import subprocess
import sys
import traceback
from pathlib import Path

from ro.paths import project_root
from ro.pillar_cad import generate_pillar_cad

PROBE_PATH = Path(__file__).resolve().parents[1] / "_probe_reference_geometry.py"
REFERENCE_JSON_NAME = "reference_geometry.json"
META_JSON_SUFFIX = "_meta.json"
AREA_RTOL = 1e-3
_STEPS = ("reference", "generate", "compare")


def load_batch_config():
    """Load configs/batch_config.py. Import does not launch Fluent or Discovery."""
    path = project_root() / "configs" / "batch_config.py"
    if not path.is_file():
        raise FileNotFoundError(f"batch_config.py not found: {path}")
    spec = importlib.util.spec_from_file_location(
        "geometry_parity_batch_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load batch config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def design_points(batch):
    """Cross product of ``_PILLAR_D_MM`` and ``_PILLAR_H_MM``."""
    points = []
    for d_mm in batch._PILLAR_D_MM:
        for h_mm in batch._PILLAR_H_MM:
            points.append(
                {
                    "d_p_mm": d_mm,
                    "d_h_mm": h_mm,
                    "geo_id": batch._pillar_geo_id(d_mm, h_mm),
                }
            )
    return points


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


def reference_cad_path(geo_id):
    return Path(f"C:/ro_data/geometries/pillar/{geo_id}/{geo_id}.dsco")


def aggregate_case(*, geo_id, d_p_mm, d_h_mm, steps, comparison, face_counts):
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
    return {
        "geo_id": geo_id,
        "d_p_mm": d_p_mm,
        "d_h_mm": d_h_mm,
        "label_set_match": label_set_match,
        "max_bbox_abs_diff_mm": max_bbox_abs_diff_mm,
        "zone_count_match": zone_count_match,
        "area_max_rel_diff": area_max_rel_diff,
        "generated_face_counts": face_counts,
        "steps": steps,
    }


def case_failed(summary):
    for name in _STEPS:
        step = summary["steps"][name]
        if step["return_code"] != 0:
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


def run_generate_step(*, d_p_mm, d_h_mm, d_f_mm, geo_id, out_dir, log_path):
    """Call the generator in-process and store its output. Record failures."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            written = generate_pillar_cad(
                d_p_mm=d_p_mm,
                d_h_mm=d_h_mm,
                d_f_mm=d_f_mm,
                geo_id=geo_id,
                out_dir=out_dir,
            )
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


def _probe_cmd(cad_path, work_dir, compare_to=None):
    cmd = [
        sys.executable,
        str(PROBE_PATH),
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


def run_case(point, *, d_f_mm, out_root):
    geo_id = point["geo_id"]
    dirs = case_directories(out_root, geo_id)
    log_dir = out_root / "logs" / geo_id
    steps = {}

    ref_log = log_dir / "reference.log"
    ref_code = run_subprocess_step(
        _probe_cmd(reference_cad_path(geo_id), dirs["ref"]),
        ref_log,
    )
    steps["reference"] = {"return_code": ref_code, "log": str(ref_log)}

    gen_log = log_dir / "generate.log"
    gen_code = run_generate_step(
        d_p_mm=point["d_p_mm"],
        d_h_mm=point["d_h_mm"],
        d_f_mm=d_f_mm,
        geo_id=geo_id,
        out_dir=dirs["gen"],
        log_path=gen_log,
    )
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
    cmp_json = dirs["cmp"] / REFERENCE_JSON_NAME
    meta_path = dirs["gen"] / f"{geo_id}{META_JSON_SUFFIX}"
    try:
        cmp_payload = _read_json_object(cmp_json)
        if isinstance(cmp_payload, dict):
            comparison = cmp_payload.get("comparison")
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
        d_p_mm=point["d_p_mm"],
        d_h_mm=point["d_h_mm"],
        steps=steps,
        comparison=comparison,
        face_counts=face_counts,
    )


def write_summary(out_root, payload):
    path = out_root / "geometry_parity_summary.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def print_summary(cases):
    print(
        f"{'geo_id':<16} {'labels':>8} {'max_|bbox|_mm':>14} "
        f"{'zones':>8} {'area_rel':>12} {'ref':>4} {'gen':>4} {'cmp':>4}"
    )
    for case in cases:
        area = case["area_max_rel_diff"]
        area_text = "" if area is None else f"{area:.6g}"
        bbox = case["max_bbox_abs_diff_mm"]
        bbox_text = "" if bbox is None else f"{bbox:.6g}"
        print(
            f"{case['geo_id']:<16} {_flag(case['label_set_match']):>8} "
            f"{bbox_text:>14} {_flag(case['zone_count_match']):>8} "
            f"{area_text:>12} "
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


def build_parser():
    parser = argparse.ArgumentParser(
        description="Import-only parity of generated Pillar CAD against the nine manual files.",
    )
    parser.add_argument("--d-f-mm", required=True, type=float, help="Filament diameter, mm.")
    parser.add_argument(
        "--out-root",
        required=True,
        help="Absolute output root. Not C:/ro_data. Case subfolders must not exist.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if isinstance(args.d_f_mm, bool) or not math.isfinite(args.d_f_mm) or args.d_f_mm <= 0.0:
        raise ValueError(f"--d-f-mm must be a positive finite number, got {args.d_f_mm!r}.")
    out_root = resolve_out_root(args.out_root)
    if out_root.exists() and not out_root.is_dir():
        raise ValueError(f"out root is not a directory: {out_root}")
    points = design_points(load_batch_config())
    if len(points) != 9:
        raise RuntimeError(
            f"Expected 9 pillar design points, found {len(points)}."
        )
    refuse_existing_case_dirs(out_root, [point["geo_id"] for point in points])
    out_root.mkdir(parents=True, exist_ok=True)

    cases = []
    for point in points:
        print(f"CASE {point['geo_id']}")
        cases.append(run_case(point, d_f_mm=args.d_f_mm, out_root=out_root))
    payload = {
        "d_f_mm": args.d_f_mm,
        "out_root": str(out_root),
        "area_rtol": AREA_RTOL,
        "cases": cases,
    }
    print_summary(cases)
    write_summary(out_root, payload)
    if any(case_failed(case) for case in cases):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
