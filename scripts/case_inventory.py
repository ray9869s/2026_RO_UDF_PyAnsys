#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inventory RO CFD result cases without opening Fluent, PyFluent, or PyEnSight.

The scanner is read-only with respect to case directories. It only writes the
inventory artifacts requested through --output-dir, unless --dry-run is used.
Default scan root is RO_DATA_ROOT/runs; default output is RO_DATA_ROOT/inventory.
Those paths are resolved after argument parsing.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import platform
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ro.manifest import iter_run_manifests
from ro.paths import data_root, project_root, runs_root
from ro.solver_common import (
    DEFAULT_MAX_ITERATIONS_FALLBACK,
    max_iterations_from_common_solver_settings,
    parse_stop_reason_from_text,
)

CONVERGED = "CONVERGED"
MAX_ITER_REACHED = "MAX_ITER_REACHED"
FAILED_OR_DIVERGED = "FAILED_OR_DIVERGED"
POSSIBLY_INCOMPLETE = "POSSIBLY_INCOMPLETE"
UNKNOWN_NO_LOG = "UNKNOWN_NO_LOG"
UNKNOWN_UNPARSED = "UNKNOWN_UNPARSED"

READY_FOR_POSTPROCESSING = "READY_FOR_POSTPROCESSING"
POSTPROCESSED_BASIC = "POSTPROCESSED_BASIC"
POSTPROCESSED_UNCONVERGED = "POSTPROCESSED_UNCONVERGED"
NEEDS_SHEAR_POSTPROCESSING = "NEEDS_SHEAR_POSTPROCESSING"
NEEDS_SOLVER_RERUN = "NEEDS_SOLVER_RERUN"
NEEDS_REPORT_EXTRACTION = "NEEDS_REPORT_EXTRACTION"
MISSING_CASE_OR_DATA = "MISSING_CASE_OR_DATA"
UNKNOWN_REVIEW_REQUIRED = "UNKNOWN_REVIEW_REQUIRED"

# Fluent writes fluent-<node>-error.log when a session crashes. When this
# happens during post-processing graphics (after the case was already
# solved), it must not be mistaken for solver-run evidence.
FLUENT_NODE_ERROR_LOG_PATTERN = re.compile(r"fluent-\d+-error\.log$", re.IGNORECASE)

LOG_WALK_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "_inventory",
    "figures",
    "contours",
    "plots",
    "images",
    "animations",
}

LOG_SUFFIXES = {".log", ".out", ".txt"}
LOG_NAME_KEYWORDS = ("transcript", "solver", "run", "fluent")

ROLE_SOLVER_RUN = "solver_run"
ROLE_POSTPROCESSING_GRAPHICS = "postprocessing_graphics"
ROLE_REPORT_EXTRACTION = "report_extraction"
ROLE_MESHING = "meshing"
ROLE_UDF_COMPILE = "udf_compile"
ROLE_UNKNOWN = "unknown"

LOG_ROLES = (
    ROLE_SOLVER_RUN,
    ROLE_POSTPROCESSING_GRAPHICS,
    ROLE_REPORT_EXTRACTION,
    ROLE_MESHING,
    ROLE_UDF_COMPILE,
    ROLE_UNKNOWN,
)

ROLE_LIST_FIELDS = {
    ROLE_SOLVER_RUN: "solver_log_files",
    ROLE_POSTPROCESSING_GRAPHICS: "postprocessing_log_files",
    ROLE_REPORT_EXTRACTION: "report_log_files",
    ROLE_MESHING: "meshing_log_files",
    ROLE_UDF_COMPILE: "udf_compile_log_files",
    ROLE_UNKNOWN: "unknown_log_files",
}

ROLE_KEYWORDS = {
    ROLE_POSTPROCESSING_GRAPHICS: (
        "save_picture",
        "graphics.colors.background",
        "results/graphics",
        "contour.display",
        "cff_wall_shear_rate",
        "shear contour",
        "color_map.visible",
        "pyensight",
        "contour_export_status",
        "shear_contour_status",
    ),
    ROLE_REPORT_EXTRACTION: (
        "report definition",
        "summary_metrics_wide",
        "area-weighted average",
        "expression error usedin",
        "report file",
        "report extraction",
    ),
    ROLE_MESHING: (
        "watertight geometry",
        "meshing workflow",
        "proximity",
        "task proximity",
        "surface mesh",
        "volume mesh",
    ),
    ROLE_UDF_COMPILE: (
        "scons",
        "libudf",
        "udf_names.c",
        ".obj error",
        "compile udf",
        "clang",
        "user_nt.udf",
    ),
    ROLE_SOLVER_RUN: (
        "iterate",
        "iterations",
        "residual",
        "continuity",
        "x-velocity",
        "y-velocity",
        "z-velocity",
        "species",
        "calculation complete",
        "solution is converged",
        "reached maximum number of iterations",
        "writing final.cas",
        "writing final.dat",
        "_final.cas.h5",
        "_final.dat.h5",
    ),
}

FILENAME_ROLE_HINTS = {
    ROLE_POSTPROCESSING_GRAPHICS: (
        "contour",
        "shear",
        "pyensight",
        "graphics",
        "figure",
        "postprocess",
        "post_processing",
    ),
    ROLE_REPORT_EXTRACTION: (
        "report",
        "summary",
        "metric",
        "extract",
    ),
    ROLE_MESHING: (
        "mesh",
        "meshing",
        "watertight",
    ),
    ROLE_UDF_COMPILE: (
        "udf",
        "libudf",
        "scons",
        "compile",
    ),
    ROLE_SOLVER_RUN: (
        "solver",
        "transcript",
        "fluent",
        "run",
        "iterate",
    ),
}

MAX_EVIDENCE_PER_KIND = 12
TEXT_HEAD_BYTES = 256 * 1024
TEXT_TAIL_BYTES = 2 * 1024 * 1024
MAX_LOG_FILES_TO_PARSE = 40

SUMMARY_FIELDS = {
    "c_bulk_center_area_avg": (
        ("c_bulk_center_area_avg",),
        ("bulk", "center", "area", "avg"),
    ),
    "lmh": (
        ("lmh",),
    ),
    "cp": (
        ("cp_inlet",),
        ("cp",),
        ("concentration", "polarization"),
    ),
    "pressure_drop": (
        ("pressure_drop",),
        ("pressure", "drop"),
    ),
    "mass_balance": (
        ("mass_balance",),
        ("mass", "balance"),
    ),
    "wall_shear_avg": (
        ("wall_shear_avg",),
        ("wall", "shear", "avg"),
    ),
    "wall_shear_rate_avg": (
        ("wall_shear_rate_avg",),
        ("wall", "shear", "rate", "avg"),
        ("shear_rate_avg",),
    ),
}

CONVERGENCE_PATTERNS = [
    re.compile(r"\bsolution\s+is\s+converged\b", re.IGNORECASE),
    re.compile(r"\bsolution\s+converged\b", re.IGNORECASE),
    re.compile(r"\bconvergence\s+criteria\s+(?:are\s+)?satisfied\b", re.IGNORECASE),
    re.compile(r"\bconverged\b", re.IGNORECASE),
]

NOT_CONVERGED_PATTERN = re.compile(
    r"\b(?:not|never|un)\s*-?\s*converged\b|\bnot\s+yet\s+converged\b",
    re.IGNORECASE,
)

MAX_ITER_PATTERNS = [
    re.compile(r"\breached\s+(?:the\s+)?maximum\s+(?:number\s+of\s+)?iterations\b", re.IGNORECASE),
    re.compile(r"\bmaximum\s+(?:number\s+of\s+)?iterations\s+(?:reached|exceeded)\b", re.IGNORECASE),
    re.compile(r"\bmax(?:imum)?\s+iterations?\s+(?:reached|exceeded)\b", re.IGNORECASE),
    re.compile(r"\bstopped\s+after\s+maximum\s+(?:number\s+of\s+)?iterations\b", re.IGNORECASE),
]

HARD_SOLVER_FAILURE_PATTERNS = [
    re.compile(r"\bdivergence\s+detected\b", re.IGNORECASE),
    re.compile(r"\bamg\s+divergence\b", re.IGNORECASE),
    re.compile(r"\bfloating\s+point\s+exception\b", re.IGNORECASE),
    re.compile(r"\bsolver\s+(?:fatal|failed|aborted|terminated)\b", re.IGNORECASE),
    re.compile(r"\bsolver\s+fatal\s+error\b", re.IGNORECASE),
    re.compile(r"\bcalculation\s+(?:failed|aborted|terminated)\b", re.IGNORECASE),
    re.compile(r"\bsolution\s+diverged\b", re.IGNORECASE),
    re.compile(r"\bdiverged\b", re.IGNORECASE),
    re.compile(r"\berror\s+encountered\s+in\s+critical\s+code\s+section\b", re.IGNORECASE),
    re.compile(r"\bsegmentation\s+fault\b", re.IGNORECASE),
    re.compile(r"\bprocess\s+aborted\b", re.IGNORECASE),
    re.compile(r"\bfluent\s+abnormal\s+exit\b", re.IGNORECASE),
    re.compile(r"\babnormal\s+exit\b", re.IGNORECASE),
    re.compile(r"\breversed\s+flow\b.*\babort", re.IGNORECASE),
    re.compile(r"\babort(?:ed|ing)?\b.*\breversed\s+flow\b", re.IGNORECASE),
]

REPORT_EXPRESSION_WARNING_PATTERNS = [
    re.compile(r"\bExpression\s+Error\s+UsedIn\b", re.IGNORECASE),
]

GRAPHICS_ERROR_PATTERNS = [
    re.compile(r"\bError\b.*(?:graphics|color|contour|display|save_picture|object\s+is\s+not\s+active)", re.IGNORECASE),
    re.compile(r"(?:graphics|results/graphics|contour\.display|color_map\.visible|save_picture).*?\bError\b", re.IGNORECASE),
    re.compile(r"\bapi-set-var\b", re.IGNORECASE),
]

MESHING_ERROR_PATTERNS = [
    re.compile(r"\b(?:error|failed|failure|aborted|fatal)\b", re.IGNORECASE),
]

UDF_COMPILE_ERROR_PATTERNS = [
    re.compile(r"\b(?:scons|libudf|udf|clang|user_nt\.udf|udf_names\.c)\b.*\b(?:error|failed|failure|fatal)\b", re.IGNORECASE),
    re.compile(r"\b(?:error|failed|failure|fatal)\b.*\b(?:scons|libudf|udf|clang|user_nt\.udf|udf_names\.c)\b", re.IGNORECASE),
    re.compile(r"\.obj\s+Error\b", re.IGNORECASE),
    re.compile(r"\bscons:\s+\*\*\*", re.IGNORECASE),
]

LAUNCH_ERROR_PATTERNS = [
    re.compile(r"\bfluent\s+abnormal\s+exit\b", re.IGNORECASE),
    re.compile(r"\babnormal\s+exit\b", re.IGNORECASE),
    re.compile(r"\bprocess\s+aborted\b", re.IGNORECASE),
    re.compile(r"\bsegmentation\s+fault\b", re.IGNORECASE),
]

WARNING_PATTERNS = [
    re.compile(r"\breversed\s+flow\b", re.IGNORECASE),
    re.compile(r"\bturbulent\s+viscosity\s+limited\b", re.IGNORECASE),
    re.compile(r"\bwarning\b", re.IGNORECASE),
]

COMPLETION_PATTERNS = [
    re.compile(r"\bcalculation\s+complete\b", re.IGNORECASE),
    re.compile(r"\bcalculation\s+completed\b", re.IGNORECASE),
    re.compile(r"\bsolution\s+complete\b", re.IGNORECASE),
    re.compile(r"\bcompleted\b", re.IGNORECASE),
    re.compile(r"\bdone\b", re.IGNORECASE),
]

ITERATION_PATTERNS = [
    re.compile(
        r"\b(?:iter|iteration)(?:ation)?(?:\s+count|\s+number|\s+no\.?)?\s*(?:#|:|=)?\s*(\d{1,7})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:at|after|completed|complete)\s+(\d{1,7})\s+(?:iter|iteration|iterations)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,7})\s+(?:iter|iteration|iterations)\b", re.IGNORECASE),
    re.compile(r"\bmaximum\s+(?:number\s+of\s+)?iterations\s*(?:reached|exceeded|:|=)?\s*(\d{1,7})\b", re.IGNORECASE),
]

SCI_NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)")
LEADING_INT_PATTERN = re.compile(r"^\s*(\d{1,7})\s+")


@dataclass
class LogFileAnalysis:
    path: Path
    role: str = ROLE_UNKNOWN
    text: str = ""
    truncated: bool = False
    error: str = ""


@dataclass
class LogParseResult:
    convergence_status: str = UNKNOWN_NO_LOG
    stop_reason: str = ""
    max_iteration_detected: Optional[int] = None
    hit_max_iter_target: bool = False
    convergence_evidence: list[str] = field(default_factory=list)
    failure_evidence: list[str] = field(default_factory=list)
    warning_evidence: list[str] = field(default_factory=list)
    completion_evidence: list[str] = field(default_factory=list)
    max_iter_evidence: list[str] = field(default_factory=list)
    iteration_notes: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    parsed_log_files: list[str] = field(default_factory=list)
    likely_complete_from_logs: bool = False
    report_expression_warning_count: int = 0
    report_expression_warning_files: list[str] = field(default_factory=list)
    report_expression_warning_evidence: list[str] = field(default_factory=list)
    postprocessing_graphics_error_files: list[str] = field(default_factory=list)
    postprocessing_graphics_error_evidence: list[str] = field(default_factory=list)
    meshing_error_files: list[str] = field(default_factory=list)
    meshing_error_evidence: list[str] = field(default_factory=list)
    udf_compile_error_files: list[str] = field(default_factory=list)
    udf_compile_error_evidence: list[str] = field(default_factory=list)
    launch_error_files: list[str] = field(default_factory=list)
    launch_error_evidence: list[str] = field(default_factory=list)


# Fallback when batch_config has no usable common_solver_settings.max_iterations.
# Kept at 2000 to match run_config.max_iterations / _solver_common default.
_DEFAULT_MAX_ITER_FALLBACK = DEFAULT_MAX_ITERATIONS_FALLBACK


def max_iter_target_from_common_solver_settings(settings) -> int:
    """Resolve max_iter from an injected common_solver_settings mapping.

    Pure relative to batch_config.py: callers supply the settings (or None).
    """
    return max_iterations_from_common_solver_settings(
        settings,
        fallback=_DEFAULT_MAX_ITER_FALLBACK,
    )


def _load_batch_config_module(batch_config_path: Path):
    spec = importlib.util.spec_from_file_location(
        "_batch_config_for_inventory", batch_config_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load batch config: {batch_config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_max_iter_target(batch_config_path: Path | None = None) -> int:
    """Read the batch campaign iteration cap from batch_config when available."""
    path = (
        batch_config_path
        if batch_config_path is not None
        else project_root() / "configs" / "batch_config.py"
    )
    try:
        module = _load_batch_config_module(path)
    except (ImportError, OSError) as exc:
        print(
            "WARNING: could not load batch_config for --max-iter default "
            f"({type(exc).__name__}: {exc}); "
            f"falling back to {_DEFAULT_MAX_ITER_FALLBACK}.",
            file=sys.stderr,
        )
        return _DEFAULT_MAX_ITER_FALLBACK

    settings = getattr(module, "common_solver_settings", None)
    return max_iter_target_from_common_solver_settings(settings)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a read-only inventory of RO CFD result cases."
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
        help="Directory for inventory outputs (default: RO_DATA_ROOT/inventory).",
    )
    parser.add_argument("--geo-name", type=str, default=None, help="Optional geometry filter.")
    parser.add_argument("--case-name", type=str, default=None, help="Optional case-name filter.")
    parser.add_argument(
        "--max-iter",
        type=int,
        default=_default_max_iter_target(),
        help=(
            "Maximum iteration target "
            "(default: batch_config common_solver_settings.max_iterations)."
        ),
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden or underscored geometry/case directories where otherwise skipped.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print scan details.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and print planned writes, but do not write inventory files.",
    )
    return parser.parse_args(argv)


def resolve_path_defaults(args: argparse.Namespace) -> argparse.Namespace:
    args.results_root = (args.results_root or runs_root()).resolve()
    if not args.results_root.is_dir():
        raise NotADirectoryError(
            f"Runs root is not an existing directory: {args.results_root}"
        )
    args.output_dir = (args.output_dir or data_root() / "inventory").resolve()
    return args


def path_to_str(path: Optional[Path]) -> str:
    return "" if path is None else path.as_posix()


def list_to_csv_cell(values: Iterable[Any]) -> str:
    return "; ".join(str(v) for v in values if str(v))


def flatten_csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return list_to_csv_cell(value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    if value is None:
        return ""
    return value


def has_windows_drive_component(path: Path) -> bool:
    return any(re.fullmatch(r"[A-Za-z]:", part) for part in path.parts)


def ensure_safe_output_dir(path: Path) -> None:
    if platform.system() != "Windows" and has_windows_drive_component(path):
        raise RuntimeError(
            f"Refusing to create Windows-style output path on this OS: {path}"
        )
    path.mkdir(parents=True, exist_ok=True)


def sort_paths(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: p.as_posix().lower())


def convergence_status_from_stop_reason(stop_reason: str) -> str:
    """Map a run-manifest stop_reason onto inventory convergence_status."""
    if stop_reason in {"residual_converged", "qoi_converged"}:
        return CONVERGED
    if stop_reason == "max_iter_reached":
        return MAX_ITER_REACHED
    if stop_reason == "diverged":
        return FAILED_OR_DIVERGED
    if stop_reason in {
        "unknown_early_stop",
        "qoi_report_unavailable",
        "iteration_unknown",
        "not_run",
        "RUNNING",
    }:
        return POSSIBLY_INCOMPLETE
    raise ValueError(f"Unsupported run manifest stop_reason: {stop_reason!r}")


def discover_cases(
    results_root: Path,
    geo_name_filter: Optional[str],
    case_name_filter: Optional[str],
    include_hidden: bool,
    verbose: bool,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for manifest_path, payload in iter_run_manifests(
        results_root, include_hidden=include_hidden
    ):
        geo_id = str(payload["geo_id"])
        run_id = str(payload["run_id"])
        if geo_name_filter and geo_id != geo_name_filter:
            continue
        if case_name_filter and run_id != case_name_filter:
            continue

        case_dir = manifest_path.parent
        post_dir = case_dir / "post"
        reports_dir = post_dir / "reports"
        contours_dir = post_dir / "figures" / "contours"
        cases.append(
            {
                "geo_name": geo_id,
                "case_name": run_id,
                "family": payload["family"],
                "geo_id": geo_id,
                "mesh_id": payload["mesh_id"],
                "run_id": run_id,
                "u_target_ms": payload["u_target_ms"],
                "p_gauge_pa": payload["p_gauge_pa"],
                "stop_reason": payload["stop_reason"],
                "case_dir": path_to_str(case_dir),
                "post_dir": path_to_str(post_dir),
                "reports_dir": path_to_str(reports_dir),
                "contours_dir": path_to_str(contours_dir),
                "_case_dir_path": case_dir,
                "_post_dir_path": post_dir,
                "_reports_dir_path": reports_dir,
                "_contours_dir_path": contours_dir,
            }
        )
        if verbose:
            print(f"  manifest {manifest_path}: {geo_id}/{run_id}")

    return cases


def pick_expected_final_file(
    files: list[Path],
    geo_name: str,
    case_name: str,
    suffix: str,
) -> Optional[Path]:
    expected = f"{geo_name}_{case_name}_final.{suffix}.h5"
    for path in files:
        if path.name == expected:
            return path
    final_files = [p for p in files if p.name.endswith(f"_final.{suffix}.h5")]
    return sort_paths(final_files)[0] if final_files else None


def detect_case_data_files(case_record: dict[str, Any]) -> None:
    case_dir = case_record["_case_dir_path"]
    geo_name = str(case_record["geo_name"])
    case_name = str(case_record["case_name"])

    cas_files = sort_paths(case_dir.glob("*.cas.h5"))
    dat_files = sort_paths(case_dir.glob("*.dat.h5"))
    final_cas = pick_expected_final_file(cas_files, geo_name, case_name, "cas")
    final_dat = pick_expected_final_file(dat_files, geo_name, case_name, "dat")

    case_record.update(
        {
            "cas_files": [path_to_str(p) for p in cas_files],
            "dat_files": [path_to_str(p) for p in dat_files],
            "final_cas_file": path_to_str(final_cas),
            "final_dat_file": path_to_str(final_dat),
            "has_final_cas": final_cas is not None and final_cas.is_file(),
            "has_final_dat": final_dat is not None and final_dat.is_file(),
            "has_case_data_pair": bool(final_cas and final_dat and final_cas.is_file() and final_dat.is_file()),
            "cas_file_count": len(cas_files),
            "dat_file_count": len(dat_files),
        }
    )


def is_log_candidate(path: Path) -> bool:
    lowered = path.name.lower()
    return path.suffix.lower() in LOG_SUFFIXES or any(k in lowered for k in LOG_NAME_KEYWORDS)


def is_fluent_node_error_log(path: Path) -> bool:
    """True for Fluent's auto-generated fluent-<node>-error.log crash files.

    These are written whenever a Fluent session dies for any reason,
    including a post-processing graphics crash long after the case was
    solved successfully; they are not on their own evidence of a bad solve.
    """
    return bool(FLUENT_NODE_ERROR_LOG_PATTERN.search(path.name))


def find_log_files(case_dir: Path) -> tuple[list[Path], list[Path], Optional[Path]]:
    """Collect log candidates under one run directory.

    ``os.walk`` starts at the run_id leaf (the manifest parent). Sibling runs
    are outside that tree, so transcripts cannot be merged across cases.
    """
    candidates: list[Path] = []
    for root, dirs, files in os.walk(case_dir):
        dirs[:] = [
            d for d in dirs
            if d.lower() not in LOG_WALK_SKIP_DIRS and not d.startswith(".")
        ]
        root_path = Path(root)
        for file_name in files:
            path = root_path / file_name
            if is_log_candidate(path):
                candidates.append(path)

    candidates = sort_paths(set(candidates))
    transcript_files = [p for p in candidates if "transcript" in p.name.lower()]

    latest: Optional[Path] = None
    if candidates:
        latest = max(candidates, key=lambda p: safe_mtime(p))
    return candidates, transcript_files, latest


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(4096)
    except OSError:
        return True
    return b"\x00" in sample


def read_text_sample(path: Path) -> tuple[str, bool, Optional[str]]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size <= TEXT_HEAD_BYTES + TEXT_TAIL_BYTES:
                data = fh.read()
                truncated = False
            else:
                head = fh.read(TEXT_HEAD_BYTES)
                fh.seek(max(size - TEXT_TAIL_BYTES, 0))
                tail = fh.read(TEXT_TAIL_BYTES)
                data = head + b"\n\n...[middle of large log omitted by inventory scanner]...\n\n" + tail
                truncated = True
    except OSError as exc:
        return "", False, str(exc)

    if b"\x00" in data[:4096]:
        return "", False, "binary file skipped"

    return data.decode("utf-8", errors="ignore"), truncated, None


def add_evidence(target: list[str], path: Path, line_no: int, line: str) -> None:
    if len(target) >= MAX_EVIDENCE_PER_KIND:
        return
    clean = " ".join(line.strip().split())
    if clean:
        if len(clean) > 240:
            clean = clean[:237] + "..."
        target.append(f"{path_to_str(path)}:{line_no}: {clean}")


def extract_iteration_numbers(line: str) -> list[int]:
    values: list[int] = []
    for pattern in ITERATION_PATTERNS:
        for match in pattern.finditer(line):
            value = safe_int(match.group(1))
            if is_plausible_iteration(value):
                values.append(value)

    # Fluent residual tables often start with the iteration number followed by
    # several scientific-notation residual columns.
    leading = LEADING_INT_PATTERN.match(line)
    if leading and len(SCI_NUMBER_PATTERN.findall(line)) >= 2:
        value = safe_int(leading.group(1))
        if is_plausible_iteration(value):
            values.append(value)

    return values


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def is_plausible_iteration(value: Optional[int]) -> bool:
    return value is not None and 0 < value <= 10_000_000


def role_keyword_score(haystack: str, role: str) -> int:
    return sum(1 for keyword in ROLE_KEYWORDS.get(role, ()) if keyword in haystack)


def role_filename_score(path: Path, role: str) -> int:
    haystack = path.as_posix().lower()
    return sum(1 for keyword in FILENAME_ROLE_HINTS.get(role, ()) if keyword in haystack)


def classify_log_role(path: Path, text: str) -> str:
    content = text.lower()
    scores: dict[str, int] = {}
    for role in LOG_ROLES:
        if role == ROLE_UNKNOWN:
            continue
        scores[role] = role_keyword_score(content, role) * 2 + role_filename_score(path, role)

    solver_score = scores.get(ROLE_SOLVER_RUN, 0)
    specific_roles = [
        ROLE_POSTPROCESSING_GRAPHICS,
        ROLE_UDF_COMPILE,
        ROLE_MESHING,
        ROLE_REPORT_EXTRACTION,
    ]
    best_specific = max(specific_roles, key=lambda role: scores.get(role, 0))
    best_specific_score = scores.get(best_specific, 0)

    if best_specific_score > 0:
        # Only let solver_run override a specific workflow role when the solver
        # evidence is substantially stronger. This avoids Fluent graphics logs
        # with incidental iteration/residual text becoming solver-run evidence.
        if solver_score >= best_specific_score + 6:
            return ROLE_SOLVER_RUN
        return best_specific
    if solver_score > 0:
        return ROLE_SOLVER_RUN
    return ROLE_UNKNOWN


def analyze_log_files(log_files: list[Path]) -> list[LogFileAnalysis]:
    analyses: list[LogFileAnalysis] = []
    files_to_parse = sorted(log_files, key=lambda p: safe_mtime(p), reverse=True)
    for idx, path in enumerate(files_to_parse):
        if idx >= MAX_LOG_FILES_TO_PARSE:
            analyses.append(
                LogFileAnalysis(
                    path=path,
                    role=classify_log_role(path, ""),
                    error=(
                        f"content not parsed; newest {MAX_LOG_FILES_TO_PARSE} "
                        "candidate log files were already analyzed"
                    ),
                )
            )
            continue
        if is_probably_binary(path):
            analyses.append(
                LogFileAnalysis(path=path, role=classify_log_role(path, ""), error="binary file skipped")
            )
            continue

        text, truncated, error = read_text_sample(path)
        role = classify_log_role(path, text)
        analyses.append(
            LogFileAnalysis(
                path=path,
                role=role,
                text=text,
                truncated=truncated,
                error=error or "",
            )
        )
    return analyses


def add_file_once(target: list[str], path: Path) -> None:
    value = path_to_str(path)
    if value and value not in target:
        target.append(value)


def scan_auxiliary_log_evidence(analyses: list[LogFileAnalysis], result: LogParseResult) -> None:
    for analysis in analyses:
        if analysis.error:
            result.parse_errors.append(f"{path_to_str(analysis.path)}: {analysis.error}")
        if analysis.truncated:
            result.iteration_notes.append(
                f"{path_to_str(analysis.path)}: parsed from a head/tail sample."
            )
        if not analysis.text:
            continue

        for line_no, line in enumerate(analysis.text.splitlines(), start=1):
            has_expression_warning = any(
                pattern.search(line) for pattern in REPORT_EXPRESSION_WARNING_PATTERNS
            )
            if has_expression_warning:
                result.report_expression_warning_count += 1
                add_file_once(result.report_expression_warning_files, analysis.path)
                add_evidence(result.report_expression_warning_evidence, analysis.path, line_no, line)

            if analysis.role == ROLE_POSTPROCESSING_GRAPHICS and any(
                pattern.search(line) for pattern in GRAPHICS_ERROR_PATTERNS
            ):
                add_file_once(result.postprocessing_graphics_error_files, analysis.path)
                add_evidence(result.postprocessing_graphics_error_evidence, analysis.path, line_no, line)

            if analysis.role == ROLE_MESHING and any(
                pattern.search(line) for pattern in MESHING_ERROR_PATTERNS
            ):
                add_file_once(result.meshing_error_files, analysis.path)
                add_evidence(result.meshing_error_evidence, analysis.path, line_no, line)

            if analysis.role == ROLE_UDF_COMPILE and any(
                pattern.search(line) for pattern in UDF_COMPILE_ERROR_PATTERNS
            ):
                add_file_once(result.udf_compile_error_files, analysis.path)
                add_evidence(result.udf_compile_error_evidence, analysis.path, line_no, line)

            if analysis.role in {ROLE_SOLVER_RUN, ROLE_UDF_COMPILE, ROLE_UNKNOWN} and any(
                pattern.search(line) for pattern in LAUNCH_ERROR_PATTERNS
            ):
                add_file_once(result.launch_error_files, analysis.path)
                add_evidence(result.launch_error_evidence, analysis.path, line_no, line)


def parse_logs(
    analyses: list[LogFileAnalysis],
    max_iter_target: int,
    has_case_data_pair: bool,
    has_summary_metrics_wide: bool,
) -> LogParseResult:
    result = LogParseResult()
    if not analyses:
        result.convergence_status = UNKNOWN_NO_LOG
        return result

    scan_auxiliary_log_evidence(analyses, result)
    solver_analyses = [a for a in analyses if a.role == ROLE_SOLVER_RUN and a.text]
    max_iteration: Optional[int] = None
    any_parsed = False

    for analysis in solver_analyses:
        path = analysis.path
        text = analysis.text
        any_parsed = True
        result.parsed_log_files.append(path_to_str(path))

        for line_no, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            for value in extract_iteration_numbers(line):
                max_iteration = value if max_iteration is None else max(max_iteration, value)

            if any(pattern.search(line) for pattern in MAX_ITER_PATTERNS):
                add_evidence(result.max_iter_evidence, path, line_no, line)

            if any(pattern.search(line) for pattern in CONVERGENCE_PATTERNS):
                if not NOT_CONVERGED_PATTERN.search(line):
                    add_evidence(result.convergence_evidence, path, line_no, line)
                else:
                    add_evidence(result.max_iter_evidence, path, line_no, line)

            if any(pattern.search(line) for pattern in HARD_SOLVER_FAILURE_PATTERNS):
                add_evidence(result.failure_evidence, path, line_no, line)

            if any(pattern.search(line) for pattern in WARNING_PATTERNS):
                add_evidence(result.warning_evidence, path, line_no, line)

            if any(pattern.search(line) for pattern in COMPLETION_PATTERNS):
                # Avoid treating "not completed" as completion evidence.
                if "not complete" not in lowered and "not completed" not in lowered:
                    add_evidence(result.completion_evidence, path, line_no, line)

    result.max_iteration_detected = max_iteration
    result.hit_max_iter_target = bool(max_iteration is not None and max_iteration >= max_iter_target)
    has_solver_role_logs = any(a.role == ROLE_SOLVER_RUN for a in analyses)
    if max_iteration is None:
        if solver_analyses:
            result.iteration_notes.append("No plausible iteration number detected in parsed solver log text.")
        else:
            result.iteration_notes.append(
                "No solver_run log was found; convergence status is based on non-solver logs only."
            )

    stop_reason = ""
    for analysis in analyses:
        if analysis.role != ROLE_SOLVER_RUN or not analysis.text:
            continue
        parsed_reason = parse_stop_reason_from_text(analysis.text)
        if parsed_reason:
            stop_reason = parsed_reason
    result.stop_reason = stop_reason

    has_failure = bool(
        result.failure_evidence
        or result.udf_compile_error_evidence
        or result.launch_error_evidence
    )
    has_max_iter = bool(result.max_iter_evidence) or (
        result.hit_max_iter_target and not result.convergence_evidence
    )
    has_converged = bool(result.convergence_evidence)
    has_completion = bool(result.completion_evidence)

    if stop_reason == "diverged" or has_failure:
        result.convergence_status = FAILED_OR_DIVERGED
        if not stop_reason and has_failure:
            result.stop_reason = "diverged"
    elif stop_reason == "max_iter_reached" or (has_max_iter and not stop_reason):
        result.convergence_status = MAX_ITER_REACHED
        if not stop_reason:
            result.stop_reason = "max_iter_reached"
    elif stop_reason in {"residual_converged", "qoi_converged"}:
        result.convergence_status = CONVERGED
    elif stop_reason in {
        "unknown_early_stop",
        "qoi_report_unavailable",
        "iteration_unknown",
        "not_run",
    }:
        result.convergence_status = POSSIBLY_INCOMPLETE
    elif has_converged:
        result.convergence_status = CONVERGED
        # Legacy runs without an explicit marker remain blank.
        result.stop_reason = ""
    elif has_completion:
        result.convergence_status = POSSIBLY_INCOMPLETE
    elif has_case_data_pair:
        result.convergence_status = POSSIBLY_INCOMPLETE
    elif not any_parsed:
        result.convergence_status = UNKNOWN_UNPARSED if has_solver_role_logs else UNKNOWN_NO_LOG
    else:
        result.convergence_status = UNKNOWN_UNPARSED

    result.likely_complete_from_logs = bool(
        (has_converged or has_completion or stop_reason in {"residual_converged", "qoi_converged"})
        and not has_failure
        and not bool(result.max_iter_evidence)
        and stop_reason
        not in {
            "max_iter_reached",
            "diverged",
            "unknown_early_stop",
            "qoi_report_unavailable",
            "iteration_unknown",
            "not_run",
        }
    )
    return result


def _evidence_entry_path(entry: str) -> str:
    # Evidence lines are formatted as "<path>:<line_no>: <text>" (see add_evidence).
    return entry.split(":", 1)[0] if ":" in entry else entry


def _exclude_evidence_from_paths(entries: list[str], exclude_paths: set[str]) -> list[str]:
    return [e for e in entries if _evidence_entry_path(e) not in exclude_paths]


def _exclude_files(paths_list: list[str], exclude_paths: set[str]) -> list[str]:
    return [p for p in paths_list if p not in exclude_paths]


def reclassify_postprocessing_crash_logs(
    case_record: dict[str, Any],
    log_files: list[Path],
    parsed: LogParseResult,
) -> tuple[bool, list[str], str]:
    """Detect fluent-<node>-error.log crashes that happened during
    post-processing graphics (after the case was already solved), and strip
    their contribution to hard-solver-failure evidence so they do not force
    NEEDS_SOLVER_RERUN on an otherwise-good case.

    Only reclassifies when the case already has a final cas/dat pair,
    summary_metrics_wide.csv, and all basic PyEnSight contour outputs — i.e.
    the solve is demonstrably already complete. Mutates `parsed` in place
    (filtering out evidence attributable only to the crash log) when the
    reclassification applies. Returns
    (has_postprocessing_runtime_crash, crash_evidence_files, failed_stage).
    """
    crash_files = [p for p in log_files if is_fluent_node_error_log(p)]
    if not crash_files:
        return False, [], ""

    has_pair = bool(case_record.get("has_case_data_pair"))
    has_summary = bool(case_record.get("has_summary_metrics_wide"))
    has_all_pyensight = bool(case_record.get("has_all_pyensight_contours"))
    if not (has_pair and has_summary and has_all_pyensight):
        return False, [], ""

    final_cas_str = str(case_record.get("final_cas_file") or "")
    final_dat_str = str(case_record.get("final_dat_file") or "")
    solved_mtime = max(
        safe_mtime(Path(final_cas_str)) if final_cas_str else 0.0,
        safe_mtime(Path(final_dat_str)) if final_dat_str else 0.0,
    )
    contour_output_mtimes = [
        safe_mtime(Path(p))
        for key in (
            "cp_contour_files",
            "water_flux_contour_files",
            "lmh_contour_files",
            "salt_flux_contour_files",
        )
        for p in case_record.get(key, [])
    ]
    outputs_mtime = max([solved_mtime] + contour_output_mtimes) if contour_output_mtimes else solved_mtime
    newest_crash_mtime = max(safe_mtime(p) for p in crash_files)

    if newest_crash_mtime < outputs_mtime:
        # The crash predates the already-present solve/contour outputs; leave
        # any hard-failure evidence it contributed as-is.
        return False, [], ""

    crash_paths = {path_to_str(p) for p in crash_files}
    parsed.failure_evidence = _exclude_evidence_from_paths(parsed.failure_evidence, crash_paths)
    parsed.launch_error_evidence = _exclude_evidence_from_paths(parsed.launch_error_evidence, crash_paths)
    parsed.launch_error_files = _exclude_files(parsed.launch_error_files, crash_paths)

    shear_status_upper = str(case_record.get("shear_export_status") or "").upper()
    failed_stage = ""
    if not case_record.get("has_shear_contour") or shear_status_upper in ("FAILED", "FAIL"):
        failed_stage = "shear"

    return True, sorted(crash_paths), failed_stage


def detect_logs_and_convergence(case_record: dict[str, Any], max_iter_target: int) -> None:
    case_dir = case_record["_case_dir_path"]
    stop_reason = str(case_record.get("stop_reason") or "")
    if not stop_reason:
        raise ValueError(f"Run directory has no manifest stop_reason: {case_dir}")
    convergence_status = convergence_status_from_stop_reason(stop_reason)

    log_files, transcript_files, latest_log = find_log_files(case_dir)
    analyses = analyze_log_files(log_files)
    log_files_by_role = {
        role: [path_to_str(a.path) for a in analyses if a.role == role]
        for role in LOG_ROLES
    }
    parsed = parse_logs(
        analyses,
        max_iter_target,
        has_case_data_pair=bool(case_record.get("has_case_data_pair")),
        has_summary_metrics_wide=bool(case_record.get("has_summary_metrics_wide")),
    )

    (
        has_postprocessing_runtime_crash,
        postprocessing_runtime_crash_evidence,
        failed_postprocessing_stage,
    ) = reclassify_postprocessing_crash_logs(case_record, log_files, parsed)

    hard_solver_failure = bool(
        parsed.failure_evidence
        or parsed.udf_compile_error_evidence
        or parsed.launch_error_evidence
    )
    case_record.update(
        {
            "has_postprocessing_runtime_crash": has_postprocessing_runtime_crash,
            "postprocessing_runtime_crash_evidence": postprocessing_runtime_crash_evidence,
            "failed_postprocessing_stage": failed_postprocessing_stage,
            "log_files": [path_to_str(p) for p in log_files],
            "transcript_files": [path_to_str(p) for p in transcript_files],
            "latest_log_file": path_to_str(latest_log),
            "log_file_count": len(log_files),
            "transcript_file_count": len(transcript_files),
            "log_files_by_role": log_files_by_role,
            "log_role_by_file": {path_to_str(a.path): a.role for a in analyses},
            "solver_log_files": log_files_by_role[ROLE_SOLVER_RUN],
            "postprocessing_log_files": log_files_by_role[ROLE_POSTPROCESSING_GRAPHICS],
            "report_log_files": log_files_by_role[ROLE_REPORT_EXTRACTION],
            "meshing_log_files": log_files_by_role[ROLE_MESHING],
            "udf_compile_log_files": log_files_by_role[ROLE_UDF_COMPILE],
            "unknown_log_files": log_files_by_role[ROLE_UNKNOWN],
            "convergence_status": convergence_status,
            "stop_reason": stop_reason,
            "max_iteration_detected": parsed.max_iteration_detected,
            "max_iter_target": max_iter_target,
            "hit_max_iter_target": parsed.hit_max_iter_target,
            "convergence_evidence": parsed.convergence_evidence,
            "failure_evidence": parsed.failure_evidence,
            "warning_evidence": parsed.warning_evidence,
            "completion_evidence": parsed.completion_evidence,
            "max_iter_evidence": parsed.max_iter_evidence,
            "iteration_notes": parsed.iteration_notes,
            "log_parse_errors": parsed.parse_errors,
            "parsed_log_files": parsed.parsed_log_files,
            "likely_complete_from_logs": parsed.likely_complete_from_logs,
            "report_expression_warning_count": parsed.report_expression_warning_count,
            "report_expression_warning_files": parsed.report_expression_warning_files,
            "report_expression_warning_evidence": parsed.report_expression_warning_evidence,
            "postprocessing_graphics_error_files": parsed.postprocessing_graphics_error_files,
            "postprocessing_graphics_error_evidence": parsed.postprocessing_graphics_error_evidence,
            "meshing_error_files": parsed.meshing_error_files,
            "meshing_error_evidence": parsed.meshing_error_evidence,
            "udf_compile_error_files": parsed.udf_compile_error_files,
            "udf_compile_error_evidence": parsed.udf_compile_error_evidence,
            "launch_error_files": parsed.launch_error_files,
            "launch_error_evidence": parsed.launch_error_evidence,
            "has_report_expression_warnings": bool(parsed.report_expression_warning_count),
            "has_postprocessing_graphics_errors": bool(parsed.postprocessing_graphics_error_evidence),
            "has_meshing_errors": bool(parsed.meshing_error_evidence),
            "has_udf_compile_errors": bool(parsed.udf_compile_error_evidence),
            "has_launch_errors": bool(parsed.launch_error_evidence),
            "hard_solver_failure_detected": hard_solver_failure,
            "max_iter_only": bool(
                convergence_status == MAX_ITER_REACHED and not hard_solver_failure
            ),
        }
    )


def read_summary_first_row(path: Path) -> tuple[list[str], dict[str, str], Optional[str]]:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            first_row_values = next(reader, [])
    except StopIteration:
        return [], {}, "summary_metrics_wide.csv has no header or data row"
    except OSError as exc:
        return [], {}, str(exc)
    except csv.Error as exc:
        return [], {}, str(exc)

    row = {
        col: first_row_values[idx] if idx < len(first_row_values) else ""
        for idx, col in enumerate(header)
    }
    return header, row, None


def normalize_column_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def column_matches(norm_column: str, tokens: tuple[str, ...], canonical: str) -> bool:
    norm_tokens = tuple(normalize_column_name(token) for token in tokens)
    if canonical == "wall_shear_avg" and "rate" in norm_column:
        return False
    if len(norm_tokens) == 1:
        token = norm_tokens[0]
        if canonical == "lmh":
            return norm_column == "lmh" or norm_column.startswith("lmh_") or norm_column.endswith("_lmh")
        if canonical == "cp" and token == "cp":
            return norm_column == "cp" or norm_column.startswith("cp_") or norm_column.endswith("_cp")
        return token in norm_column
    return all(token in norm_column for token in norm_tokens)


def find_summary_column(header: list[str], canonical: str) -> Optional[str]:
    norm_columns = [(col, normalize_column_name(col)) for col in header]

    for col, norm in norm_columns:
        if norm == normalize_column_name(canonical):
            return col

    for token_group in SUMMARY_FIELDS[canonical]:
        for col, norm in norm_columns:
            if column_matches(norm, token_group, canonical):
                return col
    return None


def detect_report_status(case_record: dict[str, Any]) -> None:
    reports_dir = case_record["_reports_dir_path"]
    summary_path = reports_dir / "summary_metrics_wide.csv"
    report_files = sort_paths(reports_dir.glob("*.csv")) if reports_dir.is_dir() else []

    header: list[str] = []
    row: dict[str, str] = {}
    summary_error = ""
    if summary_path.is_file():
        header, row, error = read_summary_first_row(summary_path)
        summary_error = error or ""

    summary_values: dict[str, str] = {}
    summary_columns: dict[str, str] = {}
    for canonical in SUMMARY_FIELDS:
        col = find_summary_column(header, canonical) if header else None
        summary_columns[canonical] = col or ""
        summary_values[canonical] = row.get(col, "") if col else ""

    update: dict[str, Any] = {
        "has_summary_metrics_wide": summary_path.is_file(),
        "summary_metrics_wide_file": path_to_str(summary_path) if summary_path.is_file() else "",
        "report_csv_count": len(report_files),
        "report_files": [path_to_str(p) for p in report_files],
        "summary_metrics_read_error": summary_error,
        "summary_metric_columns": summary_columns,
    }
    for canonical, value in summary_values.items():
        update[canonical] = value
        update[f"{canonical}_column"] = summary_columns.get(canonical, "")
    case_record.update(update)


def pngs_matching(contours_dir: Path, required_terms: tuple[str, ...]) -> list[Path]:
    if not contours_dir.is_dir():
        return []
    matches: list[Path] = []
    for path in contours_dir.glob("*.png"):
        lowered = path.name.lower()
        if all(term in lowered for term in required_terms):
            matches.append(path)
    return sort_paths(matches)


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


def parse_contour_status(path: Path) -> dict[str, Any]:
    payload, error = safe_read_json(path)
    result: dict[str, Any] = {
        "contour_export_overall_status": "",
        "contour_success_count": None,
        "contour_failed_count": None,
        "contour_status_parse_error": error,
    }
    if not payload:
        return result

    summary = payload.get("summary")
    if isinstance(summary, dict):
        success = int_from_any(summary.get("success"))
        failed = int_from_any(summary.get("failed"))
        warn = int_from_any(summary.get("warn")) or 0
        skipped = int_from_any(summary.get("skipped")) or 0
        total = int_from_any(summary.get("total")) or 0
    else:
        records = payload.get("records")
        if isinstance(records, list):
            statuses = Counter(
                str(r.get("status", "")).upper()
                for r in records
                if isinstance(r, dict)
            )
            success = statuses.get("SUCCESS", 0)
            failed = statuses.get("FAILED", 0)
            warn = statuses.get("WARN", 0)
            skipped = statuses.get("SKIPPED_EXISTING", 0) + statuses.get("DRY_RUN", 0)
            total = len(records)
        else:
            success = failed = warn = skipped = total = 0

    explicit_status = str(
        payload.get("overall_status")
        or payload.get("status")
        or ""
    ).strip()
    if explicit_status:
        overall = explicit_status
    elif failed:
        overall = "FAILED"
    elif warn:
        overall = "WARN"
    elif success:
        overall = "SUCCESS"
    elif total and skipped == total:
        overall = "SKIPPED"
    else:
        overall = "UNKNOWN"

    result.update(
        {
            "contour_export_overall_status": overall,
            "contour_success_count": success,
            "contour_failed_count": failed,
        }
    )
    return result


def parse_shear_status(path: Path) -> dict[str, Any]:
    payload, error = safe_read_json(path)
    result = {
        "shear_export_status": "",
        "shear_native_status": "",
        "shear_fallback_status": "",
        "shear_derived_variable_mode": "",
        "shear_legend_mode": "",
        "shear_colorbar_metadata_written": False,
        "shear_status_parse_error": error,
    }
    if not payload:
        return result

    result.update(
        {
            "shear_export_status": str(payload.get("status") or payload.get("overall_status") or ""),
            "shear_native_status": str(payload.get("native_status") or ""),
            "shear_fallback_status": str(payload.get("fallback_status") or ""),
            "shear_derived_variable_mode": str(payload.get("derived_variable_mode") or ""),
            "shear_legend_mode": str(payload.get("legend_mode") or payload.get("requested_legend_mode") or ""),
            "shear_colorbar_metadata_written": bool(payload.get("colorbar_metadata_written")),
        }
    )
    return result


def derive_postprocessing_status(record: dict[str, Any]) -> str:
    if record.get("has_all_basic_contours"):
        return "BASIC_COMPLETE"
    if any(
        record.get(key)
        for key in (
            "has_cp_contour",
            "has_water_flux_contour",
            "has_lmh_contour",
            "has_salt_flux_contour",
            "has_shear_contour",
            "contour_status_file",
            "shear_status_file",
        )
    ):
        return "PARTIAL_CONTOURS"
    if record.get("has_summary_metrics_wide"):
        return "REPORTS_ONLY"
    if int_from_any(record.get("report_csv_count")):
        return "PARTIAL_REPORTS"
    return "NOT_STARTED"


def derive_report_status(record: dict[str, Any]) -> str:
    if record.get("has_summary_metrics_wide"):
        return "SUMMARY_PRESENT"
    if int_from_any(record.get("report_csv_count")):
        return "REPORT_CSV_PRESENT"
    return "MISSING"


def detect_contour_status(case_record: dict[str, Any]) -> None:
    contours_dir = case_record["_contours_dir_path"]
    cp_files = pngs_matching(contours_dir, ("cp_inlet", "membrane"))
    water_flux_files = pngs_matching(contours_dir, ("water_flux", "membrane"))
    lmh_files = pngs_matching(contours_dir, ("lmh", "membrane"))
    salt_flux_files = pngs_matching(contours_dir, ("salt_flux", "membrane"))
    shear_files = pngs_matching(contours_dir, ("shear_rate", "membrane"))

    contour_status_file = contours_dir / "contour_export_status.json"
    shear_status_file = contours_dir / "shear_contour_status.json"
    colorbar_names = [
        "contour_colorbar_ranges.json",
        "contour_colorbar_ranges.txt",
        "contour_colorbar_ranges.csv",
        "shear_colorbar_range.json",
        "shear_colorbar_range.txt",
        "shear_colorbar_range.csv",
    ]
    colorbar_files = [contours_dir / name for name in colorbar_names if (contours_dir / name).is_file()]

    has_all_pyensight = bool(cp_files and water_flux_files and lmh_files and salt_flux_files)
    has_shear = bool(shear_files)

    case_record.update(
        {
            "has_cp_contour": bool(cp_files),
            "has_water_flux_contour": bool(water_flux_files),
            "has_lmh_contour": bool(lmh_files),
            "has_salt_flux_contour": bool(salt_flux_files),
            "has_all_pyensight_contours": has_all_pyensight,
            "has_shear_contour": has_shear,
            "has_all_basic_contours": bool(has_all_pyensight and has_shear),
            "cp_contour_files": [path_to_str(p) for p in cp_files],
            "water_flux_contour_files": [path_to_str(p) for p in water_flux_files],
            "lmh_contour_files": [path_to_str(p) for p in lmh_files],
            "salt_flux_contour_files": [path_to_str(p) for p in salt_flux_files],
            "shear_contour_files": [path_to_str(p) for p in shear_files],
            "contour_status_file": path_to_str(contour_status_file) if contour_status_file.is_file() else "",
            "shear_status_file": path_to_str(shear_status_file) if shear_status_file.is_file() else "",
            "colorbar_metadata_files": [path_to_str(p) for p in colorbar_files],
        }
    )
    case_record.update(parse_contour_status(contour_status_file))
    case_record.update(parse_shear_status(shear_status_file))

    # A stale shear PNG can survive a since-failed rerun; if the status JSON
    # says the shear stage did not succeed, do not trust the PNG.
    shear_status_upper = str(case_record.get("shear_export_status") or "").upper()
    if shear_status_file.is_file() and shear_status_upper not in ("", "SUCCESS"):
        case_record["has_shear_contour"] = False
        case_record["has_all_basic_contours"] = bool(
            case_record.get("has_all_pyensight_contours") and case_record["has_shear_contour"]
        )

    case_record["postprocessing_status"] = derive_postprocessing_status(case_record)
    case_record["report_status"] = derive_report_status(case_record)


def suggested_action(record: dict[str, Any]) -> str:
    if record.get("case_status") == POSTPROCESSED_BASIC:
        if record.get("has_report_expression_warnings") or record.get("has_postprocessing_graphics_errors"):
            return "Basic post-processing is complete; review warning flags only if outputs look suspect."
        return "Basic post-processing is complete."
    if record.get("case_status") == POSTPROCESSED_UNCONVERGED:
        return (
            "Basic post-processing artifacts are complete, but the solve did not converge "
            "(max-iter). Treat as usable-but-capped for screening; do not treat as "
            "MFBO-grade converged. Review residuals before any solver continuation."
        )
    if record.get("case_status") == NEEDS_SHEAR_POSTPROCESSING:
        if record.get("has_postprocessing_runtime_crash"):
            return (
                "Basic PyEnSight contours are complete; the shear stage crashed during "
                "post-processing graphics (not a solver failure). Rerun "
                "pyfluent_shear_contour_export.py with --shear-export-mode fallback "
                "(or batch_postprocess_all_cases.py --retry-shear-fallback-on-failure)."
            )
        return (
            "Basic PyEnSight contours are complete; shear contour export is missing or "
            "failed. Rerun pyfluent_shear_contour_export.py "
            "(--shear-export-mode fallback avoids native Fluent graphics)."
        )
    if record.get("convergence_status") == MAX_ITER_REACHED:
        return "Review max-iter residual/report trends; consider continuing from final data or relaxed solver settings."
    if record.get("needs_solver_rerun"):
        return "Review hard solver/UDF/launch failure evidence, then rerun or repair the failed stage."
    if not record.get("has_case_data_pair"):
        return "Generate or locate the final case/data pair before post-processing."
    if record.get("needs_report_extraction"):
        return "Run report extraction to create post/reports/summary_metrics_wide.csv."
    if record.get("needs_basic_contours") and record.get("needs_shear_contour"):
        return "Run PyEnSight basic contours, then PyFluent shear contour export."
    if record.get("needs_basic_contours"):
        return "Run PyEnSight contour export for missing membrane fields."
    if record.get("needs_shear_contour"):
        return "Run PyFluent shear contour export."
    if record.get("needs_manual_review"):
        return "Review logs and files manually before batch processing."
    return "No immediate action detected."


def combined_failure_evidence(record: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key in (
        "failure_evidence",
        "udf_compile_error_evidence",
        "launch_error_evidence",
        "meshing_error_evidence",
    ):
        value = record.get(key)
        if isinstance(value, list):
            evidence.extend(str(item) for item in value if str(item))
    return evidence


def shorten_evidence(values: list[str], limit: int = 250) -> str:
    text = " | ".join(values)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def derive_inventory_confidence(record: dict[str, Any], case_status: str) -> str:
    convergence_status = str(record.get("convergence_status") or "")
    has_solver_logs = bool(record.get("solver_log_files"))
    has_warning_flags = bool(
        record.get("has_report_expression_warnings")
        or record.get("has_postprocessing_graphics_errors")
        or record.get("has_meshing_errors")
        or record.get("has_udf_compile_errors")
        or record.get("has_launch_errors")
        or record.get("log_parse_errors")
    )

    if record.get("hard_solver_failure_detected"):
        confidence = "HIGH" if combined_failure_evidence(record) else "MEDIUM"
    elif case_status == POSTPROCESSED_BASIC:
        confidence = "HIGH"
    elif convergence_status in {CONVERGED, MAX_ITER_REACHED} and has_solver_logs:
        confidence = "HIGH"
    elif convergence_status == CONVERGED and record.get("has_case_data_pair") and record.get("has_summary_metrics_wide"):
        confidence = "MEDIUM"
    elif convergence_status in {UNKNOWN_NO_LOG, UNKNOWN_UNPARSED}:
        confidence = "LOW"
    else:
        confidence = "MEDIUM"

    if has_warning_flags and confidence == "HIGH" and not record.get("hard_solver_failure_detected"):
        return "MEDIUM"
    return confidence


def classify_case(record: dict[str, Any]) -> None:
    convergence_status = str(record.get("convergence_status") or "")
    has_pair = bool(record.get("has_case_data_pair"))
    has_summary = bool(record.get("has_summary_metrics_wide"))
    has_all_basic = bool(record.get("has_all_basic_contours"))
    hard_failure = bool(record.get("hard_solver_failure_detected"))
    contour_failed_count = int_from_any(record.get("contour_failed_count")) or 0
    contour_status = str(record.get("contour_export_overall_status") or "").upper()
    shear_status = str(record.get("shear_export_status") or "").upper()
    if contour_failed_count > 0 or contour_status in {"FAILED", "FAIL"} or shear_status in {"FAILED", "FAIL"}:
        record["has_postprocessing_graphics_errors"] = True
    likely_complete = convergence_status == CONVERGED

    solver_status_needs_rerun = convergence_status in {MAX_ITER_REACHED, FAILED_OR_DIVERGED}
    needs_reports = bool(has_pair and not has_summary and not hard_failure)
    needs_basic_contours = bool(
        has_pair
        and has_summary
        and convergence_status != FAILED_OR_DIVERGED
        and not record.get("has_all_pyensight_contours")
    )
    needs_shear_contour = bool(
        has_pair
        and has_summary
        and convergence_status != FAILED_OR_DIVERGED
        and not record.get("has_shear_contour")
    )
    needs_shear_postprocessing = bool(
        has_pair
        and has_summary
        and record.get("has_all_pyensight_contours")
        and not record.get("has_shear_contour")
    )
    ready_for_batch_contours = bool(
        has_pair
        and has_summary
        and not has_all_basic
        and convergence_status not in {MAX_ITER_REACHED, FAILED_OR_DIVERGED}
    )
    needs_manual_review = bool(
        convergence_status in {UNKNOWN_NO_LOG, UNKNOWN_UNPARSED, POSSIBLY_INCOMPLETE}
        or record.get("log_parse_errors")
        or record.get("summary_metrics_read_error")
        or record.get("contour_status_parse_error")
        or record.get("shear_status_parse_error")
    )

    if not has_pair:
        case_status = MISSING_CASE_OR_DATA
    elif hard_failure or convergence_status == FAILED_OR_DIVERGED:
        # Solve-untrusted hard failure wins over artifact completeness (F-02c).
        case_status = NEEDS_SOLVER_RERUN
    elif has_all_basic:
        # Completeness claim is gated by solve-trust (F-02c).
        if convergence_status == CONVERGED:
            case_status = POSTPROCESSED_BASIC
        elif convergence_status == MAX_ITER_REACHED:
            case_status = POSTPROCESSED_UNCONVERGED
        elif convergence_status in {
            POSSIBLY_INCOMPLETE,
            UNKNOWN_NO_LOG,
            UNKNOWN_UNPARSED,
        }:
            case_status = UNKNOWN_REVIEW_REQUIRED
        else:
            case_status = UNKNOWN_REVIEW_REQUIRED
    elif needs_shear_postprocessing:
        case_status = NEEDS_SHEAR_POSTPROCESSING
    elif solver_status_needs_rerun:
        case_status = NEEDS_SOLVER_RERUN
    elif needs_reports:
        case_status = NEEDS_REPORT_EXTRACTION
    elif has_pair and has_summary and convergence_status != FAILED_OR_DIVERGED:
        case_status = READY_FOR_POSTPROCESSING
    else:
        case_status = UNKNOWN_REVIEW_REQUIRED

    needs_solver = bool(
        solver_status_needs_rerun
        and case_status
        not in (
            POSTPROCESSED_BASIC,
            POSTPROCESSED_UNCONVERGED,
            NEEDS_SHEAR_POSTPROCESSING,
        )
    )
    failure_evidence_short = shorten_evidence(combined_failure_evidence(record))

    record.update(
        {
            "case_status": case_status,
            "likely_complete": likely_complete,
            "needs_solver_rerun": needs_solver,
            "needs_report_extraction": needs_reports,
            "needs_basic_contours": needs_basic_contours,
            "needs_shear_contour": needs_shear_contour,
            "needs_shear_postprocessing": needs_shear_postprocessing,
            "needs_manual_review": needs_manual_review,
            "ready_for_batch_contours": ready_for_batch_contours,
            "failure_evidence_short": failure_evidence_short,
        }
    )
    record["inventory_confidence"] = derive_inventory_confidence(record, case_status)
    record["suggested_next_action"] = suggested_action(record)


def strip_internal_paths(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if not k.startswith("_")}


CASE_INVENTORY_FIELDNAMES = [
    "geo_name",
    "case_name",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "u_target_ms",
    "p_gauge_pa",
    "case_dir",
    "post_dir",
    "reports_dir",
    "contours_dir",
    "cas_files",
    "dat_files",
    "final_cas_file",
    "final_dat_file",
    "has_final_cas",
    "has_final_dat",
    "has_case_data_pair",
    "cas_file_count",
    "dat_file_count",
    "log_files",
    "transcript_files",
    "latest_log_file",
    "log_file_count",
    "transcript_file_count",
    "log_files_by_role",
    "log_role_by_file",
    "solver_log_files",
    "postprocessing_log_files",
    "report_log_files",
    "meshing_log_files",
    "udf_compile_log_files",
    "unknown_log_files",
    "convergence_status",
    "stop_reason",
    "max_iteration_detected",
    "max_iter_target",
    "hit_max_iter_target",
    "likely_complete_from_logs",
    "likely_complete",
    "convergence_evidence",
    "failure_evidence",
    "warning_evidence",
    "completion_evidence",
    "max_iter_evidence",
    "iteration_notes",
    "log_parse_errors",
    "report_expression_warning_count",
    "report_expression_warning_files",
    "report_expression_warning_evidence",
    "postprocessing_graphics_error_files",
    "postprocessing_graphics_error_evidence",
    "meshing_error_files",
    "meshing_error_evidence",
    "udf_compile_error_files",
    "udf_compile_error_evidence",
    "launch_error_files",
    "launch_error_evidence",
    "has_summary_metrics_wide",
    "summary_metrics_wide_file",
    "report_csv_count",
    "report_files",
    "summary_metrics_read_error",
    "c_bulk_center_area_avg",
    "lmh",
    "cp",
    "pressure_drop",
    "mass_balance",
    "wall_shear_avg",
    "wall_shear_rate_avg",
    "has_cp_contour",
    "has_water_flux_contour",
    "has_lmh_contour",
    "has_salt_flux_contour",
    "has_all_pyensight_contours",
    "has_shear_contour",
    "has_all_basic_contours",
    "contour_status_file",
    "shear_status_file",
    "colorbar_metadata_files",
    "contour_export_overall_status",
    "contour_success_count",
    "contour_failed_count",
    "shear_export_status",
    "shear_native_status",
    "shear_fallback_status",
    "shear_derived_variable_mode",
    "shear_legend_mode",
    "shear_colorbar_metadata_written",
    "postprocessing_status",
    "report_status",
    "case_status",
    "has_postprocessing_graphics_errors",
    "has_report_expression_warnings",
    "has_udf_compile_errors",
    "has_meshing_errors",
    "has_launch_errors",
    "hard_solver_failure_detected",
    "max_iter_only",
    "has_postprocessing_runtime_crash",
    "postprocessing_runtime_crash_evidence",
    "failed_postprocessing_stage",
    "inventory_confidence",
    "failure_evidence_short",
    "needs_solver_rerun",
    "needs_report_extraction",
    "needs_basic_contours",
    "needs_shear_contour",
    "needs_shear_postprocessing",
    "needs_manual_review",
    "ready_for_batch_contours",
    "suggested_next_action",
]

RERUN_FIELDNAMES = [
    "geo_name",
    "case_name",
    "max_iteration_detected",
    "convergence_status",
    "stop_reason",
    "hard_solver_failure_detected",
    "max_iter_only",
    "failure_evidence_short",
    "latest_log_file",
    "suggested_next_action",
]

COMPACT_FIELDNAMES = [
    "geo_name",
    "case_name",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "case_dir",
    "convergence_status",
    "stop_reason",
    "case_status",
    "max_iteration_detected",
    "has_final_cas",
    "has_final_dat",
    "has_summary_metrics_wide",
    "has_all_basic_contours",
    "has_shear_contour",
    "hard_solver_failure_detected",
    "max_iter_only",
    "has_postprocessing_runtime_crash",
    "failed_postprocessing_stage",
    "has_report_expression_warnings",
    "has_postprocessing_graphics_errors",
    "inventory_confidence",
    "suggested_next_action",
]

POSTPROCESS_FIELDNAMES = [
    "geo_name",
    "case_name",
    "has_summary_metrics_wide",
    "has_all_basic_contours",
    "has_shear_contour",
    "has_postprocessing_runtime_crash",
    "failed_postprocessing_stage",
    "convergence_status",
    "case_status",
    "ready_for_batch_contours",
    "suggested_next_action",
]


def write_csv_file(path: Path, records: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: flatten_csv_value(record.get(key, "")) for key in fieldnames})


def write_json_file(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, default=str)


def format_case_line(record: dict[str, Any]) -> str:
    max_iter = record.get("max_iteration_detected")
    latest_log = record.get("latest_log_file") or ""
    return f"{record.get('geo_name')}/{record.get('case_name')} | max_iter={max_iter} | {latest_log}"


def append_case_list(lines: list[str], title: str, records: list[dict[str, Any]]) -> None:
    lines.append("")
    lines.append(title)
    if not records:
        lines.append("  (none)")
        return
    for record in sorted(records, key=lambda r: (str(r.get("geo_name")), str(r.get("case_name")))):
        lines.append(f"  {format_case_line(record)}")


def build_summary_text(records: list[dict[str, Any]]) -> str:
    by_geo = Counter(str(r.get("geo_name")) for r in records)
    by_convergence = Counter(str(r.get("convergence_status")) for r in records)
    by_case_status = Counter(str(r.get("case_status")) for r in records)

    hard_rerun_records = [
        r for r in records
        if r.get("needs_solver_rerun") and r.get("hard_solver_failure_detected")
    ]
    max_iter_records = [r for r in records if r.get("max_iter_only")]
    ready_records = [r for r in records if r.get("case_status") == READY_FOR_POSTPROCESSING]
    postprocessed_records = [r for r in records if r.get("case_status") == POSTPROCESSED_BASIC]
    postprocessed_unconverged_records = [
        r for r in records if r.get("case_status") == POSTPROCESSED_UNCONVERGED
    ]
    needs_shear_records = [r for r in records if r.get("case_status") == NEEDS_SHEAR_POSTPROCESSING]
    missing_records = [r for r in records if r.get("case_status") == MISSING_CASE_OR_DATA]
    report_extraction_records = [r for r in records if r.get("case_status") == NEEDS_REPORT_EXTRACTION]

    report_warning_count = sum(1 for r in records if r.get("has_report_expression_warnings"))
    graphics_error_count = sum(1 for r in records if r.get("has_postprocessing_graphics_errors"))
    hard_failure_count = sum(1 for r in records if r.get("hard_solver_failure_detected"))
    max_iter_only_count = sum(1 for r in records if r.get("max_iter_only"))
    postprocessing_runtime_crash_count = sum(1 for r in records if r.get("has_postprocessing_runtime_crash"))

    lines: list[str] = []
    lines.append("RO CFD Case Inventory Summary")
    lines.append("=============================")
    lines.append(f"Total cases: {len(records)}")

    lines.append("")
    lines.append("Count by geometry:")
    if by_geo:
        for name, count in sorted(by_geo.items()):
            lines.append(f"  {name}: {count}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Count by convergence_status:")
    for status in [
        CONVERGED,
        MAX_ITER_REACHED,
        FAILED_OR_DIVERGED,
        POSSIBLY_INCOMPLETE,
        UNKNOWN_NO_LOG,
        UNKNOWN_UNPARSED,
    ]:
        lines.append(f"  {status}: {by_convergence.get(status, 0)}")

    lines.append("")
    lines.append("Count by case_status:")
    for status in [
        READY_FOR_POSTPROCESSING,
        POSTPROCESSED_BASIC,
        POSTPROCESSED_UNCONVERGED,
        NEEDS_SHEAR_POSTPROCESSING,
        NEEDS_SOLVER_RERUN,
        NEEDS_REPORT_EXTRACTION,
        MISSING_CASE_OR_DATA,
        UNKNOWN_REVIEW_REQUIRED,
    ]:
        lines.append(f"  {status}: {by_case_status.get(status, 0)}")

    lines.append("")
    lines.append("Warning / failure flags:")
    lines.append(f"  report_expression_warnings: {report_warning_count}")
    lines.append(f"  postprocessing_graphics_errors: {graphics_error_count}")
    lines.append(f"  hard_solver_failure_detected: {hard_failure_count}")
    lines.append(f"  max_iter_only: {max_iter_only_count}")
    lines.append(f"  postprocessing_runtime_crash (e.g. fluent-<node>-error.log): {postprocessing_runtime_crash_count}")

    append_case_list(lines, "True hard solver rerun candidates:", hard_rerun_records)
    append_case_list(lines, "Max-iter-only candidates:", max_iter_records)
    append_case_list(lines, "READY_FOR_POSTPROCESSING cases:", ready_records)
    append_case_list(lines, "Already postprocessed cases:", postprocessed_records)
    append_case_list(
        lines,
        "POSTPROCESSED_UNCONVERGED cases (posted, max-iter):",
        postprocessed_unconverged_records,
    )
    append_case_list(lines, "NEEDS_SHEAR_POSTPROCESSING cases:", needs_shear_records)
    append_case_list(lines, "Report extraction candidates:", report_extraction_records)
    append_case_list(lines, "MISSING_CASE_OR_DATA cases:", missing_records)

    return "\n".join(lines) + "\n"


def write_outputs(
    output_dir: Path,
    records: list[dict[str, Any]],
    rerun_candidates: list[dict[str, Any]],
    postprocess_candidates: list[dict[str, Any]],
) -> None:
    ensure_safe_output_dir(output_dir)
    write_csv_file(output_dir / "case_inventory.csv", records, CASE_INVENTORY_FIELDNAMES)
    write_csv_file(output_dir / "case_inventory_compact.csv", records, COMPACT_FIELDNAMES)
    write_json_file(output_dir / "case_inventory.json", records)
    (output_dir / "case_inventory_summary.txt").write_text(
        build_summary_text(records),
        encoding="utf-8",
    )
    write_csv_file(output_dir / "rerun_candidates.csv", rerun_candidates, RERUN_FIELDNAMES)
    write_csv_file(output_dir / "postprocess_candidates.csv", postprocess_candidates, POSTPROCESS_FIELDNAMES)


def print_console_summary(
    records: list[dict[str, Any]],
    rerun_candidates: list[dict[str, Any]],
    output_dir: Path,
    dry_run: bool,
) -> None:
    by_convergence = Counter(str(r.get("convergence_status")) for r in records)
    by_case_status = Counter(str(r.get("case_status")) for r in records)
    needs_rerun = sum(1 for r in records if r.get("needs_solver_rerun"))

    label = "Case inventory dry run complete." if dry_run else "Case inventory complete."
    print(label)
    print(f"Total cases: {len(records)}")
    print(f"Converged: {by_convergence.get(CONVERGED, 0)}")
    print(f"Max-iter reached: {by_convergence.get(MAX_ITER_REACHED, 0)}")
    print(f"Failed/diverged: {by_convergence.get(FAILED_OR_DIVERGED, 0)}")
    print(f"Postprocessed basic: {by_case_status.get(POSTPROCESSED_BASIC, 0)}")
    print(f"Postprocessed unconverged: {by_case_status.get(POSTPROCESSED_UNCONVERGED, 0)}")
    print(f"Needs shear postprocessing: {by_case_status.get(NEEDS_SHEAR_POSTPROCESSING, 0)}")
    print(f"Needs solver rerun: {needs_rerun}")
    print(f"Ready for postprocessing: {by_case_status.get(READY_FOR_POSTPROCESSING, 0)}")
    if dry_run:
        print(f"Dry run: inventory would be written to: {output_dir}")
    else:
        print(f"Inventory written to: {output_dir}")

    if rerun_candidates:
        print("")
        print("First rerun candidates:")
        for record in rerun_candidates[:10]:
            print(
                f"{record.get('geo_name')}/{record.get('case_name')} | "
                f"{record.get('max_iteration_detected')} | "
                f"{record.get('latest_log_file') or ''}"
            )


def run_inventory(args: argparse.Namespace) -> int:
    if args.max_iter <= 0:
        raise ValueError("--max-iter must be positive")

    results_root = args.results_root
    output_dir = args.output_dir

    if args.verbose:
        print(f"Results root : {results_root}")
        print(f"Output dir   : {output_dir}")
        print(f"Geo filter   : {args.geo_name or '(none)'}")
        print(f"Case filter  : {args.case_name or '(none)'}")
        print(f"Max iter     : {args.max_iter}")
        print(f"Dry run      : {args.dry_run}")

    discovered = discover_cases(
        results_root=results_root,
        geo_name_filter=args.geo_name,
        case_name_filter=args.case_name,
        include_hidden=args.include_hidden,
        verbose=args.verbose,
    )

    records: list[dict[str, Any]] = []
    for idx, record in enumerate(discovered, start=1):
        if args.verbose:
            print(f"[{idx}/{len(discovered)}] {record['geo_name']}/{record['case_name']}")
        detect_case_data_files(record)
        detect_report_status(record)
        detect_contour_status(record)
        detect_logs_and_convergence(record, args.max_iter)
        classify_case(record)
        records.append(strip_internal_paths(record))

    rerun_candidates = [
        r for r in records
        if r.get("needs_solver_rerun")
    ]
    postprocess_candidates = [
        r for r in records
        if r.get("has_case_data_pair")
        and r.get("case_status")
        not in (POSTPROCESSED_BASIC, POSTPROCESSED_UNCONVERGED)
        and not r.get("needs_solver_rerun")
    ]

    if args.dry_run:
        print(f"Dry run: scanned {len(records)} case(s).")
        print(f"Dry run: would write case_inventory.csv to {output_dir / 'case_inventory.csv'}")
        print(f"Dry run: would write case_inventory_compact.csv to {output_dir / 'case_inventory_compact.csv'}")
        print(f"Dry run: would write case_inventory.json to {output_dir / 'case_inventory.json'}")
        print(f"Dry run: would write case_inventory_summary.txt to {output_dir / 'case_inventory_summary.txt'}")
        print(f"Dry run: would write rerun_candidates.csv to {output_dir / 'rerun_candidates.csv'}")
        print(f"Dry run: would write postprocess_candidates.csv to {output_dir / 'postprocess_candidates.csv'}")
    else:
        write_outputs(output_dir, records, rerun_candidates, postprocess_candidates)

    print_console_summary(records, rerun_candidates, output_dir, args.dry_run)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        resolve_path_defaults(args)
        return run_inventory(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
