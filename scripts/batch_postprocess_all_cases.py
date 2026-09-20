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

from ro.contour_campaign import contour_view_bounds_xy  # noqa: E402
from ro.domain_layout import (  # noqa: E402
    layout_from_run_directory,
    layout_post_config_values,
)
from ro.extract_skip import extract_skip_block_reason  # noqa: E402
from ro.manifest import ManifestError, read_mesh_manifest  # noqa: E402
from ro.session_retry import (  # noqa: E402
    RETRY_KIND_LAUNCH_SPAWN,
    RETRY_KIND_SCHEME_HEAP,
    RETRY_KIND_SOCKET_RESET,
    classify_retryable_session_crash,
)
from ro.paths import (  # noqa: E402
    any_id_filter,
    complete_run_identity,
    data_root,
    mesh_dir,
    project_root,
    record_matches_id_filters,
    require_existing_run,
    runs_root,
    templates_dir,
)

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

# Same defaults as batch_meshing: 2 retries after the first attempt, 15 s settle.
REPORT_TRANSIENT_FAILURE_MAX_RETRIES = 2
REPORT_POST_FAILURE_SETTLE_S = 15.0

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
    "report_transient_attempts",
    "report_retry_attempted",
    "report_retry_kinds",
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
    parser.add_argument("--family", type=str, default=None)
    parser.add_argument("--geo-id", type=str, default=None)
    parser.add_argument("--mesh-id", type=str, default=None)
    parser.add_argument("--run-id", type=str, default=None)
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
    parser.add_argument(
        "--manual-view-bounds",
        type=str,
        default=None,
        help=(
            "Override PyEnSight XY camera bounds as XMIN,XMAX,YMIN,YMAX. "
            "Default: derive from the mesh manifest "
            "(0, domain_extent_x_m, ±y-span/2)."
        ),
    )
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
    """True for any non-zero worker exit (including legacy exit-1 'partial')."""
    return worker_returncode is not None and worker_returncode != 0


def stage_status_from_worker_returncode(worker_returncode: int) -> str:
    """Map worker process exit code to a stage status.

    Exit 0 is SUCCESS. Any non-zero exit is FAILED. WARN is reserved for
    JSON-inferred partial outcomes after a successful (exit 0) worker and is
    never produced from the returncode alone — otherwise failed cases vanish
    from the failed counter.
    """
    if worker_returncode == 0:
        return STATUS_SUCCESS
    return STATUS_FAILED


def shear_stage_eligible_for_fallback_retry(worker_returncode: Optional[int]) -> bool:
    """Retry on any non-zero worker exit (hard failure)."""
    return is_worker_hard_failure(worker_returncode)


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
        # Non-zero exit is always FAILED; do not keep a stale WARN label.
        return STATUS_FAILED, ""
    return infer_fn(*infer_args)


def is_explicit_solver_status_requested(statuses: set[str]) -> bool:
    risky = {NEEDS_SOLVER_RERUN, FAILED_OR_DIVERGED, MAX_ITER_REACHED}
    return bool(statuses.intersection(risky))


def row_status_requested(row: dict[str, str], statuses: set[str]) -> bool:
    return row.get("case_status", "") in statuses or row.get("convergence_status", "") in statuses


def select_cases(
    rows: list[dict[str, str]],
    statuses: set[str],
    family: Optional[str],
    geo_id: Optional[str],
    mesh_id: Optional[str],
    run_id: Optional[str],
) -> list[dict[str, str]]:
    allow_solver_risk = is_explicit_solver_status_requested(statuses)
    selected: list[dict[str, str]] = []

    for row in rows:
        if not record_matches_id_filters(
            row,
            family=family,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=run_id,
        ):
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


def case_paths(case_dir: Path) -> dict[str, Path]:
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


def resolve_run_directory(row: dict[str, str]) -> Path:
    """Return the run directory from inventory ids via run_dir()."""
    family = (row.get("family") or "").strip()
    geo_id = (row.get("geo_id") or "").strip()
    mesh_id = (row.get("mesh_id") or "").strip()
    run_id = (row.get("run_id") or "").strip()
    identity = complete_run_identity(family, geo_id, mesh_id, run_id)
    if identity is not None:
        return require_existing_run(*identity)
    raise ValueError(
        "Inventory row has no family/geo_id/mesh_id/run_id. "
        "Re-run case_inventory so the CSV includes those columns."
    )


def resolve_final_cas_dat(
    case_dir: Path, geo_id: str, run_id: str
) -> tuple[Path, Path]:
    expected_cas = case_dir / f"{geo_id}_{run_id}_final.cas.h5"
    expected_dat = case_dir / f"{geo_id}_{run_id}_final.dat.h5"
    if expected_cas.is_file() and expected_dat.is_file():
        return expected_cas, expected_dat
    cas_files = sorted(
        path for path in case_dir.glob("*.cas.h5") if path.name.endswith("_final.cas.h5")
    )
    dat_files = sorted(
        path for path in case_dir.glob("*.dat.h5") if path.name.endswith("_final.dat.h5")
    )
    expected_names = {expected_cas.name, expected_dat.name}
    mismatched = [
        path.name
        for path in cas_files + dat_files
        if path.name not in expected_names
    ]
    if mismatched:
        raise FileNotFoundError(
            f"Expected {expected_cas.name} and {expected_dat.name} in {case_dir}; "
            f"found mismatched finals: {mismatched}. "
            "Artifact names must be {geo_id}_{run_id}_final.{cas,dat}.h5; "
            "refusing a glob fallback."
        )
    return expected_cas, expected_dat


def build_report_overrides(
    run_payload: dict[str, Any],
    layout_settings: dict[str, Any],
    case_dir: Path,
    cas_path: Path,
    dat_path: Path,
) -> dict[str, Any]:
    geo_id = str(run_payload["geo_id"])
    run_id = str(run_payload["run_id"])
    overrides: dict[str, Any] = {
        "geo_name": geo_id,
        "case_name": run_id,
        "project_root": str(project_root()),
        "case_path": str(case_dir),
        "final_case_file": str(cas_path),
        "final_data_file": str(dat_path),
        "inlet_velocity_value": run_payload["u_target_ms"],
        "outlet_gauge_pressure": run_payload["p_gauge_pa"],
        "mesh_case_name": run_payload["mesh_id"],
        "mesh_resolution_source": "mesh_manifest",
    }
    overrides.update(layout_settings)
    return overrides


def build_report_command(args: argparse.Namespace) -> list[str]:
    return [str(args.python_exe), str(REPORT_SCRIPT)]


def build_pyensight_command(
    args: argparse.Namespace,
    *,
    family: str,
    geo_id: str,
    mesh_id: str,
    run_id: str,
    geo_name: str,
    case_name: str,
    mesh_payload: Optional[dict[str, Any]] = None,
) -> list[str]:
    command = [
        str(args.python_exe),
        str(PYENSIGHT_CONTOUR_SCRIPT),
        "--family",
        family,
        "--geo-id",
        geo_id,
        "--mesh-id",
        mesh_id,
        "--run-id",
        run_id,
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
        "--manual-view-plane",
        args.manual_view_plane,
        "--legend-mode",
        args.legend_mode,
    ]
    if args.manual_view_bounds:
        command.extend(["--manual-view-bounds", args.manual_view_bounds])
    else:
        if mesh_payload is None:
            raise ValueError(
                "mesh_payload is required to derive contour view bounds when "
                "--manual-view-bounds is omitted."
            )
        derived = contour_view_bounds_xy(mesh_payload)
        command.extend(["--manual-view-bounds", derived.as_cli()])
    if args.skip_existing and not args.force:
        command.append("--skip-existing")
    return command


def build_shear_command(
    args: argparse.Namespace,
    *,
    family: str,
    geo_id: str,
    mesh_id: str,
    run_id: str,
    geo_name: str,
    case_name: str,
    cff_file: Path,
    shear_export_mode: Optional[str] = None,
) -> list[str]:
    mode = shear_export_mode if shear_export_mode is not None else args.shear_export_mode
    command = [
        str(args.python_exe),
        str(SHEAR_CONTOUR_SCRIPT),
        "--family",
        family,
        "--geo-id",
        geo_id,
        "--mesh-id",
        mesh_id,
        "--run-id",
        run_id,
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


def stage_log_path(
    log_dir: Path,
    geo_name: str,
    case_name: str,
    stage: str,
    mesh_id: str,
) -> Path:
    return log_dir / (
        f"{safe_name(geo_name)}__{safe_name(mesh_id)}__"
        f"{safe_name(case_name)}__{stage}.log"
    )


def collect_report_failure_evidence(result: StageResult) -> str:
    """Concatenate worker tails and the stage log for retry matching."""
    chunks = [
        result.error_summary or "",
        result.stdout_tail or "",
        result.stderr_tail or "",
    ]
    log_path = Path(result.log_file) if result.log_file else None
    if log_path is not None and log_path.is_file():
        try:
            chunks.append(log_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return "\n".join(chunks)


def classify_retryable_report_failure(text: str) -> Optional[str]:
    """Return a retry kind for session-level Fluent crashes, else None.

    Session signatures win even when wrapped in a Canonical CP or
    load-bearing guard message. Those wrappers are how extract re-raises a
    Scheme crash. A Canonical CP / load-bearing / missing-cas / inlet
    readback failure with no session signature is not retried.
    """
    return classify_retryable_session_crash(text)


def run_report_stage_with_retries(
    command: list[str],
    log_dir: Path,
    geo_name: str,
    mesh_id: str,
    case_name: str,
    dry_run: bool,
    env: Optional[dict[str, str]] = None,
    *,
    max_retries: int = REPORT_TRANSIENT_FAILURE_MAX_RETRIES,
    settle_s: float = REPORT_POST_FAILURE_SETTLE_S,
    sleeper=time.sleep,
    stage_runner=None,
) -> tuple[StageResult, int, list[str]]:
    """Run the report extract subprocess, retrying session-level crashes.

    max_retries is retries after the first attempt (default 2 → 3 total).
    Each attempt is a new process and a new Fluent session. Returns
    (last StageResult, attempts used, retry kinds).
    """
    if stage_runner is None:
        stage_runner = run_stage_command
    max_retries = max(0, int(max_retries))
    max_attempts = max_retries + 1
    retry_kinds: list[str] = []
    result: Optional[StageResult] = None
    for attempt in range(1, max_attempts + 1):
        stage_label = "report" if attempt == 1 else f"report_retry{attempt - 1}"
        log_path = stage_log_path(
            log_dir, geo_name, case_name, stage_label, mesh_id
        )
        print(
            f"  report attempt {attempt}/{max_attempts}: "
            f"{command_to_string(command)}"
        )
        result = stage_runner("report", command, log_path, dry_run, env=env)
        if result.status != STATUS_FAILED:
            return result, attempt, retry_kinds
        retry_kind = classify_retryable_report_failure(
            collect_report_failure_evidence(result)
        )
        can_retry = attempt < max_attempts and retry_kind is not None
        if can_retry:
            retry_kinds.append(retry_kind)
            remaining = max_attempts - attempt
            print(
                f"  report: transient {retry_kind} on attempt {attempt}; "
                f"retrying after {settle_s:g}s ({remaining} retry left)."
            )
            if settle_s > 0.0:
                sleeper(settle_s)
            continue
        if attempt < max_attempts:
            print(
                f"  report: non-retryable failure on attempt {attempt} "
                f"(rc={result.returncode}); not retrying."
            )
        return result, attempt, retry_kinds
    assert result is not None
    return result, max_attempts, retry_kinds


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
    try:
        case_dir = resolve_run_directory(row)
        layout_record, run_payload = layout_from_run_directory(case_dir)
        mesh_payload = read_mesh_manifest(
            mesh_dir(
                run_payload["family"],
                run_payload["geo_id"],
                run_payload["mesh_id"],
            )
        )
        if not args.manual_view_bounds:
            contour_view_bounds_xy(mesh_payload)
    except (ValueError, TypeError, FileNotFoundError, ManifestError, OSError) as exc:
        print(f"[{selected_index}] {geo_name}/{case_name}")
        print(f"  layout: {STATUS_FAILED} :: {exc}")
        plan = {
            "selected_index": selected_index,
            "geo_name": geo_name,
            "case_name": case_name,
            "case_dir": (row.get("case_dir") or ""),
            "case_status": row.get("case_status", ""),
            "convergence_status": row.get("convergence_status", ""),
            "report_stage_status": STATUS_FAILED,
            "pyensight_contour_stage_status": STATUS_FAILED,
            "shear_stage_status": STATUS_FAILED,
            "report_command": "",
            "contour_command": "",
            "shear_command": "",
            "missing_cff_file": "",
        }
        result = {
            "geo_name": geo_name,
            "case_name": case_name,
            "case_dir": (row.get("case_dir") or ""),
            "selected_index": selected_index,
            "report_stage_status": STATUS_FAILED,
            "pyensight_contour_stage_status": STATUS_FAILED,
            "shear_stage_status": STATUS_FAILED,
            "report_returncode": None,
            "contour_returncode": None,
            "shear_returncode": None,
            "missing_cff_file": "",
            "cff_file_used": "",
            "cff_source": "",
            "shear_export_mode": args.shear_export_mode,
            "shear_retry_attempted": False,
            "shear_retry_mode": "",
            "shear_retry_status": "",
            "shear_retry_returncode": None,
            "shear_retry_log_file": "",
            "report_transient_attempts": 0,
            "report_retry_attempted": False,
            "report_retry_kinds": "",
            "output_files_detected": "",
            "error_summary": str(exc),
            "runtime_seconds_total": 0.0,
            "suggested_next_action": (
                "Layout comes from the mesh manifest via the run directory. "
                "Provide a valid run manifest.json; do not guess 1+3+1."
            ),
            "stage_details": {
                "report": None,
                "pyensight_contours": None,
                "shear": None,
                "shear_retry_fallback": None,
            },
        }
        return plan, result

    family = str(run_payload["family"])
    geo_id = str(run_payload["geo_id"])
    mesh_id = str(run_payload["mesh_id"])
    run_id = str(run_payload["run_id"])
    geo_name = geo_id
    case_name = run_id
    worker_ids = {
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
        "geo_name": geo_name,
        "case_name": case_name,
    }
    layout_settings = layout_post_config_values(layout_record)
    layout_settings["mesh_case_name"] = run_payload["mesh_id"]
    layout_settings["mesh_resolution_source"] = "mesh_manifest"
    paths = case_paths(case_dir)
    cas_path, dat_path = resolve_final_cas_dat(case_dir, geo_id, run_id)

    report_log = stage_log_path(log_dir, geo_name, case_name, "report", mesh_id)
    contour_log = stage_log_path(
        log_dir, geo_name, case_name, "pyensight_contours", mesh_id
    )
    shear_log = stage_log_path(log_dir, geo_name, case_name, "shear", mesh_id)

    report_command = build_report_command(args)
    contour_command = build_pyensight_command(
        args, **worker_ids, mesh_payload=mesh_payload
    )
    cff_file, cff_source = resolve_cff_file(args, paths)

    report_status_planned = STATUS_PLANNED
    contour_status_planned = STATUS_PLANNED if args.run_pyensight_contours else STATUS_SKIPPED_DISABLED
    shear_status_planned = STATUS_PLANNED if args.run_shear else STATUS_SKIPPED_DISABLED

    extract_block = extract_skip_block_reason(
        case_dir,
        paths["summary_wide"],
        cas_path,
        dat_path,
    )
    extract_current = extract_block is None
    report_exists = paths["summary_wide"].is_file()
    if extract_current and args.skip_existing and not args.force:
        report_status_planned = STATUS_SKIPPED_EXISTING
    elif not args.run_reports and not (args.auto_run_missing_reports and not report_exists):
        report_status_planned = STATUS_SKIPPED_DISABLED
    elif args.skip_existing and not args.force and extract_block:
        print(f"  Not skipping existing report: {extract_block}")

    if (
        args.run_pyensight_contours
        and has_all_pyensight_outputs(paths, fields)
        and args.skip_existing
        and not args.force
        and extract_current
    ):
        contour_status_planned = STATUS_SKIPPED_EXISTING

    missing_cff_file = ""
    effective_shear_export_mode = args.shear_export_mode
    if args.run_shear:
        if (
            has_shear_outputs(paths)
            and args.skip_existing
            and not args.force
            and extract_current
        ):
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
        args, cff_file=cff_file, shear_export_mode=effective_shear_export_mode, **worker_ids
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
        f"  mesh_id: {run_payload['mesh_id']!r} "
        f"(via mesh_manifest)"
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

    report_attempts = 0
    report_retry_kinds: list[str] = []
    if report_status_planned == STATUS_PLANNED:
        overrides = build_report_overrides(
            run_payload, layout_settings, case_dir, cas_path, dat_path
        )
        env = build_subprocess_env(
            {
                "PYFLUENT_POST_CONFIG": str(BASE_POST_CONFIG),
                "PYFLUENT_POST_OVERRIDES": json.dumps(overrides),
            }
        )
        report_result, report_attempts, report_retry_kinds = (
            run_report_stage_with_retries(
                report_command,
                log_dir,
                geo_name,
                mesh_id,
                case_name,
                args.dry_run,
                env=env,
            )
        )
        print(
            f"  report: {report_result.status}"
            + (
                f" rc={report_result.returncode}"
                if report_result.returncode is not None
                else ""
            )
            + (
                f" attempts={report_attempts}"
                if report_attempts > 1
                else ""
            )
        )
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
            args,
            cff_file=cff_file,
            shear_export_mode=SHEAR_EXPORT_MODE_FALLBACK,
            **worker_ids,
        )
        retry_log = stage_log_path(
            log_dir, geo_name, case_name, "shear_retry_fallback", mesh_id
        )
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

    if contour_status_planned == STATUS_PLANNED:
        print(
            f"  pyensight_contours: {contour_recorded_status}"
            + (
                f" rc={contour_result.returncode}"
                if contour_result.returncode is not None
                else ""
            )
        )
    if shear_status_planned == STATUS_PLANNED:
        print(
            f"  shear: {final_shear_status}"
            + (
                f" rc={final_shear_returncode}"
                if final_shear_returncode is not None
                else ""
            )
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
        "report_transient_attempts": report_attempts,
        "report_retry_attempted": bool(report_retry_kinds),
        "report_retry_kinds": report_retry_kinds,
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
        f"  warn: {report_counts.get(STATUS_WARN, 0)}",
        f"  dry_run: {report_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(report_counts)}",
        f"  transient_retried: {sum(1 for r in results if r.get('report_retry_attempted'))}",
        f"  transient_retry_success: {sum(1 for r in results if r.get('report_retry_attempted') and r.get('report_stage_status') == STATUS_SUCCESS)}",
        "",
        "PyEnSight contour stage:",
        f"  success: {contour_counts.get(STATUS_SUCCESS, 0)}",
        f"  failed: {contour_counts.get(STATUS_FAILED, 0)}",
        f"  warn: {contour_counts.get(STATUS_WARN, 0)}",
        f"  dry_run: {contour_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(contour_counts)}",
        "",
        "Shear stage:",
        f"  success: {shear_counts.get(STATUS_SUCCESS, 0)}",
        f"  failed: {shear_counts.get(STATUS_FAILED, 0)}",
        f"  warn: {shear_counts.get(STATUS_WARN, 0)}",
        f"  dry_run: {shear_counts.get(STATUS_DRY_RUN, 0)}",
        f"  skipped: {skipped_count(shear_counts)}",
        f"  missing_cff: {shear_counts.get(STATUS_SKIPPED_MISSING_CFF, 0)}",
        f"  fallback_retried: {sum(1 for r in results if r.get('shear_retry_attempted'))}",
        f"  fallback_retry_success: {sum(1 for r in results if r.get('shear_retry_status') == STATUS_SUCCESS)}",
        "",
        "Selected cases:",
    ]
    if not results:
        lines.append("  (none)")
    else:
        for row in results:
            lines.append(
                "  "
                f"{row.get('selected_index')}. {row.get('geo_name')}/{row.get('case_name')} "
                f"report={row.get('report_stage_status')} "
                f"contour={row.get('pyensight_contour_stage_status')} "
                f"shear={row.get('shear_stage_status')}"
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
    print(
        f"Report: success={report_counts.get(STATUS_SUCCESS, 0)} "
        f"failed={report_counts.get(STATUS_FAILED, 0)} "
        f"warn={report_counts.get(STATUS_WARN, 0)} "
        f"skipped={skipped_count(report_counts)} "
        f"dry_run={report_counts.get(STATUS_DRY_RUN, 0)} "
        f"transient_retried={sum(1 for r in results if r.get('report_retry_attempted'))} "
        f"transient_retry_success={sum(1 for r in results if r.get('report_retry_attempted') and r.get('report_stage_status') == STATUS_SUCCESS)}"
    )
    print(
        f"Contours: success={contour_counts.get(STATUS_SUCCESS, 0)} "
        f"failed={contour_counts.get(STATUS_FAILED, 0)} "
        f"warn={contour_counts.get(STATUS_WARN, 0)} "
        f"skipped={skipped_count(contour_counts)} "
        f"dry_run={contour_counts.get(STATUS_DRY_RUN, 0)}"
    )
    fallback_retried = sum(1 for r in results if r.get("shear_retry_attempted"))
    fallback_retry_success = sum(1 for r in results if r.get("shear_retry_status") == STATUS_SUCCESS)
    print(
        "Shear: "
        f"success={shear_counts.get(STATUS_SUCCESS, 0)} "
        f"failed={shear_counts.get(STATUS_FAILED, 0)} "
        f"warn={shear_counts.get(STATUS_WARN, 0)} "
        f"skipped={skipped_count(shear_counts)} "
        f"missing_cff={shear_counts.get(STATUS_SKIPPED_MISSING_CFF, 0)} "
        f"dry_run={shear_counts.get(STATUS_DRY_RUN, 0)} "
        f"fallback_retried={fallback_retried} "
        f"fallback_retry_success={fallback_retry_success}"
    )
    print(f"Batch summary: {summary_path}")


def postprocess_batch_exit_code(results) -> int:
    """Nonzero when a selected case had a FAILED stage. Zero selected is not a failure."""
    failed = any(
        result.get(field) == STATUS_FAILED
        for result in results
        for field in (
            "report_stage_status",
            "pyensight_contour_stage_status",
            "shear_stage_status",
        )
    )
    return 1 if failed else 0


def run(args: argparse.Namespace) -> int:
    fields = parse_fields(args.fields)
    statuses = parse_status_filters(args.case_status)
    rows, error = read_inventory_csv(args.inventory_csv)
    if error:
        print(error, file=sys.stderr)
        return 2

    identity = complete_run_identity(args.family, args.geo_id, args.mesh_id, args.run_id)
    if identity is not None:
        require_existing_run(*identity)

    selected_all = select_cases(
        rows,
        statuses,
        args.family,
        args.geo_id,
        args.mesh_id,
        args.run_id,
    )
    if any_id_filter(
        family=args.family,
        geo_id=args.geo_id,
        mesh_id=args.mesh_id,
        run_id=args.run_id,
    ) and not selected_all:
        print(
            "ERROR: No inventory row matched "
            f"family={args.family!r} geo_id={args.geo_id!r} "
            f"mesh_id={args.mesh_id!r} run_id={args.run_id!r}.",
            file=sys.stderr,
        )
        return 2
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
    return postprocess_batch_exit_code(results)


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
