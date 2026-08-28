#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only: dump area-backed min columns and probe quantile-spread deltas.

Does NOT change the production CP path. Answers the pre-decision questions:

  Q1  Print cm_min_raw/used and jw_min_raw/used from summary_metrics_wide.csv
      when present; otherwise recompute them from the case via the same
      area-backed rule as production.
  Q2  For each eval cell, also compute unpaired delta using area quantiles
      at lo=0.001 and hi=0.999 (central 99.8% of membrane area) for cm and
      Jw, and report that hypothetical delta beside the current one.

Usage::

    export RO_DATA_ROOT='C:/ro_data'
    python scripts/diagnose_cp_quantile_spread.py \\
      --run-ids u0p1_p6M,u0p2_p6M,u0p3_p6M
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

from ro.cp_metrics import (
    FACET_MIN_TARGET_AREA_FRAC,
    film_theory_cp_perm_mol_m3,
    resolve_area_backed_minimum,
    scalar_rescale_guard_delta,
    facet_min_check_threshold,
    FACET_MIN_REJECT_AREA_FRAC,
)
from ro.domain_layout import layout_from_run_directory
from ro.fluent_report_helpers import (
    compute_surface_report_value,
    create_field_iso_clip,
    create_or_update_surface_field_report,
    create_x_range_iso_clip,
    delete_iso_clip,
    delete_surface_field_report,
    list_named_object_names,
)
from ro.manifest import read_run_manifest
from ro.paths import data_root, project_root
from ro.solver_common import path_to_fluent_str
from ro.udm_layout import FIELD_UDM_CM, FIELD_UDM_JW

B_PERM_DEFAULT = 2.50e-8
# Proposed central-area quantiles (excluded area each tail = 1e-3).
Q_LO = 1.0e-3
Q_HI = 1.0 - Q_LO  # 0.999


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only Q1/Q2 dump: area-backed mins + quantile-spread delta."
    )
    p.add_argument("--family", default="diamond")
    p.add_argument("--geo-id", default="D2450_a45")
    p.add_argument("--mesh-id", default="max085_min006_cpg5_bl4_peel2")
    p.add_argument("--run-ids", default="u0p1_p6M,u0p2_p6M,u0p3_p6M")
    p.add_argument("--results-root", default=None)
    p.add_argument("--b-perm", type=float, default=B_PERM_DEFAULT)
    p.add_argument("--c-inlet-ref", type=float, default=None)
    p.add_argument("--processor-count", type=int, default=1)
    p.add_argument("--product-version", default="25.1.0")
    p.add_argument("--graphics-driver", default="null")
    p.add_argument("--ui-mode", default="no_gui")
    p.add_argument(
        "--csv-only",
        action="store_true",
        help="Only print Q1 columns from existing wide CSV; do not launch Fluent.",
    )
    p.add_argument(
        "--dry-run-paths",
        action="store_true",
        help="Resolve and print case paths only.",
    )
    p.add_argument("--output-json", default=None)
    return p.parse_args(argv)


def resolve_runs_parent(args: argparse.Namespace) -> Path:
    if args.results_root:
        root = Path(args.results_root)
        if (root / "runs").is_dir() and root.name != "runs":
            return root / "runs"
        return root
    return data_root() / "runs"


def collect_membrane_walls(setup) -> list[str]:
    walls: list[str] = []
    try:
        wall_group = getattr(setup.boundary_conditions, "wall", None)
        if wall_group is not None:
            names = list_named_object_names(
                wall_group, "setup.boundary_conditions.wall"
            )
            walls = [n for n in names if "mem" in n.lower()]
    except Exception:
        walls = []
    return sorted(walls) if walls else ["wall_top_mem", "wall_bottom_mem"]


def surface_area(solution, surface_names: list[str], tag: str) -> float:
    name = f"pp_q_area_{tag}"
    create_or_update_surface_field_report(
        solution, name, "surface-area", None, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def surface_facet(
    solution, surface_names: list[str], field: str, which: str, tag: str
) -> float:
    rtype = "surface-facetmax" if which == "max" else "surface-facetmin"
    name = f"pp_q_{which}_{tag}"
    create_or_update_surface_field_report(
        solution, name, rtype, field, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def surface_areaavg(
    solution, surface_names: list[str], field: str, tag: str
) -> float:
    name = f"pp_q_avg_{tag}"
    create_or_update_surface_field_report(
        solution, name, "surface-areaavg", field, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def area_frac_field_le(
    solver,
    solution,
    parent_clip: str,
    field: str,
    threshold: float,
    total_area: float,
    tag: str,
) -> float:
    thr = float(threshold)
    if thr < 0.0 or total_area <= 0.0:
        return 0.0
    child = f"pp_q_le_{tag}"
    create_field_iso_clip(solver, child, [parent_clip], field, 0.0, thr)
    try:
        a = surface_area(solution, [child], f"{tag}_a")
        return max(0.0, a) / total_area
    finally:
        try:
            delete_iso_clip(solver, child)
        except Exception:
            pass


def area_quantile_threshold(
    solver,
    solution,
    parent_clip: str,
    field: str,
    total_area: float,
    quantile: float,
    lo_bound: float,
    hi_bound: float,
    tag: str,
    *,
    max_iter: int = 50,
) -> float:
    """Lowest T such that area(field <= T)/A >= quantile (area CDF inverse)."""
    q = float(quantile)
    lo = float(lo_bound)
    hi = float(hi_bound)
    if hi < lo:
        lo, hi = hi, lo

    def frac(t: float) -> float:
        return area_frac_field_le(
            solver, solution, parent_clip, field, t, total_area, f"{tag}_{t:.6g}"
        )

    # Expand upper if needed for high quantiles.
    expand = 0
    while frac(hi) < q and expand < 8:
        span = max(hi - lo, abs(hi), 1.0e-30)
        hi = hi + span
        expand += 1
    if frac(hi) < q:
        return hi
    if frac(lo) >= q:
        return lo
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if frac(mid) < q:
            lo = mid
        else:
            hi = mid
    return hi


def read_q1_from_wide_csv(case_dir: Path, eval_cells: list[int]) -> dict[str, Any]:
    path = case_dir / "post" / "reports" / "summary_metrics_wide.csv"
    out: dict[str, Any] = {"path": str(path), "present": path.is_file(), "cells": {}}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return out
    row = rows[0]
    for cell in eval_cells:
        out["cells"][cell] = {
            "cm_min_raw": _float_or_none(row.get(f"cm_min_raw_cell_{cell}")),
            "cm_min_used": _float_or_none(row.get(f"cm_min_used_cell_{cell}")),
            "jw_min_raw": _float_or_none(row.get(f"jw_min_raw_cell_{cell}")),
            "jw_min_used": _float_or_none(row.get(f"jw_min_used_cell_{cell}")),
            "rejected": _bool_or_none(
                row.get(f"cp_facet_min_rejected_cell_{cell}")
            ),
            "delta_current": _float_or_none(
                row.get(f"pp_cp_canon_rescale_delta_cell_{cell}")
            ),
            "c_b": _float_or_none(
                row.get(f"pp_c_b_midplane_cell_{cell}_mol_m3")
            ),
            "cp_udm9_avg": _float_or_none(
                row.get(f"pp_cp_inlet_unit_cell_boundary_{cell}")
            ),
            "cp_canon": _float_or_none(row.get(f"pp_cp_canon_cell_{cell}")),
        }
    out["delta_max"] = _float_or_none(row.get("cp_canon_rescale_delta_max"))
    out["cp_canon_window"] = _float_or_none(row.get("cp_canon_window_avg"))
    return out


def _float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes"}:
        return True
    if s in {"0", "false", "no"}:
        return False
    return None


def fmt(x: Any, digits: int = 6) -> str:
    if x is None:
        return "None"
    if isinstance(x, float):
        if not math.isfinite(x):
            return "nan"
        return f"{x:.{digits}g}"
    return str(x)


def diagnose_cell_fluent(
    solver,
    solution,
    *,
    cell_number: int,
    x_min: float,
    x_max: float,
    wall_names: list[str],
    c0: float,
    cb: float,
    b_perm: float,
) -> dict[str, Any]:
    tag = f"c{cell_number}"
    x_clip = f"pp_q_x_{tag}"
    create_x_range_iso_clip(solver, x_clip, wall_names, x_min, x_max)
    try:
        area = surface_area(solution, [x_clip], f"{tag}_tot")
        if area <= 0.0:
            return {"cell_number": cell_number, "error": "zero_area"}

        cm_avg = surface_areaavg(solution, [x_clip], FIELD_UDM_CM, f"{tag}_cm")
        jw_avg = surface_areaavg(solution, [x_clip], FIELD_UDM_JW, f"{tag}_jw")
        cm_min_raw = surface_facet(
            solution, [x_clip], FIELD_UDM_CM, "min", f"{tag}_cm"
        )
        cm_max_raw = surface_facet(
            solution, [x_clip], FIELD_UDM_CM, "max", f"{tag}_cm"
        )
        jw_min_raw = surface_facet(
            solution, [x_clip], FIELD_UDM_JW, "min", f"{tag}_jw"
        )
        jw_max_raw = surface_facet(
            solution, [x_clip], FIELD_UDM_JW, "max", f"{tag}_jw"
        )

        def _backed(field, raw_min, avg, label):
            check_hi = facet_min_check_threshold(raw_min)
            frac = area_frac_field_le(
                solver, solution, x_clip, field, check_hi, area, f"{tag}_{label}_chk"
            )
            used, rejected = resolve_area_backed_minimum(
                raw_min,
                area_frac_at_check=frac,
                area_frac_below_fn=lambda t, _f=field, _l=label: area_frac_field_le(
                    solver, solution, x_clip, _f, t, area, f"{tag}_{_l}_b"
                ),
                search_upper=max(avg, raw_min),
                reject_frac=FACET_MIN_REJECT_AREA_FRAC,
                target_frac=FACET_MIN_TARGET_AREA_FRAC,
            )
            return used, rejected, frac

        cm_min_used, cm_rej, cm_frac = _backed(
            FIELD_UDM_CM, cm_min_raw, cm_avg, "cm"
        )
        jw_min_used, jw_rej, jw_frac = _backed(
            FIELD_UDM_JW, jw_min_raw, jw_avg, "jw"
        )

        # Current production unpaired bound (area-backed mins, facet maxes).
        cp_min_cur = film_theory_cp_perm_mol_m3(cm_min_used, jw_max_raw, b_perm)
        cp_max_cur = film_theory_cp_perm_mol_m3(cm_max_raw, jw_min_used, b_perm)
        try:
            delta_cur = scalar_rescale_guard_delta(c0, cb, cp_min_cur, cp_max_cur)
        except ValueError as exc:
            delta_cur = float("nan")
            delta_cur_err = str(exc)
        else:
            delta_cur_err = ""

        # Proposed: area quantiles at Q_LO / Q_HI for both cm and Jw.
        cm_q_lo = area_quantile_threshold(
            solver,
            solution,
            x_clip,
            FIELD_UDM_CM,
            area,
            Q_LO,
            cm_min_raw,
            max(cm_avg, cm_min_raw),
            f"{tag}_cmqlo",
        )
        cm_q_hi = area_quantile_threshold(
            solver,
            solution,
            x_clip,
            FIELD_UDM_CM,
            area,
            Q_HI,
            max(cm_avg, cm_min_raw),
            max(cm_max_raw, cm_avg),
            f"{tag}_cmqhi",
        )
        jw_q_lo = area_quantile_threshold(
            solver,
            solution,
            x_clip,
            FIELD_UDM_JW,
            area,
            Q_LO,
            jw_min_raw,
            max(jw_avg, jw_min_raw),
            f"{tag}_jwqlo",
        )
        jw_q_hi = area_quantile_threshold(
            solver,
            solution,
            x_clip,
            FIELD_UDM_JW,
            area,
            Q_HI,
            max(jw_avg, jw_min_raw),
            max(jw_max_raw, jw_avg),
            f"{tag}_jwqhi",
        )

        # Unpaired quantile bound mirrors production pairing:
        # cp_lo from (cm_q_lo, jw_q_hi), cp_hi from (cm_q_hi, jw_q_lo).
        cp_min_q = film_theory_cp_perm_mol_m3(cm_q_lo, jw_q_hi, b_perm)
        cp_max_q = film_theory_cp_perm_mol_m3(cm_q_hi, jw_q_lo, b_perm)
        try:
            delta_q = scalar_rescale_guard_delta(c0, cb, cp_min_q, cp_max_q)
        except ValueError as exc:
            delta_q = float("nan")
            delta_q_err = str(exc)
        else:
            delta_q_err = ""

        return {
            "cell_number": cell_number,
            "area_m2": area,
            "cm_avg": cm_avg,
            "jw_avg": jw_avg,
            "cm_min_raw": cm_min_raw,
            "cm_min_used": cm_min_used,
            "cm_min_rejected": cm_rej,
            "cm_area_frac_at_check": cm_frac,
            "jw_min_raw": jw_min_raw,
            "jw_min_used": jw_min_used,
            "jw_min_rejected": jw_rej,
            "jw_area_frac_at_check": jw_frac,
            "cm_max_raw": cm_max_raw,
            "jw_max_raw": jw_max_raw,
            "cp_min_current": cp_min_cur,
            "cp_max_current": cp_max_cur,
            "delta_current": delta_cur,
            "delta_current_error": delta_cur_err,
            "cm_q_lo": cm_q_lo,
            "cm_q_hi": cm_q_hi,
            "jw_q_lo": jw_q_lo,
            "jw_q_hi": jw_q_hi,
            "cp_min_quantile": cp_min_q,
            "cp_max_quantile": cp_max_q,
            "spread_current": cp_max_cur - cp_min_cur,
            "spread_quantile": cp_max_q - cp_min_q,
            "delta_quantile": delta_q,
            "delta_quantile_error": delta_q_err,
            "c_b": cb,
        }
    finally:
        try:
            delete_iso_clip(solver, x_clip)
        except Exception:
            pass


def diagnose_run_fluent(args, case_dir: Path, geo_id: str, run_id: str) -> dict[str, Any]:
    import ansys.fluent.core as pyfluent  # noqa: PLC0415

    cas = case_dir / f"{geo_id}_{run_id}_final.cas.h5"
    layout_record, _ = layout_from_run_directory(case_dir)
    layout = layout_record.layout
    window = layout_record.evaluation_window
    eval_cells = window.evaluation_cell_numbers(layout)
    run_manifest = read_run_manifest(case_dir)
    x0 = float(run_manifest.get("domain_extent_x_min_m") or 0.0)
    boundaries = layout.boundary_positions(x0)
    c0 = float(
        args.c_inlet_ref
        if args.c_inlet_ref is not None
        else run_manifest.get("c_inlet_ref")
        or 597.8268309
    )
    b_perm = float(args.b_perm)

    csv_q1 = read_q1_from_wide_csv(case_dir, eval_cells)

    print(f"\nLaunching Fluent for {geo_id}/{run_id} ...")
    meshing = None
    solver = None
    try:
        meshing = pyfluent.launch_fluent(
            product_version=args.product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=args.processor_count,
            ui_mode=args.ui_mode,
            graphics_driver=args.graphics_driver,
            cwd=path_to_fluent_str(case_dir),
        )
        solver = meshing.switch_to_solver()
        meshing = None
        setup = solver.settings.setup
        solution = solver.settings.solution
        solver.settings.file.read_case_data(file_name=path_to_fluent_str(cas))
        walls = collect_membrane_walls(setup)

        # Prefer published c_b; fall back requires midplane (skip — use CSV).
        cells_out = []
        for cell in eval_cells:
            cb = None
            if csv_q1.get("cells", {}).get(cell, {}).get("c_b") is not None:
                cb = float(csv_q1["cells"][cell]["c_b"])
            if cb is None:
                raise RuntimeError(
                    f"c_b missing for cell {cell} in wide CSV; "
                    "re-run extract or supply published c_b."
                )
            print(f"  cell {cell}: c_b={cb:.6g}")
            cells_out.append(
                diagnose_cell_fluent(
                    solver,
                    solution,
                    cell_number=cell,
                    x_min=boundaries[cell - 1],
                    x_max=boundaries[cell],
                    wall_names=walls,
                    c0=c0,
                    cb=cb,
                    b_perm=b_perm,
                )
            )
        return {
            "run_id": run_id,
            "case_dir": str(case_dir),
            "eval_cells": eval_cells,
            "c0": c0,
            "b_perm": b_perm,
            "q_lo": Q_LO,
            "q_hi": Q_HI,
            "csv_q1": csv_q1,
            "cells": cells_out,
        }
    finally:
        if solver is not None:
            try:
                solver.exit(timeout=60)
            except Exception:
                try:
                    solver.force_exit()
                except Exception:
                    pass
        if meshing is not None:
            try:
                meshing.exit(timeout=60)
            except Exception:
                try:
                    meshing.force_exit()
                except Exception:
                    pass


def print_q1_table(run_id: str, csv_q1: dict[str, Any], cells_fluent: list[dict] | None = None) -> None:
    print("\n" + "=" * 78)
    print(f"Q1  {run_id}  area-backed substitutions")
    print("=" * 78)
    src = "wide CSV" if csv_q1.get("present") else "Fluent recompute"
    print(f"  source: {src}  ({csv_q1.get('path')})")
    print(
        f"  {'cell':>4} {'rej':>5} "
        f"{'cm_raw':>10} {'cm_used':>10} "
        f"{'jw_raw':>12} {'jw_used':>12} {'delta':>10}"
    )
    fluent_by_cell = {
        c.get("cell_number"): c for c in (cells_fluent or []) if "cell_number" in c
    }
    cells = csv_q1.get("cells") or {}
    keys = sorted(set(cells) | set(fluent_by_cell))
    for cell in keys:
        row = cells.get(cell) or {}
        flu = fluent_by_cell.get(cell) or {}
        cm_raw = row.get("cm_min_raw")
        cm_used = row.get("cm_min_used")
        jw_raw = row.get("jw_min_raw")
        jw_used = row.get("jw_min_used")
        rej = row.get("rejected")
        delta = row.get("delta_current")
        if cm_raw is None and flu:
            cm_raw = flu.get("cm_min_raw")
            cm_used = flu.get("cm_min_used")
            jw_raw = flu.get("jw_min_raw")
            jw_used = flu.get("jw_min_used")
            rej = flu.get("cm_min_rejected") or flu.get("jw_min_rejected")
            delta = flu.get("delta_current")
        print(
            f"  {cell:>4} {str(rej):>5} "
            f"{fmt(cm_raw):>10} {fmt(cm_used):>10} "
            f"{fmt(jw_raw):>12} {fmt(jw_used):>12} {fmt(delta):>10}"
        )


def print_q2_table(run_id: str, payload: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(
        f"Q2  {run_id}  current delta vs quantile-spread "
        f"(q_lo={Q_LO:g}, q_hi={Q_HI:g})"
    )
    print("=" * 78)
    print(
        f"  {'cell':>4} {'d_cur':>10} {'d_q':>10} {'ratio':>8} "
        f"{'cm_qhi':>10} {'cm_max':>10} {'jw_qlo':>12} {'jw_min_u':>12}"
    )
    for cell in payload.get("cells") or []:
        d0 = cell.get("delta_current")
        dq = cell.get("delta_quantile")
        ratio = None
        if (
            isinstance(d0, float)
            and isinstance(dq, float)
            and math.isfinite(d0)
            and math.isfinite(dq)
            and dq != 0.0
        ):
            ratio = d0 / dq
        print(
            f"  {cell.get('cell_number'):>4} {fmt(d0):>10} {fmt(dq):>10} "
            f"{fmt(ratio, 3):>8} "
            f"{fmt(cell.get('cm_q_hi')):>10} {fmt(cell.get('cm_max_raw')):>10} "
            f"{fmt(cell.get('jw_q_lo')):>12} {fmt(cell.get('jw_min_used')):>12}"
        )
    deltas_q = [
        c.get("delta_quantile")
        for c in (payload.get("cells") or [])
        if isinstance(c.get("delta_quantile"), float)
        and math.isfinite(c["delta_quantile"])
    ]
    deltas_c = [
        c.get("delta_current")
        for c in (payload.get("cells") or [])
        if isinstance(c.get("delta_current"), float)
        and math.isfinite(c["delta_current"])
    ]
    if deltas_q and deltas_c:
        print(
            f"  max delta_current={fmt(max(deltas_c))}  "
            f"max delta_quantile={fmt(max(deltas_q))}  "
            f"ratio={fmt(max(deltas_c)/max(deltas_q) if max(deltas_q) else None, 3)}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    run_ids = [s.strip() for s in args.run_ids.split(",") if s.strip()]
    try:
        runs_parent = resolve_runs_parent(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("CP quantile-spread diagnostic (read-only; production path untouched)")
    print(f"  runs_parent = {runs_parent}")
    print(f"  q_lo={Q_LO:g}  q_hi={Q_HI:g}  (excluded each tail = {Q_LO:g})")
    print(
        f"  saturation patch area (Yi>=0.26, u0p1 cell7) = 1.64e-04; "
        f"tail/patch = {Q_LO/1.64e-4:.2f}x"
    )

    if args.dry_run_paths:
        for run_id in run_ids:
            d = runs_parent / args.family / args.geo_id / args.mesh_id / run_id
            cas = d / f"{args.geo_id}_{run_id}_final.cas.h5"
            csv_path = d / "post" / "reports" / "summary_metrics_wide.csv"
            print(
                f"  {run_id}: dir={d.is_dir()} cas={cas.is_file()} "
                f"csv={csv_path.is_file()} path={d}"
            )
        return 0

    report: dict[str, Any] = {
        "script": Path(__file__).name,
        "q_lo": Q_LO,
        "q_hi": Q_HI,
        "runs": {},
        "errors": {},
    }

    for run_id in run_ids:
        case_dir = (
            runs_parent / args.family / args.geo_id / args.mesh_id / run_id
        )
        if not case_dir.is_dir():
            alt = (
                runs_parent
                / "runs"
                / args.family
                / args.geo_id
                / args.mesh_id
                / run_id
            )
            if alt.is_dir():
                case_dir = alt
            else:
                report["errors"][run_id] = {
                    "type": "FileNotFoundError",
                    "message": f"Run directory not found: {case_dir}",
                }
                print(f"ERROR: missing {case_dir}", file=sys.stderr)
                continue

        layout_record, _ = layout_from_run_directory(case_dir)
        eval_cells = layout_record.evaluation_window.evaluation_cell_numbers(
            layout_record.layout
        )
        csv_q1 = read_q1_from_wide_csv(case_dir, eval_cells)
        print_q1_table(run_id, csv_q1)

        if args.csv_only:
            report["runs"][run_id] = {"csv_q1": csv_q1}
            continue

        try:
            payload = diagnose_run_fluent(
                args, case_dir, args.geo_id, run_id
            )
            report["runs"][run_id] = payload
            print_q1_table(run_id, payload.get("csv_q1") or csv_q1, payload.get("cells"))
            print_q2_table(run_id, payload)
        except Exception as exc:
            report["errors"][run_id] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"ERROR on {run_id}: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Q3 note (analytic; does not need Fluent).
    print("\n" + "=" * 78)
    print("Q3  quantile choice vs saturation-patch area")
    print("=" * 78)
    patch = 1.64e-4
    print(f"  saturation patch area fraction (Yi>=0.26, u0p1 cell7) = {patch:g}")
    print(f"  proposed excluded area per tail (0.1%)                 = {Q_LO:g}")
    print(f"  excluded / patch                                       = {Q_LO/patch:.2f}x")
    print(
        "  Two orders of magnitude above the patch would require "
        f"excluded >= {100*patch:g} (= {100*patch*100:.2f}% per tail)."
    )
    print(
        "  At 0.1% the margin is ~6x (~0.8 decades), NOT two orders. "
        "The 0.1%/99.9% choice still excludes the 1.64e-04 patch from the "
        "high quantile, but the 'two orders above' rationale does not hold."
    )

    out_path = args.output_json
    if out_path is None:
        try:
            inv = data_root() / "inventory" / "diagnostics"
        except ValueError:
            inv = project_root() / "_scratch" / "diagnostics"
        inv.mkdir(parents=True, exist_ok=True)
        out_path = str(inv / "cp_quantile_spread_diagnostic.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nWrote JSON: {out_path}")

    if not report["runs"] and report["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
