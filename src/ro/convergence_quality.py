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
    lmh_rel = _as_float(metrics.get("lmh_relative_difference"))
    mb_rel = _as_float(metrics.get("mass_balance_relative_error"))
    continuity = _as_float(continuity_final)
    if continuity is None:
        continuity = _as_float(metrics.get("continuity_final"))
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
            "threshold_abs": LMH_REL_ABS_MAX,
            "available": lmh_rel is not None,
            "passed": (
                lmh_rel is not None and abs(lmh_rel) < LMH_REL_ABS_MAX
            ),
        },
        "mass_balance_relative_error": {
            "value": mb_rel,
            "threshold_abs": MASS_BALANCE_REL_ABS_MAX,
            "available": mb_rel is not None,
            "passed": (
                mb_rel is not None and abs(mb_rel) < MASS_BALANCE_REL_ABS_MAX
            ),
        },
        "continuity_final": {
            "value": continuity,
            "threshold_max": CONTINUITY_FINAL_MAX,
            "available": continuity is not None,
            "passed": (
                continuity is not None and continuity < CONTINUITY_FINAL_MAX
            ),
        },
    }

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


def continuity_final_from_case_dir(case_dir: str | Path) -> Optional[float]:
    """Parse final continuity residual from the case solve transcript."""
    from ro.residual_transcript import (  # local import keeps inventory light
        PARSE_OK,
        parse_residual_table,
        read_text_replace,
        select_solve_transcript,
    )

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
    return {
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
