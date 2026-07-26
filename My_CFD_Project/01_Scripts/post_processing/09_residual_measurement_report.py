#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Measure residual / QoI stationarity from solve transcripts (SAFE, no Fluent).

Writes a NEW report under 03_Results/_inventory/:
  residual_measurement.csv
  residual_measurement_summary.txt

Does NOT modify case_inventory schemas or rewrite convergence_status / case_status.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _residual_transcript import (  # noqa: E402
    PARSE_OK,
    build_summary_text,
    measure_case_dir,
    write_measurement_csv,
)

DEFAULT_RESULTS_ROOT = Path("My_CFD_Project") / "03_Results"
DEFAULT_OUTPUT_DIR = DEFAULT_RESULTS_ROOT / "_inventory"

SKIP_DIR_NAMES = {
    "_inventory",
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "post",
    "figures",
    "reports",
    "contours",
    "plots",
    "images",
    "tmp",
    "temp",
}


def _load_batch_common_solver_settings() -> dict[str, Any]:
    batch_config_path = SCRIPT_DIR.parent / "batch_config.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "_batch_config_for_residual_report", batch_config_path
        )
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = getattr(module, "common_solver_settings", {}) or {}
        return dict(settings) if isinstance(settings, dict) else {}
    except Exception:
        return {}


def default_max_iterations() -> int:
    settings = _load_batch_common_solver_settings()
    try:
        return int(settings.get("max_iterations", 2000))
    except (TypeError, ValueError):
        return 2000


def default_residual_target() -> float:
    settings = _load_batch_common_solver_settings()
    try:
        return float(settings.get("residual_target", 1e-7))
    except (TypeError, ValueError):
        return 1e-7


def should_skip_dir_name(name: str, include_hidden: bool) -> bool:
    if name in SKIP_DIR_NAMES:
        return True
    if not include_hidden and name.startswith(("_", ".")):
        return True
    return False


def discover_cases(
    results_root: Path,
    *,
    geo_name_filter: Optional[str],
    case_name_filter: Optional[str],
    include_hidden: bool,
) -> list[tuple[str, str, Path]]:
    cases: list[tuple[str, str, Path]] = []
    if not results_root.is_dir():
        return cases
    for geo_dir in sorted(
        (p for p in results_root.iterdir() if p.is_dir()),
        key=lambda p: p.name.lower(),
    ):
        if should_skip_dir_name(geo_dir.name, include_hidden):
            continue
        if geo_name_filter and geo_dir.name != geo_name_filter:
            continue
        for case_dir in sorted(
            (p for p in geo_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        ):
            if should_skip_dir_name(case_dir.name, include_hidden):
                continue
            if case_name_filter and case_dir.name != case_name_filter:
                continue
            cases.append((geo_dir.name, case_dir.name, case_dir))
    return cases


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure residual/QoI stationarity from solve transcripts. "
            "Does not rewrite inventory convergence or case status."
        )
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Root results directory (default: {DEFAULT_RESULTS_ROOT.as_posix()})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR.as_posix()})",
    )
    parser.add_argument("--geo-name", type=str, default=None)
    parser.add_argument("--case-name", type=str, default=None)
    parser.add_argument(
        "--window",
        type=int,
        default=200,
        help="Last N parsed iterations for window stats (default: 200).",
    )
    parser.add_argument(
        "--residual-target",
        type=float,
        default=default_residual_target(),
        help="Configured residual target used for shortfall/target_met columns.",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=default_max_iterations(),
        help="Iteration cap in effect (default: batch_config common_solver_settings).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden/underscore geometry/case dirs.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run_report(args: argparse.Namespace) -> int:
    if args.window <= 0:
        print("ERROR: --window must be positive", file=sys.stderr)
        return 1
    if args.residual_target <= 0:
        print("ERROR: --residual-target must be positive", file=sys.stderr)
        return 1
    if args.max_iter <= 0:
        print("ERROR: --max-iter must be positive", file=sys.stderr)
        return 1

    results_root = args.results_root
    output_dir = args.output_dir
    cases = discover_cases(
        results_root,
        geo_name_filter=args.geo_name,
        case_name_filter=args.case_name,
        include_hidden=args.include_hidden,
    )
    print(f"Results root : {results_root}")
    print(f"Output dir   : {output_dir}")
    print(f"Cases found  : {len(cases)}")
    print(f"Window N     : {args.window}")
    print(f"Residual tgt : {args.residual_target}")
    print(f"Iter cap     : {args.max_iter}")

    records = []
    for geo_name, case_name, case_dir in cases:
        record = measure_case_dir(
            case_dir,
            geo_name,
            case_name,
            window_n=args.window,
            residual_target=args.residual_target,
            iteration_cap=args.max_iter,
        )
        records.append(record)
        if args.verbose:
            print(
                f"  {geo_name}/{case_name}: {record.get('parse_status')} "
                f"iters={record.get('iterations_completed')} "
                f"transcript={Path(str(record.get('solve_transcript') or '')).name}"
            )

    csv_path = output_dir / "residual_measurement.csv"
    summary_path = output_dir / "residual_measurement_summary.txt"
    summary_text = build_summary_text(records)

    if args.dry_run:
        print(f"Dry run: would write {csv_path}")
        print(f"Dry run: would write {summary_path}")
        print(summary_text)
        ok = sum(1 for r in records if r.get("parse_status") == PARSE_OK)
        print(f"ok={ok} / total={len(records)}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    write_measurement_csv(csv_path, records)
    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {summary_path}")
    print(summary_text)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return run_report(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
