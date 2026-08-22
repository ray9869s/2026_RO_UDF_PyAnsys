#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch runner for RO CFD post-processing.

This script orchestrates existing one-case post-processing scripts. It does
not open, edit, delete, or rewrite case/data files itself. Batch artifacts are
written under RO_DATA_ROOT/inventory.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent

from ro.domain_layout import (  # noqa: E402
    layout_post_config_values,
    resolve_layout,
)
from ro.paths import data_root, project_root, runs_root, templates_dir  # noqa: E402

CFF_SOURCE_TEMPLATE = "template"
CFF_SOURCE_CASE_SPECIFIC = "case_specific"
CFF_SOURCE_MISSING = "missing"

REPORT_SCRIPT = SCRIPT_DIR / "pyfluent_report_extract.py"
PYENSIGHT_CONTOUR_SCRIPT = SCRIPT_DIR / "pyensight_contour_export.py"
SHEAR_CONTOUR_SCRIPT = SCRIPT_DIR / "pyfluent_shear_contour_export.py"
BASE_POST_CONFIG = project_root() / "configs" / "post_config.py"

STATUS_PLANNED = "PLANNED"
STATUS_SKIPPED_EXISTING = "SKIPPED_EXISTING"
STATUS_SKIPPED_DISABLED = "SKIPPED_DISABLED"
STATUS_SKIPPED_MISSING_CFF = "SKIPPED_MISSING_CFF"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_WARN = "WARN"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_DRY_RUN = "DRY_RUN"
STATUS_LAYOUT_UNKNOWN = "LAYOUT_UNKNOWN"

SHEAR_EXPORT_MODE_AUTO = "auto"
SHEAR_EXPORT_MODE_NATIVE = "native"
SHEAR_EXPORT_MODE_FALLBACK = "fallback"

DEFAULT_CASE_STATUS = "READY_FOR_POSTPROCESSING"
POSTPROCESSED_BASIC = "POSTPROCESSED_BASIC"
POSTPROCESSED_UNCONVERGED = "POSTPROCESSED_UNCONVERGED"
NEEDS_SOLVER_RERUN = "NEEDS_SOLVER_RERUN"
FAILED_OR_DIVERGED = "FAILED_OR_DIVERGED"
MAX_ITER_REACHED = "MAX_ITER_REACHED"

STDOUT_STDERR_TAIL_CHARS = 4000

PLAN_FIELDNAMES = [
    "selected_index",
    "geo_name",
    "case_name",
    "case_dir",
    "case_status",
    "convergence_status",
    "report_stage_status",
    "pyensight_contour_stage_status",
    "shear_stage_status",
    "report_command",
    "contour_command",
    "shear_command",
    "missing_cff_file",
]

RESULT_FIELDNAMES = [
    "geo_name",
    "case_name",
    "case_dir",
    "selected_index",
    "report_stage_status",
    "pyensight_contour_stage_status",
    "shear_stage_status",
    "report_returncode",
    "contour_returncode",
    "shear_returncode",
    "missing_cff_file",
    "cff_file_used",
    "cff_source",
    "shear_export_mode",
    "shear_retry_attempted",
    "shear_retry_mode",
    "shear_retry_status",
    "shear_retry_returncode",
    "shear_retry_log_file",
    "output_files_detected",
    "error_summary",
    "runtime_seconds_total",
    "suggested_next_action",
]


@dataclass
class StageResult:
    status: str
    returncode: Optional[int] = None
    runtime_seconds: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    log_file: str = ""
    command: list[str] | None = None
    error_summary: str = ""


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch post-process eligible RO CFD cases from case_inventory_compact.csv."
    )
    parser.add_argument(
        "--inventory-csv",
        type=Path,
        default=None,
        help="Inventory CSV (default: RO_DATA_ROOT/inventory/case_inventory_compact.csv).",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="Root results directory (default: RO_DATA_ROOT/runs).",
    )
    parser.add_argument("--python-exe", type=Path, default=Path(sys.executable))
    parser.add_argument("--geo-name", type=str, default=None)
    parser.add_argument("--case-name", type=str, default=None)
    parser.add_argument(
        "--case-status",
        action="append",
        default=None,
        help=(
            "Case status filter. Repeat or use comma-separated values. "
            f"Default: {DEFAULT_CASE_STATUS}. Known inventory statuses include "
            f"{POSTPROCESSED_BASIC}, {POSTPROCESSED_UNCONVERGED}, "
            f"{NEEDS_SOLVER_RERUN}, READY_FOR_POSTPROCESSING, "
            "NEEDS_SHEAR_POSTPROCESSING, NEEDS_REPORT_EXTRACTION."
        ),
    )
    parser.add_argument("--fields", type=str, default="cp_inlet,water_flux,lmh,salt_flux")
    parser.add_argument(
        "--membrane-surface",
        type=str,
        default="top",
        choices=["top", "bottom", "both"],
    )
    parser.add_argument("--run-reports", action="store_true", default=False)
    parser.add_argument(
        "--auto-run-missing-reports",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--run-pyensight-contours",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--run-shear", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--manual-view-bounds", type=str, default="0,0.010395,0,0.003465")
    parser.add_argument("--manual-view-plane", type=str, default="xy")
    parser.add_argument("--view-margin", type=float, default=1.25)
    parser.add_argument("--zoom-out", type=float, default=1.00)
    parser.add_argument("--legend-mode", type=str, default="hide", choices=["show", "hide"])
    parser.add_argument("--shear-range", type=str, default="0,5000")
    parser.add_argument("--shear-view-margin", type=float, default=1.20)
    parser.add_argument("--shear-width", type=int, default=1600)
    parser.add_argument("--shear-height", type=int, default=1200)
    parser.add_argument("--cff-name", type=str, default="cff_wall_shear_rate")
    parser.add_argument(
        "--shear-export-mode", type=str, default=SHEAR_EXPORT_MODE_AUTO,
        choices=[SHEAR_EXPORT_MODE_AUTO, SHEAR_EXPORT_MODE_NATIVE, SHEAR_EXPORT_MODE_FALLBACK],
        help=(
            "Passed through to pyfluent_shear_contour_export.py --shear-export-mode "
            "(default: auto). 'fallback' skips native Fluent graphics entirely."
        ),
    )
    parser.add_argument(
        "--retry-shear-fallback-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If the shear stage fails under auto/native mode, rerun it once with "
            "--shear-export-mode fallback (default: enabled)."
        ),
    )
    parser.add_argument(
        "--cff-file-template",
        type=Path,
        default=None,
        help=(
            "Shared CFF template file for shear runs. "
            "Default: <project>/templates/cff_wall_shear_rate.scm "
            "if it exists, otherwise falls back to the case-specific "
            "<case_dir>/post/figures/contours/cff_wall_shear_rate.scm. "
            "If explicitly passed, it must exist or the shear stage is skipped."
        ),
    )
    return parser.parse_args(argv)


def resolve_path_defaults(args: argparse.Namespace) -> argparse.Namespace:
    args.results_root = (args.results_root or runs_root()).resolve()
    args.inventory_csv = (
        args.inventory_csv
        or (data_root() / "inventory" / "case_inventory_compact.csv")
    ).resolve()
    return args


def bool_from_cell(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def has_windows_drive_component(path: Path) -> bool:
    return any(re.fullmatch(r"[A-Za-z]:", part) for part in path.parts)


def ensure_safe_write_root(path: Path) -> None:
    if platform.system() != "Windows" and has_windows_drive_component(path):
        raise RuntimeError(f"Refusing to create Windows-style path on this OS: {path}")


def split_csv_list(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parse_status_filters(values: Optional[list[str]]) -> set[str]:
    if not values:
        return {DEFAULT_CASE_STATUS}
    statuses: set[str] = set()
    for value in values:
        statuses.update(split_csv_list(value))
    return statuses or {DEFAULT_CASE_STATUS}


def parse_fields(fields: str) -> list[str]:
    parsed = split_csv_list(fields)
    if not parsed:
        raise ValueError("--fields must contain at least one field")
    return parsed


def read_inventory_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    if not path.is_file():
        return [], f"Inventory CSV not found: {path}"
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh)), ""
    except (OSError, csv.Error) as exc:
        return [], f"Could not read inventory CSV {path}: {exc}"


def safe_read_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    if not isinstance(payload, dict):
        return {}, "JSON root is not an object"
    return payload, ""


def int_from_any(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


WORKER_EXIT_PARTIAL = 1
WORKER_EXIT_FAILED = 2


def is_worker_hard_failure(worker_returncode: Optional[int]) -> bool:
    """True for total worker failure (exit 2+), not partial/WARN (exit 1)."""
    return worker_returncode is not None and worker_returncode not in (0, WORKER_EXIT_PARTIAL)


def stage_status_from_worker_returncode(worker_returncode: int) -> str:
    if worker_returncode == 0:
        return STATUS_SUCCESS
    if worker_returncode == WORKER_EXIT_PARTIAL:
        return STATUS_WARN
    return STATUS_FAILED


def shear_stage_eligible_for_fallback_retry(worker_returncode: Optional[int]) -> bool:
    """Retry only on total worker failure (exit 2), not partial/WARN (exit 1)."""
    return worker_returncode == WORKER_EXIT_FAILED


def infer_contour_stage_status(
    status_path: Path,
    worker_returncode: Optional[int],
    requested_fields: list[str],
) -> tuple[str, str]:
    if is_worker_hard_failure(worker_returncode):
        return STATUS_FAILED, ""

    payload, error = safe_read_json(status_path)
    if not payload:
        if not status_path.is_file():
            return STATUS_UNKNOWN, "status JSON missing after worker exit 0"
        return STATUS_UNKNOWN, f"status JSON unreadable after worker exit 0: {error}"

    summary = payload.get("summary")
    failed_count = 0
    warn_count = 0
    if isinstance(summary, dict):
        failed_count = int_from_any(summary.get("failed")) or 0
        warn_count = int_from_any(summary.get("warn")) or 0

    explicit_status = str(
        payload.get("overall_status") or payload.get("status") or ""
    ).strip().upper()

    field_statuses: dict[str, str] = {}
    records = payload.get("records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            field_key = str(record.get("field_key", "")).strip()
            if field_key in requested_fields:
                field_statuses[field_key] = str(record.get("status", "")).strip().upper()

    if failed_count > 0 or explicit_status == "FAILED":
        return STATUS_FAILED, ""
    for field_key in requested_fields:
        if field_statuses.get(field_key) == "FAILED":
            return STATUS_FAILED, ""

    missing_fields = [field_key for field_key in requested_fields if field_key not in field_statuses]
    if missing_fields:
        return STATUS_UNKNOWN, (
            "requested field(s) missing from status JSON records: "
            + ", ".join(missing_fields)
        )

    if warn_count > 0 or explicit_status == "WARN":
        return STATUS_WARN, ""
    for field_key in requested_fields:
        if field_statuses.get(field_key) == "WARN":
            return STATUS_WARN, ""

    if requested_fields and all(field_statuses.get(field_key) == "SUCCESS" for field_key in requested_fields):
        return STATUS_SUCCESS, ""
    if explicit_status == "SUCCESS":
        return STATUS_SUCCESS, ""

    return STATUS_UNKNOWN, "status JSON present but contour stage outcome could not be inferred"


def infer_shear_stage_status(
    status_path: Path,
    worker_returncode: Optional[int],
) -> tuple[str, str]:
    if is_worker_hard_failure(worker_returncode):
        return STATUS_FAILED, ""

    payload, error = safe_read_json(status_path)
    if not payload:
        if not status_path.is_file():
            return STATUS_UNKNOWN, "status JSON missing after worker exit 0"
        return STATUS_UNKNOWN, f"status JSON unreadable after worker exit 0: {error}"

    explicit_status = str(
        payload.get("status") or payload.get("overall_status") or ""
    ).strip().upper()
    native_status = str(payload.get("native_status") or "").strip().upper()
    fallback_status = str(payload.get("fallback_status") or "").strip().upper()

    if explicit_status == "FAILED":
        return STATUS_FAILED, ""
    if native_status == "FAILED" and bool(payload.get("native_attempted")):
        return STATUS_FAILED, ""
    if fallback_status == "FAILED" and bool(payload.get("fallback_attempted")):
        return STATUS_FAILED, ""

    if explicit_status == "WARN":
        return STATUS_WARN, ""

    if explicit_status == "SUCCESS":
        return STATUS_SUCCESS, ""

    return STATUS_UNKNOWN, "status JSON present but shear stage outcome could not be inferred"


def refine_recorded_stage_status(
    planned_status: str,
    stage_result: StageResult,
    infer_fn: Any,
    *infer_args: Any,
) -> tuple[str, str]:
    if planned_status != STATUS_PLANNED:
        return stage_result.status, ""
    if stage_result.status == STATUS_DRY_RUN:
        return STATUS_DRY_RUN, ""
    if is_worker_hard_failure(stage_result.returncode):
        return stage_result.status, ""
    return infer_fn(*infer_args)


def is_explicit_solver_status_requested(statuses: set[str]) -> bool:
    risky = {NEEDS_SOLVER_RERUN, FAILED_OR_DIVERGED, MAX_ITER_REACHED}
    return bool(statuses.intersection(risky))


def row_status_requested(row: dict[str, str], statuses: set[str]) -> bool:
    return row.get("case_status", "") in statuses or row.get("convergence_status", "") in statuses


def select_cases(
    rows: list[dict[str, str]],
    statuses: set[str],
    geo_name: Optional[str],
    case_name: Optional[str],
) -> list[dict[str, str]]:
    allow_solver_risk = is_explicit_solver_status_requested(statuses)
    selected: list[dict[str, str]] = []

    for row in rows:
        if geo_name and row.get("geo_name") != geo_name:
            continue
        if case_name and row.get("case_name") != case_name:
            continue
        if not row_status_requested(row, statuses):
            continue
        if not bool_from_cell(row.get("has_final_cas")):
            continue
        if not bool_from_cell(row.get("has_final_dat")):
            continue

        case_status = row.get("case_status", "")
        convergence_status = row.get("convergence_status", "")
        if case_status == NEEDS_SOLVER_RERUN and not allow_solver_risk:
            continue
        if convergence_status == FAILED_OR_DIVERGED and not allow_solver_risk:
            continue
        selected.append(row)

    return selected


def slice_cases(rows: list[dict[str, str]], start_index: int, limit: Optional[int]) -> list[dict[str, str]]:
    if start_index < 0:
        raise ValueError("--start-index must be >= 0")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be >= 0")
    sliced = rows[start_index:]
    if limit is not None:
        sliced = sliced[:limit]
    return sliced


def case_paths(results_root: Path, geo_name: str, case_name: str) -> dict[str, Path]:
    case_dir = results_root / geo_name / case_name
    post_dir = case_dir / "post"
    reports_dir = post_dir / "reports"
    contours_dir = post_dir / "figures" / "contours"
    return {
        "case_dir": case_dir,
        "reports_dir": reports_dir,
        "contours_dir": contours_dir,
        "summary_wide": reports_dir / "summary_metrics_wide.csv",
        "contour_metadata": contours_dir / "contour_colorbar_ranges.json",
        "shear_metadata": contours_dir / "shear_colorbar_range.json",
    }


def glob_pngs(directory: Path, required_terms: tuple[str, ...]) -> list[Path]:
    if not directory.is_dir():
        return []
    matches: list[Path] = []
    for path in directory.glob("*.png"):
        lowered = path.name.lower()
        if all(term.lower() in lowered for term in required_terms):
            matches.append(path)
    return sorted(matches, key=lambda p: p.as_posix().lower())


def expected_contour_outputs(contours_dir: Path, fields: list[str]) -> dict[str, list[Path]]:
    return {
        field: glob_pngs(contours_dir, (field, "membrane"))
        for field in fields
    }


def has_all_pyensight_outputs(paths: dict[str, Path], fields: list[str]) -> bool:
    outputs = expected_contour_outputs(paths["contours_dir"], fields)
    return all(bool(outputs[field]) for field in fields) and paths["contour_metadata"].is_file()


def has_shear_outputs(paths: dict[str, Path]) -> bool:
    return bool(glob_pngs(paths["contours_dir"], ("shear_rate", "membrane"))) and paths["shear_metadata"].is_file()


def detected_output_files(paths: dict[str, Path], fields: list[str]) -> list[str]:
    files: list[Path] = []
    if paths["summary_wide"].is_file():
        files.append(paths["summary_wide"])
    contour_outputs = expected_contour_outputs(paths["contours_dir"], fields)
    for field in fields:
        files.extend(contour_outputs[field])
    for key in ("contour_metadata", "shear_metadata"):
        if paths[key].is_file():
            files.append(paths[key])
    files.extend(glob_pngs(paths["contours_dir"], ("shear_rate", "membrane")))
    return [p.as_posix() for p in sorted(set(files), key=lambda p: p.as_posix().lower())]


def command_to_string(command: list[str] | None) -> str:
    if not command:
        return ""
    return " ".join(str(part) for part in command)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "case"


def tail_text(text: str, limit: int = STDOUT_STDERR_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean
    return ""


def write_text_log(
    log_path: Path,
    stage: str,
    command: list[str] | None,
    status: str,
    returncode: Optional[int],
    runtime_seconds: float,
    stdout: str = "",
    stderr: str = "",
    note: str = "",
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"stage: {stage}",
        f"status: {status}",
        f"returncode: {'' if returncode is None else returncode}",
        f"runtime_seconds: {runtime_seconds:.3f}",
        f"command: {command_to_string(command)}",
    ]
    if note:
        lines.append(f"note: {note}")
    lines.extend(["", "STDOUT:", stdout or "", "", "STDERR:", stderr or ""])
    log_path.write_text("\n".join(lines), encoding="utf-8")


def build_subprocess_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_stage_command(
    stage: str,
    command: list[str],
    log_path: Path,
    dry_run: bool,
    env: Optional[dict[str, str]] = None,
) -> StageResult:
    if dry_run:
        write_text_log(
            log_path,
            stage=stage,
            command=command,
            status=STATUS_DRY_RUN,
            returncode=None,
            runtime_seconds=0.0,
            note="Dry run: command was not executed.",
        )
        return StageResult(
            status=STATUS_DRY_RUN,
            runtime_seconds=0.0,
            log_file=log_path.as_posix(),
            command=command,
        )

    start = time.monotonic()
    proc = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    runtime = time.monotonic() - start
    status = stage_status_from_worker_returncode(proc.returncode)
    stdout_tail = tail_text(proc.stdout or "")
    stderr_tail = tail_text(proc.stderr or "")
    error_summary = ""
    if status == STATUS_FAILED:
        error_summary = first_nonempty_line(stderr_tail) or first_nonempty_line(stdout_tail)

    write_text_log(
        log_path,
        stage=stage,
        command=command,
        status=status,
        returncode=proc.returncode,
        runtime_seconds=runtime,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
    return StageResult(
        status=status,
        returncode=proc.returncode,
        runtime_seconds=runtime,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        log_file=log_path.as_posix(),
        command=command,
        error_summary=error_summary,
    )


def skipped_stage(stage: str, status: str, log_path: Path, note: str = "") -> StageResult:
    write_text_log(
        log_path,
        stage=stage,
        command=None,
        status=status,
        returncode=None,
        runtime_seconds=0.0,
        note=note,
    )
    return StageResult(status=status, runtime_seconds=0.0, log_file=log_path.as_posix())


def load_base_config() -> Any:
    if not BASE_POST_CONFIG.is_file():
        return None
    spec = importlib.util.spec_from_file_location("base_post_config", str(BASE_POST_CONFIG))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def try_resolve_post_layout_settings(
    geo_name: str,
    mesh_case_name: Optional[str],
    *,
    mesh_resolution_source: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Resolve additive layout keys. Name-keyed lookup always fails.

    Layout comes from the mesh manifest (step 6b). This wrapper still calls
    :func:`resolve_layout` so leftover (geo, mesh) callers fail loudly with
    LAYOUT_UNKNOWN instead of guessing 1+3+1.
    """
    try:
        record = resolve_layout(geo_name, mesh_case_name or "")
    except RuntimeError as exc:
        return None, str(exc)
    settings = layout_post_config_values(record)
    if mesh_resolution_source:
        settings = {
            **settings,
            "mesh_case_name": mesh_case_name,
            "mesh_resolution_source": mesh_resolution_source,
        }
    return settings, None


def resolve_case_dir_for_layout(
    row: dict[str, str],
    results_root: Path,
    geo_name: str,
    case_name: str,
) -> Path:
    """Prefer the inventory case_dir when it exists on disk.

    ``case_paths(results_root, …)`` rebuilds a CWD-relative path from
    ``--results-root``. When the inventory was scanned from a different
    working directory (or stores an absolute path), that rebuild can miss a
    real case directory and the solver replace log inside it. Use the
    inventory path when it is an existing directory.
    """
    rebuilt = results_root / geo_name / case_name
    raw = (row.get("case_dir") or "").strip()
    if raw:
        inventory_dir = Path(raw)
        if inventory_dir.is_dir():
            return inventory_dir
    return rebuilt



def write_report_config(
    config_path: Path,
    results_root: Path,
    geo_name: str,
    case_name: str,
    paths: dict[str, Path],
    layout_settings: dict[str, Any],
) -> None:
    base_cfg = load_base_config()
    inlet_velocity = None
    outlet_pressure = 6.0e6

    def cfg_get(name: str, default: Any) -> Any:
        return getattr(base_cfg, name, default) if base_cfg is not None else default

    project_root = results_root.parent
    n_buffer_in = int(layout_settings["n_buffer_in"])
    n_active = int(layout_settings["n_active"])
    n_buffer_out = int(layout_settings["n_buffer_out"])
    cell_length_x_m = float(layout_settings["cell_length_x_m"])
    n_total = n_buffer_in + n_active + n_buffer_out
    domain_length_m = n_total * cell_length_x_m
    buffer_length_m = n_buffer_in * cell_length_x_m
    lines = [
        f"project_root = {str(project_root)!r}",
        "",
        f"geo_name = {geo_name!r}",
        f"case_name = {case_name!r}",
        f"inlet_velocity_value = {inlet_velocity!r}",
        f"outlet_gauge_pressure = {outlet_pressure!r}",
        "",
        f"final_case_file = {str(paths['case_dir'] / f'{geo_name}_{case_name}_final.cas.h5')!r}",
        f"final_data_file = {str(paths['case_dir'] / f'{geo_name}_{case_name}_final.dat.h5')!r}",
        "",
        f"active_membrane_base_names = {layout_settings['active_membrane_base_names']!r}",
        f"buffer_wall_base_names = {layout_settings['buffer_wall_base_names']!r}",
        "",
        f"rho = {cfg_get('rho', 998.2)!r}",
        f"mu = {cfg_get('mu', 8.93e-4)!r}",
        f"c_inlet_ref = {cfg_get('c_inlet_ref', 597.8268309)!r}",
        "",
        f"product_version = {cfg_get('product_version', '25.1.0')!r}",
        f"processor_count = {cfg_get('processor_count', 1)!r}",
        f"graphics_driver = {cfg_get('graphics_driver', 'dx11')!r}",
        f"fluent_start_timeout = {cfg_get('fluent_start_timeout', 600)!r}",
        f"fluent_health_timeout = {cfg_get('fluent_health_timeout', 600)!r}",
        "",
        f"domain_x_min_m = {cfg_get('domain_x_min_m', 0.0)!r}",
        f"domain_length_m = {domain_length_m!r}",
        f"buffer_length_m = {buffer_length_m!r}",
        f"channel_height_m = {cfg_get('channel_height_m', 0.00077)!r}",
        f"n_unit_cells = {n_total!r}",
        f"n_inlet_spacer_cells_excluded = {cfg_get('n_inlet_spacer_cells_excluded', 1)!r}",
        "",
        f"n_buffer_in = {n_buffer_in!r}",
        f"n_active = {n_active!r}",
        f"n_buffer_out = {n_buffer_out!r}",
        f"cell_length_x_m = {cell_length_x_m!r}",
    ]
    if n_buffer_in == n_buffer_out:
        lines.append(f"n_buffer_cells_each_end = {n_buffer_in!r}")
    else:
        # Asymmetric: do not invent a fake each-end count.
        lines.append("n_buffer_cells_each_end = None")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report_command(args: argparse.Namespace) -> list[str]:
    return [str(args.python_exe), str(REPORT_SCRIPT)]


def build_pyensight_command(args: argparse.Namespace, geo_name: str, case_name: str) -> list[str]:
    command = [
        str(args.python_exe),
        str(PYENSIGHT_CONTOUR_SCRIPT),
        "--geo-name",
        geo_name,
        "--case-name",
        case_name,
        "--fields",
        args.fields,
        "--membrane-surface",
        args.membrane_surface,
        "--background",
        "white",
        "--view-margin",
        str(args.view_margin),
        "--zoom-out",
        str(args.zoom_out),
        "--manual-view-bounds",
        args.manual_view_bounds,
        "--manual-view-plane",
        args.manual_view_plane,
        "--legend-mode",
        args.legend_mode,
    ]
    if args.skip_existing and not args.force:
        command.append("--skip-existing")
    return command


def build_shear_command(
    args: argparse.Namespace,
    geo_name: str,
    case_name: str,
    cff_file: Path,
    shear_export_mode: Optional[str] = None,
) -> list[str]:
    mode = shear_export_mode if shear_export_mode is not None else args.shear_export_mode
    command = [
        str(args.python_exe),
        str(SHEAR_CONTOUR_SCRIPT),
        "--geo-name",
        geo_name,
        "--case-name",
        case_name,
        "--membrane-surface",
        args.membrane_surface,
        "--view-preset",
        "match_pyensight",
        "--background",
        "white",
        "--view-margin",
        str(args.shear_view_margin),
        "--shear-range",
        args.shear_range,
        "--width",
        str(args.shear_width),
        "--height",
        str(args.shear_height),
        "--cff-file",
        str(cff_file),
        "--cff-name",
        args.cff_name,
        "--legend-mode",
        args.legend_mode,
        "--shear-export-mode",
        mode,
    ]
    if args.skip_existing and not args.force:
        command.append("--skip-existing")
    return command


def resolve_cff_file(args: argparse.Namespace, paths: dict[str, Path]) -> tuple[Path, str]:
    case_specific_file = paths["contours_dir"] / f"{args.cff_name}.scm"

    if args.cff_file_template is not None:
        if args.cff_file_template.is_file():
            return args.cff_file_template, CFF_SOURCE_TEMPLATE
        return args.cff_file_template, CFF_SOURCE_MISSING

    default_template = templates_dir() / "cff_wall_shear_rate.scm"
    if default_template.is_file():
        return default_template, CFF_SOURCE_TEMPLATE

    if case_specific_file.is_file():
        return case_specific_file, CFF_SOURCE_CASE_SPECIFIC

    return case_specific_file, CFF_SOURCE_MISSING


def stage_log_path(log_dir: Path, geo_name: str, case_name: str, stage: str) -> Path:
    return log_dir / f"{safe_name(geo_name)}__{safe_name(case_name)}__{stage}.log"


def execute_case(
    row: dict[str, str],
    selected_index: int,
    args: argparse.Namespace,
    fields: list[str],
    batch_dir: Path,
    log_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    geo_name = row["geo_name"]
    case_name = row["case_name"]
    paths = case_paths(args.results_root, geo_name, case_name)
    case_dir = resolve_case_dir_for_layout(
        row, args.results_root, geo_name, case_name
    )
    paths["case_dir"] = case_dir
    config_dir = batch_dir / "report_configs"

    report_log = stage_log_path(log_dir, geo_name, case_name, "report")
    contour_log = stage_log_path(log_dir, geo_name, case_name, "pyensight_contours")
    shear_log = stage_log_path(log_dir, geo_name, case_name, "shear")

    report_command = build_report_command(args)
    contour_command = build_pyensight_command(args, geo_name, case_name)
    cff_file, cff_source = resolve_cff_file(args, paths)

    # Name-keyed layout lookup is gone; this raises via resolve_layout until
    # step 6b reads the mesh manifest. Never fall back to 1+3+1.
    layout_settings, layout_error = try_resolve_post_layout_settings(
        geo_name,
        None,
    )
    if layout_error is not None:
        print(f"[{selected_index}] {geo_name}/{case_name}")
        print(f"  layout: {STATUS_LAYOUT_UNKNOWN} :: {layout_error}")
        print(f"  case_dir: {case_dir.as_posix()}")
        plan = {
            "selected_index": selected_index,
            "geo_name": geo_name,
            "case_name": case_name,
            "case_dir": case_dir.as_posix(),
            "case_status": row.get("case_status", ""),
            "convergence_status": row.get("convergence_status", ""),
            "report_stage_status": STATUS_LAYOUT_UNKNOWN,
            "pyensight_contour_stage_status": STATUS_LAYOUT_UNKNOWN,
            "shear_stage_status": STATUS_LAYOUT_UNKNOWN,
            "report_command": "",
            "contour_command": "",
            "shear_command": "",
            "missing_cff_file": "",
        }
        result = {
            "geo_name": geo_name,
            "case_name": case_name,
            "case_dir": case_dir.as_posix(),
            "selected_index": selected_index,
            "report_stage_status": STATUS_LAYOUT_UNKNOWN,
            "pyensight_contour_stage_status": STATUS_LAYOUT_UNKNOWN,
            "shear_stage_status": STATUS_LAYOUT_UNKNOWN,
            "report_returncode": None,
            "contour_returncode": None,
            "shear_returncode": None,
            "missing_cff_file": "",
            "cff_file_used": cff_file.as_posix(),
            "cff_source": cff_source,
            "shear_export_mode": args.shear_export_mode,
            "shear_retry_attempted": False,
            "shear_retry_mode": "",
            "shear_retry_status": "",
            "shear_retry_returncode": None,
            "shear_retry_log_file": "",
            "output_files_detected": "",
            "error_summary": layout_error,
            "runtime_seconds_total": 0.0,
            "suggested_next_action": (
                "Layout comes from the mesh manifest. Wait for step 6b to "
                "read layout_from_mesh_manifest(mesh_directory); do not "
                "guess 1+3+1 from geo/case names."
            ),
            "stage_details": {
                "report": None,
                "pyensight_contours": None,
                "shear": None,
                "shear_retry_fallback": None,
            },
        }
        return plan, result

    assert layout_settings is not None

    report_status_planned = STATUS_PLANNED
    contour_status_planned = STATUS_PLANNED if args.run_pyensight_contours else STATUS_SKIPPED_DISABLED
    shear_status_planned = STATUS_PLANNED if args.run_shear else STATUS_SKIPPED_DISABLED

    report_exists = paths["summary_wide"].is_file()
    if report_exists and args.skip_existing and not args.force:
        report_status_planned = STATUS_SKIPPED_EXISTING
    elif not args.run_reports and not (args.auto_run_missing_reports and not report_exists):
        report_status_planned = STATUS_SKIPPED_DISABLED

    if args.run_pyensight_contours and has_all_pyensight_outputs(paths, fields) and args.skip_existing and not args.force:
        contour_status_planned = STATUS_SKIPPED_EXISTING

    missing_cff_file = ""
    effective_shear_export_mode = args.shear_export_mode
    if args.run_shear:
        if has_shear_outputs(paths) and args.skip_existing and not args.force:
            shear_status_planned = STATUS_SKIPPED_EXISTING
        elif cff_source == CFF_SOURCE_MISSING:
            if args.shear_export_mode != SHEAR_EXPORT_MODE_NATIVE:
                # The field-data fallback does not require a CFF file, so run
                # directly in fallback mode instead of skipping the case.
                effective_shear_export_mode = SHEAR_EXPORT_MODE_FALLBACK
                missing_cff_file = cff_file.as_posix()
            else:
                shear_status_planned = STATUS_SKIPPED_MISSING_CFF
                missing_cff_file = cff_file.as_posix()

    shear_command = build_shear_command(
        args, geo_name, case_name, cff_file, shear_export_mode=effective_shear_export_mode
    )

    plan = {
        "selected_index": selected_index,
        "geo_name": geo_name,
        "case_name": case_name,
        "case_dir": case_dir.as_posix(),
        "case_status": row.get("case_status", ""),
        "convergence_status": row.get("convergence_status", ""),
        "report_stage_status": report_status_planned,
        "pyensight_contour_stage_status": contour_status_planned,
        "shear_stage_status": shear_status_planned,
        "report_command": command_to_string(report_command) if report_status_planned == STATUS_PLANNED else "",
        "contour_command": command_to_string(contour_command) if contour_status_planned == STATUS_PLANNED else "",
        "shear_command": command_to_string(shear_command) if shear_status_planned == STATUS_PLANNED else "",
        "missing_cff_file": missing_cff_file,
    }

    print(f"[{selected_index}] {geo_name}/{case_name}")
    print(
        f"  mesh_case_name: {mesh_case_name!r} "
        f"(via {mesh_resolution_source})"
    )
    for stage_name, planned_status, command in (
        ("report", report_status_planned, report_command),
        ("pyensight_contours", contour_status_planned, contour_command),
        ("shear", shear_status_planned, shear_command),
    ):
        if planned_status == STATUS_PLANNED:
            print(f"  {stage_name}: {STATUS_DRY_RUN if args.dry_run else STATUS_PLANNED} :: {command_to_string(command)}")
        else:
            print(f"  {stage_name}: {planned_status}")

    start_total = time.monotonic()

    if report_status_planned == STATUS_PLANNED:
        report_config = config_dir / f"{safe_name(geo_name)}__{safe_name(case_name)}__post_config.py"
        env = build_subprocess_env({"PYFLUENT_POST_CONFIG": str(report_config)})
        if not args.dry_run:
            write_report_config(
                report_config,
                args.results_root,
                geo_name,
                case_name,
                paths,
                layout_settings,
            )
        report_result = run_stage_command("report", report_command, report_log, args.dry_run, env=env)
    else:
        report_result = skipped_stage("report", report_status_planned, report_log)

    if contour_status_planned == STATUS_PLANNED:
        contour_result = run_stage_command(
            "pyensight_contours",
            contour_command,
            contour_log,
            args.dry_run,
            env=build_subprocess_env(),
        )
    else:
        contour_result = skipped_stage("pyensight_contours", contour_status_planned, contour_log)

    if shear_status_planned == STATUS_PLANNED:
        shear_result = run_stage_command(
            "shear", shear_command, shear_log, args.dry_run, env=build_subprocess_env()
        )
    else:
        note = f"Missing CFF file: {missing_cff_file}" if missing_cff_file else ""
        shear_result = skipped_stage("shear", shear_status_planned, shear_log, note=note)

    shear_retry_attempted = False
    shear_retry_mode = ""
    shear_retry_result: Optional[StageResult] = None
    if (
        shear_status_planned == STATUS_PLANNED
        and shear_stage_eligible_for_fallback_retry(shear_result.returncode)
        and args.retry_shear_fallback_on_failure
        and effective_shear_export_mode != SHEAR_EXPORT_MODE_FALLBACK
    ):
        shear_retry_attempted = True
        shear_retry_mode = SHEAR_EXPORT_MODE_FALLBACK
        retry_command = build_shear_command(
            args, geo_name, case_name, cff_file, shear_export_mode=SHEAR_EXPORT_MODE_FALLBACK
        )
        retry_log = stage_log_path(log_dir, geo_name, case_name, "shear_retry_fallback")
        print(f"  shear: FAILED, retrying with --shear-export-mode fallback :: {command_to_string(retry_command)}")
        shear_retry_result = run_stage_command(
            "shear_retry_fallback", retry_command, retry_log, args.dry_run, env=build_subprocess_env()
        )

    shear_counting_result = (
        shear_retry_result
        if (shear_retry_result is not None and shear_retry_result.status == STATUS_SUCCESS)
        else shear_result
    )
    final_shear_returncode = shear_counting_result.returncode

    contour_status_file = paths["contours_dir"] / "contour_export_status.json"
    contour_recorded_status, contour_status_note = refine_recorded_stage_status(
        contour_status_planned,
        contour_result,
        infer_contour_stage_status,
        contour_status_file,
        contour_result.returncode,
        fields,
    )
    shear_status_file = paths["contours_dir"] / "shear_contour_status.json"
    final_shear_status, shear_status_note = refine_recorded_stage_status(
        shear_status_planned,
        shear_counting_result,
        infer_shear_stage_status,
        shear_status_file,
        shear_counting_result.returncode,
    )

    total_runtime = time.monotonic() - start_total
    error_parts = [
        part
        for part in (
            report_result.error_summary,
            contour_result.error_summary if contour_recorded_status == STATUS_FAILED else "",
            shear_counting_result.error_summary if final_shear_status == STATUS_FAILED else "",
            contour_status_note,
            shear_status_note,
        )
        if part
    ]

    result = {
        "geo_name": geo_name,
        "case_name": case_name,
        "case_dir": case_dir.as_posix(),
        "selected_index": selected_index,
        "report_stage_status": report_result.status,
        "pyensight_contour_stage_status": contour_recorded_status,
        "shear_stage_status": final_shear_status,
        "report_returncode": report_result.returncode,
        "contour_returncode": contour_result.returncode,
        "shear_returncode": final_shear_returncode,
        "missing_cff_file": missing_cff_file,
        "cff_file_used": cff_file.as_posix(),
        "cff_source": cff_source,
        "shear_export_mode": effective_shear_export_mode,
        "shear_retry_attempted": shear_retry_attempted,
        "shear_retry_mode": shear_retry_mode,
        "shear_retry_status": shear_retry_result.status if shear_retry_result is not None else "",
        "shear_retry_returncode": shear_retry_result.returncode if shear_retry_result is not None else None,
        "shear_retry_log_file": shear_retry_result.log_file if shear_retry_result is not None else "",
        "output_files_detected": detected_output_files(paths, fields),
        "error_summary": " | ".join(error_parts),
        "runtime_seconds_total": round(total_runtime, 3),
        "suggested_next_action": suggest_next_action(
            report_result,
            replace(contour_result, status=contour_recorded_status),
            replace(shear_counting_result, status=final_shear_status),
        ),
        "stage_details": {
            "report": stage_result_to_dict(report_result),
            "pyensight_contours": stage_result_to_dict(contour_result),
            "shear": stage_result_to_dict(shear_result),
            "shear_retry_fallback": (
                stage_result_to_dict(shear_retry_result) if shear_retry_result is not None else None
            ),
        },
    }
    return plan, result


def stage_result_to_dict(result: StageResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "returncode": result.returncode,
        "runtime_seconds": round(result.runtime_seconds, 3),
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
        "log_file": result.log_file,
        "command": result.command or [],
        "error_summary": result.error_summary,
    }


def suggest_next_action(
    report_result: StageResult,
    contour_result: StageResult,
    shear_result: StageResult,
) -> str:
    failed = [
        name
        for name, result in (
            ("report", report_result),
            ("pyensight_contours", contour_result),
            ("shear", shear_result),
        )
        if result.status == STATUS_FAILED
    ]
    if failed:
        return "Inspect batch stage logs and rerun failed stage(s): " + ", ".join(failed)
    if shear_result.status == STATUS_SKIPPED_MISSING_CFF:
        return "Provide a case CFF file or pass --cff-file-template, then rerun shear stage."
    if any(result.status == STATUS_DRY_RUN for result in (report_result, contour_result, shear_result)):
        return "Review dry-run plan, then rerun without --dry-run."
    return "Run inventory again to refresh post-processing status."


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def write_csv_file(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)


def stage_counts(results: list[dict[str, Any]], field: str) -> Counter:
    return Counter(str(row.get(field, "")) for row in results)


def skipped_count(counter: Counter) -> int:
    return sum(count for status, count in counter.items() if status.startswith("SKIPPED"))


def build_summary_text(
    selected_rows: list[dict[str, str]],
    plans: list[dict[str, Any]],
    results: list[dict[str, Any]],
    args: argparse.Namespace,
) -> str:
    report_counts = stage_counts(results, "report_stage_status")
    contour_counts = stage_counts(results, "pyensight_contour_stage_status")
    shear_counts = stage_counts(results, "shear_stage_status")

    lines = [
        "Batch Post-processing Summary",
        "=============================",
        f"inventory_csv: {args.inventory_csv}",
        f"results_root: {args.results_root}",
        f"dry_run: {args.dry_run}",
        f"selected_case_count: {len(selected_rows)}",
        "",
        "Report stage:",
        f"  success: {report_counts.get(STATUS_SUCCESS, 0)}",
        f"  failed: {report_counts.get(STATUS_FAILED, 0)}",
        f"  dry_run: {report_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(report_counts)}",
        "",
        "PyEnSight contour stage:",
        f"  success: {contour_counts.get(STATUS_SUCCESS, 0)}",
        f"  failed: {contour_counts.get(STATUS_FAILED, 0)}",
        f"  dry_run: {contour_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(contour_counts)}",
        "",
        "Shear stage:",
        f"  success: {shear_counts.get(STATUS_SUCCESS, 0)}",
        f"  failed: {shear_counts.get(STATUS_FAILED, 0)}",
        f"  dry_run: {shear_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(shear_counts)}",
        f"  missing_cff: {shear_counts.get(STATUS_SKIPPED_MISSING_CFF, 0)}",
        f"  fallback_retried: {sum(1 for r in results if r.get('shear_retry_attempted'))}",
        f"  fallback_retry_success: {sum(1 for r in results if r.get('shear_retry_status') == STATUS_SUCCESS)}",
        "",
        "Selected cases:",
    ]
    if not plans:
        lines.append("  (none)")
    else:
        for plan in plans:
            lines.append(
                "  "
                f"{plan['selected_index']}. {plan['geo_name']}/{plan['case_name']} "
                f"report={plan['report_stage_status']} "
                f"contour={plan['pyensight_contour_stage_status']} "
                f"shear={plan['shear_stage_status']}"
            )
    return "\n".join(lines) + "\n"


def write_batch_outputs(
    batch_dir: Path,
    selected_rows: list[dict[str, str]],
    plans: list[dict[str, Any]],
    results: list[dict[str, Any]],
    args: argparse.Namespace,
) -> Path:
    batch_dir.mkdir(parents=True, exist_ok=True)
    write_csv_file(batch_dir / "batch_postprocess_plan.csv", plans, PLAN_FIELDNAMES)
    write_csv_file(batch_dir / "batch_postprocess_results.csv", results, RESULT_FIELDNAMES)
    write_json_file(batch_dir / "batch_postprocess_results.json", results)
    summary_path = batch_dir / "batch_postprocess_summary.txt"
    summary_path.write_text(
        build_summary_text(selected_rows, plans, results, args),
        encoding="utf-8",
    )
    return summary_path


def print_selected_cases(rows: list[dict[str, str]]) -> None:
    print(f"Selected case count: {len(rows)}")
    if not rows:
        return
    for idx, row in enumerate(rows, start=1):
        print(
            f"  {idx}. {row.get('geo_name')}/{row.get('case_name')} "
            f"| case_status={row.get('case_status')} "
            f"| convergence_status={row.get('convergence_status')}"
        )


def print_console_summary(results: list[dict[str, Any]], summary_path: Path) -> None:
    report_counts = stage_counts(results, "report_stage_status")
    contour_counts = stage_counts(results, "pyensight_contour_stage_status")
    shear_counts = stage_counts(results, "shear_stage_status")
    print("")
    print(f"Report: success={report_counts.get(STATUS_SUCCESS, 0)} failed={report_counts.get(STATUS_FAILED, 0)} skipped={skipped_count(report_counts)} dry_run={report_counts.get(STATUS_DRY_RUN, 0)}")
    print(f"Contours: success={contour_counts.get(STATUS_SUCCESS, 0)} failed={contour_counts.get(STATUS_FAILED, 0)} skipped={skipped_count(contour_counts)} dry_run={contour_counts.get(STATUS_DRY_RUN, 0)}")
    fallback_retried = sum(1 for r in results if r.get("shear_retry_attempted"))
    fallback_retry_success = sum(1 for r in results if r.get("shear_retry_status") == STATUS_SUCCESS)
    print(
        "Shear: "
        f"success={shear_counts.get(STATUS_SUCCESS, 0)} "
        f"failed={shear_counts.get(STATUS_FAILED, 0)} "
        f"skipped={skipped_count(shear_counts)} "
        f"missing_cff={shear_counts.get(STATUS_SKIPPED_MISSING_CFF, 0)} "
        f"dry_run={shear_counts.get(STATUS_DRY_RUN, 0)} "
        f"fallback_retried={fallback_retried} "
        f"fallback_retry_success={fallback_retry_success}"
    )
    print(f"Batch summary: {summary_path}")


def run(args: argparse.Namespace) -> int:
    fields = parse_fields(args.fields)
    statuses = parse_status_filters(args.case_status)
    rows, error = read_inventory_csv(args.inventory_csv)
    if error:
        print(error, file=sys.stderr)
        return 2

    selected_all = select_cases(rows, statuses, args.geo_name, args.case_name)
    selected = slice_cases(selected_all, args.start_index, args.limit)
    print_selected_cases(selected)

    inventory_root = data_root() / "inventory"
    batch_dir = inventory_root / "batch_postprocess"
    log_dir = inventory_root / "batch_postprocess_logs"
    ensure_safe_write_root(batch_dir)
    ensure_safe_write_root(log_dir)

    plans: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for selected_index, row in enumerate(selected, start=1):
        plan, result = execute_case(
            row=row,
            selected_index=selected_index,
            args=args,
            fields=fields,
            batch_dir=batch_dir,
            log_dir=log_dir,
        )
        plans.append(plan)
        results.append(result)

        failed = any(
            result.get(field) == STATUS_FAILED
            for field in (
                "report_stage_status",
                "pyensight_contour_stage_status",
                "shear_stage_status",
            )
        )
        if failed and not args.continue_on_error:
            print("Stopping after first failed case because --no-continue-on-error was set.")
            break

    summary_path = write_batch_outputs(batch_dir, selected, plans, results, args)
    print_console_summary(results, summary_path)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        resolve_path_defaults(args)
        return run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
