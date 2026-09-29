"""Paired medium / very_fine screening. Not an optimization domain.

The 3-pitch ladder at d = 0.40 mm and L = 4.0 mm is one point. This
module lists a 3 by 3 grid around that point and compares the medium
mesh with the very_fine mesh. It does not launch Fluent, assign LF/HF,
or accept a low-fidelity model.

Channel height stays the campaign value, 0.77 mm. The only geometric
rules in ``PilotConfig`` are ``d < H`` and ``L > d``. The grid below is
narrower than those rules:

- d = 0.30, 0.40, 0.50 mm. The largest filament leaves a 0.135 mm gap
  to each membrane (blockage 0.65). That is not a near-blockage gap.
  The smallest filament is still about 19 medium cells across.
- L = 3.0, 4.0, 5.0 mm. The tightest corner is L / d = 6. The O-grid
  streamwise block is still set by the channel half-height, not by
  neighboring circles.
- The center cell is the solved ladder point.

``fine`` is omitted. It was the intermediate ladder diagnostic.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Mapping

from ro_2d_pilot.config import (
    MESH_LEVEL_MEDIUM,
    MESH_LEVEL_VERY_FINE,
    OperatingPoint,
    PilotConfig,
)
from ro_2d_pilot.geometry import geo_id_for
from ro_2d_pilot.mesh import resolution_layout
from ro_2d_pilot.paths import data_root, run_dir

# Nanometre grid, matching geometry.length_token.
_NM = 1e-9
SCREEN_D_M = (300_000 * _NM, 400_000 * _NM, 500_000 * _NM)
SCREEN_L_M = (3_000_000 * _NM, 4_000_000 * _NM, 5_000_000 * _NM)
SCREEN_N_PITCHES = 3
PAIR_LEVELS = (MESH_LEVEL_MEDIUM, MESH_LEVEL_VERY_FINE)
LEVEL_MEDIUM = MESH_LEVEL_MEDIUM
LEVEL_VERY_FINE = MESH_LEVEL_VERY_FINE
STUDY_NAME = "medium_very_fine_screening"
SUMMARY_DIR_NAME = "studies/fidelity_pairs"

# Larger LMH is better. Smaller CP excess and dP/L are better.
QOI_LARGER_IS_BETTER = {
    "lmh": True,
    "cp_excess": False,
    "pressure_drop_per_length_pa_per_m": False,
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
_CONVERGENCE_FIELDS = (
    "validity",
    "validity_reason",
    "convergence_status",
    "source_ramp_final",
    "full_source_reached",
    "full_source_iterations",
    "total_iterations",
    "solver_iterations",
)


@dataclass(frozen=True)
class ScreenGeometry:
    pair_id: str
    d_m: float
    L_m: float
    geo_id: str


def screening_operating() -> OperatingPoint:
    """Fixed pilot condition: 0.2 m/s and 6 MPa."""
    return OperatingPoint()


def screening_geometries() -> tuple[ScreenGeometry, ...]:
    """Nine points, d varying slowest, then L. Center is d=0.40 mm, L=4.0 mm."""
    operating = screening_operating()
    points: list[ScreenGeometry] = []
    for d_m in SCREEN_D_M:
        for L_m in SCREEN_L_M:
            config = PilotConfig(
                d_m=d_m,
                L_m=L_m,
                fidelity=LEVEL_MEDIUM,
                operating=operating,
                n_pitches=SCREEN_N_PITCHES,
            )
            very_fine = PilotConfig(
                d_m=d_m,
                L_m=L_m,
                fidelity=LEVEL_VERY_FINE,
                operating=operating,
                n_pitches=SCREEN_N_PITCHES,
            )
            for level_config in (config, very_fine):
                layout = resolution_layout(level_config)
                for key in ("n_side", "n_radial", "n_gap", "n_stream"):
                    count = int(layout[key])
                    if count < 2:
                        raise ValueError(
                            f"{level_config.mesh_level} {key}={count} at "
                            f"d_m={d_m!r} L_m={L_m!r}."
                        )
            points.append(
                ScreenGeometry(
                    pair_id=_pair_id(d_m, L_m),
                    d_m=d_m,
                    L_m=L_m,
                    geo_id=geo_id_for(config),
                )
            )
    return tuple(points)


def result_path(root: Path, geometry: ScreenGeometry, level: str) -> Path:
    if level not in PAIR_LEVELS:
        raise ValueError(f"pair level must be one of {PAIR_LEVELS}, got {level!r}.")
    run_id = screening_operating().run_id
    return run_dir(root, geometry.geo_id, level, run_id) / "result.json"


def summary_paths(root: Path) -> dict[str, Path]:
    directory = root / "studies" / "fidelity_pairs"
    return {
        "json": directory / "medium_very_fine_summary.json",
        "csv": directory / "medium_very_fine_summary.csv",
    }


def plan_rows(root: Path) -> list[dict[str, object]]:
    """One row per geometry. Does not launch Fluent."""
    rows: list[dict[str, object]] = []
    for geometry in screening_geometries():
        medium_path = result_path(root, geometry, LEVEL_MEDIUM)
        very_fine_path = result_path(root, geometry, LEVEL_VERY_FINE)
        medium_record, medium_validity = _read_record(medium_path)
        very_fine_record, very_fine_validity = _read_record(very_fine_path)
        rows.append(
            {
                "pair_id": geometry.pair_id,
                "d_m": geometry.d_m,
                "L_m": geometry.L_m,
                "geo_id": geometry.geo_id,
                "medium_result": str(medium_path),
                "very_fine_result": str(very_fine_path),
                "medium_exists": medium_path.is_file(),
                "very_fine_exists": very_fine_path.is_file(),
                "medium_validity": medium_validity,
                "very_fine_validity": very_fine_validity,
                "medium_record": medium_record,
                "very_fine_record": very_fine_record,
            }
        )
    return rows


def missing_run_commands(rows: list[Mapping[str, object]]) -> list[str]:
    """Git Bash commands for levels that are not yet validity=valid.

    A missing file and an invalid file both get a command. A valid file
    does not. Commands are one case at a time, medium before very_fine.
    """
    commands: list[str] = []
    for row in rows:
        d_m = float(row["d_m"])
        L_m = float(row["L_m"])
        for level, validity_key in (
            (LEVEL_MEDIUM, "medium_validity"),
            (LEVEL_VERY_FINE, "very_fine_validity"),
        ):
            if row.get(validity_key) == "valid":
                continue
            state = "missing" if row.get(validity_key) is None else str(row.get(validity_key))
            commands.append(
                f"# {row['pair_id']} {level} ({state})\n"
                + _run_command(d_m, L_m, level)
            )
    return commands


def format_plan(rows: list[Mapping[str, object]]) -> str:
    lines = [
        "2D medium/very_fine screening",
        f"geometries={len(rows)} fidelities={PAIR_LEVELS[0]},{PAIR_LEVELS[1]}",
        f"expected_runs={len(rows) * len(PAIR_LEVELS)}",
        "fine is not in this study.",
        "n_pitches=3  u=0.2 m/s  pressure=6 MPa",
        "",
        (
            f"{'pair_id':<14}{'d_mm':>8}{'L_mm':>8}{'medium':>12}{'very_fine':>12}"
        ),
    ]
    for row in rows:
        lines.append(
            f"{row['pair_id']:<14}"
            f"{float(row['d_m']) * 1e3:8.2f}"
            f"{float(row['L_m']) * 1e3:8.2f}"
            f"{_validity_cell(row.get('medium_exists'), row.get('medium_validity')):>12}"
            f"{_validity_cell(row.get('very_fine_exists'), row.get('very_fine_validity')):>12}"
        )
        lines.append(f"  geo_id={row['geo_id']}")
        lines.append(f"  medium: {row['medium_result']}")
        lines.append(f"  very_fine: {row['very_fine_result']}")
    commands = missing_run_commands(rows)
    lines.append("")
    if commands:
        lines.append("Commands for runs that are not yet valid, one case at a time:")
        lines.extend(commands)
    else:
        lines.append("No missing runs. Every medium and very_fine result is valid.")
    lines.append("This script does not launch Fluent.")
    return "\n".join(lines)


def analyze_rows(rows: list[Mapping[str, object]]) -> dict[str, object]:
    """Compare completed pairs. Does not launch Fluent or accept an LF model."""
    pair_reports: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for row in rows:
        report, reason = _pair_report(row)
        if report is None:
            skipped.append(
                {
                    "pair_id": row["pair_id"],
                    "geo_id": row["geo_id"],
                    "d_m": row["d_m"],
                    "L_m": row["L_m"],
                    "reason": reason,
                    "medium_validity": row.get("medium_validity"),
                    "very_fine_validity": row.get("very_fine_validity"),
                }
            )
            continue
        pair_reports.append(report)
    usable = [item for item in pair_reports if item["comparable"]]
    correlations = {
        name: _qoi_summary(usable, name) for name in QOI_LARGER_IS_BETTER
    }
    rank = {
        name: _rank_reversals(usable, name, QOI_LARGER_IS_BETTER[name])
        for name in QOI_LARGER_IS_BETTER
    }
    return {
        "study": STUDY_NAME,
        "n_geometries": len(rows),
        "n_comparable_pairs": len(usable),
        "pairs": pair_reports,
        "skipped": skipped,
        "correlation": correlations,
        "rank_reversals": rank,
        "solver_cost": _cost_summary(usable, "solver_cost_ratio"),
        "total_wall_cost": _cost_summary(usable, "total_wall_cost_ratio"),
        "decision": "not_made",
        "note": (
            "Qualification screen only. No low-fidelity acceptance decision "
            "is made."
        ),
    }


def format_analysis(report: Mapping[str, object]) -> str:
    pairs = report["pairs"]
    if not isinstance(pairs, list):
        raise TypeError("analysis report is missing pairs.")
    lines = [
        "2D medium vs very_fine",
        f"comparable_pairs={report['n_comparable_pairs']}",
        str(report["note"]),
        "",
        (
            f"{'pair_id':<14}{'d_mm':>8}{'L_mm':>8}"
            f"{'LMH_m':>12}{'LMH_vf':>12}{'rel_LMH':>12}"
            f"{'CPex_m':>12}{'CPex_vf':>12}{'rel_CPex':>12}"
            f"{'dP/L_m':>12}{'dP/L_vf':>12}{'rel_dP/L':>12}"
        ),
    ]
    for pair in pairs:
        if not isinstance(pair, Mapping):
            raise TypeError("pair report is not a mapping.")
        relative = pair["relative_discrepancy"]
        if not isinstance(relative, Mapping):
            raise TypeError("relative discrepancy is not a mapping.")
        lines.append(
            f"{pair['pair_id']:<14}"
            f"{float(pair['d_m']) * 1e3:8.2f}"
            f"{float(pair['L_m']) * 1e3:8.2f}"
            f"{_fmt(pair['lmh_medium']):>12}{_fmt(pair['lmh_very_fine']):>12}"
            f"{_fmt(relative.get('lmh')):>12}"
            f"{_fmt(pair['cp_excess_medium']):>12}"
            f"{_fmt(pair['cp_excess_very_fine']):>12}"
            f"{_fmt(relative.get('cp_excess')):>12}"
            f"{_fmt(pair['dp_per_l_medium']):>12}"
            f"{_fmt(pair['dp_per_l_very_fine']):>12}"
            f"{_fmt(relative.get('pressure_drop_per_length_pa_per_m')):>12}"
        )
    lines.append("")
    lines.append(
        f"{'pair_id':<14}{'CP_m':>12}{'CP_vf':>12}{'rel_CP':>12}"
        f"{'solve_m':>12}{'solve_vf':>12}{'solve_ratio':>12}{'speedup':>12}"
        f"{'wall_ratio':>12}"
    )
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        relative = pair["relative_discrepancy"]
        if not isinstance(relative, Mapping):
            continue
        lines.append(
            f"{pair['pair_id']:<14}"
            f"{_fmt(pair['cp_medium']):>12}{_fmt(pair['cp_very_fine']):>12}"
            f"{_fmt(relative.get('cp_average')):>12}"
            f"{_fmt(pair['solver_time_medium']):>12}"
            f"{_fmt(pair['solver_time_very_fine']):>12}"
            f"{_fmt(pair['solver_cost_ratio']):>12}"
            f"{_fmt(pair['solver_speedup']):>12}"
            f"{_fmt(pair['total_wall_cost_ratio']):>12}"
        )
    lines.append("")
    lines.append(
        "Relative discrepancy is (medium - very_fine) / very_fine. "
        "CP excess is CP - 1 and is the CP discrepancy used below."
    )
    lines.append("solver_cost_ratio = medium solver time / very_fine solver time.")
    lines.append("solver_speedup = very_fine solver time / medium solver time.")
    lines.append(
        "total_wall_cost_ratio includes startup and extraction. "
        "It is not the solver cost."
    )
    correlations = report["correlation"]
    reversals = report["rank_reversals"]
    if isinstance(correlations, Mapping):
        lines.append("")
        lines.append("Correlation on comparable pairs. Not a significance test.")
        for name, summary in correlations.items():
            if not isinstance(summary, Mapping):
                continue
            lines.append(
                f"  {name}: n={summary['n']} pearson={_fmt(summary['pearson'])} "
                f"spearman={_fmt(summary['spearman'])} "
                f"mean_signed_rel={_fmt(summary['mean_signed_relative_discrepancy'])} "
                f"mean_abs_rel={_fmt(summary['mean_absolute_relative_discrepancy'])} "
                f"max_abs_rel={_fmt(summary['max_absolute_relative_discrepancy'])}"
            )
            if summary.get("reason"):
                lines.append(f"    {summary['reason']}")
    if isinstance(reversals, Mapping):
        lines.append("")
        lines.append(
            "Rank reversals: medium and very_fine disagree on which geometry is better."
        )
        lines.append("LMH: larger is better. CP excess and dP/L: smaller is better.")
        for name, summary in reversals.items():
            if not isinstance(summary, Mapping):
                continue
            lines.append(
                f"  {name}: reversals={summary['reversals']} "
                f"comparisons={summary['comparisons']} "
                f"fraction={_fmt(summary['fraction'])}"
            )
    for label, key in (
        ("solver_cost_ratio", "solver_cost"),
        ("total_wall_cost_ratio", "total_wall_cost"),
    ):
        summary = report.get(key)
        if not isinstance(summary, Mapping):
            continue
        lines.append(
            f"{label}: n={summary['n']} median={_fmt(summary['median'])} "
            f"min={_fmt(summary['min'])} max={_fmt(summary['max'])}"
        )
    skipped = report["skipped"]
    if isinstance(skipped, list) and skipped:
        lines.append("")
        lines.append("Skipped geometries:")
        for item in skipped:
            if isinstance(item, Mapping):
                lines.append(f"  {item['pair_id']}: {item['reason']}")
    return "\n".join(lines)


def write_summary(root: Path, report: Mapping[str, object]) -> dict[str, Path]:
    paths = summary_paths(root)
    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    payload = _jsonable(report)
    paths["json"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(paths["csv"], report)
    return paths


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation. ``None`` when either series has zero variance."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("pearson needs two equal series of length >= 2.")
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    norm_x = math.sqrt(sum(value * value for value in dx))
    norm_y = math.sqrt(sum(value * value for value in dy))
    if norm_x == 0.0 or norm_y == 0.0:
        return None
    return sum(left * right for left, right in zip(dx, dy)) / (norm_x * norm_y)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman correlation via Pearson on average ranks."""
    return pearson(average_ranks(xs), average_ranks(ys))


def average_ranks(values: list[float]) -> list[float]:
    """1-based average ranks. Ties share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start
        while (
            stop + 1 < len(order)
            and values[order[stop + 1]] == values[order[start]]
        ):
            stop += 1
        rank = 0.5 * ((start + 1) + (stop + 1))
        for index in order[start : stop + 1]:
            ranks[index] = rank
        start = stop + 1
    return ranks


def rank_reversal_count(
    medium: list[float],
    very_fine: list[float],
    *,
    larger_is_better: bool,
) -> dict[str, object]:
    """Count strict order disagreements over unordered geometry pairs."""
    if len(medium) != len(very_fine):
        raise ValueError("rank comparison series must have the same length.")
    reversals = 0
    comparisons = 0
    for left, right in combinations(range(len(medium)), 2):
        comparisons += 1
        medium_sign = _preference(
            medium[left], medium[right], larger_is_better=larger_is_better
        )
        very_fine_sign = _preference(
            very_fine[left], very_fine[right], larger_is_better=larger_is_better
        )
        if medium_sign != 0 and very_fine_sign != 0 and medium_sign != very_fine_sign:
            reversals += 1
    fraction = None if comparisons == 0 else reversals / comparisons
    return {
        "reversals": reversals,
        "comparisons": comparisons,
        "fraction": fraction,
    }


def _pair_id(d_m: float, L_m: float) -> str:
    return f"d{d_m * 1e3:.2f}_L{L_m * 1e3:.2f}".replace(".", "p")


def _run_command(d_m: float, L_m: float, level: str) -> str:
    operating = screening_operating()
    return (
        "python scripts/run_ro_2d_case.py "
        f"--d-m {d_m:.12g} --l-m {L_m:.12g} "
        f"--n-pitches {SCREEN_N_PITCHES} "
        f"--u-ms {operating.inlet_velocity_m_s:.12g} "
        f"--pressure-pa {operating.outlet_gauge_pressure_pa:.12g} "
        f"--mesh-level {level}"
    )


def _read_record(path: Path) -> tuple[dict[str, object] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, "unreadable:not_object"
    validity = payload.get("validity")
    if not isinstance(validity, str):
        return payload, "missing"
    return payload, validity


def _validity_cell(exists: object, validity: object) -> str:
    if not exists:
        return "missing"
    if validity is None:
        return "missing"
    return str(validity)


def _pair_report(
    row: Mapping[str, object],
) -> tuple[dict[str, object] | None, str]:
    medium = row.get("medium_record")
    very_fine = row.get("very_fine_record")
    if not isinstance(medium, Mapping) or not isinstance(very_fine, Mapping):
        missing = []
        if not isinstance(medium, Mapping):
            missing.append("medium")
        if not isinstance(very_fine, Mapping):
            missing.append("very_fine")
        return None, "missing " + ",".join(missing)
    problems = _identity_problems(medium, very_fine)
    if problems:
        return None, "; ".join(problems)
    if medium.get("validity") != "valid" or very_fine.get("validity") != "valid":
        return None, (
            "not both valid "
            f"(medium={medium.get('validity')!r}, "
            f"very_fine={very_fine.get('validity')!r})"
        )
    lmh_m = _optional_finite(medium, "lmh")
    lmh_v = _optional_finite(very_fine, "lmh")
    cp_m = _optional_finite(medium, "cp_average")
    cp_v = _optional_finite(very_fine, "cp_average")
    dp_m = _optional_finite(medium, "pressure_drop_per_length_pa_per_m")
    dp_v = _optional_finite(very_fine, "pressure_drop_per_length_pa_per_m")
    solve_m = _optional_finite(medium, "solver_wall_time_s")
    solve_v = _optional_finite(very_fine, "solver_wall_time_s")
    wall_m = _optional_finite(medium, "total_wall_time_s")
    wall_v = _optional_finite(very_fine, "total_wall_time_s")
    cpex_m = None if cp_m is None else cp_m - 1.0
    cpex_v = None if cp_v is None else cp_v - 1.0
    report = {
        "pair_id": row["pair_id"],
        "d_m": row["d_m"],
        "L_m": row["L_m"],
        "geo_id": row["geo_id"],
        "medium_validity": row.get("medium_validity"),
        "very_fine_validity": row.get("very_fine_validity"),
        "medium_convergence": _convergence(medium),
        "very_fine_convergence": _convergence(very_fine),
        "comparable": True,
        "reason": "",
        "lmh_medium": lmh_m,
        "lmh_very_fine": lmh_v,
        "cp_medium": cp_m,
        "cp_very_fine": cp_v,
        "cp_excess_medium": cpex_m,
        "cp_excess_very_fine": cpex_v,
        "dp_per_l_medium": dp_m,
        "dp_per_l_very_fine": dp_v,
        "solver_time_medium": solve_m,
        "solver_time_very_fine": solve_v,
        "total_wall_time_medium": wall_m,
        "total_wall_time_very_fine": wall_v,
        "solver_cost_ratio": _ratio(solve_m, solve_v),
        "solver_speedup": _ratio(solve_v, solve_m),
        "total_wall_cost_ratio": _ratio(wall_m, wall_v),
        "total_wall_speedup": _ratio(wall_v, wall_m),
        "relative_discrepancy": {
            "lmh": _relative(lmh_m, lmh_v),
            "cp_average": _relative(cp_m, cp_v),
            "cp_excess": _relative(cpex_m, cpex_v),
            "pressure_drop_per_length_pa_per_m": _relative(dp_m, dp_v),
        },
    }
    return report, ""


def _identity_problems(
    medium: Mapping[str, object],
    very_fine: Mapping[str, object],
) -> list[str]:
    problems: list[str] = []
    if medium.get("mesh_level") not in (None, LEVEL_MEDIUM):
        problems.append(
            f"medium record mesh level is {medium.get('mesh_level')!r}."
        )
    if medium.get("fidelity") not in (None, LEVEL_MEDIUM):
        problems.append(
            f"medium record fidelity is {medium.get('fidelity')!r}."
        )
    if very_fine.get("mesh_level") not in (None, LEVEL_VERY_FINE):
        problems.append(
            f"very_fine record mesh level is {very_fine.get('mesh_level')!r}."
        )
    if very_fine.get("fidelity") not in (None, LEVEL_VERY_FINE):
        problems.append(
            f"very_fine record fidelity is {very_fine.get('fidelity')!r}."
        )
    for key in _IDENTITY_FIELDS:
        if key not in medium or key not in very_fine:
            problems.append(f"missing {key}.")
            continue
        if not _same(medium[key], very_fine[key]):
            problems.append(f"{key}: medium={medium[key]!r} very_fine={very_fine[key]!r}.")
    return problems


def _convergence(record: Mapping[str, object]) -> dict[str, object]:
    return {key: record.get(key) for key in _CONVERGENCE_FIELDS}


def _qoi_summary(pairs: list[Mapping[str, object]], name: str) -> dict[str, object]:
    medium_key, very_fine_key, relative_key = _qoi_keys(name)
    medium_values: list[float] = []
    very_fine_values: list[float] = []
    relatives: list[float] = []
    for pair in pairs:
        medium = pair.get(medium_key)
        very_fine = pair.get(very_fine_key)
        if not _is_finite_number(medium) or not _is_finite_number(very_fine):
            continue
        medium_values.append(float(medium))
        very_fine_values.append(float(very_fine))
        relative = pair["relative_discrepancy"]
        if isinstance(relative, Mapping):
            value = relative.get(relative_key)
            if _is_finite_number(value):
                relatives.append(float(value))
    summary: dict[str, object] = {
        "n": len(medium_values),
        "pearson": None,
        "spearman": None,
        "mean_signed_relative_discrepancy": _mean(relatives),
        "mean_absolute_relative_discrepancy": _mean([abs(value) for value in relatives]),
        "max_absolute_relative_discrepancy": (
            None if not relatives else max(abs(value) for value in relatives)
        ),
        "reason": "",
    }
    if len(medium_values) < 3:
        summary["reason"] = "fewer than 3 comparable values; correlation not computed."
        return summary
    summary["pearson"] = pearson(medium_values, very_fine_values)
    summary["spearman"] = spearman(medium_values, very_fine_values)
    if summary["pearson"] is None:
        summary["reason"] = "correlation is undefined because one series has zero variance."
    return summary


def _rank_reversals(
    pairs: list[Mapping[str, object]],
    name: str,
    larger_is_better: bool,
) -> dict[str, object]:
    medium_key, very_fine_key, _relative_key = _qoi_keys(name)
    medium_values: list[float] = []
    very_fine_values: list[float] = []
    for pair in pairs:
        medium = pair.get(medium_key)
        very_fine = pair.get(very_fine_key)
        if not _is_finite_number(medium) or not _is_finite_number(very_fine):
            continue
        medium_values.append(float(medium))
        very_fine_values.append(float(very_fine))
    if len(medium_values) < 2:
        return {"reversals": 0, "comparisons": 0, "fraction": None}
    return rank_reversal_count(
        medium_values,
        very_fine_values,
        larger_is_better=larger_is_better,
    )


def _qoi_keys(name: str) -> tuple[str, str, str]:
    if name == "lmh":
        return "lmh_medium", "lmh_very_fine", "lmh"
    if name == "cp_excess":
        return "cp_excess_medium", "cp_excess_very_fine", "cp_excess"
    if name == "pressure_drop_per_length_pa_per_m":
        return (
            "dp_per_l_medium",
            "dp_per_l_very_fine",
            "pressure_drop_per_length_pa_per_m",
        )
    raise KeyError(name)


def _cost_summary(pairs: list[Mapping[str, object]], key: str) -> dict[str, object]:
    values = [
        float(pair[key])
        for pair in pairs
        if _is_finite_number(pair.get(key))
    ]
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _preference(left: float, right: float, *, larger_is_better: bool) -> int:
    if left == right:
        return 0
    if larger_is_better:
        return 1 if left > right else -1
    return 1 if left < right else -1


def _optional_finite(record: Mapping[str, object], key: str) -> float | None:
    if key not in record:
        return None
    value = record[key]
    if not _is_finite_number(value):
        return None
    return float(value)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _relative(medium: float | None, very_fine: float | None) -> float | None:
    if medium is None or very_fine is None or very_fine == 0.0:
        return None
    return (medium - very_fine) / very_fine


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


def _fmt(value: object) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{float(value):.6g}"
    return str(value)


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_csv(path: Path, report: Mapping[str, object]) -> None:
    pairs = report.get("pairs")
    skipped = report.get("skipped")
    rows: list[dict[str, object]] = []
    if isinstance(pairs, list):
        for pair in pairs:
            if isinstance(pair, Mapping):
                rows.append(dict(pair))
    if isinstance(skipped, list):
        for item in skipped:
            if isinstance(item, Mapping):
                rows.append(
                    {
                        "pair_id": item.get("pair_id"),
                        "d_m": item.get("d_m"),
                        "L_m": item.get("L_m"),
                        "geo_id": item.get("geo_id"),
                        "comparable": False,
                        "reason": item.get("reason"),
                        "medium_validity": item.get("medium_validity"),
                        "very_fine_validity": item.get("very_fine_validity"),
                    }
                )
    fieldnames = [
        "pair_id",
        "d_m",
        "L_m",
        "geo_id",
        "comparable",
        "reason",
        "medium_validity",
        "very_fine_validity",
        "lmh_medium",
        "lmh_very_fine",
        "cp_medium",
        "cp_very_fine",
        "cp_excess_medium",
        "cp_excess_very_fine",
        "dp_per_l_medium",
        "dp_per_l_very_fine",
        "solver_time_medium",
        "solver_time_very_fine",
        "solver_cost_ratio",
        "solver_speedup",
        "total_wall_time_medium",
        "total_wall_time_very_fine",
        "total_wall_cost_ratio",
        "total_wall_speedup",
        "relative_lmh",
        "relative_cp_average",
        "relative_cp_excess",
        "relative_dp_per_l",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            relative = row.get("relative_discrepancy")
            flat = dict(row)
            if isinstance(relative, Mapping):
                flat["relative_lmh"] = relative.get("lmh")
                flat["relative_cp_average"] = relative.get("cp_average")
                flat["relative_cp_excess"] = relative.get("cp_excess")
                flat["relative_dp_per_l"] = relative.get(
                    "pressure_drop_per_length_pa_per_m"
                )
            writer.writerow({name: flat.get(name, "") for name in fieldnames})


def load_plan(root: Path | None = None) -> list[dict[str, object]]:
    destination = data_root() if root is None else root
    return plan_rows(destination)
