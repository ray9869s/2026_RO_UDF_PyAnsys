"""Post-hoc convergence quality gate (independent of stop_reason).

The QoI / residual stop can declare convergence while two LMH paths still
disagree or continuity sits above 1e-4. Those checks caught under-converged
u0p3 at the 301-iteration QoI stop on D2450_a45 (p=6 MPa), where CP and
spacer dP were already within 0.1% of the longer residual-converged solution.

Unit-cell pressure-drop spread over the layout evaluation window is recorded
as a WARNING, never a failure. The window is
``evaluation_window.evaluation_cell_numbers(layout)`` (global 5–8 on 1+7+2,
5–22 on 1+21+2). Hardcoded cells 4–7 are kept as a continuity column; that
range includes excluded cell 4 and omits window cell 8 on a 1+7+2 layout.
No numeric threshold yet: the evaluation-window mean is only meaningful
when the spread is small.

Keep this separate from stop_reason: a max_iter_reached run (e.g. u0p1 with
continuity floored at ~4e-7) can still PASS.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

QUALITY_PASS = "PASS"
QUALITY_FAIL = "FAIL"
QUALITY_UNKNOWN = "UNKNOWN"

LMH_REL_ABS_MAX = 1e-3
MASS_BALANCE_REL_ABS_MAX = 1e-3
CONTINUITY_FINAL_MAX = 1e-4
PRESSURE_DROP_CELLS = (4, 5, 6, 7)


def pressure_drop_cells_in_layout(n_unit_cells: int) -> tuple[int, ...]:
    """Legacy cells 4–7 when every one of them is inside the layout.

    A shorter domain omits the whole set. A partial set is not a substitute.
    """
    if isinstance(n_unit_cells, bool) or not isinstance(n_unit_cells, int):
        raise TypeError(f"n_unit_cells must be an int, got {n_unit_cells!r}.")
    if n_unit_cells < 1:
        raise ValueError(f"n_unit_cells must be >= 1, got {n_unit_cells!r}.")
    if any(cell > n_unit_cells for cell in PRESSURE_DROP_CELLS):
        return ()
    return PRESSURE_DROP_CELLS
PRESSURE_DROP_REL_SPREAD_WARNING = "pp_pressure_drop_rel_spread_window"
PRESSURE_DROP_REL_SPREAD_NOTE = (
    "Evaluation-window mean is only meaningful when this spread is small."
)
_CELL_DP_KEY_RE = re.compile(r"^pp_pressure_drop_cell_(\d+)$")


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if result == result else None  # NaN → None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if result == result else None


def _is_non_finite(value: Any) -> bool:
    """True for NaN/inf given as a number or as text ("nan", "inf", "-inf")."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not math.isfinite(float(value))
    text = str(value).strip()
    if not text:
        return False
    try:
        return not math.isfinite(float(text))
    except ValueError:
        return False


def _finite_limit(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}.")
    return number


def relative_spread(values: list[float]) -> Optional[float]:
    """(max - min) / abs(mean). None if empty or mean is zero."""
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean == 0.0:
        return None
    return (max(values) - min(values)) / abs(mean)


def pressure_drop_rel_spread_for_cells(
    metrics: Mapping[str, Any],
    cells: Sequence[int],
) -> tuple[Optional[float], list[str]]:
    """Return (spread, missing_column_names) for the given global cell numbers."""
    values: list[float] = []
    missing: list[str] = []
    for cell in cells:
        key = f"pp_pressure_drop_cell_{cell}"
        parsed = _as_float(metrics.get(key))
        if parsed is None:
            missing.append(key)
        else:
            values.append(parsed)
    if missing:
        return None, missing
    return relative_spread(values), []


def pressure_drop_rel_spread_cells_4_7(
    metrics: Mapping[str, Any],
) -> tuple[Optional[float], list[str]]:
    """Continuity column: hardcoded global cells 4–7, not the evaluation window."""
    return pressure_drop_rel_spread_for_cells(metrics, PRESSURE_DROP_CELLS)


def evaluate_convergence_quality(
    metrics: Mapping[str, Any],
    *,
    continuity_final: Any = None,
    evaluation_cell_numbers: Optional[Sequence[int]] = None,
    lmh_rel_abs_max: Optional[float] = None,
    mass_balance_rel_abs_max: Optional[float] = None,
    continuity_final_max: Optional[float] = None,
) -> dict[str, Any]:
    """Classify post-hoc convergence quality from summary + residual metrics.

    Parameters
    ----------
    metrics:
        Mapping with at least the summary columns used by the gate (string or
        numeric values). Missing keys are treated as unavailable.
    continuity_final:
        Final continuity residual. If omitted, ``metrics["continuity_final"]``
        is used when present.
    evaluation_cell_numbers:
        Global unit-cell indices from
        ``evaluation_window.evaluation_cell_numbers(layout)``. Window spread
        is None when this is omitted or a window cell is missing.

    Returns
    -------
    dict
        ``convergence_quality`` in {PASS, FAIL, UNKNOWN},
        ``needs_longer_solve`` (True only on FAIL),
        per-check values / pass flags, ``failures``, ``warnings``
        (never fail the gate),
        ``pp_pressure_drop_rel_spread_window``, and the continuity column
        ``pp_pressure_drop_rel_spread_cells_4_7``.
    """
    lmh_limit = LMH_REL_ABS_MAX if lmh_rel_abs_max is None else float(lmh_rel_abs_max)
    mb_limit = (
        MASS_BALANCE_REL_ABS_MAX
        if mass_balance_rel_abs_max is None
        else float(mass_balance_rel_abs_max)
    )
    continuity_limit = (
        CONTINUITY_FINAL_MAX
        if continuity_final_max is None
        else float(continuity_final_max)
    )
    lmh_limit = _finite_limit("lmh_rel_abs_max", lmh_limit)
    mb_limit = _finite_limit("mass_balance_rel_abs_max", mb_limit)
    continuity_limit = _finite_limit("continuity_final_max", continuity_limit)
    raw_lmh = metrics.get("lmh_relative_difference")
    raw_mb = metrics.get("mass_balance_relative_error")
    raw_continuity = continuity_final
    if _as_float(raw_continuity) is None and not _is_non_finite(raw_continuity):
        raw_continuity = metrics.get("continuity_final")
    lmh_rel = _as_float(raw_lmh)
    mb_rel = _as_float(raw_mb)
    continuity = _as_float(raw_continuity)
    # NaN/inf is a failed solve or a broken report, never "unavailable" and
    # never a pass (C1-01).
    non_finite = {
        "lmh_relative_difference": _is_non_finite(raw_lmh),
        "mass_balance_relative_error": _is_non_finite(raw_mb),
        "continuity_final": _is_non_finite(raw_continuity),
    }
    dP_spread_4_7, dP_missing_4_7 = pressure_drop_rel_spread_cells_4_7(metrics)
    window_cells = list(evaluation_cell_numbers) if evaluation_cell_numbers else []
    if window_cells:
        dP_spread_window, dP_missing_window = pressure_drop_rel_spread_for_cells(
            metrics, window_cells
        )
    else:
        dP_spread_window, dP_missing_window = None, []

    checks: dict[str, dict[str, Any]] = {
        "lmh_relative_difference": {
            "value": lmh_rel,
            "threshold_abs": lmh_limit,
            "available": lmh_rel is not None,
            "passed": (
                lmh_rel is not None and abs(lmh_rel) < lmh_limit
            ),
        },
        "mass_balance_relative_error": {
            "value": mb_rel,
            "threshold_abs": mb_limit,
            "available": mb_rel is not None,
            "passed": (
                mb_rel is not None and abs(mb_rel) < mb_limit
            ),
        },
        "continuity_final": {
            "value": continuity,
            "threshold_max": continuity_limit,
            "available": continuity is not None,
            "passed": (
                continuity is not None and continuity < continuity_limit
            ),
        },
    }

    for name, flagged in non_finite.items():
        if flagged:
            checks[name].update(value=None, available=True, passed=False, non_finite=True)
    failures = [
        name for name, check in checks.items() if check["available"] and not check["passed"]
    ]
    unavailable = [name for name, check in checks.items() if not check["available"]]

    if failures:
        quality = QUALITY_FAIL
    elif unavailable:
        quality = QUALITY_UNKNOWN
    else:
        quality = QUALITY_PASS

    warnings: list[str] = []
    spread_note: Optional[str] = None
    if dP_spread_window is not None:
        warnings.append(PRESSURE_DROP_REL_SPREAD_WARNING)
        spread_note = PRESSURE_DROP_REL_SPREAD_NOTE

    return {
        "convergence_quality": quality,
        "needs_longer_solve": quality == QUALITY_FAIL,
        "failures": failures,
        "warnings": warnings,
        "unavailable": unavailable,
        "checks": checks,
        "lmh_relative_difference": lmh_rel,
        "mass_balance_relative_error": mb_rel,
        "continuity_final": continuity,
        # WARNING only — not part of the gate (converged physics / window).
        "pp_pressure_drop_rel_spread_window": dP_spread_window,
        "pp_pressure_drop_rel_spread_window_missing": dP_missing_window,
        "pp_pressure_drop_rel_spread_cells_4_7": dP_spread_4_7,
        "pp_pressure_drop_rel_spread_cells_4_7_missing": dP_missing_4_7,
        "pp_pressure_drop_rel_spread_note": spread_note,
    }


REPAIRED_FINAL_PROVENANCE_RE = re.compile(r"^repaired_from_iter_([1-9][0-9]*)$")


def repaired_final_iteration(run_manifest: Mapping[str, Any] | None) -> Optional[int]:
    """``N`` for a manifest repaired by ``repair_final_from_iter.py``, else None.

    Such a leaf's final is the saved ``*_iter_<N>`` pair, while its
    transcripts and ``.out`` histories run on past ``N`` (the stopped
    chunk), so tail-based evidence must stop at ``N``.
    """
    if not isinstance(run_manifest, Mapping):
        return None
    match = REPAIRED_FINAL_PROVENANCE_RE.fullmatch(str(run_manifest.get("final_provenance") or ""))
    return int(match.group(1)) if match else None


def continuity_at_iteration(case_dir: str | Path, iteration: int) -> Optional[float]:
    """Continuity residual of row ``iteration`` exactly, or None.

    Solver logs first, then other transcripts, newest first. No row
    ``iteration`` in any transcript is None (the gate is then UNKNOWN, not PASS).
    """
    from ro.residual_transcript import (
        list_transcript_candidates,
        parse_residual_table,
        read_text_replace,
    )

    def _mtime(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    candidates = sorted(
        list_transcript_candidates(Path(case_dir)),
        key=lambda p: (p.name.lower().startswith("solver_log_"), _mtime(p)),
        reverse=True,
    )
    for path in candidates:
        text, _err = read_text_replace(path)
        if text is None:
            continue
        rows, _detail = parse_residual_table(text)
        for row in rows:
            if int(row["iter"]) == int(iteration):
                return _as_float(row.get("continuity"))
    return None


def continuity_final_from_case_dir(
    case_dir: str | Path, *, max_iteration: Optional[int] = None
) -> Optional[float]:
    """Parse final continuity residual from the case solve transcript.

    ``max_iteration`` (a repaired final, see ``repaired_final_iteration``)
    takes row ``max_iteration`` instead of the transcript's last row.
    """
    from ro.residual_transcript import (  # local import keeps inventory light
        PARSE_OK,
        parse_residual_table,
        read_text_replace,
        select_solve_transcript,
    )

    if max_iteration is not None:
        return continuity_at_iteration(case_dir, max_iteration)
    path, status, _detail = select_solve_transcript(Path(case_dir))
    if path is None or status != PARSE_OK:
        return None
    text, _err = read_text_replace(path)
    if text is None:
        return None
    rows, table_detail = parse_residual_table(text)
    if not rows:
        return None
    if "unparsed_later_residual_rows=" in table_detail:
        return None
    return _as_float(rows[-1].get("continuity"))


def metrics_from_summary_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Pull gate inputs and per-cell dP from a summary_metrics_wide row."""
    # Prefer exact header names; also accept case-insensitive matches.
    lowered = {str(k).strip().lower(): k for k in row.keys()}
    out: dict[str, Any] = {}
    for key in (
        "lmh_relative_difference",
        "mass_balance_relative_error",
        "continuity_final",
    ):
        raw_key = lowered.get(key)
        out[key] = row.get(raw_key) if raw_key is not None else None
    for lowered_key, raw_key in lowered.items():
        match = _CELL_DP_KEY_RE.match(lowered_key)
        if match is None:
            continue
        out[f"pp_pressure_drop_cell_{int(match.group(1))}"] = row.get(raw_key)
    return out


def manifest_quality_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Fields safe to merge onto a run manifest (non-parameter)."""
    payload = {
        "convergence_quality": result["convergence_quality"],
        "needs_longer_solve": bool(result["needs_longer_solve"]),
        "convergence_quality_failures": list(result.get("failures") or []),
        "convergence_quality_warnings": list(result.get("warnings") or []),
        "convergence_quality_unavailable": list(result.get("unavailable") or []),
        "lmh_relative_difference": result.get("lmh_relative_difference"),
        "mass_balance_relative_error": result.get("mass_balance_relative_error"),
        "continuity_final": result.get("continuity_final"),
        "pp_pressure_drop_rel_spread_window": result.get(
            "pp_pressure_drop_rel_spread_window"
        ),
        "pp_pressure_drop_rel_spread_cells_4_7": result.get(
            "pp_pressure_drop_rel_spread_cells_4_7"
        ),
        "pp_pressure_drop_rel_spread_note": result.get(
            "pp_pressure_drop_rel_spread_note"
        ),
    }
    if result.get("convergence_gate") is not None:
        payload["convergence_gate"] = result["convergence_gate"]
        for key in (
            "lf_monitor_ramp_end",
            "lf_monitor_window",
            "lf_monitor_lmh_range",
            "lf_monitor_dp_range",
            "lf_monitor_tol_lmh",
            "lf_monitor_tol_dp",
            "lf_monitor_n",
        ):
            if key in result:
                payload[key] = result[key]
    return payload


# Reporting class (does not change the gate). ``continuity_boundary`` is a
# FAIL whose only failure is continuity_final, sitting on a plateau just
# above the limit: flux consistency and mass balance pass, continuity_final
# is below CONTINUITY_BOUNDARY_MAX, and the LMH and dP monitors do not drift
# (|monitor_trend_last500| <= MONITOR_FLAT_TREND_MAX, both recorded).
# Host case (2026-11-22): P_p100_h00 u0p2_p6M, continuity_final 1.28e-4
# with a 1.25-2.7e-4 plateau over the last 200 iterations; more
# iterations are not expected to cross 1e-4. Tables include such leaves
# only on request (campaign_v3 --include-continuity-boundary).
CONVERGENCE_CLASS_PASS = "pass"
CONVERGENCE_CLASS_CONTINUITY_BOUNDARY = "continuity_boundary"
CONVERGENCE_CLASS_FAIL = "fail"
CONVERGENCE_CLASS_UNKNOWN = "unknown"
CONTINUITY_BOUNDARY_MAX = 3e-4
MONITOR_FLAT_TREND_MAX = 1e-4
_FLAT_TREND_FIELDS = ("monitor_trend_last500_lmh", "monitor_trend_last500_dp")


def convergence_class(manifest: Mapping[str, Any]) -> str:
    """``pass``, ``continuity_boundary``, ``fail`` or ``unknown`` for a run manifest."""
    gate = manifest.get("convergence_quality")
    if gate == QUALITY_PASS:
        return CONVERGENCE_CLASS_PASS
    if gate != QUALITY_FAIL:
        return CONVERGENCE_CLASS_UNKNOWN
    if list(manifest.get("convergence_quality_failures") or []) != ["continuity_final"]:
        return CONVERGENCE_CLASS_FAIL
    lmh = _as_float(manifest.get("lmh_relative_difference"))
    mass = _as_float(manifest.get("mass_balance_relative_error"))
    continuity = _as_float(manifest.get("continuity_final"))
    if lmh is None or mass is None or continuity is None:
        return CONVERGENCE_CLASS_FAIL
    if not (math.isfinite(lmh) and math.isfinite(mass) and math.isfinite(continuity)):
        return CONVERGENCE_CLASS_FAIL
    if abs(lmh) >= LMH_REL_ABS_MAX or abs(mass) >= MASS_BALANCE_REL_ABS_MAX:
        return CONVERGENCE_CLASS_FAIL
    if not CONTINUITY_FINAL_MAX <= continuity < CONTINUITY_BOUNDARY_MAX:
        return CONVERGENCE_CLASS_FAIL
    for field in _FLAT_TREND_FIELDS:
        trend = _as_float(manifest.get(field))
        if trend is None or not math.isfinite(trend) or abs(trend) > MONITOR_FLAT_TREND_MAX:
            return CONVERGENCE_CLASS_FAIL
    return CONVERGENCE_CLASS_CONTINUITY_BOUNDARY
