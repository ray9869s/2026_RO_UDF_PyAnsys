"""Compare one low result with one high result of the same physical case.

The comparison prints measured differences. It does not score the pair
and it does not start Fluent.
"""

from __future__ import annotations

import math
from typing import Mapping

_IDENTITY_FIELDS = (
    "geo_id",
    "d_m",
    "L_m",
    "channel_height_m",
    "n_pitches",
    "inlet_velocity_m_s",
    "outlet_gauge_pressure_pa",
)

_PRINTED_FIELDS = (
    "cell_count",
    "lmh",
    "cp_average",
    "cp_excess",
    "pressure_drop_pa",
    "pressure_drop_per_length_pa_per_m",
    "mass_balance_rel",
    "solver_iterations",
    "solver_wall_time_s",
    "total_wall_time_s",
)

_RELATIVE_FIELDS = (
    "lmh",
    "cp_excess",
    "pressure_drop_per_length_pa_per_m",
)


class PairMismatch(ValueError):
    """Low and high records are not the same physical case."""


def compare_results(
    low: Mapping[str, object],
    high: Mapping[str, object],
) -> dict[str, object]:
    """Return measured LF/HF values. ``(LF - HF) / HF`` where HF is nonzero."""
    problems = _identity_problems(low, high)
    if problems:
        raise PairMismatch(
            "Refusing LF/HF comparison because the physical case differs:\n"
            + "\n".join(problems)
        )
    metrics: dict[str, dict[str, float | None]] = {}
    for name in _PRINTED_FIELDS:
        if name == "cp_excess":
            low_value = _cp_excess(low)
            high_value = _cp_excess(high)
        else:
            low_value = _optional_number(low, name)
            high_value = _optional_number(high, name)
        metrics[name] = {"low": low_value, "high": high_value}
    relative = {
        name: _relative(metrics[name]["low"], metrics[name]["high"])
        for name in _RELATIVE_FIELDS
    }
    return {
        "geo_id": low["geo_id"],
        "metrics": metrics,
        "relative_difference": relative,
        "cost_ratio_solver": _ratio(
            metrics["solver_wall_time_s"]["low"],
            metrics["solver_wall_time_s"]["high"],
        ),
        "cost_ratio_total_wall": _ratio(
            metrics["total_wall_time_s"]["low"],
            metrics["total_wall_time_s"]["high"],
        ),
    }


def format_comparison(report: Mapping[str, object]) -> str:
    """Plain text table. A missing ratio is printed as ``undefined``."""
    metrics = report["metrics"]
    relative = report["relative_difference"]
    if not isinstance(metrics, Mapping) or not isinstance(relative, Mapping):
        raise TypeError("comparison report is missing metrics.")
    lines = [
        "LF/HF pair",
        f"geo_id={report['geo_id']}",
        "same physical case",
        f"{'field':<40}{'low':>16}{'high':>16}{'(LF-HF)/HF':>16}",
    ]
    for name in _PRINTED_FIELDS:
        row = metrics[name]
        if not isinstance(row, Mapping):
            raise TypeError(f"metric {name} is not a mapping.")
        ratio = relative[name] if name in relative else None
        lines.append(
            f"{name:<40}{_format_number(row['low']):>16}"
            f"{_format_number(row['high']):>16}{_format_number(ratio):>16}"
        )
    lines.append(
        "cost_ratio_solver="
        + _format_number(report["cost_ratio_solver"])
        + "  (low solver time / high solver time)"
    )
    lines.append(
        "cost_ratio_total_wall="
        + _format_number(report["cost_ratio_total_wall"])
        + "  (low total wall time / high total wall time)"
    )
    lines.append("No acceptance threshold is applied.")
    return "\n".join(lines)


def _identity_problems(
    low: Mapping[str, object],
    high: Mapping[str, object],
) -> list[str]:
    problems: list[str] = []
    if low.get("fidelity") != "low":
        problems.append(
            f"first record fidelity must be 'low', got {low.get('fidelity')!r}."
        )
    if high.get("fidelity") != "high":
        problems.append(
            f"second record fidelity must be 'high', got {high.get('fidelity')!r}."
        )
    for key in _IDENTITY_FIELDS:
        if key not in low or key not in high:
            problems.append(f"missing {key}.")
            continue
        if not _same_identity(low[key], high[key]):
            problems.append(f"{key}: low={low[key]!r} high={high[key]!r}.")
    return problems


def _same_identity(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _optional_number(record: Mapping[str, object], key: str) -> float | None:
    if key not in record or record[key] is None:
        return None
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PairMismatch(f"{key} must be a number or null, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise PairMismatch(f"{key} must be finite, got {value!r}.")
    return number


def _cp_excess(record: Mapping[str, object]) -> float | None:
    cp_average = _optional_number(record, "cp_average")
    if cp_average is None:
        return None
    return cp_average - 1.0


def _relative(low: float | None, high: float | None) -> float | None:
    if low is None or high is None or high == 0.0:
        return None
    return (low - high) / high


def _ratio(low: float | None, high: float | None) -> float | None:
    if low is None or high is None or high == 0.0:
        return None
    return low / high


def _format_number(value: object) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.8g}"
    return str(value)
