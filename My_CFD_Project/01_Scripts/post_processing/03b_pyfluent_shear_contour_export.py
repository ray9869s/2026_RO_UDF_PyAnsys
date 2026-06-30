# -*- coding: utf-8 -*-
"""
03b_pyfluent_shear_contour_export.py

Export a membrane wall shear-rate contour image using PyFluent (Fluent solver).

WHY THIS SCRIPT EXISTS:
  wall-shear is not present in the EnSight variable inventory for this case.
  03_pyensight_contour_export.py records STATUS_WARN with
  derived_variable_mode="wall_shear_unavailable" when shear_rate is requested.
  This script accesses the Fluent solver directly, where wall-shear is always
  available as a built-in wall boundary result.

Formula:
  wall_shear_rate [1/s] = wall-shear [Pa] / mu [Pa·s]
  - wall-shear : Fluent built-in wall shear stress magnitude
  - mu         : dynamic viscosity from 00_post_config.py (default 8.93e-4 Pa·s)

  UDM_10 (cell_strain_rate) is NOT used.  Cell strain rate is NOT used.
  UDM_10 is cell-centered strain rate magnitude and is intentionally not used
  for wall shear-rate contouring.

Paths:
  CFF direct:      <figures_dir>/<geo>_<case>_shear_rate_membrane_<side>.png
  Fallback (mpl):  <figures_dir>/<geo>_<case>_shear_rate_membrane_<side>.png
  Status JSON:     <figures_dir>/shear_contour_status.json

Usage:
  python 03b_pyfluent_shear_contour_export.py \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M \\
      --membrane-surface top --background white \\
      --view-margin 1.20 --width 1600 --height 1200
  python 03b_pyfluent_shear_contour_export.py --dry-run \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03b_pyfluent_shear_contour_export.py --membrane-surface both
  python 03b_pyfluent_shear_contour_export.py --shear-range 0,5000
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ansys.fluent.core as pyfluent
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri

# ---------------------------------------------------------------------------
# Paths — resolved from this script's own location (WSL/Linux-safe)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

# Fluent CFF and contour object names
CFF_NAME = "cff_shear_rate"
CONTOUR_NAME = "pp_shear_rate"

# Candidate Fluent scalar variable names for wall shear stress magnitude.
# These are searched against the Fluent field_info registry; only confirmed
# names are used.  DO NOT assume "wall-shear" works in CFF expressions —
# Fluent's Scheme evaluator may parse hyphens as subtraction operators.
SHEAR_FIELD_CANDIDATES = [
    "wall-shear",
    "wall_shear",
    "wall-shear-stress",
    "wall-shear-stress-magnitude",
    "wall-shear-magnitude",
    "Wall Shear Stress",
    "wall shear",
]


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

def _has_windows_drive(p: Path) -> bool:
    return any(re.match(r"^[A-Za-z]:$", part) for part in p.parts)


def _safe_mkdir(p: Path) -> bool:
    if platform.system() != "Windows" and _has_windows_drive(p):
        return False
    p.mkdir(parents=True, exist_ok=True)
    return True


def as_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


def as_fluent_path(path: Any) -> str:
    return os.path.abspath(str(path)).replace("\\", "/")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_python_config(config_path: Path) -> Any:
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build module spec: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


# ---------------------------------------------------------------------------
# Path building
# ---------------------------------------------------------------------------

def get_case_paths(
    cfg: Any,
    geo_name_override: Optional[str] = None,
    case_name_override: Optional[str] = None,
) -> dict:
    project_root = as_path(cfg_get(cfg, "project_root", PROJECT_ROOT_DEFAULT)).resolve()
    geo_name = geo_name_override or str(cfg_get(cfg, "geo_name", ""))
    case_name = case_name_override or str(cfg_get(cfg, "case_name", ""))

    if not geo_name or geo_name == "===== Edit here =====":
        raise ValueError("geo_name is unset. Pass --geo-name or edit 00_post_config.py.")
    if not case_name or case_name == "===== Edit here =====":
        raise ValueError("case_name is unset. Pass --case-name or edit 00_post_config.py.")

    results_root = as_path(cfg_get(cfg, "results_dir", project_root / "03_Results"))
    case_path = results_root / geo_name / case_name

    final_case_file = as_path(
        cfg_get(cfg, "final_case_file", case_path / f"{geo_name}_{case_name}_final.cas.h5")
    )
    final_data_file = as_path(
        cfg_get(cfg, "final_data_file", case_path / f"{geo_name}_{case_name}_final.dat.h5")
    )
    figures_dir = case_path / "post" / "figures" / "contours"

    return {
        "project_root": project_root,
        "geo_name": geo_name,
        "case_name": case_name,
        "case_path": case_path,
        "final_case_file": final_case_file,
        "final_data_file": final_data_file,
        "figures_dir": figures_dir,
    }


# ---------------------------------------------------------------------------
# Zone helpers
# ---------------------------------------------------------------------------

def _zone_matches_base(zone_name: str, base_name: str) -> bool:
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def find_zones_by_base_names(zone_names: List[str], base_names: List[str]) -> List[str]:
    return sorted({n for n in zone_names for b in base_names if _zone_matches_base(n, b)})


def list_named_object_names(named_object: Any, label: str = "") -> List[str]:
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass
    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(state.keys())
        if isinstance(state, list):
            return list(state)
    except Exception:
        pass
    try:
        names = named_object.list_1()
        if names is not None:
            return list(names)
    except Exception as exc:
        if label:
            print(f"  WARN: could not list names for '{label}': {exc}")
    return []


def collect_wall_zones(setup: Any) -> List[str]:
    try:
        return list_named_object_names(setup.boundary_conditions.wall, "wall")
    except Exception as exc:
        print(f"  WARN: collect_wall_zones: {exc}")
        return []


# ---------------------------------------------------------------------------
# Diagnostic: detect shear-related field and CFF candidates
# ---------------------------------------------------------------------------

def detect_shear_candidates(solver: Any) -> Tuple[List[str], List[str], List[str]]:
    """Return (scalar_candidates, cff_candidates, all_scalar_names_head).

    scalar_candidates:    solverName keys from field_info that match shear/wall keywords.
    cff_candidates:       names from list_valid_cell_function_names that match.
    all_scalar_names_head: first 100 scalar field names when scalar_candidates is empty
                           (populated so a single server run reveals the actual names);
                           empty list when scalar_candidates is non-empty.
    """
    scalar_candidates: List[str] = []
    cff_candidates: List[str] = []
    all_scalar_names_head: List[str] = []

    # Method 1: enumerate scalar fields via field_data field_info
    print("  [Diag] Enumerating scalar field names via field_info ...")
    try:
        field_info = solver.fields.field_data._field_info
        all_fields = field_info._get_scalar_fields_info()
        kw = {"wall", "shear"}
        scalar_candidates = [
            name for name in all_fields
            if any(k in name.lower() for k in kw)
        ]
        print(f"  [Diag] Wall/shear scalar fields ({len(scalar_candidates)}): {scalar_candidates}")
        if not scalar_candidates:
            all_scalar_names_head = list(all_fields.keys())[:100]
            print(f"  [Diag] (No wall/shear match — full scalar field list head): "
                  f"{all_scalar_names_head!r}")
    except Exception as exc:
        print(f"  [Diag] scalar field enumeration failed: {exc}")

    # Method 2: list valid CFF cell function names
    print("  [Diag] Listing valid CFF cell function names ...")
    try:
        result = solver.tui.define.custom_field_functions.list_valid_cell_function_names()
        raw = str(result) if result is not None else ""
        # The output is typically a multiline string of names
        tokens = re.split(r"[\s,]+", raw)
        cff_candidates = [
            t for t in tokens
            if t and any(k in t.lower() for k in ("wall", "shear"))
        ]
        print(f"  [Diag] Wall/shear CFF candidates ({len(cff_candidates)}): {cff_candidates}")
        if not cff_candidates:
            print(f"  [Diag] (Full CFF list head): {raw[:500]!r}")
    except Exception as exc:
        print(f"  [Diag] CFF name listing failed: {exc}")

    return scalar_candidates, cff_candidates, all_scalar_names_head


# ---------------------------------------------------------------------------
# CFF direct path
# ---------------------------------------------------------------------------

def _attempt_cff_define(solver: Any, cff_expr_var: str, mu: float) -> str:
    """Try to define a CFF with one expression variable. Returns expression on success."""
    definition = f"{cff_expr_var} / {mu:.6e}"
    try:
        solver.tui.define.custom_field_functions.delete(CFF_NAME)
    except Exception:
        pass
    solver.tui.define.custom_field_functions.define(CFF_NAME, definition)
    print(f"  CFF defined: {CFF_NAME!r} = {definition!r}")
    return definition


def try_cff_path(
    solver: Any,
    mu: float,
    scalar_candidates: List[str],
    cff_candidates: List[str],
    membrane_zones: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
) -> Tuple[bool, str, str, str]:
    """Attempt CFF-based contour export.

    Returns (success, cff_status, used_var, error_msg).
    success=True means the output_file was written.
    """
    # Build ordered list of candidates to try in the CFF expression.
    # Prefer names confirmed from list_valid_cell_function_names, then
    # from scalar_fields_info, then the hardcoded fallback list.
    ordered: List[str] = []
    for c in cff_candidates:
        if c not in ordered:
            ordered.append(c)
    for c in scalar_candidates:
        if c not in ordered:
            ordered.append(c)
    for c in SHEAR_FIELD_CANDIDATES:
        if c not in ordered:
            ordered.append(c)

    print(f"  CFF candidates to try: {ordered[:10]}")

    last_exc = "No candidates available"
    for var_name in ordered:
        print(f"  Trying CFF expression variable: {var_name!r}")
        try:
            definition = _attempt_cff_define(solver, var_name, mu)
            # CFF defined — now export the contour
            _export_contour_cff(
                solver, membrane_zones, shear_range, output_file, image_width, image_height
            )
            if output_file.is_file():
                return True, "SUCCESS", var_name, ""
            last_exc = "File not written after contour export"
        except Exception as exc:
            last_exc = f"{type(exc).__name__}: {exc}"
            print(f"  CFF with {var_name!r} failed: {last_exc}")

    return False, "FAILED", "", last_exc


def _export_contour_cff(
    solver: Any,
    membrane_zones: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
) -> None:
    """Create Fluent contour using CFF_NAME, display, and save. Raises on failure."""
    if output_file.exists():
        output_file.unlink()

    graphics = solver.settings.results.graphics

    try:
        existing = list_named_object_names(graphics.contour, "graphics.contour")
        if CONTOUR_NAME in existing:
            graphics.contour[CONTOUR_NAME].delete()
    except Exception:
        pass

    contour = graphics.contour.create(CONTOUR_NAME)
    contour.field = CFF_NAME
    contour.surfaces_list = list(membrane_zones)

    if shear_range is not None:
        contour.range_options.auto_range = False
        contour.range_options.minimum = float(shear_range[0])
        contour.range_options.maximum = float(shear_range[1])
        print(f"  Contour range: [{shear_range[0]}, {shear_range[1]}] 1/s (fixed)")
    else:
        contour.range_options.auto_range = True
        contour.range_options.global_range = True
        print("  Contour range: auto (global)")

    print(f"  Displaying on: {membrane_zones}")
    contour.display()

    pic = solver.settings.results.graphics.picture
    pic.x_resolution = image_width
    pic.y_resolution = image_height

    output_posix = as_fluent_path(output_file)
    try:
        pic.save_picture(file_name=output_posix)
    except Exception as exc_settings:
        print(f"  WARN: picture.save_picture error ({exc_settings}); trying TUI")
        try:
            solver.tui.display.save_picture(output_posix)
        except Exception as exc_tui:
            raise RuntimeError(
                "Both save_picture approaches failed.\n"
                f"  Settings API: {exc_settings}\n"
                f"  TUI:          {exc_tui}"
            ) from exc_tui

    if not output_file.is_file():
        raise RuntimeError(
            f"save_picture appeared to succeed but file is missing: {output_file}"
        )
    print(f"  CFF contour image saved: {output_file}")


# ---------------------------------------------------------------------------
# Field-data fallback (matplotlib)
# ---------------------------------------------------------------------------

def _find_valid_scalar_field(
    solver: Any,
    scalar_candidates: List[str],
    surface: str,
) -> Optional[str]:
    """Return the first scalar field name that successfully returns data."""
    # Build candidates: confirmed from field_info first, then hardcoded list
    ordered: List[str] = list(scalar_candidates)
    for c in SHEAR_FIELD_CANDIDATES:
        if c not in ordered:
            ordered.append(c)

    for name in ordered:
        print(f"  Trying scalar field: {name!r}")
        try:
            data = solver.fields.field_data.get_scalar_field_data(
                field_name=name,
                surfaces=[surface],
                node_value=False,
                boundary_value=True,
            )
            if data:
                sizes = {k: np.asarray(v).size for k, v in data.items()}
                print(f"  Field {name!r} OK: {sizes}")
                return name
        except Exception as exc:
            print(f"  Field {name!r} failed: {exc}")
    return None


def try_field_data_fallback(
    solver: Any,
    mu: float,
    membrane_zones: List[str],
    scalar_candidates: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
    background: str,
) -> Tuple[bool, str, str, str]:
    """Retrieve wall-shear field data, compute shear_rate=wall_shear/mu, save PNG.

    Returns (success, fallback_status, used_var, error_msg).
    """
    from ansys.fluent.core.field_data_interfaces import SurfaceDataType

    if output_file.exists():
        output_file.unlink()

    # Use the first (or only) surface — if --membrane-surface top/bottom
    surface = membrane_zones[0]

    # Confirm which scalar field name works
    found_field = _find_valid_scalar_field(solver, scalar_candidates, surface)
    if found_field is None:
        msg = "No valid wall-shear scalar field found in field_data"
        print(f"  FALLBACK: {msg}")
        return False, "FAILED", "", msg

    # Gather scalar data from all selected zones
    all_values_list: List[np.ndarray] = []
    all_coords_list: List[np.ndarray] = []

    for zone in membrane_zones:
        print(f"  Fetching scalar data for zone: {zone}")
        try:
            scalar_data = solver.fields.field_data.get_scalar_field_data(
                field_name=found_field,
                surfaces=[zone],
                node_value=False,
                boundary_value=True,
            )
        except Exception as exc:
            print(f"  WARN: get_scalar_field_data on {zone}: {exc}")
            continue

        print(f"  Fetching centroid coordinates for zone: {zone}")
        try:
            surf_data = solver.fields.field_data.get_surface_data(
                data_types=[SurfaceDataType.FacesCentroid],
                surfaces=[zone],
            )
        except Exception as exc:
            print(f"  WARN: get_surface_data on {zone}: {exc}")
            continue

        # Concatenate data from all surface_id entries
        for sid, arr in scalar_data.items():
            vals = np.asarray(arr).ravel()
            all_values_list.append(vals)

        for sid, type_dict in surf_data.items():
            centroids = np.asarray(type_dict[SurfaceDataType.FacesCentroid])
            if centroids.ndim == 1:
                centroids = centroids.reshape(-1, 3)
            all_coords_list.append(centroids)

    if not all_values_list or not all_coords_list:
        msg = "No data retrieved from field_data for any membrane zone"
        return False, "FAILED", found_field, msg

    all_values = np.concatenate(all_values_list)
    all_coords = np.concatenate(all_coords_list, axis=0)

    # Sanity: lengths must match (face-center values vs. face centroids)
    min_len = min(len(all_values), len(all_coords))
    if len(all_values) != len(all_coords):
        print(
            f"  WARN: value count ({len(all_values)}) != centroid count ({len(all_coords)}); "
            f"truncating to {min_len}"
        )
        all_values = all_values[:min_len]
        all_coords = all_coords[:min_len]

    # Compute shear rate [1/s] = wall_shear [Pa] / mu [Pa·s]
    shear_rate = all_values / mu
    print(
        f"  shear_rate stats: min={shear_rate.min():.3f}, "
        f"max={shear_rate.max():.3f}, mean={shear_rate.mean():.3f} [1/s]"
    )

    # Auto-detect in-plane axes (largest spread → x_plot, y_plot)
    spreads = [np.ptp(all_coords[:, i]) for i in range(3)]
    sorted_axes = sorted(range(3), key=lambda i: spreads[i], reverse=True)
    ax1, ax2 = sorted_axes[0], sorted_axes[1]
    ax_labels = ["x [m]", "y [m]", "z [m]"]
    x_plot = all_coords[:, ax1]
    y_plot = all_coords[:, ax2]

    # Colorbar range
    vmin = float(shear_range[0]) if shear_range is not None else float(shear_rate.min())
    vmax = float(shear_range[1]) if shear_range is not None else float(shear_rate.max())

    # Attempt tripcolor (triangulation from face centroids); fall back to scatter
    fig_w = image_width / 100.0
    fig_h = image_height / 100.0
    bg_color = "white" if background == "white" else "black"
    fg_color = "black" if background == "white" else "white"

    fig, ax_mpl = plt.subplots(figsize=(fig_w, fig_h), facecolor=bg_color)
    ax_mpl.set_facecolor(bg_color)

    try:
        triang = mtri.Triangulation(x_plot, y_plot)
        tcf = ax_mpl.tripcolor(
            triang, shear_rate, shading="flat", cmap="rainbow", vmin=vmin, vmax=vmax
        )
        plot_type = "tripcolor"
    except Exception as exc_tri:
        print(f"  tripcolor failed ({exc_tri}); using scatter")
        point_size = max(0.1, 72 * min(spreads[ax1], spreads[ax2]) / len(x_plot) ** 0.5)
        tcf = ax_mpl.scatter(
            x_plot, y_plot, c=shear_rate, cmap="rainbow",
            s=point_size, vmin=vmin, vmax=vmax, linewidths=0
        )
        plot_type = "scatter"

    cbar = fig.colorbar(tcf, ax=ax_mpl)
    cbar.set_label("Wall shear rate [1/s]", color=fg_color)
    cbar.ax.yaxis.set_tick_params(color=fg_color)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=fg_color)

    ax_mpl.set_xlabel(ax_labels[ax1], color=fg_color)
    ax_mpl.set_ylabel(ax_labels[ax2], color=fg_color)
    ax_mpl.tick_params(colors=fg_color)
    for spine in ax_mpl.spines.values():
        spine.set_edgecolor(fg_color)

    zone_label = ", ".join(membrane_zones)
    ax_mpl.set_title(
        f"Wall shear rate: wall-shear / mu,  mu = {mu:.6e} Pa·s\n"
        f"Zones: {zone_label}  |  {plot_type}",
        color=fg_color,
    )

    _safe_mkdir(output_file.parent)
    fig.savefig(str(output_file), dpi=100, bbox_inches="tight", facecolor=bg_color)
    plt.close(fig)

    if not output_file.is_file():
        return False, "FAILED", found_field, "matplotlib savefig produced no file"

    print(f"  Fallback PNG saved: {output_file}")
    return True, "SUCCESS", found_field, ""


# ---------------------------------------------------------------------------
# Status JSON
# ---------------------------------------------------------------------------

STATUS_OK   = "SUCCESS"
STATUS_FAIL = "FAILED"
STATUS_DRY  = "DRY_RUN"
STATUS_SKIP = "SKIPPED_EXISTING"
STATUS_WARN = "WARN"


def write_status_json(status_file: Path, payload: Dict[str, Any]) -> None:
    _safe_mkdir(status_file.parent)
    with status_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"Status written: {status_file}")


def build_status_payload(
    geo_name: str,
    case_name: str,
    status: str,
    selected_surfaces: List[str],
    mu_used: float,
    output_files: List[Path],
    message: str,
    selected_variable: Optional[str],
    derived_variable_mode: str,
    cff_attempted: bool,
    cff_status: str,
    cff_error: str,
    fallback_attempted: bool,
    fallback_status: str,
    fallback_error: str,
    shear_related_field_candidates: List[str],
    shear_related_cff_candidates: List[str],
    background: str,
    view_margin: float,
    image_width: int,
    image_height: int,
    all_scalar_field_names_head: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "geo_name": geo_name,
        "case_name": case_name,
        "field_key": "shear_rate",
        "status": status,
        "selected_variable": selected_variable,
        "derived_variable_mode": derived_variable_mode,
        "formula_summary": "wall-shear / mu",
        "contour_target_type": "membrane_wall",
        "selected_surfaces": selected_surfaces,
        "mu_used": mu_used,
        "output_file": [str(f) for f in output_files] if len(output_files) != 1
                       else str(output_files[0]),
        "message": message,
        "cff_attempted": cff_attempted,
        "cff_status": cff_status,
        "cff_error": cff_error,
        "fallback_attempted": fallback_attempted,
        "fallback_status": fallback_status,
        "fallback_error": fallback_error,
        "shear_related_field_candidates": shear_related_field_candidates,
        "shear_related_cff_candidates": shear_related_cff_candidates,
        "all_scalar_field_names_head": all_scalar_field_names_head or [],
        "background": background,
        "view_margin": view_margin,
        "image_width": image_width,
        "image_height": image_height,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_range_arg(s: str) -> Tuple[float, float]:
    parts = s.strip().split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected MIN,MAX, got: {s!r}")
    return float(parts[0]), float(parts[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export membrane wall shear-rate contour via PyFluent.\n"
            "Formula: wall_shear_rate [1/s] = wall-shear [Pa] / mu [Pa·s]\n\n"
            "Width/height priority: --width beats --image-width; "
            "--height beats --image-height."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help="Post-processing config path (default: 00_post_config.py beside this script).",
    )
    parser.add_argument("--geo-name",  type=str, default=None)
    parser.add_argument("--case-name", type=str, default=None)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate inputs without launching Fluent.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Exit 0 without re-exporting if the output PNG already exists.",
    )
    parser.add_argument(
        "--membrane-surface", type=str, default="top",
        choices=["top", "bottom", "both"],
        help="Membrane side to export (default: top).",
    )
    parser.add_argument(
        "--shear-range", type=_parse_range_arg, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range [1/s], e.g. --shear-range 0,5000.",
    )
    # Legacy resolution options
    parser.add_argument("--image-width",  type=int, default=1920,
                        help="Image width (px). Overridden by --width if provided.")
    parser.add_argument("--image-height", type=int, default=1080,
                        help="Image height (px). Overridden by --height if provided.")
    # New resolution options (take priority over --image-width/--image-height)
    parser.add_argument("--width",  type=int, default=None,
                        help="Image width (px); if given, overrides --image-width.")
    parser.add_argument("--height", type=int, default=None,
                        help="Image height (px); if given, overrides --image-height.")
    # New visual options (applied in matplotlib fallback; logged for CFF path)
    parser.add_argument(
        "--background", type=str, default="white", choices=["white", "black"],
        help="Background colour (default: white). Applied in matplotlib fallback.",
    )
    parser.add_argument(
        "--view-margin", type=float, default=1.20, metavar="FACTOR",
        help="View zoom margin factor (default: 1.20). Logged; applied if possible.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    # Resolve effective resolution
    eff_width  = args.width  if args.width  is not None else args.image_width
    eff_height = args.height if args.height is not None else args.image_height

    config_path = Path(args.config).resolve()
    try:
        cfg = load_python_config(config_path)
    except Exception as exc:
        print(f"ERROR: Cannot load config {config_path}: {exc}")
        return 3

    try:
        paths = get_case_paths(
            cfg,
            geo_name_override=args.geo_name,
            case_name_override=args.case_name,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 3

    geo_name    = paths["geo_name"]
    case_name   = paths["case_name"]
    case_path   = Path(paths["case_path"])
    case_file   = Path(paths["final_case_file"])
    data_file   = Path(paths["final_data_file"])
    figures_dir = Path(paths["figures_dir"])

    mu: float               = float(cfg_get(cfg, "mu", 8.93e-4))
    active_mem_bases: List[str] = list(
        cfg_get(cfg, "active_membrane_base_names", ["wall_top_mem", "wall_bottom_mem"])
    )
    product_version: str    = str(cfg_get(cfg, "product_version", "25.1.0"))
    processor_count: int    = int(cfg_get(cfg, "processor_count", 1))
    graphics_driver: str    = str(cfg_get(cfg, "graphics_driver", "dx11"))
    start_timeout:   int    = int(cfg_get(cfg, "fluent_start_timeout", 600))
    health_timeout:  int    = int(cfg_get(cfg, "fluent_health_timeout", 600))

    shear_range: Optional[Tuple[float, float]] = args.shear_range

    # Determine which sides to export
    if args.membrane_surface == "both":
        sides = ["top", "bottom"]
    else:
        sides = [args.membrane_surface]

    # Build per-side output filenames (always include _top / _bottom suffix)
    output_files_by_side: Dict[str, Path] = {
        side: figures_dir / f"{geo_name}_{case_name}_shear_rate_membrane_{side}.png"
        for side in sides
    }
    status_file = figures_dir / "shear_contour_status.json"

    print(f"Geo          : {geo_name}")
    print(f"Case         : {case_name}")
    print(f"Case file    : {case_file}")
    for side, of in output_files_by_side.items():
        print(f"Output ({side:6s}): {of}")
    print(f"Status JSON  : {status_file}")
    print(f"mu           : {mu:.6e} Pa·s")
    print(f"Formula      : wall-shear / {mu:.6e}")
    print(f"Membrane side: {args.membrane_surface}")
    print(f"Background   : {args.background}")
    print(f"View margin  : {args.view_margin}")
    print(f"Resolution   : {eff_width} x {eff_height} px")
    if shear_range:
        print(f"Shear range  : {shear_range[0]} – {shear_range[1]} [1/s]")

    # --- Dry run ---
    if args.dry_run:
        print("\nDRY RUN — no Fluent launch, no image written.")
        _safe_mkdir(figures_dir)
        payload = build_status_payload(
            geo_name=geo_name, case_name=case_name,
            status=STATUS_DRY, selected_surfaces=active_mem_bases,
            mu_used=mu, output_files=list(output_files_by_side.values()),
            message="dry-run only",
            selected_variable=None,
            derived_variable_mode="pyfluent_wall_shear_over_mu",
            cff_attempted=False, cff_status="SKIPPED", cff_error="",
            fallback_attempted=False, fallback_status="SKIPPED", fallback_error="",
            shear_related_field_candidates=[], shear_related_cff_candidates=[],
            background=args.background, view_margin=args.view_margin,
            image_width=eff_width, image_height=eff_height,
        )
        write_status_json(status_file, payload)
        return 0

    # --- Skip existing ---
    if args.skip_existing:
        all_exist = all(f.is_file() for f in output_files_by_side.values())
        if all_exist:
            print(f"SKIP: all output files already exist.")
            payload = build_status_payload(
                geo_name=geo_name, case_name=case_name,
                status=STATUS_SKIP, selected_surfaces=[],
                mu_used=mu, output_files=list(output_files_by_side.values()),
                message="Skipped: all output files already exist.",
                selected_variable=None,
                derived_variable_mode="pyfluent_wall_shear_over_mu",
                cff_attempted=False, cff_status="SKIPPED", cff_error="",
                fallback_attempted=False, fallback_status="SKIPPED", fallback_error="",
                shear_related_field_candidates=[], shear_related_cff_candidates=[],
                background=args.background, view_margin=args.view_margin,
                image_width=eff_width, image_height=eff_height,
            )
            write_status_json(status_file, payload)
            return 0

    # --- Validate input files (only when path is locally reachable) ---
    if not _has_windows_drive(case_file):
        if not case_file.is_file():
            print(f"ERROR: Case file not found: {case_file}")
            return 2
    if not _has_windows_drive(data_file):
        if not data_file.is_file():
            print(f"ERROR: Data file not found: {data_file}")
            return 2

    _safe_mkdir(figures_dir)
    pyfluent.config.check_health_timeout = health_timeout

    meshing = None
    solver  = None

    # Accumulate per-run state
    final_status       = STATUS_FAIL
    final_message      = "Not attempted."
    all_selected_surfs: List[str] = []
    all_output_files:   List[Path] = []
    used_variable:      Optional[str] = None
    derived_mode        = "pyfluent_wall_shear_over_mu"
    cff_attempted       = False
    cff_status_str      = "SKIPPED"
    cff_error_str       = ""
    fb_attempted        = False
    fb_status_str       = "SKIPPED"
    fb_error_str        = ""
    scalar_candidates:      List[str] = []
    cff_candidates:         List[str] = []
    all_scalar_names_head:  List[str] = []

    try:
        # --- Launch ---
        print("\nLaunching Fluent (meshing mode → solver)...")
        meshing = pyfluent.launch_fluent(
            product_version=product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=processor_count,
            ui_mode="gui",
            graphics_driver=graphics_driver,
            start_timeout=start_timeout,
            cwd=as_fluent_path(case_path),
        )
        print("Switching to solver...")
        solver = meshing.switch_to_solver()
        meshing = None
        print("Solver ready.")

        setup = solver.settings.setup

        # --- Read case and data ---
        print(f"\nReading: {as_fluent_path(case_file)}")
        solver.settings.file.read_case_data(file_name=as_fluent_path(case_file))
        print("Case loaded.")

        # --- Detect membrane wall zones ---
        all_wall_zones = collect_wall_zones(setup)
        all_membrane_zones = find_zones_by_base_names(all_wall_zones, active_mem_bases)
        print(f"Membrane zones found: {all_membrane_zones}")

        if not all_membrane_zones:
            raise RuntimeError(
                f"No membrane zones found for base names {active_mem_bases}. "
                f"Available wall zones (first 20): {all_wall_zones[:20]}"
            )

        # --- Diagnostics: detect shear-related candidates ---
        print("\nRunning field/CFF diagnostics ...")
        scalar_candidates, cff_candidates, all_scalar_names_head = detect_shear_candidates(solver)

        # --- Process each side ---
        side_results: List[Tuple[bool, str, str]] = []  # (success, side, output_path)

        for side in sides:
            print(f"\n{'='*60}")
            print(f"Processing membrane side: {side}")
            print(f"{'='*60}")

            output_file = output_files_by_side[side]

            # Filter zones for this side
            if side == "top":
                flt = [z for z in all_membrane_zones if "top" in z.lower()]
                membrane_zones = flt if flt else all_membrane_zones
                if not flt:
                    print(f"  WARN: no 'top' zones found; using all: {all_membrane_zones}")
            elif side == "bottom":
                flt = [z for z in all_membrane_zones if "bot" in z.lower()]
                membrane_zones = flt if flt else all_membrane_zones
                if not flt:
                    print(f"  WARN: no 'bottom' zones found; using all: {all_membrane_zones}")
            else:
                membrane_zones = list(all_membrane_zones)

            print(f"  Selected surfaces: {membrane_zones}")
            all_selected_surfs.extend(membrane_zones)

            side_ok      = False
            side_var     = None
            side_mode    = "pyfluent_wall_shear_over_mu"

            # A. Try CFF direct path
            print(f"\n  [A] Attempting CFF contour export ...")
            cff_attempted = True
            cff_ok, cff_st, cff_var, cff_err = try_cff_path(
                solver=solver,
                mu=mu,
                scalar_candidates=scalar_candidates,
                cff_candidates=cff_candidates,
                membrane_zones=membrane_zones,
                shear_range=shear_range,
                output_file=output_file,
                image_width=eff_width,
                image_height=eff_height,
            )
            cff_status_str = cff_st
            cff_error_str  = cff_err

            if cff_ok:
                side_ok   = True
                side_var  = cff_var
                side_mode = "pyfluent_wall_shear_over_mu"
                print(f"  [A] CFF SUCCESS: {output_file.name}")
            else:
                print(f"  [A] CFF FAILED: {cff_err}")

                # B. Try field-data fallback
                print(f"\n  [B] Attempting field-data fallback (matplotlib) ...")
                fb_attempted = True
                fb_ok, fb_st, fb_var, fb_err = try_field_data_fallback(
                    solver=solver,
                    mu=mu,
                    membrane_zones=membrane_zones,
                    scalar_candidates=scalar_candidates,
                    shear_range=shear_range,
                    output_file=output_file,
                    image_width=eff_width,
                    image_height=eff_height,
                    background=args.background,
                )
                fb_status_str = fb_st
                fb_error_str  = fb_err

                if fb_ok:
                    side_ok   = True
                    side_var  = fb_var
                    side_mode = "pyfluent_field_data_wall_shear_over_mu"
                    print(f"  [B] Fallback SUCCESS: {output_file.name}")
                else:
                    print(f"  [B] Fallback FAILED: {fb_err}")

            side_results.append((side_ok, side, str(output_file)))
            if side_ok:
                all_output_files.append(output_file)
                if used_variable is None:
                    used_variable = side_var
                derived_mode = side_mode

        # --- Overall status ---
        any_ok  = any(ok for ok, _, _ in side_results)
        all_ok  = all(ok for ok, _, _ in side_results)

        if all_ok:
            final_status  = STATUS_OK
            final_message = (
                f"wall-shear / mu={mu:.6e}: "
                + ", ".join(f"{s}→{Path(p).name}" for _, s, p in side_results)
            )
        elif any_ok:
            final_status  = STATUS_WARN
            failed_sides  = [s for ok, s, _ in side_results if not ok]
            final_message = f"Partial success; failed sides: {failed_sides}"
        else:
            final_status  = STATUS_FAIL
            final_message = "Both CFF and fallback failed for all sides."

        print(f"\nOverall: {final_status} — {final_message}")

    except Exception as exc:
        final_status  = STATUS_FAIL
        final_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        print(f"\nFAIL: {exc}")
        print(traceback.format_exc())

    finally:
        if meshing is not None:
            try:
                meshing.exit()
            except Exception:
                pass
        if solver is not None:
            try:
                solver.exit()
                print("Fluent session closed.")
            except Exception as ec:
                print(f"WARN: solver.exit() raised: {ec}")

    payload = build_status_payload(
        geo_name=geo_name,
        case_name=case_name,
        status=final_status,
        selected_surfaces=sorted(set(all_selected_surfs)),
        mu_used=mu,
        output_files=all_output_files if all_output_files else list(output_files_by_side.values()),
        message=final_message,
        selected_variable=used_variable,
        derived_variable_mode=derived_mode,
        cff_attempted=cff_attempted,
        cff_status=cff_status_str,
        cff_error=cff_error_str,
        fallback_attempted=fb_attempted,
        fallback_status=fb_status_str,
        fallback_error=fb_error_str,
        shear_related_field_candidates=scalar_candidates,
        shear_related_cff_candidates=cff_candidates,
        all_scalar_field_names_head=all_scalar_names_head,
        background=args.background,
        view_margin=args.view_margin,
        image_width=eff_width,
        image_height=eff_height,
    )
    write_status_json(status_file, payload)

    return 0 if final_status in (STATUS_OK, STATUS_WARN) else 2


if __name__ == "__main__":
    raise SystemExit(main())
