"""LF monitor stop. Importing this module does not launch Fluent.

The production QoI stop is unchanged. This rule is used only when a
fidelity level sets ``lf_monitor_stop``.

``ramp_end`` is ``#define RAMP_ITER_3`` in the UDF that was copied into
the case and compiled. ``source_ramp()`` returns ``RAMP_FACTOR_FULL``
when ``N_ITER >= RAMP_ITER_3`` (``udfs/260822_RO_UDF.c`` ``source_ramp``).
The N-iteration window may stop the run only when every iteration in it
is strictly after that define, so the earliest stop is ``ramp_end + N``.
``configs/run_config.py`` ``ramp_full_iteration`` is not read.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ro.solver_common import (
    STOP_REASON_LF_MONITOR_STABLE,
    STOP_REASON_MAX_ITER_REACHED,
    parse_fluent_report_file_series,
)
from ro.udm_layout import find_case_udf_path

LMH_REPORT_FILE = "lmh_udm_avg.out"
DP_REPORT_FILE = "pressure_drop_spacer.out"
GATE_LF_MONITOR = "lf_monitor"
_RAMP_ITER_3_RE = re.compile(
    r"^[ \t]*#define[ \t]+RAMP_ITER_3[ \t]+(\d+)[ \t]*(?:/\*.*\*/)?[ \t]*$",
    re.MULTILINE,
)
_RAMP_COMPARE_RE = re.compile(r"if\s*\(\s*iter\s*<\s*RAMP_ITER_3\s*\)")


def ramp_end_from_udf_source(source: str) -> int:
    """Return ``RAMP_ITER_3`` from one UDF translation unit.

    Raises if the define is missing or repeated, or if ``source_ramp``
    does not compare ``iter`` with ``RAMP_ITER_3``.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError("UDF source is empty; cannot read RAMP_ITER_3.")
    found = _RAMP_ITER_3_RE.findall(source)
    if len(found) != 1:
        raise ValueError(
            f"Expected one #define RAMP_ITER_3 in the UDF, found {len(found)}."
        )
    if _RAMP_COMPARE_RE.search(source) is None:
        raise ValueError(
            "UDF source_ramp does not compare iter with RAMP_ITER_3, "
            "so ramp_end cannot be read from this file."
        )
    return int(found[0])


def ramp_end_from_case(case_dir: str | Path) -> int:
    """Read ``ramp_end`` from the case-local UDF the solver compiled.

    ``find_case_udf_path`` is the ``*_RO_UDF.c`` copy written by
    ``copy_and_patch_udf_to_case_folder``. It does not fall back to
    ``udfs/`` and it does not use ``ramp_full_iteration``.
    """
    path = find_case_udf_path(case_dir)
    return ramp_end_from_udf_source(path.read_text(encoding="utf-8"))


def require_lf_monitor_stop(value: Any) -> dict[str, Any]:
    """Return ``tol_lmh``, ``tol_dp``, and ``n``. ``None`` is refused."""
    if not isinstance(value, Mapping):
        raise TypeError(
            "lf_monitor_stop must be an object with tol_lmh, tol_dp, and n, "
            f"got {value!r}."
        )
    unknown = sorted(set(value) - {"tol_lmh", "tol_dp", "n"})
    if unknown:
        raise ValueError(f"lf_monitor_stop has unknown keys: {unknown}.")
    missing = [key for key in ("tol_lmh", "tol_dp", "n") if key not in value]
    if missing:
        raise ValueError(f"lf_monitor_stop is missing {missing}.")
    return {
        "tol_lmh": _positive_float("tol_lmh", value["tol_lmh"]),
        "tol_dp": _positive_float("tol_dp", value["tol_dp"]),
        "n": _positive_int("n", value["n"]),
    }


def monitor_range(values: Sequence[float]) -> float:
    """``(max - min) / |last|`` over one window."""
    if isinstance(values, (str, bytes)) or not values:
        raise ValueError("monitor window is empty.")
    numbers = [float(value) for value in values]
    if any(isinstance(value, bool) or not math.isfinite(number) for value, number in zip(values, numbers)):
        raise ValueError(f"monitor window is not finite: {list(values)!r}.")
    last = numbers[-1]
    if last == 0.0:
        raise ValueError("monitor window last value is 0; the range is undefined.")
    return (max(numbers) - min(numbers)) / abs(last)


def lf_monitor_decision(
    lmh: Sequence[tuple[int, float]],
    dp: Sequence[tuple[int, float]],
    *,
    ramp_end: int,
    n: int,
    tol_lmh: float,
    tol_dp: float,
) -> dict[str, Any]:
    """Decide whether the last N samples of both monitors are stable.

    The window is the last ``n`` samples. It is eligible only when those
    samples are the same consecutive iterations on both monitors and the
    first iteration is strictly greater than ``ramp_end``. A numerically
    flat window that still overlaps the ramp is not stable.
    """
    ramp = _nonnegative_int("ramp_end", ramp_end)
    count = _positive_int("n", n)
    tol_lmh_value = _positive_float("tol_lmh", tol_lmh)
    tol_dp_value = _positive_float("tol_dp", tol_dp)
    base = {
        "stable": False,
        "reason": "short_series",
        "ramp_end": ramp,
        "n": count,
        "tol_lmh": tol_lmh_value,
        "tol_dp": tol_dp_value,
        "window_start": None,
        "window_end": None,
        "lmh_range": None,
        "dp_range": None,
    }
    if len(lmh) < count or len(dp) < count:
        return base
    lmh_window = list(lmh[-count:])
    dp_window = list(dp[-count:])
    lmh_iterations = [int(iteration) for iteration, _value in lmh_window]
    dp_iterations = [int(iteration) for iteration, _value in dp_window]
    if lmh_iterations != dp_iterations:
        raise ValueError(
            "LMH and spacer dP monitor windows do not share iterations: "
            f"lmh={lmh_iterations!r}, dp={dp_iterations!r}."
        )
    expected = list(range(lmh_iterations[0], lmh_iterations[0] + count))
    if lmh_iterations != expected:
        raise ValueError(
            "monitor window iterations are not consecutive: "
            f"{lmh_iterations!r}."
        )
    start = lmh_iterations[0]
    end = lmh_iterations[-1]
    base["window_start"] = start
    base["window_end"] = end
    lmh_range = monitor_range([value for _iteration, value in lmh_window])
    dp_range = monitor_range([value for _iteration, value in dp_window])
    base["lmh_range"] = lmh_range
    base["dp_range"] = dp_range
    if start <= ramp:
        base["reason"] = "window_overlaps_ramp"
        return base
    if lmh_range <= tol_lmh_value and dp_range <= tol_dp_value:
        base["stable"] = True
        base["reason"] = "lf_monitor_stable"
        return base
    base["reason"] = "not_yet"
    return base


def read_case_monitor_series(case_dir: str | Path) -> tuple[list, list]:
    """Read ``lmh_udm_avg.out`` and ``pressure_drop_spacer.out``."""
    directory = Path(case_dir)
    series = []
    for name in (LMH_REPORT_FILE, DP_REPORT_FILE):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"Monitor report file not found: {path}")
        parsed = parse_fluent_report_file_series(
            path.read_text(encoding="utf-8", errors="ignore")
        )
        if not parsed:
            raise ValueError(f"Monitor report file has no numeric rows: {path}")
        series.append(parsed)
    return series[0], series[1]


def run_lf_monitor_iterations(
    *,
    iterate,
    read_decision,
    ramp_end: int,
    n: int,
    max_iterations: int,
) -> dict[str, Any]:
    """Iterate until the monitor window is stable or ``max_iterations``.

    ``iterate(count)`` runs that many Fluent iterations with residual and
    QoI stopping already off. The first check is at ``ramp_end + n``.
    """
    ramp = _nonnegative_int("ramp_end", ramp_end)
    count = _positive_int("n", n)
    limit = _positive_int("max_iterations", max_iterations)
    earliest = ramp + count

    def _finish(done: int, reason: str) -> dict[str, Any]:
        decision = dict(read_decision())
        decision["stop_reason"] = reason
        decision["iterations_run"] = done
        return decision

    if earliest > limit:
        iterate(limit)
        return _finish(limit, STOP_REASON_MAX_ITER_REACHED)
    iterate(earliest)
    decision = read_decision()
    if decision.get("stable"):
        decision = dict(decision)
        decision["stop_reason"] = STOP_REASON_LF_MONITOR_STABLE
        decision["iterations_run"] = earliest
        return decision
    done = earliest
    while done < limit:
        iterate(1)
        done += 1
        decision = read_decision()
        if decision.get("stable"):
            decision = dict(decision)
            decision["stop_reason"] = STOP_REASON_LF_MONITOR_STABLE
            decision["iterations_run"] = done
            return decision
    return _finish(limit, STOP_REASON_MAX_ITER_REACHED)


def evaluate_lf_monitor_gate(
    case_dir: str | Path,
    *,
    continuity_final: Any,
    tol_lmh: float,
    tol_dp: float,
    n: int,
    metrics: Mapping[str, Any] | None = None,
    evaluation_cell_numbers: Sequence[int] | None = None,
) -> dict[str, Any]:
    """PASS when both monitors are stable and continuity is finite.

    Does not read ``lmh_relative_difference`` or
    ``mass_balance_relative_error``. Continuity has no magnitude limit.
    Pressure-drop spread stays a warning, as in the production gate.
    """
    from ro.convergence_quality import (
        PRESSURE_DROP_REL_SPREAD_NOTE,
        PRESSURE_DROP_REL_SPREAD_WARNING,
        pressure_drop_rel_spread_cells_4_7,
        pressure_drop_rel_spread_for_cells,
    )

    ramp_end = ramp_end_from_case(case_dir)
    lmh, dp = read_case_monitor_series(case_dir)
    decision = lf_monitor_decision(
        lmh,
        dp,
        ramp_end=ramp_end,
        n=n,
        tol_lmh=tol_lmh,
        tol_dp=tol_dp,
    )
    continuity = _finite_or_none(continuity_final)
    failures = []
    if not decision["stable"]:
        failures.append("lf_monitor_stable")
    if continuity is None:
        failures.append("continuity_final")
    quality = "PASS" if not failures else "FAIL"
    metric_row = metrics or {}
    dP_4_7, _missing_4_7 = pressure_drop_rel_spread_cells_4_7(metric_row)
    window_cells = list(evaluation_cell_numbers or [])
    if window_cells:
        dP_window, _missing_window = pressure_drop_rel_spread_for_cells(
            metric_row, window_cells
        )
    else:
        dP_window = None
    warnings = []
    spread_note = None
    if dP_window is not None:
        warnings.append(PRESSURE_DROP_REL_SPREAD_WARNING)
        spread_note = PRESSURE_DROP_REL_SPREAD_NOTE
    return {
        "convergence_quality": quality,
        "needs_longer_solve": quality == "FAIL",
        "failures": failures,
        "warnings": warnings,
        "unavailable": [],
        "checks": {
            "lf_monitor_stable": {
                "passed": bool(decision["stable"]),
                "reason": decision["reason"],
                "lmh_range": decision["lmh_range"],
                "dp_range": decision["dp_range"],
                "tol_lmh": decision["tol_lmh"],
                "tol_dp": decision["tol_dp"],
                "n": decision["n"],
                "ramp_end": decision["ramp_end"],
            },
            "continuity_final": {
                "value": continuity,
                "available": continuity is not None,
                "passed": continuity is not None,
            },
        },
        "lmh_relative_difference": None,
        "mass_balance_relative_error": None,
        "continuity_final": continuity,
        "convergence_gate": GATE_LF_MONITOR,
        "lf_monitor_ramp_end": decision["ramp_end"],
        "lf_monitor_window": {
            "start": decision["window_start"],
            "end": decision["window_end"],
            "n": decision["n"],
        },
        "lf_monitor_lmh_range": decision["lmh_range"],
        "lf_monitor_dp_range": decision["dp_range"],
        "lf_monitor_tol_lmh": decision["tol_lmh"],
        "lf_monitor_tol_dp": decision["tol_dp"],
        "lf_monitor_n": decision["n"],
        "pp_pressure_drop_rel_spread_window": dP_window,
        "pp_pressure_drop_rel_spread_cells_4_7": dP_4_7,
        "pp_pressure_drop_rel_spread_note": spread_note,
    }


def manifest_monitor_fields(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Non-parameter manifest fields for one LF monitor decision."""
    return {
        "lf_monitor_ramp_end": decision["ramp_end"],
        "lf_monitor_window": {
            "start": decision.get("window_start"),
            "end": decision.get("window_end"),
            "n": decision["n"],
        },
        "lf_monitor_lmh_range": decision.get("lmh_range"),
        "lf_monitor_dp_range": decision.get("dp_range"),
        "lf_monitor_tol_lmh": decision["tol_lmh"],
        "lf_monitor_tol_dp": decision["tol_dp"],
        "lf_monitor_n": decision["n"],
    }


def _positive_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}.")
    return number


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value!r}.")
    return value


def _nonnegative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}.")
    return value


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return number
