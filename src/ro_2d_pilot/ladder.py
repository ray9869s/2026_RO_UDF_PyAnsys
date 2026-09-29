"""Compare mesh-study results for one physical case.

The script reports how the quantities change as the mesh is refined.
It does not assign a level to LF or HF, and it does not start Fluent.
Formal GCI is omitted: interval counts are rounded from one target
edge length, so the refinement ratio is not constant.
"""

from __future__ import annotations

import math
from typing import Mapping

from ro_2d_pilot.config import MESH_LEVELS
from ro_2d_pilot.source_schedule import display_ramp, full_source_gate_ok

GCI_NOT_APPLIED = (
    "Formal GCI and Richardson extrapolation are not applied. "
    "One target edge length sets every interval, and rounding makes "
    "the height, streamwise, and radial ratios inexact."
)

_LEGACY_LEVEL = {
    "low": "coarse",
    "high": "fine",
}

_IDENTITY_FIELDS = (
    "geo_id",
    "d_m",
    "L_m",
    "channel_height_m",
    "n_pitches",
    "inlet_velocity_m_s",
    "outlet_gauge_pressure_pa",
)

_TABLE_FIELDS = (
    "cell_count",
    "cells_across_channel_height",
    "lmh",
    "cp_average",
    "cp_excess",
    "pressure_drop_per_length_pa_per_m",
    "mass_balance_rel",
    "solver_iterations",
    "solver_wall_time_s",
    "total_wall_time_s",
    "startup_overhead_s",
    "extraction_time_s",
)

_QOIS = (
    "lmh",
    "cp_excess",
    "pressure_drop_per_length_pa_per_m",
)


class LadderMismatch(ValueError):
    """Mesh-study records are not one physical case."""


def compare_ladder(records: list[Mapping[str, object]]) -> dict[str, object]:
    """Order records from coarse to fine and compare adjacent levels."""
    if len(records) < 2:
        raise LadderMismatch("A mesh ladder needs at least two result records.")
    ordered = _ordered_records(records)
    rows = [_row(record) for record in ordered]
    pairs = []
    for coarser, finer in zip(rows, rows[1:]):
        pairs.append(_adjacent(coarser, finer))
    trends = {
        name: _trend(
            [
                row[name] if row["mesh_comparison"] == "full_source" else None
                for row in rows
            ]
        )
        for name in _QOIS
    }
    return {
        "geo_id": ordered[0]["geo_id"],
        "levels": [row["mesh_level"] for row in rows],
        "rows": rows,
        "adjacent": pairs,
        "trend_with_refinement": trends,
        "formal_gci": GCI_NOT_APPLIED,
    }


def format_ladder(report: Mapping[str, object]) -> str:
    """Plain text. A missing number is printed as ``undefined``."""
    rows = report["rows"]
    adjacent = report["adjacent"]
    trends = report["trend_with_refinement"]
    if not isinstance(rows, list) or not isinstance(adjacent, list):
        raise TypeError("ladder report is missing rows.")
    if not isinstance(trends, Mapping):
        raise TypeError("ladder report is missing trends.")
    lines = [
        "2D mesh ladder",
        f"geo_id={report['geo_id']}",
        "same physical case",
        "Mesh levels are not LF/HF assignments.",
        "",
        _header(),
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("ladder row is not a mapping.")
        lines.append(_format_row(row))
    lines.append("")
    lines.append(
        "A row marked unsuitable is not at full source ramp 1.0 "
        "and is omitted from QoI discrepancies."
    )
    lines.append(
        "Adjacent relative discrepancy with respect to the finer mesh, "
        "(coarser - finer) / finer:"
    )
    for pair in adjacent:
        if not isinstance(pair, Mapping):
            raise TypeError("adjacent pair is not a mapping.")
        lines.append(
            f"{pair['coarser']} -> {pair['finer']}"
        )
        if not pair["qoi_compared"]:
            lines.append(
                "  QoI discrepancy not compared; "
                "full source ramp was not 1.0."
            )
        for name in _QOIS:
            lines.append(
                f"  {name}: {_format_number(pair['relative_discrepancy'][name])}"
            )
        lines.append(
            "  solver_time_ratio_coarser_over_finer: "
            + _format_number(pair["solver_time_ratio"])
        )
        lines.append(
            "  total_wall_time_ratio_coarser_over_finer: "
            + _format_number(pair["total_wall_time_ratio"])
        )
    lines.append("")
    lines.append("Trend with refinement:")
    for name in _QOIS:
        lines.append(f"  {name}: {trends[name]}")
    lines.append(str(report["formal_gci"]))
    lines.append("No acceptance threshold is applied.")
    return "\n".join(lines)


def _ordered_records(
    records: list[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    problems = _identity_problems(records)
    ranked: list[tuple[int, Mapping[str, object]]] = []
    seen: dict[str, int] = {}
    for record in records:
        level = _level_of(record)
        if level in seen:
            problems.append(f"duplicate mesh level {level!r}.")
            continue
        seen[level] = len(ranked)
        ranked.append((MESH_LEVELS.index(level), record))
    if problems:
        raise LadderMismatch(
            "Refusing mesh-ladder comparison:\n" + "\n".join(problems)
        )
    ranked.sort(key=lambda item: item[0])
    return [record for _, record in ranked]


def _identity_problems(records: list[Mapping[str, object]]) -> list[str]:
    problems: list[str] = []
    base = records[0]
    for key in _IDENTITY_FIELDS:
        if key not in base:
            problems.append(f"missing {key}.")
            continue
        for record in records[1:]:
            if key not in record:
                problems.append(f"missing {key}.")
                continue
            if not _same(base[key], record[key]):
                problems.append(
                    f"{key}: {base[key]!r} vs {record[key]!r}."
                )
    return problems


def _level_of(record: Mapping[str, object]) -> str:
    raw = record.get("mesh_level")
    if isinstance(raw, str) and raw in MESH_LEVELS:
        return raw
    label = record.get("fidelity")
    if isinstance(label, str) and label in _LEGACY_LEVEL:
        return _LEGACY_LEVEL[label]
    if isinstance(label, str) and label in MESH_LEVELS:
        return label
    raise LadderMismatch(
        f"Unrecognized mesh level mesh_level={raw!r} fidelity={label!r}."
    )


def _row(record: Mapping[str, object]) -> dict[str, object]:
    cp_average = _number(record, "cp_average")
    total_iterations = record.get("total_iterations")
    if total_iterations is None:
        total_iterations = record.get("solver_iterations")
    final_ramp = display_ramp(record.get("source_ramp_final"), total_iterations)
    row: dict[str, object] = {
        "mesh_level": _level_of(record),
        "convergence_status": _status_text(record.get("convergence_status")),
        "cp_excess": None if cp_average is None else cp_average - 1.0,
        "total_iterations": _number({"total_iterations": total_iterations}, "total_iterations"),
        "full_source_iterations": _number(record, "full_source_iterations"),
        "final_ramp": _number({"final_ramp": final_ramp}, "final_ramp"),
        "mesh_comparison": (
            "full_source" if _comparable(record) else "unsuitable"
        ),
    }
    for name in _TABLE_FIELDS:
        if name == "cp_excess":
            continue
        row[name] = _number(record, name)
    return row


def _comparable(record: Mapping[str, object]) -> bool:
    return full_source_gate_ok(
        source_ramp_final=record.get("source_ramp_final"),
        full_source_reached=record.get("full_source_reached"),
        convergence_checked_after_full_source=record.get(
            "convergence_checked_after_full_source"
        ),
        full_source_iterations=record.get("full_source_iterations"),
    )


def _adjacent(
    coarser: Mapping[str, object],
    finer: Mapping[str, object],
) -> dict[str, object]:
    compared = (
        coarser["mesh_comparison"] == "full_source"
        and finer["mesh_comparison"] == "full_source"
    )
    relative = {
        name: _relative(coarser[name], finer[name]) if compared else None
        for name in _QOIS
    }
    return {
        "coarser": coarser["mesh_level"],
        "finer": finer["mesh_level"],
        "qoi_compared": compared,
        "relative_discrepancy": relative,
        "solver_time_ratio": _ratio(
            coarser["solver_wall_time_s"],
            finer["solver_wall_time_s"],
        ),
        "total_wall_time_ratio": _ratio(
            coarser["total_wall_time_s"],
            finer["total_wall_time_s"],
        ),
    }


def _trend(values: list[object]) -> str:
    """Trend across finite values only.

    Unsuitable mesh levels are passed as ``None``. ``undefined`` and NaN
    are dropped the same way. Remaining numbers stay in refinement order.
    """
    numbers: list[float] = []
    for value in values:
        if value is None or value == "undefined":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if not math.isfinite(number):
            continue
        numbers.append(number)
    if len(numbers) < 3:
        return "fewer than three finite values"
    deltas = [right - left for left, right in zip(numbers, numbers[1:])]
    if all(delta <= 0.0 for delta in deltas):
        return "non-increasing with refinement"
    if all(delta >= 0.0 for delta in deltas):
        return "non-decreasing with refinement"
    return "not monotonic with refinement"


def _number(record: Mapping[str, object], key: str) -> float | None:
    if key not in record or record[key] is None:
        return None
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LadderMismatch(f"{key} must be a number or null, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise LadderMismatch(f"{key} must be finite, got {value!r}.")
    return number


def _relative(coarser: object, finer: object) -> float | None:
    if not isinstance(coarser, (int, float)) or not isinstance(finer, (int, float)):
        return None
    if isinstance(coarser, bool) or isinstance(finer, bool) or finer == 0.0:
        return None
    return (float(coarser) - float(finer)) / float(finer)


def _ratio(coarser: object, finer: object) -> float | None:
    if not isinstance(coarser, (int, float)) or not isinstance(finer, (int, float)):
        return None
    if isinstance(coarser, bool) or isinstance(finer, bool) or finer == 0.0:
        return None
    return float(coarser) / float(finer)


def _same(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _header() -> str:
    return (
        f"{'mesh_level':<12}{'cells':>10}{'total_iter':>12}"
        f"{'full_iter':>11}{'final_ramp':>12}{'lmh':>12}"
        f"{'cp':>12}{'cp_excess':>12}{'dp_per_L':>12}{'mass_bal':>12}"
        f"{'status':>20}{'solve_s':>10}{'total_s':>10}{'compare':>12}"
    )


def _format_row(row: Mapping[str, object]) -> str:
    return (
        f"{row['mesh_level']:<12}"
        f"{_format_number(row['cell_count']):>10}"
        f"{_format_number(row['total_iterations']):>12}"
        f"{_format_number(row['full_source_iterations']):>11}"
        f"{_format_number(row['final_ramp']):>12}"
        f"{_format_number(row['lmh']):>12}"
        f"{_format_number(row['cp_average']):>12}"
        f"{_format_number(row['cp_excess']):>12}"
        f"{_format_number(row['pressure_drop_per_length_pa_per_m']):>12}"
        f"{_format_number(row['mass_balance_rel']):>12}"
        f"{row['convergence_status']:>20}"
        f"{_format_number(row['solver_wall_time_s']):>10}"
        f"{_format_number(row['total_wall_time_s']):>10}"
        f"{row['mesh_comparison']:>12}"
    )


def _status_text(value: object) -> str:
    if value is None or value == "":
        return "undefined"
    return str(value)


def _format_number(value: object) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.6g}"
    return str(value)
