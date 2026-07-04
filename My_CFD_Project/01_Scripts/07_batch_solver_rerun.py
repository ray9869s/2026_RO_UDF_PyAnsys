"""Safe CLI runner for active solver reruns.

This script plans and, on the Windows Fluent host, executes conservative
continuation runs for active current-matrix CFD cases. It intentionally avoids
importing PyFluent during argparse help and dry-run planning.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any


MATRIX_CASE_RE = re.compile(r"^u\d+p\d+_p\d+M$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "03_Results"
DEFAULT_REPORT_SCRIPT = SCRIPT_DIR / "post_processing" / "01_pyfluent_report_extract.py"

PLAN_FIELDS = [
    "selected_index",
    "source_row_number",
    "geo_name",
    "case_name",
    "convergence_status_before",
    "case_status_before",
    "case_dir",
    "final_case_file",
    "final_data_file",
    "planned_backup_dir",
    "attempt_dir",
    "attempt_case_file",
    "attempt_data_file",
    "matrix_case_name",
    "final_pair_exists",
    "case_dir_exists",
    "final_case_exists",
    "final_data_exists",
]

RESULT_FIELDS = [
    "selected_index",
    "source_row_number",
    "geo_name",
    "case_name",
    "convergence_status_before",
    "case_status_before",
    "final_case_file",
    "final_data_file",
    "backup_dir",
    "rerun_status",
    "returncode_or_exception",
    "iterations_requested",
    "runtime_seconds",
    "output_case_file",
    "output_data_file",
    "report_extraction_status",
    "error_summary",
    "suggested_next_action",
    "log_file",
    "solver_strategy",
    "promote_on_success",
    "attempt_dir",
    "attempt_case_file",
    "attempt_data_file",
    "promoted_to_final",
    "launcher_profile",
    "launch_mode",
    "launch_mode_used",
    "launch_working_dir",
    "launch_case_dir_resolved",
    "launch_exception_type",
    "launch_exception_message",
    "processor_count_requested",
    "processor_count_used",
    "processor_count",
    "start_timeout",
    "fallback_used",
    "meshing_to_solver_used",
    "launch_kwargs",
    "failure_stage",
    "strategy_stage_status",
    "convergence_assessment",
    "monitor_window",
    "monitor_rel_tol",
    "residual_growth_limit",
    "product_version",
]


class Tee:
    """Write stream data to multiple file-like objects."""

    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or execute conservative Fluent solver continuation reruns "
            "for active current-matrix cases."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--candidates-csv",
        type=Path,
        default=None,
        help=(
            "CSV of active solver rerun candidates. Defaults to "
            "<results-root>/_inventory/active_solver_rerun_candidates.csv."
        ),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="Root results folder containing <geo_name>/<case_name> case folders.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Central folder for solver_rerun_plan/results/summary outputs.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Central folder for per-case solver rerun logs.",
    )
    parser.add_argument(
        "--report-script",
        type=Path,
        default=DEFAULT_REPORT_SCRIPT,
        help="Optional report extraction worker checked for safe direct CLI support.",
    )

    parser.add_argument(
        "--geo-name",
        action="append",
        default=[],
        help="Only include matching geometry name. May be passed more than once.",
    )
    parser.add_argument(
        "--case-name",
        action="append",
        default=[],
        help="Only include matching case name. May be passed more than once.",
    )
    parser.add_argument(
        "--convergence-status",
        action="append",
        default=[],
        help="Only include matching convergence status. May be passed more than once.",
    )
    parser.add_argument(
        "--limit",
        type=non_negative_int,
        default=None,
        help="Maximum number of selected cases to process after filtering.",
    )
    parser.add_argument(
        "--start-index",
        type=non_negative_int,
        default=0,
        help="Zero-based offset into the filtered current-matrix candidate list.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print and write the plan/results without launching Fluent or PyFluent.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing later selected cases after a failed live rerun.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore previous SUCCESS records when --skip-existing-rerun-success is used.",
    )
    parser.add_argument(
        "--skip-existing-rerun-success",
        action="store_true",
        help=(
            "Skip cases that already have a SUCCESS record in the existing "
            "solver_rerun_results.csv in the output directory."
        ),
    )

    parser.add_argument(
        "--additional-iterations",
        type=positive_int,
        default=3000,
        help="Total iteration budget for the selected solver strategy.",
    )
    parser.add_argument(
        "--solver-strategy",
        choices=[
            "continue_only",
            "damped_steady",
            "pseudo_transient_ramp",
            "first_order_ramp",
            "diagnose_only",
        ],
        default="damped_steady",
        help="Continuation strategy applied after loading the final case/data.",
    )
    parser.add_argument(
        "--iteration-chunk-size",
        type=positive_int,
        default=200,
        help="Iteration chunk size used by staged solver strategies.",
    )
    parser.add_argument(
        "--monitor-window",
        type=positive_int,
        default=200,
        help="Last-window iteration count used for monitor stability assessment.",
    )
    parser.add_argument(
        "--monitor-rel-tol",
        type=positive_float,
        default=0.005,
        help="Relative monitor variation threshold for success assessment.",
    )
    parser.add_argument(
        "--residual-growth-limit",
        type=positive_float,
        default=100.0,
        help="Allowed residual growth factor before marking divergence.",
    )
    parser.add_argument(
        "--promote-on-success",
        dest="promote_on_success",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Promote staged attempt case/data to the final filenames only after "
            "success criteria pass."
        ),
    )
    parser.add_argument(
        "--residual-target",
        type=positive_float,
        default=1e-5,
        help="Residual convergence criterion to apply before continuing.",
    )
    parser.add_argument(
        "--use-pseudo-transient",
        dest="use_pseudo_transient",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Best-effort enable pseudo-transient continuation if supported.",
    )
    parser.add_argument(
        "--pressure-velocity-coupling",
        choices=["preserve", "SIMPLE", "SIMPLEC", "Coupled"],
        default="preserve",
        help="Best-effort pressure-velocity coupling update. preserve leaves it unchanged.",
    )
    parser.add_argument(
        "--coupled",
        action="store_true",
        help="Shortcut for --pressure-velocity-coupling Coupled.",
    )
    parser.add_argument(
        "--relaxation-profile",
        choices=["conservative", "baseline"],
        default="conservative",
        help="Best-effort under-relaxation profile. baseline leaves controls unchanged.",
    )
    parser.add_argument(
        "--write-transcript",
        dest="write_transcript",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write a Fluent transcript next to the per-case runner log.",
    )

    parser.add_argument(
        "--run-report-after-success",
        dest="run_report_after_success",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run direct report extraction after successful reruns if the worker has safe CLI support.",
    )

    parser.add_argument(
        "--product-version",
        default="25.1.0",
        help="Fluent product version for live reruns.",
    )
    parser.add_argument(
        "--launch-mode",
        choices=["solver", "meshing_to_solver"],
        default="meshing_to_solver",
        help=(
            "How to launch Fluent for live reruns. solver launches directly in "
            "solver mode; meshing_to_solver keeps the older compatibility path."
        ),
    )
    parser.add_argument(
        "--launcher-profile",
        choices=["known_good_postprocess", "solver_direct"],
        default="known_good_postprocess",
        help=(
            "Launch profile. known_good_postprocess mirrors the working shear "
            "postprocess launch path; solver_direct uses direct solver launch."
        ),
    )
    parser.add_argument(
        "--processor-count",
        type=positive_int,
        default=4,
        help="Fluent processor count for live reruns.",
    )
    parser.add_argument(
        "--no-launch-fallback",
        action="store_true",
        help=(
            "Disable the one-time solver_direct Deadline Exceeded fallback to "
            "meshing_to_solver with 4 processors."
        ),
    )
    parser.add_argument(
        "--ui-mode",
        default="gui",
        help="Fluent UI mode for live reruns.",
    )
    parser.add_argument(
        "--graphics-driver",
        default=None,
        help=(
            "Optional Fluent graphics driver for live reruns. Omit for solver-only "
            "batch runs."
        ),
    )
    parser.add_argument(
        "--start-timeout",
        "--fluent-start-timeout",
        dest="start_timeout",
        type=positive_int,
        default=600,
        help="PyFluent launch start timeout in seconds.",
    )
    parser.add_argument(
        "--fluent-health-timeout",
        type=positive_int,
        default=600,
        help="PyFluent health check timeout in seconds.",
    )
    parser.add_argument(
        "--species-residual-name",
        default="nacl",
        help="Species residual equation name to update when present.",
    )

    args = parser.parse_args(argv)

    if args.coupled:
        args.pressure_velocity_coupling = "Coupled"

    args.results_root = args.results_root or DEFAULT_RESULTS_ROOT
    args.candidates_csv = (
        args.candidates_csv
        or args.results_root / "_inventory" / "active_solver_rerun_candidates.csv"
    )
    args.output_dir = args.output_dir or args.results_root / "_inventory" / "solver_rerun"
    args.logs_dir = args.logs_dir or args.results_root / "_inventory" / "solver_rerun_logs"

    unsafe_paths = [
        args.results_root,
        args.candidates_csv,
        args.output_dir,
        args.logs_dir,
        args.report_script,
    ]
    reject_windows_drive_paths_on_non_windows(unsafe_paths, parser)

    args.results_root = args.results_root.resolve()
    args.candidates_csv = args.candidates_csv.resolve()
    args.output_dir = args.output_dir.resolve()
    args.logs_dir = args.logs_dir.resolve()
    args.report_script = args.report_script.resolve()

    report_cli_safe, report_cli_reason = report_worker_has_safe_direct_cli(args.report_script)
    args.report_cli_safe = report_cli_safe
    args.report_cli_reason = report_cli_reason
    if args.run_report_after_success is None:
        args.run_report_after_success = report_cli_safe

    return args


def reject_windows_drive_paths_on_non_windows(
    paths: list[Path],
    parser: argparse.ArgumentParser,
) -> None:
    if os.name == "nt":
        return

    unsafe = [str(path) for path in paths if WINDOWS_DRIVE_RE.match(str(path))]
    if not unsafe:
        return

    parser.error(
        "Windows drive paths are not accepted on this non-Windows host because "
        "they can create C: folders inside the WSL repo. Run live/server paths "
        f"from Windows Python instead. Unsafe path(s): {unsafe}"
    )


def resolve_existing_dir(path: Path, label: str) -> Path:
    """Resolve and require an existing directory."""
    resolved = path.resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(f"{label} is not an existing directory: {resolved}")
    return resolved


def resolve_existing_file(path: Path, label: str) -> Path:
    """Resolve and require an existing file."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} is not an existing file: {resolved}")
    return resolved


def fluent_path(path: Path) -> str:
    """Convert a path to a Fluent-friendly absolute path."""
    return str(path.resolve()).replace("\\", "/")


@contextmanager
def pushd(path: Path):
    """Temporarily change the process working directory."""
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_candidates(candidates_csv: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not candidates_csv.is_file():
        raise FileNotFoundError(f"Candidate CSV not found: {candidates_csv}")

    with candidates_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Candidate CSV has no header: {candidates_csv}")
        rows = []
        for row_number, row in enumerate(reader, start=2):
            cleaned = {
                str(key).strip(): "" if value is None else str(value).strip()
                for key, value in row.items()
                if key is not None
            }
            cleaned["_source_row_number"] = str(row_number)
            rows.append(cleaned)

    return rows, list(reader.fieldnames)


def first_value(row: dict[str, str], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key, "")
        if value != "":
            return value
    return ""


def normalize_candidate_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "source_row_number": row.get("_source_row_number", ""),
        "geo_name": first_value(row, ["geo_name", "geometry", "geometry_name"]),
        "case_name": first_value(row, ["case_name", "case"]),
        "convergence_status_before": first_value(
            row,
            ["convergence_status", "convergence_status_before"],
        ),
        "case_status_before": first_value(
            row,
            ["case_status", "case_status_before", "status"],
        ),
    }


def select_candidates(
    rows: list[dict[str, str]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    stats = {
        "input_rows": len(rows),
        "missing_required": 0,
        "non_matrix_case_name": 0,
        "filtered_geo_name": 0,
        "filtered_case_name": 0,
        "filtered_convergence_status": 0,
        "before_start_limit": 0,
        "selected": 0,
    }

    filtered: list[dict[str, str]] = []
    geo_filter = set(args.geo_name)
    case_filter = set(args.case_name)
    convergence_filter = set(args.convergence_status)

    for row in rows:
        candidate = normalize_candidate_row(row)
        geo_name = candidate["geo_name"]
        case_name = candidate["case_name"]

        if not geo_name or not case_name:
            stats["missing_required"] += 1
            continue

        if not MATRIX_CASE_RE.match(case_name):
            stats["non_matrix_case_name"] += 1
            continue

        if geo_filter and geo_name not in geo_filter:
            stats["filtered_geo_name"] += 1
            continue

        if case_filter and case_name not in case_filter:
            stats["filtered_case_name"] += 1
            continue

        if (
            convergence_filter
            and candidate["convergence_status_before"] not in convergence_filter
        ):
            stats["filtered_convergence_status"] += 1
            continue

        filtered.append(candidate)

    stats["before_start_limit"] = len(filtered)
    selected = filtered[args.start_index :]
    if args.limit is not None:
        selected = selected[: args.limit]

    for selected_index, candidate in enumerate(selected, start=1):
        candidate["selected_index"] = str(selected_index)

    stats["selected"] = len(selected)
    return selected, stats


def final_pair_for_case(
    results_root: Path,
    geo_name: str,
    case_name: str,
) -> tuple[Path, Path, Path]:
    case_dir = results_root / geo_name / case_name
    final_case_file = case_dir / f"{geo_name}_{case_name}_final.cas.h5"
    final_data_file = case_dir / f"{geo_name}_{case_name}_final.dat.h5"
    return case_dir, final_case_file, final_data_file


def attempt_pair_for_case(
    case_dir: Path,
    geo_name: str,
    case_name: str,
    timestamp: str,
) -> tuple[Path, Path, Path]:
    attempt_dir = (
        case_dir / "post" / "solver_rerun" / "attempts" / timestamp
    ).resolve()
    attempt_case_file = attempt_dir / f"{geo_name}_{case_name}_rerun_attempt.cas.h5"
    attempt_data_file = attempt_dir / f"{geo_name}_{case_name}_rerun_attempt.dat.h5"
    return attempt_dir, attempt_case_file, attempt_data_file


def build_plan_rows(
    candidates: list[dict[str, str]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    timestamp = now_stamp()
    plan_rows: list[dict[str, str]] = []

    for candidate in candidates:
        geo_name = candidate["geo_name"]
        case_name = candidate["case_name"]
        case_dir, final_case_file, final_data_file = final_pair_for_case(
            args.results_root,
            geo_name,
            case_name,
        )
        case_dir_exists = case_dir.is_dir()
        final_case_exists = final_case_file.is_file()
        final_data_exists = final_data_file.is_file()
        final_pair_exists = final_case_exists and final_data_exists
        planned_backup_dir = (
            case_dir / "post" / "solver_rerun" / "backups" / timestamp
        )
        attempt_dir, attempt_case_file, attempt_data_file = attempt_pair_for_case(
            case_dir,
            geo_name,
            case_name,
            timestamp,
        )

        plan_rows.append(
            {
                "selected_index": candidate["selected_index"],
                "source_row_number": candidate["source_row_number"],
                "geo_name": geo_name,
                "case_name": case_name,
                "convergence_status_before": candidate[
                    "convergence_status_before"
                ],
                "case_status_before": candidate["case_status_before"],
                "case_dir": str(case_dir),
                "final_case_file": str(final_case_file),
                "final_data_file": str(final_data_file),
                "planned_backup_dir": str(planned_backup_dir),
                "attempt_dir": str(attempt_dir),
                "attempt_case_file": str(attempt_case_file),
                "attempt_data_file": str(attempt_data_file),
                "matrix_case_name": "true",
                "final_pair_exists": str(final_pair_exists).lower(),
                "case_dir_exists": str(case_dir_exists).lower(),
                "final_case_exists": str(final_case_exists).lower(),
                "final_data_exists": str(final_data_exists).lower(),
            }
        )

    return plan_rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def print_plan(plan_rows: list[dict[str, str]], stats: dict[str, int]) -> None:
    print("\nSolver rerun plan")
    print("=" * 72)
    print(f"Input rows: {stats['input_rows']}")
    print(f"Current-matrix rows before start/limit: {stats['before_start_limit']}")
    print(f"Selected rows: {stats['selected']}")
    print(f"Skipped non-matrix case names: {stats['non_matrix_case_name']}")
    print(f"Skipped missing required columns/values: {stats['missing_required']}")

    if not plan_rows:
        print("No cases selected.")
        return

    print("\nSelected cases:")
    for row in plan_rows:
        print(
            "  "
            f"{row['selected_index']}. {row['geo_name']}/{row['case_name']} "
            f"case_dir_exists={row['case_dir_exists']} "
            f"final_pair_exists={row['final_pair_exists']} "
            f"convergence={row['convergence_status_before'] or 'UNKNOWN'}"
        )
        print(f"     case_dir={row['case_dir']}")
        print(f"     final_case_file={row['final_case_file']}")
        print(f"     final_data_file={row['final_data_file']}")
        print(f"     attempt_case_file={row.get('attempt_case_file', '')}")


def load_prior_successes(results_csv: Path) -> set[tuple[str, str]]:
    successes: set[tuple[str, str]] = set()
    if not results_csv.is_file():
        return successes

    try:
        with results_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get("rerun_status") in {
                    "SUCCESS",
                    "SUCCESS_PROMOTED",
                    "SUCCESS_ATTEMPT_ONLY",
                }:
                    geo_name = row.get("geo_name", "")
                    case_name = row.get("case_name", "")
                    if geo_name and case_name:
                        successes.add((geo_name, case_name))
    except Exception as exc:
        print(f"Warning: could not read prior results for skip-success: {exc}")

    return successes


@contextmanager
def case_log(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        print("\n" + "=" * 72, file=handle)
        print(f"Log opened: {datetime.now().isoformat(timespec='seconds')}", file=handle)
        print("=" * 72, file=handle)
        tee_stdout = Tee(sys.stdout, handle)
        tee_stderr = Tee(sys.stderr, handle)
        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            yield


def make_base_result(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    geo_name = row["geo_name"]
    case_name = row["case_name"]
    _, final_case_file, final_data_file = final_pair_for_case(
        args.results_root,
        geo_name,
        case_name,
    )
    log_file = args.logs_dir / f"{geo_name}__{case_name}__solver_rerun.log"
    case_dir = final_case_file.parent

    return {
        "selected_index": row["selected_index"],
        "source_row_number": row["source_row_number"],
        "geo_name": geo_name,
        "case_name": case_name,
        "convergence_status_before": row["convergence_status_before"],
        "case_status_before": row["case_status_before"],
        "final_case_file": str(final_case_file),
        "final_data_file": str(final_data_file),
        "backup_dir": "",
        "rerun_status": "",
        "returncode_or_exception": "",
        "iterations_requested": str(args.additional_iterations),
        "runtime_seconds": "",
        "output_case_file": str(final_case_file),
        "output_data_file": str(final_data_file),
        "report_extraction_status": "",
        "error_summary": "",
        "suggested_next_action": "",
        "log_file": str(log_file),
        "solver_strategy": args.solver_strategy,
        "promote_on_success": str(args.promote_on_success).lower(),
        "attempt_dir": "",
        "attempt_case_file": "",
        "attempt_data_file": "",
        "promoted_to_final": "false",
        "launcher_profile": args.launcher_profile,
        "launch_mode": args.launch_mode,
        "launch_mode_used": "",
        "launch_working_dir": str(case_dir.resolve()),
        "launch_case_dir_resolved": str(case_dir.resolve()),
        "launch_exception_type": "",
        "launch_exception_message": "",
        "processor_count_requested": str(args.processor_count),
        "processor_count_used": "",
        "processor_count": str(args.processor_count),
        "start_timeout": str(args.start_timeout),
        "fallback_used": "false",
        "meshing_to_solver_used": "false",
        "launch_kwargs": "",
        "failure_stage": "",
        "strategy_stage_status": "",
        "convergence_assessment": "",
        "monitor_window": str(args.monitor_window),
        "monitor_rel_tol": str(args.monitor_rel_tol),
        "residual_growth_limit": str(args.residual_growth_limit),
        "product_version": args.product_version,
    }


def create_backup(
    case_dir: Path,
    final_case_file: Path,
    final_data_file: Path,
    timestamp: str,
) -> Path:
    case_dir = case_dir.resolve()
    final_case_file = final_case_file.resolve()
    final_data_file = final_data_file.resolve()
    backup_dir = (case_dir / "post" / "solver_rerun" / "backups" / timestamp).resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(final_case_file, backup_dir / final_case_file.name)
    shutil.copy2(final_data_file, backup_dir / final_data_file.name)
    return backup_dir


def promote_attempt_to_final(
    case_dir: Path,
    final_case_file: Path,
    final_data_file: Path,
    attempt_case_file: Path,
    attempt_data_file: Path,
    timestamp: str,
) -> Path:
    backup_dir = create_backup(
        case_dir=case_dir,
        final_case_file=final_case_file,
        final_data_file=final_data_file,
        timestamp=timestamp,
    )
    shutil.copy2(attempt_case_file.resolve(), final_case_file.resolve())
    shutil.copy2(attempt_data_file.resolve(), final_data_file.resolve())
    return backup_dir


def require_windows_for_live_run() -> None:
    if platform.system() == "Windows":
        return

    raise RuntimeError(
        "Live solver reruns are blocked on this host. Run without --dry-run only "
        "from the Windows Fluent/PyFluent server."
    )


def set_residual_targets(
    solution: Any,
    residual_target: float,
    species_name: str,
) -> None:
    print("\nApplying residual convergence settings.")
    try:
        residual_equations_state = solution.monitor.residual.equations.get_state()
    except Exception as exc:
        print(f"Could not read residual equation state: {exc}")
        return

    available = list(residual_equations_state.keys())
    targets = ["continuity", "x-velocity", "y-velocity", "z-velocity", species_name]
    print(f"Available residual equations: {available}")
    print(f"Target residual equations: {targets}")

    for equation_name in targets:
        if equation_name not in available:
            print(f"Residual equation not found, skipping: {equation_name}")
            continue

        try:
            equation = solution.monitor.residual.equations[equation_name]
            before = equation.get_state()
            print(f"Residual before {equation_name}: {before}")
            equation.monitor = True
            equation.check_convergence = True
            current_state = equation.get_state()
            if "absolute_criteria" in current_state:
                equation.absolute_criteria = residual_target
            elif "relative_criteria" in current_state:
                equation.relative_criteria = residual_target
            else:
                print(
                    f"No residual criteria field found for {equation_name}; "
                    f"state={current_state}"
                )
                continue
            print(f"Residual after {equation_name}: {equation.get_state()}")
        except Exception as exc:
            print(f"Could not update residual {equation_name}: {exc}")


def apply_relaxation_profile(solution: Any, profile: str) -> None:
    if profile == "baseline":
        print("\nRelaxation profile baseline: leaving relaxation controls unchanged.")
        return

    print("\nApplying conservative relaxation profile where supported.")
    conservative_values = {
        "pressure": 0.2,
        "momentum": 0.3,
        "density": 0.8,
        "body-force": 0.8,
        "nacl": 0.5,
        "species": 0.5,
    }

    try:
        equations = solution.controls.equations
        state = equations.get_state()
    except Exception as exc:
        print(f"Could not inspect relaxation controls; leaving unchanged: {exc}")
        return

    if not isinstance(state, dict):
        print(f"Unexpected relaxation controls state type: {type(state).__name__}")
        return

    print(f"Available relaxation controls: {list(state.keys())}")
    for control_name, value in conservative_values.items():
        if control_name not in state:
            continue

        try:
            control = equations[control_name]
            control_state = control.get_state()
            print(f"Relaxation before {control_name}: {control_state}")
            if "under_relaxation_factor" in control_state:
                control.under_relaxation_factor = value
            elif "relaxation_factor" in control_state:
                control.relaxation_factor = value
            else:
                print(
                    f"No supported relaxation factor field for {control_name}; "
                    f"state={control_state}"
                )
                continue
            print(f"Relaxation after {control_name}: {control.get_state()}")
        except Exception as exc:
            print(f"Could not update relaxation control {control_name}: {exc}")


def apply_pressure_velocity_coupling(solver: Any, coupling: str) -> None:
    if coupling == "preserve":
        print("\nPressure-velocity coupling preserve: leaving method unchanged.")
        return

    print(f"\nApplying pressure-velocity coupling where supported: {coupling}")
    solution = solver.settings.solution
    attempts = [
        ("solution.methods.p_v_coupling.flow_scheme", ("methods", "p_v_coupling", "flow_scheme")),
        ("solution.methods.pressure_velocity_coupling.scheme", ("methods", "pressure_velocity_coupling", "scheme")),
    ]

    for label, chain in attempts:
        try:
            target = solution
            for attr in chain[:-1]:
                target = getattr(target, attr)
            before = target.get_state() if hasattr(target, "get_state") else "<no state>"
            print(f"Trying {label}; before={before}")
            setattr(target, chain[-1], coupling)
            after = target.get_state() if hasattr(target, "get_state") else "<no state>"
            print(f"Applied {label}; after={after}")
            return
        except Exception as exc:
            print(f"Could not apply {label}: {exc}")

    print("No supported pressure-velocity coupling setting path was found.")


def apply_pseudo_transient(solver: Any, use_pseudo_transient: bool | None) -> None:
    if use_pseudo_transient is None:
        print("\nPseudo-transient setting preserve: leaving current state unchanged.")
        return

    print(f"\nApplying pseudo-transient={use_pseudo_transient} where supported.")
    solution = solver.settings.solution
    attempts = [
        ("solution.methods.pseudo_time_method.enabled", ("methods", "pseudo_time_method", "enabled")),
        ("solution.methods.pseudo_transient.enabled", ("methods", "pseudo_transient", "enabled")),
    ]

    for label, chain in attempts:
        try:
            target = solution
            for attr in chain[:-1]:
                target = getattr(target, attr)
            before = target.get_state() if hasattr(target, "get_state") else "<no state>"
            print(f"Trying {label}; before={before}")
            setattr(target, chain[-1], use_pseudo_transient)
            after = target.get_state() if hasattr(target, "get_state") else "<no state>"
            print(f"Applied {label}; after={after}")
            return
        except Exception as exc:
            print(f"Could not apply {label}: {exc}")

    print("No supported pseudo-transient setting path was found.")


def apply_continuation_settings(solver: Any, args: argparse.Namespace) -> None:
    solution = solver.settings.solution
    set_residual_targets(
        solution=solution,
        residual_target=args.residual_target,
        species_name=args.species_residual_name,
    )
    apply_pressure_velocity_coupling(
        solver=solver,
        coupling=args.pressure_velocity_coupling,
    )
    apply_pseudo_transient(
        solver=solver,
        use_pseudo_transient=args.use_pseudo_transient,
    )
    apply_relaxation_profile(
        solution=solution,
        profile=args.relaxation_profile,
    )


def list_object_names(named_object: Any) -> list[str]:
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass
    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(str(key) for key in state.keys())
        if isinstance(state, list):
            return [str(item) for item in state]
    except Exception:
        pass
    return []


def first_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for child in value.values():
            found = first_numeric(child)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for child in value:
            found = first_numeric(child)
            if found is not None:
                return found
    return None


def residual_numeric_from_state(state: Any) -> float | None:
    """Extract a current residual-like value without using convergence criteria."""
    if not isinstance(state, dict):
        return None

    for key, value in state.items():
        key_text = str(key).lower()
        if "criteria" in key_text or "criterion" in key_text:
            continue
        if any(token in key_text for token in ("current", "residual", "value")):
            numeric = first_numeric(value)
            if numeric is not None:
                return numeric

    for key, value in state.items():
        key_text = str(key).lower()
        if "criteria" in key_text or "criterion" in key_text:
            continue
        found = residual_numeric_from_state(value)
        if found is not None:
            return found

    return None


def collect_solver_diagnostics(solver: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "solution_methods": {},
        "residual_equations": {},
        "report_definitions": {},
        "errors": [],
    }
    solution = solver.settings.solution

    for label, getter in [
        ("solution.methods", lambda: solution.methods.get_state()),
        ("solution.controls", lambda: solution.controls.get_state()),
        ("residual_equations", lambda: solution.monitor.residual.equations.get_state()),
    ]:
        try:
            diagnostics[
                "residual_equations" if label == "residual_equations" else "solution_methods"
            ][label] = getter()
            print(f"Diagnostic {label}: {diagnostics['residual_equations' if label == 'residual_equations' else 'solution_methods'][label]}")
        except Exception as exc:
            message = f"{label}: {type(exc).__name__}: {exc}"
            diagnostics["errors"].append(message)
            print(f"Could not collect diagnostic {message}")

    try:
        report_defs = solution.report_definitions
        diagnostics["report_definitions"] = {
            "flux": list_object_names(report_defs.flux),
            "surface": list_object_names(report_defs.surface),
            "volume": list_object_names(report_defs.volume),
            "single_valued_expression": list_object_names(
                report_defs.single_valued_expression
            ),
        }
        print(f"Report definition diagnostics: {diagnostics['report_definitions']}")
    except Exception as exc:
        message = f"report_definitions: {type(exc).__name__}: {exc}"
        diagnostics["errors"].append(message)
        print(f"Could not collect diagnostic {message}")

    return diagnostics


def collect_monitor_snapshot(solver: Any) -> dict[str, Any]:
    solution = solver.settings.solution
    snapshot: dict[str, Any] = {
        "residual_state": {},
        "residual_numeric": {},
        "report_values": {},
        "errors": [],
    }

    try:
        residual_state = solution.monitor.residual.equations.get_state()
        snapshot["residual_state"] = residual_state
        if isinstance(residual_state, dict):
            for name, state in residual_state.items():
                numeric = residual_numeric_from_state(state)
                if numeric is not None:
                    snapshot["residual_numeric"][str(name)] = numeric
    except Exception as exc:
        snapshot["errors"].append(f"residual_state: {type(exc).__name__}: {exc}")

    try:
        report_defs = solution.report_definitions
        report_names: list[str] = []
        for group in [
            report_defs.flux,
            report_defs.surface,
            report_defs.volume,
            report_defs.single_valued_expression,
        ]:
            report_names.extend(list_object_names(group))
        for report_name in report_names:
            if not (
                "lmh" in report_name.lower()
                or "mass" in report_name.lower()
                or report_name.lower() in {"m_in", "m_out", "pp_m_in", "pp_m_out"}
            ):
                continue
            try:
                result = report_defs.compute(report_defs=[report_name])
                numeric = first_numeric(result)
                if numeric is not None:
                    snapshot["report_values"][report_name] = numeric
            except Exception as exc:
                snapshot["errors"].append(
                    f"report {report_name}: {type(exc).__name__}: {exc}"
                )
    except Exception as exc:
        snapshot["errors"].append(f"report_values: {type(exc).__name__}: {exc}")

    return snapshot


def execute_tui_best_effort(solver: Any, label: str, commands: list[str]) -> list[str]:
    errors: list[str] = []
    for command in commands:
        try:
            print(f"Trying TUI fallback for {label}: {command}")
            solver.execute_tui(command)
            print(f"TUI fallback succeeded for {label}: {command}")
            return errors
        except Exception as exc:
            message = f"{command}: {type(exc).__name__}: {exc}"
            errors.append(message)
            print(f"TUI fallback failed for {label}: {message}")
    return errors


def apply_conservative_under_relaxation(solver: Any) -> dict[str, Any]:
    print("\nApplying damped steady strategy settings.")
    result: dict[str, Any] = {"status": "ATTEMPTED", "errors": []}
    try:
        apply_relaxation_profile(solver.settings.solution, "conservative")
    except Exception as exc:
        result["errors"].append(f"settings relaxation: {type(exc).__name__}: {exc}")

    tui_commands = [
        "/solve/set/under-relaxation pressure 0.2",
        "/solve/set/under-relaxation momentum 0.3",
        "/solve/set/under-relaxation species 0.5",
    ]
    result["errors"].extend(
        execute_tui_best_effort(solver, "under-relaxation", tui_commands)
    )
    return result


def apply_pseudo_transient_ramp(solver: Any, args: argparse.Namespace) -> dict[str, Any]:
    print("\nApplying pseudo-transient ramp strategy settings.")
    result: dict[str, Any] = {"status": "ATTEMPTED", "errors": []}
    try:
        apply_pseudo_transient(solver, True)
    except Exception as exc:
        result["errors"].append(f"settings pseudo transient: {type(exc).__name__}: {exc}")

    tui_commands = [
        "/solve/set/pseudo-transient yes",
        "/solve/set/pseudo-time-method yes",
        "/solve/set/pseudo-transient-method yes",
    ]
    result["errors"].extend(
        execute_tui_best_effort(solver, "pseudo-transient", tui_commands)
    )
    apply_conservative_under_relaxation(solver)
    return result


def apply_first_order_ramp(solver: Any) -> dict[str, Any]:
    print("\nApplying first-order ramp strategy settings.")
    result: dict[str, Any] = {
        "status": "ATTEMPTED",
        "restore_reliable": False,
        "original_state": {},
        "errors": [],
    }
    solution = solver.settings.solution
    try:
        methods = solution.methods
        if hasattr(methods, "get_state"):
            result["original_state"] = methods.get_state()
            print(f"Original solution methods: {result['original_state']}")
    except Exception as exc:
        result["errors"].append(f"capture methods: {type(exc).__name__}: {exc}")

    tui_commands = [
        "/solve/set/discretization-scheme mom first-order-upwind",
        "/solve/set/discretization-scheme species-0 first-order-upwind",
        "/solve/set/discretization-scheme pressure standard",
    ]
    result["errors"].extend(
        execute_tui_best_effort(solver, "first-order discretization", tui_commands)
    )
    apply_conservative_under_relaxation(solver)
    return result


def restore_first_order_ramp(solver: Any, strategy_state: dict[str, Any]) -> bool:
    original_state = strategy_state.get("original_state")
    if not original_state:
        print("Original discretization state was not captured; restore is not reliable.")
        return False
    try:
        solver.settings.solution.methods.set_state(original_state)
        print("Original solution methods restored from captured state.")
        return True
    except Exception as exc:
        print(f"Could not restore original solution methods: {type(exc).__name__}: {exc}")
        return False


def run_iteration_chunks(
    solver: Any,
    total_iterations: int,
    chunk_size: int,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    remaining = total_iterations
    chunk_index = 0
    while remaining > 0:
        chunk_index += 1
        iter_count = min(chunk_size, remaining)
        print(f"\nRunning iteration chunk {chunk_index}: {iter_count} iterations")
        try:
            solver.settings.solution.run_calculation.iterate(iter_count=iter_count)
        except Exception as exc:
            raise FluentStageError(
                status="FAILED_DIVERGED_DURING_RERUN",
                failure_stage="iterate",
                message=f"Iteration chunk {chunk_index} failed: {exc}",
                original_exception=exc,
            ) from exc
        snapshot = collect_monitor_snapshot(solver)
        snapshot["chunk_index"] = chunk_index
        snapshot["iterations_completed_in_chunk"] = iter_count
        history.append(snapshot)
        remaining -= iter_count
        print(
            f"Chunk {chunk_index} monitor snapshot: residual_keys="
            f"{list(snapshot.get('residual_numeric', {}).keys())}, report_keys="
            f"{list(snapshot.get('report_values', {}).keys())}"
        )
    return history


def assess_history(
    history: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    assessment: dict[str, Any] = {
        "status": "MONITORS_UNAVAILABLE",
        "diverged": False,
        "stable": False,
        "bounded_not_converged": False,
        "details": "",
    }
    series: dict[str, list[float]] = {}
    for snapshot in history:
        for group_name in ("residual_numeric", "report_values"):
            values = snapshot.get(group_name, {})
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    series.setdefault(f"{group_name}:{name}", []).append(float(value))

    if not series:
        assessment["details"] = "No numeric residual/report monitor values were available."
        return assessment

    worst_growth = 0.0
    max_rel_variation = 0.0
    for name, values in series.items():
        if len(values) < 2:
            continue
        first = abs(values[0])
        last = abs(values[-1])
        if first > 0.0:
            worst_growth = max(worst_growth, last / first)
        window_values = values[-max(2, min(len(values), args.monitor_window)) :]
        mean_abs = sum(abs(v) for v in window_values) / len(window_values)
        if mean_abs > 0.0:
            rel_variation = (max(window_values) - min(window_values)) / mean_abs
            max_rel_variation = max(max_rel_variation, abs(rel_variation))
        print(f"Assessment series {name}: first={values[0]} last={values[-1]}")

    assessment["worst_growth"] = worst_growth
    assessment["max_rel_variation"] = max_rel_variation

    if worst_growth > args.residual_growth_limit:
        assessment["status"] = "DIVERGED"
        assessment["diverged"] = True
        assessment["details"] = (
            f"Residual/report growth {worst_growth:.3g} exceeded limit "
            f"{args.residual_growth_limit:.3g}."
        )
    elif max_rel_variation <= args.monitor_rel_tol:
        assessment["status"] = "STABLE"
        assessment["stable"] = True
        assessment["details"] = (
            f"Last-window relative variation {max_rel_variation:.3g} is within "
            f"{args.monitor_rel_tol:.3g}."
        )
    else:
        assessment["status"] = "BOUNDED_NOT_CONVERGED"
        assessment["bounded_not_converged"] = True
        assessment["details"] = (
            f"Monitor variation {max_rel_variation:.3g} remains above "
            f"{args.monitor_rel_tol:.3g}; bounded but not converged."
        )
    return assessment


def execute_solver_strategy(
    solver: Any,
    args: argparse.Namespace,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "strategy_stage_status": "NOT_STARTED",
        "convergence_assessment": "",
        "diagnostics": {},
        "history": [],
        "assessment": {},
    }
    diagnostics = collect_solver_diagnostics(solver)
    result["diagnostics"] = diagnostics

    if args.solver_strategy == "diagnose_only":
        result["strategy_stage_status"] = "DIAGNOSE_ONLY_COMPLETED"
        result["convergence_assessment"] = "DIAGNOSE_ONLY_NO_ITERATION"
        return result

    try:
        apply_continuation_settings(solver=solver, args=args)
        if args.solver_strategy == "damped_steady":
            result["strategy_settings"] = apply_conservative_under_relaxation(solver)
        elif args.solver_strategy == "pseudo_transient_ramp":
            result["strategy_settings"] = apply_pseudo_transient_ramp(solver, args)
        elif args.solver_strategy == "first_order_ramp":
            result["strategy_settings"] = apply_first_order_ramp(solver)
        else:
            result["strategy_settings"] = {"status": "CONTINUE_ONLY"}
    except Exception as exc:
        raise FluentStageError(
            status="FAILED_APPLY_STRATEGY",
            failure_stage="apply_strategy",
            message=f"Could not apply solver strategy {args.solver_strategy}: {exc}",
            original_exception=exc,
        ) from exc

    history = run_iteration_chunks(
        solver=solver,
        total_iterations=args.additional_iterations,
        chunk_size=args.iteration_chunk_size,
    )
    result["history"] = history

    if args.solver_strategy == "first_order_ramp":
        restored = restore_first_order_ramp(solver, result.get("strategy_settings", {}))
        if not restored:
            result["strategy_stage_status"] = "NEEDS_MANUAL_REVIEW"
            result["convergence_assessment"] = (
                "First-order ramp completed, but original discretization restore "
                "was not verified."
            )
            return result

    assessment = assess_history(history, args)
    result["assessment"] = assessment
    result["convergence_assessment"] = json.dumps(assessment, sort_keys=True)

    if assessment.get("diverged"):
        raise FluentStageError(
            status="FAILED_DIVERGED_DURING_RERUN",
            failure_stage="iterate",
            message=str(assessment.get("details", "Residual/report monitors diverged.")),
        )

    if assessment.get("stable"):
        result["strategy_stage_status"] = "STABLE"
    elif assessment.get("bounded_not_converged"):
        result["strategy_stage_status"] = "NEEDS_TRANSIENT_REVIEW"
    else:
        result["strategy_stage_status"] = "COMPLETED_NEEDS_REVIEW"
    return result


class FluentLaunchError(RuntimeError):
    """Raised when Fluent cannot be launched or switched into solver mode."""

    def __init__(
        self,
        message: str,
        original_exception: BaseException,
        failure_stage: str = "launch",
    ) -> None:
        super().__init__(message)
        self.original_exception = original_exception
        self.exception_type = type(original_exception).__name__
        self.exception_message = str(original_exception)
        self.failure_stage = failure_stage


class FluentStageError(RuntimeError):
    """Raised for non-launch Fluent stages with explicit status classification."""

    def __init__(
        self,
        status: str,
        failure_stage: str,
        message: str,
        original_exception: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.failure_stage = failure_stage
        self.original_exception = original_exception
        self.exception_type = (
            type(original_exception).__name__ if original_exception is not None else ""
        )
        self.exception_message = str(original_exception) if original_exception else message


def deadline_exceeded(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "deadline exceeded" in text


def effective_primary_launch_mode(args: argparse.Namespace) -> str:
    if args.launcher_profile == "known_good_postprocess":
        return "meshing_to_solver"
    if args.launcher_profile == "solver_direct":
        return "solver"
    return args.launch_mode


def make_launch_kwargs(
    args: argparse.Namespace,
    launch_mode: str,
    launch_working_dir: Path,
    processor_count: int,
) -> dict[str, Any]:
    launch_kwargs: dict[str, Any] = {
        "product_version": args.product_version,
        "mode": "solver" if launch_mode == "solver" else "meshing",
        "dimension": 3,
        "precision": "double",
        "processor_count": processor_count,
        "ui_mode": args.ui_mode,
        "start_timeout": args.start_timeout,
        "cwd": fluent_path(launch_working_dir),
    }
    if args.graphics_driver:
        launch_kwargs["graphics_driver"] = args.graphics_driver
    return launch_kwargs


def launch_solver_session(
    pyfluent: Any,
    args: argparse.Namespace,
    launch_working_dir: Path,
    metadata: dict[str, Any],
) -> Any:
    primary_mode = effective_primary_launch_mode(args)
    attempts: list[tuple[str, int, bool]] = [(primary_mode, args.processor_count, False)]
    if (
        args.launcher_profile == "solver_direct"
        and primary_mode == "solver"
        and not args.no_launch_fallback
    ):
        attempts.append(("meshing_to_solver", 4, True))

    last_error: FluentLaunchError | None = None

    for launch_mode, processor_count, is_fallback in attempts:
        launch_kwargs = make_launch_kwargs(
            args=args,
            launch_mode=launch_mode,
            launch_working_dir=launch_working_dir,
            processor_count=processor_count,
        )
        metadata["launch_mode_used"] = launch_mode
        metadata["processor_count_used"] = str(processor_count)
        metadata["fallback_used"] = str(is_fallback).lower()
        metadata["meshing_to_solver_used"] = str(launch_mode == "meshing_to_solver").lower()
        metadata["launch_kwargs"] = json.dumps(launch_kwargs, sort_keys=True)

        print(f"\nLaunch attempt: mode={launch_mode} fallback={is_fallback}")
        print(f"launch kwargs={launch_kwargs}")

        meshing = None
        try:
            with pushd(launch_working_dir):
                print(f"cwd inside launch context={Path.cwd()}")
                launched = pyfluent.launch_fluent(**launch_kwargs)

                if launch_mode == "meshing_to_solver":
                    meshing = launched
                    print("Switching to solver...")
                    try:
                        solver = meshing.switch_to_solver()
                    except Exception as exc:
                        raise FluentLaunchError(
                            f"Fluent switch_to_solver failed: {exc}",
                            exc,
                            failure_stage="switch_to_solver",
                        ) from exc
                    meshing = None
                    print("Solver ready.")
                    return solver

                print("Solver ready.")
                return launched
        except FluentLaunchError as exc:
            last_error = exc
            if meshing is not None:
                try:
                    meshing.exit()
                except Exception as cleanup_error:
                    print(f"Warning: could not exit failed meshing session: {cleanup_error}")
            if exc.failure_stage == "launch" and deadline_exceeded(exc) and not is_fallback:
                print("Launch Deadline Exceeded; trying configured fallback if available.")
                continue
            raise
        except Exception as exc:
            last_error = FluentLaunchError(
                f"Fluent launch failed during {launch_mode} startup: {exc}",
                exc,
                failure_stage="launch",
            )
            if meshing is not None:
                try:
                    meshing.exit()
                except Exception as cleanup_error:
                    print(f"Warning: could not exit failed meshing session: {cleanup_error}")
            if deadline_exceeded(exc) and not is_fallback:
                print("Launch Deadline Exceeded; trying configured fallback if available.")
                continue
            raise last_error from exc

    if last_error is not None:
        raise last_error
    raise RuntimeError("No Fluent launch attempts were configured.")


def run_live_solver_rerun(
    case_dir: Path,
    final_case_file: Path,
    final_data_file: Path,
    attempt_case_file: Path,
    attempt_data_file: Path,
    args: argparse.Namespace,
    log_path: Path,
) -> dict[str, Any]:
    require_windows_for_live_run()

    import ansys.fluent.core as pyfluent  # Imported only for live non-dry-run execution.

    pyfluent.config.check_health_timeout = args.fluent_health_timeout

    case_dir = resolve_existing_dir(case_dir, "case_dir")
    final_case_file = resolve_existing_file(final_case_file, "final case file")
    final_data_file = resolve_existing_file(final_data_file, "final data file")
    attempt_case_file = attempt_case_file.resolve()
    attempt_data_file = attempt_data_file.resolve()
    log_path = log_path.resolve()
    launch_working_dir = case_dir
    transcript_path = log_path.with_name(log_path.stem + "__fluent.trn").resolve()

    solver = None
    transcript_is_running = False
    result: dict[str, Any] = {
        "rerun_status": "",
        "strategy_stage_status": "",
        "convergence_assessment": "",
        "attempt_saved": False,
        "diagnostics": {},
        "history": [],
        "assessment": {},
        "launch_metadata": {},
    }

    try:
        print("\nLaunching Fluent for solver rerun.")
        print(f"cwd before launch={Path.cwd()}")
        print(f"product_version={args.product_version}")
        print(f"launcher_profile={args.launcher_profile}")
        print(f"launch_mode={args.launch_mode}")
        print(f"primary_launch_mode={effective_primary_launch_mode(args)}")
        print(f"processor_count_requested={args.processor_count}")
        print(f"launch_working_dir={launch_working_dir}")
        print(f"resolved_case_dir={case_dir}")
        print(f"resolved_final_case_file={final_case_file}")
        print(f"resolved_final_data_file={final_data_file}")
        print(f"attempt_case_file={attempt_case_file}")
        print(f"attempt_data_file={attempt_data_file}")
        print(f"solver_strategy={args.solver_strategy}")
        print(f"promote_on_success={args.promote_on_success}")
        print(f"additional_iterations={args.additional_iterations}")

        launch_metadata: dict[str, Any] = {}
        solver = launch_solver_session(
            pyfluent=pyfluent,
            args=args,
            launch_working_dir=launch_working_dir,
            metadata=launch_metadata,
        )
        result["launch_metadata"] = launch_metadata

        if args.write_transcript:
            print(f"Starting Fluent transcript: {transcript_path}")
            solver.transcript.start(file_name=fluent_path(transcript_path))
            transcript_is_running = True

        print(f"Reading final case/data: {final_case_file}")
        try:
            solver.settings.file.read_case_data(file_name=fluent_path(final_case_file))
        except Exception as exc:
            raise FluentStageError(
                status="FAILED_READ_CASE_DATA",
                failure_stage="read_case",
                message=f"Could not read final case/data: {exc}",
                original_exception=exc,
            ) from exc
        print("Final case/data loaded. Solution will not be reinitialized.")

        strategy_result = execute_solver_strategy(solver=solver, args=args)
        result.update(strategy_result)

        if args.solver_strategy == "diagnose_only":
            result["rerun_status"] = "COMPLETED_NEEDS_REVIEW"
            return result

        if strategy_result.get("strategy_stage_status") == "NEEDS_TRANSIENT_REVIEW":
            result["rerun_status"] = "NEEDS_TRANSIENT_REVIEW"
        elif strategy_result.get("strategy_stage_status") == "COMPLETED_NEEDS_REVIEW":
            result["rerun_status"] = "COMPLETED_NEEDS_REVIEW"
        elif strategy_result.get("strategy_stage_status") == "NEEDS_MANUAL_REVIEW":
            result["rerun_status"] = "NEEDS_MANUAL_REVIEW"
        elif strategy_result.get("strategy_stage_status") == "STABLE":
            result["rerun_status"] = (
                "SUCCESS_ATTEMPT_ONLY"
                if not args.promote_on_success
                else "SUCCESS_READY_TO_PROMOTE"
            )
        else:
            result["rerun_status"] = "COMPLETED_NEEDS_REVIEW"

        attempt_case_file.parent.mkdir(parents=True, exist_ok=True)
        print(f"Writing staged attempt case/data: {attempt_case_file}")
        try:
            solver.settings.file.write_case_data(file_name=fluent_path(attempt_case_file))
        except Exception as exc:
            raise FluentStageError(
                status="FAILED_SAVE_ATTEMPT",
                failure_stage="save",
                message=f"Could not save staged attempt case/data: {exc}",
                original_exception=exc,
            ) from exc

        if not attempt_case_file.is_file() or not attempt_data_file.is_file():
            raise FluentStageError(
                status="FAILED_SAVE_ATTEMPT",
                failure_stage="save",
                message=(
                    "Staged write finished but attempt case/data pair is missing: "
                    f"{attempt_case_file}; {attempt_data_file}"
                ),
            )

        result["attempt_saved"] = True
        print("Staged attempt case/data write completed.")
        return result

    finally:
        if solver is not None and transcript_is_running:
            try:
                solver.transcript.stop()
                transcript_is_running = False
            except Exception as cleanup_error:
                print(f"Warning: could not stop Fluent transcript: {cleanup_error}")

        if solver is not None:
            try:
                solver.exit()
            except Exception as cleanup_error:
                print(f"Warning: could not exit solver session: {cleanup_error}")


def report_worker_has_safe_direct_cli(report_script: Path) -> tuple[bool, str]:
    if not report_script.is_file():
        return False, f"report script not found: {report_script}"

    try:
        text = report_script.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return False, f"could not inspect report script: {exc}"

    has_argparse = "argparse" in text
    has_main_guard = "if __name__" in text and "__main__" in text
    has_geo_case_flags = "--geo-name" in text and "--case-name" in text

    if has_argparse and has_main_guard and has_geo_case_flags:
        return True, "direct argparse CLI detected"

    return (
        False,
        "direct --geo-name/--case-name argparse CLI not detected; report extraction deferred",
    )


def maybe_run_report_after_success(
    record: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str]:
    if not args.run_report_after_success:
        return (
            "SKIPPED_BY_OPTION",
            "Run report extraction later after solver rerun inventory refresh.",
        )

    if not args.report_cli_safe:
        return (
            "DEFERRED_NO_SAFE_CLI",
            "Report worker has no safe direct CLI; run report extraction later.",
        )

    cmd = [
        sys.executable,
        str(args.report_script),
        "--geo-name",
        record["geo_name"],
        "--case-name",
        record["case_name"],
    ]
    print(f"Running report extraction: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode == 0:
        return ("SUCCESS", "")

    return (
        f"FAILED_RETURN_CODE_{result.returncode}",
        "Report extraction failed; rerun report extraction manually after checking its log.",
    )


def short_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def process_case(
    candidate: dict[str, str],
    args: argparse.Namespace,
    prior_successes: set[tuple[str, str]],
    run_timestamp: str,
) -> dict[str, Any]:
    record = make_base_result(candidate, args)
    geo_name = record["geo_name"]
    case_name = record["case_name"]
    case_dir, final_case_file, final_data_file = final_pair_for_case(
        args.results_root,
        geo_name,
        case_name,
    )
    case_dir_resolved = case_dir.resolve()
    final_case_file_resolved = final_case_file.resolve()
    final_data_file_resolved = final_data_file.resolve()
    attempt_dir, attempt_case_file, attempt_data_file = attempt_pair_for_case(
        case_dir_resolved,
        geo_name,
        case_name,
        run_timestamp,
    )
    launch_working_dir = case_dir_resolved
    log_path = Path(record["log_file"]).resolve()
    record["final_case_file"] = str(final_case_file_resolved)
    record["final_data_file"] = str(final_data_file_resolved)
    record["output_case_file"] = str(attempt_case_file)
    record["output_data_file"] = str(attempt_data_file)
    record["attempt_dir"] = str(attempt_dir)
    record["attempt_case_file"] = str(attempt_case_file)
    record["attempt_data_file"] = str(attempt_data_file)
    record["log_file"] = str(log_path)
    record["launch_working_dir"] = str(launch_working_dir)
    record["launch_case_dir_resolved"] = str(case_dir_resolved)
    record["launch_mode_used"] = effective_primary_launch_mode(args)
    record["processor_count_used"] = str(args.processor_count)
    record["meshing_to_solver_used"] = str(
        effective_primary_launch_mode(args) == "meshing_to_solver"
    ).lower()
    record["launch_kwargs"] = json.dumps(
        make_launch_kwargs(
            args=args,
            launch_mode=effective_primary_launch_mode(args),
            launch_working_dir=launch_working_dir,
            processor_count=args.processor_count,
        ),
        sort_keys=True,
    )
    start_time = time.monotonic()

    with case_log(log_path):
        print(f"Processing selected case {record['selected_index']}: {geo_name}/{case_name}")
        print(f"Dry run: {args.dry_run}")
        print(f"cwd before launch: {Path.cwd()}")
        print(f"resolved results_root: {args.results_root.resolve()}")
        print(f"resolved case_dir: {case_dir_resolved}")
        print(f"resolved final_case_file: {final_case_file_resolved}")
        print(f"resolved final_data_file: {final_data_file_resolved}")
        print(f"attempt_dir={attempt_dir}")
        print(f"attempt_case_file={attempt_case_file}")
        print(f"attempt_data_file={attempt_data_file}")
        print(f"launcher_profile={args.launcher_profile}")
        print(f"launch_mode={args.launch_mode}")
        print(f"effective_launch_mode={effective_primary_launch_mode(args)}")
        print(f"launch_working_dir={launch_working_dir}")
        print(f"processor_count={args.processor_count}")
        print(f"start_timeout={args.start_timeout}")
        print(f"product_version={args.product_version}")
        print(f"graphics_driver={args.graphics_driver or 'not supplied'}")
        print(f"solver_strategy={args.solver_strategy}")
        print(f"promote_on_success={str(args.promote_on_success).lower()}")
        print(f"additional_iterations={args.additional_iterations}")
        print(f"case_dir exists: {case_dir_resolved.is_dir()}")
        print(f"final case exists: {final_case_file_resolved.is_file()}")
        print(f"final data exists: {final_data_file_resolved.is_file()}")

        try:
            if (
                args.skip_existing_rerun_success
                and not args.force
                and (geo_name, case_name) in prior_successes
            ):
                record["rerun_status"] = "SKIPPED_EXISTING_RERUN_SUCCESS"
                record["returncode_or_exception"] = "0"
                record["suggested_next_action"] = (
                    "Use --force to rerun despite the previous SUCCESS record."
                )
                return record

            case_dir_exists = case_dir_resolved.is_dir()
            final_pair_exists = (
                final_case_file_resolved.is_file()
                and final_data_file_resolved.is_file()
            )

            if args.dry_run:
                record["rerun_status"] = "DRY_RUN"
                record["returncode_or_exception"] = "0"
                record["report_extraction_status"] = "SKIPPED_DRY_RUN"
                if not final_pair_exists:
                    record["error_summary"] = "Final case/data pair is missing in dry-run precheck."
                    record["suggested_next_action"] = (
                        "Verify the final case/data files on the Windows server before live rerun."
                    )
                else:
                    record["suggested_next_action"] = "Run without --dry-run on the Windows server."
                return record

            if not case_dir_exists:
                record["rerun_status"] = "FAILED_INVALID_CASE_DIR"
                record["returncode_or_exception"] = "invalid case directory"
                record["error_summary"] = (
                    f"Case directory is not an existing directory: {case_dir_resolved}"
                )
                record["suggested_next_action"] = (
                    "Verify --results-root, --geo-name, and --case-name on the Windows server."
                )
                return record

            if not final_pair_exists:
                record["rerun_status"] = "FAILED_MISSING_FINAL_PAIR"
                record["returncode_or_exception"] = "missing final case/data pair"
                missing = [
                    str(path)
                    for path in (final_case_file_resolved, final_data_file_resolved)
                    if not path.is_file()
                ]
                record["error_summary"] = "Missing: " + "; ".join(missing)
                record["suggested_next_action"] = (
                    "Restore or regenerate the final case/data pair before rerunning."
                )
                return record

            try:
                require_windows_for_live_run()
            except Exception as exc:
                record["rerun_status"] = "FAILED_UNSAFE_HOST"
                record["returncode_or_exception"] = short_exception(exc)
                record["error_summary"] = short_exception(exc)
                record["suggested_next_action"] = (
                    "Run the live rerun from the Windows Fluent/PyFluent server."
                )
                return record

            try:
                live_result = run_live_solver_rerun(
                    case_dir=case_dir_resolved,
                    final_case_file=final_case_file_resolved,
                    final_data_file=final_data_file_resolved,
                    attempt_case_file=attempt_case_file,
                    attempt_data_file=attempt_data_file,
                    args=args,
                    log_path=log_path,
                )
                launch_metadata = live_result.get("launch_metadata", {})
                for key in [
                    "launch_mode_used",
                    "processor_count_used",
                    "fallback_used",
                    "meshing_to_solver_used",
                    "launch_kwargs",
                ]:
                    if key in launch_metadata:
                        record[key] = str(launch_metadata[key])
                record["strategy_stage_status"] = str(
                    live_result.get("strategy_stage_status", "")
                )
                record["convergence_assessment"] = str(
                    live_result.get("convergence_assessment", "")
                )
                record["diagnostics_json"] = json.dumps(
                    live_result.get("diagnostics", {}),
                    default=str,
                    sort_keys=True,
                )
                record["monitor_history_json"] = json.dumps(
                    live_result.get("history", []),
                    default=str,
                    sort_keys=True,
                )
            except FluentLaunchError as exc:
                record["rerun_status"] = (
                    "FAILED_SWITCH_TO_SOLVER"
                    if exc.failure_stage == "switch_to_solver"
                    else "FAILED_LAUNCH"
                )
                record["returncode_or_exception"] = short_exception(exc)
                record["launch_exception_type"] = exc.exception_type
                record["launch_exception_message"] = exc.exception_message
                record["failure_stage"] = exc.failure_stage
                record["error_summary"] = short_exception(exc)
                record["suggested_next_action"] = (
                    "Inspect the launch path, Fluent installation, and PyFluent startup log."
                )
                traceback.print_exc()
                return record
            except FluentStageError as exc:
                record["rerun_status"] = exc.status
                record["returncode_or_exception"] = short_exception(exc)
                record["launch_exception_type"] = exc.exception_type
                record["launch_exception_message"] = exc.exception_message
                record["failure_stage"] = exc.failure_stage
                record["error_summary"] = short_exception(exc)
                if exc.status == "FAILED_DIVERGED_DURING_RERUN":
                    record["suggested_next_action"] = (
                        "Treat this as a steady-solver stability problem; review "
                        "pseudo-transient or transient strategy manually."
                    )
                else:
                    record["suggested_next_action"] = (
                        "Inspect the per-case solver rerun log for the failed stage."
                    )
                traceback.print_exc()
                return record
            except Exception as exc:
                record["rerun_status"] = "FAILED_EXCEPTION"
                record["returncode_or_exception"] = short_exception(exc)
                record["failure_stage"] = "unknown"
                record["error_summary"] = short_exception(exc)
                record["suggested_next_action"] = (
                    "Inspect the per-case solver rerun log and Fluent transcript if present."
                )
                traceback.print_exc()
                return record

            live_status = str(live_result.get("rerun_status", "COMPLETED_NEEDS_REVIEW"))
            record["rerun_status"] = live_status

            if live_status == "SUCCESS_READY_TO_PROMOTE":
                try:
                    backup_dir = promote_attempt_to_final(
                        case_dir=case_dir_resolved,
                        final_case_file=final_case_file_resolved,
                        final_data_file=final_data_file_resolved,
                        attempt_case_file=attempt_case_file,
                        attempt_data_file=attempt_data_file,
                        timestamp=run_timestamp,
                    )
                    record["backup_dir"] = str(backup_dir)
                    record["promoted_to_final"] = "true"
                    record["rerun_status"] = "SUCCESS_PROMOTED"
                    print(f"Promoted staged attempt to final after backup: {backup_dir}")
                except Exception as exc:
                    record["rerun_status"] = "FAILED_BACKUP"
                    record["returncode_or_exception"] = short_exception(exc)
                    record["error_summary"] = short_exception(exc)
                    record["suggested_next_action"] = (
                        "Attempt files exist, but promotion backup/copy failed. "
                        "Do not overwrite finals until this is reviewed."
                    )
                    traceback.print_exc()
                    return record

            if record["rerun_status"] == "SUCCESS_READY_TO_PROMOTE":
                record["rerun_status"] = "SUCCESS_ATTEMPT_ONLY"

            if record["rerun_status"] == "SUCCESS_ATTEMPT_ONLY":
                record["suggested_next_action"] = (
                    "Review staged attempt outputs before promotion to final."
                )
            elif record["rerun_status"] == "SUCCESS_PROMOTED":
                record["suggested_next_action"] = (
                    "Refresh inventory and run report/postprocessing as needed."
                )
            elif record["rerun_status"] == "NEEDS_TRANSIENT_REVIEW":
                record["suggested_next_action"] = (
                    "Steady residual/report variation remains bounded but not stable; "
                    "consider transient or pseudo-transient manual review."
                )
            elif record["rerun_status"] == "COMPLETED_NEEDS_REVIEW":
                record["suggested_next_action"] = (
                    "Monitor extraction was incomplete or inconclusive; review staged "
                    "attempt files and logs manually."
                )
            elif record["rerun_status"] == "NEEDS_MANUAL_REVIEW":
                record["suggested_next_action"] = (
                    "Strategy completed but restoration/assessment was not reliable; "
                    "do not promote without manual review."
                )

            record["returncode_or_exception"] = "0"
            if record["rerun_status"].startswith("SUCCESS"):
                report_status, next_action = maybe_run_report_after_success(record, args)
                record["report_extraction_status"] = report_status
                if next_action:
                    record["suggested_next_action"] = next_action
            else:
                record["report_extraction_status"] = "SKIPPED_NOT_SUCCESS"

            if (
                record["rerun_status"].startswith("SUCCESS")
                and not Path(record["attempt_case_file"]).is_file()
            ):
                record["rerun_status"] = "FAILED_SAVE_ATTEMPT"
                record["error_summary"] = "Success status but attempt case file is missing."
                return record
            return record

        finally:
            runtime_seconds = time.monotonic() - start_time
            record["runtime_seconds"] = f"{runtime_seconds:.3f}"
            print(f"Case status: {record['rerun_status'] or 'UNKNOWN'}")
            print(f"Runtime seconds: {record['runtime_seconds']}")


def write_summary(
    path: Path,
    args: argparse.Namespace,
    stats: dict[str, int],
    results: list[dict[str, Any]],
    plan_csv: Path,
    results_csv: Path,
    results_json: Path,
) -> None:
    status_counts: dict[str, int] = {}
    for record in results:
        status = str(record.get("rerun_status", "UNKNOWN") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    lines = [
        "Solver rerun summary",
        "=" * 72,
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"Dry run: {args.dry_run}",
        f"Candidates CSV: {args.candidates_csv}",
        f"Results root: {args.results_root}",
        f"Output dir: {args.output_dir}",
        f"Logs dir: {args.logs_dir}",
        f"Plan CSV: {plan_csv}",
        f"Results CSV: {results_csv}",
        f"Results JSON: {results_json}",
        "",
        "Selection:",
        f"  input_rows: {stats['input_rows']}",
        f"  selected: {stats['selected']}",
        f"  before_start_limit: {stats['before_start_limit']}",
        f"  non_matrix_case_name: {stats['non_matrix_case_name']}",
        f"  missing_required: {stats['missing_required']}",
        f"  filtered_geo_name: {stats['filtered_geo_name']}",
        f"  filtered_case_name: {stats['filtered_case_name']}",
        f"  filtered_convergence_status: {stats['filtered_convergence_status']}",
        "",
        "Solver settings:",
        f"  additional_iterations: {args.additional_iterations}",
        f"  solver_strategy: {args.solver_strategy}",
        f"  iteration_chunk_size: {args.iteration_chunk_size}",
        f"  residual_target: {args.residual_target}",
        f"  monitor_window: {args.monitor_window}",
        f"  monitor_rel_tol: {args.monitor_rel_tol}",
        f"  residual_growth_limit: {args.residual_growth_limit}",
        f"  promote_on_success: {args.promote_on_success}",
        f"  pseudo_transient: {args.use_pseudo_transient if args.use_pseudo_transient is not None else 'preserve'}",
        f"  pressure_velocity_coupling: {args.pressure_velocity_coupling}",
        f"  relaxation_profile: {args.relaxation_profile}",
        f"  write_transcript: {args.write_transcript}",
        f"  launcher_profile: {args.launcher_profile}",
        f"  launch_mode: {args.launch_mode}",
        f"  effective_primary_launch_mode: {effective_primary_launch_mode(args)}",
        f"  processor_count: {args.processor_count}",
        f"  start_timeout: {args.start_timeout}",
        f"  product_version: {args.product_version}",
        f"  graphics_driver: {args.graphics_driver or 'not supplied'}",
        "",
        "Report extraction:",
        f"  requested: {args.run_report_after_success}",
        f"  safe_direct_cli: {args.report_cli_safe}",
        f"  reason: {args.report_cli_reason}",
        "",
        "Status counts:",
    ]

    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  No cases processed.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.logs_dir.mkdir(parents=True, exist_ok=True)

    plan_csv = args.output_dir / "solver_rerun_plan.csv"
    plan_json = args.output_dir / "solver_rerun_plan.json"
    results_csv = args.output_dir / "solver_rerun_results.csv"
    results_json = args.output_dir / "solver_rerun_results.json"
    summary_txt = args.output_dir / "solver_rerun_summary.txt"

    rows, fieldnames = read_candidates(args.candidates_csv)
    candidates, stats = select_candidates(rows, args)
    plan_rows = build_plan_rows(candidates, args)

    write_csv(plan_csv, plan_rows, PLAN_FIELDS)
    write_json(
        plan_json,
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "candidate_columns": fieldnames,
            "selection_stats": stats,
            "plan": plan_rows,
        },
    )
    print_plan(plan_rows, stats)
    print(f"\nPlan CSV written: {plan_csv}")
    print(f"Plan JSON written: {plan_json}")

    prior_successes = load_prior_successes(results_csv)
    run_timestamp = now_stamp()
    results: list[dict[str, Any]] = []
    stop_after_failure = False

    for candidate in candidates:
        if stop_after_failure:
            record = make_base_result(candidate, args)
            record["rerun_status"] = "NOT_RUN_STOPPED_AFTER_FAILURE"
            record["suggested_next_action"] = (
                "Re-run with --continue-on-error after reviewing the prior failure."
            )
            results.append(record)
            continue

        record = process_case(
            candidate=candidate,
            args=args,
            prior_successes=prior_successes,
            run_timestamp=run_timestamp,
        )
        results.append(record)

        failed = record["rerun_status"].startswith("FAILED")
        if failed and not args.continue_on_error:
            stop_after_failure = True

    write_csv(results_csv, results, RESULT_FIELDS)
    write_json(
        results_json,
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": args.dry_run,
            "selection_stats": stats,
            "records": results,
        },
    )
    write_summary(
        path=summary_txt,
        args=args,
        stats=stats,
        results=results,
        plan_csv=plan_csv,
        results_csv=results_csv,
        results_json=results_json,
    )

    print(f"\nResults CSV written: {results_csv}")
    print(f"Results JSON written: {results_json}")
    print(f"Summary written: {summary_txt}")

    failures = [record for record in results if str(record["rerun_status"]).startswith("FAILED")]
    if failures and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
