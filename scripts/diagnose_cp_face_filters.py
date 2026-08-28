#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only diagnostic: Axis A (c_m==0) and Axis B (low Jw) on membrane CP.

Does NOT modify the production CP path. Launches Fluent, measures area
fractions via nested iso-clips, prints tables, writes JSON, exits.

Usage (on the CFD host with case data)::

    export RO_DATA_ROOT='C:/ro_data'
    python scripts/diagnose_cp_face_filters.py \\
      --family diamond --geo-id D2450_a45 \\
      --mesh-id max085_min006_cpg5_bl4_peel2 \\
      --run-ids u0p1_p6M,u0p2_p6M,u0p3_p6M
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

import ansys.fluent.core as pyfluent

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    film_theory_cp_perm_mol_m3,
    scalar_rescale_guard_delta,
    window_area_weighted_average,
)
from ro.domain_layout import layout_from_run_directory
from ro.fluent_report_helpers import (
    compute_surface_report_value,
    create_or_update_surface_field_report,
    create_x_range_iso_clip,
    delete_iso_clip,
    delete_surface_field_report,
    evaluation_window_midplane_bulk_concentrations,
    list_named_object_names,
)
from ro.manifest import read_run_manifest
from ro.paths import data_root, project_root
from ro.udm_layout import FIELD_UDM_CM, FIELD_UDM_CP_INLET, FIELD_UDM_JW

SCRIPT_DIR = Path(__file__).resolve().parent
B_PERM_DEFAULT = 2.50e-8
CM_ZERO_EPS = 1.0e-12  # mol/m3; treat as unwritten UDM
CM_SOFT_EPS = 1.0  # mol/m3
ALPHAS = (0.1, 1.0, 10.0)
JW_HIST_ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0, 600.0)
CONTACT_WIDTH_OVER_PITCH = 0.000152 / 0.003465  # ~4.4%
DISCRIMINABILITY = 0.006


def create_z_normal_plane(solver_obj, surface_name: str, z_value_m: float) -> None:
    """Create a z-normal iso-surface (settings API, TUI fallback)."""
    settings_error = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list_named_object_names(
            iso_group, "results.surfaces.iso_surface"
        )
        if surface_name in existing:
            iso_group.delete(surface_name)
        iso_group.create(surface_name)
        iso_group[surface_name].field = "z-coordinate"
        iso_group[surface_name].iso_values = [float(z_value_m)]
        return
    except Exception as exc:
        settings_error = exc
    try:
        solver_obj.tui.surface.iso_surface(
            "z-coordinate",
            surface_name,
            "()",
            "()",
            str(z_value_m),
            "0",
        )
    except Exception as tui_error:
        raise RuntimeError(
            f"Could not create iso-surface {surface_name!r}. "
            f"Settings error: {settings_error}. TUI error: {tui_error}"
        ) from tui_error


def compute_c_b_by_cell(
    solver,
    solution,
    *,
    boundaries: list[float],
    eval_cells: list[int],
    density: float,
    mw: float,
    channel_height_m: float = 0.00077,
) -> dict[int, float]:
    z_center = channel_height_m / 2.0
    plane_name = None
    for z_val in (z_center, 0.0):
        pname = f"pp_diag_zc_{abs(z_val):.7f}".replace(".", "p")
        try:
            create_z_normal_plane(solver, pname, z_val)
            plane_name = pname
            break
        except Exception as exc:
            print(f"  mid-plane at z={z_val} failed: {exc}")
    if plane_name is None:
        raise RuntimeError("Could not create mid-plane iso-surface for c_b.")

    last_error = None
    for salt_field in ("nacl", "mass-fraction-of-nacl", "yi-0"):
        try:
            c_b_by_cell, _areas, _window = (
                evaluation_window_midplane_bulk_concentrations(
                    solver=solver,
                    solution=solution,
                    midplane_surface_names=[plane_name],
                    unit_cell_boundary_x_m=boundaries,
                    evaluation_cell_numbers=eval_cells,
                    salt_field=salt_field,
                    density_kg_per_m3=density,
                    molecular_weight_kg_per_mol=mw,
                    salt_is_mass_fraction=True,
                )
            )
            return {int(k): float(v) for k, v in c_b_by_cell.items()}
        except Exception as exc:
            last_error = exc
    raise RuntimeError(
        f"Could not compute mid-plane c_b (last error: {last_error!r})."
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only Axis A/B CP face-filter diagnostic (no production changes)."
    )
    p.add_argument("--family", default="diamond")
    p.add_argument("--geo-id", default="D2450_a45")
    p.add_argument("--mesh-id", default="max085_min006_cpg5_bl4_peel2")
    p.add_argument(
        "--run-ids",
        default="u0p1_p6M,u0p2_p6M,u0p3_p6M",
        help="Comma-separated run ids.",
    )
    p.add_argument(
        "--results-root",
        default=None,
        help="Override RO_DATA_ROOT/runs parent (absolute). Default: runs_root().",
    )
    p.add_argument(
        "--output-json",
        default=None,
        help="Write full JSON report here (default: inventory/diagnostics/).",
    )
    p.add_argument("--b-perm", type=float, default=B_PERM_DEFAULT)
    p.add_argument("--c-inlet-ref", type=float, default=None)
    p.add_argument("--processor-count", type=int, default=1)
    p.add_argument("--product-version", default="25.1.0")
    p.add_argument("--graphics-driver", default="null")
    p.add_argument("--ui-mode", default="no_gui")
    p.add_argument(
        "--dry-run-paths",
        action="store_true",
        help="Only resolve and print case paths; do not launch Fluent.",
    )
    return p.parse_args(argv)


def create_field_iso_clip(
    solver,
    clip_name: str,
    surface_names: list[str],
    field: str,
    range_min: float,
    range_max: float,
) -> str:
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
    iso_group.create(clip_name)
    clip = iso_group[clip_name]
    clip.field = field
    clip.surfaces = list(surface_names)
    clip.range.minimum = float(range_min)
    clip.range.maximum = float(range_max)
    return clip_name


def surface_area(solution, surface_names: list[str], tag: str) -> float:
    name = f"pp_diag_area_{tag}"
    create_or_update_surface_field_report(
        solution, name, "surface-area", None, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def surface_areaavg(
    solution, surface_names: list[str], field: str, tag: str
) -> float:
    name = f"pp_diag_avg_{tag}"
    create_or_update_surface_field_report(
        solution, name, "surface-areaavg", field, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def surface_facet(
    solution, surface_names: list[str], field: str, which: str, tag: str
) -> float:
    report_type = "surface-facetmax" if which == "max" else "surface-facetmin"
    name = f"pp_diag_{which}_{tag}"
    create_or_update_surface_field_report(
        solution, name, report_type, field, surface_names
    )
    try:
        return float(compute_surface_report_value(solution, name))
    finally:
        delete_surface_field_report(solution, name)


def safe_area(solution, surface_names: list[str], tag: str) -> float:
    try:
        area = surface_area(solution, surface_names, tag)
    except Exception as exc:
        return 0.0
    return max(0.0, float(area))


def collect_membrane_walls(setup) -> list[str]:
    """Return wall_top_mem / wall_bottom_mem style names present in the case."""
    walls: list[str] = []
    try:
        boundary = setup.boundary_conditions
        # Prefer typed wall group when present.
        wall_group = getattr(boundary, "wall", None)
        if wall_group is not None:
            names = list_named_object_names(wall_group, "setup.boundary_conditions.wall")
            walls = [n for n in names if "mem" in n.lower()]
    except Exception:
        walls = []
    if walls:
        return sorted(walls)
    # Fallback: common campaign names.
    return ["wall_top_mem", "wall_bottom_mem"]


def load_c_b_from_wide_csv(case_dir: Path) -> dict[str, Any]:
    """Pull published c_b / canon columns when a prior extract exists."""
    path = case_dir / "post" / "reports" / "summary_metrics_wide.csv"
    out: dict[str, Any] = {"path": str(path) if path.is_file() else ""}
    if not path.is_file():
        return out
    try:
        import pandas as pd

        df = pd.read_csv(path)
        if df.empty:
            return out
        row = df.iloc[0].to_dict()
        for key, value in row.items():
            if key.startswith("pp_c_b_midplane_cell_") or key in {
                "c_b_window_mol_m3",
                "cp_canon_window_avg",
                "cp_canon_rescale_delta_max",
                "c_inlet_ref_mol_m3",
            }:
                out[str(key)] = value
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def delta_from_extrema(
    c0: float,
    cb: float,
    cm_avg: float,
    jw_avg: float,
    cm_min: float,
    cm_max: float,
    jw_min: float,
    jw_max: float,
    b_perm: float,
) -> dict[str, float]:
    cp_avg = film_theory_cp_perm_mol_m3(cm_avg, jw_avg, b_perm)
    cp_min = film_theory_cp_perm_mol_m3(cm_min, jw_max, b_perm)
    cp_max = film_theory_cp_perm_mol_m3(cm_max, jw_min, b_perm)
    denom = cb - cp_avg
    k = (c0 - cp_avg) / denom if denom > 0.0 else float("nan")
    try:
        delta = scalar_rescale_guard_delta(c0, cb, cp_min, cp_max)
    except ValueError:
        delta = float("nan")
    return {
        "cp_avg": cp_avg,
        "cp_min": cp_min,
        "cp_max": cp_max,
        "k": k,
        "delta": delta,
        "spread_cp": cp_max - cp_min,
    }


def diagnose_cell(
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
    x_clip = f"pp_diag_x_{tag}"
    created = [x_clip]
    try:
        create_x_range_iso_clip(solver, x_clip, wall_names, x_min, x_max)
        area = safe_area(solution, [x_clip], f"{tag}_tot")
        if area <= 0.0:
            return {
                "cell_number": cell_number,
                "area_m2": 0.0,
                "error": "zero_segment_area",
            }

        cm_avg = surface_areaavg(solution, [x_clip], FIELD_UDM_CM, f"{tag}_cm")
        jw_avg = surface_areaavg(solution, [x_clip], FIELD_UDM_JW, f"{tag}_jw")
        cp_udm9_avg = surface_areaavg(
            solution, [x_clip], FIELD_UDM_CP_INLET, f"{tag}_cp"
        )
        cm_min = surface_facet(solution, [x_clip], FIELD_UDM_CM, "min", f"{tag}_cm")
        cm_max = surface_facet(solution, [x_clip], FIELD_UDM_CM, "max", f"{tag}_cm")
        jw_min = surface_facet(solution, [x_clip], FIELD_UDM_JW, "min", f"{tag}_jw")
        jw_max = surface_facet(solution, [x_clip], FIELD_UDM_JW, "max", f"{tag}_jw")

        baseline = delta_from_extrema(
            c0, cb, cm_avg, jw_avg, cm_min, cm_max, jw_min, jw_max, b_perm
        )
        baseline["cp_udm9_avg"] = cp_udm9_avg
        baseline["cp_canon"] = (
            cp_udm9_avg * baseline["k"]
            if math.isfinite(baseline["k"])
            else float("nan")
        )

        # ---- Axis A: c_m == 0 / soft zero ----
        axis_a: dict[str, Any] = {}
        for label, lo, hi in (
            ("cm_eq_0", 0.0, CM_ZERO_EPS),
            ("cm_lt_1", 0.0, CM_SOFT_EPS),
        ):
            clip = f"pp_diag_{label}_{tag}"
            created.append(clip)
            create_field_iso_clip(solver, clip, [x_clip], FIELD_UDM_CM, lo, hi)
            a_sub = safe_area(solution, [clip], f"{tag}_{label}")
            axis_a[f"area_frac_{label}"] = a_sub / area

        # Nonzero cm clip for cleaned stats.
        nz_clip = f"pp_diag_cm_nz_{tag}"
        created.append(nz_clip)
        create_field_iso_clip(
            solver, nz_clip, [x_clip], FIELD_UDM_CM, CM_ZERO_EPS, 1.0e9
        )
        area_nz = safe_area(solution, [nz_clip], f"{tag}_nz")
        axis_a["area_frac_cm_gt_0"] = area_nz / area
        if area_nz > 0.0:
            cm_avg_nz = surface_areaavg(
                solution, [nz_clip], FIELD_UDM_CM, f"{tag}_cm_nz"
            )
            jw_avg_nz = surface_areaavg(
                solution, [nz_clip], FIELD_UDM_JW, f"{tag}_jw_nz"
            )
            cp_udm9_nz = surface_areaavg(
                solution, [nz_clip], FIELD_UDM_CP_INLET, f"{tag}_cp_nz"
            )
            cm_min_nz = surface_facet(
                solution, [nz_clip], FIELD_UDM_CM, "min", f"{tag}_cm_nz"
            )
            cm_max_nz = surface_facet(
                solution, [nz_clip], FIELD_UDM_CM, "max", f"{tag}_cm_nz"
            )
            jw_min_nz = surface_facet(
                solution, [nz_clip], FIELD_UDM_JW, "min", f"{tag}_jw_nz"
            )
            jw_max_nz = surface_facet(
                solution, [nz_clip], FIELD_UDM_JW, "max", f"{tag}_jw_nz"
            )
            cleaned = delta_from_extrema(
                c0,
                cb,
                cm_avg_nz,
                jw_avg_nz,
                cm_min_nz,
                cm_max_nz,
                jw_min_nz,
                jw_max_nz,
                b_perm,
            )
            cleaned["cm_avg"] = cm_avg_nz
            cleaned["cm_min"] = cm_min_nz
            cleaned["cm_max"] = cm_max_nz
            cleaned["jw_min"] = jw_min_nz
            cleaned["jw_max"] = jw_max_nz
            cleaned["cp_udm9_avg"] = cp_udm9_nz
            cleaned["cp_canon"] = (
                cp_udm9_nz * cleaned["k"]
                if math.isfinite(cleaned["k"])
                else float("nan")
            )
        else:
            cleaned = {"error": "no_nonzero_cm_area"}

        axis_a["cm_avg_all"] = cm_avg
        axis_a["cm_min_all"] = cm_min
        axis_a["excluding_cm_eq_0"] = cleaned

        # ---- Axis B: Jw / B cumulative ----
        axis_b: dict[str, Any] = {
            "jw_over_B_min": jw_min / b_perm,
            "jw_over_B_avg": jw_avg / b_perm,
            "jw_over_B_max": jw_max / b_perm,
            "geometric_contact_frac_per_cut": CONTACT_WIDTH_OVER_PITCH,
            "histogram_cum_area_frac_Jw_lt_alpha_B": {},
            "alpha_cuts": {},
        }
        for alpha in JW_HIST_ALPHAS:
            clip = f"pp_diag_jw_a{alpha:g}_{tag}".replace(".", "p")
            created.append(clip)
            create_field_iso_clip(
                solver, clip, [x_clip], FIELD_UDM_JW, 0.0, alpha * b_perm
            )
            a_sub = safe_area(solution, [clip], f"{tag}_jw_a{alpha:g}")
            axis_b["histogram_cum_area_frac_Jw_lt_alpha_B"][str(alpha)] = a_sub / area

        for alpha in ALPHAS:
            keep = f"pp_diag_jw_keep_a{alpha:g}_{tag}".replace(".", "p")
            created.append(keep)
            # Keep faces with Jw >= alpha*B (upper bound large).
            create_field_iso_clip(
                solver, keep, [x_clip], FIELD_UDM_JW, alpha * b_perm, 1.0e3
            )
            area_keep = safe_area(solution, [keep], f"{tag}_keep_a{alpha:g}")
            excluded_frac = 1.0 - area_keep / area
            entry: dict[str, Any] = {
                "alpha": alpha,
                "Jw_cut": alpha * b_perm,
                "area_frac_excluded": excluded_frac,
                "area_frac_kept": area_keep / area,
            }
            if area_keep > 0.0:
                cm_a = surface_areaavg(solution, [keep], FIELD_UDM_CM, f"{tag}_a{alpha:g}_cm")
                jw_a = surface_areaavg(solution, [keep], FIELD_UDM_JW, f"{tag}_a{alpha:g}_jw")
                cp9_a = surface_areaavg(
                    solution, [keep], FIELD_UDM_CP_INLET, f"{tag}_a{alpha:g}_cp"
                )
                cm_min_a = surface_facet(
                    solution, [keep], FIELD_UDM_CM, "min", f"{tag}_a{alpha:g}_cm"
                )
                cm_max_a = surface_facet(
                    solution, [keep], FIELD_UDM_CM, "max", f"{tag}_a{alpha:g}_cm"
                )
                jw_min_a = surface_facet(
                    solution, [keep], FIELD_UDM_JW, "min", f"{tag}_a{alpha:g}_jw"
                )
                jw_max_a = surface_facet(
                    solution, [keep], FIELD_UDM_JW, "max", f"{tag}_a{alpha:g}_jw"
                )
                d = delta_from_extrema(
                    c0, cb, cm_a, jw_a, cm_min_a, cm_max_a, jw_min_a, jw_max_a, b_perm
                )
                entry.update(d)
                entry["cp_udm9_avg"] = cp9_a
                entry["cp_canon"] = (
                    cp9_a * d["k"] if math.isfinite(d["k"]) else float("nan")
                )
            axis_b["alpha_cuts"][str(alpha)] = entry

        # ---- Paired vs unpaired bound ----
        pairing: dict[str, Any] = {
            "unpaired_cp_max": baseline["cp_max"],
            "unpaired_delta": baseline["delta"],
            "note": (
                "Unpaired bound uses (cm_max, jw_min). True face-wise cp_max "
                "cannot exceed that. Lower estimates use co-located clips."
            ),
        }
        # Low-Jw pocket: Jw <= max(10*jw_min, 0.1*B) but at least >0 band.
        jw_hi_low = max(10.0 * max(jw_min, 0.0), 0.1 * b_perm, CM_ZERO_EPS)
        low_jw = f"pp_diag_lowjw_{tag}"
        created.append(low_jw)
        create_field_iso_clip(solver, low_jw, [x_clip], FIELD_UDM_JW, 0.0, jw_hi_low)
        area_low = safe_area(solution, [low_jw], f"{tag}_lowjw")
        pairing["low_jw_band_max"] = jw_hi_low
        pairing["area_frac_low_jw_band"] = area_low / area
        if area_low > 0.0:
            cm_on_low_jw = surface_facet(
                solution, [low_jw], FIELD_UDM_CM, "max", f"{tag}_lowjw_cm"
            )
            jw_on_low = surface_facet(
                solution, [low_jw], FIELD_UDM_JW, "min", f"{tag}_lowjw_jw"
            )
            cp_max_lowjw = film_theory_cp_perm_mol_m3(
                cm_on_low_jw, jw_on_low, b_perm
            )
            pairing["cm_max_on_low_jw_band"] = cm_on_low_jw
            pairing["jw_min_on_low_jw_band"] = jw_on_low
            pairing["cp_max_on_low_jw_band"] = cp_max_lowjw
        else:
            cp_max_lowjw = float("nan")

        # High-cm pocket: cm >= 0.5 * cm_max (if cm_max > soft eps).
        if cm_max > CM_SOFT_EPS:
            high_cm = f"pp_diag_highcm_{tag}"
            created.append(high_cm)
            create_field_iso_clip(
                solver, high_cm, [x_clip], FIELD_UDM_CM, 0.5 * cm_max, 1.0e9
            )
            area_hi = safe_area(solution, [high_cm], f"{tag}_highcm")
            pairing["area_frac_high_cm_band"] = area_hi / area
            if area_hi > 0.0:
                jw_on_high_cm = surface_facet(
                    solution, [high_cm], FIELD_UDM_JW, "min", f"{tag}_highcm_jw"
                )
                cm_on_high = surface_facet(
                    solution, [high_cm], FIELD_UDM_CM, "max", f"{tag}_highcm_cm"
                )
                cp_max_highcm = film_theory_cp_perm_mol_m3(
                    cm_on_high, jw_on_high_cm, b_perm
                )
                pairing["jw_min_on_high_cm_band"] = jw_on_high_cm
                pairing["cm_max_on_high_cm_band"] = cm_on_high
                pairing["cp_max_on_high_cm_band"] = cp_max_highcm
            else:
                cp_max_highcm = float("nan")
        else:
            cp_max_highcm = float("nan")
            pairing["area_frac_high_cm_band"] = 0.0

        # Conservative "same-face-ish" lower envelope for cp_max / delta.
        candidates = [
            v
            for v in (cp_max_lowjw, cp_max_highcm)
            if isinstance(v, float) and math.isfinite(v)
        ]
        if candidates:
            cp_max_coloc = max(candidates)
            # Keep cp_min from nonzero-cleaned path when available.
            cp_min_ref = cleaned.get("cp_min", baseline["cp_min"])
            if isinstance(cp_min_ref, float) and math.isfinite(cp_min_ref):
                try:
                    delta_coloc = scalar_rescale_guard_delta(
                        c0, cb, cp_min_ref, cp_max_coloc
                    )
                except ValueError:
                    delta_coloc = float("nan")
            else:
                delta_coloc = float("nan")
            pairing["colocated_cp_max_estimate"] = cp_max_coloc
            pairing["colocated_delta_estimate"] = delta_coloc
            pairing["unpaired_over_colocated_cp_max"] = (
                baseline["cp_max"] / cp_max_coloc
                if cp_max_coloc > 0.0
                else float("nan")
            )

        return {
            "cell_number": cell_number,
            "x_min_m": x_min,
            "x_max_m": x_max,
            "area_m2": area,
            "c_b_mol_m3": cb,
            "c0_mol_m3": c0,
            "cm_avg": cm_avg,
            "cm_min": cm_min,
            "cm_max": cm_max,
            "jw_avg": jw_avg,
            "jw_min": jw_min,
            "jw_max": jw_max,
            "baseline_unpaired": baseline,
            "axis_a": axis_a,
            "axis_b": axis_b,
            "pairing": pairing,
        }
    finally:
        for name in reversed(created):
            try:
                delete_iso_clip(solver, name)
            except Exception:
                pass


def fmt(x: Any, digits: int = 6) -> str:
    if x is None:
        return "None"
    if isinstance(x, float):
        if not math.isfinite(x):
            return "nan"
        return f"{x:.{digits}g}"
    return str(x)


def print_run_tables(run_id: str, payload: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(f"RUN {run_id}")
    print("=" * 78)
    cells = payload.get("cells") or []
    print(
        f"{'cell':>4} {'A_cm0':>8} {'A_cm<1':>8} {'cm_min':>10} {'cm_avg':>10} "
        f"{'cm_avg_nz':>10} {'d_base':>10} {'d_nz':>10}"
    )
    for cell in cells:
        a = cell.get("axis_a") or {}
        excl = a.get("excluding_cm_eq_0") or {}
        print(
            f"{cell.get('cell_number'):>4} "
            f"{fmt(a.get('area_frac_cm_eq_0'), 4):>8} "
            f"{fmt(a.get('area_frac_cm_lt_1'), 4):>8} "
            f"{fmt(a.get('cm_min_all')):>10} "
            f"{fmt(a.get('cm_avg_all')):>10} "
            f"{fmt(excl.get('cm_avg')):>10} "
            f"{fmt((cell.get('baseline_unpaired') or {}).get('delta')):>10} "
            f"{fmt(excl.get('delta')):>10}"
        )

    print(
        f"\n{'cell':>4} {'Jw/Bmin':>10} "
        + " ".join(f"{'A<a='+str(a):>10}" for a in ALPHAS)
        + " "
        + " ".join(f"{'d@a='+str(a):>10}" for a in ALPHAS)
    )
    for cell in cells:
        b = cell.get("axis_b") or {}
        hist = b.get("histogram_cum_area_frac_Jw_lt_alpha_B") or {}
        cuts = b.get("alpha_cuts") or {}
        row = f"{cell.get('cell_number'):>4} {fmt(b.get('jw_over_B_min')):>10}"
        for a in ALPHAS:
            row += f" {fmt(hist.get(str(a)), 4):>10}"
        for a in ALPHAS:
            row += f" {fmt((cuts.get(str(a)) or {}).get('delta')):>10}"
        print(row)

    print(
        f"\nGeometric contact width/pitch = {CONTACT_WIDTH_OVER_PITCH:.4f} "
        f"({100*CONTACT_WIDTH_OVER_PITCH:.2f}% per filament-membrane cut)"
    )
    print(
        f"Guard threshold in force = {CP_SCALAR_RESCALE_GUARD_THRESHOLD:g}; "
        f"discriminability ~ {100*DISCRIMINABILITY:.2f}%"
    )
    win = payload.get("window") or {}
    print(
        "Window: "
        f"delta_max_base={fmt(win.get('delta_max_baseline'))} "
        f"delta_max_nz={fmt(win.get('delta_max_cm_nz'))} "
        f"cp_canon_base={fmt(win.get('cp_canon_window_avg_baseline'))} "
        f"cp_canon_nz={fmt(win.get('cp_canon_window_avg_cm_nz'))}"
    )
    for a in ALPHAS:
        print(
            f"  alpha={a:g}: excl_area_max_cell="
            f"{fmt(win.get(f'excl_area_frac_max_alpha_{a:g}'))} "
            f"delta_max={fmt(win.get(f'delta_max_alpha_{a:g}'))} "
            f"cp_canon={fmt(win.get(f'cp_canon_window_avg_alpha_{a:g}'))}"
        )


def diagnose_run(
    args: argparse.Namespace,
    family: str,
    geo_id: str,
    mesh_id: str,
    run_id: str,
    runs_parent: Path,
) -> dict[str, Any]:
    case_dir = runs_parent / family / geo_id / mesh_id / run_id
    if not case_dir.is_dir():
        # Also accept RO_DATA_ROOT/runs/... when runs_parent is RO_DATA_ROOT.
        alt = runs_parent / "runs" / family / geo_id / mesh_id / run_id
        if alt.is_dir():
            case_dir = alt
        else:
            raise FileNotFoundError(f"Run directory not found: {case_dir}")

    cas = case_dir / f"{geo_id}_{run_id}_final.cas.h5"
    if not cas.is_file():
        raise FileNotFoundError(f"Missing case file: {cas}")

    layout_record, _run_payload = layout_from_run_directory(case_dir)
    layout = layout_record.layout
    window = layout_record.evaluation_window
    eval_cells = window.evaluation_cell_numbers(layout)
    # Domain x0 from manifest extents when present.
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
    prior = load_c_b_from_wide_csv(case_dir)

    print(f"\nLaunching Fluent for {geo_id}/{run_id} ...")
    print(f"  case: {cas}")
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
            cwd=as_fluent_path(case_dir),
        )
        solver = meshing.switch_to_solver()
        meshing = None
        setup = solver.settings.setup
        solution = solver.settings.solution
        solver.settings.file.read_case_data(file_name=as_fluent_path(cas))

        wall_names = collect_membrane_walls(setup)
        print(f"  membrane walls: {wall_names}")

        # Prefer published per-cell c_b; fall back to mid-plane compute when
        # the extract still fails the rescale guard (no wide CSV).
        c_b_by_cell: dict[int, float] = {}
        missing_cb: list[int] = []
        for cell in eval_cells:
            key = f"pp_c_b_midplane_cell_{cell}_mol_m3"
            raw = prior.get(key)
            if raw in (None, ""):
                missing_cb.append(cell)
                continue
            try:
                c_b_by_cell[cell] = float(raw)
            except (TypeError, ValueError):
                missing_cb.append(cell)
        if missing_cb:
            print(
                f"  c_b missing for cells {missing_cb}; "
                "computing mid-plane mixing-cup c_b..."
            )
            computed = compute_c_b_by_cell(
                solver,
                solution,
                boundaries=boundaries,
                eval_cells=eval_cells,
                density=float(run_manifest.get("density_kg_per_m3") or 998.2),
                mw=float(
                    run_manifest.get("molecular_weight_kg_per_mol") or 0.05844
                ),
                channel_height_m=float(
                    run_manifest.get("channel_height_m") or 0.00077
                ),
            )
            c_b_by_cell.update(computed)
        still_missing = [c for c in eval_cells if c not in c_b_by_cell]
        if still_missing:
            raise RuntimeError(f"c_b still missing for cells {still_missing}.")

        cells_out: list[dict[str, Any]] = []
        for cell in eval_cells:
            x_min = boundaries[cell - 1]
            x_max = boundaries[cell]
            cb = float(c_b_by_cell[cell])
            print(f"  cell {cell}: x=[{x_min:.6g},{x_max:.6g}] c_b={cb:.6g}")
            cells_out.append(
                diagnose_cell(
                    solver,
                    solution,
                    cell_number=cell,
                    x_min=x_min,
                    x_max=x_max,
                    wall_names=wall_names,
                    c0=c0,
                    cb=cb,
                    b_perm=b_perm,
                )
            )

        # Window aggregates (area-weighted over evaluation cells).
        areas = {c["cell_number"]: c["area_m2"] for c in cells_out if c.get("area_m2")}
        win: dict[str, Any] = {
            "evaluation_cells": eval_cells,
            "prior_wide_csv": prior.get("path", ""),
            "prior_cp_canon_window_avg": prior.get("cp_canon_window_avg"),
            "prior_delta_max": prior.get("cp_canon_rescale_delta_max"),
        }

        def _max_delta(getter) -> float:
            vals = []
            for c in cells_out:
                v = getter(c)
                if isinstance(v, float) and math.isfinite(v):
                    vals.append(v)
            return max(vals) if vals else float("nan")

        def _win_avg(getter) -> float:
            values = {}
            for c in cells_out:
                v = getter(c)
                if isinstance(v, float) and math.isfinite(v):
                    values[c["cell_number"]] = v
            if not values:
                return float("nan")
            return window_area_weighted_average(values, areas, list(values))

        win["delta_max_baseline"] = _max_delta(
            lambda c: (c.get("baseline_unpaired") or {}).get("delta")
        )
        win["cp_canon_window_avg_baseline"] = _win_avg(
            lambda c: (c.get("baseline_unpaired") or {}).get("cp_canon")
        )
        win["delta_max_cm_nz"] = _max_delta(
            lambda c: ((c.get("axis_a") or {}).get("excluding_cm_eq_0") or {}).get(
                "delta"
            )
        )
        win["cp_canon_window_avg_cm_nz"] = _win_avg(
            lambda c: ((c.get("axis_a") or {}).get("excluding_cm_eq_0") or {}).get(
                "cp_canon"
            )
        )
        for a in ALPHAS:
            key = str(a)
            win[f"delta_max_alpha_{a:g}"] = _max_delta(
                lambda c, k=key: ((c.get("axis_b") or {}).get("alpha_cuts") or {})
                .get(k, {})
                .get("delta")
            )
            win[f"cp_canon_window_avg_alpha_{a:g}"] = _win_avg(
                lambda c, k=key: ((c.get("axis_b") or {}).get("alpha_cuts") or {})
                .get(k, {})
                .get("cp_canon")
            )
            win[f"excl_area_frac_max_alpha_{a:g}"] = _max_delta(
                lambda c, k=key: ((c.get("axis_b") or {}).get("alpha_cuts") or {})
                .get(k, {})
                .get("area_frac_excluded")
            )

        return {
            "run_id": run_id,
            "case_dir": str(case_dir),
            "c0_mol_m3": c0,
            "b_perm": b_perm,
            "cells": cells_out,
            "window": win,
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


def resolve_runs_parent(args: argparse.Namespace) -> Path:
    if args.results_root:
        root = Path(args.results_root)
        if (root / "runs").is_dir() and root.name != "runs":
            return root / "runs"
        return root
    return data_root() / "runs"


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    run_ids = [s.strip() for s in args.run_ids.split(",") if s.strip()]

    try:
        runs_parent = resolve_runs_parent(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Set RO_DATA_ROOT to an absolute path (e.g. C:/ro_data) "
            "or pass --results-root.",
            file=sys.stderr,
        )
        return 2

    print("CP face-filter diagnostic (read-only; production CP path untouched)")
    print(f"  runs_parent = {runs_parent}")
    print(f"  geo = {args.family}/{args.geo_id}/{args.mesh_id}")
    print(f"  run_ids = {run_ids}")
    print(f"  B = {args.b_perm:g} m/s")
    print(f"  alphas = {ALPHAS}")
    print(f"  contact width/pitch = {CONTACT_WIDTH_OVER_PITCH:.4%}")

    if args.dry_run_paths:
        for run_id in run_ids:
            d = runs_parent / args.family / args.geo_id / args.mesh_id / run_id
            cas = d / f"{args.geo_id}_{run_id}_final.cas.h5"
            print(
                f"  {run_id}: dir_exists={d.is_dir()} "
                f"cas_exists={cas.is_file()} path={d}"
            )
        return 0

    report: dict[str, Any] = {
        "script": Path(__file__).name,
        "guard_threshold": CP_SCALAR_RESCALE_GUARD_THRESHOLD,
        "discriminability": DISCRIMINABILITY,
        "b_perm": args.b_perm,
        "alphas": list(ALPHAS),
        "jw_hist_alphas": list(JW_HIST_ALPHAS),
        "contact_width_over_pitch": CONTACT_WIDTH_OVER_PITCH,
        "cm_zero_eps": CM_ZERO_EPS,
        "cm_soft_eps": CM_SOFT_EPS,
        "runs": {},
        "errors": {},
    }

    for run_id in run_ids:
        try:
            payload = diagnose_run(
                args,
                args.family,
                args.geo_id,
                args.mesh_id,
                run_id,
                runs_parent,
            )
            report["runs"][run_id] = payload
            print_run_tables(run_id, payload)
        except Exception as exc:
            report["errors"][run_id] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"\nERROR on {run_id}: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Decision hints (numbers only; no code changes).
    print("\n" + "=" * 78)
    print("DECISION HINTS (from measured numbers)")
    print("=" * 78)
    for run_id, payload in report["runs"].items():
        win = payload.get("window") or {}
        d0 = win.get("delta_max_baseline")
        dnz = win.get("delta_max_cm_nz")
        print(f"\n{run_id}:")
        print(f"  unpaired delta_max          = {fmt(d0)}")
        print(f"  delta_max after drop cm==0  = {fmt(dnz)}")
        if isinstance(dnz, float) and math.isfinite(dnz):
            if dnz < DISCRIMINABILITY / 10.0:
                print(
                    "  -> Axis A alone puts delta ≪ discriminability "
                    "(data hygiene, not a redefinition)."
                )
            elif dnz < DISCRIMINABILITY:
                print(
                    "  -> Axis A alone puts delta under discriminability; "
                    "still check Axis B area fractions."
                )
            else:
                print(
                    "  -> Axis A alone is NOT enough; inspect Axis B alphas."
                )
        for a in ALPHAS:
            print(
                f"  alpha={a:g}: excl_max={fmt(win.get(f'excl_area_frac_max_alpha_{a:g}'))} "
                f"(geom~{CONTACT_WIDTH_OVER_PITCH:.3f}–{3*CONTACT_WIDTH_OVER_PITCH:.3f}) "
                f"delta_max={fmt(win.get(f'delta_max_alpha_{a:g}'))}"
            )

    out_path = args.output_json
    if out_path is None:
        try:
            inv = data_root() / "inventory" / "diagnostics"
        except ValueError:
            inv = project_root() / "_scratch" / "diagnostics"
        inv.mkdir(parents=True, exist_ok=True)
        out_path = str(inv / "cp_face_filter_diagnostic.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nWrote JSON: {out_path}")

    if report["errors"] and not report["runs"]:
        return 1
    if report["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
