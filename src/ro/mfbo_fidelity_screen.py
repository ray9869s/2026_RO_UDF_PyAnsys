"""Paired LF/HF screen for pillar designs.

Importing this module does not launch Fluent. Correlation, rank reversal,
and relative discrepancy use the same definitions as
``ro_2d_pilot.fidelity_screen``: relative bias is ``(LF - HF) / HF``,
Pearson and Spearman are computed only for three or more finite pairs,
and a rank reversal is a strict disagreement about which design is better.
``cp_average`` is compared as CP-1. Wall-time cost is
``(mesh + solver + extraction)`` at LF divided by the same sum at HF.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.mfbo_adapter import (
    PillarDesign,
    _design,
    evaluate,
    mesh_id_for_settings,
    resolve_data_root,
)
from ro.mfbo_drivers import Drivers, repo_root
from ro_2d_pilot.fidelity_screen import pearson, rank_reversal_count, spearman

LF_LEVEL = "LF"
HF_LEVEL = "HF"
LEVELS = (LF_LEVEL, HF_LEVEL)
TBD = "TBD"
SCREEN_NAME = "3d_fidelity_screen"

# Larger LMH is better. Smaller CP-1 and dP/L are better.
# cp_average is compared as CP-1, matching the 2D cp_excess screen.
_QOI = (
    ("lmh", True, False),
    ("lmh_module_area", True, False),
    ("pressure_drop_per_length_pa_per_m", False, False),
    ("cp_average", False, True),
)
_WALL_KEYS = (
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
)
_IDENTITY = ("geo_id", "d_p_mm", "d_h_mm", "d_f_mm", "run_id")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_EVALUATE_SCRIPT = "scripts/mfbo/run_3d_evaluate.py"


def load_fidelity_table(path: str | Path) -> dict[str, Any]:
    """Read a fidelity table. The string ``TBD`` is kept for the caller to refuse."""
    file = Path(path)
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid fidelity table JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"could not read fidelity table {path}: {exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"fidelity table must be a non-empty JSON object: {path}")
    for name, entry in payload.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"fidelity name must be a non-empty string, got {name!r}.")
        if entry == TBD:
            continue
        if not isinstance(entry, dict):
            raise TypeError(
                f"fidelity {name!r} must be mesh settings or {TBD!r}, "
                f"got {type(entry).__name__}."
            )
    return payload


def require_runnable_fidelity(table: Mapping[str, Any], fidelity: str) -> dict[str, Any]:
    """Refuse a missing level and a level marked ``TBD``."""
    if not isinstance(fidelity, str) or not fidelity:
        raise ValueError(f"fidelity must be a non-empty name, got {fidelity!r}.")
    if fidelity not in table:
        raise KeyError(
            f"fidelity {fidelity!r} is not in the fidelity table. "
            "High fidelity is not defined."
        )
    entry = table[fidelity]
    if entry == TBD:
        raise ValueError(f"fidelity {fidelity!r} is TBD and is refused.")
    mesh_id_for_settings(entry)
    return dict(entry)


def require_screen_table(table: Mapping[str, Any]) -> dict[str, Any]:
    """Require exactly LF and HF, both runnable mesh settings."""
    if not isinstance(table, Mapping):
        raise TypeError("fidelity table must be a mapping.")
    names = list(table)
    if set(names) != set(LEVELS):
        raise ValueError(
            "A 3D fidelity screen needs exactly two levels, LF and HF, "
            f"got {names!r}."
        )
    checked = {}
    for name in LEVELS:
        checked[name] = require_runnable_fidelity(table, name)
    return checked


def load_designs(path: str | Path) -> list[PillarDesign]:
    """Read a non-empty JSON list of ``d_p_mm``, ``d_h_mm``, ``d_f_mm`` objects."""
    file = Path(path)
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid design list JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"could not read design list {path}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"design list must be a non-empty JSON list: {path}")
    designs = []
    for index, item in enumerate(payload):
        try:
            designs.append(_design(item))
        except (KeyError, TypeError, ValueError) as exc:
            raise type(exc)(f"design {index}: {exc}") from exc
    return designs


def build_queue(
    designs: Sequence[PillarDesign],
    *,
    run_id: str,
    data_root: str | Path,
    fidelity_table: str | Path,
) -> list[dict[str, Any]]:
    """One ``run_3d_evaluate.py`` job per design and level, LF then HF.

    Does not call ``evaluate``. Job ids match the queue runner's filename-safe
    pattern. ``{python}`` is left for that runner to replace.
    """
    if not designs:
        raise ValueError("design list is empty.")
    root = resolve_data_root(data_root)
    table_text = _queue_path(fidelity_table)
    jobs = []
    seen = set()
    for design in designs:
        geo_id = format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm)
        for fidelity in LEVELS:
            job_id = f"{fidelity}-{geo_id}"
            if _JOB_ID_RE.fullmatch(job_id) is None:
                raise ValueError(f"job id is not filename-safe: {job_id!r}.")
            if job_id in seen:
                raise ValueError(f"duplicate job id {job_id!r}.")
            seen.add(job_id)
            jobs.append(
                {
                    "id": job_id,
                    "argv": [
                        "{python}",
                        _EVALUATE_SCRIPT,
                        "--d-p-mm",
                        _cli_number(design.d_p_mm),
                        "--d-h-mm",
                        _cli_number(design.d_h_mm),
                        "--d-f-mm",
                        _cli_number(design.d_f_mm),
                        "--fidelity",
                        fidelity,
                        "--run-id",
                        run_id,
                        "--data-root",
                        root.as_posix(),
                        "--fidelity-table",
                        table_text,
                    ],
                    "cwd": str(repo_root()),
                    "env": {"RO_DATA_ROOT": root.as_posix()},
                }
            )
    return jobs


def write_queue(path: str | Path, jobs: Sequence[Mapping[str, Any]]) -> Path:
    """Write a queue file the runner can load. Does not start the queue."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = [dict(job) for job in jobs]
    destination.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def run_screen(
    designs: Sequence[PillarDesign],
    fidelity_table: Mapping[str, Any],
    *,
    run_id: str,
    data_root: str | Path,
    drivers: Drivers,
) -> dict[str, Any]:
    """Evaluate every design at LF and then HF. One call finishes before the next."""
    table = require_screen_table(fidelity_table)
    root = resolve_data_root(data_root)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for design in designs:
        low = evaluate(
            design,
            LF_LEVEL,
            run_id=run_id,
            data_root=root,
            fidelity_table=table,
            drivers=drivers,
        )
        high = evaluate(
            design,
            HF_LEVEL,
            run_id=run_id,
            data_root=root,
            fidelity_table=table,
            drivers=drivers,
        )
        pairs.append((low, high))
    return summarize(pairs)


def summarize(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
    """Per-design LF/HF rows plus the screen summary. Does not launch Fluent."""
    rows = [_pair_row(low, high) for low, high in pairs]
    usable = [row for row in rows if row["comparable"]]
    qoi_summary = {name: _qoi_summary(usable, name, larger, excess) for name, larger, excess in _QOI}
    cost_ratios = [
        float(row["cost_ratio"])
        for row in usable
        if _is_finite(row.get("cost_ratio"))
    ]
    return {
        "study": SCREEN_NAME,
        "n_designs": len(rows),
        "n_comparable": len(usable),
        "rows": rows,
        "qoi": qoi_summary,
        "cost_ratio": _cost_summary(cost_ratios),
        "note": (
            "Relative discrepancy is (LF - HF) / HF. "
            "cp_average is compared as CP-1. "
            "cost_ratio is LF wall time / HF wall time, where wall time is "
            "mesh_wall_time_s + solver_wall_time_s + extraction_wall_time_s."
        ),
    }


def summary_paths(out_dir: str | Path) -> dict[str, Path]:
    directory = Path(out_dir)
    return {
        "csv": directory / f"{SCREEN_NAME}.csv",
        "markdown": directory / f"{SCREEN_NAME}.md",
    }


def write_summary(out_dir: str | Path, report: Mapping[str, Any]) -> dict[str, Path]:
    paths = summary_paths(out_dir)
    paths["csv"].parent.mkdir(parents=True, exist_ok=True)
    _write_csv(paths["csv"], report)
    paths["markdown"].write_text(format_markdown(report), encoding="utf-8")
    return paths


def format_markdown(report: Mapping[str, Any]) -> str:
    rows = report["rows"]
    if not isinstance(rows, list):
        raise TypeError("screen report is missing rows.")
    lines = [
        "# 3D fidelity screen",
        "",
        str(report["note"]),
        "",
        f"designs={report['n_designs']} comparable={report['n_comparable']}",
        "",
        (
            "| geo_id | LF status | HF status | LMH LF | LMH HF | rel LMH "
            "| module LMH LF | module LMH HF | rel module "
            "| dP/L LF | dP/L HF | rel dP/L "
            "| CP LF | CP HF | CP-1 LF | CP-1 HF | rel CP-1 "
            "| wall LF | wall HF | cost ratio |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("screen row is not a mapping.")
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("geo_id")),
                    _md(row.get("status_lf")),
                    _md(row.get("status_hf")),
                    _fmt(row.get("lmh_lf")),
                    _fmt(row.get("lmh_hf")),
                    _fmt(row.get("lmh_rel")),
                    _fmt(row.get("lmh_module_area_lf")),
                    _fmt(row.get("lmh_module_area_hf")),
                    _fmt(row.get("lmh_module_area_rel")),
                    _fmt(row.get("pressure_drop_per_length_pa_per_m_lf")),
                    _fmt(row.get("pressure_drop_per_length_pa_per_m_hf")),
                    _fmt(row.get("pressure_drop_per_length_pa_per_m_rel")),
                    _fmt(row.get("cp_average_lf")),
                    _fmt(row.get("cp_average_hf")),
                    _fmt(row.get("cp_excess_lf")),
                    _fmt(row.get("cp_excess_hf")),
                    _fmt(row.get("cp_excess_rel")),
                    _fmt(row.get("wall_s_lf")),
                    _fmt(row.get("wall_s_hf")),
                    _fmt(row.get("cost_ratio")),
                ]
            )
            + " |"
        )
        if row.get("reason"):
            lines.append(f"reason `{row['geo_id']}`: {_md(row['reason'])}")
    lines.extend(["", "## Summary", ""])
    qoi = report["qoi"]
    if isinstance(qoi, Mapping):
        for name, _larger, excess in _QOI:
            summary = qoi.get(name)
            if not isinstance(summary, Mapping):
                continue
            label = f"{name} (CP-1)" if excess else name
            lines.append(
                f"- {label}: n={summary['n']} pearson={_fmt(summary['pearson'])} "
                f"spearman={_fmt(summary['spearman'])} "
                f"mean_signed_relative_lf_bias={_fmt(summary['mean_signed_relative_lf_bias'])} "
                f"rank_reversals={summary['rank_reversals']}/{summary['rank_comparisons']} "
                f"fraction={_fmt(summary['rank_fraction'])}"
            )
            if summary.get("reason"):
                lines.append(f"  {summary['reason']}")
    cost = report["cost_ratio"]
    if isinstance(cost, Mapping):
        lines.append(
            f"- cost_ratio: n={cost['n']} median={_fmt(cost['median'])} "
            f"min={_fmt(cost['min'])} max={_fmt(cost['max'])}"
        )
    lines.append("")
    return "\n".join(lines)


def _pair_row(low: Mapping[str, Any], high: Mapping[str, Any]) -> dict[str, Any]:
    problems = _identity_problems(low, high)
    both_valid = low.get("status") == "valid" and high.get("status") == "valid"
    comparable = not problems and both_valid
    if problems:
        reason = "; ".join(problems)
    elif not both_valid:
        reason = (
            "not both valid "
            f"(LF={low.get('status')!r}, HF={high.get('status')!r})"
        )
    else:
        reason = ""
    row: dict[str, Any] = {
        "geo_id": low.get("geo_id"),
        "d_p_mm": low.get("d_p_mm"),
        "d_h_mm": low.get("d_h_mm"),
        "d_f_mm": low.get("d_f_mm"),
        "run_id": low.get("run_id"),
        "mesh_id_lf": low.get("mesh_id"),
        "mesh_id_hf": high.get("mesh_id"),
        "status_lf": low.get("status"),
        "status_hf": high.get("status"),
        "failure_reason_lf": low.get("failure_reason"),
        "failure_reason_hf": high.get("failure_reason"),
        "comparable": comparable,
        "reason": reason,
    }
    for name, _larger, excess in _QOI:
        low_value = _qoi_value(low, name, excess=False)
        high_value = _qoi_value(high, name, excess=False)
        low_compared = _qoi_value(low, name, excess=excess)
        high_compared = _qoi_value(high, name, excess=excess)
        row[f"{name}_lf"] = low_value
        row[f"{name}_hf"] = high_value
        row[f"{name}_rel"] = _relative(low_compared, high_compared)
        if excess:
            row["cp_excess_lf"] = low_compared
            row["cp_excess_hf"] = high_compared
            row["cp_excess_rel"] = row[f"{name}_rel"]
    wall_low = _wall_time(low)
    wall_high = _wall_time(high)
    row["wall_s_lf"] = wall_low
    row["wall_s_hf"] = wall_high
    row["cost_ratio"] = _ratio(wall_low, wall_high) if comparable else None
    if not comparable:
        for name, _larger, excess in _QOI:
            row[f"{name}_rel"] = None
            if excess:
                row["cp_excess_rel"] = None
    return row


def _qoi_summary(
    rows: Sequence[Mapping[str, Any]],
    name: str,
    larger_is_better: bool,
    excess: bool,
) -> dict[str, Any]:
    compared = "cp_excess" if excess else name
    low_values: list[float] = []
    high_values: list[float] = []
    relatives: list[float] = []
    for row in rows:
        low = row.get(f"{compared}_lf")
        high = row.get(f"{compared}_hf")
        if not _is_finite(low) or not _is_finite(high):
            continue
        low_values.append(float(low))
        high_values.append(float(high))
        relative_key = "cp_excess_rel" if excess else f"{name}_rel"
        relative = row.get(relative_key)
        if _is_finite(relative):
            relatives.append(float(relative))
    summary: dict[str, Any] = {
        "n": len(low_values),
        "compared_as": "cp_average - 1" if excess else name,
        "larger_is_better": larger_is_better,
        "pearson": None,
        "spearman": None,
        "mean_signed_relative_lf_bias": _mean(relatives),
        "rank_reversals": 0,
        "rank_comparisons": 0,
        "rank_fraction": None,
        "reason": "",
    }
    if len(low_values) >= 2:
        rank = rank_reversal_count(
            low_values,
            high_values,
            larger_is_better=larger_is_better,
        )
        summary["rank_reversals"] = rank["reversals"]
        summary["rank_comparisons"] = rank["comparisons"]
        summary["rank_fraction"] = rank["fraction"]
    if len(low_values) < 3:
        summary["reason"] = "fewer than 3 comparable values; correlation not computed."
        return summary
    summary["pearson"] = pearson(low_values, high_values)
    summary["spearman"] = spearman(low_values, high_values)
    if summary["pearson"] is None:
        summary["reason"] = "correlation is undefined because one series has zero variance."
    return summary


def _identity_problems(low: Mapping[str, Any], high: Mapping[str, Any]) -> list[str]:
    problems = []
    for key in _IDENTITY:
        if key not in low or key not in high:
            problems.append(f"missing {key}.")
            continue
        if not _same(low[key], high[key]):
            problems.append(f"{key}: LF={low[key]!r} HF={high[key]!r}.")
    if low.get("fidelity") not in (None, LF_LEVEL):
        problems.append(f"LF record fidelity is {low.get('fidelity')!r}.")
    if high.get("fidelity") not in (None, HF_LEVEL):
        problems.append(f"HF record fidelity is {high.get('fidelity')!r}.")
    return problems


def _qoi_value(record: Mapping[str, Any], name: str, *, excess: bool) -> float | None:
    if name not in record:
        return None
    value = record[name]
    if not _is_finite(value):
        return None
    number = float(value)
    if excess:
        return number - 1.0
    return number


def _wall_time(record: Mapping[str, Any]) -> float | None:
    parts = []
    for key in _WALL_KEYS:
        value = record.get(key)
        if not _is_finite(value):
            return None
        parts.append(float(value))
    return sum(parts)


def _cost_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _relative(low: float | None, high: float | None) -> float | None:
    if low is None or high is None or high == 0.0:
        return None
    return (low - high) / high


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0.0:
        return None
    return numerator / denominator


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.fmean(values)


def _same(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _is_finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return number == number and number not in (float("inf"), float("-inf"))


def _queue_path(value: str | Path) -> str:
    """Path text for a queue argv entry. Windows absolute paths stay as given."""
    text = str(value).strip().replace("\\", "/")
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        return text
    path = Path(value)
    if not path.is_absolute():
        path = path.resolve()
    return path.as_posix()


def _cli_number(value: float) -> str:
    return format(float(value), ".12g")


def _fmt(value: object) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{float(value):.6g}"
    return str(value)


def _md(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/")


def _write_csv(path: Path, report: Mapping[str, Any]) -> None:
    rows = report.get("rows")
    qoi = report.get("qoi")
    fieldnames = [
        "row_type",
        "geo_id",
        "d_p_mm",
        "d_h_mm",
        "d_f_mm",
        "run_id",
        "mesh_id_lf",
        "mesh_id_hf",
        "status_lf",
        "status_hf",
        "failure_reason_lf",
        "failure_reason_hf",
        "comparable",
        "reason",
        "lmh_lf",
        "lmh_hf",
        "lmh_rel",
        "lmh_module_area_lf",
        "lmh_module_area_hf",
        "lmh_module_area_rel",
        "pressure_drop_per_length_pa_per_m_lf",
        "pressure_drop_per_length_pa_per_m_hf",
        "pressure_drop_per_length_pa_per_m_rel",
        "cp_average_lf",
        "cp_average_hf",
        "cp_excess_lf",
        "cp_excess_hf",
        "cp_excess_rel",
        "wall_s_lf",
        "wall_s_hf",
        "cost_ratio",
        "summary_qoi",
        "n",
        "pearson",
        "spearman",
        "mean_signed_relative_lf_bias",
        "rank_reversals",
        "rank_comparisons",
        "rank_fraction",
        "cost_n",
        "cost_median",
        "cost_min",
        "cost_max",
    ]
    body: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                item = dict(row)
                item["row_type"] = "design"
                body.append(item)
    if isinstance(qoi, Mapping):
        for name, _larger, _excess in _QOI:
            summary = qoi.get(name)
            if not isinstance(summary, Mapping):
                continue
            body.append(
                {
                    "row_type": "summary",
                    "summary_qoi": name,
                    "n": summary.get("n"),
                    "pearson": summary.get("pearson"),
                    "spearman": summary.get("spearman"),
                    "mean_signed_relative_lf_bias": summary.get(
                        "mean_signed_relative_lf_bias"
                    ),
                    "rank_reversals": summary.get("rank_reversals"),
                    "rank_comparisons": summary.get("rank_comparisons"),
                    "rank_fraction": summary.get("rank_fraction"),
                    "reason": summary.get("reason"),
                }
            )
    cost = report.get("cost_ratio")
    if isinstance(cost, Mapping):
        body.append(
            {
                "row_type": "summary",
                "summary_qoi": "cost_ratio",
                "cost_n": cost.get("n"),
                "cost_median": cost.get("median"),
                "cost_min": cost.get("min"),
                "cost_max": cost.get("max"),
            }
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in body:
            writer.writerow({name: "" if row.get(name) is None else row.get(name) for name in fieldnames})
