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

from ro.manifest import ManifestError, read_run_manifest
from ro.paths import data_root, run_dir, runs_root
from ro.solver_common import (
    assess_history,
    assess_residual_convergence,
    blending_ramp_values,
    classify_convergence,
    detect_residual_plateau,
    find_windows_drive_paths,
    is_matrix_base_case_name,
    parse_residuals_from_transcript_text,
    path_to_fluent_str_resolved,
    strip_mesh_suffix,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPORT_SCRIPT = SCRIPT_DIR / "pyfluent_report_extract.py"

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
    "first_order_apply_status",
    "discretization_before",
    "discretization_after_first_order",
    "discretization_first_order_readback",
    "discretization_restore_status",
    "discretization_after_restore",
    "first_order_error_summary",
    "post_restore_polish_iterations",
    "post_restore_residual_latest",
    "post_restore_residual_target_met",
    "post_restore_report_values",
    "post_restore_monitor_assessment",
    "post_restore_strict_converged",
    "first_order_stage_residual_latest",
    "first_order_stage_report_values",
    "final_assessment_window",
    "final_assessment_reason",
    "staged_restore_enabled",
    "flow_second_order_species_first_order_enabled",
    "final_species_scheme",
    "final_flow_scheme_status",
    "species_second_order_skipped_reason",
    "mixed_order_strict_converged",
    "mixed_order_residual_latest",
    "mixed_order_report_values",
    "final_discretization_readback",
    "high_order_term_relaxation_before",
    "high_order_term_relaxation_after",
    "high_order_term_relaxation_apply_status",
    "second_order_blending_before",
    "second_order_blending_after",
    "second_order_blending_apply_status",
    "blending_ramp_iterations",
    "blending_hold_iterations",
    "blending_hold_residual_latest",
    "blending_hold_report_values",
    "blending_hold_monitor_assessment",
    "blending_hold_residual_target_met",
    "blending_hold_strict_converged",
    "momentum_restore_assessment_window",
    "restore_pressure_status",
    "restore_pressure_residual_latest",
    "restore_pressure_report_values",
    "restore_momentum_status",
    "restore_momentum_residual_latest",
    "restore_momentum_report_values",
    "restore_species_status",
    "restore_species_residual_latest",
    "restore_species_report_values",
    "limiting_restore_stage",
    "staged_restore_strict_converged",
    "convergence_assessment",
    "monitor_window",
    "monitor_rel_tol",
    "residual_growth_limit",
    "product_version",
    "residual_target_effective",
    "relaxation_profile",
    "relaxation_apply_status",
    "relaxation_before",
    "relaxation_after",
    "residual_latest",
    "residual_target_met",
    "plateau_detected",
    "plateau_reason",
    "strict_convergence_status",
]

FIRST_ORDER_DISCRETIZATION_CANDIDATES = {
    "mom": [
        "first-order-upwind",
        "first-order",
        "First Order Upwind",
        "first_order_upwind",
    ],
    "species-0": [
        "first-order-upwind",
        "first-order",
        "First Order Upwind",
        "first_order_upwind",
    ],
    "pressure": [
        "standard",
        "Standard",
        "linear",
        "second-order",
    ],
}
FIRST_ORDER_REQUIRED_KEYS = {"mom", "species-0"}
FIRST_ORDER_CONFIRMED_STATUSES = {
    "FIRST_ORDER_APPLIED_CONFIRMED",
    "FIRST_ORDER_PARTIAL_CONFIRMED",
}
FIRST_ORDER_STOP_STATUSES = {
    "FAILED_APPLY_FIRST_ORDER",
    "FIRST_ORDER_SWITCH_NOT_CONFIRMED",
}
STAGED_SECOND_ORDER_TARGETS = {
    "pressure": ["second-order", "Second Order", "second_order"],
    "mom": ["second-order-upwind", "second-order", "Second Order Upwind", "second_order_upwind"],
    "species-0": ["second-order-upwind", "second-order", "Second Order Upwind", "second_order_upwind"],
}
MIXED_ORDER_FIRST_STAGE_CANDIDATES = {
    "pressure": ["standard", "Standard"],
    "mom": [
        "first-order-upwind",
        "First Order Upwind",
        "first_order_upwind",
    ],
    "species-0": [
        "first-order-upwind",
        "First Order Upwind",
        "first_order_upwind",
    ],
}
MIXED_ORDER_REQUIRED_KEYS = {"pressure", "mom", "species-0"}
MIXED_ORDER_FINAL_TARGETS = {
    "pressure": ["second-order", "Second Order", "second_order"],
    "mom": ["second-order-upwind", "Second Order Upwind", "second_order_upwind"],
    "species-0": [
        "first-order-upwind",
        "First Order Upwind",
        "first_order_upwind",
    ],
}
MIXED_ORDER_SPECIES_SKIP_REASON = (
    "species-0 second-order restore intentionally skipped because "
    "staged_second_order_restore identified species as the limiting restore stage."
)
STAGED_RESTORE_STAGE_TO_KEY = {
    "pressure": "pressure",
    "momentum": "mom",
    "species": "species-0",
}
DISCRETIZATION_TUI_ERROR_PATTERNS = (
    "unbound variable",
    "invalid integer",
    "requested scheme is unavailable",
    "scheme is unavailable",
    "error object",
)


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


def residual_target_type(value: str) -> str | float:
    """Parse --residual-target as the literal 'preserve' or a positive float.

    Defaulting to 'preserve' (rather than a numeric fallback) is the fix for a
    prior bug where the runner silently relaxed strict 1e-7 residual criteria
    to 1e-5, which caused Fluent to report false convergence.
    """
    text = value.strip()
    if text.lower() == "preserve":
        return "preserve"
    parsed = float(text)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive or 'preserve'")
    return parsed


def pseudo_time_verbosity_type(value: str) -> str | int:
    """Parse --pseudo-time-verbosity as 'preserve' or an integer in {0, 1, 2}."""
    text = value.strip()
    if text.lower() == "preserve":
        return "preserve"
    if any(ch in text.lower() for ch in (".", "e")):
        raise argparse.ArgumentTypeError(
            "value must be 'preserve' or an integer 0/1/2"
        )
    try:
        parsed = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "value must be 'preserve' or an integer 0/1/2"
        ) from exc
    if parsed not in {0, 1, 2}:
        raise argparse.ArgumentTypeError(
            "value must be 'preserve' or an integer 0/1/2"
        )
    return parsed


def resolve_path_defaults(args: argparse.Namespace) -> argparse.Namespace:
    inventory_root = args.results_root or (data_root() / "inventory")
    args.results_root = Path(inventory_root).resolve()
    args.candidates_csv = Path(
        args.candidates_csv
        or args.results_root / "active_solver_rerun_candidates.csv"
    ).resolve()
    args.output_dir = Path(
        args.output_dir or args.results_root / "solver_rerun"
    ).resolve()
    args.logs_dir = Path(
        args.logs_dir or args.results_root / "solver_rerun_logs"
    ).resolve()
    return args


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
            "RO_DATA_ROOT/inventory/active_solver_rerun_candidates.csv."
        ),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help=(
            "Inventory/control output root (default: RO_DATA_ROOT/inventory). "
            "Candidate run data is resolved from its manifest under "
            "RO_DATA_ROOT/runs."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Central folder for solver_rerun_plan/results/summary outputs "
            "(default: RO_DATA_ROOT/inventory/solver_rerun)."
        ),
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help=(
            "Central folder for per-case solver rerun logs "
            "(default: RO_DATA_ROOT/inventory/solver_rerun_logs)."
        ),
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
            "staged_second_order_restore",
            "flow_second_order_species_first_order",
            "diagnose_only",
        ],
        default="damped_steady",
        help="Continuation strategy applied after loading the final case/data.",
    )
    parser.add_argument(
        "--allow-iterate-after-ramp-failure",
        action="store_true",
        help=(
            "For first_order_ramp/staged_second_order_restore/"
            "flow_second_order_species_first_order, continue iterating even if "
            "the first-order discretization switch is not confirmed by readback."
        ),
    )
    parser.add_argument(
        "--post-restore-polish-iterations",
        type=non_negative_int,
        default=None,
        help=(
            "Additional second-order iterations after first_order_ramp restore is "
            "confirmed. Derived default: 300 for first_order_ramp, 0 for other "
            "strategies unless explicitly requested."
        ),
    )
    parser.add_argument(
        "--restore-pressure-iterations",
        type=non_negative_int,
        default=300,
        help="Iteration budget after staged pressure restore.",
    )
    parser.add_argument(
        "--restore-momentum-iterations",
        type=non_negative_int,
        default=500,
        help="Iteration budget after staged momentum restore.",
    )
    parser.add_argument(
        "--restore-species-iterations",
        type=non_negative_int,
        default=1000,
        help="Iteration budget after staged species restore.",
    )
    parser.add_argument(
        "--use-high-order-term-relaxation",
        dest="use_high_order_term_relaxation",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Best-effort enable solution.methods.high_order_term_relaxation.enable "
            "with readback confirmation. Derived default: enabled for "
            "staged_second_order_restore and flow_second_order_species_first_order."
        ),
    )
    parser.add_argument(
        "--second-order-blending-start",
        type=positive_float,
        default=0.2,
        help="Starting expert first-to-second-order blending value when editable.",
    )
    parser.add_argument(
        "--second-order-blending-end",
        type=positive_float,
        default=1.0,
        help="Ending expert first-to-second-order blending value when editable.",
    )
    parser.add_argument(
        "--second-order-blending-steps",
        type=positive_int,
        default=5,
        help=(
            "Number of blending values used during the staged species restore "
            "(staged_second_order_restore) or momentum restore "
            "(flow_second_order_species_first_order)."
        ),
    )
    parser.add_argument(
        "--blending-hold-iterations",
        type=non_negative_int,
        default=1000,
        help=(
            "Iteration budget run at the final blending value after the "
            "flow_second_order_species_first_order momentum blending ramp. "
            "The hold-phase history alone is used for the final momentum "
            "restore convergence assessment; 0 disables the hold phase and "
            "falls back to assessing the full ramp history."
        ),
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
        type=residual_target_type,
        default="preserve",
        help=(
            "Residual convergence criterion. 'preserve' (default) never touches "
            "Fluent's existing residual criteria and uses whatever is already set "
            "(e.g. 1e-7) as the strict-convergence reference. Pass a numeric value "
            "such as 1e-7 to explicitly set the criterion for all requested "
            "equations. This is never silently downgraded."
        ),
    )
    parser.add_argument(
        "--plateau-window-chunks",
        type=positive_int,
        default=3,
        help=(
            "Number of most-recent iteration chunks inspected for a residual "
            "plateau (stuck above target with little chunk-to-chunk change)."
        ),
    )
    parser.add_argument(
        "--plateau-rel-change-tol",
        type=positive_float,
        default=0.02,
        help=(
            "Relative change threshold over the plateau window below which a "
            "still-unconverged residual equation is considered stuck."
        ),
    )
    parser.add_argument(
        "--plateau-min-residual-above-target-factor",
        type=positive_float,
        default=1.2,
        help=(
            "A residual equation must remain at least this many times its "
            "strict target to be eligible for plateau detection."
        ),
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
        choices=["baseline", "conservative", "strong"],
        default="conservative",
        help=(
            "Under-relaxation profile applied via the settings API with before/after "
            "readback verification. baseline leaves controls unchanged."
        ),
    )
    parser.add_argument(
        "--species-implicit-under-relaxation",
        type=residual_target_type,
        default="preserve",
        help=(
            "Orthogonal species implicit under-relaxation factor on "
            "solution.controls.advanced.expert.pseudo_time_method_usage."
            "global_dt[<species>].implicit_under_relaxation_factor. "
            "'preserve' (default) leaves the leaf unchanged. Independent of "
            "--relaxation-profile."
        ),
    )
    parser.add_argument(
        "--pseudo-time-verbosity",
        type=pseudo_time_verbosity_type,
        default="preserve",
        help=(
            "Orthogonal run_calculation.pseudo_time_settings.verbosity control. "
            "'preserve' (default) leaves Fluent unchanged. 1 prints the pseudo "
            "time step size per Fluent UG; 2 prints additional details. No "
            "transcript dt parser is enabled yet."
        ),
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

    if args.post_restore_polish_iterations is None:
        args.post_restore_polish_iterations = (
            300 if args.solver_strategy == "first_order_ramp" else 0
        )
    if args.use_high_order_term_relaxation is None:
        args.use_high_order_term_relaxation = args.solver_strategy in {
            "staged_second_order_restore",
            "flow_second_order_species_first_order",
        }

    unsafe_paths = [
        path
        for path in (
            args.results_root,
            args.candidates_csv,
            args.output_dir,
            args.logs_dir,
            args.report_script,
        )
        if path is not None
    ]
    reject_windows_drive_paths_on_non_windows(unsafe_paths, parser)

    if args.results_root is not None:
        args.results_root = args.results_root.resolve()
    if args.candidates_csv is not None:
        args.candidates_csv = args.candidates_csv.resolve()
    if args.output_dir is not None:
        args.output_dir = args.output_dir.resolve()
    if args.logs_dir is not None:
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

    unsafe = find_windows_drive_paths(paths)
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


fluent_path = path_to_fluent_str_resolved


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
        "latest_log_file": first_value(row, ["latest_log_file"]),
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

        base_case_name, _mesh_suffix = strip_mesh_suffix(case_name)
        if not is_matrix_base_case_name(base_case_name):
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


def resolve_candidate_run_directory(candidate: dict[str, str]) -> Path:
    cached = candidate.get("_run_directory", "")
    if cached:
        return Path(cached)

    geo_name = candidate["geo_name"]
    case_name = candidate["case_name"]
    label = f"{geo_name}/{case_name}"
    latest_log_file = candidate.get("latest_log_file", "")
    if not latest_log_file:
        raise ManifestError(
            f"Candidate {label} has no latest_log_file; refusing to locate its "
            "run manifest by name parsing or legacy path fallback."
        )

    latest_log_path = Path(latest_log_file)
    if not latest_log_path.is_absolute():
        raise ManifestError(
            f"Candidate {label} latest_log_file must be absolute to locate its "
            f"run manifest, got {latest_log_file!r}."
        )

    canonical_runs_root = runs_root().resolve()
    log_parent = latest_log_path.resolve().parent
    try:
        log_parent.relative_to(canonical_runs_root)
    except ValueError as exc:
        raise ManifestError(
            f"Candidate {label} latest_log_file is outside the canonical runs "
            f"tree {canonical_runs_root}: {latest_log_path}. Refusing legacy "
            "path fallback."
        ) from exc

    candidate_directory = log_parent
    while candidate_directory != canonical_runs_root:
        manifest_path = candidate_directory / "manifest.json"
        if manifest_path.is_file():
            payload = read_run_manifest(candidate_directory)
            canonical_directory = run_dir(
                payload["family"],
                payload["geo_id"],
                payload["mesh_id"],
                payload["run_id"],
            )
            candidate["_run_directory"] = str(canonical_directory)
            return canonical_directory
        candidate_directory = candidate_directory.parent

    raise ManifestError(
        f"Candidate {label} has no manifest.json in the latest_log_file "
        f"ancestry under {canonical_runs_root}: {latest_log_path}. Refusing "
        "name parsing and legacy path fallback."
    )


def final_pair_for_run(
    run_directory: Path,
    geo_name: str,
    case_name: str,
) -> tuple[Path, Path, Path]:
    case_dir = Path(run_directory)
    final_case_file = case_dir / f"{geo_name}_{case_name}_final.cas.h5"
    final_data_file = case_dir / f"{geo_name}_{case_name}_final.dat.h5"
    return case_dir, final_case_file, final_data_file


def final_pair_for_candidate(
    candidate: dict[str, str],
) -> tuple[Path, Path, Path]:
    return final_pair_for_run(
        resolve_candidate_run_directory(candidate),
        candidate["geo_name"],
        candidate["case_name"],
    )


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
        case_dir, final_case_file, final_data_file = final_pair_for_candidate(candidate)
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
    _, final_case_file, final_data_file = final_pair_for_candidate(row)
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
        "first_order_apply_status": "",
        "discretization_before": "",
        "discretization_after_first_order": "",
        "discretization_first_order_readback": "",
        "discretization_restore_status": "",
        "discretization_after_restore": "",
        "first_order_error_summary": "",
        "post_restore_polish_iterations": str(args.post_restore_polish_iterations),
        "post_restore_residual_latest": "{}",
        "post_restore_residual_target_met": "false",
        "post_restore_report_values": "{}",
        "post_restore_monitor_assessment": "{}",
        "post_restore_strict_converged": "false",
        "first_order_stage_residual_latest": "{}",
        "first_order_stage_report_values": "{}",
        "final_assessment_window": "",
        "final_assessment_reason": "",
        "staged_restore_enabled": str(
            args.solver_strategy == "staged_second_order_restore"
        ).lower(),
        "flow_second_order_species_first_order_enabled": str(
            args.solver_strategy == "flow_second_order_species_first_order"
        ).lower(),
        "final_species_scheme": "",
        "final_flow_scheme_status": "",
        "species_second_order_skipped_reason": (
            MIXED_ORDER_SPECIES_SKIP_REASON
            if args.solver_strategy == "flow_second_order_species_first_order"
            else ""
        ),
        "mixed_order_strict_converged": "false",
        "mixed_order_residual_latest": "{}",
        "mixed_order_report_values": "{}",
        "final_discretization_readback": "{}",
        "high_order_term_relaxation_before": "",
        "high_order_term_relaxation_after": "",
        "high_order_term_relaxation_apply_status": "",
        "second_order_blending_before": "",
        "second_order_blending_after": "",
        "second_order_blending_apply_status": "",
        "blending_ramp_iterations": "0",
        "blending_hold_iterations": str(args.blending_hold_iterations),
        "blending_hold_residual_latest": "{}",
        "blending_hold_report_values": "{}",
        "blending_hold_monitor_assessment": "{}",
        "blending_hold_residual_target_met": "false",
        "blending_hold_strict_converged": "false",
        "momentum_restore_assessment_window": "",
        "restore_pressure_status": "",
        "restore_pressure_residual_latest": "{}",
        "restore_pressure_report_values": "{}",
        "restore_momentum_status": "",
        "restore_momentum_residual_latest": "{}",
        "restore_momentum_report_values": "{}",
        "restore_species_status": "",
        "restore_species_residual_latest": "{}",
        "restore_species_report_values": "{}",
        "limiting_restore_stage": "",
        "staged_restore_strict_converged": "false",
        "convergence_assessment": "",
        "monitor_window": str(args.monitor_window),
        "monitor_rel_tol": str(args.monitor_rel_tol),
        "residual_growth_limit": str(args.residual_growth_limit),
        "product_version": args.product_version,
        "residual_target_effective": "",
        "relaxation_profile": args.relaxation_profile,
        "relaxation_apply_status": "",
        "relaxation_before": "",
        "relaxation_after": "",
        "residual_latest": "",
        "residual_target_met": "",
        "plateau_detected": "false",
        "plateau_reason": "",
        "strict_convergence_status": "",
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


def apply_residual_targets(
    solution: Any,
    residual_target: str | float,
    species_name: str,
) -> dict[str, float]:
    """Apply or preserve residual convergence criteria.

    When residual_target == "preserve" (the default), existing Fluent residual
    criteria are never modified -- they are only read back and returned so
    they can be used as the strict-convergence reference for later
    assessment. When residual_target is numeric, it is applied explicitly and
    used as the reference. This function never substitutes a looser value
    (e.g. 1e-5) for a stricter one on its own.
    """
    print("\nApplying residual convergence settings.")
    print(f"residual_target={residual_target!r}")
    resulting_targets: dict[str, float] = {}
    try:
        residual_equations_state = solution.monitor.residual.equations.get_state()
    except Exception as exc:
        print(f"Could not read residual equation state: {exc}")
        return resulting_targets

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

            if residual_target == "preserve":
                existing = None
                if isinstance(before, dict):
                    existing = before.get("absolute_criteria", before.get("relative_criteria"))
                if existing is not None:
                    resulting_targets[equation_name] = float(existing)
                    print(f"Preserving existing criterion for {equation_name}: {existing}")
                else:
                    print(
                        f"No existing residual criteria field found for {equation_name}; "
                        f"state={before}"
                    )
                continue

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
            after = equation.get_state()
            print(f"Residual after {equation_name}: {after}")
            applied = after.get("absolute_criteria", after.get("relative_criteria")) if isinstance(after, dict) else None
            if applied is not None:
                resulting_targets[equation_name] = float(applied)
        except Exception as exc:
            print(f"Could not update residual {equation_name}: {exc}")

    return resulting_targets


RELAXATION_PROFILES: dict[str, dict[str, float]] = {
    "conservative": {
        "explicit_pressure_under_relaxation": 0.2,
        "explicit_momentum_under_relaxation": 0.3,
        "species_pseudo_relaxation": 0.5,
    },
    "strong": {
        "explicit_pressure_under_relaxation": 0.1,
        "explicit_momentum_under_relaxation": 0.2,
        "species_pseudo_relaxation": 0.3,
    },
}


def set_and_verify_leaf(parent: Any, attr_name: str, value: float, label: str) -> dict[str, Any]:
    """Set a scalar settings-API leaf and confirm it via readback.

    Never reports success on a bare "no exception" -- the value is re-read
    after assignment and compared to what was requested.
    """
    outcome: dict[str, Any] = {"label": label, "requested": value, "status": "WARN_APPLY_URF_FAILED"}
    try:
        before_state = parent.get_state()
    except Exception as exc:
        outcome["error"] = f"could not read parent state: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    before = before_state.get(attr_name) if isinstance(before_state, dict) else None
    outcome["before"] = before
    print(f"URF before {label}: {before}")

    if isinstance(before_state, dict) and attr_name not in before_state:
        outcome["error"] = f"{attr_name} not present in state keys {list(before_state.keys())}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        setattr(parent, attr_name, value)
    except Exception as exc:
        outcome["error"] = f"set failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        after_state = parent.get_state()
        after = after_state.get(attr_name) if isinstance(after_state, dict) else None
    except Exception as exc:
        outcome["error"] = f"readback failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    outcome["after"] = after
    print(f"URF after {label}: {after}")

    confirmed = (
        isinstance(after, (int, float))
        and not isinstance(after, bool)
        and abs(float(after) - float(value)) < 1e-9
    )
    outcome["status"] = "APPLIED_CONFIRMED" if confirmed else "WARN_APPLY_URF_FAILED"
    if not confirmed:
        print(f"WARN_APPLY_URF_FAILED ({label}): readback {after} does not confirm requested {value}")
    return outcome


def set_and_verify_dict_entry(container: Any, key: str, value: float, label: str) -> dict[str, Any]:
    """Set one entry of a dict-like settings-API container and confirm via readback."""
    full_label = f"{label}[{key}]"
    outcome: dict[str, Any] = {"label": full_label, "requested": value, "status": "WARN_APPLY_URF_FAILED"}
    try:
        before_state = container.get_state()
    except Exception as exc:
        outcome["error"] = f"could not read container state: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {outcome['error']}")
        return outcome

    before = before_state.get(key) if isinstance(before_state, dict) else None
    outcome["before"] = before
    print(f"URF before {full_label}: {before}")

    set_ok = False
    set_error = ""
    try:
        container[key] = value
        set_ok = True
    except Exception as exc_item:
        set_error = f"container[key]=value failed: {type(exc_item).__name__}: {exc_item}"
        try:
            container.set_state({key: value})
            set_ok = True
        except Exception as exc_state:
            set_error += f"; set_state failed: {type(exc_state).__name__}: {exc_state}"

    if not set_ok:
        outcome["error"] = set_error
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {set_error}")
        return outcome

    try:
        after_state = container.get_state()
        after = after_state.get(key) if isinstance(after_state, dict) else None
    except Exception as exc:
        outcome["error"] = f"readback failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {outcome['error']}")
        return outcome

    outcome["after"] = after
    print(f"URF after {full_label}: {after}")
    confirmed = (
        isinstance(after, (int, float))
        and not isinstance(after, bool)
        and abs(float(after) - float(value)) < 1e-9
    )
    outcome["status"] = "APPLIED_CONFIRMED" if confirmed else "WARN_APPLY_URF_FAILED"
    if not confirmed:
        print(f"WARN_APPLY_URF_FAILED ({full_label}): readback {after} does not confirm requested {value}")
    return outcome


def apply_pseudo_time_species_relaxation(solution: Any, species_name: str, value: float) -> dict[str, Any]:
    """Best-effort species pseudo-time explicit relaxation update.

    The diagnostic logs that motivated this expose the setting at
    solution.controls.pseudo_time_explicit_relaxation_factor.global_dt_pseudo_relax,
    keyed either by the case's species name or the literal 'species-0'. Both
    are tried; if neither key exists this warns and continues rather than
    raising, per the requirement to never crash on an unavailable species
    path.
    """
    label = "pseudo_time_species_relaxation"
    outcome: dict[str, Any] = {"label": label, "requested": value, "status": "SKIPPED_SPECIES_UNAVAILABLE"}
    try:
        container = solution.controls.pseudo_time_explicit_relaxation_factor.global_dt_pseudo_relax
    except Exception as exc:
        outcome["error"] = f"container not found: {type(exc).__name__}: {exc}"
        outcome["status"] = "WARN_APPLY_URF_FAILED"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    candidate_keys = [key for key in (species_name, "species-0") if key]
    seen: set[str] = set()
    candidate_keys = [key for key in candidate_keys if not (key in seen or seen.add(key))]

    available_keys: list[str] = []
    try:
        state = container.get_state()
        if isinstance(state, dict):
            available_keys = list(state.keys())
    except Exception:
        available_keys = list_object_names(container)

    print(f"Pseudo-time species relaxation available keys: {available_keys}")

    matched_key = next((key for key in candidate_keys if key in available_keys), None)
    if matched_key is None:
        outcome["error"] = f"none of {candidate_keys} present in {available_keys}"
        print(f"SKIPPED_SPECIES_UNAVAILABLE ({label}): {outcome['error']}")
        return outcome

    return set_and_verify_dict_entry(container, matched_key, value, label)


def apply_species_implicit_under_relaxation(
    solution: Any,
    species_name: str,
    value: str | float,
) -> dict[str, Any]:
    """Apply orthogonal species implicit URF via the expert path.

    Independent of RELAXATION_PROFILES. value=="preserve" leaves the leaf
    untouched. Candidate keys prefer "species-0", then the configured species
    residual name.
    """
    label = "species_implicit_under_relaxation"
    outcome: dict[str, Any] = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    if isinstance(value, str) and value.strip().lower() == "preserve":
        print(
            "\nSpecies implicit under-relaxation preserve: "
            "leaving expert leaf unchanged."
        )
        outcome["status"] = "PRESERVED"
        return outcome

    try:
        requested = float(value)
    except (TypeError, ValueError) as exc:
        outcome["error"] = (
            f"value must be 'preserve' or a positive float: {value!r} "
            f"({type(exc).__name__}: {exc})"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome
    if requested <= 0.0:
        outcome["error"] = f"value must be positive: {value!r}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        global_dt = (
            solution.controls.advanced.expert
            .pseudo_time_method_usage.global_dt
        )
    except Exception as exc:
        outcome["error"] = (
            f"global_dt container not found: {type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    candidate_keys: list[str] = []
    for key in ("species-0", species_name):
        if key and key not in candidate_keys:
            candidate_keys.append(key)

    available_keys: list[str] = []
    try:
        state = global_dt.get_state()
        if isinstance(state, dict):
            available_keys = list(state.keys())
    except Exception:
        available_keys = list_object_names(global_dt)

    print(
        "Species implicit under-relaxation available keys: "
        f"{available_keys}"
    )
    matched_key = next(
        (key for key in candidate_keys if key in available_keys),
        None,
    )
    if matched_key is None:
        outcome["error"] = (
            f"none of {candidate_keys} present in {available_keys}"
        )
        outcome["status"] = "SKIPPED_SPECIES_UNAVAILABLE"
        print(f"SKIPPED_SPECIES_UNAVAILABLE ({label}): {outcome['error']}")
        return outcome

    print(f"Species implicit under-relaxation using key: {matched_key!r}")
    try:
        parent = global_dt[matched_key]
    except Exception as exc:
        outcome["error"] = (
            f"could not resolve global_dt[{matched_key!r}]: "
            f"{type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    leaf_outcome = set_and_verify_leaf(
        parent,
        "implicit_under_relaxation_factor",
        requested,
        f"{label}[{matched_key}]",
    )
    leaf_outcome["matched_key"] = matched_key
    leaf_outcome["label"] = label
    return leaf_outcome


def apply_pseudo_time_verbosity(
    solution: Any,
    value: str | int,
) -> dict[str, Any]:
    """Optionally raise run_calculation.pseudo_time_settings.verbosity.

    Default preserve leaves Fluent unchanged. Verbosity 1 is documented to
    print the pseudo time step size; no transcript parser is wired yet.
    """
    label = "pseudo_time_verbosity"
    outcome: dict[str, Any] = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    if isinstance(value, str) and value.strip().lower() == "preserve":
        print(
            "\nPseudo-time verbosity preserve: "
            "leaving run_calculation.pseudo_time_settings.verbosity unchanged."
        )
        outcome["status"] = "PRESERVED"
        return outcome

    try:
        requested = int(value)
    except (TypeError, ValueError) as exc:
        outcome["error"] = (
            f"value must be 'preserve' or an integer 0/1/2: {value!r} "
            f"({type(exc).__name__}: {exc})"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome
    if float(value) != float(requested) or requested not in {0, 1, 2}:
        outcome["error"] = (
            f"value must be 'preserve' or an integer in {{0, 1, 2}}: {value!r}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        parent = solution.run_calculation.pseudo_time_settings
    except Exception as exc:
        outcome["error"] = (
            "pseudo_time_settings not found: "
            f"{type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    return set_and_verify_leaf(parent, "verbosity", float(requested), label)


def apply_real_under_relaxation(solver: Any, profile: str, species_name: str) -> dict[str, Any]:
    """Apply the Coupled-solver under-relaxation profile via the settings API.

    Replaces the previous TUI-based '/solve/set/under-relaxation ...' path,
    which is not a valid command for this solver (Fluent reports "invalid
    command") and was being treated as a successful application. Every value
    set here is read back and only marked APPLIED_CONFIRMED if the readback
    matches; otherwise WARN_APPLY_URF_FAILED is logged and no success is
    claimed.
    """
    result: dict[str, Any] = {"profile": profile, "applied": []}
    if profile == "baseline":
        print("\nRelaxation profile baseline: leaving under-relaxation controls unchanged.")
        return result

    values = RELAXATION_PROFILES.get(profile)
    if values is None:
        print(f"Unknown relaxation profile {profile!r}; leaving controls unchanged.")
        return result

    print(f"\nApplying real under-relaxation profile: {profile}")
    solution = solver.settings.solution

    try:
        p_v_controls = solution.controls.p_v_controls
    except Exception as exc:
        print(f"WARN_APPLY_URF_FAILED (p_v_controls): could not access p_v_controls: {exc}")
        p_v_controls = None

    if p_v_controls is not None:
        result["applied"].append(
            set_and_verify_leaf(
                p_v_controls,
                "explicit_pressure_under_relaxation",
                values["explicit_pressure_under_relaxation"],
                "explicit_pressure_under_relaxation",
            )
        )
        result["applied"].append(
            set_and_verify_leaf(
                p_v_controls,
                "explicit_momentum_under_relaxation",
                values["explicit_momentum_under_relaxation"],
                "explicit_momentum_under_relaxation",
            )
        )
    else:
        result["applied"].append(
            {"label": "explicit_pressure_under_relaxation", "status": "WARN_APPLY_URF_FAILED", "error": "p_v_controls unavailable"}
        )
        result["applied"].append(
            {"label": "explicit_momentum_under_relaxation", "status": "WARN_APPLY_URF_FAILED", "error": "p_v_controls unavailable"}
        )

    result["applied"].append(
        apply_pseudo_time_species_relaxation(solution, species_name, values["species_pseudo_relaxation"])
    )

    for entry in result["applied"]:
        if entry.get("status") not in {"APPLIED_CONFIRMED", "SKIPPED_SPECIES_UNAVAILABLE"}:
            print(
                f"WARN_APPLY_URF_FAILED summary: {entry.get('label')}: "
                f"{entry.get('error', 'readback mismatch')}"
            )

    return result


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


def apply_continuation_settings(solver: Any, args: argparse.Namespace) -> tuple[dict[str, float], dict[str, Any]]:
    solution = solver.settings.solution
    residual_targets = apply_residual_targets(
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
    relaxation_result = apply_real_under_relaxation(
        solver=solver,
        profile=args.relaxation_profile,
        species_name=args.species_residual_name,
    )
    relaxation_result.setdefault("applied", []).append(
        apply_species_implicit_under_relaxation(
            solution=solution,
            species_name=args.species_residual_name,
            value=args.species_implicit_under_relaxation,
        )
    )
    verbosity_result = apply_pseudo_time_verbosity(
        solution=solution,
        value=args.pseudo_time_verbosity,
    )
    relaxation_result["pseudo_time_verbosity"] = verbosity_result
    return residual_targets, relaxation_result


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


def json_field(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, default=str)


def discretization_scheme_container(solver: Any) -> Any:
    return solver.settings.solution.methods.spatial_discretization.discretization_scheme


def normalize_discretization_value(value: Any) -> str:
    return re.sub(r"[\s_]+", "-", str(value).strip().lower())


def read_discretization_settings(solver: Any) -> dict[str, Any]:
    """Read solution.methods.spatial_discretization.discretization_scheme."""
    try:
        container = discretization_scheme_container(solver)
    except Exception as exc:
        raise RuntimeError(
            "solution.methods.spatial_discretization.discretization_scheme "
            f"is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        state = container.get_state() if hasattr(container, "get_state") else container
    except Exception as exc:
        raise RuntimeError(
            "could not read solution.methods.spatial_discretization."
            f"discretization_scheme: {type(exc).__name__}: {exc}"
        ) from exc

    if isinstance(state, dict) and isinstance(state.get("discretization_scheme"), dict):
        state = state["discretization_scheme"]

    if not isinstance(state, dict):
        raise RuntimeError(
            "solution.methods.spatial_discretization.discretization_scheme "
            f"returned non-dict state: {state!r}"
        )

    return {str(key): value for key, value in state.items()}


def _coerce_allowed_values(raw: Any, key: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        if key in raw:
            return _coerce_allowed_values(raw[key], key)
        for field in ("allowed_values", "allowed-values", "values", "choices", "options"):
            if field in raw:
                return _coerce_allowed_values(raw[field], key)
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw]
    return []


def _read_allowed_values_from_object(obj: Any, key: str) -> list[str]:
    allowed: list[str] = []
    for attr_name in ("allowed_values", "allowed-values"):
        try:
            attr = getattr(obj, attr_name)
        except Exception:
            continue
        try:
            raw = attr() if callable(attr) else attr
            allowed = _coerce_allowed_values(raw, key)
            if allowed:
                return allowed
        except Exception:
            continue

    for attr_name in ("allowed-values", "allowed_values", "allowedValues"):
        try:
            raw = obj.get_attr(attr_name)
            allowed = _coerce_allowed_values(raw, key)
            if allowed:
                return allowed
        except Exception:
            continue

    return allowed


def allowed_discretization_values(solver: Any, key: str) -> list[str]:
    """Best-effort allowed-value introspection for one discretization key."""
    try:
        container = discretization_scheme_container(solver)
    except Exception:
        return []

    objects: list[Any] = []
    try:
        objects.append(container[key])
    except Exception:
        pass
    objects.append(container)

    for obj in objects:
        allowed = _read_allowed_values_from_object(obj, key)
        if allowed:
            seen: set[str] = set()
            deduped: list[str] = []
            for value in allowed:
                if value not in seen:
                    seen.add(value)
                    deduped.append(value)
            return deduped

    return []


def _candidate_values_from_allowed(candidates: list[str], allowed_values: list[str]) -> list[str]:
    if not allowed_values:
        return candidates

    allowed_by_normalized: dict[str, str] = {}
    for allowed_value in allowed_values:
        allowed_by_normalized.setdefault(
            normalize_discretization_value(allowed_value),
            allowed_value,
        )

    selected: list[str] = []
    for candidate in candidates:
        match = allowed_by_normalized.get(normalize_discretization_value(candidate))
        if match and match not in selected:
            selected.append(match)
    return selected


def set_discretization_value(solver: Any, key: str, value: str) -> dict[str, Any]:
    """Set a discretization-scheme value through the Settings API."""
    outcome: dict[str, Any] = {
        "key": key,
        "requested": value,
        "method": "settings_api",
        "status": "SET_FAILED",
        "errors": [],
    }

    try:
        before = read_discretization_settings(solver)
        outcome["before"] = before.get(key)
    except Exception as exc:
        outcome["errors"].append(f"read before: {type(exc).__name__}: {exc}")
        return outcome

    if key not in before:
        outcome["errors"].append(f"{key!r} not present in discretization state")
        return outcome

    try:
        container = discretization_scheme_container(solver)
    except Exception as exc:
        outcome["errors"].append(f"container access: {type(exc).__name__}: {exc}")
        return outcome

    attempts: list[tuple[str, Any]] = []
    attempts.append(("container[key]", lambda: container.__setitem__(key, value)))
    try:
        child = container[key]
        if hasattr(child, "set_state"):
            attempts.append(("container[key].set_state", lambda: child.set_state(value)))
    except Exception:
        pass
    if key.isidentifier():
        attempts.append(("setattr(container, key)", lambda: setattr(container, key, value)))
    if hasattr(container, "set_state"):
        attempts.append(
            (
                "container.set_state(merged_state)",
                lambda: container.set_state({**before, key: value}),
            )
        )

    for attempt_label, setter in attempts:
        try:
            setter()
            outcome["status"] = "SET_ATTEMPTED"
            outcome["set_attempt"] = attempt_label
            return outcome
        except Exception as exc:
            outcome["errors"].append(f"{attempt_label}: {type(exc).__name__}: {exc}")

    return outcome


def confirm_discretization_value(
    solver: Any,
    key: str,
    expected_values: list[str],
) -> dict[str, Any]:
    """Confirm a discretization value by readback only."""
    outcome: dict[str, Any] = {
        "key": key,
        "expected_values": expected_values,
        "confirmed": False,
    }
    try:
        state = read_discretization_settings(solver)
    except Exception as exc:
        outcome["error"] = f"readback: {type(exc).__name__}: {exc}"
        return outcome

    actual = state.get(key)
    outcome["actual"] = actual
    expected_normalized = {
        normalize_discretization_value(value) for value in expected_values
    }
    outcome["confirmed"] = (
        key in state
        and normalize_discretization_value(actual) in expected_normalized
    )
    return outcome


def _tui_discretization_error(text: Any) -> bool:
    lowered = str(text).lower()
    return any(pattern in lowered for pattern in DISCRETIZATION_TUI_ERROR_PATTERNS)


def _execute_tui_discretization_value(
    solver: Any,
    key: str,
    value: str,
    expected_values: list[str],
) -> dict[str, Any]:
    command = f"/solve/set/discretization-scheme {key} {value}"
    outcome: dict[str, Any] = {
        "key": key,
        "requested": value,
        "method": "tui",
        "command": command,
        "status": "TUI_NOT_CONFIRMED",
    }
    print(f"Trying TUI fallback for first-order discretization: {command}")
    try:
        tui_result = solver.execute_tui(command)
        outcome["tui_result"] = "" if tui_result is None else str(tui_result)
        if _tui_discretization_error(outcome["tui_result"]):
            outcome["status"] = "TUI_FAILED"
            outcome["error"] = (
                "TUI returned a discretization error: "
                f"{outcome['tui_result']}"
            )
            print(f"TUI fallback failed for first-order discretization: {outcome['error']}")
            return outcome
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        outcome["error"] = message
        outcome["status"] = "TUI_FAILED"
        print(f"TUI fallback failed for first-order discretization: {command}: {message}")
        return outcome

    readback = confirm_discretization_value(solver, key, expected_values)
    outcome["readback"] = readback
    if readback.get("confirmed"):
        outcome["status"] = "CONFIRMED"
        print(
            "TUI fallback readback confirmed for first-order discretization: "
            f"{key}={readback.get('actual')}"
        )
    else:
        print(
            "TUI fallback returned without a matching error, but readback did "
            f"not confirm {key}={value}: {readback}"
        )
    return outcome


def _pressure_candidate_is_lower_order(value: Any) -> bool:
    return normalize_discretization_value(value) in {"standard", "linear"}


def apply_discretization_candidates(
    solver: Any,
    key: str,
    candidates: list[str],
    required: bool,
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "key": key,
        "required": required,
        "requested_candidates": candidates,
        "allowed_values": [],
        "attempts": [],
        "status": "NOT_CONFIRMED",
        "errors": [],
    }

    try:
        before_state = read_discretization_settings(solver)
    except Exception as exc:
        outcome["status"] = "READ_FAILED"
        outcome["errors"].append(f"read before: {type(exc).__name__}: {exc}")
        return outcome

    before_value = before_state.get(key)
    outcome["before"] = before_value
    if key not in before_state:
        outcome["status"] = "KEY_UNAVAILABLE"
        outcome["errors"].append(f"{key!r} not present in discretization state")
        print(f"Discretization key unavailable: {key}; available={list(before_state.keys())}")
        return outcome

    allowed_values = allowed_discretization_values(solver, key)
    outcome["allowed_values"] = allowed_values
    if allowed_values:
        print(f"Allowed discretization values for {key}: {allowed_values}")
    else:
        print(f"Allowed discretization values for {key}: unavailable")

    candidate_values = _candidate_values_from_allowed(candidates, allowed_values)
    if allowed_values and not candidate_values:
        outcome["status"] = "NO_ALLOWED_CANDIDATE" if required else "WARN_UNCHANGED"
        outcome["after"] = before_value
        outcome["errors"].append(
            f"none of {candidates} matched allowed values {allowed_values}"
        )
        return outcome

    for candidate in candidate_values:
        expected_values = candidates if key in FIRST_ORDER_REQUIRED_KEYS else [candidate]
        if (
            key == "pressure"
            and normalize_discretization_value(candidate) == "second-order"
            and normalize_discretization_value(candidate)
            != normalize_discretization_value(before_value)
        ):
            message = (
                f"skipping pressure candidate {candidate!r}; it is not a "
                "lower-order pressure scheme for this ramp"
            )
            outcome["attempts"].append(
                {
                    "key": key,
                    "requested": candidate,
                    "method": "settings_api",
                    "status": "SKIPPED_NON_LOWER_ORDER_PRESSURE",
                    "message": message,
                }
            )
            print(f"WARN first-order pressure discretization: {message}")
            continue
        if (
            key == "pressure"
            and normalize_discretization_value(candidate)
            == normalize_discretization_value(before_value)
            and not _pressure_candidate_is_lower_order(candidate)
        ):
            message = (
                f"pressure already uses {before_value}; leaving unchanged because "
                "no lower-order pressure candidate has been confirmed yet"
            )
            outcome["attempts"].append(
                {
                    "key": key,
                    "requested": candidate,
                    "method": "settings_api",
                    "status": "SKIPPED_UNCHANGED_PRESSURE",
                    "message": message,
                }
            )
            print(f"WARN first-order pressure discretization: {message}")
            continue

        if normalize_discretization_value(candidate) == normalize_discretization_value(before_value):
            readback = confirm_discretization_value(solver, key, [candidate])
            outcome["attempts"].append(
                {
                    "key": key,
                    "requested": candidate,
                    "method": "settings_api",
                    "status": "ALREADY_CONFIRMED",
                    "readback": readback,
                }
            )
            if readback.get("confirmed"):
                outcome["status"] = "CONFIRMED"
                outcome["after"] = readback.get("actual")
                outcome["confirmed_value"] = readback.get("actual")
                return outcome

        print(f"Trying Settings API discretization: {key} -> {candidate}")
        set_outcome = set_discretization_value(solver, key, candidate)
        outcome["attempts"].append(set_outcome)
        if set_outcome.get("status") != "SET_ATTEMPTED":
            outcome["errors"].extend(str(error) for error in set_outcome.get("errors", []))
            continue

        readback = confirm_discretization_value(solver, key, expected_values)
        set_outcome["readback"] = readback
        if readback.get("confirmed"):
            outcome["status"] = "CONFIRMED"
            outcome["after"] = readback.get("actual")
            outcome["confirmed_value"] = readback.get("actual")
            print(f"Settings API readback confirmed: {key}={readback.get('actual')}")
            return outcome

        message = (
            f"readback {readback.get('actual')!r} did not confirm any of "
            f"{expected_values}"
        )
        outcome["errors"].append(message)
        print(f"Settings API discretization not confirmed for {key}: {message}")

    for candidate in candidate_values:
        expected_values = candidates if key in FIRST_ORDER_REQUIRED_KEYS else [candidate]
        if (
            key == "pressure"
            and normalize_discretization_value(candidate) == "second-order"
            and normalize_discretization_value(candidate)
            != normalize_discretization_value(before_value)
        ):
            continue
        if (
            key == "pressure"
            and normalize_discretization_value(candidate)
            == normalize_discretization_value(before_value)
            and not _pressure_candidate_is_lower_order(candidate)
        ):
            continue
        tui_outcome = _execute_tui_discretization_value(
            solver=solver,
            key=key,
            value=candidate,
            expected_values=expected_values,
        )
        outcome["attempts"].append(tui_outcome)
        if tui_outcome.get("status") == "CONFIRMED":
            readback = tui_outcome.get("readback", {})
            outcome["status"] = "CONFIRMED"
            outcome["after"] = readback.get("actual")
            outcome["confirmed_value"] = readback.get("actual")
            return outcome
        if "error" in tui_outcome:
            outcome["errors"].append(str(tui_outcome["error"]))

    try:
        after_state = read_discretization_settings(solver)
        outcome["after"] = after_state.get(key)
    except Exception as exc:
        outcome["errors"].append(f"read after failed: {type(exc).__name__}: {exc}")

    if not required and outcome["status"] != "CONFIRMED":
        outcome["status"] = "WARN_UNCHANGED"
    return outcome


def _first_order_readback_summary(key_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, outcome in key_results.items():
        summary[key] = {
            "status": outcome.get("status"),
            "before": outcome.get("before"),
            "after": outcome.get("after"),
            "confirmed_value": outcome.get("confirmed_value"),
            "allowed_values": outcome.get("allowed_values", []),
            "errors": outcome.get("errors", []),
        }
    return summary


def _first_order_error_summary(key_results: dict[str, dict[str, Any]]) -> str:
    messages: list[str] = []
    for key, outcome in key_results.items():
        for error in outcome.get("errors", []):
            messages.append(f"{key}: {error}")
        if outcome.get("status") != "CONFIRMED":
            messages.append(f"{key}: status={outcome.get('status')}")
    return "; ".join(messages[:12])


def apply_pseudo_time_scale_reduction(solver: Any) -> dict[str, Any]:
    """Best-effort pseudo-time Courant/scale-factor reduction.

    The exact settings-API path for this varies by Fluent version; each
    candidate path is tried with before/after readback, and a WARN is logged
    (never a claimed success) if none apply.
    """
    print("\nAttempting pseudo-time Courant/scale factor reduction (best effort).")
    result: dict[str, Any] = {"attempts": []}
    solution = solver.settings.solution
    candidates = [
        ("solution.methods.pseudo_time_method.time_step_method.courant_number", ("methods", "pseudo_time_method", "time_step_method", "courant_number"), 20.0),
        ("solution.controls.pseudo_time_courant_number", ("controls", "pseudo_time_courant_number"), 20.0),
    ]
    for label, chain, value in candidates:
        try:
            target = solution
            for attr in chain[:-1]:
                target = getattr(target, attr)
            outcome = set_and_verify_leaf(target, chain[-1], value, label)
            result["attempts"].append(outcome)
            if outcome.get("status") == "APPLIED_CONFIRMED":
                return result
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            print(f"WARN_APPLY_URF_FAILED ({label}): {message}")
            result["attempts"].append({"label": label, "status": "WARN_APPLY_URF_FAILED", "error": message})
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
    result["pseudo_time_scale"] = apply_pseudo_time_scale_reduction(solver)
    return result


def apply_first_order_ramp(solver: Any) -> dict[str, Any]:
    print("\nApplying first-order ramp strategy settings.")
    result: dict[str, Any] = {
        "status": "ATTEMPTED",
        "restore_reliable": False,
        "discretization_before": {},
        "discretization_after_first_order": {},
        "discretization_first_order_readback": {},
        "key_results": {},
        "errors": [],
    }

    try:
        before = read_discretization_settings(solver)
        result["discretization_before"] = before
        result["restore_reliable"] = True
        print(
            "Original discretization settings from "
            "solution.methods.spatial_discretization.discretization_scheme: "
            f"{before}"
        )
    except Exception as exc:
        message = f"capture discretization: {type(exc).__name__}: {exc}"
        result["errors"].append(message)
        result["status"] = "FAILED_APPLY_FIRST_ORDER"
        result["first_order_error_summary"] = message
        print(f"FAILED_APPLY_FIRST_ORDER: {message}")
        return result

    key_results: dict[str, dict[str, Any]] = {}
    for key, candidates in FIRST_ORDER_DISCRETIZATION_CANDIDATES.items():
        key_results[key] = apply_discretization_candidates(
            solver=solver,
            key=key,
            candidates=candidates,
            required=key in FIRST_ORDER_REQUIRED_KEYS,
        )

    result["key_results"] = key_results
    try:
        after = read_discretization_settings(solver)
        result["discretization_after_first_order"] = after
        print(f"Discretization after first-order attempt: {after}")
    except Exception as exc:
        message = f"read after first-order: {type(exc).__name__}: {exc}"
        result["errors"].append(message)
        print(f"Could not read discretization after first-order attempt: {message}")

    result["discretization_first_order_readback"] = _first_order_readback_summary(key_results)

    required_confirmed = all(
        key_results.get(key, {}).get("status") == "CONFIRMED"
        for key in FIRST_ORDER_REQUIRED_KEYS
    )
    all_confirmed = all(
        outcome.get("status") == "CONFIRMED"
        for outcome in key_results.values()
    )
    if required_confirmed and all_confirmed:
        result["status"] = "FIRST_ORDER_APPLIED_CONFIRMED"
    elif required_confirmed:
        result["status"] = "FIRST_ORDER_PARTIAL_CONFIRMED"
    else:
        result["status"] = "FIRST_ORDER_SWITCH_NOT_CONFIRMED"

    error_summary = _first_order_error_summary(key_results)
    if result["errors"]:
        error_summary = "; ".join(result["errors"] + ([error_summary] if error_summary else []))
    result["first_order_error_summary"] = error_summary
    print(f"First-order ramp apply status: {result['status']}")
    if error_summary:
        print(f"First-order ramp warnings/errors: {error_summary}")
    return result


def apply_mixed_order_first_stage(solver: Any) -> dict[str, Any]:
    print("\nApplying mixed-order first-stage stabilization settings.")
    result: dict[str, Any] = {
        "status": "ATTEMPTED",
        "restore_reliable": False,
        "discretization_before": {},
        "discretization_after_first_order": {},
        "discretization_first_order_readback": {},
        "key_results": {},
        "errors": [],
    }

    try:
        before = read_discretization_settings(solver)
        result["discretization_before"] = before
        result["restore_reliable"] = True
        print(
            "Original discretization settings from "
            "solution.methods.spatial_discretization.discretization_scheme: "
            f"{before}"
        )
    except Exception as exc:
        message = f"capture discretization: {type(exc).__name__}: {exc}"
        result["errors"].append(message)
        result["status"] = "FAILED_APPLY_FIRST_ORDER"
        result["first_order_error_summary"] = message
        print(f"FAILED_APPLY_FIRST_ORDER: {message}")
        return result

    key_results: dict[str, dict[str, Any]] = {}
    for key, candidates in MIXED_ORDER_FIRST_STAGE_CANDIDATES.items():
        key_results[key] = apply_discretization_candidates(
            solver=solver,
            key=key,
            candidates=candidates,
            required=key in MIXED_ORDER_REQUIRED_KEYS,
        )

    result["key_results"] = key_results
    try:
        after = read_discretization_settings(solver)
        result["discretization_after_first_order"] = after
        print(f"Discretization after mixed-order first-stage attempt: {after}")
    except Exception as exc:
        message = f"read after first-order: {type(exc).__name__}: {exc}"
        result["errors"].append(message)
        print(f"Could not read discretization after first-order attempt: {message}")

    result["discretization_first_order_readback"] = _first_order_readback_summary(
        key_results
    )

    required_confirmed = all(
        key_results.get(key, {}).get("status") == "CONFIRMED"
        for key in MIXED_ORDER_REQUIRED_KEYS
    )
    result["status"] = (
        "FIRST_ORDER_APPLIED_CONFIRMED"
        if required_confirmed
        else "FIRST_ORDER_SWITCH_NOT_CONFIRMED"
    )

    error_summary = _first_order_error_summary(key_results)
    if result["errors"]:
        error_summary = "; ".join(result["errors"] + ([error_summary] if error_summary else []))
    result["first_order_error_summary"] = error_summary
    print(f"Mixed-order first-stage apply status: {result['status']}")
    if error_summary:
        print(f"Mixed-order first-stage warnings/errors: {error_summary}")
    return result


def restore_first_order_ramp(solver: Any, strategy_state: dict[str, Any]) -> dict[str, Any]:
    original_state = strategy_state.get("discretization_before") or {}
    result: dict[str, Any] = {
        "status": "RESTORE_NOT_CONFIRMED",
        "discretization_after_restore": {},
        "attempts": [],
        "errors": [],
    }
    if not original_state:
        message = "Original discretization state was not captured; restore is not reliable."
        result["errors"].append(message)
        print(message)
        return result

    print("Restoring original discretization settings.")
    for key, original_value in original_state.items():
        print(f"Trying Settings API restore: {key} -> {original_value}")
        set_outcome = set_discretization_value(solver, key, str(original_value))
        result["attempts"].append(set_outcome)
        if set_outcome.get("status") == "SET_ATTEMPTED":
            readback = confirm_discretization_value(solver, key, [str(original_value)])
            set_outcome["readback"] = readback
            if readback.get("confirmed"):
                print(f"Restore readback confirmed: {key}={readback.get('actual')}")
                continue
            result["errors"].append(
                f"{key}: restore readback {readback.get('actual')!r} "
                f"did not confirm {original_value!r}"
            )
        else:
            result["errors"].extend(
                f"{key}: {error}" for error in set_outcome.get("errors", [])
            )

        tui_outcome = _execute_tui_discretization_value(
            solver=solver,
            key=key,
            value=str(original_value),
            expected_values=[str(original_value)],
        )
        result["attempts"].append(tui_outcome)
        if tui_outcome.get("status") != "CONFIRMED" and "error" in tui_outcome:
            result["errors"].append(f"{key}: {tui_outcome['error']}")

    try:
        after = read_discretization_settings(solver)
        result["discretization_after_restore"] = after
        print(f"Discretization after restore attempt: {after}")
    except Exception as exc:
        message = f"read after restore: {type(exc).__name__}: {exc}"
        result["errors"].append(message)
        print(f"Could not read discretization after restore: {message}")
        return result

    confirmed = True
    for key, original_value in original_state.items():
        if key not in result["discretization_after_restore"]:
            confirmed = False
            result["errors"].append(f"{key}: missing after restore")
            continue
        if (
            normalize_discretization_value(result["discretization_after_restore"][key])
            != normalize_discretization_value(original_value)
        ):
            confirmed = False
            result["errors"].append(
                f"{key}: restore has {result['discretization_after_restore'][key]!r}, "
                f"expected {original_value!r}"
            )

    result["status"] = "RESTORE_CONFIRMED" if confirmed else "RESTORE_NOT_CONFIRMED"
    print(f"First-order ramp restore status: {result['status']}")
    return result


def _value_matches_requested(actual: Any, requested: Any) -> bool:
    if isinstance(requested, bool):
        return isinstance(actual, bool) and actual is requested
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and abs(float(actual) - float(requested)) < 1e-9
        )
    return str(actual) == str(requested)


def _read_settings_leaf(parent: Any, attr_name: str) -> Any:
    state = parent.get_state() if hasattr(parent, "get_state") else None
    if isinstance(state, dict) and attr_name in state:
        return state[attr_name]
    child = getattr(parent, attr_name)
    if hasattr(child, "get_state"):
        return child.get_state()
    return child


def set_and_verify_settings_leaf(
    parent: Any,
    attr_name: str,
    value: Any,
    label: str,
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_FAILED",
        "errors": [],
    }
    try:
        before = _read_settings_leaf(parent, attr_name)
        outcome["before"] = before
        print(f"{label} before: {before}")
    except Exception as exc:
        outcome["errors"].append(f"read before: {type(exc).__name__}: {exc}")
        print(f"WARN_APPLY_FAILED ({label}): {outcome['errors'][-1]}")
        return outcome

    set_attempts: list[tuple[str, Any]] = [
        ("setattr", lambda: setattr(parent, attr_name, value)),
    ]
    try:
        state = parent.get_state() if hasattr(parent, "get_state") else None
        if isinstance(state, dict):
            set_attempts.append(
                (
                    "set_state(merged_state)",
                    lambda: parent.set_state({**state, attr_name: value}),
                )
            )
    except Exception:
        pass

    for attempt_label, setter in set_attempts:
        try:
            setter()
            outcome["set_attempt"] = attempt_label
            break
        except Exception as exc:
            outcome["errors"].append(f"{attempt_label}: {type(exc).__name__}: {exc}")
    else:
        print(f"WARN_APPLY_FAILED ({label}): set attempts failed: {outcome['errors']}")
        return outcome

    try:
        after = _read_settings_leaf(parent, attr_name)
        outcome["after"] = after
        print(f"{label} after: {after}")
    except Exception as exc:
        outcome["errors"].append(f"read after: {type(exc).__name__}: {exc}")
        print(f"WARN_APPLY_FAILED ({label}): {outcome['errors'][-1]}")
        return outcome

    if _value_matches_requested(outcome["after"], value):
        outcome["status"] = "APPLIED_CONFIRMED"
    else:
        outcome["errors"].append(
            f"readback {outcome['after']!r} did not confirm requested {value!r}"
        )
        print(f"WARN_APPLY_FAILED ({label}): {outcome['errors'][-1]}")
    return outcome


def apply_high_order_term_relaxation(solver: Any, enable: bool) -> dict[str, Any]:
    label = "solution.methods.high_order_term_relaxation.enable"
    result: dict[str, Any] = {
        "status": "NOT_REQUESTED",
        "before": "",
        "after": "",
        "errors": [],
    }
    print(f"\nHigh Order Term Relaxation requested: {enable}")
    try:
        hotr = solver.settings.solution.methods.high_order_term_relaxation
    except Exception as exc:
        result["status"] = "WARN_APPLY_FAILED"
        result["errors"].append(f"access: {type(exc).__name__}: {exc}")
        print(f"WARN_APPLY_FAILED ({label}): {result['errors'][-1]}")
        return result

    try:
        result["before"] = _read_settings_leaf(hotr, "enable")
        print(f"{label} before: {result['before']}")
    except Exception as exc:
        result["status"] = "WARN_APPLY_FAILED"
        result["errors"].append(f"read before: {type(exc).__name__}: {exc}")
        print(f"WARN_APPLY_FAILED ({label}): {result['errors'][-1]}")
        return result

    if not enable:
        result["status"] = "DISABLED_BY_OPTION"
        result["after"] = result["before"]
        return result

    outcome = set_and_verify_settings_leaf(hotr, "enable", True, label)
    result["before"] = outcome.get("before", result["before"])
    result["after"] = outcome.get("after", "")
    result["status"] = outcome.get("status", "WARN_APPLY_FAILED")
    result["errors"] = outcome.get("errors", [])
    return result


def _second_order_blending_parent(solver: Any) -> Any:
    return solver.settings.solution.methods.expert.numerics_pbns


def read_second_order_blending(solver: Any) -> dict[str, Any]:
    label = "solution.methods.expert.numerics_pbns.first_to_second_order_blending"
    result: dict[str, Any] = {"status": "READ_FAILED", "value": "", "errors": []}
    try:
        parent = _second_order_blending_parent(solver)
        result["value"] = _read_settings_leaf(parent, "first_to_second_order_blending")
        result["status"] = "READ_CONFIRMED"
        print(f"{label}: {result['value']}")
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        print(f"Could not read {label}: {result['errors'][-1]}")
    return result


def set_second_order_blending(solver: Any, value: float) -> dict[str, Any]:
    label = "solution.methods.expert.numerics_pbns.first_to_second_order_blending"
    try:
        parent = _second_order_blending_parent(solver)
    except Exception as exc:
        return {
            "label": label,
            "requested": value,
            "status": "WARN_APPLY_FAILED",
            "errors": [f"access: {type(exc).__name__}: {exc}"],
        }
    return set_and_verify_settings_leaf(
        parent,
        "first_to_second_order_blending",
        float(value),
        label,
    )


def prepare_second_order_blending(solver: Any, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "NOT_ATTEMPTED",
        "before": "",
        "after": "",
        "editable": False,
        "steps": [],
        "errors": [],
    }
    before = read_second_order_blending(solver)
    result["before"] = before.get("value", "")
    if before.get("status") != "READ_CONFIRMED":
        result["status"] = "UNAVAILABLE_CONTINUING_WITH_HOTR_ONLY"
        result["errors"] = before.get("errors", [])
        return result

    start_outcome = set_second_order_blending(
        solver,
        args.second_order_blending_start,
    )
    result["after"] = start_outcome.get("after", "")
    if start_outcome.get("status") == "APPLIED_CONFIRMED":
        result["status"] = "START_CONFIRMED"
        result["editable"] = True
    else:
        result["status"] = "READ_ONLY_OR_UNCONFIRMED_CONTINUING_WITH_HOTR_ONLY"
        result["errors"] = start_outcome.get("errors", [])
        print(
            "Second-order blending appears read-only or unconfirmed; continuing "
            "with High Order Term Relaxation only."
        )
    return result


def apply_discretization_target(
    solver: Any,
    key: str,
    candidates: list[str],
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "key": key,
        "requested_candidates": candidates,
        "allowed_values": [],
        "attempts": [],
        "status": "NOT_CONFIRMED",
        "errors": [],
    }
    try:
        before_state = read_discretization_settings(solver)
        outcome["before"] = before_state.get(key)
    except Exception as exc:
        outcome["status"] = "READ_FAILED"
        outcome["errors"].append(f"read before: {type(exc).__name__}: {exc}")
        return outcome

    if key not in before_state:
        outcome["status"] = "KEY_UNAVAILABLE"
        outcome["errors"].append(f"{key!r} not present in discretization state")
        return outcome

    allowed_values = allowed_discretization_values(solver, key)
    outcome["allowed_values"] = allowed_values
    candidate_values = _candidate_values_from_allowed(candidates, allowed_values)
    if allowed_values and not candidate_values:
        outcome["status"] = "NO_ALLOWED_CANDIDATE"
        outcome["errors"].append(
            f"none of {candidates} matched allowed values {allowed_values}"
        )
        return outcome
    if not candidate_values:
        candidate_values = candidates

    for candidate in candidate_values:
        if normalize_discretization_value(candidate) == normalize_discretization_value(
            outcome["before"]
        ):
            readback = confirm_discretization_value(solver, key, candidates)
            outcome["attempts"].append(
                {
                    "key": key,
                    "requested": candidate,
                    "method": "settings_api",
                    "status": "ALREADY_CONFIRMED",
                    "readback": readback,
                }
            )
            if readback.get("confirmed"):
                outcome["status"] = "CONFIRMED"
                outcome["after"] = readback.get("actual")
                outcome["confirmed_value"] = readback.get("actual")
                return outcome

        print(f"Trying staged restore discretization: {key} -> {candidate}")
        set_outcome = set_discretization_value(solver, key, candidate)
        outcome["attempts"].append(set_outcome)
        if set_outcome.get("status") == "SET_ATTEMPTED":
            readback = confirm_discretization_value(solver, key, candidates)
            set_outcome["readback"] = readback
            if readback.get("confirmed"):
                outcome["status"] = "CONFIRMED"
                outcome["after"] = readback.get("actual")
                outcome["confirmed_value"] = readback.get("actual")
                return outcome
            outcome["errors"].append(
                f"readback {readback.get('actual')!r} did not confirm {candidates}"
            )
        else:
            outcome["errors"].extend(str(error) for error in set_outcome.get("errors", []))

    for candidate in candidate_values:
        tui_outcome = _execute_tui_discretization_value(
            solver=solver,
            key=key,
            value=candidate,
            expected_values=candidates,
        )
        outcome["attempts"].append(tui_outcome)
        if tui_outcome.get("status") == "CONFIRMED":
            readback = tui_outcome.get("readback", {})
            outcome["status"] = "CONFIRMED"
            outcome["after"] = readback.get("actual")
            outcome["confirmed_value"] = readback.get("actual")
            return outcome
        if "error" in tui_outcome:
            outcome["errors"].append(str(tui_outcome["error"]))

    try:
        after_state = read_discretization_settings(solver)
        outcome["after"] = after_state.get(key)
    except Exception as exc:
        outcome["errors"].append(f"read after: {type(exc).__name__}: {exc}")
    return outcome


def full_second_order_discretization_confirmed(state: dict[str, Any]) -> bool:
    for key, candidates in STAGED_SECOND_ORDER_TARGETS.items():
        if key not in state:
            return False
        expected = {normalize_discretization_value(value) for value in candidates}
        if normalize_discretization_value(state[key]) not in expected:
            return False
    return True


def mixed_order_target_candidates(original_state: dict[str, Any], key: str) -> list[str]:
    candidates: list[str] = []
    original_value = original_state.get(key)
    expected = {
        normalize_discretization_value(value)
        for value in MIXED_ORDER_FINAL_TARGETS.get(key, [])
    }
    if (
        original_value is not None
        and normalize_discretization_value(original_value) in expected
    ):
        candidates.append(str(original_value))
    candidates.extend(MIXED_ORDER_FINAL_TARGETS.get(key, []))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        normalized = normalize_discretization_value(value)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(value)
    return deduped


def _coerce_discretization_state(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def mixed_order_discretization_confirmed(state: Any) -> bool:
    coerced = _coerce_discretization_state(state)
    for key, candidates in MIXED_ORDER_FINAL_TARGETS.items():
        if key not in coerced:
            return False
        expected = {normalize_discretization_value(value) for value in candidates}
        if normalize_discretization_value(coerced[key]) not in expected:
            return False
    return True


def residual_equation_above_target(
    residual_assessment: dict[str, Any],
    equation_name: str,
) -> bool:
    target_name = equation_name.lower()
    per_equation = residual_assessment.get("per_equation", {})
    if not isinstance(per_equation, dict):
        return False
    for name, details in per_equation.items():
        if str(name).lower() != target_name or not isinstance(details, dict):
            continue
        latest = details.get("latest")
        target = details.get("target")
        return (
            isinstance(latest, (int, float))
            and isinstance(target, (int, float))
            and not isinstance(latest, bool)
            and not isinstance(target, bool)
            and float(latest) > float(target)
        )
    return False


def run_iteration_chunks(
    solver: Any,
    total_iterations: int,
    chunk_size: int,
    transcript_path: Path | None,
    species_name: str,
    strict_targets: dict[str, float],
    plateau_window_chunks: int,
    plateau_rel_change_tol: float,
    plateau_min_above_target_factor: float,
    stage_name: str = "solver_stage",
    early_stop_on_strict_residual: bool = False,
    early_stop_on_strict_stable: bool = False,
    assessment_args: argparse.Namespace | None = None,
    monitor_value_groups: tuple[str, ...] = ("residual_numeric", "report_values"),
    monitor_require_two_samples: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history: list[dict[str, Any]] = []
    remaining = total_iterations
    chunk_index = 0
    target_names = {"continuity", "x-velocity", "y-velocity", "z-velocity", species_name.lower()}
    plateau_result: dict[str, Any] = {"detected": False, "reason": "Iteration budget completed without a plateau check triggering."}
    while remaining > 0:
        chunk_index += 1
        iter_count = min(chunk_size, remaining)
        print(f"\nRunning {stage_name} iteration chunk {chunk_index}: {iter_count} iterations")
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
        snapshot["stage"] = stage_name
        snapshot["chunk_index"] = chunk_index
        snapshot["iterations_completed_in_chunk"] = iter_count

        transcript_residuals: dict[str, float] = {}
        if transcript_path is not None:
            try:
                if transcript_path.is_file():
                    text = transcript_path.read_text(encoding="utf-8", errors="ignore")
                    transcript_residuals = parse_residuals_from_transcript_text(text, target_names)
            except Exception as exc:
                print(f"Could not parse transcript residuals for chunk {chunk_index}: {exc}")
        snapshot["residual_transcript"] = transcript_residuals
        if transcript_residuals:
            merged = dict(snapshot.get("residual_numeric", {}))
            merged.update(transcript_residuals)
            snapshot["residual_numeric"] = merged

        history.append(snapshot)
        remaining -= iter_count
        print(
            f"Chunk {chunk_index} monitor snapshot: residual_keys="
            f"{list(snapshot.get('residual_numeric', {}).keys())}, "
            f"transcript_residual_keys={list(transcript_residuals.keys())}, "
            f"report_keys={list(snapshot.get('report_values', {}).keys())}"
        )

        plateau_result = detect_residual_plateau(
            history=history,
            strict_targets=strict_targets,
            window_chunks=plateau_window_chunks,
            rel_change_tol=plateau_rel_change_tol,
            min_above_target_factor=plateau_min_above_target_factor,
        )
        if plateau_result["detected"]:
            print(f"Residual plateau detected after chunk {chunk_index}: {plateau_result['reason']}")
            print("Stopping iteration early instead of continuing to the full iteration budget.")
            break

        if early_stop_on_strict_residual:
            latest_residuals = dict(snapshot.get("residual_numeric", {}))
            residual_assessment = assess_residual_convergence(
                latest_residuals=latest_residuals,
                strict_targets=strict_targets,
            )
            if residual_assessment.get("strict_met"):
                plateau_result["reason"] = (
                    "Stopped early after strict residual target "
                    f"in {stage_name}."
                )
                print(plateau_result["reason"])
                break

        if early_stop_on_strict_stable and assessment_args is not None:
            latest_residuals = dict(snapshot.get("residual_numeric", {}))
            residual_assessment = assess_residual_convergence(
                latest_residuals=latest_residuals,
                strict_targets=strict_targets,
            )
            monitor_assessment = assess_history(
                history,
                assessment_args,
                value_groups=monitor_value_groups,
                require_two_samples=monitor_require_two_samples,
            )
            if residual_assessment.get("strict_met") and monitor_assessment.get("stable"):
                plateau_result["reason"] = (
                    "Stopped early after strict residual target and stable monitors "
                    f"in {stage_name}."
                )
                print(plateau_result["reason"])
                break

    return history, plateau_result


def latest_snapshot_group(history: list[dict[str, Any]], group_name: str) -> dict[str, Any]:
    if not history:
        return {}
    values = history[-1].get(group_name, {})
    return dict(values) if isinstance(values, dict) else {}


def strict_residual_met_at_snapshot(
    snapshot: dict[str, Any],
    strict_targets: dict[str, float],
) -> bool:
    values = snapshot.get("residual_numeric", {})
    if not isinstance(values, dict):
        values = {}
    return bool(
        assess_residual_convergence(
            latest_residuals=dict(values),
            strict_targets=strict_targets,
        ).get("strict_met")
    )


def history_after_first_strict_residual_met(
    history: list[dict[str, Any]],
    strict_targets: dict[str, float],
    max_snapshots: int,
) -> tuple[list[dict[str, Any]], str]:
    if not history:
        return [], "No monitor history was available."

    first_met_index: int | None = None
    for index, snapshot in enumerate(history):
        if strict_residual_met_at_snapshot(snapshot, strict_targets):
            first_met_index = index
            break

    if first_met_index is None:
        window = history[-max(1, max_snapshots) :]
        return (
            window,
            "Residual target was not met in the first-order stage; using the latest "
            "available first_order_ramp_stage snapshots.",
        )

    post_met_history = history[first_met_index:]
    window = post_met_history[-max(1, max_snapshots) :]
    chunk_index = history[first_met_index].get("chunk_index", first_met_index + 1)
    return (
        window,
        "Using first_order_ramp_stage snapshots after strict residual target was "
        f"first met at chunk {chunk_index}.",
    )


def summarize_relaxation_result(relaxation_result: dict[str, Any]) -> dict[str, str]:
    """Flatten apply_real_under_relaxation's output into CSV-friendly fields."""
    profile = relaxation_result.get("profile", "")
    applied = relaxation_result.get("applied", [])

    if profile == "baseline" and not applied:
        return {"apply_status": "BASELINE_NO_CHANGE", "before": "{}", "after": "{}"}
    if not applied:
        return {"apply_status": "NOT_ATTEMPTED", "before": "{}", "after": "{}"}

    ok_statuses = {
        "APPLIED_CONFIRMED",
        "SKIPPED_SPECIES_UNAVAILABLE",
        "PRESERVED",
    }
    statuses = {entry.get("status") for entry in applied}
    confirmed_or_ok = statuses <= ok_statuses
    if confirmed_or_ok and "APPLIED_CONFIRMED" in statuses:
        apply_status = "ALL_CONFIRMED"
    elif confirmed_or_ok:
        apply_status = (
            "BASELINE_NO_CHANGE"
            if profile == "baseline"
            else "PRESERVED_OR_SKIPPED"
        )
    elif "APPLIED_CONFIRMED" in statuses:
        apply_status = "PARTIAL_WARN"
    else:
        apply_status = "WARN_APPLY_URF_FAILED"

    before = {entry.get("label"): entry.get("before") for entry in applied}
    after = {entry.get("label"): entry.get("after") for entry in applied}
    return {
        "apply_status": apply_status,
        "before": json.dumps(before, sort_keys=True, default=str),
        "after": json.dumps(after, sort_keys=True, default=str),
    }


def stage_iteration_assessment(
    history: list[dict[str, Any]],
    args: argparse.Namespace,
    residual_targets: dict[str, float],
) -> dict[str, Any]:
    latest_residuals = latest_snapshot_group(history, "residual_numeric")
    latest_reports = latest_snapshot_group(history, "report_values")
    residual_assessment = assess_residual_convergence(
        latest_residuals=latest_residuals,
        strict_targets=residual_targets,
    )
    report_assessment = assess_history(
        history,
        args,
        value_groups=("report_values",),
        require_two_samples=True,
    )
    all_numeric_assessment = assess_history(history, args)
    strict_converged = bool(
        residual_assessment.get("strict_met")
        and report_assessment.get("stable")
        and not report_assessment.get("diverged")
        and not all_numeric_assessment.get("diverged")
    )
    return {
        "latest_residuals": latest_residuals,
        "latest_reports": latest_reports,
        "residual": residual_assessment,
        "monitor": report_assessment,
        "monitor_all_numeric": all_numeric_assessment,
        "strict_converged": strict_converged,
    }


def second_order_target_candidates(original_state: dict[str, Any], key: str) -> list[str]:
    candidates: list[str] = []
    original_value = original_state.get(key)
    expected = {
        normalize_discretization_value(value)
        for value in STAGED_SECOND_ORDER_TARGETS.get(key, [])
    }
    if (
        original_value is not None
        and normalize_discretization_value(original_value) in expected
    ):
        candidates.append(str(original_value))
    candidates.extend(STAGED_SECOND_ORDER_TARGETS.get(key, []))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        normalized = normalize_discretization_value(value)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(value)
    return deduped


def run_plain_restore_iterations(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    stage_name: str,
    iterations: int,
    residual_targets: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return run_iteration_chunks(
        solver=solver,
        total_iterations=iterations,
        chunk_size=args.iteration_chunk_size,
        transcript_path=transcript_path,
        species_name=args.species_residual_name,
        strict_targets=residual_targets,
        plateau_window_chunks=args.plateau_window_chunks,
        plateau_rel_change_tol=args.plateau_rel_change_tol,
        plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
        stage_name=stage_name,
        early_stop_on_strict_stable=True,
        assessment_args=args,
        monitor_value_groups=("report_values",),
        monitor_require_two_samples=True,
    )


def run_blending_ramp_iterations(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    stage_name: str,
    total_iterations: int,
    residual_targets: dict[str, float],
    blending_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    history: list[dict[str, Any]] = []
    plateau_result: dict[str, Any] = {
        "detected": False,
        "reason": "Blending ramp completed without a plateau check triggering.",
    }
    values = blending_ramp_values(
        args.second_order_blending_start,
        args.second_order_blending_end,
        args.second_order_blending_steps,
    )
    if total_iterations <= 0:
        blending_result["status"] = "RAMP_CONFIRMED_NO_ITERATIONS"
        return history, plateau_result, blending_result

    base_iterations = total_iterations // len(values)
    remainder = total_iterations % len(values)
    for index, value in enumerate(values):
        iter_count = base_iterations + (1 if index < remainder else 0)
        step: dict[str, Any] = {"value": value, "iterations": iter_count}
        set_outcome = set_second_order_blending(solver, value)
        step["set_outcome"] = set_outcome
        blending_result.setdefault("steps", []).append(step)
        if set_outcome.get("status") != "APPLIED_CONFIRMED":
            blending_result["status"] = "RAMP_NOT_CONFIRMED_CONTINUING_WITH_HOTR_ONLY"
            blending_result["after"] = set_outcome.get("after", "")
            print(
                "Second-order blending ramp could not confirm a value; continuing "
                "without further blending edits."
            )
            if iter_count > 0:
                chunk_history, plateau_result = run_plain_restore_iterations(
                    solver=solver,
                    args=args,
                    transcript_path=transcript_path,
                    stage_name=stage_name,
                    iterations=iter_count,
                    residual_targets=residual_targets,
                )
                history.extend(chunk_history)
            return history, plateau_result, blending_result

        if iter_count <= 0:
            continue
        chunk_history, plateau_result = run_iteration_chunks(
            solver=solver,
            total_iterations=iter_count,
            chunk_size=iter_count,
            transcript_path=transcript_path,
            species_name=args.species_residual_name,
            strict_targets=residual_targets,
            plateau_window_chunks=args.plateau_window_chunks,
            plateau_rel_change_tol=args.plateau_rel_change_tol,
            plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
            stage_name=f"{stage_name}_blending_{index + 1}",
        )
        history.extend(chunk_history)
        step["assessment"] = stage_iteration_assessment(
            chunk_history,
            args,
            residual_targets,
        )

    after = read_second_order_blending(solver)
    blending_result["after"] = after.get("value", "")
    if after.get("status") == "READ_CONFIRMED":
        blending_result["status"] = "RAMP_CONFIRMED"
    else:
        blending_result["status"] = "RAMP_COMPLETED_READBACK_FAILED"
        blending_result.setdefault("errors", []).extend(after.get("errors", []))
    return history, plateau_result, blending_result


def run_staged_restore_component(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    stage_label: str,
    original_state: dict[str, Any],
    iterations: int,
    residual_targets: dict[str, float],
    blending_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = STAGED_RESTORE_STAGE_TO_KEY[stage_label]
    stage_prefix = stage_label.upper()
    result: dict[str, Any] = {
        "stage": stage_label,
        "key": key,
        "status": f"{stage_prefix}_RESTORE_NOT_CONVERGED",
        "iterations_requested": iterations,
        "restore_outcome": {},
        "history": [],
        "plateau": {},
        "assessment": {},
    }
    candidates = second_order_target_candidates(original_state, key)
    restore_outcome = apply_discretization_target(solver, key, candidates)
    result["restore_outcome"] = restore_outcome
    if restore_outcome.get("status") != "CONFIRMED":
        result["status"] = f"{stage_prefix}_RESTORE_READBACK_FAILED"
        result["assessment"] = {
            "details": "Discretization readback did not confirm the staged restore.",
        }
        return result

    if stage_label == "species" and blending_result and blending_result.get("editable"):
        history, plateau_result, blending_result = run_blending_ramp_iterations(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_name=f"restore_{stage_label}_stage",
            total_iterations=iterations,
            residual_targets=residual_targets,
            blending_result=blending_result,
        )
        result["blending_result"] = blending_result
    else:
        history, plateau_result = run_plain_restore_iterations(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_name=f"restore_{stage_label}_stage",
            iterations=iterations,
            residual_targets=residual_targets,
        )

    result["history"] = history
    result["plateau"] = plateau_result
    assessment = stage_iteration_assessment(history, args, residual_targets)
    result["assessment"] = assessment
    if assessment.get("strict_converged"):
        result["status"] = f"{stage_prefix}_RESTORE_CONVERGED"
    return result


def run_mixed_order_restore_component(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    stage_label: str,
    original_state: dict[str, Any],
    iterations: int,
    residual_targets: dict[str, float],
    blending_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = STAGED_RESTORE_STAGE_TO_KEY[stage_label]
    stage_prefix = stage_label.upper()
    result: dict[str, Any] = {
        "stage": stage_label,
        "key": key,
        "status": f"{stage_prefix}_RESTORE_NOT_CONVERGED",
        "iterations_requested": iterations,
        "restore_outcome": {},
        "history": [],
        "plateau": {},
        "assessment": {},
    }
    candidates = mixed_order_target_candidates(original_state, key)
    restore_outcome = apply_discretization_target(solver, key, candidates)
    result["restore_outcome"] = restore_outcome
    if restore_outcome.get("status") != "CONFIRMED":
        result["status"] = f"{stage_prefix}_RESTORE_READBACK_FAILED"
        result["assessment"] = {
            "details": "Mixed-order discretization readback did not confirm restore.",
        }
        return result

    if stage_label == "momentum" and blending_result and blending_result.get("editable"):
        ramp_history, plateau_result, blending_result = run_blending_ramp_iterations(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_name=f"mixed_order_restore_{stage_label}_stage",
            total_iterations=iterations,
            residual_targets=residual_targets,
            blending_result=blending_result,
        )
        result["blending_result"] = blending_result
        result["blending_ramp_iterations"] = sum(
            int(snapshot.get("iterations_completed_in_chunk", 0))
            for snapshot in ramp_history
        )
        result["ramp_assessment"] = (
            stage_iteration_assessment(ramp_history, args, residual_targets)
            if ramp_history
            else {}
        )
        history = list(ramp_history)

        hold_history: list[dict[str, Any]] = []
        if args.blending_hold_iterations > 0:
            print(
                f"\nStarting final-blending hold phase: "
                f"{args.blending_hold_iterations} iterations at blending "
                f"{blending_result.get('after', '')!r}"
            )
            hold_history, plateau_result = run_plain_restore_iterations(
                solver=solver,
                args=args,
                transcript_path=transcript_path,
                stage_name=(
                    f"mixed_order_restore_{stage_label}_stage_blending_hold"
                ),
                iterations=args.blending_hold_iterations,
                residual_targets=residual_targets,
            )
            history.extend(hold_history)
        result["blending_hold_iterations"] = sum(
            int(snapshot.get("iterations_completed_in_chunk", 0))
            for snapshot in hold_history
        )

        if hold_history:
            assessment = stage_iteration_assessment(
                hold_history,
                args,
                residual_targets,
            )
            result["assessment_window"] = "final_blending_hold_phase"
        else:
            assessment = stage_iteration_assessment(history, args, residual_targets)
            result["assessment_window"] = "momentum_restore_ramp_history"
        result["hold_assessment"] = assessment if hold_history else {}
    else:
        history, plateau_result = run_plain_restore_iterations(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_name=f"mixed_order_restore_{stage_label}_stage",
            iterations=iterations,
            residual_targets=residual_targets,
        )
        assessment = stage_iteration_assessment(history, args, residual_targets)
        result["assessment_window"] = f"{stage_label}_restore_stage"
    result["history"] = history
    result["plateau"] = plateau_result
    result["assessment"] = assessment
    if assessment.get("strict_converged"):
        result["status"] = f"{stage_prefix}_RESTORE_CONVERGED"
    return result


def build_staged_restore_convergence_json(
    first_order: dict[str, Any],
    pressure: dict[str, Any] | None,
    momentum: dict[str, Any] | None,
    species: dict[str, Any] | None,
    classification: dict[str, Any],
    hotr_result: dict[str, Any],
    blending_result: dict[str, Any],
    final_discretization: dict[str, Any],
) -> str:
    def compact(stage: dict[str, Any] | None) -> dict[str, Any] | None:
        if stage is None:
            return None
        return {key: value for key, value in stage.items() if key != "history"}

    payload = {
        "first_order_stage": first_order,
        "restore_pressure_stage": compact(pressure),
        "restore_momentum_stage": compact(momentum),
        "restore_species_stage": compact(species),
        "high_order_term_relaxation": hotr_result,
        "second_order_blending": blending_result,
        "final_discretization": final_discretization,
        "classification": classification,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def build_mixed_order_convergence_json(
    first_order: dict[str, Any],
    pressure: dict[str, Any] | None,
    momentum: dict[str, Any] | None,
    classification: dict[str, Any],
    final_discretization: dict[str, Any],
    hotr_result: dict[str, Any],
    blending_result: dict[str, Any],
) -> str:
    def compact(stage: dict[str, Any] | None) -> dict[str, Any] | None:
        if stage is None:
            return None
        return {key: value for key, value in stage.items() if key != "history"}

    payload = {
        "strategy": "flow_second_order_species_first_order",
        "mixed_order": True,
        "full_second_order_attempted": False,
        "final_targets": MIXED_ORDER_FINAL_TARGETS,
        "first_order_stage": first_order,
        "restore_pressure_stage": compact(pressure),
        "restore_momentum_stage": compact(momentum),
        "restore_species_stage": {
            "status": "SKIPPED",
            "reason": MIXED_ORDER_SPECIES_SKIP_REASON,
        },
        "high_order_term_relaxation": hotr_result,
        "second_order_blending": blending_result,
        "final_discretization": final_discretization,
        "classification": classification,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def execute_staged_second_order_restore(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    result["staged_restore_enabled"] = True
    result["final_assessment_window"] = "staged_second_order_restore"
    first_order_state = result["strategy_settings"]
    original_state = first_order_state.get("discretization_before", {})

    history: list[dict[str, Any]] = []
    first_order_history, first_order_plateau = run_iteration_chunks(
        solver=solver,
        total_iterations=args.additional_iterations,
        chunk_size=args.iteration_chunk_size,
        transcript_path=transcript_path,
        species_name=args.species_residual_name,
        strict_targets=result["residual_targets"],
        plateau_window_chunks=args.plateau_window_chunks,
        plateau_rel_change_tol=args.plateau_rel_change_tol,
        plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
        stage_name="staged_first_order_stage",
        early_stop_on_strict_residual=True,
    )
    history.extend(first_order_history)
    result["history"] = history
    result["plateau_assessment"] = first_order_plateau
    first_order_assessment = stage_iteration_assessment(
        first_order_history,
        args,
        result["residual_targets"],
    )
    result["first_order_stage_residual_latest"] = first_order_assessment[
        "latest_residuals"
    ]
    result["first_order_stage_report_values"] = first_order_assessment[
        "latest_reports"
    ]
    result["residual_latest"] = first_order_assessment["latest_residuals"]
    result["residual_assessment"] = first_order_assessment["residual"]

    if not first_order_assessment["residual"].get("strict_met"):
        classification = classify_convergence(
            first_order_assessment["monitor"],
            first_order_assessment["residual"],
            first_order_plateau,
        )
        result["strategy_stage_status"] = classification["status"]
        result["limiting_restore_stage"] = "first_order"
        result["final_assessment_reason"] = (
            "First-order stage did not meet the strict residual target; staged "
            "second-order restore was not attempted."
        )
        result["convergence_assessment"] = build_staged_restore_convergence_json(
            first_order=first_order_assessment,
            pressure=None,
            momentum=None,
            species=None,
            classification=classification,
            hotr_result={},
            blending_result={},
            final_discretization={},
        )
        return result

    hotr_result = apply_high_order_term_relaxation(
        solver,
        bool(args.use_high_order_term_relaxation),
    )
    result["high_order_term_relaxation_before"] = hotr_result.get("before", "")
    result["high_order_term_relaxation_after"] = hotr_result.get("after", "")
    result["high_order_term_relaxation_apply_status"] = hotr_result.get("status", "")

    blending_result = prepare_second_order_blending(solver, args)
    result["second_order_blending_before"] = blending_result.get("before", "")
    result["second_order_blending_after"] = blending_result.get("after", "")
    result["second_order_blending_apply_status"] = blending_result.get("status", "")

    stage_results: dict[str, dict[str, Any]] = {}
    stage_iterations = {
        "pressure": args.restore_pressure_iterations,
        "momentum": args.restore_momentum_iterations,
        "species": args.restore_species_iterations,
    }
    final_status_by_stage = {
        "pressure": "PRESSURE_RESTORE_NOT_CONVERGED",
        "momentum": "MOMENTUM_RESTORE_NOT_CONVERGED",
        "species": "SPECIES_RESTORE_NOT_CONVERGED",
    }

    for stage_label in ("pressure", "momentum", "species"):
        stage_result = run_staged_restore_component(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_label=stage_label,
            original_state=original_state,
            iterations=stage_iterations[stage_label],
            residual_targets=result["residual_targets"],
            blending_result=blending_result if stage_label == "species" else None,
        )
        stage_results[stage_label] = stage_result
        history.extend(stage_result.get("history", []))
        result["history"] = history
        result["plateau_assessment"] = stage_result.get("plateau", {})
        result[f"restore_{stage_label}_status"] = stage_result.get("status", "")
        assessment = stage_result.get("assessment", {})
        result[f"restore_{stage_label}_residual_latest"] = assessment.get(
            "latest_residuals",
            {},
        )
        result[f"restore_{stage_label}_report_values"] = assessment.get(
            "latest_reports",
            {},
        )
        result["residual_latest"] = assessment.get("latest_residuals", {})
        result["residual_assessment"] = assessment.get("residual", {})
        result["monitor_assessment"] = assessment.get("monitor", {})
        if stage_label == "species" and "blending_result" in stage_result:
            blending_result = stage_result["blending_result"]
            result["second_order_blending_after"] = blending_result.get("after", "")
            result["second_order_blending_apply_status"] = blending_result.get(
                "status",
                "",
            )

        if stage_result.get("status") != f"{stage_label.upper()}_RESTORE_CONVERGED":
            final_status = final_status_by_stage[stage_label]
            final_discretization_for_failure: dict[str, Any] = {}
            final_second_order_for_failure = False
            if stage_label == "species":
                try:
                    final_discretization_for_failure = read_discretization_settings(solver)
                except Exception as exc:
                    final_discretization_for_failure = {
                        "error": f"{type(exc).__name__}: {exc}"
                    }
                result["discretization_after_restore"] = json_field(
                    final_discretization_for_failure
                )
                final_second_order_for_failure = (
                    isinstance(final_discretization_for_failure, dict)
                    and full_second_order_discretization_confirmed(
                        final_discretization_for_failure
                    )
                )
                result["discretization_restore_status"] = (
                    "RESTORE_CONFIRMED"
                    if final_second_order_for_failure
                    else "RESTORE_NOT_CONFIRMED"
                )
            if (
                stage_label == "species"
                and final_second_order_for_failure
                and residual_equation_above_target(
                    assessment.get("residual", {}),
                    args.species_residual_name,
                )
            ):
                final_status = "SECOND_ORDER_SPECIES_RESIDUAL_PLATEAU"
            classification = {
                "status": final_status,
                "details": (
                    f"{stage_label} restore did not meet strict residual and "
                    "report stability criteria."
                ),
            }
            result["strategy_stage_status"] = final_status
            result["limiting_restore_stage"] = stage_label
            result["final_assessment_reason"] = classification["details"]
            result["convergence_assessment"] = build_staged_restore_convergence_json(
                first_order=first_order_assessment,
                pressure=stage_results.get("pressure"),
                momentum=stage_results.get("momentum"),
                species=stage_results.get("species"),
                classification=classification,
                hotr_result=hotr_result,
                blending_result=blending_result,
                final_discretization=final_discretization_for_failure,
            )
            return result

    try:
        final_discretization = read_discretization_settings(solver)
    except Exception as exc:
        final_discretization = {"error": f"{type(exc).__name__}: {exc}"}
    result["discretization_after_restore"] = json_field(final_discretization)
    final_second_order = (
        isinstance(final_discretization, dict)
        and full_second_order_discretization_confirmed(final_discretization)
    )
    result["discretization_restore_status"] = (
        "RESTORE_CONFIRMED" if final_second_order else "RESTORE_NOT_CONFIRMED"
    )

    species_assessment = stage_results["species"].get("assessment", {})
    staged_strict = bool(
        final_second_order
        and species_assessment.get("strict_converged")
        and all(
            stage_results[label].get("status") == f"{label.upper()}_RESTORE_CONVERGED"
            for label in ("pressure", "momentum", "species")
        )
    )
    result["staged_restore_strict_converged"] = staged_strict
    if staged_strict:
        classification = {
            "status": "STRICT_CONVERGED_ATTEMPT",
            "details": (
                "Pressure, momentum, and species restored to second order with "
                "strict residual target met and stable report monitors."
            ),
        }
    elif residual_equation_above_target(
        species_assessment.get("residual", {}),
        args.species_residual_name,
    ):
        classification = {
            "status": "SECOND_ORDER_SPECIES_RESIDUAL_PLATEAU",
            "details": "Species residual remains above target after full restore.",
        }
    else:
        classification = {
            "status": "SPECIES_RESTORE_NOT_CONVERGED",
            "details": (
                "Full second-order readback or final species-stage strict "
                "convergence was not confirmed."
            ),
        }

    result["strategy_stage_status"] = classification["status"]
    result["limiting_restore_stage"] = "" if staged_strict else "species"
    result["final_assessment_reason"] = classification["details"]
    result["convergence_assessment"] = build_staged_restore_convergence_json(
        first_order=first_order_assessment,
        pressure=stage_results.get("pressure"),
        momentum=stage_results.get("momentum"),
        species=stage_results.get("species"),
        classification=classification,
        hotr_result=hotr_result,
        blending_result=blending_result,
        final_discretization=final_discretization,
    )
    return result


def execute_flow_second_order_species_first_order(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    result["flow_second_order_species_first_order_enabled"] = True
    result["species_second_order_skipped_reason"] = MIXED_ORDER_SPECIES_SKIP_REASON
    result["restore_species_status"] = "SKIPPED_MIXED_ORDER_SPECIES_FIRST_ORDER"
    result["final_assessment_window"] = "flow_second_order_species_first_order"
    first_order_state = result["strategy_settings"]
    original_state = first_order_state.get("discretization_before", {})

    history: list[dict[str, Any]] = []
    first_order_history, first_order_plateau = run_iteration_chunks(
        solver=solver,
        total_iterations=args.additional_iterations,
        chunk_size=args.iteration_chunk_size,
        transcript_path=transcript_path,
        species_name=args.species_residual_name,
        strict_targets=result["residual_targets"],
        plateau_window_chunks=args.plateau_window_chunks,
        plateau_rel_change_tol=args.plateau_rel_change_tol,
        plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
        stage_name="mixed_order_first_order_stage",
        early_stop_on_strict_residual=True,
    )
    history.extend(first_order_history)
    result["history"] = history
    result["plateau_assessment"] = first_order_plateau
    first_order_assessment = stage_iteration_assessment(
        first_order_history,
        args,
        result["residual_targets"],
    )
    result["first_order_stage_residual_latest"] = first_order_assessment[
        "latest_residuals"
    ]
    result["first_order_stage_report_values"] = first_order_assessment[
        "latest_reports"
    ]
    result["residual_latest"] = first_order_assessment["latest_residuals"]
    result["residual_assessment"] = first_order_assessment["residual"]

    if not first_order_assessment["residual"].get("strict_met"):
        classification = classify_convergence(
            first_order_assessment["monitor"],
            first_order_assessment["residual"],
            first_order_plateau,
        )
        result["strategy_stage_status"] = classification["status"]
        result["limiting_restore_stage"] = "first_order"
        result["final_assessment_reason"] = (
            "Mixed-order first stage did not meet the strict residual target; "
            "pressure and momentum restore were not attempted."
        )
        skipped_hotr_result = {
            "status": "SKIPPED_FIRST_ORDER_STAGE_NOT_CONVERGED",
            "before": "",
            "after": "",
            "errors": [],
        }
        skipped_blending_result = {
            "status": "SKIPPED_FIRST_ORDER_STAGE_NOT_CONVERGED",
            "before": "",
            "after": "",
            "editable": False,
            "steps": [],
            "errors": [],
        }
        result["high_order_term_relaxation_before"] = skipped_hotr_result["before"]
        result["high_order_term_relaxation_after"] = skipped_hotr_result["after"]
        result["high_order_term_relaxation_apply_status"] = skipped_hotr_result["status"]
        result["second_order_blending_before"] = skipped_blending_result["before"]
        result["second_order_blending_after"] = skipped_blending_result["after"]
        result["second_order_blending_apply_status"] = skipped_blending_result["status"]
        result["convergence_assessment"] = build_mixed_order_convergence_json(
            first_order=first_order_assessment,
            pressure=None,
            momentum=None,
            classification=classification,
            final_discretization={},
            hotr_result=skipped_hotr_result,
            blending_result=skipped_blending_result,
        )
        return result

    hotr_result = apply_high_order_term_relaxation(
        solver,
        bool(args.use_high_order_term_relaxation),
    )
    result["high_order_term_relaxation_before"] = hotr_result.get("before", "")
    result["high_order_term_relaxation_after"] = hotr_result.get("after", "")
    result["high_order_term_relaxation_apply_status"] = hotr_result.get("status", "")

    blending_result = prepare_second_order_blending(solver, args)
    result["second_order_blending_before"] = blending_result.get("before", "")
    result["second_order_blending_after"] = blending_result.get("after", "")
    result["second_order_blending_apply_status"] = blending_result.get("status", "")

    stage_results: dict[str, dict[str, Any]] = {}
    stage_iterations = {
        "pressure": args.restore_pressure_iterations,
        "momentum": args.restore_momentum_iterations,
    }
    final_status_by_stage = {
        "pressure": "PRESSURE_RESTORE_NOT_CONVERGED",
        "momentum": "MOMENTUM_RESTORE_NOT_CONVERGED",
    }

    for stage_label in ("pressure", "momentum"):
        stage_result = run_mixed_order_restore_component(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            stage_label=stage_label,
            original_state=original_state,
            iterations=stage_iterations[stage_label],
            residual_targets=result["residual_targets"],
            blending_result=blending_result if stage_label == "momentum" else None,
        )
        stage_results[stage_label] = stage_result
        history.extend(stage_result.get("history", []))
        result["history"] = history
        result["plateau_assessment"] = stage_result.get("plateau", {})
        result[f"restore_{stage_label}_status"] = stage_result.get("status", "")
        assessment = stage_result.get("assessment", {})
        result[f"restore_{stage_label}_residual_latest"] = assessment.get(
            "latest_residuals",
            {},
        )
        result[f"restore_{stage_label}_report_values"] = assessment.get(
            "latest_reports",
            {},
        )
        result["residual_latest"] = assessment.get("latest_residuals", {})
        result["residual_assessment"] = assessment.get("residual", {})
        result["monitor_assessment"] = assessment.get("monitor", {})
        result["mixed_order_residual_latest"] = assessment.get("latest_residuals", {})
        result["mixed_order_report_values"] = assessment.get("latest_reports", {})

        if stage_label == "momentum" and "blending_result" in stage_result:
            blending_result = stage_result["blending_result"]
            result["second_order_blending_after"] = blending_result.get("after", "")
            result["second_order_blending_apply_status"] = blending_result.get(
                "status",
                "",
            )

        if stage_label == "momentum":
            result["momentum_restore_assessment_window"] = stage_result.get(
                "assessment_window",
                "",
            )
            result["blending_ramp_iterations"] = stage_result.get(
                "blending_ramp_iterations",
                0,
            )
            result["blending_hold_iterations"] = stage_result.get(
                "blending_hold_iterations",
                0,
            )
            hold_assessment = stage_result.get("hold_assessment") or {}
            if hold_assessment:
                result["blending_hold_residual_latest"] = hold_assessment.get(
                    "latest_residuals",
                    {},
                )
                result["blending_hold_report_values"] = hold_assessment.get(
                    "latest_reports",
                    {},
                )
                result["blending_hold_monitor_assessment"] = hold_assessment.get(
                    "monitor",
                    {},
                )
                result["blending_hold_residual_target_met"] = bool(
                    hold_assessment.get("residual", {}).get("strict_met")
                )
                result["blending_hold_strict_converged"] = bool(
                    hold_assessment.get("strict_converged")
                )

        if stage_result.get("status") != f"{stage_label.upper()}_RESTORE_CONVERGED":
            try:
                final_discretization = read_discretization_settings(solver)
            except Exception as exc:
                final_discretization = {"error": f"{type(exc).__name__}: {exc}"}
            result["final_discretization_readback"] = final_discretization
            result["discretization_after_restore"] = json_field(final_discretization)
            result["final_species_scheme"] = str(final_discretization.get("species-0", ""))
            final_mixed_order = mixed_order_discretization_confirmed(final_discretization)
            result["final_flow_scheme_status"] = (
                "MIXED_ORDER_CONFIRMED"
                if final_mixed_order
                else "MIXED_ORDER_NOT_CONFIRMED"
            )
            result["discretization_restore_status"] = (
                "RESTORE_CONFIRMED" if final_mixed_order else "RESTORE_NOT_CONFIRMED"
            )
            classification = {
                "status": final_status_by_stage[stage_label],
                "details": (
                    f"Mixed-order {stage_label} restore did not meet strict "
                    "residual and report stability criteria."
                ),
            }
            result["strategy_stage_status"] = classification["status"]
            result["limiting_restore_stage"] = stage_label
            result["final_assessment_reason"] = classification["details"]
            result["convergence_assessment"] = build_mixed_order_convergence_json(
                first_order=first_order_assessment,
                pressure=stage_results.get("pressure"),
                momentum=stage_results.get("momentum"),
                classification=classification,
                final_discretization=final_discretization,
                hotr_result=hotr_result,
                blending_result=blending_result,
            )
            return result

    try:
        final_discretization = read_discretization_settings(solver)
    except Exception as exc:
        final_discretization = {"error": f"{type(exc).__name__}: {exc}"}
    result["final_discretization_readback"] = final_discretization
    result["discretization_after_restore"] = json_field(final_discretization)
    result["final_species_scheme"] = str(final_discretization.get("species-0", ""))
    final_mixed_order = mixed_order_discretization_confirmed(final_discretization)
    result["final_flow_scheme_status"] = (
        "MIXED_ORDER_CONFIRMED" if final_mixed_order else "MIXED_ORDER_NOT_CONFIRMED"
    )
    result["discretization_restore_status"] = (
        "RESTORE_CONFIRMED" if final_mixed_order else "RESTORE_NOT_CONFIRMED"
    )

    momentum_assessment = stage_results["momentum"].get("assessment", {})
    mixed_order_strict = bool(
        final_mixed_order
        and momentum_assessment.get("strict_converged")
        and all(
            stage_results[label].get("status") == f"{label.upper()}_RESTORE_CONVERGED"
            for label in ("pressure", "momentum")
        )
    )
    result["mixed_order_strict_converged"] = mixed_order_strict
    result["mixed_order_residual_latest"] = momentum_assessment.get(
        "latest_residuals",
        {},
    )
    result["mixed_order_report_values"] = momentum_assessment.get("latest_reports", {})

    if mixed_order_strict:
        classification = {
            "status": "STRICT_CONVERGED_ATTEMPT",
            "details": (
                "Mixed-order attempt confirmed pressure second-order, momentum "
                "second-order-upwind, and species-0 first-order-upwind with strict "
                "residual target met and stable report monitors."
            ),
        }
    elif not final_mixed_order:
        classification = {
            "status": "MIXED_ORDER_DISCRETIZATION_NOT_CONFIRMED",
            "details": (
                "Final mixed-order readback did not confirm pressure second-order, "
                "momentum second-order-upwind, and species-0 first-order-upwind."
            ),
        }
    else:
        classification = {
            "status": "MOMENTUM_RESTORE_NOT_CONVERGED",
            "details": (
                "Mixed-order final readback was confirmed, but the final momentum "
                "stage did not meet strict residual/report criteria."
            ),
        }

    result["strategy_stage_status"] = classification["status"]
    result["limiting_restore_stage"] = "" if mixed_order_strict else "mixed_order"
    result["final_assessment_reason"] = classification["details"]
    result["convergence_assessment"] = build_mixed_order_convergence_json(
        first_order=first_order_assessment,
        pressure=stage_results.get("pressure"),
        momentum=stage_results.get("momentum"),
        classification=classification,
        final_discretization=final_discretization,
        hotr_result=hotr_result,
        blending_result=blending_result,
    )
    return result


def execute_solver_strategy(
    solver: Any,
    args: argparse.Namespace,
    transcript_path: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "strategy_stage_status": "NOT_STARTED",
        "first_order_apply_status": "",
        "discretization_before": "",
        "discretization_after_first_order": "",
        "discretization_first_order_readback": "",
        "discretization_restore_status": "",
        "discretization_after_restore": "",
        "first_order_error_summary": "",
        "convergence_assessment": "",
        "diagnostics": {},
        "history": [],
        "monitor_assessment": {},
        "residual_assessment": {},
        "plateau_assessment": {},
        "residual_targets": {},
        "post_restore_polish_iterations": args.post_restore_polish_iterations,
        "post_restore_residual_latest": {},
        "post_restore_residual_target_met": False,
        "post_restore_report_values": {},
        "post_restore_monitor_assessment": {},
        "post_restore_strict_converged": False,
        "first_order_stage_residual_latest": {},
        "first_order_stage_report_values": {},
        "final_assessment_window": "",
        "final_assessment_reason": "",
        "staged_restore_enabled": args.solver_strategy == "staged_second_order_restore",
        "flow_second_order_species_first_order_enabled": (
            args.solver_strategy == "flow_second_order_species_first_order"
        ),
        "final_species_scheme": "",
        "final_flow_scheme_status": "",
        "species_second_order_skipped_reason": (
            MIXED_ORDER_SPECIES_SKIP_REASON
            if args.solver_strategy == "flow_second_order_species_first_order"
            else ""
        ),
        "mixed_order_strict_converged": False,
        "mixed_order_residual_latest": {},
        "mixed_order_report_values": {},
        "final_discretization_readback": {},
        "high_order_term_relaxation_before": "",
        "high_order_term_relaxation_after": "",
        "high_order_term_relaxation_apply_status": "",
        "second_order_blending_before": "",
        "second_order_blending_after": "",
        "second_order_blending_apply_status": "",
        "blending_ramp_iterations": 0,
        "blending_hold_iterations": 0,
        "blending_hold_residual_latest": {},
        "blending_hold_report_values": {},
        "blending_hold_monitor_assessment": {},
        "blending_hold_residual_target_met": False,
        "blending_hold_strict_converged": False,
        "momentum_restore_assessment_window": "",
        "restore_pressure_status": "",
        "restore_pressure_residual_latest": {},
        "restore_pressure_report_values": {},
        "restore_momentum_status": "",
        "restore_momentum_residual_latest": {},
        "restore_momentum_report_values": {},
        "restore_species_status": "",
        "restore_species_residual_latest": {},
        "restore_species_report_values": {},
        "limiting_restore_stage": "",
        "staged_restore_strict_converged": False,
    }
    diagnostics = collect_solver_diagnostics(solver)
    result["diagnostics"] = diagnostics

    if args.solver_strategy == "diagnose_only":
        result["strategy_stage_status"] = "DIAGNOSE_ONLY_COMPLETED"
        result["convergence_assessment"] = "DIAGNOSE_ONLY_NO_ITERATION"
        return result

    try:
        residual_targets, relaxation_result = apply_continuation_settings(solver=solver, args=args)
        result["residual_targets"] = residual_targets
        result["residual_target_effective"] = json.dumps(residual_targets, sort_keys=True, default=str)
        relaxation_summary = summarize_relaxation_result(relaxation_result)
        result["relaxation_apply_status"] = relaxation_summary["apply_status"]
        result["relaxation_before"] = relaxation_summary["before"]
        result["relaxation_after"] = relaxation_summary["after"]
        if args.solver_strategy == "damped_steady":
            result["strategy_settings"] = {"status": "DAMPED_STEADY_SETTINGS_APPLIED"}
        elif args.solver_strategy == "pseudo_transient_ramp":
            result["strategy_settings"] = apply_pseudo_transient_ramp(solver, args)
        elif args.solver_strategy in {
            "first_order_ramp",
            "staged_second_order_restore",
            "flow_second_order_species_first_order",
        }:
            if args.solver_strategy == "flow_second_order_species_first_order":
                result["strategy_settings"] = apply_mixed_order_first_stage(solver)
            else:
                result["strategy_settings"] = apply_first_order_ramp(solver)
            first_order_state = result["strategy_settings"]
            result["first_order_apply_status"] = str(first_order_state.get("status", ""))
            result["discretization_before"] = json_field(
                first_order_state.get("discretization_before", {})
            )
            result["discretization_after_first_order"] = json_field(
                first_order_state.get("discretization_after_first_order", {})
            )
            result["discretization_first_order_readback"] = json_field(
                first_order_state.get("discretization_first_order_readback", {})
            )
            result["first_order_error_summary"] = str(
                first_order_state.get("first_order_error_summary", "")
            )
            if (
                result["first_order_apply_status"] not in FIRST_ORDER_CONFIRMED_STATUSES
                and not args.allow_iterate_after_ramp_failure
            ):
                restore_result = restore_first_order_ramp(solver, first_order_state)
                result["discretization_restore_status"] = str(
                    restore_result.get("status", "")
                )
                result["discretization_after_restore"] = json_field(
                    restore_result.get("discretization_after_restore", {})
                )
                restore_errors = "; ".join(str(error) for error in restore_result.get("errors", []))
                if restore_errors:
                    result["first_order_error_summary"] = "; ".join(
                        part
                        for part in [
                            result["first_order_error_summary"],
                            f"restore: {restore_errors}",
                        ]
                        if part
                    )
                result["strategy_stage_status"] = (
                    result["first_order_apply_status"]
                    if result["first_order_apply_status"] in FIRST_ORDER_STOP_STATUSES
                    else "FIRST_ORDER_SWITCH_NOT_CONFIRMED"
                )
                result["convergence_assessment"] = json_field(
                    {
                        "classification": result["strategy_stage_status"],
                        "first_order_apply_status": result["first_order_apply_status"],
                        "details": (
                            "First-order ramp was not confirmed by discretization "
                            "readback; iteration was not started."
                        ),
                    }
                )
                return result
        else:
            result["strategy_settings"] = {"status": "CONTINUE_ONLY"}
    except Exception as exc:
        raise FluentStageError(
            status="FAILED_APPLY_STRATEGY",
            failure_stage="apply_strategy",
            message=f"Could not apply solver strategy {args.solver_strategy}: {exc}",
            original_exception=exc,
        ) from exc

    if args.solver_strategy == "staged_second_order_restore":
        return execute_staged_second_order_restore(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            result=result,
        )
    if args.solver_strategy == "flow_second_order_species_first_order":
        return execute_flow_second_order_species_first_order(
            solver=solver,
            args=args,
            transcript_path=transcript_path,
            result=result,
        )

    history, plateau_assessment = run_iteration_chunks(
        solver=solver,
        total_iterations=args.additional_iterations,
        chunk_size=args.iteration_chunk_size,
        transcript_path=transcript_path,
        species_name=args.species_residual_name,
        strict_targets=result["residual_targets"],
        plateau_window_chunks=args.plateau_window_chunks,
        plateau_rel_change_tol=args.plateau_rel_change_tol,
        plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
        stage_name=(
            "first_order_ramp_stage"
            if args.solver_strategy == "first_order_ramp"
            else "solver_stage"
        ),
    )
    result["history"] = history
    result["plateau_assessment"] = plateau_assessment

    latest_residuals: dict[str, float] = latest_snapshot_group(history, "residual_numeric")
    result["residual_latest"] = latest_residuals
    residual_assessment = assess_residual_convergence(
        latest_residuals=latest_residuals,
        strict_targets=result["residual_targets"],
    )
    result["residual_assessment"] = residual_assessment

    if args.solver_strategy == "first_order_ramp":
        result["first_order_stage_residual_latest"] = latest_residuals
        result["first_order_stage_report_values"] = latest_snapshot_group(history, "report_values")
        first_order_report_assessment = assess_history(
            history,
            args,
            value_groups=("report_values",),
            require_two_samples=True,
        )
        fallback_window, fallback_reason = history_after_first_strict_residual_met(
            history=history,
            strict_targets=result["residual_targets"],
            max_snapshots=max(2, args.plateau_window_chunks),
        )
        fallback_monitor_assessment = assess_history(
            fallback_window,
            args,
            value_groups=("report_values",),
            require_two_samples=True,
        )

        restore_result = restore_first_order_ramp(solver, result.get("strategy_settings", {}))
        result["discretization_restore_status"] = str(restore_result.get("status", ""))
        result["discretization_after_restore"] = json_field(
            restore_result.get("discretization_after_restore", {})
        )
        restore_errors = "; ".join(str(error) for error in restore_result.get("errors", []))
        if restore_errors:
            result["first_order_error_summary"] = "; ".join(
                part
                for part in [
                    result.get("first_order_error_summary", ""),
                    f"restore: {restore_errors}",
                ]
                if part
            )
        if restore_result.get("status") != "RESTORE_CONFIRMED":
            result["strategy_stage_status"] = "NEEDS_MANUAL_REVIEW"
            result["convergence_assessment"] = (
                "First-order ramp completed, but original discretization restore "
                "was not verified."
            )
            result["monitor_assessment"] = fallback_monitor_assessment
            result["final_assessment_window"] = "first_order_ramp_stage"
            result["final_assessment_reason"] = (
                "Original second-order discretization restore was not confirmed; "
                "post-restore polish was not run."
            )
            return result

        if args.post_restore_polish_iterations > 0:
            post_restore_history, post_restore_plateau_assessment = run_iteration_chunks(
                solver=solver,
                total_iterations=args.post_restore_polish_iterations,
                chunk_size=args.iteration_chunk_size,
                transcript_path=transcript_path,
                species_name=args.species_residual_name,
                strict_targets=result["residual_targets"],
                plateau_window_chunks=args.plateau_window_chunks,
                plateau_rel_change_tol=args.plateau_rel_change_tol,
                plateau_min_above_target_factor=args.plateau_min_residual_above_target_factor,
                stage_name="post_restore_second_order_stage",
                early_stop_on_strict_stable=True,
                assessment_args=args,
                monitor_value_groups=("report_values",),
                monitor_require_two_samples=True,
            )
            result["history"] = history + post_restore_history
            result["plateau_assessment"] = post_restore_plateau_assessment

            post_restore_residuals = latest_snapshot_group(
                post_restore_history,
                "residual_numeric",
            )
            post_restore_reports = latest_snapshot_group(
                post_restore_history,
                "report_values",
            )
            post_restore_residual_assessment = assess_residual_convergence(
                latest_residuals=post_restore_residuals,
                strict_targets=result["residual_targets"],
            )
            post_restore_report_assessment = assess_history(
                post_restore_history,
                args,
                value_groups=("report_values",),
                require_two_samples=True,
            )
            post_restore_all_assessment = assess_history(post_restore_history, args)

            result["post_restore_residual_latest"] = post_restore_residuals
            result["post_restore_residual_target_met"] = bool(
                post_restore_residual_assessment.get("strict_met")
            )
            result["post_restore_report_values"] = post_restore_reports
            result["post_restore_monitor_assessment"] = post_restore_report_assessment
            result["monitor_assessment"] = post_restore_report_assessment
            result["residual_latest"] = post_restore_residuals
            result["residual_assessment"] = post_restore_residual_assessment
            result["final_assessment_window"] = "post_restore_second_order_stage"
            result["final_assessment_reason"] = (
                "Post-restore polish ran after RESTORE_CONFIRMED; final monitor "
                "classification excludes the intentional first-order ramp transient."
            )

            first_order_fully_confirmed = (
                result.get("first_order_apply_status") == "FIRST_ORDER_APPLIED_CONFIRMED"
            )
            restore_confirmed = (
                result.get("discretization_restore_status") == "RESTORE_CONFIRMED"
            )
            post_restore_diverged = bool(
                post_restore_report_assessment.get("diverged")
                or post_restore_all_assessment.get("diverged")
            )
            post_restore_strict_converged = bool(
                first_order_fully_confirmed
                and restore_confirmed
                and post_restore_residual_assessment.get("strict_met")
                and post_restore_report_assessment.get("stable")
                and not post_restore_diverged
            )
            result["post_restore_strict_converged"] = post_restore_strict_converged

            if post_restore_diverged:
                classification = {
                    "status": "DIVERGED",
                    "details": (
                        post_restore_report_assessment.get("details")
                        or post_restore_all_assessment.get("details")
                        or "Post-restore second-order monitors diverged."
                    ),
                }
            elif not post_restore_residual_assessment.get("strict_met"):
                classification = {
                    "status": "SECOND_ORDER_POST_RESTORE_NOT_CONVERGED",
                    "details": (
                        "Post-restore second-order residuals did not meet the "
                        "requested strict residual target."
                    ),
                }
            elif not post_restore_report_assessment.get("stable"):
                classification = {
                    "status": "SECOND_ORDER_POST_RESTORE_MONITOR_UNSTABLE",
                    "details": (
                        "Post-restore second-order report monitors were not stable "
                        "within monitor_rel_tol."
                    ),
                }
            elif not first_order_fully_confirmed or not restore_confirmed:
                classification = {
                    "status": "NEEDS_MANUAL_REVIEW",
                    "details": (
                        "Post-restore strict convergence was met, but "
                        "FIRST_ORDER_APPLIED_CONFIRMED and RESTORE_CONFIRMED are "
                        "required before success classification."
                    ),
                }
            else:
                classification = {
                    "status": "STRICT_CONVERGED_ATTEMPT",
                    "details": (
                        "Post-restore second-order residual target met and report "
                        "monitors are stable."
                    ),
                }

            result["convergence_assessment"] = json.dumps(
                {
                    "first_order_ramp_stage": {
                        "monitor": first_order_report_assessment,
                        "residual": residual_assessment,
                        "plateau": plateau_assessment,
                    },
                    "post_restore_second_order_stage": {
                        "monitor": post_restore_report_assessment,
                        "monitor_all_numeric": post_restore_all_assessment,
                        "residual": post_restore_residual_assessment,
                        "plateau": post_restore_plateau_assessment,
                    },
                    "final_assessment_window": result["final_assessment_window"],
                    "final_assessment_reason": result["final_assessment_reason"],
                    "classification": classification,
                },
                sort_keys=True,
                default=str,
            )

            if classification["status"] == "DIVERGED":
                raise FluentStageError(
                    status="FAILED_DIVERGED_DURING_RERUN",
                    failure_stage="iterate",
                    message=str(
                        classification.get(
                            "details",
                            "Post-restore residual/report monitors diverged.",
                        )
                    ),
                )

            result["strategy_stage_status"] = classification["status"]
            return result

        result["monitor_assessment"] = fallback_monitor_assessment
        result["final_assessment_window"] = "first_order_ramp_stage_after_strict_residual"
        result["final_assessment_reason"] = (
            f"{fallback_reason} Post-restore polish was not run, so final "
            "first_order_ramp success is intentionally withheld."
        )
        if residual_assessment.get("strict_met"):
            classification = {
                "status": "FIRST_ORDER_CONVERGED_RESTORE_CONFIRMED_NEEDS_POST_RESTORE_POLISH",
                "details": (
                    "First-order residuals met the strict target and original "
                    "second-order discretization was restored, but no post-restore "
                    "second-order polish was run."
                ),
            }
        else:
            classification = classify_convergence(
                fallback_monitor_assessment,
                residual_assessment,
                plateau_assessment,
            )

        result["convergence_assessment"] = json.dumps(
            {
                "first_order_ramp_stage": {
                    "monitor": first_order_report_assessment,
                    "final_window_monitor": fallback_monitor_assessment,
                    "residual": residual_assessment,
                    "plateau": plateau_assessment,
                },
                "final_assessment_window": result["final_assessment_window"],
                "final_assessment_reason": result["final_assessment_reason"],
                "classification": classification,
            },
            sort_keys=True,
            default=str,
        )

        if classification["status"] == "DIVERGED":
            raise FluentStageError(
                status="FAILED_DIVERGED_DURING_RERUN",
                failure_stage="iterate",
                message=str(classification.get("details", "Residual/report monitors diverged.")),
            )

        result["strategy_stage_status"] = classification["status"]
        return result

    monitor_assessment = assess_history(history, args)
    result["monitor_assessment"] = monitor_assessment

    classification = classify_convergence(monitor_assessment, residual_assessment, plateau_assessment)
    result["convergence_assessment"] = json.dumps(
        {
            "monitor": monitor_assessment,
            "residual": residual_assessment,
            "plateau": plateau_assessment,
            "classification": classification,
        },
        sort_keys=True,
        default=str,
    )

    if classification["status"] == "DIVERGED":
        raise FluentStageError(
            status="FAILED_DIVERGED_DURING_RERUN",
            failure_stage="iterate",
            message=str(classification.get("details", "Residual/report monitors diverged.")),
        )

    result["strategy_stage_status"] = classification["status"]
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
        print(f"post_restore_polish_iterations={args.post_restore_polish_iterations}")
        print(f"restore_pressure_iterations={args.restore_pressure_iterations}")
        print(f"restore_momentum_iterations={args.restore_momentum_iterations}")
        print(f"restore_species_iterations={args.restore_species_iterations}")
        print(f"use_high_order_term_relaxation={args.use_high_order_term_relaxation}")
        print(f"second_order_blending_start={args.second_order_blending_start}")
        print(f"second_order_blending_end={args.second_order_blending_end}")
        print(f"second_order_blending_steps={args.second_order_blending_steps}")
        print(f"blending_hold_iterations={args.blending_hold_iterations}")

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

        strategy_result = execute_solver_strategy(
            solver=solver,
            args=args,
            transcript_path=transcript_path if args.write_transcript else None,
        )
        result.update(strategy_result)

        if args.solver_strategy == "diagnose_only":
            result["rerun_status"] = "COMPLETED_NEEDS_REVIEW"
            return result

        stage_status = strategy_result.get("strategy_stage_status")
        if stage_status in FIRST_ORDER_STOP_STATUSES:
            result["rerun_status"] = str(stage_status)
            result["failure_stage"] = "apply_first_order"
            result["returncode_or_exception"] = (
                result.get("first_order_error_summary")
                or "first-order discretization switch was not confirmed"
            )
            print(
                "Stopping before iteration/save because the first-order "
                f"discretization switch was not confirmed: {stage_status}"
            )
            return result

        strategy_promotion_ready = True
        if args.solver_strategy == "first_order_ramp":
            strategy_promotion_ready = (
                bool(strategy_result.get("post_restore_strict_converged"))
                and strategy_result.get("discretization_restore_status") == "RESTORE_CONFIRMED"
            )
        elif args.solver_strategy == "staged_second_order_restore":
            strategy_promotion_ready = (
                bool(strategy_result.get("staged_restore_strict_converged"))
                and strategy_result.get("discretization_restore_status") == "RESTORE_CONFIRMED"
            )
        elif args.solver_strategy == "flow_second_order_species_first_order":
            strategy_promotion_ready = (
                bool(strategy_result.get("mixed_order_strict_converged"))
                and mixed_order_discretization_confirmed(
                    strategy_result.get("final_discretization_readback", {})
                )
            )

        if stage_status == "STRICT_CONVERGED_ATTEMPT" and strategy_promotion_ready:
            # Only strict-target convergence with stable monitors is treated as
            # success; never mark success on a merely stable-but-relaxed result.
            result["rerun_status"] = (
                "SUCCESS_ATTEMPT_ONLY"
                if not args.promote_on_success
                else "SUCCESS_READY_TO_PROMOTE"
            )
        elif stage_status == "STRICT_CONVERGED_ATTEMPT":
            result["rerun_status"] = "NEEDS_MANUAL_REVIEW"
            result["strategy_stage_status"] = "NEEDS_MANUAL_REVIEW"
        elif stage_status in {
            "NOT_CONVERGED_STABLE_MONITORS",
            "NOT_CONVERGED_RESIDUAL_PLATEAU",
            "NEEDS_TRANSIENT_REVIEW",
            "NEEDS_MANUAL_REVIEW",
            "SECOND_ORDER_POST_RESTORE_NOT_CONVERGED",
            "SECOND_ORDER_POST_RESTORE_MONITOR_UNSTABLE",
            "FIRST_ORDER_CONVERGED_RESTORE_CONFIRMED_NEEDS_POST_RESTORE_POLISH",
            "PRESSURE_RESTORE_NOT_CONVERGED",
            "MOMENTUM_RESTORE_NOT_CONVERGED",
            "SPECIES_RESTORE_NOT_CONVERGED",
            "SECOND_ORDER_SPECIES_RESIDUAL_PLATEAU",
            "MIXED_ORDER_DISCRETIZATION_NOT_CONFIRMED",
        }:
            result["rerun_status"] = stage_status
        else:
            result["rerun_status"] = "ATTEMPT_WRITTEN_NEEDS_REVIEW"

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


def is_failure_status(status: Any) -> bool:
    status_text = str(status)
    return status_text.startswith("FAILED") or status_text in FIRST_ORDER_STOP_STATUSES


def process_case(
    candidate: dict[str, str],
    args: argparse.Namespace,
    prior_successes: set[tuple[str, str]],
    run_timestamp: str,
) -> dict[str, Any]:
    record = make_base_result(candidate, args)
    geo_name = record["geo_name"]
    case_name = record["case_name"]
    case_dir, final_case_file, final_data_file = final_pair_for_candidate(candidate)
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
        print(f"post_restore_polish_iterations={args.post_restore_polish_iterations}")
        print(f"restore_pressure_iterations={args.restore_pressure_iterations}")
        print(f"restore_momentum_iterations={args.restore_momentum_iterations}")
        print(f"restore_species_iterations={args.restore_species_iterations}")
        print(f"use_high_order_term_relaxation={args.use_high_order_term_relaxation}")
        print(f"second_order_blending_start={args.second_order_blending_start}")
        print(f"second_order_blending_end={args.second_order_blending_end}")
        print(f"second_order_blending_steps={args.second_order_blending_steps}")
        print(f"blending_hold_iterations={args.blending_hold_iterations}")
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
                if args.solver_strategy == "flow_second_order_species_first_order":
                    record["high_order_term_relaxation_apply_status"] = "SKIPPED_DRY_RUN"
                    record["second_order_blending_apply_status"] = "SKIPPED_DRY_RUN"
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
                    "Verify the candidate run manifest and RO_DATA_ROOT on the "
                    "Windows server."
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
                for key in [
                    "first_order_apply_status",
                    "discretization_before",
                    "discretization_after_first_order",
                    "discretization_first_order_readback",
                    "discretization_restore_status",
                    "discretization_after_restore",
                    "first_order_error_summary",
                    "final_assessment_window",
                    "final_assessment_reason",
                    "staged_restore_enabled",
                    "flow_second_order_species_first_order_enabled",
                    "final_species_scheme",
                    "final_flow_scheme_status",
                    "species_second_order_skipped_reason",
                    "high_order_term_relaxation_before",
                    "high_order_term_relaxation_after",
                    "high_order_term_relaxation_apply_status",
                    "second_order_blending_before",
                    "second_order_blending_after",
                    "second_order_blending_apply_status",
                    "restore_pressure_status",
                    "restore_momentum_status",
                    "restore_species_status",
                    "limiting_restore_stage",
                    "failure_stage",
                    "returncode_or_exception",
                ]:
                    if key in live_result and live_result.get(key) not in (None, ""):
                        record[key] = str(live_result[key])
                record["post_restore_polish_iterations"] = str(
                    live_result.get(
                        "post_restore_polish_iterations",
                        args.post_restore_polish_iterations,
                    )
                )
                record["staged_restore_enabled"] = str(
                    bool(live_result.get("staged_restore_enabled"))
                ).lower()
                record["flow_second_order_species_first_order_enabled"] = str(
                    bool(
                        live_result.get(
                            "flow_second_order_species_first_order_enabled"
                        )
                    )
                ).lower()
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
                record["residual_target_effective"] = str(
                    live_result.get("residual_target_effective", "")
                )
                record["relaxation_apply_status"] = str(
                    live_result.get("relaxation_apply_status", "")
                )
                record["relaxation_before"] = str(live_result.get("relaxation_before", ""))
                record["relaxation_after"] = str(live_result.get("relaxation_after", ""))
                record["residual_latest"] = json.dumps(
                    live_result.get("residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["first_order_stage_residual_latest"] = json.dumps(
                    live_result.get("first_order_stage_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["first_order_stage_report_values"] = json.dumps(
                    live_result.get("first_order_stage_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["post_restore_residual_latest"] = json.dumps(
                    live_result.get("post_restore_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["post_restore_report_values"] = json.dumps(
                    live_result.get("post_restore_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["post_restore_monitor_assessment"] = json.dumps(
                    live_result.get("post_restore_monitor_assessment", {}),
                    default=str,
                    sort_keys=True,
                )
                record["post_restore_residual_target_met"] = str(
                    bool(live_result.get("post_restore_residual_target_met"))
                ).lower()
                record["post_restore_strict_converged"] = str(
                    bool(live_result.get("post_restore_strict_converged"))
                ).lower()
                record["restore_pressure_residual_latest"] = json.dumps(
                    live_result.get("restore_pressure_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["restore_pressure_report_values"] = json.dumps(
                    live_result.get("restore_pressure_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["restore_momentum_residual_latest"] = json.dumps(
                    live_result.get("restore_momentum_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["restore_momentum_report_values"] = json.dumps(
                    live_result.get("restore_momentum_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["restore_species_residual_latest"] = json.dumps(
                    live_result.get("restore_species_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["restore_species_report_values"] = json.dumps(
                    live_result.get("restore_species_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["staged_restore_strict_converged"] = str(
                    bool(live_result.get("staged_restore_strict_converged"))
                ).lower()
                record["mixed_order_strict_converged"] = str(
                    bool(live_result.get("mixed_order_strict_converged"))
                ).lower()
                record["mixed_order_residual_latest"] = json.dumps(
                    live_result.get("mixed_order_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["mixed_order_report_values"] = json.dumps(
                    live_result.get("mixed_order_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["blending_ramp_iterations"] = str(
                    live_result.get("blending_ramp_iterations", 0)
                )
                record["blending_hold_iterations"] = str(
                    live_result.get("blending_hold_iterations", 0)
                )
                record["blending_hold_residual_latest"] = json.dumps(
                    live_result.get("blending_hold_residual_latest", {}),
                    default=str,
                    sort_keys=True,
                )
                record["blending_hold_report_values"] = json.dumps(
                    live_result.get("blending_hold_report_values", {}),
                    default=str,
                    sort_keys=True,
                )
                record["blending_hold_monitor_assessment"] = json.dumps(
                    live_result.get("blending_hold_monitor_assessment", {}),
                    default=str,
                    sort_keys=True,
                )
                record["blending_hold_residual_target_met"] = str(
                    bool(live_result.get("blending_hold_residual_target_met"))
                ).lower()
                record["blending_hold_strict_converged"] = str(
                    bool(live_result.get("blending_hold_strict_converged"))
                ).lower()
                record["momentum_restore_assessment_window"] = str(
                    live_result.get("momentum_restore_assessment_window", "")
                )
                record["final_discretization_readback"] = json.dumps(
                    live_result.get("final_discretization_readback", {}),
                    default=str,
                    sort_keys=True,
                )
                residual_assessment = live_result.get("residual_assessment", {}) or {}
                record["residual_target_met"] = str(bool(residual_assessment.get("strict_met"))).lower()
                plateau_assessment = live_result.get("plateau_assessment", {}) or {}
                record["plateau_detected"] = str(bool(plateau_assessment.get("detected"))).lower()
                record["plateau_reason"] = str(plateau_assessment.get("reason", ""))
                record["strict_convergence_status"] = str(
                    live_result.get("strategy_stage_status", "")
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

            promotion_allowed = (
                record["strict_convergence_status"] == "STRICT_CONVERGED_ATTEMPT"
                and (
                    args.solver_strategy
                    not in {
                        "first_order_ramp",
                        "staged_second_order_restore",
                        "flow_second_order_species_first_order",
                    }
                    or (
                        args.solver_strategy == "first_order_ramp"
                        and record["post_restore_strict_converged"] == "true"
                        and record["discretization_restore_status"] == "RESTORE_CONFIRMED"
                    )
                    or (
                        args.solver_strategy == "staged_second_order_restore"
                        and record["staged_restore_strict_converged"] == "true"
                        and record["discretization_restore_status"] == "RESTORE_CONFIRMED"
                    )
                    or (
                        args.solver_strategy == "flow_second_order_species_first_order"
                        and record["mixed_order_strict_converged"] == "true"
                        and mixed_order_discretization_confirmed(
                            record["final_discretization_readback"]
                        )
                    )
                )
            )
            if live_status == "SUCCESS_READY_TO_PROMOTE" and not promotion_allowed:
                record["rerun_status"] = "NEEDS_MANUAL_REVIEW"
                record["error_summary"] = (
                    "Promotion blocked because strict convergence, strategy-specific "
                    "strict convergence, or restore confirmation was missing."
                )
                record["suggested_next_action"] = (
                    "Review convergence_assessment before considering manual promotion."
                )
                return record

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
            elif record["rerun_status"] == "NOT_CONVERGED_STABLE_MONITORS":
                record["suggested_next_action"] = (
                    "Report monitors are stable but the strict residual target was not "
                    "met; consider a stronger --relaxation-profile or a longer run "
                    "before treating this as success."
                )
            elif record["rerun_status"] == "NOT_CONVERGED_RESIDUAL_PLATEAU":
                record["suggested_next_action"] = (
                    "One or more residual equations plateaued above the strict target "
                    "with little chunk-to-chunk change; iteration was stopped early. "
                    "Consider --relaxation-profile strong or manual review before "
                    "burning further iterations at these settings."
                )
            elif record["rerun_status"] == "SECOND_ORDER_POST_RESTORE_NOT_CONVERGED":
                record["suggested_next_action"] = (
                    "First-order ramp restored second-order settings, but the "
                    "post-restore second-order polish did not meet the strict "
                    "residual target. Increase --post-restore-polish-iterations or "
                    "review solver settings before promotion."
                )
            elif record["rerun_status"] == "SECOND_ORDER_POST_RESTORE_MONITOR_UNSTABLE":
                record["suggested_next_action"] = (
                    "Post-restore residuals met the strict target, but report "
                    "monitors were not stable within monitor_rel_tol. Review the "
                    "post_restore_monitor_assessment before promotion."
                )
            elif (
                record["rerun_status"]
                == "FIRST_ORDER_CONVERGED_RESTORE_CONFIRMED_NEEDS_POST_RESTORE_POLISH"
            ):
                record["suggested_next_action"] = (
                    "Run first_order_ramp with --post-restore-polish-iterations "
                    "greater than zero so final classification uses restored "
                    "second-order monitor stability."
                )
            elif record["rerun_status"] == "PRESSURE_RESTORE_NOT_CONVERGED":
                record["suggested_next_action"] = (
                    "The staged pressure restore did not meet strict residual and "
                    "report stability criteria. Review restore_pressure_* fields "
                    "before increasing stage iterations."
                )
            elif record["rerun_status"] == "MOMENTUM_RESTORE_NOT_CONVERGED":
                record["suggested_next_action"] = (
                    "Pressure restore passed, but staged momentum restore did not. "
                    "Review restore_momentum_* fields and consider a longer "
                    "--restore-momentum-iterations budget."
                )
            elif record["rerun_status"] == "SPECIES_RESTORE_NOT_CONVERGED":
                record["suggested_next_action"] = (
                    "Pressure and momentum restore passed, but final species "
                    "restore did not meet strict residual/report criteria. Review "
                    "restore_species_* and blending fields."
                )
            elif record["rerun_status"] == "SECOND_ORDER_SPECIES_RESIDUAL_PLATEAU":
                record["suggested_next_action"] = (
                    "The full staged second-order restore completed, but the "
                    "species residual remains above target. Increase "
                    "--restore-species-iterations or review species numerics."
                )
            elif record["rerun_status"] == "MIXED_ORDER_DISCRETIZATION_NOT_CONFIRMED":
                record["suggested_next_action"] = (
                    "The mixed-order strategy reached the final readback check, "
                    "but pressure second-order, momentum second-order-upwind, "
                    "and species-0 first-order-upwind were not all confirmed. "
                    "Do not promote without manual review."
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
            elif record["rerun_status"] in FIRST_ORDER_STOP_STATUSES:
                record["error_summary"] = (
                    record["first_order_error_summary"]
                    or record["returncode_or_exception"]
                    or "First-order ramp was not confirmed by discretization readback."
                )
                record["suggested_next_action"] = (
                    "Inspect first_order_error_summary and the per-case log; "
                    "iteration was not started because first_order_ramp was not "
                    "confirmed."
                )

            if not record["returncode_or_exception"]:
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
        f"  post_restore_polish_iterations: {args.post_restore_polish_iterations}",
        f"  restore_pressure_iterations: {args.restore_pressure_iterations}",
        f"  restore_momentum_iterations: {args.restore_momentum_iterations}",
        f"  restore_species_iterations: {args.restore_species_iterations}",
        f"  solver_strategy: {args.solver_strategy}",
        f"  allow_iterate_after_ramp_failure: {args.allow_iterate_after_ramp_failure}",
        f"  iteration_chunk_size: {args.iteration_chunk_size}",
        f"  residual_target: {args.residual_target}",
        f"  monitor_window: {args.monitor_window}",
        f"  monitor_rel_tol: {args.monitor_rel_tol}",
        f"  residual_growth_limit: {args.residual_growth_limit}",
        f"  plateau_window_chunks: {args.plateau_window_chunks}",
        f"  plateau_rel_change_tol: {args.plateau_rel_change_tol}",
        f"  plateau_min_residual_above_target_factor: {args.plateau_min_residual_above_target_factor}",
        f"  promote_on_success: {args.promote_on_success}",
        f"  pseudo_transient: {args.use_pseudo_transient if args.use_pseudo_transient is not None else 'preserve'}",
        f"  use_high_order_term_relaxation: {args.use_high_order_term_relaxation}",
        f"  second_order_blending_start: {args.second_order_blending_start}",
        f"  second_order_blending_end: {args.second_order_blending_end}",
        f"  second_order_blending_steps: {args.second_order_blending_steps}",
        f"  blending_hold_iterations: {args.blending_hold_iterations}",
        f"  pressure_velocity_coupling: {args.pressure_velocity_coupling}",
        f"  relaxation_profile: {args.relaxation_profile}",
        f"  species_implicit_under_relaxation: "
        f"{args.species_implicit_under_relaxation}",
        f"  pseudo_time_verbosity: {args.pseudo_time_verbosity}",
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
    resolve_path_defaults(args)

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

        failed = is_failure_status(record["rerun_status"])
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

    failures = [record for record in results if is_failure_status(record["rerun_status"])]
    if failures and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
