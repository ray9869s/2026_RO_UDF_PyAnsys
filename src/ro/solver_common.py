"""Pure shared primitives for solver batch drivers and workers.

Phase 1 (07 consolidation): path helpers.
Phase 2: case naming helpers (batch wired; 07 selection unchanged).
Phase 3: input mode + solver worker artifact exit helpers.
Phase 4: pure convergence math extracted from 07_batch_solver_rerun.
No PyFluent imports.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from ro.residual_transcript import looks_like_residual_table_row

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


def _refuse_mesh_qualified_artifact_name(name):
    if isinstance(name, str) and "__" in name:
        raise ValueError(
            "mesh-qualified names are not used for artifacts: "
            f"{name!r}. Use run_id (u0p2_p6M); mesh_id lives in the path."
        )
    return name


def resolve_case_names(case_dict) -> tuple[Any, str]:
    """Return (base_case_name, case_name) for a solver sweep entry.

    case_name is the run_id used in artifact names ({geo_id}_{run_id}).
    Mesh identity is not encoded in the filename.

    Priority:
      1. Explicit "run_id".
      2. Explicit "case_name" (must not be mesh-qualified).
      3. inlet_velocity_value + outlet_gauge_pressure → make_base_case_name.
      4. Explicit "base_case_name".
      5. Otherwise "case_name" is required, as before.
    """
    run_id = _refuse_mesh_qualified_artifact_name(case_dict.get("run_id"))
    explicit_case_name = _refuse_mesh_qualified_artifact_name(
        case_dict.get("case_name")
    )
    base_case_name = case_dict.get("base_case_name")

    if run_id:
        return base_case_name or run_id, run_id

    if explicit_case_name:
        return base_case_name, explicit_case_name

    u = case_dict.get("inlet_velocity_value")
    p = case_dict.get("outlet_gauge_pressure")
    if u is not None and p is not None:
        derived = make_base_case_name(u, p)
        return derived, derived

    if base_case_name:
        return base_case_name, _refuse_mesh_qualified_artifact_name(base_case_name)

    return base_case_name, case_dict["case_name"]


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
U_MEAN_PROFILE_IDENTITY_REL_TOL = 1e-6


def max_iterations_from_common_solver_settings(settings: Any) -> int:
    """Return max_iterations from a common_solver_settings mapping.

    Missing or malformed values raise. Does not load batch_config.py.
    """
    if not isinstance(settings, dict):
        raise ValueError(
            "common_solver_settings must be a dict, "
            f"got {type(settings).__name__}."
        )
    if "max_iterations" not in settings:
        raise ValueError("common_solver_settings missing max_iterations.")
    raw = settings["max_iterations"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            "common_solver_settings.max_iterations must be an int, "
            f"got {raw!r}."
        )
    if raw <= 0:
        raise ValueError(
            "common_solver_settings.max_iterations must be positive, "
            f"got {raw!r}."
        )
    return raw


def residual_target_from_common_solver_settings(settings: Any) -> float:
    """Return residual_target from a common_solver_settings mapping.

    Missing or malformed values raise. Does not load batch_config.py.
    """
    if not isinstance(settings, dict):
        raise ValueError(
            "common_solver_settings must be a dict, "
            f"got {type(settings).__name__}."
        )
    if "residual_target" not in settings:
        raise ValueError("common_solver_settings missing residual_target.")
    raw = settings["residual_target"]
    if isinstance(raw, bool):
        raise ValueError(
            "common_solver_settings.residual_target must be numeric, "
            f"got {raw!r}."
        )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "common_solver_settings.residual_target must be numeric, "
            f"got {raw!r}."
        ) from exc
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(
            "common_solver_settings.residual_target must be a positive "
            f"finite number, got {raw!r}."
        )
    return value


def operating_point_token_from_run_id(run_id) -> str:
    """Return the ``u0p2_p6M`` token, stripping an optional letter-led suffix."""
    parts = str(run_id).split("_")
    if len(parts) < 2:
        raise ValueError(f"run_id is not a campaign operating-point token: {run_id!r}")
    token = f"{parts[0]}_{parts[1]}"
    if MATRIX_BASE_CASE_RE.fullmatch(token) is None:
        raise ValueError(f"run_id is not a campaign operating-point token: {run_id!r}")
    return token


def run_id_operating_point_block_reason(run_id, u_target_ms, p_gauge_pa):
    """Return why run_id does not match ``make_base_case_name(u, p)``, or None."""
    expected = make_base_case_name(u_target_ms, p_gauge_pa)
    try:
        token = operating_point_token_from_run_id(run_id)
    except ValueError as exc:
        return str(exc)
    if token != expected:
        return (
            f"run_id {run_id!r} does not match make_base_case_name("
            f"{u_target_ms!r}, {p_gauge_pa!r}) = {expected!r}"
        )
    return None


def require_run_id_matches_operating_point(run_id, u_target_ms, p_gauge_pa) -> None:
    reason = run_id_operating_point_block_reason(run_id, u_target_ms, p_gauge_pa)
    if reason is not None:
        raise ValueError(reason)


def u_mean_profile_identity_block_reason(
    u_mean_ms,
    inlet_profile_g,
    u_target_ms,
    *,
    inlet_bc_type="parabolic",
    rel_tol: float = U_MEAN_PROFILE_IDENTITY_REL_TOL,
):
    """Return why ``u_mean_ms * G != u_target_ms``, or None.

    Plug runs and unfilled ``u_mean_ms`` / ``inlet_profile_G`` are N/A
    (not a reject). ``u_mean_ms`` stays the legacy profile coefficient.
    """
    if inlet_bc_type != "parabolic":
        return None
    if u_mean_ms is None or inlet_profile_g is None:
        return None
    try:
        product = float(u_mean_ms) * float(inlet_profile_g)
        target = float(u_target_ms)
    except (TypeError, ValueError):
        return (
            "u_mean_ms * inlet_profile_G identity is not numeric: "
            f"u_mean_ms={u_mean_ms!r} G={inlet_profile_g!r} "
            f"u_target_ms={u_target_ms!r}"
        )
    denom = abs(target)
    if denom == 0.0 or not math.isfinite(product) or not math.isfinite(target):
        return (
            "u_mean_ms * inlet_profile_G identity is not finite: "
            f"{u_mean_ms!r} * {inlet_profile_g!r} vs {u_target_ms!r}"
        )
    relative = abs(product - target) / denom
    if relative > rel_tol:
        return (
            "u_mean_ms * inlet_profile_G does not equal u_target_ms: "
            f"{u_mean_ms!r} * {inlet_profile_g!r} = {product!r} vs "
            f"{u_target_ms!r} (relative {relative:.3g} > {rel_tol})"
        )
    return None


def require_u_mean_profile_identity(
    u_mean_ms,
    inlet_profile_g,
    u_target_ms,
    *,
    inlet_bc_type="parabolic",
    rel_tol: float = U_MEAN_PROFILE_IDENTITY_REL_TOL,
) -> None:
    reason = u_mean_profile_identity_block_reason(
        u_mean_ms,
        inlet_profile_g,
        u_target_ms,
        inlet_bc_type=inlet_bc_type,
        rel_tol=rel_tol,
    )
    if reason is not None:
        raise ValueError(reason)


def mesh_sha256_file_block_reason(recorded_sha256, mesh_file):
    """Return why recorded SHA does not match ``.msh.h5`` bytes, or None."""
    path = Path(mesh_file)
    if not path.is_file():
        return f"mesh file was not found: {path}"
    if not recorded_sha256:
        return "missing mesh_sha256"
    try:
        actual = sha256_file(path)
    except OSError as exc:
        return f"mesh file not readable for sha256 ({path}): {exc}"
    if actual != recorded_sha256:
        return (
            f"mesh_sha256 does not match {path}: recorded "
            f"{recorded_sha256} vs file {actual}"
        )
    return None


def require_mesh_sha256_matches_file(recorded_sha256, mesh_file) -> None:
    reason = mesh_sha256_file_block_reason(recorded_sha256, mesh_file)
    if reason is not None:
        raise ValueError(reason)


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


def sha256_file(path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    """Return residual-table header tokens, or None if this is not a table header.

    Requires the Fluent column header that starts with 'iter' and includes
    'continuity'. 'iteration N: continuity ...' progress lines are not headers.
    """
    tokens = header_line.split()
    if not tokens:
        return None
    lowered = [token.lower() for token in tokens]
    if lowered[0] != "iter":
        return None
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


# ---------------------------------------------------------------------------
# Solver stop reason (residual vs QoI vs cap vs diverge)
# ---------------------------------------------------------------------------

STOP_REASON_RESIDUAL_CONVERGED = "residual_converged"
STOP_REASON_QOI_CONVERGED = "qoi_converged"
STOP_REASON_MAX_ITER_REACHED = "max_iter_reached"
STOP_REASON_DIVERGED = "diverged"
STOP_REASON_UNKNOWN_EARLY_STOP = "unknown_early_stop"
STOP_REASON_QOI_REPORT_UNAVAILABLE = "qoi_report_unavailable"
STOP_REASON_ITERATION_UNKNOWN = "iteration_unknown"
STOP_REASON_NOT_RUN = "not_run"
STOP_REASON_DETERMINATION_FAILED = "stop_reason_determination_failed"
STOP_REASON_MARKER_PREFIX = "SOLVER_STOP_REASON="

# Skip may treat only these as complete. determination_failed is terminal
# FAILED and must not skip. Hashes below are written only after this attempt's
# canonical write_case_data succeeds; a previous pair cannot match them.
SOLVER_SKIP_ALLOWED_STOP_REASONS = frozenset(
    {
        STOP_REASON_RESIDUAL_CONVERGED,
        STOP_REASON_QOI_CONVERGED,
        STOP_REASON_MAX_ITER_REACHED,
    }
)
SOLVER_ATTEMPT_ID_FIELD = "solver_attempt_id"
FINAL_CASE_SHA256_FIELD = "final_case_sha256"
FINAL_DATA_SHA256_FIELD = "final_data_sha256"

# Fluent console phrases. The QoI phrase contains the residual phrase as a
# substring, so match QoI first.
FLUENT_QOI_CONVERGED_PHRASE = "report definition solution is converged"
FLUENT_RESIDUAL_CONVERGED_PHRASE = "solution is converged"

STOP_REASON_VALUES = (
    STOP_REASON_RESIDUAL_CONVERGED,
    STOP_REASON_QOI_CONVERGED,
    STOP_REASON_MAX_ITER_REACHED,
    STOP_REASON_DIVERGED,
    STOP_REASON_UNKNOWN_EARLY_STOP,
    STOP_REASON_QOI_REPORT_UNAVAILABLE,
    STOP_REASON_ITERATION_UNKNOWN,
    STOP_REASON_NOT_RUN,
    STOP_REASON_DETERMINATION_FAILED,
)

_ITERATION_PREFIX_RE = re.compile(r"^iteration\s+(\d+)\s*:", re.IGNORECASE)
_BANG_ITERATION_RE = re.compile(r"!\s*(\d+)\s+")


def fluent_report_relative_window_met(
    values: list[float],
    stop_criterion: float,
) -> bool:
    """Return True when Fluent UG 37.18 report stop criterion is met.

    For history values ordered oldest→newest with length Np+1 (current plus
    previous_values_to_consider samples), Fluent stops when::

        max_k |m(n) - m(n-k)| / |m(n)|  <  stop_criterion

    for k = 1 .. Np. Absolute value protects zero/sign flips.
    """
    if stop_criterion <= 0.0:
        raise ValueError(f"stop_criterion must be positive, got {stop_criterion!r}")
    if len(values) < 2:
        return False
    current = float(values[-1])
    denom = abs(current)
    if denom == 0.0:
        return False
    previous = values[:-1]
    return max(abs(current - float(v)) / denom for v in previous) < float(stop_criterion)


def parse_fluent_report_file_series(text: str) -> list[tuple[int, float]]:
    """Parse a Fluent report-file body into (iteration, value) pairs.

    Accepts the common quoted-header + whitespace numeric rows layout.
    Last value column is used when multiple report defs share one file.
    """
    rows: list[tuple[int, float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('"') or line.startswith("("):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        try:
            iteration = int(float(tokens[0]))
            value = float(tokens[-1])
        except ValueError:
            continue
        rows.append((iteration, value))
    return rows


def _iteration_from_convergence_line(line: str) -> int | None:
    """Extract the iteration printed on a Fluent convergence marker line."""
    bang = _BANG_ITERATION_RE.search(line)
    if bang:
        return int(bang.group(1))
    lower = line.lower()
    for phrase in (FLUENT_QOI_CONVERGED_PHRASE, FLUENT_RESIDUAL_CONVERGED_PHRASE):
        idx = lower.find(phrase)
        if idx < 0:
            continue
        prefix = line[:idx].strip()
        tokens = prefix.replace("!", " ").split()
        if tokens:
            try:
                return int(tokens[-1])
            except ValueError:
                return None
        return None
    return None


def first_fluent_report_file_iteration(report_file_paths) -> int | None:
    """Return the earliest data-line iteration across Fluent report files.

    That first row is the iteration Fluent already holds when this session
    starts writing. On mesh_initialization it is 1. On restart it is the
    source run's last iterate. Missing or unreadable files are skipped.
    """
    firsts: list[int] = []
    for raw_path in report_file_paths or ():
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        series = parse_fluent_report_file_series(text)
        if series:
            firsts.append(int(series[0][0]))
    if not firsts:
        return None
    return min(firsts)


def last_fluent_report_file_iteration(report_file_paths) -> int | None:
    """Return the latest data-line iteration across Fluent report files."""
    lasts: list[int] = []
    for raw_path in report_file_paths or ():
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        series = parse_fluent_report_file_series(text)
        if series:
            lasts.append(int(series[-1][0]))
    if not lasts:
        return None
    return max(lasts)


def parse_fluent_convergence_marker(
    text: str,
    *,
    after_iteration: int | None = None,
) -> tuple[str | None, int | None]:
    """Return (stop_reason, iteration) from Fluent console convergence lines.

    Matches 'report definition solution is converged' before the residual
    phrase 'solution is converged' so a QoI stop is not misclassified.
    Last matching line in *text* wins.

    If *after_iteration* is set, ignore a marker whose iteration is missing
    or not strictly greater. That bound is this session's inherited
    iteration (first .out data line). A declaration at that same number
    belongs to the source run.
    """
    reason: str | None = None
    iteration: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if FLUENT_QOI_CONVERGED_PHRASE in lower:
            candidate_reason = STOP_REASON_QOI_CONVERGED
            candidate_iteration = _iteration_from_convergence_line(line)
        elif FLUENT_RESIDUAL_CONVERGED_PHRASE in lower:
            candidate_reason = STOP_REASON_RESIDUAL_CONVERGED
            candidate_iteration = _iteration_from_convergence_line(line)
        else:
            continue
        if after_iteration is not None:
            if candidate_iteration is None:
                continue
            if int(candidate_iteration) <= int(after_iteration):
                continue
        reason = candidate_reason
        iteration = candidate_iteration
    return reason, iteration


def parse_first_residual_iteration_from_transcript_text(text: str) -> int | None:
    """Return the first residual-table (or 'iteration N:') iteration in *text*.

    Digit-leading mesh inventory lines (cells/faces/nodes) are not iterations.
    """
    first: int | None = None
    saw_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        prefix = _ITERATION_PREFIX_RE.match(line)
        if prefix:
            value = int(prefix.group(1))
            if first is None:
                first = value
            continue
        if parse_transcript_residual_columns(line):
            saw_header = True
            continue
        if saw_header and looks_like_residual_table_row(line):
            value = int(line.split()[0])
            if first is None:
                first = value
    return first


def parse_last_residual_iteration_from_transcript_text(text: str) -> int | None:
    """Return the last residual-table (or 'iteration N:') iteration in *text*.

    Digit-leading mesh inventory lines (cells/faces/nodes) are not iterations.
    """
    last: int | None = None
    saw_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        prefix = _ITERATION_PREFIX_RE.match(line)
        if prefix:
            last = int(prefix.group(1))
            continue
        if parse_transcript_residual_columns(line):
            saw_header = True
            continue
        if saw_header and looks_like_residual_table_row(line):
            last = int(line.split()[0])
    return last


def classify_solver_stop_reason(
    *,
    diverged: bool,
    residuals_met: bool,
    qoi_met: bool,
    final_iteration: int | None,
    max_iterations: int,
    calculation_ran: bool = True,
) -> str:
    """Classify why a solver run stopped.

    Priority:
      not_run → diverged → residual_converged → qoi_converged →
      unknown_early_stop → max_iter_reached → iteration_unknown

    residuals_met / qoi_met come from Fluent transcript markers, not from
    settings-state residual 'current' fields. Report-file window checks are
    a cross-check only and must not be passed as qoi_met.

    No marker plus a known count below the cap is unknown_early_stop.
    No marker plus count equal to the cap is max_iter_reached.
    No marker and no countable iteration is iteration_unknown, never
    max_iter_reached. qoi_report_unavailable remains in the enum for
    historical logs; new classification does not emit it.
    """
    if not calculation_ran:
        return STOP_REASON_NOT_RUN
    if diverged:
        return STOP_REASON_DIVERGED
    if residuals_met:
        return STOP_REASON_RESIDUAL_CONVERGED
    if qoi_met:
        return STOP_REASON_QOI_CONVERGED
    if final_iteration is None:
        return STOP_REASON_ITERATION_UNKNOWN
    if int(final_iteration) < int(max_iterations):
        return STOP_REASON_UNKNOWN_EARLY_STOP
    return STOP_REASON_MAX_ITER_REACHED


def format_stop_reason_marker(reason: str) -> str:
    """Return the stable solver-log marker line for inventory parsing."""
    if reason not in STOP_REASON_VALUES:
        raise ValueError(f"Unknown stop reason: {reason!r}")
    return f"{STOP_REASON_MARKER_PREFIX}{reason}"


def parse_stop_reason_from_text(text: str) -> str | None:
    """Return the last SOLVER_STOP_REASON= value found in log/transcript text."""
    found: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(STOP_REASON_MARKER_PREFIX):
            continue
        value = line[len(STOP_REASON_MARKER_PREFIX) :].strip()
        if value in STOP_REASON_VALUES:
            found = value
    return found
