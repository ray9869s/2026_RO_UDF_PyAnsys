#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only diagnostic: extent of high Yi_s on membrane walls and in fluid.

Does NOT modify the production CP path, facetmin statistics, or UDF.
Launches Fluent, measures area-/volume-weighted Yi thresholds via iso-clip
(and reduction volume), runs probe_cp_reconstruction, prints tables, writes
JSON, exits.

Purpose: distinguish (a) negligible-area Yi=1 wall artifacts from (b) a
meaningful region above NaCl saturation (Yi ~ 0.26) that would make all
u=0.1 campaign solutions physically suspect regardless of residuals.

Usage (on the CFD host with case data)::

    export RO_DATA_ROOT='C:/ro_data'
    python scripts/diagnose_yi_saturation.py \\
      --family diamond --geo-id D2450_a45 \\
      --mesh-id max085_min006_cpg5_bl4_peel2 \\
      --run-ids u0p1_p6M,u0p2_p6M,u0p3_p6M
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

from ro.domain_layout import layout_from_run_directory
from ro.fluent_report_helpers import (
    compute_surface_report_value,
    create_or_update_surface_field_report,
    create_x_range_iso_clip,
    delete_iso_clip,
    delete_surface_field_report,
    fluid_zone_reduction_locations,
    list_named_object_names,
)
from ro.manifest import read_run_manifest
from ro.paths import data_root, project_root
from ro.solver_common import path_to_fluent_str

# PyFluent is imported inside diagnose_run so --dry-run-paths works without
# an installed Fluent/ansys client on the reviewing host.

SCRIPT_DIR = Path(__file__).resolve().parent

# Species mass-fraction thresholds (area- and volume-weighted).
# 0.26 ~ NaCl saturation at ambient; 0.99 catches clamp-to-1.0 cells.
YI_THRESHOLDS = (0.05, 0.10, 0.26, 0.50, 0.99)
YI_EXTENT_THRESH = 0.26
YI_CLIP_HI = 1.0001  # include faces clamped exactly at 1.0

SALT_FIELD_CANDIDATES = (
    "nacl",
    "mass-fraction-of-nacl",
    "yi-0",
    "species-0",
)

PROBE_TUI = (
    '/define/user-defined/execute-on-demand '
    '"probe_cp_reconstruction::libudf"'
)
PROBE_MARKER = "=== RO_UDF probe_cp_reconstruction ==="


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Read-only Yi_s saturation extent diagnostic "
            "(no production CP path changes)."
        )
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
        help="Override RO_DATA_ROOT/runs parent (absolute).",
    )
    p.add_argument(
        "--output-json",
        default=None,
        help="Write full JSON report here (default: inventory/diagnostics/).",
    )
    p.add_argument("--processor-count", type=int, default=1)
    p.add_argument("--product-version", default="25.1.0")
    p.add_argument("--graphics-driver", default="null")
    p.add_argument("--ui-mode", default="no_gui")
    p.add_argument(
        "--dry-run-paths",
        action="store_true",
        help="Only resolve and print case paths; do not launch Fluent.",
    )
    p.add_argument(
        "--skip-probe",
        action="store_true",
        help="Skip probe_cp_reconstruction (still measures Yi extents).",
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
    name = f"pp_yi_area_{tag}"
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
    report_type = "surface-facetmax" if which == "max" else "surface-facetmin"
    name = f"pp_yi_{which}_{tag}"
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
    except Exception:
        return 0.0
    return max(0.0, float(area))


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
    if walls:
        return sorted(walls)
    return ["wall_top_mem", "wall_bottom_mem"]


def collect_fluid_zones(setup) -> list[str]:
    try:
        fluid_group = setup.cell_zone_conditions.fluid
        return list_named_object_names(
            fluid_group, "setup.cell_zone_conditions.fluid"
        )
    except Exception:
        return []


def discover_salt_field(
    solver, solution, wall_names: list[str]
) -> tuple[str, float]:
    """Return (field_name, total_membrane_area) for the first working species."""
    last_error: Optional[BaseException] = None
    for field in SALT_FIELD_CANDIDATES:
        tag = f"disc_{field}".replace("-", "_")
        try:
            area = surface_area(solution, wall_names, f"{tag}_a")
            if area <= 0.0:
                continue
            # Confirm the field is readable on the walls (facetmax).
            yi_max = surface_facet(solution, wall_names, field, "max", f"{tag}_m")
            if not math.isfinite(yi_max):
                continue
            return field, float(area)
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(
        "Could not resolve a readable salt mass-fraction field on membrane "
        f"walls. Tried {list(SALT_FIELD_CANDIDATES)}. Last error: {last_error}"
    )


def area_fractions_above(
    solver,
    solution,
    *,
    base_surfaces: list[str],
    salt_field: str,
    base_area: float,
    tag: str,
) -> dict[str, Any]:
    """Area-weighted fractions with Yi >= thresh via nested iso_clip."""
    out: dict[str, Any] = {
        "base_area_m2": base_area,
        "area_frac_yi_ge": {},
        "area_m2_yi_ge": {},
    }
    if base_area <= 0.0:
        for thresh in YI_THRESHOLDS:
            out["area_frac_yi_ge"][str(thresh)] = float("nan")
            out["area_m2_yi_ge"][str(thresh)] = 0.0
        return out

    created: list[str] = []
    try:
        for thresh in YI_THRESHOLDS:
            clip = f"pp_yi_ge{thresh:g}_{tag}".replace(".", "p")
            created.append(clip)
            create_field_iso_clip(
                solver, clip, base_surfaces, salt_field, thresh, YI_CLIP_HI
            )
            a_sub = safe_area(solution, [clip], f"{tag}_ge{thresh:g}")
            out["area_m2_yi_ge"][str(thresh)] = a_sub
            out["area_frac_yi_ge"][str(thresh)] = a_sub / base_area
    finally:
        for name in reversed(created):
            try:
                delete_iso_clip(solver, name)
            except Exception:
                pass
    return out


def wall_extent_above(
    solver,
    solution,
    *,
    wall_names: list[str],
    salt_field: str,
    thresh: float,
    tag: str,
) -> dict[str, Any]:
    """Bounding box of membrane-wall faces with Yi >= thresh (iso_clip)."""
    clip = f"pp_yi_ext_{tag}"
    out: dict[str, Any] = {
        "thresh": thresh,
        "area_m2": 0.0,
        "x_min_m": None,
        "x_max_m": None,
        "y_min_m": None,
        "y_max_m": None,
        "z_min_m": None,
        "z_max_m": None,
        "error": "",
    }
    try:
        create_field_iso_clip(
            solver, clip, wall_names, salt_field, thresh, YI_CLIP_HI
        )
        area = safe_area(solution, [clip], f"{tag}_a")
        out["area_m2"] = area
        if area <= 0.0:
            out["note"] = "empty_clip"
            return out
        for coord, key in (
            ("x-coordinate", "x"),
            ("y-coordinate", "y"),
            ("z-coordinate", "z"),
        ):
            lo = surface_facet(solution, [clip], coord, "min", f"{tag}_{key}")
            hi = surface_facet(solution, [clip], coord, "max", f"{tag}_{key}")
            out[f"{key}_min_m"] = lo
            out[f"{key}_max_m"] = hi
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            delete_iso_clip(solver, clip)
        except Exception:
            pass
    return out


def volume_fractions_above(
    solver,
    setup,
    *,
    fluid_zones: list[str],
    species_name: str,
) -> dict[str, Any]:
    """Volume fraction of fluid domain with Yi > thresh (reduction API).

    Prefers sum_if(expression="1", weight="Volume") under a MassFraction
    condition. Also reports cell-count fractions so a broken sum_if still
    yields a usable signal.
    """
    out: dict[str, Any] = {
        "fluid_zones": list(fluid_zones),
        "species_name": species_name,
        "volume_total_m3": None,
        "cell_count_total": None,
        "vol_frac_yi_gt": {},
        "vol_m3_yi_gt": {},
        "cell_frac_yi_gt": {},
        "cell_count_yi_gt": {},
        "yi_min": None,
        "yi_max": None,
        "method": "",
        "errors": [],
    }
    if not fluid_zones:
        out["errors"].append("no_fluid_zones")
        return out

    try:
        locations = fluid_zone_reduction_locations(setup, fluid_zones)
        reduction = solver.fields.reduction
    except Exception as exc:
        out["errors"].append(f"locations: {type(exc).__name__}: {exc}")
        return out

    expression = f'MassFraction(species="{species_name}")'
    try:
        out["yi_min"] = float(
            reduction.minimum(expression=expression, locations=locations)
        )
        out["yi_max"] = float(
            reduction.maximum(expression=expression, locations=locations)
        )
    except Exception as exc:
        out["errors"].append(f"yi_extrema: {type(exc).__name__}: {exc}")

    try:
        out["volume_total_m3"] = float(reduction.volume(locations=locations))
    except Exception as exc:
        out["errors"].append(f"volume: {type(exc).__name__}: {exc}")

    try:
        # Unconditional cell count via count_if True-ish: use Yi > -1.
        out["cell_count_total"] = float(
            reduction.count_if(
                condition=f"{expression} > {-1.0!r}",
                locations=locations,
            )
        )
    except Exception as exc:
        out["errors"].append(f"cell_count: {type(exc).__name__}: {exc}")

    vol_ok = False
    for thresh in YI_THRESHOLDS:
        cond = f"{expression} > {thresh!r}"
        # Cell counts (always useful; area lesson was about facet extrema).
        try:
            n = float(reduction.count_if(condition=cond, locations=locations))
            out["cell_count_yi_gt"][str(thresh)] = n
            total_n = out.get("cell_count_total")
            if isinstance(total_n, float) and total_n > 0.0:
                out["cell_frac_yi_gt"][str(thresh)] = n / total_n
        except Exception as exc:
            out["errors"].append(
                f"count_if>{thresh}: {type(exc).__name__}: {exc}"
            )

        # True volume: sum_if requires weight= on Fluent 25.1 / PyFluent 0.38.
        try:
            v = float(
                reduction.sum_if(
                    expression="1",
                    condition=cond,
                    weight="Volume",
                    locations=locations,
                )
            )
            out["vol_m3_yi_gt"][str(thresh)] = v
            total_v = out.get("volume_total_m3")
            if isinstance(total_v, float) and total_v > 0.0:
                out["vol_frac_yi_gt"][str(thresh)] = v / total_v
                vol_ok = True
        except Exception as exc:
            out["errors"].append(
                f"sum_if_vol>{thresh}: {type(exc).__name__}: {exc}"
            )

    out["method"] = "sum_if_Volume" if vol_ok else "count_if_only"
    return out


def volume_extent_above(
    solver,
    setup,
    *,
    fluid_zones: list[str],
    species_name: str,
    thresh: float,
) -> dict[str, Any]:
    """x/y/z extent of fluid cells with Yi > thresh via reduction min/max."""
    out: dict[str, Any] = {
        "thresh": thresh,
        "x_min_m": None,
        "x_max_m": None,
        "y_min_m": None,
        "y_max_m": None,
        "z_min_m": None,
        "z_max_m": None,
        "error": "",
    }
    if not fluid_zones:
        out["error"] = "no_fluid_zones"
        return out
    try:
        locations = fluid_zone_reduction_locations(setup, fluid_zones)
        reduction = solver.fields.reduction
        expression = f'MassFraction(species="{species_name}")'
        cond = f"{expression} > {thresh!r}"
        for coord_expr, key in (
            ("XCoordinate()", "x"),
            ("YCoordinate()", "y"),
            ("ZCoordinate()", "z"),
        ):
            # Prefer conditional extrema; fall back to unconditional if needed.
            try:
                lo = float(
                    reduction.minimum(
                        expression=coord_expr,
                        condition=cond,
                        locations=locations,
                    )
                )
                hi = float(
                    reduction.maximum(
                        expression=coord_expr,
                        condition=cond,
                        locations=locations,
                    )
                )
            except TypeError:
                # Older reduction APIs may not take condition=.
                lo = float(
                    reduction.minimum(
                        expression=coord_expr, locations=locations
                    )
                )
                hi = float(
                    reduction.maximum(
                        expression=coord_expr, locations=locations
                    )
                )
                out["note"] = "unconditional_domain_bbox_fallback"
            out[f"{key}_min_m"] = lo
            out[f"{key}_max_m"] = hi
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def classify_extent(
    extent: dict[str, Any],
    *,
    domain: dict[str, Any],
    boundaries: list[float],
    eval_cells: list[int],
) -> list[str]:
    """Human-readable location hints from a wall/volume bounding box."""
    hints: list[str] = []
    area = extent.get("area_m2")
    if area is not None and isinstance(area, float) and area <= 0.0:
        hints.append("empty (no faces/cells above threshold)")
        return hints

    x_lo, x_hi = extent.get("x_min_m"), extent.get("x_max_m")
    y_lo, y_hi = extent.get("y_min_m"), extent.get("y_max_m")
    z_lo, z_hi = extent.get("z_min_m"), extent.get("z_max_m")
    if not all(
        isinstance(v, float) and math.isfinite(v)
        for v in (x_lo, x_hi, y_lo, y_hi, z_lo, z_hi)
    ):
        hints.append("extent unavailable")
        return hints

    dx = x_hi - x_lo
    dy = y_hi - y_lo
    dz = z_hi - z_lo
    hints.append(
        f"bbox dx={dx:.4g} m, dy={dy:.4g} m, dz={dz:.4g} m"
    )

    x_dom0 = domain.get("x_min_m")
    x_dom1 = domain.get("x_max_m")
    y_dom0 = domain.get("y_min_m")
    y_dom1 = domain.get("y_max_m")
    z_dom0 = domain.get("z_min_m")
    z_dom1 = domain.get("z_max_m")

    # Outlet / inlet buffer proximity (within last/first ~5% of domain x).
    if (
        isinstance(x_dom0, float)
        and isinstance(x_dom1, float)
        and x_dom1 > x_dom0
    ):
        Lx = x_dom1 - x_dom0
        if x_lo >= x_dom1 - 0.05 * Lx:
            hints.append("near outlet (x high)")
        if x_hi <= x_dom0 + 0.05 * Lx:
            hints.append("near inlet (x low)")

    # Periodic-y edges.
    if (
        isinstance(y_dom0, float)
        and isinstance(y_dom1, float)
        and y_dom1 > y_dom0
    ):
        Ly = y_dom1 - y_dom0
        tol = 0.02 * Ly
        if y_lo <= y_dom0 + tol or y_hi >= y_dom1 - tol:
            hints.append("touches periodic-y boundary")

    # Membrane z faces (channel floor/ceiling).
    if (
        isinstance(z_dom0, float)
        and isinstance(z_dom1, float)
        and z_dom1 > z_dom0
    ):
        Lz = z_dom1 - z_dom0
        tol = 0.05 * Lz
        if z_lo <= z_dom0 + tol:
            hints.append("at/near bottom membrane (z low)")
        if z_hi >= z_dom1 - tol:
            hints.append("at/near top membrane (z high)")
        if dz < 0.15 * Lz:
            hints.append("thin in z (wall-adjacent layer, not bulk)")
        elif dz > 0.5 * Lz:
            hints.append("thick in z (bulk region)")

    # Which evaluation cells the x-span covers.
    covered = []
    if boundaries and len(boundaries) >= 2:
        for cell in eval_cells:
            if cell < 1 or cell >= len(boundaries):
                continue
            c_lo, c_hi = boundaries[cell - 1], boundaries[cell]
            if x_hi >= c_lo and x_lo <= c_hi:
                covered.append(cell)
        if covered:
            hints.append(f"x-span covers eval cells {covered}")

    # Scattered vs compact: large x span across many pitches.
    if (
        isinstance(x_dom0, float)
        and isinstance(x_dom1, float)
        and x_dom1 > x_dom0
        and dx > 0.5 * (x_dom1 - x_dom0)
    ):
        hints.append("scattered or streamwise-extended in x")
    elif dx > 0.0 and dy > 0.0 and dx * dy > 0.0:
        # Very small footprint → contact-like.
        if (
            isinstance(y_dom0, float)
            and isinstance(y_dom1, float)
            and y_dom1 > y_dom0
            and dx < 0.05 * (x_dom1 - x_dom0)
            and dy < 0.15 * (y_dom1 - y_dom0)
        ):
            hints.append("compact footprint (filament-contact scale possible)")

    return hints


def domain_bbox_from_manifest(run_manifest: dict[str, Any]) -> dict[str, Any]:
    """Best-effort domain extents from run manifest (keys vary by vintage)."""
    def _get(*keys: str) -> Optional[float]:
        for k in keys:
            v = run_manifest.get(k)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    x_min = _get("domain_extent_x_min_m") or 0.0
    x_len = _get("domain_extent_x_m")
    x_max = _get("domain_extent_x_max_m")
    if x_max is None and x_len is not None:
        x_max = x_min + x_len

    y_min = _get("domain_extent_y_min_m")
    y_len = _get("domain_extent_y_m")
    y_max = _get("domain_extent_y_max_m")
    if y_min is None and y_len is not None:
        # Often y is periodic about 0; treat [0, Ly] if min unknown.
        y_min = 0.0
        y_max = y_len if y_max is None else y_max
    elif y_max is None and y_min is not None and y_len is not None:
        y_max = y_min + y_len

    z_min = _get("domain_extent_z_min_m")
    z_len = _get("domain_extent_z_m", "channel_height_m")
    z_max = _get("domain_extent_z_max_m")
    if z_min is None:
        z_min = 0.0
    if z_max is None and z_len is not None:
        z_max = z_min + z_len

    return {
        "x_min_m": x_min,
        "x_max_m": x_max,
        "y_min_m": y_min,
        "y_max_m": y_max,
        "z_min_m": z_min,
        "z_max_m": z_max,
        "channel_height_m": _get("channel_height_m", "domain_extent_z_m"),
    }


def run_probe_cp_reconstruction(
    solver,
    case_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    """Execute on-demand probe; capture transcript text."""
    out: dict[str, Any] = {
        "tui": PROBE_TUI,
        "transcript_path": "",
        "console_excerpt": "",
        "cp_raw": None,
        "cp_recon": None,
        "error": "",
    }
    diag_dir = case_dir / "post" / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = diag_dir / f"probe_cp_reconstruction_{run_id}.txt"
    out["transcript_path"] = str(transcript_path)

    # Ensure libudf is loaded (saved cases usually already have it).
    try:
        solver.tui.define.user_defined.compiled_functions("load", "libudf")
    except Exception as exc:
        # Already loaded is fine; record and continue.
        out["load_note"] = f"{type(exc).__name__}: {exc}"

    started = False
    try:
        solver.transcript.start(file_name=path_to_fluent_str(transcript_path))
        started = True
    except Exception as exc:
        out["error"] = f"transcript.start: {type(exc).__name__}: {exc}"

    try:
        solver.execute_tui(PROBE_TUI)
    except Exception as exc:
        out["error"] = (
            (out["error"] + "; " if out["error"] else "")
            + f"execute_tui: {type(exc).__name__}: {exc}"
        )
    finally:
        if started:
            try:
                solver.transcript.stop()
            except Exception:
                pass

    text = ""
    if transcript_path.is_file():
        try:
            text = transcript_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            out["error"] = (
                (out["error"] + "; " if out["error"] else "")
                + f"read: {type(exc).__name__}: {exc}"
            )

    # Keep a focused excerpt around the probe marker.
    if PROBE_MARKER in text:
        idx = text.rfind(PROBE_MARKER)
        excerpt = text[idx : idx + 800]
    else:
        excerpt = text[-1200:] if text else ""
    out["console_excerpt"] = excerpt

    m_raw = re.search(
        r"CP raw \(cell-centre\)\s*=\s*([-+0-9.eE]+)", excerpt
    )
    m_recon = re.search(
        r"CP reconstructed\s*=\s*([-+0-9.eE]+)", excerpt
    )
    if m_raw:
        out["cp_raw"] = float(m_raw.group(1))
    if m_recon:
        out["cp_recon"] = float(m_recon.group(1))
    if PROBE_MARKER not in text and not out["error"]:
        out["error"] = "probe marker not found in transcript"
    return out


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
    print(f"  salt_field = {payload.get('salt_field')}")
    print(
        f"  membrane area = {fmt(payload.get('membrane_area_m2'))} m2; "
        f"fluid zones = {payload.get('fluid_zones')}"
    )

    wall = payload.get("wall_domain") or {}
    fracs = wall.get("area_frac_yi_ge") or {}
    areas = wall.get("area_m2_yi_ge") or {}
    print("\n  Membrane-wall AREA fractions (iso_clip on species, whole mem):")
    print(f"  {'thresh':>8} {'area_frac':>12} {'area_m2':>14}")
    for thresh in YI_THRESHOLDS:
        key = str(thresh)
        print(
            f"  {thresh:>8g} {fmt(fracs.get(key), 4):>12} "
            f"{fmt(areas.get(key)):>14}"
        )

    print("\n  Per evaluation cell (membrane walls, iso_clip):")
    hdr = f"  {'cell':>4} {'A_tot':>10}"
    for thresh in YI_THRESHOLDS:
        hdr += f" {'A>={thresh:g}':>10}"
    print(hdr)
    for cell in payload.get("cells") or []:
        row = (
            f"  {cell.get('cell_number'):>4} "
            f"{fmt((cell.get('wall') or {}).get('base_area_m2')):>10}"
        )
        fr = (cell.get("wall") or {}).get("area_frac_yi_ge") or {}
        for thresh in YI_THRESHOLDS:
            row += f" {fmt(fr.get(str(thresh)), 4):>10}"
        print(row)

    vol = payload.get("volume") or {}
    print(
        f"\n  Fluid VOLUME fractions "
        f"(method={vol.get('method')}; "
        f"V_tot={fmt(vol.get('volume_total_m3'))} m3; "
        f"Yi_max={fmt(vol.get('yi_max'))}):"
    )
    print(
        f"  {'thresh':>8} {'vol_frac':>12} {'cell_frac':>12} "
        f"{'n_cells':>10}"
    )
    vf = vol.get("vol_frac_yi_gt") or {}
    cf = vol.get("cell_frac_yi_gt") or {}
    nc = vol.get("cell_count_yi_gt") or {}
    for thresh in YI_THRESHOLDS:
        key = str(thresh)
        print(
            f"  {thresh:>8g} {fmt(vf.get(key), 4):>12} "
            f"{fmt(cf.get(key), 4):>12} {fmt(nc.get(key), 4):>10}"
        )
    if vol.get("errors"):
        print(f"  volume errors: {vol['errors'][:4]}")

    print(f"\n  Extent of region with Yi >= {YI_EXTENT_THRESH:g}:")
    for label, key in (
        ("wall faces", "wall_extent_yi_ge_0p26"),
        ("fluid cells", "volume_extent_yi_gt_0p26"),
    ):
        ext = payload.get(key) or {}
        print(
            f"    {label}: area={fmt(ext.get('area_m2'))} "
            f"x=[{fmt(ext.get('x_min_m'))},{fmt(ext.get('x_max_m'))}] "
            f"y=[{fmt(ext.get('y_min_m'))},{fmt(ext.get('y_max_m'))}] "
            f"z=[{fmt(ext.get('z_min_m'))},{fmt(ext.get('z_max_m'))}]"
        )
        for hint in ext.get("location_hints") or []:
            print(f"      - {hint}")
        if ext.get("error"):
            print(f"      error: {ext['error']}")

    probe = payload.get("probe") or {}
    print("\n  probe_cp_reconstruction:")
    print(f"    cp_raw   = {fmt(probe.get('cp_raw'))}")
    print(f"    cp_recon = {fmt(probe.get('cp_recon'))}")
    if probe.get("error"):
        print(f"    error: {probe['error']}")
    excerpt = (probe.get("console_excerpt") or "").strip()
    if excerpt:
        print("    --- transcript excerpt ---")
        for line in excerpt.splitlines():
            print(f"    {line}")
        print("    --- end excerpt ---")


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
    run_manifest = read_run_manifest(case_dir)
    x0 = float(run_manifest.get("domain_extent_x_min_m") or 0.0)
    boundaries = layout.boundary_positions(x0)
    domain = domain_bbox_from_manifest(run_manifest)

    print(f"\nLaunching Fluent for {geo_id}/{run_id} ...")
    print(f"  case: {cas}")
    print(f"  eval cells: {eval_cells}")
    import ansys.fluent.core as pyfluent  # noqa: PLC0415 — Fluent host only

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

        wall_names = collect_membrane_walls(setup)
        fluid_zones = collect_fluid_zones(setup)
        print(f"  membrane walls: {wall_names}")
        print(f"  fluid zones: {fluid_zones}")

        salt_field, membrane_area = discover_salt_field(
            solver, solution, wall_names
        )
        print(
            f"  salt field = {salt_field!r}; "
            f"membrane area = {membrane_area:.6g} m2"
        )
        # Species name for MassFraction(...) — usually the same token as
        # the surface field ("nacl").
        species_name = salt_field
        if salt_field.startswith("mass-fraction-of-"):
            species_name = salt_field[len("mass-fraction-of-") :]
        elif salt_field in ("yi-0", "species-0"):
            species_name = "nacl"

        wall_domain = area_fractions_above(
            solver,
            solution,
            base_surfaces=wall_names,
            salt_field=salt_field,
            base_area=membrane_area,
            tag="dom",
        )

        cells_out: list[dict[str, Any]] = []
        for cell in eval_cells:
            x_min = boundaries[cell - 1]
            x_max = boundaries[cell]
            tag = f"c{cell}"
            x_clip = f"pp_yi_x_{tag}"
            try:
                create_x_range_iso_clip(
                    solver, x_clip, wall_names, x_min, x_max
                )
                cell_area = safe_area(solution, [x_clip], f"{tag}_tot")
                wall_cell = area_fractions_above(
                    solver,
                    solution,
                    base_surfaces=[x_clip],
                    salt_field=salt_field,
                    base_area=cell_area,
                    tag=tag,
                )
            finally:
                try:
                    delete_iso_clip(solver, x_clip)
                except Exception:
                    pass
            cells_out.append(
                {
                    "cell_number": cell,
                    "x_min_m": x_min,
                    "x_max_m": x_max,
                    "wall": wall_cell,
                }
            )
            print(
                f"  cell {cell}: area={cell_area:.4g} "
                f"A(Yi>=0.26)={fmt((wall_cell.get('area_frac_yi_ge') or {}).get('0.26'), 4)} "
                f"A(Yi>=0.99)={fmt((wall_cell.get('area_frac_yi_ge') or {}).get('0.99'), 4)}"
            )

        wall_extent = wall_extent_above(
            solver,
            solution,
            wall_names=wall_names,
            salt_field=salt_field,
            thresh=YI_EXTENT_THRESH,
            tag="ext026",
        )
        wall_extent["location_hints"] = classify_extent(
            wall_extent,
            domain=domain,
            boundaries=boundaries,
            eval_cells=eval_cells,
        )

        volume = volume_fractions_above(
            solver,
            setup,
            fluid_zones=fluid_zones,
            species_name=species_name,
        )
        vol_extent = volume_extent_above(
            solver,
            setup,
            fluid_zones=fluid_zones,
            species_name=species_name,
            thresh=YI_EXTENT_THRESH,
        )
        vol_extent["location_hints"] = classify_extent(
            vol_extent,
            domain=domain,
            boundaries=boundaries,
            eval_cells=eval_cells,
        )

        if args.skip_probe:
            probe = {
                "skipped": True,
                "cp_raw": None,
                "cp_recon": None,
                "console_excerpt": "",
                "error": "",
            }
        else:
            print("  running probe_cp_reconstruction ...")
            probe = run_probe_cp_reconstruction(solver, case_dir, run_id)
            if probe.get("console_excerpt"):
                print(probe["console_excerpt"])

        return {
            "case_dir": str(case_dir),
            "cas": str(cas),
            "eval_cells": list(eval_cells),
            "boundaries_m": list(boundaries),
            "domain": domain,
            "salt_field": salt_field,
            "species_name": species_name,
            "membrane_walls": wall_names,
            "fluid_zones": fluid_zones,
            "membrane_area_m2": membrane_area,
            "wall_domain": wall_domain,
            "cells": cells_out,
            "wall_extent_yi_ge_0p26": wall_extent,
            "volume": volume,
            "volume_extent_yi_gt_0p26": vol_extent,
            "probe": probe,
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


def decision_hints(report: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print("DECISION HINTS (a vs b)")
    print("=" * 78)
    print(
        "  (a) handful of degenerate cells, negligible area "
        "→ facetmin/delta hygiene; clamp note in docs."
    )
    print(
        "  (b) meaningful area above Yi=0.26 "
        "→ u=0.1 solution physically wrong; campaign u=0.1 suspect."
    )
    for run_id, payload in report.get("runs", {}).items():
        wall = payload.get("wall_domain") or {}
        fr = wall.get("area_frac_yi_ge") or {}
        ar = wall.get("area_m2_yi_ge") or {}
        a26 = fr.get("0.26")
        a99 = fr.get("0.99")
        vol = payload.get("volume") or {}
        vf26 = (vol.get("vol_frac_yi_gt") or {}).get("0.26")
        cf26 = (vol.get("cell_frac_yi_gt") or {}).get("0.26")
        print(f"\n{run_id}:")
        print(
            f"  wall A(Yi>=0.26)={fmt(a26, 4)} "
            f"({fmt(ar.get('0.26'))} m2); "
            f"A(Yi>=0.99)={fmt(a99, 4)} "
            f"({fmt(ar.get('0.99'))} m2)"
        )
        print(
            f"  fluid vol_frac(Yi>0.26)={fmt(vf26, 4)}; "
            f"cell_frac={fmt(cf26, 4)}; Yi_max={fmt(vol.get('yi_max'))}"
        )
        # Per-cell where Yi>=0.99 sits.
        hot = []
        for cell in payload.get("cells") or []:
            cfr = (cell.get("wall") or {}).get("area_frac_yi_ge") or {}
            if (cfr.get("0.99") or 0.0) > 0.0 or (cfr.get("0.26") or 0.0) > 1e-6:
                hot.append(
                    f"c{cell.get('cell_number')}:"
                    f"A26={fmt(cfr.get('0.26'), 3)},"
                    f"A99={fmt(cfr.get('0.99'), 3)}"
                )
        if hot:
            print(f"  hot eval cells: {', '.join(hot)}")
        else:
            print("  hot eval cells: none above thresholds")

        # Crude (a)/(b) call on wall area only.
        if isinstance(a26, float) and math.isfinite(a26):
            if a26 < 1e-4 and (not isinstance(a99, float) or a99 < 1e-4):
                print("  -> leans (a): wall area above sat is negligible")
            elif a26 >= 0.01 or (isinstance(a99, float) and a99 >= 0.001):
                print("  -> leans (b): meaningful wall area above saturation")
            else:
                print(
                    "  -> intermediate: small but non-zero wall area; "
                    "inspect per-cell table and extent hints"
                )


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

    print("Yi_s saturation diagnostic (read-only; production CP path untouched)")
    print(f"  runs_parent = {runs_parent}")
    print(f"  geo = {args.family}/{args.geo_id}/{args.mesh_id}")
    print(f"  run_ids = {run_ids}")
    print(f"  Yi thresholds = {list(YI_THRESHOLDS)}")
    print(f"  extent thresh = {YI_EXTENT_THRESH}")

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
        "yi_thresholds": list(YI_THRESHOLDS),
        "yi_extent_thresh": YI_EXTENT_THRESH,
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
            print(
                f"\nERROR on {run_id}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if report["runs"]:
        decision_hints(report)
    else:
        print("\n" + "=" * 78)
        print("DIAGNOSTIC FAILED: no runs succeeded")
        print("=" * 78)
        for run_id, err in report["errors"].items():
            print(
                f"  {run_id}: {err.get('type')}: {err.get('message')}",
                file=sys.stderr,
            )

    out_path = args.output_json
    if out_path is None:
        try:
            inv = data_root() / "inventory" / "diagnostics"
        except ValueError:
            inv = project_root() / "_scratch" / "diagnostics"
        inv.mkdir(parents=True, exist_ok=True)
        out_path = str(inv / "yi_saturation_diagnostic.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nWrote JSON: {out_path}")

    n_ok = len(report["runs"])
    n_err = len(report["errors"])
    if n_ok == 0:
        print(
            f"FAILED: 0/{n_ok + n_err} runs succeeded "
            f"({n_err} error(s)). See JSON for tracebacks.",
            file=sys.stderr,
        )
        return 1
    if n_err:
        print(
            f"PARTIAL: {n_ok}/{n_ok + n_err} runs succeeded "
            f"({n_err} error(s)).",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {n_ok}/{n_ok} runs succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
