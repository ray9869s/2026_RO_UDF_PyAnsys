"""Pure shared primitives for solver batch drivers and workers.

Phase 1 (07 consolidation): path helpers.
Phase 2: case naming helpers (batch wired; 07 selection unchanged).
Phase 3: input mode + solver worker artifact exit helpers.
Phase 4: pure convergence math extracted from 07_batch_solver_rerun.
No PyFluent imports.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def path_to_fluent_str(path: Any) -> str:
    """Convert a path to a Fluent-friendly absolute path.

    Semantics match solver_code_260616.as_fluent_path: os.path.abspath plus
    forward slashes. Accepts str or pathlib.Path.
    """
    return os.path.abspath(path).replace("\\", "/")


def path_to_fluent_str_resolved(path: Any) -> str:
    """Convert a path to a Fluent-friendly absolute path using Path.resolve().

    Semantics match 07_batch_solver_rerun.fluent_path: resolve symlinks and
    normalize, then forward slashes. Accepts str or pathlib.Path.
    """
    return str(Path(path).resolve()).replace("\\", "/")


def normalize_path(path: Any) -> str:
    """Normalize a path for comparison (solver semantics)."""
    return os.path.normcase(os.path.abspath(path))


def final_case_data_paths_under_root(
    results_root: Any,
    geo_name: str,
    case_name: str,
) -> tuple[str, str, str]:
    """Return (case_dir, final_case_path, final_data_path) under results_root."""
    case_dir = os.path.join(results_root, geo_name, case_name)
    final_case = os.path.join(case_dir, f"{geo_name}_{case_name}_final.cas.h5")
    final_data = final_case.replace(".cas.h5", ".dat.h5")
    return case_dir, final_case, final_data


def final_case_data_paths(
    project_root: Any,
    geo_name: str,
    case_name: str,
) -> tuple[str, str, str]:
    """Return (case_dir, final_case_path, final_data_path) as strings.

    Layout matches batch_solver_sweep.py expected_final_case/expected_final_data
    construction (project_root/03_Results/<geo>/<case>/...).
    """
    return final_case_data_paths_under_root(
        os.path.join(project_root, "03_Results"),
        geo_name,
        case_name,
    )


def find_windows_drive_paths(paths: Iterable[Any]) -> list[str]:
    """Return inputs whose string form starts with a Windows drive letter."""
    unsafe: list[str] = []
    for path in paths:
        text = str(path)
        if WINDOWS_DRIVE_RE.match(text):
            unsafe.append(text)
    return unsafe


def reject_windows_drive_paths_on_non_windows(
    paths: Iterable[Any],
    *,
    host_os_name: str | None = None,
) -> None:
    """Refuse Windows drive paths on non-Windows hosts (WSL/repo safety).

    Not wired into solver/batch in phase 1; provided for future shared use.
    """
    if (host_os_name if host_os_name is not None else os.name) == "nt":
        return

    unsafe = find_windows_drive_paths(paths)
    if not unsafe:
        return

    raise ValueError(
        "Windows drive paths are not accepted on this non-Windows host because "
        "they can create C: folders inside the WSL repo. Run live/server paths "
        f"from Windows Python instead. Unsafe path(s): {unsafe}"
    )


# ---------------------------------------------------------------------------
# naming — case token build/resolve (batch_solver_sweep semantics)
# ---------------------------------------------------------------------------

MATRIX_BASE_CASE_RE = re.compile(r"^u\d+p\d+_p\d+M$")


def velocity_to_case_token(u) -> str:
    # 0.1 -> "u0p1"
    return f"u0p{int(round(float(u) * 10))}"


def pressure_to_case_token(p) -> str:
    # 4.0e6 -> "p4M"
    return f"p{int(round(float(p) / 1.0e6))}M"


def make_base_case_name(u, p) -> str:
    # (0.1, 4.0e6) -> "u0p1_p4M"
    return f"{velocity_to_case_token(u)}_{pressure_to_case_token(p)}"


def make_mesh_qualified_case_name(base_case_name, mesh_case_name) -> str:
    if mesh_case_name:
        return f"{base_case_name}__{mesh_case_name}"
    return base_case_name


def resolve_case_names(case_dict) -> tuple[Any, str]:
    """Return (base_case_name, case_name) for a solver sweep entry.

    Priority:
      1. Explicit "case_name" is used as-is (legacy behavior).
      2. Explicit "base_case_name" is mesh-qualified with mesh_case_name.
      3. inlet_velocity_value + outlet_gauge_pressure + mesh_case_name derive both.
      4. Otherwise "case_name" is required, as before.
    """
    base_case_name = case_dict.get("base_case_name")
    mesh_case_name = case_dict.get("mesh_case_name")
    explicit_case_name = case_dict.get("case_name")

    if explicit_case_name:
        return base_case_name, explicit_case_name

    if base_case_name and mesh_case_name:
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    u = case_dict.get("inlet_velocity_value")
    p = case_dict.get("outlet_gauge_pressure")
    if u is not None and p is not None and mesh_case_name:
        base_case_name = make_base_case_name(u, p)
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    # Legacy behavior: an explicit case_name is required when it cannot be derived.
    return base_case_name, case_dict["case_name"]


def strip_mesh_suffix(case_name: str) -> tuple[str, str | None]:
    """Split a mesh-qualified case name into (base_case_name, mesh_suffix)."""
    if "__" not in case_name:
        return case_name, None
    base, mesh = case_name.split("__", 1)
    return base, mesh


def is_matrix_base_case_name(case_name: str) -> bool:
    """True when case_name matches the plain matrix token (no mesh suffix)."""
    return bool(MATRIX_BASE_CASE_RE.match(case_name))


def merge_batch_case_overrides(
    common_settings: Any,
    case_dict: Any,
) -> dict[str, Any]:
    """Merge common batch settings with one case entry (case wins).

    Production rule from batch_solver_sweep.py::

        overrides = {**common_solver_settings, **case_dict}

    Callers that need resolved worker keys (e.g. operating_pressure living only
    in common_solver_settings) must use this helper rather than reading the
    raw case dict alone.
    """
    if case_dict is None:
        raise TypeError("case_dict must be a mapping, got None.")
    if not hasattr(case_dict, "keys"):
        raise TypeError(
            f"case_dict must be a mapping, got {type(case_dict).__name__}."
        )
    common: dict[str, Any]
    if common_settings is None:
        common = {}
    elif hasattr(common_settings, "keys"):
        common = dict(common_settings)
    else:
        raise TypeError(
            "common_settings must be a mapping or None, "
            f"got {type(common_settings).__name__}."
        )
    return {**common, **dict(case_dict)}


DEFAULT_MAX_ITERATIONS_FALLBACK = 2000
DEFAULT_RESIDUAL_TARGET_FALLBACK = 1.0e-7


def max_iterations_from_common_solver_settings(
    settings: Any,
    *,
    fallback: int = DEFAULT_MAX_ITERATIONS_FALLBACK,
    warn: Any = None,
) -> int:
    """Resolve max_iterations from a common_solver_settings mapping.

    Missing or malformed settings warn (when ``warn`` is provided) and return
    ``fallback``. Does not load batch_config.py itself.
    """
    if warn is None:
        def warn(message: str) -> None:
            print(message, file=sys.stderr)

    if not isinstance(settings, dict):
        warn(
            "WARNING: common_solver_settings is not a usable dict "
            f"(got {type(settings).__name__}); falling back to {fallback}."
        )
        return int(fallback)
    raw = settings.get("max_iterations", fallback)
    try:
        return int(raw)
    except (TypeError, ValueError):
        warn(
            "WARNING: common_solver_settings.max_iterations="
            f"{raw!r} is not an int; falling back to {fallback}."
        )
        return int(fallback)


def residual_target_from_common_solver_settings(
    settings: Any,
    *,
    fallback: float = DEFAULT_RESIDUAL_TARGET_FALLBACK,
    warn: Any = None,
) -> float:
    """Resolve residual_target from a common_solver_settings mapping."""
    if warn is None:
        def warn(message: str) -> None:
            print(message, file=sys.stderr)

    if not isinstance(settings, dict):
        warn(
            "WARNING: common_solver_settings is not a usable dict "
            f"(got {type(settings).__name__}); falling back to {fallback}."
        )
        return float(fallback)
    raw = settings.get("residual_target", fallback)
    try:
        return float(raw)
    except (TypeError, ValueError):
        warn(
            "WARNING: common_solver_settings.residual_target="
            f"{raw!r} is not numeric; falling back to {fallback}."
        )
        return float(fallback)


# ---------------------------------------------------------------------------
# input_mode — restart vs mesh initialization (batch_solver_sweep semantics)
# ---------------------------------------------------------------------------


def resolve_input_mode(case_settings: dict[str, Any]) -> tuple[str, Any, Any]:
    """Return the solver input mode and optional restart source paths."""
    restart_case = case_settings.get("restart_from_case_file")
    restart_data = case_settings.get("restart_from_data_file")

    has_restart_case = restart_case is not None
    has_restart_data = restart_data is not None

    if has_restart_case != has_restart_data:
        raise ValueError(
            "restart_from_case_file and restart_from_data_file must be provided together."
        )

    if has_restart_case:
        if not str(restart_case).strip() or not str(restart_data).strip():
            raise ValueError(
                "restart_from_case_file and restart_from_data_file must be non-empty paths."
            )
        return "restart_continuation", restart_case, restart_data

    return "mesh_initialization", None, None


# ---------------------------------------------------------------------------
# artifacts — solver worker final case/data exit contract (F-03 #6)
# ---------------------------------------------------------------------------

# Solver worker exit codes (solver_code_260616.py / batch_solver_sweep.py):
#   0 = success (final .cas.h5/.dat.h5 exist and are non-empty)
#   1 = unhandled exception / preflight failure
#   2 = final artifact verification failure after write
SOLVER_EXIT_SUCCESS = 0
SOLVER_EXIT_ARTIFACT_FAILURE = 2


def collect_solver_final_artifact_failures(
    final_case_path,
    final_data_path,
    *,
    is_file=os.path.isfile,
    get_size=os.path.getsize,
):
    """Return human-readable failure messages for missing or empty final artifacts."""
    failures = []
    for path, description in (
        (final_case_path, "final case file"),
        (final_data_path, "final data file"),
    ):
        if not is_file(path):
            failures.append(f"{description} was not found: {path}")
            continue
        try:
            size = get_size(path)
        except OSError as exc:
            failures.append(f"{description} size could not be read ({path}): {exc}")
            continue
        if size <= 0:
            failures.append(f"{description} is empty (0 bytes): {path}")
    return failures


def resolve_solver_final_artifact_exit_code(
    final_case_path,
    final_data_path,
    *,
    is_file=os.path.isfile,
    get_size=os.path.getsize,
) -> int:
    """Return 0 when both final artifacts exist and are non-empty; else 2."""
    if collect_solver_final_artifact_failures(
        final_case_path,
        final_data_path,
        is_file=is_file,
        get_size=get_size,
    ):
        return SOLVER_EXIT_ARTIFACT_FAILURE
    return SOLVER_EXIT_SUCCESS


def solver_worker_succeeded(returncode: int) -> bool:
    """True only when the solver worker completed with verified final artifacts."""
    return returncode == SOLVER_EXIT_SUCCESS


def describe_solver_worker_failure(returncode: int) -> str:
    if returncode == SOLVER_EXIT_ARTIFACT_FAILURE:
        return "final case/data missing or empty"
    return "worker failed"


# ---------------------------------------------------------------------------
# convergence_pure — transcript/monitor assessment (07_batch_solver_rerun)
# ---------------------------------------------------------------------------


def blending_ramp_values(start: float, end: float, steps: int) -> list[float]:
    if steps <= 1:
        return [float(end)]
    delta = (float(end) - float(start)) / float(steps - 1)
    return [float(start) + delta * index for index in range(steps)]


def parse_transcript_residual_columns(header_line: str) -> list[str] | None:
    tokens = header_line.split()
    if not tokens:
        return None
    lowered = [token.lower() for token in tokens]
    if "continuity" not in lowered:
        return None
    return lowered


def parse_residuals_from_transcript_text(text: str, target_names: set[str]) -> dict[str, float]:
    """Best-effort extraction of the latest per-equation residual values.

    Fluent's console/transcript prints a residual table with a header row
    (containing 'continuity') followed by numeric iteration rows. This is a
    fallback for when the settings API residual-equation objects expose only
    convergence criteria, not the live current value (the reported cause of
    residual_keys=[] in monitor snapshots). Parsing is intentionally
    tolerant: unparsable lines are skipped rather than raising, and the
    caller must treat an empty result as "unknown", not "converged".
    """
    latest: dict[str, float] = {}
    columns: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "continuity" in line.lower() and not line[0].isdigit():
            candidate_columns = parse_transcript_residual_columns(line)
            if candidate_columns:
                columns = candidate_columns
            continue
        if columns is None:
            continue
        tokens = line.split()
        if not tokens:
            continue
        try:
            int(tokens[0])
        except ValueError:
            continue
        row_values: dict[str, float] = {}
        for name, token in zip(columns[1:], tokens[1:]):
            if name not in target_names:
                continue
            try:
                row_values[name] = float(token)
            except ValueError:
                row_values = {}
                break
        if row_values:
            latest.update(row_values)
    return latest


def detect_residual_plateau(
    history: list[dict[str, Any]],
    strict_targets: dict[str, float],
    window_chunks: int,
    rel_change_tol: float,
    min_above_target_factor: float,
) -> dict[str, Any]:
    """Detect a residual equation stuck above its target across recent chunks."""
    result: dict[str, Any] = {"detected": False, "reason": "", "per_equation": {}}
    if not strict_targets:
        result["reason"] = "No residual targets available for plateau assessment."
        return result
    if len(history) < window_chunks:
        result["reason"] = f"Fewer than {window_chunks} chunks completed; plateau assessment deferred."
        return result

    recent = history[-window_chunks:]
    stuck_equations: list[str] = []
    per_equation: dict[str, Any] = {}
    for name, target in strict_targets.items():
        series = [snap.get("residual_numeric", {}).get(name) for snap in recent]
        series = [float(v) for v in series if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(series) < window_chunks:
            per_equation[name] = {"status": "insufficient_data", "series": series}
            continue

        above_target = series[-1] > target * min_above_target_factor
        mean_abs = sum(abs(v) for v in series) / len(series)
        rel_change = (max(series) - min(series)) / mean_abs if mean_abs > 0.0 else 0.0
        flat = rel_change <= rel_change_tol
        per_equation[name] = {
            "series": series,
            "latest": series[-1],
            "target": target,
            "above_target": above_target,
            "rel_change": rel_change,
            "flat": flat,
        }
        if above_target and flat:
            stuck_equations.append(name)

    result["per_equation"] = per_equation
    if stuck_equations:
        result["detected"] = True
        result["reason"] = (
            f"{stuck_equations} remained above {min_above_target_factor}x target with "
            f"<= {rel_change_tol} relative change over the last {window_chunks} chunks."
        )
    else:
        result["reason"] = "No plateau detected."
    return result


def assess_history(
    history: list[dict[str, Any]],
    args: argparse.Namespace,
    value_groups: tuple[str, ...] = ("residual_numeric", "report_values"),
    require_two_samples: bool = False,
) -> dict[str, Any]:
    assessment: dict[str, Any] = {
        "status": "MONITORS_UNAVAILABLE",
        "diverged": False,
        "stable": False,
        "bounded_not_converged": False,
        "details": "",
        "value_groups": list(value_groups),
    }
    series: dict[str, list[float]] = {}
    for snapshot in history:
        for group_name in value_groups:
            values = snapshot.get(group_name, {})
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    series.setdefault(f"{group_name}:{name}", []).append(float(value))

    if not series:
        assessment["details"] = (
            f"No numeric values were available for monitor groups {list(value_groups)}."
        )
        return assessment

    worst_growth = 0.0
    max_rel_variation = 0.0
    comparable_series_count = 0
    for name, values in series.items():
        if len(values) < 2:
            continue
        comparable_series_count += 1
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

    if require_two_samples and comparable_series_count == 0:
        assessment["details"] = (
            f"At least two numeric samples are required for monitor groups "
            f"{list(value_groups)}."
        )
        return assessment

    assessment["worst_growth"] = worst_growth
    assessment["max_rel_variation"] = max_rel_variation
    assessment["comparable_series_count"] = comparable_series_count

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


def assess_residual_convergence(
    latest_residuals: dict[str, float],
    strict_targets: dict[str, float],
) -> dict[str, Any]:
    """Compare the latest known residual values against the strict target."""
    per_equation: dict[str, Any] = {}
    if not strict_targets:
        return {
            "strict_met": False,
            "data_available": False,
            "reason": "No residual convergence targets were available (criteria could not be read).",
            "per_equation": per_equation,
        }

    strict_met = True
    data_available = False
    missing: list[str] = []
    for name, target in strict_targets.items():
        value = latest_residuals.get(name)
        per_equation[name] = {"latest": value, "target": target}
        if value is None:
            missing.append(name)
            strict_met = False
            continue
        data_available = True
        if value > target:
            strict_met = False

    reason = f"Residual current value unavailable for: {missing}." if missing else ""
    return {
        "strict_met": strict_met,
        "data_available": data_available,
        "reason": reason,
        "per_equation": per_equation,
    }


def classify_convergence(
    monitor_assessment: dict[str, Any],
    residual_assessment: dict[str, Any],
    plateau_assessment: dict[str, Any],
) -> dict[str, Any]:
    """Combine monitor stability, strict residual match, and plateau detection."""
    if monitor_assessment.get("diverged"):
        return {"status": "DIVERGED", "details": monitor_assessment.get("details", "")}

    strict_met = bool(residual_assessment.get("strict_met"))

    if strict_met and monitor_assessment.get("stable"):
        return {
            "status": "STRICT_CONVERGED_ATTEMPT",
            "details": "Residual strict target met and report monitors are stable.",
        }

    if plateau_assessment.get("detected"):
        return {
            "status": "NOT_CONVERGED_RESIDUAL_PLATEAU",
            "details": plateau_assessment.get("reason", "Residual plateau detected above target."),
        }

    if monitor_assessment.get("stable"):
        return {
            "status": "NOT_CONVERGED_STABLE_MONITORS",
            "details": "Monitors stable but residuals did not meet the strict target.",
        }

    if monitor_assessment.get("bounded_not_converged"):
        return {
            "status": "NEEDS_TRANSIENT_REVIEW",
            "details": "Monitors bounded but oscillating, and residual target not met.",
        }

    return {
        "status": "COMPLETED_NEEDS_REVIEW",
        "details": "Monitor stability could not be determined from available data.",
    }
