#!/usr/bin/env python3
"""Validate on-disk summary_metrics_wide.csv files against load-bearing columns.

No Fluent required. Intended for pre-sweep checks on the Windows server::

    python scripts/validate_load_bearing_summary_csvs.py --data-root C:/ro_data

Exit codes:
  0 — every file PASS or LEGACY (legacy = pre-decomposition schema only)
  1 — any FAIL or unreadable file

Prior to the accounting fix in this script, FAIL lines were printed but the
failure counter and exit code stayed 0 (read errors were the only failures
counted). That was exit code 0 with printed FAIL lines.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from ro.fluent_report_helpers import (
    DIAGNOSTIC_FLUX_DECOMPOSITION_SUMMARY_METRICS,
    LOAD_BEARING_SUMMARY_METRICS,
    audit_load_bearing_summary_columns,
)
from ro.paths import data_root as default_data_root

STATUS_PASS = "PASS"
STATUS_LEGACY = "LEGACY"
STATUS_FAIL = "FAIL"


def _read_wide_row(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("no data rows")
    return rows[0]


def _blank_columns(row: dict[str, str], columns: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for column in columns:
        if column not in row:
            missing.append(column)
            continue
        value = (row.get(column) or "").strip()
        if not value or value.lower() in {"nan", "none"}:
            missing.append(column)
    return missing


def discover_default_csvs(data_root_path: Path) -> list[Path]:
    patterns = [
        "runs/diamond/D2450_a45/*/*/post/reports/summary_metrics_wide.csv",
        "runs/**/REF_empty/**/post/reports/summary_metrics_wide.csv",
    ]
    found: list[Path] = []
    for pattern in patterns:
        found.extend(sorted(data_root_path.glob(pattern)))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def classify_file(path: Path) -> tuple[str, dict[str, object], list[str]]:
    row = _read_wide_row(path)
    audit = audit_load_bearing_summary_columns(row)
    diagnostic_missing = _blank_columns(
        row,
        DIAGNOSTIC_FLUX_DECOMPOSITION_SUMMARY_METRICS,
    )
    missing = list(audit["missing"])
    legacy_exempt_missing = list(audit["legacy_exempt_missing"])
    if missing:
        status = STATUS_FAIL
    elif audit["legacy_schema"] and legacy_exempt_missing:
        status = STATUS_LEGACY
    else:
        status = STATUS_PASS
    return status, audit, diagnostic_missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply load-bearing summary column guards to existing "
            "summary_metrics_wide.csv files."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "CSV files or directories to scan. When omitted, scans diamond "
            "D2450_a45 runs and REF_empty under --data-root."
        ),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="RO data root (default: RO_DATA_ROOT or repo data_root()).",
    )
    args = parser.parse_args(argv)

    if args.paths:
        csv_paths: list[Path] = []
        for raw in args.paths:
            path = Path(raw)
            if path.is_dir():
                csv_paths.extend(sorted(path.rglob("summary_metrics_wide.csv")))
            else:
                csv_paths.append(path)
    else:
        root = args.data_root or default_data_root()
        csv_paths = discover_default_csvs(root)
        if not csv_paths:
            print(f"No summary_metrics_wide.csv files found under {root}")
            return 1

    print(f"Load-bearing columns ({len(LOAD_BEARING_SUMMARY_METRICS)}):")
    for column in LOAD_BEARING_SUMMARY_METRICS:
        print(f"  {column}")
    print()

    pass_count = 0
    legacy_count = 0
    fail_count = 0

    for path in csv_paths:
        try:
            status, audit, diagnostic_missing = classify_file(path)
        except (OSError, ValueError) as exc:
            fail_count += 1
            print(f"{STATUS_FAIL} {path}")
            print(f"  read error: {exc}")
            continue

        print(f"{status} {path}")
        if status == STATUS_FAIL:
            fail_count += 1
            print(f"  missing load-bearing: {audit['missing']}")
        elif status == STATUS_LEGACY:
            legacy_count += 1
            print(
                "  legacy schema (no flux decomposition group); exempt missing: "
                f"{audit['legacy_exempt_missing']}"
            )
        else:
            pass_count += 1

        if diagnostic_missing:
            print(f"  missing diagnostic flux decomposition: {diagnostic_missing}")

    print()
    print(
        f"Checked {len(csv_paths)} file(s): "
        f"pass={pass_count}, legacy={legacy_count}, fail={fail_count}."
    )
    exit_code = 1 if fail_count else 0
    print(f"Exit code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
