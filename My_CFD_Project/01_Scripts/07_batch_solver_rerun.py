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
    "matrix_case_name",
    "final_pair_exists",
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
        help="Continuation iterations requested for each live rerun.",
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
        "--processor-count",
        type=positive_int,
        default=50,
        help="Fluent processor count for live reruns.",
    )
    parser.add_argument(
        "--graphics-driver",
        default="dx11",
        help="Fluent graphics driver for live reruns.",
    )
    parser.add_argument(
        "--fluent-start-timeout",
        type=positive_int,
        default=300,
        help="PyFluent launch start timeout in seconds.",
    )
    parser.add_argument(
        "--fluent-health-timeout",
        type=positive_int,
        default=300,
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


def as_fluent_path(path: Path) -> str:
    """Convert a path to a Fluent-friendly absolute path."""
    return str(path.resolve()).replace("\\", "/")


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
        final_pair_exists = final_case_file.is_file() and final_data_file.is_file()
        planned_backup_dir = (
            case_dir / "post" / "solver_rerun" / "backups" / timestamp
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
                "matrix_case_name": "true",
                "final_pair_exists": str(final_pair_exists).lower(),
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
            f"final_pair_exists={row['final_pair_exists']} "
            f"convergence={row['convergence_status_before'] or 'UNKNOWN'}"
        )


def load_prior_successes(results_csv: Path) -> set[tuple[str, str]]:
    successes: set[tuple[str, str]] = set()
    if not results_csv.is_file():
        return successes

    try:
        with results_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get("rerun_status") == "SUCCESS":
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
    }


def create_backup(
    case_dir: Path,
    final_case_file: Path,
    final_data_file: Path,
    timestamp: str,
) -> Path:
    backup_dir = case_dir / "post" / "solver_rerun" / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(final_case_file, backup_dir / final_case_file.name)
    shutil.copy2(final_data_file, backup_dir / final_data_file.name)
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


def run_live_solver_rerun(
    case_dir: Path,
    final_case_file: Path,
    args: argparse.Namespace,
    log_path: Path,
) -> None:
    require_windows_for_live_run()

    import ansys.fluent.core as pyfluent  # Imported only for live non-dry-run execution.

    pyfluent.config.check_health_timeout = args.fluent_health_timeout

    meshing = None
    solver = None
    transcript_is_running = False
    original_working_directory = Path.cwd()
    transcript_path = log_path.with_name(log_path.stem + "__fluent.trn")

    try:
        os.chdir(case_dir)

        print("\nLaunching Fluent in meshing mode, then switching to solver.")
        print(f"product_version={args.product_version}")
        print(f"processor_count={args.processor_count}")
        print(f"case_dir={case_dir}")

        meshing = pyfluent.launch_fluent(
            product_version=args.product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=args.processor_count,
            ui_mode="gui",
            graphics_driver=args.graphics_driver,
            start_timeout=args.fluent_start_timeout,
            cwd=as_fluent_path(case_dir),
        )
        solver = meshing.switch_to_solver()
        meshing = None

        if args.write_transcript:
            print(f"Starting Fluent transcript: {transcript_path}")
            solver.transcript.start(file_name=as_fluent_path(transcript_path))
            transcript_is_running = True

        print(f"Reading final case/data: {final_case_file}")
        solver.settings.file.read_case_data(file_name=as_fluent_path(final_case_file))
        print("Final case/data loaded. Solution will not be reinitialized.")

        apply_continuation_settings(solver=solver, args=args)

        print(f"\nContinuing solution for {args.additional_iterations} iterations.")
        solver.settings.solution.run_calculation.iterate(
            iter_count=args.additional_iterations
        )
        print("Continuation iterations completed.")

        print(f"Writing final case/data: {final_case_file}")
        solver.settings.file.write_case_data(file_name=as_fluent_path(final_case_file))
        print("Final case/data write completed.")

    finally:
        if solver is not None and transcript_is_running:
            try:
                solver.transcript.stop()
                transcript_is_running = False
            except Exception as cleanup_error:
                print(f"Warning: could not stop Fluent transcript: {cleanup_error}")

        if meshing is not None:
            try:
                meshing.exit()
            except Exception as cleanup_error:
                print(f"Warning: could not exit meshing session: {cleanup_error}")

        if solver is not None:
            try:
                solver.exit()
            except Exception as cleanup_error:
                print(f"Warning: could not exit solver session: {cleanup_error}")

        try:
            os.chdir(original_working_directory)
        except Exception as cleanup_error:
            print(f"Warning: could not restore working directory: {cleanup_error}")


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
    log_path = Path(record["log_file"])
    start_time = time.monotonic()

    with case_log(log_path):
        print(f"Processing selected case {record['selected_index']}: {geo_name}/{case_name}")
        print(f"Dry run: {args.dry_run}")
        print(f"Final case file: {final_case_file}")
        print(f"Final data file: {final_data_file}")

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

            final_pair_exists = final_case_file.is_file() and final_data_file.is_file()

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

            if not final_pair_exists:
                record["rerun_status"] = "FAILED_MISSING_FINAL_PAIR"
                record["returncode_or_exception"] = "missing final case/data pair"
                missing = [
                    str(path)
                    for path in (final_case_file, final_data_file)
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
                backup_dir = create_backup(
                    case_dir=case_dir,
                    final_case_file=final_case_file,
                    final_data_file=final_data_file,
                    timestamp=run_timestamp,
                )
                record["backup_dir"] = str(backup_dir)
                print(f"Backup complete: {backup_dir}")
            except Exception as exc:
                record["rerun_status"] = "FAILED_BACKUP"
                record["returncode_or_exception"] = short_exception(exc)
                record["error_summary"] = short_exception(exc)
                record["suggested_next_action"] = (
                    "Fix backup path/permissions before allowing final case/data overwrite."
                )
                traceback.print_exc()
                return record

            try:
                run_live_solver_rerun(
                    case_dir=case_dir,
                    final_case_file=final_case_file,
                    args=args,
                    log_path=log_path,
                )
            except Exception as exc:
                record["rerun_status"] = "FAILED_EXCEPTION"
                record["returncode_or_exception"] = short_exception(exc)
                record["error_summary"] = short_exception(exc)
                record["suggested_next_action"] = (
                    "Inspect the per-case solver rerun log and Fluent transcript if present."
                )
                traceback.print_exc()
                return record

            if not final_case_file.is_file() or not final_data_file.is_file():
                record["rerun_status"] = "FAILED_OUTPUT_MISSING_AFTER_RUN"
                record["returncode_or_exception"] = "output final case/data missing"
                record["error_summary"] = (
                    "Solver command finished but final case/data pair was not found."
                )
                record["suggested_next_action"] = (
                    "Inspect Fluent write_case_data output in the per-case log."
                )
                return record

            record["rerun_status"] = "SUCCESS"
            record["returncode_or_exception"] = "0"
            report_status, next_action = maybe_run_report_after_success(record, args)
            record["report_extraction_status"] = report_status
            record["suggested_next_action"] = next_action
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
        f"  residual_target: {args.residual_target}",
        f"  pseudo_transient: {args.use_pseudo_transient if args.use_pseudo_transient is not None else 'preserve'}",
        f"  pressure_velocity_coupling: {args.pressure_velocity_coupling}",
        f"  relaxation_profile: {args.relaxation_profile}",
        f"  write_transcript: {args.write_transcript}",
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
