"""Audit PASS-leaf convergence from monitor report files.

For each run whose manifest ``convergence_quality`` is PASS, report the
stop iteration, the final continuity and nacl residuals, and the UG 37.18
relative change of LMH, spacer pressure drop, and CP over the last 20 and
last 100 iterations.

Monitor history is read from the report files
(``lmh_udm_avg.out``, ``pressure_drop_spacer.out``, ``cp_window.out`` or
``cp_full_membrane.out``). Those files are the source of record. When a
report file is missing, the same columns are taken from transcript rows
whose continuity and nacl fields are written in scientific notation.
Fixed-decimal transcript columns are not used.

Prints one summary for leaves that stopped before the iteration cap and
one for leaves that reached it, and writes a CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from ro.monitor_change import (
    CP_MONITOR_FULL_MEMBRANE,
    CP_MONITOR_WINDOW,
    DP_REPORT_FILE,
    LMH_REPORT_FILE,
    cp_report_file_name,
    read_report_series,
    relative_change_last_iterations,
)
from ro.residual_transcript import (
    _clock_and_remaining_from_right,
    is_residual_header_line,
    require_residual_header_layout,
)

_SCIENTIFIC = re.compile(
    r"^[+-]?(?:\d+\.\d+|\d+\.|\.\d+|\d+)[eE][+-]?\d+$"
)
_GROUP_EARLY = "stopped_early"
_GROUP_CAP = "ran_to_cap"
_GROUP_UNKNOWN = "unknown"
_MONITORS = (
    ("lmh", LMH_REPORT_FILE, "lmh_udm_avg"),
    ("dp", DP_REPORT_FILE, "pressure_drop_spacer"),
    ("cp", cp_report_file_name(CP_MONITOR_WINDOW), CP_MONITOR_WINDOW),
)
_COLUMNS = (
    "case_dir",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "group",
    "stop_iteration",
    "max_iterations",
    "continuity_final",
    "nacl_final",
    "monitor_change_last20_lmh",
    "monitor_change_last100_lmh",
    "monitor_change_last20_dp",
    "monitor_change_last100_dp",
    "monitor_change_last20_cp",
    "monitor_change_last100_cp",
    "lmh_source",
    "dp_source",
    "cp_source",
    "residual_source",
)


def _load_json(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def iter_pass_leaves(data_root: Path):
    """Run leaves with ``convergence_quality`` PASS. Mesh manifests are skipped."""
    for path in sorted(data_root.rglob("manifest.json")):
        if path.parent.name == "post":
            continue
        payload = _load_json(path)
        if payload is None:
            continue
        if payload.get("convergence_quality") != "PASS":
            continue
        if "run_id" not in payload and "solver_settings" not in payload:
            continue
        yield path.parent, payload


def scientific_transcript_rows(text: str) -> list[dict]:
    """Residual rows whose continuity and nacl tokens use scientific notation.

    Last row at each iteration wins. A fixed-decimal residual row is skipped,
    which drops the low-precision transcript pages.
    """
    header = None
    by_iter: dict[int, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if is_residual_header_line(line):
            try:
                header = require_residual_header_layout(line)
            except ValueError:
                header = None
            continue
        if header is None or not line[0].isdigit():
            continue
        tokens = line.split()
        clock_idx, _remaining = _clock_and_remaining_from_right(tokens)
        if clock_idx is None:
            continue
        value_names = header[:-1]
        values = tokens[:clock_idx]
        if len(values) != len(value_names) or value_names[0] != "iter":
            continue
        try:
            iteration = int(values[0])
        except ValueError:
            continue
        named: dict[str, float] = {}
        scientific_residuals = True
        parsed = True
        for name, token in zip(value_names[1:], values[1:]):
            if name in ("continuity", "nacl") and _SCIENTIFIC.match(token) is None:
                scientific_residuals = False
                break
            try:
                named[name] = float(token)
            except ValueError:
                parsed = False
                break
        if not scientific_residuals or not parsed:
            continue
        if "continuity" not in named or "nacl" not in named:
            continue
        named["iter"] = float(iteration)
        by_iter[iteration] = named
    return [by_iter[key] for key in sorted(by_iter)]


def _read_transcripts(case_dir: Path) -> str:
    chunks = []
    try:
        paths = sorted(case_dir.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return ""
    for path in paths:
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".trn", ".log"}:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def _series_from_rows(rows: list[dict], column: str) -> list[tuple[int, float]]:
    series = []
    for row in rows:
        if column not in row:
            continue
        series.append((int(row["iter"]), float(row[column])))
    return series


def _cp_series(case_dir: Path, rows: list[dict]) -> tuple[list[tuple[int, float]], str]:
    window = read_report_series(case_dir / cp_report_file_name(CP_MONITOR_WINDOW))
    if window:
        return window, "report_file"
    full = read_report_series(
        case_dir / cp_report_file_name(CP_MONITOR_FULL_MEMBRANE)
    )
    if full:
        return full, "report_file"
    for column in (CP_MONITOR_WINDOW, CP_MONITOR_FULL_MEMBRANE):
        series = _series_from_rows(rows, column)
        if series:
            return series, "transcript"
    return [], "missing"


def _monitor_series(case_dir: Path, filename: str, column: str, rows: list[dict]):
    series = read_report_series(case_dir / filename)
    if series:
        return series, "report_file"
    fallback = _series_from_rows(rows, column)
    if fallback:
        return fallback, "transcript"
    return [], "missing"


def _max_iterations(manifest: dict) -> int | None:
    settings = manifest.get("solver_settings")
    if not isinstance(settings, dict):
        return None
    value = settings.get("max_iterations")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _group(stop_iteration: int | None, max_iterations: int | None) -> str:
    if stop_iteration is None or max_iterations is None:
        return _GROUP_UNKNOWN
    if stop_iteration >= max_iterations:
        return _GROUP_CAP
    return _GROUP_EARLY


def _fmt(value) -> str:
    if value is None:
        return ""
    return format(float(value), ".16g")


def audit_leaf(case_dir: Path, manifest: dict) -> dict:
    rows = scientific_transcript_rows(_read_transcripts(case_dir))
    lmh, lmh_source = _monitor_series(case_dir, LMH_REPORT_FILE, "lmh_udm_avg", rows)
    dp, dp_source = _monitor_series(
        case_dir, DP_REPORT_FILE, "pressure_drop_spacer", rows
    )
    cp, cp_source = _cp_series(case_dir, rows)
    stop_candidates = [
        int(series[-1][0]) for series in (lmh, dp, cp) if series
    ]
    if stop_candidates:
        stop_iteration = max(stop_candidates)
    elif rows:
        stop_iteration = int(rows[-1]["iter"])
    else:
        stop_iteration = None
    max_iterations = _max_iterations(manifest)
    continuity = None if not rows else float(rows[-1]["continuity"])
    nacl = None if not rows else float(rows[-1]["nacl"])
    record = {
        "case_dir": str(case_dir),
        "family": manifest.get("family") or "",
        "geo_id": manifest.get("geo_id") or "",
        "mesh_id": manifest.get("mesh_id") or "",
        "run_id": manifest.get("run_id") or "",
        "group": _group(stop_iteration, max_iterations),
        "stop_iteration": "" if stop_iteration is None else str(stop_iteration),
        "max_iterations": "" if max_iterations is None else str(max_iterations),
        "continuity_final": _fmt(continuity),
        "nacl_final": _fmt(nacl),
        "lmh_source": lmh_source,
        "dp_source": dp_source,
        "cp_source": cp_source,
        "residual_source": "transcript" if rows else "missing",
    }
    for label, series in (("lmh", lmh), ("dp", dp), ("cp", cp)):
        for n in (20, 100):
            record[f"monitor_change_last{n}_{label}"] = _fmt(
                relative_change_last_iterations(series, n)
            )
    return record


def _finite(text: str) -> float | None:
    if text is None or str(text).strip() == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def print_group_summary(rows: list[dict]) -> None:
    print(f"PASS leaves: {len(rows)}")
    for group in (_GROUP_EARLY, _GROUP_CAP, _GROUP_UNKNOWN):
        members = [row for row in rows if row["group"] == group]
        print(f"{group}: {len(members)}")
        if not members or group == _GROUP_UNKNOWN:
            continue
        for n in (20, 100):
            for label in ("lmh", "dp", "cp"):
                key = f"monitor_change_last{n}_{label}"
                values = [
                    value
                    for value in (_finite(row.get(key)) for row in members)
                    if value is not None
                ]
                if not values:
                    print(f"  last{n} {label}: no samples")
                    continue
                print(
                    f"  last{n} {label}: n={len(values)} "
                    f"max={max(values):.8g} min={min(values):.8g}"
                )


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in _COLUMNS})


def audit_data_root(data_root: Path) -> list[dict]:
    if not data_root.is_dir():
        raise FileNotFoundError(f"data root is not a directory: {data_root}")
    return [audit_leaf(case_dir, manifest) for case_dir, manifest in iter_pass_leaves(data_root)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_root", help="Campaign data root, for example C:/ro_data.")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="CSV path. Default: <data_root>/convergence_audit.csv.",
    )
    args = parser.parse_args(argv)
    data_root = Path(args.data_root)
    output = Path(args.output) if args.output else data_root / "convergence_audit.csv"
    try:
        rows = audit_data_root(data_root)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    print_group_summary(rows)
    write_csv(output, rows)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
