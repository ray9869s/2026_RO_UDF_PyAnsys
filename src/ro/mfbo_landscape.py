"""HF and LF response surfaces for the 3D pillar screen. No Fluent.

The fit is the high-fidelity branch of the loop's autoregressive model:
an RBF Gaussian process on the valid points of one level. Invalid points
are left out. Coordinates are scaled to the design box the same way
``ro.mfbo_3d`` scales them. Probability of feasibility is the product of
one Gaussian tail per constraint.
"""

from __future__ import annotations

import csv
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ro.mfbo_3d import geometry_block_reason
from ro_2d_pilot.optimization.acquisition import feasibility_probability, stack_xy
from ro_2d_pilot.optimization.gp import AutoregressiveModel

SWEEP_CP_EXCESS = (0.039, 0.040, 0.041)
SWEEP_DP_PA_PER_M = (69000.0, 71000.0, 73000.0)
_GRID_P = 81
_GRID_H = 61
_LEVELS = ("LF", "HF")
_QOI = ("lmh", "dp", "cp_excess")


@dataclass(frozen=True)
class Box:
    d_p_min: float
    d_p_max: float
    d_h_min: float
    d_h_max: float
    d_f_mm: float = 0.40
    m_min_mm: float = 0.006


@dataclass(frozen=True)
class LevelObs:
    status: str
    lmh: float | None
    dp_per_l: float | None
    cp_average: float | None
    wall_s: float | None
    iterations_run: float | None = None
    cell_count: float | None = None

    @property
    def cp_excess(self) -> float | None:
        if self.cp_average is None:
            return None
        return self.cp_average - 1.0

    @property
    def trains(self) -> bool:
        return (
            self.status == "valid"
            and self.lmh is not None
            and self.dp_per_l is not None
            and self.cp_average is not None
        )


@dataclass(frozen=True)
class ScreenDesign:
    d_p_mm: float
    d_h_mm: float
    d_f_mm: float
    low: LevelObs
    high: LevelObs
    cost_ratio: float | None = None
    cost_proxy_ratio: float | None = None


def read_screen_rows(path: Path) -> list[ScreenDesign]:
    """Design rows of a v1 screen CSV. Summary rows are skipped."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "d_p_mm" not in reader.fieldnames:
            raise ValueError(f"Screen CSV is missing d_p_mm: {path}")
        designs = []
        for index, row in enumerate(reader):
            if (row.get("row_type") or "design") != "design":
                continue
            if not (row.get("d_p_mm") or "").strip():
                continue
            designs.append(_design_from_csv_row(row, index))
    if not designs:
        raise ValueError(f"Screen CSV has no design rows: {path}")
    return designs


def cost_medians(designs: Sequence[ScreenDesign]) -> dict[str, Any]:
    """Median wall seconds per level, and the two screen cost ratios.

    Wall seconds are mesh plus solver plus extraction. A finite wall time
    is included whether or not that level's solve was valid. The wall-time
    ratio and the cell-times-iteration ratio use rows where both levels
    recorded a positive value.
    """
    hf_wall = [item.high.wall_s for item in designs if _positive_number(item.high.wall_s)]
    lf_wall = [item.low.wall_s for item in designs if _positive_number(item.low.wall_s)]
    ratios = []
    proxies = []
    for item in designs:
        ratio = item.cost_ratio
        if not _positive_number(ratio) and _positive_number(item.low.wall_s) and _positive_number(item.high.wall_s):
            ratio = float(item.low.wall_s) / float(item.high.wall_s)
        if _positive_number(ratio):
            ratios.append(float(ratio))
        proxy = item.cost_proxy_ratio
        if not _positive_number(proxy):
            proxy = _proxy_ratio(item)
        if _positive_number(proxy):
            proxies.append(float(proxy))
    if not hf_wall:
        raise ValueError("Screen has no positive HF wall time.")
    return {
        "hf_wall_median_s": float(statistics.median(hf_wall)),
        "lf_wall_median_s": float(statistics.median(lf_wall)) if lf_wall else None,
        "wall_cost_ratio_median": float(statistics.median(ratios)) if ratios else None,
        "cost_proxy_ratio_median": float(statistics.median(proxies)) if proxies else None,
        "n_hf_wall": len(hf_wall),
        "n_lf_wall": len(lf_wall),
        "n_wall_ratio": len(ratios),
        "n_proxy_ratio": len(proxies),
    }


def fit_level(designs: Sequence[ScreenDesign], level: str, box: Box) -> dict[str, Any]:
    """Fit LMH, dP/L, and CP excess at one level. Valid points only."""
    if level not in _LEVELS:
        raise ValueError(f"level must be one of {_LEVELS}, got {level!r}.")
    rows = []
    for design in designs:
        side = design.high if level == "HF" else design.low
        if side.trains:
            rows.append((design, side))
    if not rows:
        raise ValueError(f"No valid {level} points to fit.")
    x = stack_xy([_xy(design.d_p_mm, design.d_h_mm, box) for design, _side in rows])

    def fit(values: list[float]) -> AutoregressiveModel:
        model = AutoregressiveModel()
        model.fit(
            np.zeros((0, 2)),
            np.zeros((0,)),
            x,
            np.asarray(values, dtype=float),
        )
        return model

    return {
        "level": level,
        "n": len(rows),
        "points": tuple((design.d_p_mm, design.d_h_mm) for design, _side in rows),
        "lmh": fit([float(side.lmh) for _design, side in rows]),
        "dp": fit([float(side.dp_per_l) for _design, side in rows]),
        "cp_excess": fit([float(side.cp_excess) for _design, side in rows]),
    }


def analyze(
    designs: Sequence[ScreenDesign],
    box: Box,
    *,
    cp_excess_max: float,
    dp_max_pa_per_m: float,
) -> dict[str, Any]:
    """HF constrained optimum, feasible fraction, and PoF on the screen points."""
    fitted = {level: fit_level(designs, level, box) for level in _LEVELS if _has_valid(designs, level)}
    if "HF" not in fitted:
        raise ValueError("No valid HF points to fit.")
    primary = _at_thresholds(
        fitted["HF"],
        designs,
        box,
        cp_excess_max=cp_excess_max,
        dp_max_pa_per_m=dp_max_pa_per_m,
    )
    sweep = []
    for cp_limit in SWEEP_CP_EXCESS:
        for dp_limit in SWEEP_DP_PA_PER_M:
            sweep.append(
                _at_thresholds(
                    fitted["HF"],
                    designs,
                    box,
                    cp_excess_max=cp_limit,
                    dp_max_pa_per_m=dp_limit,
                )
            )
    return {
        "fitted": fitted,
        "costs": cost_medians(designs),
        "primary": primary,
        "sweep": sweep,
        "box": box,
        "designs": list(designs),
    }


def format_report(report: Mapping[str, Any]) -> str:
    """Text of the primary threshold and the nine-threshold sweep."""
    lines = [_costs_line(report["costs"]), ""]
    lines.append(_block(report["primary"], heading="primary"))
    lines.append("")
    lines.append("threshold sweep")
    for item in report["sweep"]:
        lines.append(_block(item, heading="sweep"))
    return "\n".join(lines) + "\n"


def write_contours(report: Mapping[str, Any], path: Path) -> Path:
    """LF and HF predicted LMH, with both constraint lines, side by side."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    primary = report["primary"]
    box: Box = report["box"]
    fitted = report["fitted"]
    d_p, d_h, grid = _mesh(box)
    figure, axes = plt.subplots(1, 2, figsize=(9.4, 4.15), sharey=True, constrained_layout=True)
    fields = []
    panels = []
    for level in _LEVELS:
        if level not in fitted:
            panels.append(None)
            continue
        lmh, dp, cp = _field(fitted[level], grid)
        fields.append(lmh)
        panels.append((lmh, dp, cp))
    finite = np.concatenate([item[np.isfinite(item)] for item in fields]) if fields else np.asarray([0.0])
    vmin = float(np.min(finite)) if len(finite) else 0.0
    vmax = float(np.max(finite)) if len(finite) else 1.0
    if vmax <= vmin:
        vmax = vmin + 1.0
    image = None
    for axis, level, panel in zip(axes, _LEVELS, panels, strict=True):
        axis.set_title(level)
        axis.set_xlabel("D_p (mm)")
        if panel is None:
            axis.set_xlim(box.d_p_min, box.d_p_max)
            axis.set_ylim(box.d_h_min, box.d_h_max)
            axis.text(0.5, 0.5, "no valid points", ha="center", va="center", transform=axis.transAxes)
            continue
        lmh, dp, cp = panel
        image = axis.contourf(d_p, d_h, lmh, levels=12, vmin=vmin, vmax=vmax, cmap="viridis")
        axis.contour(d_p, d_h, cp, levels=[primary["cp_excess_max"]], colors="black", linewidths=1.2)
        axis.contour(
            d_p,
            d_h,
            dp,
            levels=[primary["dp_max_pa_per_m"]],
            colors="white",
            linewidths=1.2,
            linestyles="--",
        )
        points = fitted[level]["points"]
        if points:
            axis.scatter(
                [item[0] for item in points],
                [item[1] for item in points],
                s=28,
                c="white",
                edgecolors="black",
                linewidths=0.6,
                zorder=3,
            )
        _mark_invalid(axis, report_designs(report), level)
        optimum = primary["optimum"]
        if level == "HF" and optimum is not None:
            axis.scatter(
                [optimum["d_p_mm"]],
                [optimum["d_h_mm"]],
                s=80,
                marker="*",
                c="red",
                edgecolors="black",
                linewidths=0.4,
                zorder=4,
            )
    axes[0].set_ylabel("d_h (mm)")
    if image is not None:
        figure.colorbar(image, ax=axes, label="predicted LMH", shrink=0.9)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=150)
    plt.close(figure)
    return destination


def report_designs(report: Mapping[str, Any]) -> Sequence[ScreenDesign]:
    designs = report.get("designs")
    if not isinstance(designs, Sequence):
        return ()
    return designs


def collect_from_leaves(
    designs: Sequence[Any],
    *,
    data_root: Path,
    fidelity_table: Mapping[str, Any],
    run_id: str,
) -> list[ScreenDesign]:
    """Read LF_d and HF_m leaves. Does not generate, mesh, or solve."""
    from ro.mfbo_adapter import record_from_leaves

    found = []
    for design in designs:
        low = record_from_leaves(
            design,
            "LF_d",
            run_id=run_id,
            data_root=data_root,
            fidelity_table=fidelity_table,
        )
        high = record_from_leaves(
            design,
            "HF_m",
            run_id=run_id,
            data_root=data_root,
            fidelity_table=fidelity_table,
        )
        found.append(_design_from_records(design, low, high))
    if not found:
        raise ValueError("No designs to read from leaves.")
    return found


def _at_thresholds(
    hf: Mapping[str, Any],
    designs: Sequence[ScreenDesign],
    box: Box,
    *,
    cp_excess_max: float,
    dp_max_pa_per_m: float,
) -> dict[str, Any]:
    search = _search(hf, box, cp_excess_max=cp_excess_max, dp_max_pa_per_m=dp_max_pa_per_m)
    grid = []
    for design in sorted(designs, key=lambda item: (item.d_p_mm, item.d_h_mm)):
        probability = _pof(
            hf,
            design.d_p_mm,
            design.d_h_mm,
            box,
            cp_excess_max=cp_excess_max,
            dp_max_pa_per_m=dp_max_pa_per_m,
        )
        grid.append(
            {
                "d_p_mm": design.d_p_mm,
                "d_h_mm": design.d_h_mm,
                "pof": probability,
            }
        )
    search["cp_excess_max"] = cp_excess_max
    search["dp_max_pa_per_m"] = dp_max_pa_per_m
    search["grid_pof"] = grid
    return search


def _search(
    hf: Mapping[str, Any],
    box: Box,
    *,
    cp_excess_max: float,
    dp_max_pa_per_m: float,
) -> dict[str, Any]:
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    best: dict[str, Any] | None = None
    n_blocked = 0
    for i_p in range(_GRID_P):
        for i_h in range(_GRID_H):
            d_p = box.d_p_min + (box.d_p_max - box.d_p_min) * i_p / (_GRID_P - 1)
            d_h = box.d_h_min + (box.d_h_max - box.d_h_min) * i_h / (_GRID_H - 1)
            blocked = geometry_block_reason(d_p, d_h, box.d_f_mm, box.m_min_mm) is not None
            lmh, _lmh_var = _predict(hf["lmh"], d_p, d_h, box)
            dp, _dp_var = _predict(hf["dp"], d_p, d_h, box)
            cp, _cp_var = _predict(hf["cp_excess"], d_p, d_h, box)
            if blocked:
                n_blocked += 1
            feasible = (not blocked) and dp <= dp_max_pa_per_m and cp <= cp_excess_max
            cell = {
                "i_p": i_p,
                "i_h": i_h,
                "d_p_mm": d_p,
                "d_h_mm": d_h,
                "lmh": lmh,
                "dp": dp,
                "cp_excess": cp,
                "feasible": feasible,
                "blocked": blocked,
            }
            cells[(i_p, i_h)] = cell
            if feasible and (best is None or lmh > best["lmh"]):
                best = cell
    n_cells = _GRID_P * _GRID_H
    n_feasible = sum(1 for cell in cells.values() if cell["feasible"])
    optimum = None
    if best is not None:
        active = _active(cells, best, cp_excess_max=cp_excess_max, dp_max_pa_per_m=dp_max_pa_per_m)
        optimum = {
            "d_p_mm": best["d_p_mm"],
            "d_h_mm": best["d_h_mm"],
            "lmh": best["lmh"],
            "cp_excess": best["cp_excess"],
            "dp_per_l": best["dp"],
            "active": active,
        }
    return {
        "n_cells": n_cells,
        "n_feasible": n_feasible,
        "n_blocked": n_blocked,
        "feasible_fraction": n_feasible / n_cells,
        "optimum": optimum,
    }


def _active(
    cells: Mapping[tuple[int, int], Mapping[str, Any]],
    best: Mapping[str, Any],
    *,
    cp_excess_max: float,
    dp_max_pa_per_m: float,
) -> list[str]:
    names = []
    limits = (("cp_excess", "cp_excess", cp_excess_max), ("dp_per_l", "dp", dp_max_pa_per_m))
    for label, key, limit in limits:
        for step_p, step_h in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            other = cells.get((best["i_p"] + step_p, best["i_h"] + step_h))
            if other is None or other["blocked"]:
                continue
            if other["lmh"] > best["lmh"] and other[key] > limit:
                names.append(label)
                break
    return names


def _pof(
    hf: Mapping[str, Any],
    d_p_mm: float,
    d_h_mm: float,
    box: Box,
    *,
    cp_excess_max: float,
    dp_max_pa_per_m: float,
) -> float:
    _lmh, _lmh_var = _predict(hf["lmh"], d_p_mm, d_h_mm, box)
    dp, dp_var = _predict(hf["dp"], d_p_mm, d_h_mm, box)
    cp, cp_var = _predict(hf["cp_excess"], d_p_mm, d_h_mm, box)
    return float(
        feasibility_probability(dp, dp_var, dp_max_pa_per_m)
        * feasibility_probability(cp, cp_var, cp_excess_max)
    )


def _predict(model: AutoregressiveModel, d_p_mm: float, d_h_mm: float, box: Box) -> tuple[float, float]:
    mean, var = model.predict_high(np.asarray([_xy(d_p_mm, d_h_mm, box)], dtype=float))
    return float(mean[0]), float(var[0])


def _field(fitted: Mapping[str, Any], grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lmh_mean, _lmh_var = fitted["lmh"].predict_high(grid)
    dp_mean, _dp_var = fitted["dp"].predict_high(grid)
    cp_mean, _cp_var = fitted["cp_excess"].predict_high(grid)
    shape = (_GRID_H, _GRID_P)
    return lmh_mean.reshape(shape), dp_mean.reshape(shape), cp_mean.reshape(shape)


def _mesh(box: Box) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d_p_axis = np.linspace(box.d_p_min, box.d_p_max, _GRID_P)
    d_h_axis = np.linspace(box.d_h_min, box.d_h_max, _GRID_H)
    d_p, d_h = np.meshgrid(d_p_axis, d_h_axis)
    points = np.column_stack(
        [
            ((d_p - box.d_p_min) / (box.d_p_max - box.d_p_min)).ravel(),
            ((d_h - box.d_h_min) / (box.d_h_max - box.d_h_min)).ravel(),
        ]
    )
    return d_p, d_h, points


def _xy(d_p_mm: float, d_h_mm: float, box: Box) -> tuple[float, float]:
    return (
        (d_p_mm - box.d_p_min) / (box.d_p_max - box.d_p_min),
        (d_h_mm - box.d_h_min) / (box.d_h_max - box.d_h_min),
    )


def _has_valid(designs: Sequence[ScreenDesign], level: str) -> bool:
    for design in designs:
        side = design.high if level == "HF" else design.low
        if side.trains:
            return True
    return False


def _block(item: Mapping[str, Any], *, heading: str) -> str:
    optimum = item["optimum"]
    if optimum is None:
        where = "optimum=none"
        active = "active=none"
    else:
        where = (
            f"optimum d_p_mm={optimum['d_p_mm']:.4f} d_h_mm={optimum['d_h_mm']:.4f} "
            f"lmh={optimum['lmh']:.4f} cp_excess={optimum['cp_excess']:.5f} "
            f"dp_per_l={optimum['dp_per_l']:.1f}"
        )
        active = "active=" + (",".join(optimum["active"]) if optimum["active"] else "none")
    pof = " ".join(
        f"{point['d_p_mm']:.2f},{point['d_h_mm']:.2f}:{point['pof']:.3f}" for point in item["grid_pof"]
    )
    return (
        f"{heading} cp_excess_max={item['cp_excess_max']:.3f} "
        f"dp_max_pa_per_m={item['dp_max_pa_per_m']:.0f} "
        f"feasible_fraction={item['feasible_fraction']:.4f} "
        f"n_feasible={item['n_feasible']} n_blocked={item['n_blocked']} "
        f"{where} {active}\n"
        f"  pof {pof}"
    )


def _costs_line(costs: Mapping[str, Any]) -> str:
    return (
        f"hf_wall_median_s={costs['hf_wall_median_s']:.6g} "
        f"lf_wall_median_s={costs['lf_wall_median_s']} "
        f"wall_cost_ratio_median={costs['wall_cost_ratio_median']} "
        f"cost_proxy_ratio_median={costs['cost_proxy_ratio_median']} "
        f"n_hf_wall={costs['n_hf_wall']}"
    )


def _mark_invalid(axis, designs: Sequence[ScreenDesign], level: str) -> None:
    marks = []
    for design in designs:
        side = design.high if level == "HF" else design.low
        if side.status != "valid":
            marks.append((design.d_p_mm, design.d_h_mm))
    if not marks:
        return
    axis.scatter(
        [item[0] for item in marks],
        [item[1] for item in marks],
        s=36,
        marker="x",
        c="red",
        linewidths=1.1,
        zorder=3,
    )


def _design_from_csv_row(row: Mapping[str, str], index: int) -> ScreenDesign:
    d_p = _required_float(f"row {index} d_p_mm", row.get("d_p_mm"))
    d_h = _required_float(f"row {index} d_h_mm", row.get("d_h_mm"))
    d_f = _optional_float(row.get("d_f_mm"))
    if d_f is None:
        d_f = 0.40
    if abs(d_f - 0.40) > 1.0e-9:
        raise ValueError(f"row {index} d_f_mm must be 0.40, got {d_f}.")
    return ScreenDesign(
        d_p_mm=d_p,
        d_h_mm=d_h,
        d_f_mm=d_f,
        low=_level_from_row(row, "lf"),
        high=_level_from_row(row, "hf"),
        cost_ratio=_optional_float(row.get("cost_ratio")),
        cost_proxy_ratio=_optional_float(row.get("cost_proxy_ratio")),
    )


def _design_from_records(design: Any, low: Mapping[str, Any], high: Mapping[str, Any]) -> ScreenDesign:
    low_obs = _level_from_record(low)
    high_obs = _level_from_record(high)
    ratio = None
    if _positive_number(low_obs.wall_s) and _positive_number(high_obs.wall_s):
        ratio = float(low_obs.wall_s) / float(high_obs.wall_s)
    return ScreenDesign(
        d_p_mm=float(design.d_p_mm),
        d_h_mm=float(design.d_h_mm),
        d_f_mm=float(design.d_f_mm),
        low=low_obs,
        high=high_obs,
        cost_ratio=ratio,
        cost_proxy_ratio=_proxy_from_records(low, high),
    )


def _level_from_row(row: Mapping[str, str], side: str) -> LevelObs:
    wall = _optional_float(row.get(f"wall_s_{side}"))
    if wall is None:
        parts = [
            _optional_float(row.get(f"{name}_{side}"))
            for name in ("mesh_wall_time_s", "solver_wall_time_s", "extraction_wall_time_s")
        ]
        if all(part is not None for part in parts):
            wall = float(sum(parts))
    status = (row.get(f"status_{side}") or "").strip() or "execution_failed"
    return LevelObs(
        status=status,
        lmh=_optional_float(row.get(f"lmh_{side}")),
        dp_per_l=_optional_float(row.get(f"pressure_drop_per_length_pa_per_m_{side}")),
        cp_average=_optional_float(row.get(f"cp_average_{side}")),
        wall_s=wall,
        iterations_run=_optional_float(row.get(f"iterations_run_{side}")),
        cell_count=_optional_float(row.get(f"cell_count_{side}")),
    )


def _level_from_record(record: Mapping[str, Any]) -> LevelObs:
    parts = [
        _optional_float(record.get(name))
        for name in ("mesh_wall_time_s", "solver_wall_time_s", "extraction_wall_time_s")
    ]
    wall = float(sum(parts)) if all(part is not None for part in parts) else None
    status = record.get("status")
    if not isinstance(status, str) or not status:
        status = "execution_failed"
    return LevelObs(
        status=status,
        lmh=_optional_float(record.get("lmh")),
        dp_per_l=_optional_float(record.get("pressure_drop_per_length_pa_per_m")),
        cp_average=_optional_float(record.get("cp_average")),
        wall_s=wall,
        iterations_run=_optional_float(record.get("iterations_run")),
        cell_count=_optional_float(record.get("cell_count")),
    )


def _proxy_ratio(design: ScreenDesign) -> float | None:
    return _product_ratio(
        design.low.cell_count,
        design.low.iterations_run,
        design.high.cell_count,
        design.high.iterations_run,
    )


def _proxy_from_records(low: Mapping[str, Any], high: Mapping[str, Any]) -> float | None:
    direct_low = _optional_float(low.get("cost_proxy"))
    direct_high = _optional_float(high.get("cost_proxy"))
    if _positive_number(direct_low) and _positive_number(direct_high):
        return float(direct_low) / float(direct_high)
    return _product_ratio(
        _optional_float(low.get("cell_count")),
        _optional_float(low.get("iterations_run")),
        _optional_float(high.get("cell_count")),
        _optional_float(high.get("iterations_run")),
    )


def _product_ratio(
    cells_low: float | None,
    iter_low: float | None,
    cells_high: float | None,
    iter_high: float | None,
) -> float | None:
    if not all(_positive_number(value) for value in (cells_low, iter_low, cells_high, iter_high)):
        return None
    return float(cells_low) * float(iter_low) / (float(cells_high) * float(iter_high))


def _required_float(name: str, value: Any) -> float:
    number = _optional_float(value)
    if number is None:
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return number


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    else:
        number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _positive_number(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0.0
