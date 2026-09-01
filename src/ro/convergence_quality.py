"""Post-hoc convergence quality gate (independent of stop_reason).

The QoI / residual stop can declare convergence while two LMH paths still
disagree or continuity sits above 1e-4. Those checks caught under-converged
u0p3 at the 301-iteration QoI stop on D2450_a45 (p=6 MPa), where CP and
spacer dP were already within 0.1% of the longer residual-converged solution.

Unit-cell pressure-drop spread across evaluation cells 4–7 is recorded as a
diagnostic only. On D2450_a45 it is essentially unchanged between the short
and long solves (u0p2 ~2.58%, u0p3 ~14.7%) — a steady-state property of the
flow field / evaluation window, not a convergence signal.

Keep this separate from stop_reason: a max_iter_reached run (e.g. u0p1 with
continuity floored at ~4e-7) can still PASS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

QUALITY_PASS = "PASS"
QUALITY_FAIL = "FAIL"
QUALITY_UNKNOWN = "UNKNOWN"

LMH_REL_ABS_MAX = 1e-3
MASS_BALANCE_REL_ABS_MAX = 1e-3
CONTINUITY_FINAL_MAX = 1e-4
PRESSURE_DROP_CELLS = (4, 5, 6, 7)


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


def pressure_drop_rel_spread_cells_4_7(
    metrics: Mapping[str, Any],
) -> tuple[Optional[float], list[str]]:
    """Return (spread, missing_column_names) for pp_pressure_drop_cell_4..7."""
    values: list[float] = []
    missing: list[str] = []
    for cell in PRESSURE_DROP_CELLS:
        key = f"pp_pressure_drop_cell_{cell}"
        parsed = _as_float(metrics.get(key))
        if parsed is None:
            missing.append(key)
        else:
            values.append(parsed)
    if missing:
        return None, missing
    return relative_spread(values), []


def evaluate_convergence_quality(
    metrics: Mapping[str, Any],
    *,
    continuity_final: Any = None,
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

    Returns
    -------
    dict
        ``convergence_quality`` in {PASS, FAIL, UNKNOWN},
        ``needs_longer_solve`` (True only on FAIL),
        per-check values / pass flags, ``failures``, and diagnostic
        ``pp_pressure_drop_rel_spread_cells_4_7`` (not gated).
    """
    lmh_rel = _as_float(metrics.get("lmh_relative_difference"))
    mb_rel = _as_float(metrics.get("mass_balance_relative_error"))
    continuity = _as_float(continuity_final)
    if continuity is None:
        continuity = _as_float(metrics.get("continuity_final"))
    dP_spread, dP_missing = pressure_drop_rel_spread_cells_4_7(metrics)

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

    return {
        "convergence_quality": quality,
        "needs_longer_solve": quality == QUALITY_FAIL,
        "failures": failures,
        "unavailable": unavailable,
        "checks": checks,
        "lmh_relative_difference": lmh_rel,
        "mass_balance_relative_error": mb_rel,
        "continuity_final": continuity,
        # Diagnostic only — not part of the gate (converged physics / window).
        "pp_pressure_drop_rel_spread_cells_4_7": dP_spread,
        "pp_pressure_drop_rel_spread_cells_4_7_missing": dP_missing,
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
    rows, _table_detail = parse_residual_table(text)
    if not rows:
        return None
    return _as_float(rows[-1].get("continuity"))


def metrics_from_summary_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Pull gate inputs and dP diagnostic from a summary_metrics_wide row."""
    keys = (
        "lmh_relative_difference",
        "mass_balance_relative_error",
        *(f"pp_pressure_drop_cell_{cell}" for cell in PRESSURE_DROP_CELLS),
        "continuity_final",
    )
    # Prefer exact header names; also accept case-insensitive matches.
    lowered = {str(k).strip().lower(): k for k in row.keys()}
    out: dict[str, Any] = {}
    for key in keys:
        raw_key = lowered.get(key.lower())
        out[key] = row.get(raw_key) if raw_key is not None else None
    return out


def manifest_quality_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Fields safe to merge onto a run manifest (non-parameter)."""
    return {
        "convergence_quality": result["convergence_quality"],
        "needs_longer_solve": bool(result["needs_longer_solve"]),
        "convergence_quality_failures": list(result.get("failures") or []),
        "convergence_quality_unavailable": list(result.get("unavailable") or []),
        "lmh_relative_difference": result.get("lmh_relative_difference"),
        "mass_balance_relative_error": result.get("mass_balance_relative_error"),
        "continuity_final": result.get("continuity_final"),
        "pp_pressure_drop_rel_spread_cells_4_7": result.get(
            "pp_pressure_drop_rel_spread_cells_4_7"
        ),
    }
