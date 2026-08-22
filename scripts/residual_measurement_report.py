#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Measure residual / QoI stationarity from solve transcripts (SAFE, no Fluent).

Writes a NEW report under RO_DATA_ROOT/inventory/:
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

from ro.residual_transcript import (
    PARSE_OK,
    build_summary_text,
    measure_case_dir,
    write_measurement_csv,
)
from ro.paths import data_root, project_root, runs_root
from ro.solver_common import (
    DEFAULT_MAX_ITERATIONS_FALLBACK,
    DEFAULT_RESIDUAL_TARGET_FALLBACK,
    max_iterations_from_common_solver_settings,
    residual_target_from_common_solver_settings,
)
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


_LOAD_FAILED = object()


def _load_batch_common_solver_settings() -> Any:
    """Load common_solver_settings from batch_config.

    Returns the attribute value (may be None / non-dict) on successful module
    load, or ``_LOAD_FAILED`` after warning when the file cannot be imported.
    """
    batch_config_path = project_root() / "configs" / "batch_config.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "_batch_config_for_residual_report", batch_config_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load batch config: {batch_config_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, OSError) as exc:
        print(
            "WARNING: could not load batch_config for residual defaults "
            f"({type(exc).__name__}: {exc}); "
            f"falling back to max_iterations={DEFAULT_MAX_ITERATIONS_FALLBACK}, "
            f"residual_target={DEFAULT_RESIDUAL_TARGET_FALLBACK}.",
            file=sys.stderr,
        )
        return _LOAD_FAILED

    return getattr(module, "common_solver_settings", None)


def default_max_iterations() -> int:
    settings = _load_batch_common_solver_settings()
    if settings is _LOAD_FAILED:
        return DEFAULT_MAX_ITERATIONS_FALLBACK
    return max_iterations_from_common_solver_settings(settings)


def default_residual_target() -> float:
    settings = _load_batch_common_solver_settings()
    if settings is _LOAD_FAILED:
        return DEFAULT_RESIDUAL_TARGET_FALLBACK
    return residual_target_from_common_solver_settings(settings)


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
        raise NotADirectoryError(
            f"Results root is not an existing directory: {results_root}"
        )
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
        default=None,
        help="Root results directory (default: RO_DATA_ROOT/runs).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: RO_DATA_ROOT/inventory).",
    )
    parser.add_argument("--geo-name", type=str, default=None)
    parser.add_argument("--case-name", type=str, default=None)
    parser.add_argument(
        "--window",
        type=int,
        default=200,
        help=(
            "ENDPOINT window: last N parsed iterations used for trend labels and "
            "window_* / acf1 / shortfall metrics (default: 200; intended range "
            "100-200). Large values (>=500) mix descent history into the endpoint "
            "and make trend labels meaningless. Full-series history columns are "
            "always computed separately and do not feed the labels."
        ),
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


def resolve_path_defaults(args: argparse.Namespace) -> argparse.Namespace:
    args.results_root = (args.results_root or runs_root()).resolve()
    if not args.results_root.is_dir():
        raise NotADirectoryError(
            f"Runs root is not an existing directory: {args.results_root}"
        )
    args.output_dir = (args.output_dir or data_root() / "inventory").resolve()
    return args


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
    args = parse_args(argv)
    try:
        resolve_path_defaults(args)
        return run_report(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
