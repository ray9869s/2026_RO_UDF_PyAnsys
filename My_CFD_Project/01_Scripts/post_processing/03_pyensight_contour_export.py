# -*- coding: utf-8 -*-
"""
03_pyensight_contour_export.py

Export presentation-quality contour images from a solved Fluent case using
PyEnSight (ansys.pyensight.core v0.11+, EnSight 25.1).

Fields exported (membrane wall unless noted):
  cp_inlet         - CP = salt_conc / bulk_center_avg  on membrane walls
  water_flux       - Jw [m/s] from solution-diffusion formula on membrane walls
  lmh              - Jw * 3.6e6 [LMH] on membrane walls
  salt_flux        - salt mass flux [kg/m2/s] on membrane walls
  shear_rate       - wall-shear / mu [1/s] on membrane walls
  wall_shear_rate  - wall-shear / mu [1/s] on membrane + spacer walls
  velocity_midplane- velocity-magnitude on best-available plane/fluid surface

Usage:
  python 03_pyensight_contour_export.py --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03_pyensight_contour_export.py --dry-run --fields cp_inlet,water_flux,lmh,salt_flux,shear_rate
  python 03_pyensight_contour_export.py --cp-range 1.00,1.15 --lmh-range 20,30
  python 03_pyensight_contour_export.py --auto-range --membrane-surface both
  python 03_pyensight_contour_export.py --skip-existing --image-width 2560 --image-height 1440
"""
from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import inspect
import json
import os
import platform
import re
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, FrozenSet, List, Optional, Tuple

from ro.paths import project_root

# ---------------------------------------------------------------------------
# PyEnSight import guard — fail early with a clear message if not installed
# ---------------------------------------------------------------------------

_PYENSIGHT_AVAILABLE = False
_PYENSIGHT_IMPORT_ERROR = ""

try:
    import ansys.pyensight.core as _pyensight_pkg  # noqa: F401
    _PYENSIGHT_AVAILABLE = True
except ImportError as _exc:
    _PYENSIGHT_IMPORT_ERROR = str(_exc)

# ---------------------------------------------------------------------------
# Paths — all relative to this script; never hard-code C:\ or Windows paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = project_root() / "configs" / "00_post_config.py"
CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

def _has_windows_drive(p: Path) -> bool:
    """Return True if any component of p looks like a Windows drive letter (e.g. 'C:').
    On WSL/Linux such paths are resolved as relative subdirectories, so creating
    them is almost always unintentional."""
    return any(re.match(r"^[A-Za-z]:$", part) for part in p.parts)


def _safe_mkdir(p: Path) -> bool:
    """Create p and its parents only if the path is safe on the current OS.
    On Linux/WSL, skips creation and returns False when a Windows drive component
    is detected. Returns True on success."""
    if platform.system() != "Windows" and _has_windows_drive(p):
        return False
    p.mkdir(parents=True, exist_ok=True)
    return True


# ---------------------------------------------------------------------------
# Field specifications
# ---------------------------------------------------------------------------

DEFAULT_FIELDS: List[str] = ["cp_inlet", "water_flux", "lmh", "salt_flux"]

# Candidates are tried in order (exact, then normalised) against ENS_VAR.DESCRIPTION.
FIELD_SPECS: dict = {
    "cp_inlet": {
        # Field key kept as cp_inlet for pipeline compatibility; semantics are
        # now film-theory CP on udm-9 (not Cm/C_INLET_REF). Intended rename: cp.
        "display_label": "CP [-] (film-theory)",
        "var_candidates": ["udm-9", "UDM-9", "User Defined Memory 9", "udm_9"],
        "surface_type": "membrane",
        "output_suffix": "cp_inlet_membrane",
        "derive_shear_rate": False,
    },
    "lmh": {
        "display_label": "LMH [LMH]",
        "var_candidates": ["udm-8", "UDM-8", "User Defined Memory 8", "udm_8"],
        "surface_type": "membrane",
        "output_suffix": "lmh_membrane",
        "derive_shear_rate": False,
    },
    "water_flux": {
        "display_label": "Water flux Jw [m/s]",
        "var_candidates": ["udm-6", "UDM-6", "User Defined Memory 6", "udm_6"],
        "surface_type": "membrane",
        "output_suffix": "water_flux_membrane",
        "derive_shear_rate": False,
    },
    "salt_flux": {
        "display_label": "Salt flux Js [kg/m²/s]",
        # COUNT=13 => valid indices 0..12. udm-12 is Y1 (wall-centroid
        # distance), not salt flux. udm-13 is out of range — do not fall back.
        "var_candidates": ["udm-10", "UDM-10", "User Defined Memory 10", "udm_10"],
        "surface_type": "membrane",
        "output_suffix": "salt_flux_membrane",
        "derive_shear_rate": False,
    },
    "shear_rate": {
        "display_label": "Wall shear rate [1/s]",
        "var_candidates": [
            "wall-shear", "Wall Shear Stress", "wall_shear",
            "wall-shear-stress", "Wall Shear", "wall-shear-1",
            "Wall Shear Stress Magnitude", "wall shear magnitude",
            "wall-shear-magnitude",
        ],
        "surface_type": "membrane",
        "output_suffix": "shear_rate_membrane",
        "derive_shear_rate": False,
    },
    "wall_shear_rate": {
        "display_label": "Wall shear rate [1/s]",
        "var_candidates": [
            "wall-shear", "Wall Shear Stress", "wall_shear",
            "wall-shear-stress", "Wall Shear", "wall-shear-1",
        ],
        "surface_type": "membrane_and_spacer",
        "output_suffix": "wall_shear_rate_membrane",
        "derive_shear_rate": True,
    },
    "velocity_midplane": {
        "display_label": "Velocity magnitude [m/s]",
        "var_candidates": [
            "velocity-magnitude", "Velocity Magnitude", "velocity_magnitude",
            "Velocity", "velocity",
        ],
        "surface_type": "midplane",
        "output_suffix": "velocity_midplane",
        "derive_shear_rate": False,
    },
}

# ---------------------------------------------------------------------------
# EDITABLE: Default fixed color ranges per field.
# Set a field to None to use EnSight auto-range for that field.
# These are applied unless --auto-range or a per-field CLI override is given.
# ---------------------------------------------------------------------------

FIELD_COLOR_RANGES: dict = {
    "cp_inlet":          (1.00, 1.15),
    "water_flux":        None,           # None = auto-range
    "lmh":               (20.0, 30.0),
    "salt_flux":         None,           # None = auto-range
    "shear_rate":        None,           # None = auto-range
    "wall_shear_rate":   None,           # None = auto-range
    "velocity_midplane": (0.0,  0.7),
}

# ---------------------------------------------------------------------------
# UDF membrane constants — must exactly match 260612_RO_UDF.c
# Used to reconstruct CP and LMH directly from primitive EnSight variables.
# ---------------------------------------------------------------------------

UDF_A_PERM      = 2.50e-12     # Water permeability [m/s/Pa]
UDF_B_PERM      = 2.50e-8      # Salt permeability [m/s]
UDF_KAPPA       = 4958.0       # Osmotic pressure coefficient [Pa·m³/mol]
UDF_P_PERM      = 101325.0     # Permeate-side pressure [Pa]
UDF_MW_SALT     = 0.05844      # NaCl molecular weight [kg/mol]
UDF_RHO_REF     = 998.20       # Reference density [kg/m³]
UDF_MS_TO_LMH   = 3600000.0    # [m/s] → [L/m²/hr]
UDF_C_INLET_REF = 597.8268309  # Inlet NaCl concentration [mol/m³]

# Inlet salt mass fraction reference (≈ 0.035 for 3.5 % seawater).
# Fallback CP denominator when center-plane average is unavailable.
INLET_SALT_MASS_FRAC_REF: float = UDF_C_INLET_REF * UDF_MW_SALT / UDF_RHO_REF

# Channel geometry constants for center-plane z fallback.
# Membrane-normal direction is z; channel height = 0.77 mm.
CHANNEL_HEIGHT_M: float = 0.00077
# Candidate z values tried when bounding-box extraction fails.
# Origin is channel-centred (confirmed): server mesh z extent
# -3.850746e-04..3.852144e-04 m; probe_inlet_profile inlet face centroids
# -0.378616..+0.378583 mm, eta in [0.00829, 0.99167], zero clamped faces.
# 0.0 is therefore the correct mid-channel plane; CHANNEL_HEIGHT_M/2 remains
# only as a legacy bottom-origin fallback candidate.
CENTER_PLANE_Z_CANDIDATES: List[float] = [0.0, CHANNEL_HEIGHT_M / 2.0]

# ---------------------------------------------------------------------------
# Salt variable name candidates for primitive matching (tried in order)
# ---------------------------------------------------------------------------

SALT_MASS_FRAC_CANDIDATES: List[str] = [
    "nacl", "NaCl", "salt", "Salt",
    "species-0", "species_0",
    "yi-0", "yi_0",
    "yi-nacl", "yi_nacl", "yi-NaCl",
    "mass-fraction-of-nacl", "mass-fraction-of-NaCl",
    "mass-fraction-of-salt",
    "nacl-mass-fraction", "salt-mass-fraction",
    "mass-fraction-of-nacl-1",
    "molar-concentration-of-nacl", "nacl-concentration",
    "nacl-molar-concentration",
    "concentration", "species",
]

# ---------------------------------------------------------------------------
# Status / range-mode constants
# ---------------------------------------------------------------------------

STATUS_SUCCESS = "SUCCESS"
STATUS_WARN = "WARN"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED_EXISTING = "SKIPPED_EXISTING"
STATUS_DRY_RUN = "DRY_RUN"

RANGE_MODE_FIXED = "fixed"   # script default applied
RANGE_MODE_CLI   = "cli"     # user-supplied CLI override
RANGE_MODE_AUTO  = "auto"    # EnSight auto-range
RANGE_MODE_NONE  = "none"    # palette not found; range not applied

BOUNDS_API_ATTEMPTS: List[Tuple[str, str, Optional[str]]] = [
    ("attr", "BOUNDS", None),
    ("attr", "bounds", None),
    ("attr", "BOUNDINGBOX", None),
    ("attr", "BOUNDING_BOX", None),
    ("attr", "EXTENTS", None),
    ("attr", "extents", None),
    ("attr", "MINMAX", None),
    ("attr", "minmax", None),
    ("method", "get_bounds", None),
    ("method", "getbounds", None),
    ("method", "get_extents", None),
    ("method", "getextents", None),
    ("method_arg", "get_values", "BOUNDS"),
    ("method_arg", "get_values", "BOUNDINGBOX"),
    ("method_arg", "getattr", "BOUNDS"),
    ("method_arg", "getattr", "BOUNDINGBOX"),
]

BOUNDS_ATTR_KEYWORDS: Tuple[str, ...] = (
    "bound", "extent", "min", "max", "box", "range", "coord", "xyz",
)

BOUNDS_DIAG_NAME_KEYWORDS: Tuple[str, ...] = (
    "wall_top_mem", "wall_bottom_mem", "membrane", "top", "bottom",
    "contour", "cp", "lmh", "flux",
)

# ---------------------------------------------------------------------------
# Status record dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExportRecord:
    geo_name: str
    case_name: str
    field_key: str
    field_name: str
    target_surface_or_plane: str
    output_file: str
    status: str
    message: str = ""
    selected_variable: str = ""
    selected_surfaces: str = ""
    color_range_mode: str = ""
    color_range_min: Optional[float] = None
    color_range_max: Optional[float] = None
    clean_scene_status: str = ""
    palette_diagnostics: Optional[str] = None
    contour_target_type: str = "wall"
    derived_variable_mode: str = ""
    primitive_variables_used: str = ""
    bulk_reference_mode: str = ""
    bulk_reference_value: Optional[float] = None
    bulk_reference_units_or_type: str = ""
    center_plane_name: str = ""
    formula_summary: str = ""
    center_plane_diagnostics: Optional[dict] = None
    view_margin_requested: Optional[float] = None
    zoom_out_requested: Optional[float] = None
    zoom_out_applied: bool = False
    zoom_out_method: str = ""
    zoom_out_status: str = ""
    zoom_out_error: str = ""
    view_orientation_preserved: bool = True
    membrane_bounds_raw: Optional[List[float]] = None
    membrane_bounds_padded: Optional[List[float]] = None
    membrane_bounds_source: str = ""
    fit_target_used: str = ""
    bounds_fit_attempted: bool = False
    bounds_fit_status: str = ""
    bounds_fit_method: str = ""
    bounds_fit_error: str = ""
    bounds_candidate_attempts: List[dict] = field(default_factory=list)
    bounds_candidate_successes: List[dict] = field(default_factory=list)
    chosen_bounds_candidate: str = ""
    chosen_bounds_format: str = ""
    manual_view_bounds_requested: Optional[List[float]] = None
    manual_view_plane_requested: str = ""
    export_crop_disabled: bool = True
    export_viewport_method: str = ""
    export_region_used: str = ""
    bounds_debug_sweep_attempted: bool = False
    bounds_debug_sweep_outputs: List[str] = field(default_factory=list)
    legend_preset_requested: str = ""
    legend_preset_applied: str = ""
    legend_layout_status: str = ""
    legend_layout_method: str = ""
    legend_layout_error: str = ""
    legend_x: Optional[float] = None
    legend_y: Optional[float] = None
    legend_width: Optional[float] = None
    legend_height: Optional[float] = None
    legend_text_size: Optional[float] = None
    legend_title_size: Optional[float] = None
    legend_label_count: Optional[int] = None
    legend_reserve_right_requested: Optional[float] = None
    legend_reserve_right_applied: bool = False
    legend_reserve_right_method: str = ""
    legend_reserve_right_error: str = ""
    legend_mode: str = ""
    legend_hide_attempted: bool = False
    legend_hide_status: str = ""
    legend_hide_method: str = ""
    legend_hide_error: str = ""
    legend_visible_after_hide: Optional[bool] = None
    colorbar_metadata_written: bool = False
    colorbar_metadata_files: List[str] = field(default_factory=list)
    colorbar_range_min: Optional[float] = None
    colorbar_range_max: Optional[float] = None
    colorbar_range_source: str = ""
    colorbar_units: str = ""
    colorbar_title: str = ""
    colorbar_palette_name: str = ""


# ---------------------------------------------------------------------------
# Config loading — importlib because filename starts with a digit
# ---------------------------------------------------------------------------

def load_python_config(config_path: Path) -> Any:
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build module spec for: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


def as_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


# ---------------------------------------------------------------------------
# Path building
# ---------------------------------------------------------------------------

def get_case_paths(
    cfg: Any,
    geo_name_override: Optional[str] = None,
    case_name_override: Optional[str] = None,
) -> dict:
    configured_root = cfg_get(cfg, "project_root")
    resolved_project_root = (
        as_path(configured_root).resolve() if configured_root else project_root()
    )
    geo_name = geo_name_override or str(cfg_get(cfg, "geo_name", ""))
    case_name = case_name_override or str(cfg_get(cfg, "case_name", ""))

    if not geo_name or geo_name == "===== Edit here =====":
        raise ValueError("geo_name is unset. Pass --geo-name or edit 00_post_config.py.")
    if not case_name or case_name == "===== Edit here =====":
        raise ValueError("case_name is unset. Pass --case-name or edit 00_post_config.py.")

    results_raw = cfg_get(cfg, "results_dir")
    if not results_raw:
        raise ValueError("results_dir is unset. Set it in the post config.")
    results_root = as_path(results_raw)
    case_path = results_root / geo_name / case_name

    final_case_file = as_path(
        cfg_get(cfg, "final_case_file", case_path / f"{geo_name}_{case_name}_final.cas.h5")
    )
    final_data_file = as_path(
        cfg_get(cfg, "final_data_file", case_path / f"{geo_name}_{case_name}_final.dat.h5")
    )

    figures_dir = case_path / "post" / "figures" / "contours"

    return {
        "project_root": resolved_project_root,
        "geo_name": geo_name,
        "case_name": case_name,
        "case_path": case_path,
        "final_case_file": final_case_file,
        "final_data_file": final_data_file,
        "figures_dir": figures_dir,
    }


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_range(s: str) -> Tuple[float, float]:
    """Parse 'min,max' string. Raises ValueError on bad input."""
    parts = s.strip().split(",")
    if len(parts) != 2:
        raise ValueError(f"Expected 'min,max' but got '{s}'.")
    return float(parts[0]), float(parts[1])


def _parse_manual_view_bounds(s: str) -> Tuple[float, float, float, float]:
    """Parse 'xmin,xmax,ymin,ymax' string. Raises ValueError on bad input."""
    parts = [p.strip() for p in s.strip().split(",")]
    if len(parts) != 4:
        raise ValueError(f"Expected 'XMIN,XMAX,YMIN,YMAX' but got '{s}'.")
    xmin, xmax, ymin, ymax = (float(p) for p in parts)
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(
            f"Manual view bounds must satisfy XMIN<XMAX and YMIN<YMAX; got '{s}'."
        )
    return xmin, xmax, ymin, ymax


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export presentation-ready contour images from a solved Fluent case via PyEnSight.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Fields: " + ", ".join(FIELD_SPECS) + "\n\n"
            "Default color ranges (edit FIELD_COLOR_RANGES in script to change):\n"
            "  cp_inlet:          1.00 - 1.15\n"
            "  water_flux:        auto\n"
            "  lmh:               20.0 - 30.0\n"
            "  salt_flux:         auto\n"
            "  shear_rate:        auto\n"
            "  wall_shear_rate:   auto\n"
            "  velocity_midplane: 0.0  - 0.7\n\n"
            "Examples:\n"
            "  python 03_pyensight_contour_export.py "
            "--geo-name Diamond_Spacer --case-name u0p2_p6M\n"
            "  python 03_pyensight_contour_export.py --dry-run "
            "--fields cp_inlet,water_flux,lmh,salt_flux,shear_rate\n"
            "  python 03_pyensight_contour_export.py "
            "--cp-range 1.00,1.15 --membrane-surface top\n"
        ),
    )

    # --- core ---
    parser.add_argument(
        "--config", type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help=f"Post-processing config Python file. Defaults to ${CONFIG_ENV_VAR} or <project>/configs/00_post_config.py.",
    )
    parser.add_argument("--geo-name", type=str, default=None, help="Override geo_name from config.")
    parser.add_argument("--case-name", type=str, default=None, help="Override case_name from config.")
    parser.add_argument(
        "--fields", type=str, default=None,
        help="Comma-separated fields to export. Default: all four.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate paths and plan only; do not launch PyEnSight or write images.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip export if the output PNG already exists.",
    )
    parser.add_argument("--image-width",  type=int, default=1920, help="Image width in pixels (default: 1920).")
    parser.add_argument("--image-height", type=int, default=1080, help="Image height in pixels (default: 1080).")

    # --- color range ---
    rg = parser.add_argument_group("Color range options")
    rg.add_argument(
        "--auto-range", action="store_true",
        help="Use EnSight auto-range for all fields (ignores defaults and per-field overrides).",
    )
    rg.add_argument(
        "--cp-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for cp_inlet, e.g. --cp-range 1.00,1.15",
    )
    rg.add_argument(
        "--lmh-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for lmh, e.g. --lmh-range 20,30",
    )
    rg.add_argument(
        "--velocity-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for velocity_midplane, e.g. --velocity-range 0,0.7",
    )
    rg.add_argument(
        "--wall-shear-rate-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for wall_shear_rate, e.g. --wall-shear-rate-range 0,5000",
    )
    rg.add_argument(
        "--water-flux-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for water_flux [m/s], e.g. --water-flux-range 0,5e-7",
    )
    rg.add_argument(
        "--salt-flux-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for salt_flux [kg/m2/s], e.g. --salt-flux-range 0,1e-5",
    )
    rg.add_argument(
        "--shear-rate-range", type=str, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range for shear_rate [1/s], e.g. --shear-rate-range 0,5000",
    )

    # --- scene / presentation ---
    sg = parser.add_argument_group("Scene and presentation options")
    sg.add_argument(
        "--membrane-surface", type=str, default="top",
        choices=["top", "bottom", "both"],
        help="Which membrane surface to show for cp_inlet and lmh. Default: top.",
    )
    sg.add_argument(
        "--show-triad", action="store_true",
        help="Keep the axis triad visible (default: hidden).",
    )
    sg.add_argument(
        "--background", type=str, default="white",
        choices=["white", "transparent", "default"],
        help="Viewport background: white (default), transparent, or default (leave as-is).",
    )
    sg.add_argument(
        "--view-margin", type=float, default=1.20, metavar="FACTOR",
        help=(
            "Zoom-out factor applied after fit (default: 1.20). "
            "1.0 = no margin; 1.2 = zoom out 20 %%. "
            "Use 1.35-1.40 on the server if membrane ends are clipped."
        ),
    )
    sg.add_argument(
        "--zoom-out", type=float, default=1.15, metavar="FACTOR",
        help=(
            "Additional explicit camera/view zoom-out applied after --view-margin "
            "and after the view is fit and oriented (default: 1.15). "
            "1.0 = no extra zoom-out; 1.15 = zoom out ~15%%; 1.25 = zoom out ~25%%. "
            "Stacks with --view-margin. Secondary to the padded-bounds fit below; "
            "kept for compatibility but no longer the primary anti-clipping lever."
        ),
    )
    sg.add_argument(
        "--bounds-debug-sweep", action="store_true", default=False,
        help=(
            "Also export extra diagnostic images per field at several fit "
            "strategies/margins (current, 1.20, 1.40, 1.60) for server-side "
            "visual comparison. Never replaces the main output. Default: off."
        ),
    )
    sg.add_argument(
        "--bounds-diagnostics", action="store_true", default=False,
        help=(
            "Write detailed PyEnSight part/object bounds diagnostics to "
            "post/figures/contours/pyensight_bounds_diagnostics.json and print "
            "the inspected objects. Default: off."
        ),
    )
    sg.add_argument(
        "--manual-view-bounds", type=str, default=None, metavar="XMIN,XMAX,YMIN,YMAX",
        help=(
            "Manual 2D view-plane fit bounds. When provided, these bounds are "
            "used as the fit target even if automatic PyEnSight part bounds fail."
        ),
    )
    sg.add_argument(
        "--manual-view-plane", type=str, default="xy", metavar="PLANE",
        help="Manual view bounds plane. Only 'xy' is implemented for now. Default: xy.",
    )

    # --- legend / colorbar layout ---
    lgd = parser.add_argument_group("Legend / colorbar layout options")
    lgd.add_argument(
        "--legend-mode", type=str, default="hide",
        choices=["show", "hide"],
        help=(
            "Legend/colorbar visibility in exported PNGs (default: hide). "
            "'hide' removes all legend/colorbar annotation from the exported "
            "image and instead writes colorbar range/unit metadata to "
            "contour_colorbar_ranges.json/.txt/.csv in the output directory. "
            "'show' preserves the previous legend layout behavior, in which "
            "case --legend-preset and the other --legend-* options below apply."
        ),
    )
    lgd.add_argument(
        "--legend-preset", type=str, default="presentation_right",
        choices=["default", "presentation_right", "compact_right"],
        help=(
            "Legend/colorbar layout preset (default: presentation_right). "
            "'default' preserves prior behavior (no layout changes). "
            "'presentation_right' moves the legend to the right side outside "
            "the membrane contour region with smaller readable text. "
            "'compact_right' is similar but narrower with even smaller text."
        ),
    )
    lgd.add_argument(
        "--legend-x", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend LOCATIONX (normalized viewport coord, 0-1). Overrides preset.",
    )
    lgd.add_argument(
        "--legend-y", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend LOCATIONY (normalized viewport coord, 0-1). Overrides preset.",
    )
    lgd.add_argument(
        "--legend-width", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend WIDTH (normalized viewport coord). Overrides preset.",
    )
    lgd.add_argument(
        "--legend-height", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend HEIGHT (normalized viewport coord). Overrides preset.",
    )
    lgd.add_argument(
        "--legend-text-size", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend label TEXTSIZE. Overrides preset.",
    )
    lgd.add_argument(
        "--legend-title-size", type=float, default=None, metavar="FLOAT",
        help="Manual override for legend title text size. Overrides preset.",
    )
    lgd.add_argument(
        "--legend-label-count", type=int, default=None, metavar="INT",
        help="Manual override for legend LABELCOUNT. Overrides preset.",
    )
    lgd.add_argument(
        "--legend-reserve-right", type=float, default=0.12, metavar="FRACTION",
        help=(
            "Reserve additional right-side view padding, as a fraction of the "
            "membrane width, so the legend preset has whitespace to sit in "
            "(default: 0.12). Only attempted when --manual-view-bounds is set "
            "and the camera-fit API can accommodate it without a pan/lookat "
            "adjustment; recorded either way in the status JSON."
        ),
    )

    # --- LMH derivation ---
    lg = parser.add_argument_group("LMH direct derivation options")
    lg.add_argument(
        "--operating-pressure", type=float, default=None, metavar="PA",
        help=(
            "Fluent operating (reference) pressure in Pa, used to convert gauge pressure "
            "to absolute for the LMH solution-diffusion formula. "
            "Default: read from config 'operating_pressure' attribute, or 101325 Pa."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Resolve per-field color ranges from CLI args
# ---------------------------------------------------------------------------

def _resolve_field_ranges(args: argparse.Namespace) -> dict:
    """
    Returns a dict mapping field_key -> (min, max) or None.
    None means auto-range. Raises ValueError on bad range strings.
    """
    if args.auto_range:
        return {k: None for k in FIELD_SPECS}

    ranges = dict(FIELD_COLOR_RANGES)

    cli_map = {
        "cp_inlet":          args.cp_range,
        "water_flux":        args.water_flux_range,
        "lmh":               args.lmh_range,
        "salt_flux":         args.salt_flux_range,
        "shear_rate":        args.shear_rate_range,
        "wall_shear_rate":   args.wall_shear_rate_range,
        "velocity_midplane": args.velocity_range,
    }
    for key, raw in cli_map.items():
        if raw is not None:
            try:
                ranges[key] = _parse_range(raw)
            except ValueError as exc:
                raise ValueError(f"--{key.replace('_', '-')}-range: {exc}") from exc

    return ranges


# ---------------------------------------------------------------------------
# Export plan
# ---------------------------------------------------------------------------

def build_export_plan(
    cfg: Any,
    paths: dict,
    field_keys: List[str],
    field_ranges: dict,
    auto_range: bool,
    operating_pressure: Optional[float] = None,
) -> List[dict]:
    geo_name = paths["geo_name"]
    case_name = paths["case_name"]
    figures_dir = Path(paths["figures_dir"])

    mu = float(cfg_get(cfg, "mu", 8.93e-4))
    active_membrane_base_names = list(
        cfg_get(cfg, "active_membrane_base_names", ["wall_top_mem", "wall_bottom_mem"])
    )
    buffer_wall_base_names = list(
        cfg_get(cfg, "buffer_wall_base_names", ["wall_top_buffer", "wall_bottom_buffer"])
    )
    wall_spacer_labels = list(cfg_get(cfg, "wall_spacer_labels", []))

    c_inlet_ref_mol = float(cfg_get(cfg, "c_inlet_ref", UDF_C_INLET_REF))
    p_op = (
        operating_pressure
        if operating_pressure is not None
        else float(cfg_get(cfg, "operating_pressure", 101325.0))
    )

    plan = []
    for key in field_keys:
        if key not in FIELD_SPECS:
            print(f"WARNING: Unknown field '{key}', skipping.")
            continue
        spec = FIELD_SPECS[key]
        out_file = figures_dir / f"{geo_name}_{case_name}_{spec['output_suffix']}.png"

        rng = field_ranges.get(key)
        if auto_range or rng is None:
            r_min, r_max = None, None
            r_mode = RANGE_MODE_AUTO
        else:
            r_min, r_max = rng
            r_mode = RANGE_MODE_CLI if (FIELD_COLOR_RANGES.get(key) != rng) else RANGE_MODE_FIXED

        plan.append({
            "field_key": key,
            "field_name": spec["display_label"],
            "var_candidates": spec["var_candidates"],
            "surface_type": spec["surface_type"],
            "output_file": out_file,
            "mu": mu,
            "derive_shear_rate": spec["derive_shear_rate"],
            "active_membrane_base_names": active_membrane_base_names,
            "buffer_wall_base_names": buffer_wall_base_names,
            "wall_spacer_labels": wall_spacer_labels,
            "color_range_min": r_min,
            "color_range_max": r_max,
            "color_range_mode": r_mode,
            # UDF constants for direct CP / LMH derivation
            "udf_a_perm":     UDF_A_PERM,
            "udf_b_perm":     UDF_B_PERM,
            "udf_kappa":      UDF_KAPPA,
            "udf_p_perm":     UDF_P_PERM,
            "udf_mw_salt":    UDF_MW_SALT,
            "udf_rho_ref":    UDF_RHO_REF,
            "udf_ms_to_lmh":  UDF_MS_TO_LMH,
            "c_inlet_ref_mol": c_inlet_ref_mol,
            "operating_pressure": p_op,
            "case_path": str(paths["case_path"]),
        })

    return plan


# ---------------------------------------------------------------------------
# Surface matching helpers
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _zone_matches_base(zone_name: str, base_name: str) -> bool:
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def _find_by_base_names(all_names: List[str], base_names: List[str]) -> List[str]:
    matched = [n for n in all_names for b in base_names if _zone_matches_base(n, b)]
    return sorted(set(matched))


def _find_normalized(all_names: List[str], base_names: List[str]) -> List[str]:
    result = []
    for base in base_names:
        base_n = _normalize(base)
        for name in all_names:
            nn = _normalize(name)
            if nn == base_n or nn.startswith(base_n + "_"):
                result.append(name)
    return sorted(set(result))


def _find_by_keywords(all_names: List[str], keywords: List[str]) -> List[str]:
    result = []
    for kw in keywords:
        kw_n = _normalize(kw)
        for name in all_names:
            if kw_n in _normalize(name):
                result.append(name)
    return sorted(set(result))


# ---------------------------------------------------------------------------
# Find surfaces in the loaded PyEnSight session
# ---------------------------------------------------------------------------

def find_surfaces(
    session: Any,
    surface_type: str,
    plan_item: dict,
) -> Tuple[List[str], str]:
    """Return (matched_part_names, warning_message). warning_message is '' on clean match."""
    active_membrane = plan_item["active_membrane_base_names"]
    spacer_walls = plan_item["wall_spacer_labels"]

    try:
        all_parts = session.ensight.objs.core.PARTS
        all_names = [p.DESCRIPTION for p in all_parts if p.DESCRIPTION]
    except Exception as exc:
        return [], f"Could not list EnSight parts: {exc}"

    if surface_type == "midplane":
        plane_kws = ["midplane", "mid_plane", "interior", "mid-plane", "plane_mid", "symm"]
        matched = _find_by_keywords(all_names, plane_kws)
        if matched:
            return matched, ""
        try:
            vol_parts = session.ensight.utils.parts.select_parts_by_dimension(3)
            vol_names = [p.DESCRIPTION for p in vol_parts if p.DESCRIPTION]
            if vol_names:
                return vol_names, (
                    "No named mid-plane part found; using 3D volume parts for velocity. "
                    "Pre-create a mid-plane surface in Fluent/EnSight for a true cross-section."
                )
        except Exception:
            pass
        return all_names[:10], "Mid-plane and 3D part lookup both failed; using first available parts."

    if surface_type == "membrane":
        target_bases = active_membrane
    elif surface_type == "membrane_and_spacer":
        target_bases = active_membrane + spacer_walls if spacer_walls else active_membrane
    else:
        target_bases = active_membrane

    matched = _find_by_base_names(all_names, target_bases)
    if matched:
        return matched, ""

    matched = _find_normalized(all_names, target_bases)
    if matched:
        return matched, f"Used normalised name matching for bases: {target_bases}"

    keywords = [b.split("_")[-1] for b in target_bases if "_" in b] + target_bases
    matched = _find_by_keywords(all_names, keywords)
    if matched:
        return matched, f"Keyword-matched surfaces {matched}. Available ({len(all_names)}): {all_names[:20]}"

    return [], (
        f"No surfaces found for base names {target_bases}. "
        f"Available ({len(all_names)}): {all_names[:20]}"
    )


# ---------------------------------------------------------------------------
# Find EnSight variable by candidate name list
# ---------------------------------------------------------------------------

def find_ensight_variable(
    session: Any,
    candidates: List[str],
) -> Tuple[Optional[Any], Optional[str]]:
    """Return (var_object, matched_DESCRIPTION) or (None, None) if not found."""
    try:
        all_vars = list(session.ensight.objs.core.VARIABLES)
        desc_to_var = {v.DESCRIPTION: v for v in all_vars if v.DESCRIPTION}

        for c in candidates:
            if c in desc_to_var:
                return desc_to_var[c], c

        norm_map = {_normalize(k): (k, v) for k, v in desc_to_var.items()}
        for c in candidates:
            cn = _normalize(c)
            if cn in norm_map:
                orig_name, var_obj = norm_map[cn]
                return var_obj, orig_name

        return None, None
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Filter membrane surfaces by side (top / bottom / both)
# ---------------------------------------------------------------------------

def filter_membrane_surface(
    surface_names: List[str],
    membrane_side: str,
) -> Tuple[List[str], str]:
    """Filter surface_names to top, bottom, or both. Returns (filtered, warning_msg)."""
    if membrane_side == "both":
        return surface_names, ""

    if membrane_side == "top":
        tops = [n for n in surface_names if "top" in _normalize(n)]
        if tops:
            return tops, ""
        return surface_names, (
            f"--membrane-surface top: no 'top' surface found among {surface_names}; using all."
        )

    if membrane_side == "bottom":
        bots = [n for n in surface_names if "bot" in _normalize(n)]
        if bots:
            return bots, ""
        return surface_names, (
            f"--membrane-surface bottom: no 'bottom' surface found among {surface_names}; using all."
        )

    return surface_names, ""


# ---------------------------------------------------------------------------
# PyEnSight part/object bounds extraction and diagnostics
# ---------------------------------------------------------------------------

def _safe_attr(obj: Any, attr: str) -> Tuple[Any, Optional[str]]:
    """Best-effort public attribute read. Returns (value, error_or_None)."""
    try:
        return getattr(obj, attr), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _safe_preview(value: Any, max_items: int = 12, max_chars: int = 500) -> Any:
    """Return a JSON-friendly, bounded preview of an arbitrary PyEnSight value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return repr(value[:max_chars])
    if isinstance(value, dict):
        out = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= max_items:
                out["..."] = f"{len(value) - max_items} more item(s)"
                break
            out[str(k)] = _safe_preview(v, max_items=max_items, max_chars=max_chars)
        return out
    if hasattr(value, "tolist"):
        try:
            return _safe_preview(value.tolist(), max_items=max_items, max_chars=max_chars)
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        vals = list(value)
        preview = [
            _safe_preview(v, max_items=max_items, max_chars=max_chars)
            for v in vals[:max_items]
        ]
        if len(vals) > max_items:
            preview.append(f"... {len(vals) - max_items} more item(s)")
        return preview
    text = repr(value)
    return text if len(text) <= max_chars else text[:max_chars] + "...<truncated>"


def _flatten_numeric_values(value: Any, limit: int = 24) -> List[float]:
    """Flatten nested list/tuple/array-like values into numeric floats."""
    values: List[float] = []

    def _walk(v: Any) -> None:
        if len(values) >= limit:
            return
        if v is None or isinstance(v, (str, bytes, bool)):
            return
        if isinstance(v, (int, float)):
            try:
                values.append(float(v))
            except Exception:
                pass
            return
        if hasattr(v, "item"):
            try:
                _walk(v.item())
                return
            except Exception:
                pass
        if hasattr(v, "tolist"):
            try:
                _walk(v.tolist())
                return
            except Exception:
                pass
        if isinstance(v, dict):
            for item in v.values():
                _walk(item)
                if len(values) >= limit:
                    break
            return
        try:
            iterator = iter(v)
        except Exception:
            return
        for item in iterator:
            _walk(item)
            if len(values) >= limit:
                break

    _walk(value)
    return values


def _valid_bounds_tuple(bounds: Tuple[float, float, float, float, float, float]) -> bool:
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    return xmin < xmax and ymin < ymax and zmin <= zmax


def _normalise_bounds_value(value: Any) -> dict:
    """
    Normalise a raw PyEnSight bounds-like value into xmin,xmax,ymin,ymax,zmin,zmax.
    Accepts exactly one unambiguous layout:
      A: [xmin, xmax, ymin, ymax, zmin, zmax]
      B: [xmin, ymin, zmin, xmax, ymax, zmax]
    """
    nums = _flatten_numeric_values(value)
    result: dict = {
        "numeric_values": nums,
        "status": "not_six_numeric",
        "bounds": None,
        "format": "",
        "error": "",
    }
    if len(nums) != 6:
        result["error"] = f"expected 6 numeric values, got {len(nums)}"
        return result

    a_bounds = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
    b_bounds = (nums[0], nums[3], nums[1], nums[4], nums[2], nums[5])
    candidates: List[Tuple[str, Tuple[float, float, float, float, float, float]]] = []
    if _valid_bounds_tuple(a_bounds):
        candidates.append(("xmin_xmax_ymin_ymax_zmin_zmax", a_bounds))
    if _valid_bounds_tuple(b_bounds):
        candidates.append(("xmin_ymin_zmin_xmax_ymax_zmax", b_bounds))

    if len(candidates) == 1:
        fmt, bounds = candidates[0]
        result.update({
            "status": "success",
            "bounds": list(bounds),
            "format": fmt,
            "error": "",
        })
        return result

    if len(candidates) > 1:
        result.update({
            "status": "ambiguous",
            "candidate_formats": [
                {"format": fmt, "bounds": list(bounds)}
                for fmt, bounds in candidates
            ],
            "error": "both supported bounds layouts are plausible",
        })
        return result

    result.update({
        "status": "invalid_order",
        "error": "six numeric values did not satisfy either supported min/max layout",
    })
    return result


def attempt_object_bounds(obj: Any) -> Tuple[List[dict], List[dict]]:
    """Try all supported PyEnSight bounds APIs on one object."""
    attempts: List[dict] = []
    successes: List[dict] = []

    for attempt_kind, name, arg in BOUNDS_API_ATTEMPTS:
        label = f"{name}({arg!r})" if arg is not None else name
        entry: dict = {
            "api": label,
            "kind": attempt_kind,
            "status": "",
            "raw_preview": None,
            "normalization": None,
            "error": "",
        }
        try:
            if attempt_kind == "attr":
                raw = getattr(obj, name)
            elif attempt_kind == "method":
                method = getattr(obj, name)
                if not callable(method):
                    raise TypeError(f"{name} exists but is not callable")
                raw = method()
            elif attempt_kind == "method_arg":
                method = getattr(obj, name)
                if not callable(method):
                    raise TypeError(f"{name} exists but is not callable")
                raw = method(arg)
            else:
                raise RuntimeError(f"unknown attempt kind: {attempt_kind}")

            entry["raw_preview"] = _safe_preview(raw)
            norm = _normalise_bounds_value(raw)
            entry["normalization"] = norm
            entry["status"] = norm["status"]
            if norm["status"] == "success":
                successes.append({
                    "api": label,
                    "format": norm["format"],
                    "bounds": norm["bounds"],
                })
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        attempts.append(entry)

    return attempts, successes


def _object_description(obj: Any) -> Optional[str]:
    value, err = _safe_attr(obj, "DESCRIPTION")
    if err is not None or value in (None, ""):
        return None
    return str(value)


def _object_name(obj: Any) -> Optional[str]:
    for attr in ("name", "NAME"):
        value, err = _safe_attr(obj, attr)
        if err is None and value not in (None, ""):
            return str(value)
    return None


def _object_visible(obj: Any) -> Optional[Any]:
    value, err = _safe_attr(obj, "VISIBLE")
    return None if err else _safe_preview(value)


def _object_selected(obj: Any) -> Optional[Any]:
    value, err = _safe_attr(obj, "SELECTED")
    return None if err else _safe_preview(value)


def _object_label(obj: Any) -> str:
    desc = _object_description(obj)
    name = _object_name(obj)
    pnum, pnum_err = _safe_attr(obj, "PARTNUMBER")
    pieces = []
    if desc:
        pieces.append(f"DESCRIPTION={desc}")
    if name and name != desc:
        pieces.append(f"name={name}")
    if pnum_err is None:
        pieces.append(f"PARTNUMBER={pnum}")
    return ", ".join(pieces) if pieces else repr(obj)


def _object_search_text(obj: Any) -> str:
    vals = [_object_description(obj), _object_name(obj)]
    return " ".join(v for v in vals if v).lower()


def _object_matches_any_keyword(obj: Any, keywords: Tuple[str, ...]) -> bool:
    text = _object_search_text(obj)
    return any(kw.lower() in text for kw in keywords)


def _is_visible(obj: Any) -> bool:
    value, err = _safe_attr(obj, "VISIBLE")
    if err is not None:
        return False
    return bool(value)


def _is_selected(obj: Any) -> bool:
    value, err = _safe_attr(obj, "SELECTED")
    if err is not None:
        return False
    return bool(value)


def _dedupe_objects(objects: List[Any]) -> List[Any]:
    seen: set = set()
    result: List[Any] = []
    for obj in objects:
        key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        result.append(obj)
    return result


def _accumulate_bounds(
    bounds_list: List[List[float]],
) -> Optional[Tuple[float, float, float, float, float, float]]:
    if not bounds_list:
        return None
    xmin = min(min(b[0], b[1]) for b in bounds_list)
    xmax = max(max(b[0], b[1]) for b in bounds_list)
    ymin = min(min(b[2], b[3]) for b in bounds_list)
    ymax = max(max(b[2], b[3]) for b in bounds_list)
    zmin = min(min(b[4], b[5]) for b in bounds_list)
    zmax = max(max(b[4], b[5]) for b in bounds_list)
    return (xmin, xmax, ymin, ymax, zmin, zmax)


def _first_success_for_object(obj: Any) -> Tuple[Optional[dict], List[dict]]:
    attempts, successes = attempt_object_bounds(obj)
    return (successes[0] if successes else None), attempts


def _bounds_candidate_groups(session: Any, target_parts: List[Any]) -> List[Tuple[str, List[Any]]]:
    try:
        all_parts = list(session.ensight.objs.core.PARTS)
    except Exception:
        all_parts = list(target_parts)

    selected_membrane = [
        p for p in all_parts
        if _is_selected(p)
        and _object_matches_any_keyword(
            p, ("wall_top_mem", "wall_bottom_mem", "membrane", "top", "bottom")
        )
    ]
    visible_named_membrane = [
        p for p in all_parts
        if _is_visible(p)
        and _object_matches_any_keyword(p, ("wall_top_mem", "wall_bottom_mem"))
    ]
    visible_parts = [p for p in all_parts if _is_visible(p)]

    return [
        ("displayed_contour_parts", _dedupe_objects(list(target_parts))),
        ("selected_membrane_candidates", _dedupe_objects(selected_membrane)),
        ("visible_named_membrane_parts", _dedupe_objects(visible_named_membrane)),
        ("visible_parts", _dedupe_objects(visible_parts)),
        ("all_parts", _dedupe_objects(all_parts)),
    ]


def select_membrane_view_bounds(session: Any, target_parts: List[Any]) -> dict:
    """
    Find usable display bounds by priority:
      1. displayed/exported contour parts
      2. selected membrane candidates
      3. visible wall_top_mem/wall_bottom_mem candidates
      4. visible parts
      5. all parts
    """
    candidate_attempts: List[dict] = []
    candidate_successes: List[dict] = []
    chosen_bounds: Optional[Tuple[float, float, float, float, float, float]] = None
    chosen_candidate = ""
    chosen_format = ""

    for group_name, objects in _bounds_candidate_groups(session, target_parts):
        group_success_bounds: List[List[float]] = []
        group_success_formats: List[str] = []
        group_entry = {
            "candidate_group": group_name,
            "object_count": len(objects),
            "objects": [],
        }
        for obj in objects:
            attempts, successes = attempt_object_bounds(obj)
            object_entry = {
                "object_label": _object_label(obj),
                "python_type": f"{type(obj).__module__}.{type(obj).__qualname__}",
                "attempts": attempts,
            }
            group_entry["objects"].append(object_entry)
            if successes:
                first = successes[0]
                group_success_bounds.append(list(first["bounds"]))
                group_success_formats.append(str(first["format"]))
                candidate_successes.append({
                    "candidate_group": group_name,
                    "object_label": _object_label(obj),
                    "api": first["api"],
                    "format": first["format"],
                    "bounds": first["bounds"],
                })

        candidate_attempts.append(group_entry)

        if group_success_bounds and chosen_bounds is None:
            chosen_bounds = _accumulate_bounds(group_success_bounds)
            chosen_candidate = group_name
            unique_formats = sorted(set(group_success_formats))
            chosen_format = (
                unique_formats[0]
                if len(unique_formats) == 1 and len(group_success_bounds) == 1
                else "aggregate_" + "_and_".join(unique_formats)
            )
            candidate_successes.append({
                "candidate_group": group_name,
                "object_label": "__group_accumulated_bounds__",
                "api": "aggregate_first_success_per_object",
                "format": chosen_format,
                "bounds": list(chosen_bounds) if chosen_bounds else None,
            })
            break

    return {
        "bounds": chosen_bounds,
        "bounds_candidate_attempts": candidate_attempts,
        "bounds_candidate_successes": candidate_successes,
        "chosen_bounds_candidate": chosen_candidate,
        "chosen_bounds_format": chosen_format,
    }


def inspect_pyensight_object_for_bounds(
    obj: Any,
    collection: str,
    categories: List[str],
) -> dict:
    try:
        public_attrs = [a for a in dir(obj) if not a.startswith("_")]
    except Exception as exc:
        public_attrs = []
        dir_error = f"{type(exc).__name__}: {exc}"
    else:
        dir_error = ""

    uppercase_attrs = [a for a in public_attrs if a.upper() == a]
    keyword_attrs = [
        a for a in public_attrs
        if any(kw in a.lower() for kw in BOUNDS_ATTR_KEYWORDS)
    ]
    attr_previews: dict = {}
    for attr in keyword_attrs:
        lower_attr = attr.lower()
        if not any(kw in lower_attr for kw in ("bound", "extent", "min", "max", "box", "range")):
            continue
        value, err = _safe_attr(obj, attr)
        attr_previews[attr] = {"error": err} if err else _safe_preview(value)

    attempts, successes = attempt_object_bounds(obj)

    return {
        "collection": collection,
        "categories": sorted(set(categories)),
        "python_type": f"{type(obj).__module__}.{type(obj).__qualname__}",
        "repr": repr(obj),
        "DESCRIPTION": _object_description(obj),
        "name": _object_name(obj),
        "VISIBLE": _object_visible(obj),
        "SELECTED": _object_selected(obj),
        "dir_error": dir_error,
        "uppercase_public_attributes": uppercase_attrs,
        "public_attributes_matching_bounds_keywords": keyword_attrs,
        "matching_attribute_value_previews": attr_previews,
        "attempted_bounds": attempts,
        "bounds_successes": successes,
    }


def _iter_core_collection_objects(session: Any) -> List[Tuple[str, Any]]:
    try:
        core = session.ensight.objs.core
    except Exception:
        return []

    objects: List[Tuple[str, Any]] = []
    seen_collections: set = set()
    collection_names: List[str] = ["PARTS", "VARIABLES", "PALETTES", "VPORTS"]
    try:
        collection_names.extend(
            name for name in dir(core)
            if not name.startswith("_") and name.upper() == name
        )
    except Exception:
        pass

    for collection_name in collection_names:
        if collection_name in seen_collections:
            continue
        seen_collections.add(collection_name)
        try:
            collection = getattr(core, collection_name)
        except Exception:
            continue
        if isinstance(collection, (str, bytes)):
            continue
        try:
            items = list(collection)
        except Exception:
            continue
        for item in items:
            objects.append((collection_name, item))
    return objects


def collect_pyensight_bounds_diagnostics(
    session: Any,
    field_key: str,
    display_var_desc: str,
    surface_desc: str,
    target_parts: List[Any],
    bounds_selection: dict,
    manual_view_bounds: Optional[Tuple[float, float, float, float]],
    manual_view_plane: str,
) -> dict:
    try:
        all_parts = list(session.ensight.objs.core.PARTS)
    except Exception:
        all_parts = []

    visible_parts = [p for p in all_parts if _is_visible(p)]
    selected_membrane_candidates = [
        p for p in all_parts
        if _is_selected(p)
        and _object_matches_any_keyword(
            p, ("wall_top_mem", "wall_bottom_mem", "membrane", "top", "bottom")
        )
    ]

    registry: dict = {}

    def _add(obj: Any, collection: str, category: str) -> None:
        key = id(obj)
        if key not in registry:
            registry[key] = {"obj": obj, "collection": collection, "categories": []}
        registry[key]["categories"].append(category)

    for p in all_parts:
        _add(p, "PARTS", "core.PARTS")
    for p in visible_parts:
        _add(p, "PARTS", "visible_parts")
    for p in selected_membrane_candidates:
        _add(p, "PARTS", "selected_membrane_candidates")
    for p in target_parts:
        _add(p, "PARTS", "field_displayed_contour_parts")

    for collection_name, obj in _iter_core_collection_objects(session):
        if _object_matches_any_keyword(obj, BOUNDS_DIAG_NAME_KEYWORDS):
            _add(obj, collection_name, "keyword_name_or_description_match")

    inspected_objects = [
        inspect_pyensight_object_for_bounds(
            item["obj"], item["collection"], item["categories"]
        )
        for item in registry.values()
    ]

    return {
        "field_key": field_key,
        "display_variable": display_var_desc,
        "surface_desc": surface_desc,
        "manual_view_bounds": list(manual_view_bounds) if manual_view_bounds else None,
        "manual_view_plane": manual_view_plane,
        "counts": {
            "core_PARTS": len(all_parts),
            "visible_parts": len(visible_parts),
            "selected_membrane_candidates": len(selected_membrane_candidates),
            "field_displayed_contour_parts": len(target_parts),
            "inspected_objects": len(inspected_objects),
        },
        "bounds_selection": bounds_selection,
        "inspected_objects": inspected_objects,
    }


def write_pyensight_bounds_diagnostics(
    diagnostics_path: Path,
    diagnostics_records: List[dict],
    geo_name: str,
    case_name: str,
) -> None:
    if not _safe_mkdir(diagnostics_path.parent):
        print(
            f"  NOTE: Diagnostics directory contains a Windows drive path ({diagnostics_path.parent}); "
            "skipping diagnostics JSON write on Linux/WSL."
        )
        return
    payload = {
        "geo_name": geo_name,
        "case_name": case_name,
        "diagnostic_record_count": len(diagnostics_records),
        "records": diagnostics_records,
    }
    with diagnostics_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"Bounds diagnostics saved: {diagnostics_path}")


def print_pyensight_bounds_diagnostics(entry: dict) -> None:
    print()
    print("=" * 72)
    print(f"PYENSIGHT BOUNDS DIAGNOSTICS: {entry.get('field_key')}")
    print("=" * 72)
    counts = entry.get("counts", {})
    print(
        "  counts: "
        f"PARTS={counts.get('core_PARTS')} "
        f"visible={counts.get('visible_parts')} "
        f"selected_membrane={counts.get('selected_membrane_candidates')} "
        f"displayed={counts.get('field_displayed_contour_parts')} "
        f"inspected={counts.get('inspected_objects')}"
    )
    selection = entry.get("bounds_selection", {})
    print(
        "  chosen: "
        f"candidate={selection.get('chosen_bounds_candidate') or 'none'} "
        f"format={selection.get('chosen_bounds_format') or 'none'} "
        f"bounds={selection.get('bounds')}"
    )
    for obj_entry in entry.get("inspected_objects", []):
        print("-" * 72)
        print(
            f"  [{obj_entry.get('collection')}] "
            f"{', '.join(obj_entry.get('categories', []))}"
        )
        print(f"    type       : {obj_entry.get('python_type')}")
        print(f"    repr       : {obj_entry.get('repr')}")
        print(f"    DESCRIPTION: {obj_entry.get('DESCRIPTION')}")
        print(f"    name       : {obj_entry.get('name')}")
        print(f"    VISIBLE    : {obj_entry.get('VISIBLE')}  SELECTED: {obj_entry.get('SELECTED')}")
        print(f"    uppercase attrs: {obj_entry.get('uppercase_public_attributes')}")
        print(
            "    bounds-like attrs: "
            f"{obj_entry.get('public_attributes_matching_bounds_keywords')}"
        )
        attempt_summaries = []
        for attempt in obj_entry.get("attempted_bounds", []):
            summary = f"{attempt.get('api')}={attempt.get('status')}"
            if attempt.get("error"):
                summary += f"({attempt.get('error')})"
            norm = attempt.get("normalization") or {}
            if norm.get("bounds"):
                summary += f" bounds={norm.get('bounds')} fmt={norm.get('format')}"
            attempt_summaries.append(summary)
        print(f"    bounds attempts: {'; '.join(attempt_summaries)}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Find ENS_PALETTE — legacy helper (normalized DESCRIPTION match only)
# ---------------------------------------------------------------------------

def find_palette_for_variable(session: Any, var_desc: str) -> Optional[Any]:
    """Return the ENS_PALETTE matching var_desc, or None. Normalized match."""
    try:
        all_palettes = list(session.ensight.objs.core.PALETTES)
        desc_to_pal = {p.DESCRIPTION: p for p in all_palettes if p.DESCRIPTION}

        if var_desc in desc_to_pal:
            return desc_to_pal[var_desc]

        norm_map = {_normalize(k): v for k, v in desc_to_pal.items()}
        cn = _normalize(var_desc)
        if cn in norm_map:
            return norm_map[cn]

        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Multi-strategy palette finder — called after COLORBYPALETTE has been applied
# ---------------------------------------------------------------------------

def get_palette_after_colorby(
    session: Any,
    variable: Any,
    var_desc: str,
    selected_parts: List[Any],
    pre_palette_descs: Optional[FrozenSet[str]] = None,
) -> Tuple[Optional[Any], str]:
    """
    Find the active ENS_PALETTE after COLORBYPALETTE has been applied.
    Tries multiple strategies in order. Returns (palette_or_None, diagnostic_str).

    pre_palette_descs: frozenset of DESCRIPTION strings from PALETTES *before*
    COLORBYPALETTE was called (used for snapshot-diff, Strategy 4).
    DESCRIPTION keys are used rather than Python id() because PyEnSight may
    return fresh wrapper objects on each PALETTES access.
    """
    tried: List[str] = []

    # Strategy 1: variable object has a palette attribute
    for attr in ("PALETTE", "palette", "ENS_PALETTE"):
        try:
            raw = getattr(variable, attr, None)
            if raw is None:
                continue
            # Guard against list/collection return — take first element
            if hasattr(raw, "__iter__") and not hasattr(raw, "MINMAX"):
                items = list(raw)
                raw = items[0] if items else None
            if raw is not None:
                tried.append(f"S1(var.{attr}=found)")
                return raw, "; ".join(tried)
        except Exception as exc:
            tried.append(f"S1(var.{attr}=err:{exc})")

    # Strategy 2: selected parts expose the active palette
    for p in selected_parts[:3]:
        part_lbl = getattr(p, "DESCRIPTION", "?")
        for attr in ("COLORBYPALETTE_OBJ", "palette", "PALETTE", "ENS_PALETTE"):
            try:
                raw = getattr(p, attr, None)
                if raw is None:
                    continue
                if hasattr(raw, "__iter__") and not hasattr(raw, "MINMAX"):
                    items = list(raw)
                    raw = items[0] if items else None
                if raw is not None:
                    tried.append(f"S2(part[{part_lbl}].{attr}=found)")
                    return raw, "; ".join(tried)
            except Exception:
                pass
    tried.append("S2(part-attrs=not-found)")

    # Strategy 3: core object has a current/active palette attribute
    try:
        core = session.ensight.objs.core
        for attr in ("CURRENTPALETTE", "current_palette", "ACTIVE_PALETTE", "active_palette"):
            try:
                raw = getattr(core, attr, None)
                if raw is None:
                    continue
                if hasattr(raw, "__iter__") and not hasattr(raw, "MINMAX"):
                    items = list(raw)
                    raw = items[0] if items else None
                if raw is not None:
                    tried.append(f"S3(core.{attr}=found)")
                    return raw, "; ".join(tried)
            except Exception:
                pass
    except Exception as exc:
        tried.append(f"S3(core-active=err:{exc})")
    tried.append("S3(core-active=not-found)")

    # Collect all current palettes for remaining strategies
    all_palettes: List[Any] = []
    all_descs: List[str] = []
    try:
        all_palettes = list(session.ensight.objs.core.PALETTES)
        all_descs = [getattr(p, "DESCRIPTION", "") for p in all_palettes]
    except Exception as exc:
        tried.append(f"PALETTES-list-err:{exc}")
        return None, "; ".join(tried)

    # Strategy 4: snapshot diff by DESCRIPTION — find palettes added after COLORBYPALETTE
    if pre_palette_descs is not None:
        new_pals = [
            p for p, d in zip(all_palettes, all_descs) if d not in pre_palette_descs
        ]
        if len(new_pals) == 1:
            desc = getattr(new_pals[0], "DESCRIPTION", "?")
            tried.append(f"S4(snapshot-diff,1-new='{desc}'=found)")
            return new_pals[0], "; ".join(tried)
        elif new_pals:
            new_descs = [getattr(p, "DESCRIPTION", "?") for p in new_pals]
            tried.append(f"S4(snapshot-diff,{len(new_pals)}-new=ambiguous:{new_descs})")
        else:
            tried.append(
                f"S4(snapshot-diff=no-new; pre={sorted(pre_palette_descs)[:5]})"
            )

    # Strategy 5: exactly one palette in the collection
    if len(all_palettes) == 1:
        tried.append(f"S5(single-palette='{all_descs[0]}'=found)")
        return all_palettes[0], "; ".join(tried)

    # Fallback S6: normalized DESCRIPTION match
    norm_var = _normalize(var_desc)
    for pal, desc in zip(all_palettes, all_descs):
        if _normalize(desc) == norm_var:
            tried.append(f"S6(norm-desc-match='{desc}'=found)")
            return pal, "; ".join(tried)

    # Fallback S7: partial fragment match
    frags = [f for f in re.split(r"[-_ ]+", var_desc.lower()) if len(f) >= 2]
    for pal, desc in zip(all_palettes, all_descs):
        if any(frag in _normalize(desc) for frag in frags):
            tried.append(f"S7(frag-match='{desc}'=found)")
            return pal, "; ".join(tried)

    tried.append(
        f"all-strategies-failed for '{var_desc}'. "
        f"available({len(all_palettes)}): {all_descs[:15]}"
    )
    return None, "; ".join(tried)


# ---------------------------------------------------------------------------
# Apply palette color range
# ---------------------------------------------------------------------------

def apply_palette_range(
    session: Any,
    var_obj: Any,
    display_var_desc: str,
    selected_parts: List[Any],
    range_min: Optional[float],
    range_max: Optional[float],
    auto_range: bool,
    pre_palette_descs: Optional[FrozenSet[str]] = None,
) -> Tuple[str, Optional[float], Optional[float], str, Optional[Any]]:
    """
    Find the active palette and set its color range.
    Returns (mode_str, applied_min, applied_max, warn_msg, palette_obj_or_None).
    """
    palette, diag = get_palette_after_colorby(
        session, var_obj, display_var_desc, selected_parts, pre_palette_descs
    )

    if auto_range or (range_min is None or range_max is None):
        if palette is not None:
            try:
                palette.set_range_to_part_minmax()
                return RANGE_MODE_AUTO, None, None, "", palette
            except Exception as e1:
                try:
                    palette.set_range_to_viewport_minmax()
                    return RANGE_MODE_AUTO, None, None, (
                        f"Part minmax failed ({e1}); used viewport minmax."
                    ), palette
                except Exception as e2:
                    return RANGE_MODE_AUTO, None, None, (
                        f"Auto range failed: part={e1}, viewport={e2}."
                    ), palette
        return (
            RANGE_MODE_AUTO, None, None,
            f"Palette not found for '{display_var_desc}'; auto-range not applied. Diag: {diag}",
            None,
        )

    # Fixed range requested
    if palette is None:
        try:
            avail = [getattr(p, "DESCRIPTION", "?") for p in session.ensight.objs.core.PALETTES]
        except Exception:
            avail = ["<error listing palettes>"]
        return (
            RANGE_MODE_NONE, None, None,
            (
                f"Palette not found for '{display_var_desc}'; "
                f"range [{range_min},{range_max}] not applied. "
                f"Diag: {diag}. Available ({len(avail)}): {avail}"
            ),
            None,
        )

    try:
        palette.MINMAX = [range_min, range_max]
        # Verify readback
        try:
            actual = list(palette.MINMAX)
            if abs(actual[0] - range_min) < 1e-4 and abs(actual[1] - range_max) < 1e-4:
                return RANGE_MODE_FIXED, range_min, range_max, "", palette
            return (
                RANGE_MODE_FIXED, float(actual[0]), float(actual[1]),
                f"Range set [{range_min},{range_max}] but readback: "
                f"[{actual[0]:.4g},{actual[1]:.4g}].",
                palette,
            )
        except Exception:
            return RANGE_MODE_FIXED, range_min, range_max, "", palette
    except Exception as exc1:
        try:
            palette.set_minmax(range_min, range_max)
            return RANGE_MODE_FIXED, range_min, range_max, "", palette
        except Exception as exc2:
            return (
                RANGE_MODE_NONE, None, None,
                f"Range setting failed: MINMAX={exc1}; set_minmax={exc2}",
                palette,
            )


# ---------------------------------------------------------------------------
# Apply colorbar label (best-effort)
# ---------------------------------------------------------------------------

def apply_palette_label(
    session: Any,
    display_var_desc: str,
    field_label: str,
    palette: Optional[Any] = None,
) -> str:
    """Try to set the colorbar title. Returns warning string on failure, '' on success."""
    # Approach 1: command language
    try:
        session.ensight.palette.select_palette_begin(display_var_desc)
        session.ensight.palette.title(field_label)
        session.ensight.palette.select_palette_end()
        return ""
    except Exception:
        pass

    # Approach 2: rename DESCRIPTION on the palette object
    pal = palette
    if pal is None:
        pal = find_palette_for_variable(session, display_var_desc)
    if pal is not None:
        try:
            pal.DESCRIPTION = field_label
            return ""
        except Exception:
            pass

    return (
        f"Could not set colorbar label to '{field_label}' "
        f"(command language and DESCRIPTION rename both failed)."
    )


# ---------------------------------------------------------------------------
# Legend / colorbar layout — presets + best-effort attribute application.
#
# Source inspection of the installed ansys-pyensight-core 0.11.6 API stubs
# (ansys/api/pyensight/ens_annot_lgnd.py) confirms LOCATIONX, LOCATIONY,
# WIDTH, HEIGHT, SCALE, TEXTSIZE, SPECIFYLABELCOUNT, LABELCOUNT,
# TITLELOCATION, ORIENTATION, VISIBLE, RANGE, FORMAT all live on
# ENS_ANNOT_LGND ("legend" annotation objects, ANNOTTYPE ==
# ensight.objs.enums.ANNOT_LEGEND == 3) — NOT on ENS_PALETTE (inspection of
# ens_palette.py shows only color-mapping attributes: DESCRIPTION, MINMAX,
# INTERP, VARIABLE, etc., no layout attributes). ENS_ANNOT_LGND has no
# separate title-size attribute in this API version; VARCOMP
# (List[Tuple[ENS_VAR, int]]) is the only link back to the variable it
# represents, used below to find the right legend among possibly several.
# ---------------------------------------------------------------------------

LEGEND_TITLE_SIZE_ATTR_CANDIDATES: Tuple[str, ...] = (
    "TITLESIZE", "TITLE_TEXT_SIZE", "TITLETEXTSIZE",
)

# Scale factors are relative to whatever TEXTSIZE the legend currently has —
# units-agnostic, so "smaller than current" holds regardless of what that
# baseline actually is.
LEGEND_PRESETS: dict = {
    "default": {
        "x": None, "y": None, "width": None, "height": None,
        "text_size_scale": None, "title_size_scale": None, "label_count": None,
    },
    "presentation_right": {
        "x": 0.88, "y": 0.20, "width": 0.09, "height": 0.55,
        "text_size_scale": 0.70, "title_size_scale": 0.70, "label_count": 9,
    },
    "compact_right": {
        "x": 0.90, "y": 0.22, "width": 0.07, "height": 0.45,
        "text_size_scale": 0.55, "title_size_scale": 0.55, "label_count": 7,
    },
}

DEFAULT_LEGEND_OPTS: dict = {
    "mode": "hide",
    "preset": "presentation_right",
    "x": None, "y": None, "width": None, "height": None,
    "text_size": None, "title_size": None, "label_count": None,
    "reserve_right": 0.12,
}


def find_legend_annotation(
    session: Any,
    var_obj: Any,
    display_var_desc: str,
) -> Tuple[Optional[Any], str]:
    """
    Find the ENS_ANNOT_LGND (legend/colorbar annotation) for a variable.
    Tries, in order: (1) VARCOMP match by variable DESCRIPTION, (2) VARCOMP
    match by object identity, (3) legend DESCRIPTION match, (4) single
    remaining legend. Returns (annotation_or_None, diagnostic_str).
    """
    tried: List[str] = []
    try:
        annots = list(session.ensight.objs.core.ANNOTS)
    except Exception as exc:
        return None, f"core.ANNOTS list failed: {type(exc).__name__}: {exc}"

    legend_enum = None
    for enum_name in ("ANNOT_LEGEND", "ANNO_LGND"):
        try:
            legend_enum = getattr(session.ensight.objs.enums, enum_name)
            break
        except Exception:
            continue

    def _is_legend(a: Any) -> bool:
        if legend_enum is None:
            return True
        try:
            return getattr(a, "ANNOTTYPE", None) == legend_enum
        except Exception:
            return False

    legends = [a for a in annots if _is_legend(a)]
    tried.append(f"annots_total={len(annots)},legend_candidates={len(legends)}")

    var_desc_norm = _normalize(display_var_desc)

    # Strategy 1/2: VARCOMP match, by variable DESCRIPTION or object identity.
    for a in legends:
        try:
            varcomp = getattr(a, "VARCOMP", None)
        except Exception:
            continue
        if not varcomp:
            continue
        entries = varcomp if isinstance(varcomp[0], (list, tuple)) else [varcomp]
        for entry in entries:
            try:
                v = entry[0]
            except Exception:
                continue
            if v is var_obj:
                tried.append("matched_by_VARCOMP_identity")
                return a, "; ".join(tried)
            try:
                v_desc = getattr(v, "DESCRIPTION", None)
            except Exception:
                v_desc = None
            if v_desc is not None and _normalize(v_desc) == var_desc_norm:
                tried.append(f"matched_by_VARCOMP_description='{v_desc}'")
                return a, "; ".join(tried)

    # Strategy 3: legend DESCRIPTION match.
    for a in legends:
        try:
            desc = getattr(a, "DESCRIPTION", None)
        except Exception:
            desc = None
        if desc is not None and _normalize(desc) == var_desc_norm:
            tried.append(f"matched_by_legend_DESCRIPTION='{desc}'")
            return a, "; ".join(tried)

    # Strategy 4: exactly one legend annotation present.
    if len(legends) == 1:
        tried.append("single_legend_fallback")
        return legends[0], "; ".join(tried)

    tried.append(
        f"no_match; legend_descs={[getattr(a, 'DESCRIPTION', None) for a in legends][:10]}"
    )
    return None, "; ".join(tried)


def hide_legend_annotation(
    session: Any,
    var_obj: Any,
    display_var_desc: str,
) -> dict:
    """
    Best-effort hide of the ENS_ANNOT_LGND legend/colorbar for a variable.
    Reuses find_legend_annotation()'s VARCOMP-based lookup; if no field-
    specific legend is found, falls back to hiding every currently-visible
    legend annotation. Never raises and never signals a failure that should
    fail the overall contour export — worst case status is WARN. Does not
    touch part/geometry visibility. Returns dict with keys: status, method,
    error, visible_after.
    """
    result: dict = {"status": STATUS_SUCCESS, "method": "", "error": "", "visible_after": None}

    legend_annot, find_diag = find_legend_annotation(session, var_obj, display_var_desc)

    used_fallback = False
    targets: List[Any] = []
    if legend_annot is not None:
        targets = [legend_annot]
    else:
        used_fallback = True
        try:
            annots = list(session.ensight.objs.core.ANNOTS)
        except Exception as exc:
            result.update(
                status=STATUS_WARN, method="none",
                error=f"core.ANNOTS list failed: {type(exc).__name__}: {exc}. Diag: {find_diag}",
            )
            return result
        legend_enum = None
        for enum_name in ("ANNOT_LEGEND", "ANNO_LGND"):
            try:
                legend_enum = getattr(session.ensight.objs.enums, enum_name)
                break
            except Exception:
                continue
        for a in annots:
            try:
                is_legend = legend_enum is None or getattr(a, "ANNOTTYPE", None) == legend_enum
            except Exception:
                is_legend = False
            if is_legend and _is_visible(a):
                targets.append(a)
        if not targets:
            result.update(
                status=STATUS_WARN, method="none",
                error=(
                    f"No legend annotation found for '{display_var_desc}' and no "
                    f"visible legends to fall back on. Diag: {find_diag}"
                ),
            )
            return result

    applied: List[str] = []
    failed: List[str] = []
    visible_after: Optional[bool] = None
    for annot in targets:
        hidden = False
        for attr, value in (("VISIBLE", False), ("VISIBLE", 0), ("visible", False), ("visible", 0)):
            try:
                setattr(annot, attr, value)
                hidden = True
                applied.append(f"{attr}={value}")
                break
            except Exception as exc:
                failed.append(f"{attr}={value}: {type(exc).__name__}: {exc}")
        if hidden:
            try:
                visible_after = bool(getattr(annot, "VISIBLE"))
            except Exception:
                pass
        else:
            failed.append(f"could not hide annotation '{getattr(annot, 'DESCRIPTION', '?')}'")

    if applied and not failed:
        result.update(status=STATUS_SUCCESS, method="; ".join(applied), error="", visible_after=visible_after)
    elif applied:
        result.update(status=STATUS_WARN, method="; ".join(applied), error="; ".join(failed), visible_after=visible_after)
    else:
        result.update(
            status=STATUS_WARN, method="none",
            error="; ".join(failed) or "no attributes applied", visible_after=visible_after,
        )

    if used_fallback:
        fallback_note = (
            f"used fallback: hid {len(targets)} visible legend(s); "
            f"field-specific match failed. Diag: {find_diag}"
        )
        result["error"] = "; ".join(m for m in (result["error"], fallback_note) if m)
        if result["status"] == STATUS_SUCCESS:
            result["status"] = STATUS_WARN

    return result


def resolve_legend_layout_values(legend_opts: dict) -> dict:
    """Merge the selected preset with manual CLI overrides (overrides win)."""
    preset_name = legend_opts.get("preset", "presentation_right")
    preset = LEGEND_PRESETS.get(preset_name, LEGEND_PRESETS["presentation_right"])
    values: dict = {
        "x": preset.get("x"),
        "y": preset.get("y"),
        "width": preset.get("width"),
        "height": preset.get("height"),
        "label_count": preset.get("label_count"),
        "text_size_scale": preset.get("text_size_scale"),
        "title_size_scale": preset.get("title_size_scale"),
        "text_size_absolute": None,
        "title_size_absolute": None,
    }
    for key in ("x", "y", "width", "height", "label_count"):
        override = legend_opts.get(key)
        if override is not None:
            values[key] = override
    # Manual --legend-text-size / --legend-title-size are absolute and take
    # priority over the preset's relative scale.
    if legend_opts.get("text_size") is not None:
        values["text_size_absolute"] = legend_opts["text_size"]
        values["text_size_scale"] = None
    if legend_opts.get("title_size") is not None:
        values["title_size_absolute"] = legend_opts["title_size"]
        values["title_size_scale"] = None
    return values


def configure_legend_layout(
    session: Any,
    field_key: str,
    var_obj: Any,
    display_var_desc: str,
    legend_opts: dict,
) -> dict:
    """
    Best-effort legend/colorbar layout configuration for presentation output.
    Never raises; always returns a status dict so callers can record the
    outcome without risking the contour export itself. Keys: preset_requested,
    preset_applied, status, method, error, x, y, width, height, text_size,
    title_size, label_count.
    """
    preset_name = legend_opts.get("preset", "presentation_right")
    result: dict = {
        "preset_requested": preset_name,
        "preset_applied": "",
        "status": STATUS_SUCCESS,
        "method": "",
        "error": "",
        "x": None, "y": None, "width": None, "height": None,
        "text_size": None, "title_size": None, "label_count": None,
    }

    has_manual_override = any(
        legend_opts.get(k) is not None
        for k in ("x", "y", "width", "height", "text_size", "title_size", "label_count")
    )

    if preset_name == "default" and not has_manual_override:
        result.update(preset_applied="default", method="unchanged", error="")
        return result

    legend_annot, find_diag = find_legend_annotation(session, var_obj, display_var_desc)
    if legend_annot is None:
        result.update(
            preset_applied=preset_name, status=STATUS_WARN, method="none",
            error=(
                f"No ENS_ANNOT_LGND legend annotation found for '{display_var_desc}'; "
                f"layout not applied. Diag: {find_diag}"
            ),
        )
        return result

    values = resolve_legend_layout_values(legend_opts)
    applied: List[str] = []
    failed: List[str] = []

    def _try_set(attr_names: Any, value: Any, out_key: str) -> None:
        if value is None:
            return
        names = attr_names if isinstance(attr_names, (list, tuple)) else [attr_names]
        for attr in names:
            try:
                setattr(legend_annot, attr, value)
                result[out_key] = value
                applied.append(f"{attr}={value}")
                return
            except Exception as exc:
                failed.append(f"{attr}: {type(exc).__name__}: {exc}")
        failed.append(f"{out_key}: no supported attribute among {list(names)}")

    def _try_scale(attr_names: Any, scale: Optional[float], absolute: Optional[float], out_key: str) -> None:
        if absolute is not None:
            _try_set(attr_names, absolute, out_key)
            return
        if scale is None:
            return
        names = attr_names if isinstance(attr_names, (list, tuple)) else [attr_names]
        for attr in names:
            try:
                current = getattr(legend_annot, attr)
            except Exception:
                continue
            try:
                target_raw = float(current) * scale
                target = int(round(target_raw)) if isinstance(current, int) else target_raw
                setattr(legend_annot, attr, target)
                result[out_key] = target
                applied.append(f"{attr}={target}(scale={scale}xcurrent={current})")
                return
            except Exception as exc:
                failed.append(f"{attr}: {type(exc).__name__}: {exc}")
        failed.append(f"{out_key}: no readable/settable attribute among {list(names)}")

    _try_set("LOCATIONX", values.get("x"), "x")
    _try_set("LOCATIONY", values.get("y"), "y")
    _try_set("WIDTH", values.get("width"), "width")
    _try_set("HEIGHT", values.get("height"), "height")
    _try_scale("TEXTSIZE", values.get("text_size_scale"), values.get("text_size_absolute"), "text_size")
    _try_scale(
        list(LEGEND_TITLE_SIZE_ATTR_CANDIDATES),
        values.get("title_size_scale"), values.get("title_size_absolute"), "title_size",
    )
    if values.get("label_count") is not None:
        try:
            setattr(legend_annot, "SPECIFYLABELCOUNT", 1)
            applied.append("SPECIFYLABELCOUNT=1")
        except Exception as exc:
            failed.append(f"SPECIFYLABELCOUNT: {type(exc).__name__}: {exc}")
        _try_set("LABELCOUNT", values.get("label_count"), "label_count")

    result["preset_applied"] = preset_name
    if applied and not failed:
        result.update(status=STATUS_SUCCESS, method="; ".join(applied), error="")
    elif applied and failed:
        result.update(status=STATUS_WARN, method="; ".join(applied), error="; ".join(failed))
    else:
        result.update(status=STATUS_FAILED, method="none", error="; ".join(failed) or "no attributes applied")

    return result


# ---------------------------------------------------------------------------
# Clean scene setup — call once after loading the case
# ---------------------------------------------------------------------------

def setup_clean_scene(
    session: Any,
    background_mode: str,
    show_triad: bool,
) -> List[str]:
    """
    Configure the EnSight viewport for a presentation-ready appearance.
    All steps are best-effort; returns a list of warning strings.
    """
    warnings: List[str] = []

    # 1. Background colour
    try:
        vport = session.ensight.objs.core.VPORTS[0]
        if background_mode == "white":
            vport.BACKGROUNDTYPE = session.ensight.objs.enums.VPORT_CONS
            vport.CONSTANTRGB = [1.0, 1.0, 1.0]
        elif background_mode == "transparent":
            try:
                vport.BACKGROUNDTYPE = session.ensight.objs.enums.VPORT_TRANSPARENT
            except Exception as exc_t:
                warnings.append(
                    f"Transparent background unsupported ({exc_t}); falling back to white."
                )
                vport.BACKGROUNDTYPE = session.ensight.objs.enums.VPORT_CONS
                vport.CONSTANTRGB = [1.0, 1.0, 1.0]
        # "default" → leave untouched
    except Exception as exc:
        warnings.append(f"Background setup failed: {exc}")

    # 2. Orthographic projection
    try:
        vport = session.ensight.objs.core.VPORTS[0]
        vport.PERSPECTIVE = False
        session.ensight.view.perspective("OFF")
    except Exception as exc:
        warnings.append(f"Orthographic projection not set: {exc}")

    # 3. Axis / triad visibility
    if not show_triad:
        try:
            session.ensight.annotation.axis_global("off")
            session.ensight.annotation.axis_local("off")
            session.ensight.annotation.axis_model("off")
        except Exception as exc:
            warnings.append(f"Axis annotation hide failed: {exc}")
        try:
            vport = session.ensight.objs.core.VPORTS[0]
            vport.GLOBALAXISVISIBLE = 0
        except Exception:
            pass

    # 4. Disable floor / grid
    try:
        session.ensight.view.floor("OFF")
    except Exception as exc:
        warnings.append(f"Floor disable failed: {exc}")

    # 5. Disable shadow casting
    try:
        lights = session.ensight.objs.core.LIGHTSOURCES
        for light in lights:
            try:
                light.CASTS_SHADOWS = 0
            except Exception:
                pass
    except Exception as exc:
        warnings.append(f"Shadow disable failed: {exc}")

    # 6. Disable ambient occlusion
    try:
        session.ensight.view.ambient_occlusion("OFF")
    except Exception:
        pass

    return warnings


# ---------------------------------------------------------------------------
# Explicit post-fit zoom-out — applied after fit(), before image export.
# ---------------------------------------------------------------------------

def _read_vport_zoom_diag(session: Any) -> str:
    """Best-effort readback of any zoom/scale-like VPORT attribute exposed by
    this EnSight version, purely for console diagnostics (never raises)."""
    try:
        vport = session.ensight.objs.core.VPORTS[0]
    except Exception as exc:
        return f"vport_unavailable:{exc}"
    found = []
    for attr in ("ZOOM", "SCALE", "PARALLELSCALE", "ORTHOSCALE", "VIEWSCALE"):
        try:
            val = getattr(vport, attr, None)
            if val is not None:
                found.append(f"{attr}={val}")
        except Exception:
            pass
    return ",".join(found) if found else "no_known_zoom_attr_exposed"


def apply_zoom_out(session: Any, combined_factor: float) -> Tuple[str, str, str]:
    """
    Zoom the already-fit-and-oriented EnSight view out by combined_factor
    (the product of --view-margin and --zoom-out, applied as ONE call — see
    caller). Tries multiple PyEnSight strategies in order and stops at the
    first one that does not raise. None of the strategies below change view
    direction, up vector, or camera orientation — only scale/distance — so
    view orientation is always preserved.

    A single combined call (rather than two sequential zoom() calls) avoids
    ambiguity between cumulative and absolute zoom semantics across EnSight
    versions: two sequential calls would compound under cumulative semantics
    but one would silently override the other under absolute semantics.

    Returns (method_used, status, error_msg):
      status is "applied", "skipped" (factor <= 1.0), or "failed".
      "applied" only means the call did not raise; it is not proof the
      viewport actually changed scale (see console zoom-diag readback).
    """
    if combined_factor is None or combined_factor <= 1.0:
        return "none", "skipped", ""

    scale = 1.0 / combined_factor
    errors: List[str] = []
    before_diag = _read_vport_zoom_diag(session)

    # Strategy 1: view_transf.zoom — the same API --view-margin already uses.
    try:
        session.ensight.view_transf.zoom(scale)
        after_diag = _read_vport_zoom_diag(session)
        print(f"  zoom_diag before='{before_diag}' after='{after_diag}'")
        return "view_transf.zoom", "applied", ""
    except Exception as exc:
        errors.append(f"view_transf.zoom(scale={scale:.4g}): {exc}")

    # Strategy 2: raw command-language string, in case the Python binding for
    # view_transf.zoom differs from the underlying command-language function.
    try:
        session.ensight.command(f"view_transf: zoom {scale}")
        after_diag = _read_vport_zoom_diag(session)
        print(f"  zoom_diag before='{before_diag}' after='{after_diag}'")
        return "command_language:view_transf.zoom", "applied", ""
    except Exception as exc:
        errors.append(f"command('view_transf: zoom {scale}'): {exc}")

    # Strategy 3 (last resort; ineffective under orthographic projection, which
    # this script uses, since moving the camera along the view axis does not
    # change apparent size in true ortho — kept only in case perspective is
    # ever re-enabled): move the camera farther from its focal point along the
    # existing view direction (distance only; direction/up untouched).
    try:
        vport = session.ensight.objs.core.VPORTS[0]
        cam = getattr(vport, "CAMERA", None) or getattr(vport, "CURRENTCAMERA", None)
        if cam is not None and hasattr(cam, "LOOKFROM") and hasattr(cam, "LOOKAT"):
            look_from = list(cam.LOOKFROM)
            look_at = list(cam.LOOKAT)
            new_from = [
                look_at[i] + (look_from[i] - look_at[i]) * combined_factor
                for i in range(3)
            ]
            cam.LOOKFROM = new_from
            return "camera.LOOKFROM_distance", "applied", ""
        errors.append("camera object or LOOKFROM/LOOKAT attributes not available")
    except Exception as exc:
        errors.append(f"camera distance fallback: {exc}")

    return "failed", "failed", "; ".join(errors)


# ---------------------------------------------------------------------------
# Padded-bounds view fit — the PRIMARY anti-clipping mechanism. Must run
# AFTER any --view-margin / --zoom-out zoom() call (apply_zoom_out above),
# immediately before image export, so it is the last camera operation and
# overrides rather than gets overridden. Server testing showed the existing
# zoom() saturates before it can reveal the full padded region, and under
# the current scale = 1.0/(view_margin*zoom_out) formula it actually zooms
# IN (confirmed from server data: requested factor 3.00 -> scale 0.278 ->
# zoomed in; 0.75 -> scale 1.11 -> zoomed out; 0.10 -> scale 8.33 -> zoomed
# out but capped). That formula is left unchanged per instruction — this
# padded-bounds fit does not depend on it at all.
# ---------------------------------------------------------------------------

def fit_view_to_padded_bounds(
    session: Any,
    target_parts: List[Any],
    raw_bounds: Optional[Tuple[float, float, float, float, float, float]],
    padded_bounds: Optional[Tuple[float, float, float, float, float, float]],
    image_width: int,
    image_height: int,
) -> Tuple[str, str, str]:
    """
    Force the EnSight view to encompass padded_bounds without changing view
    direction, up vector, or camera orientation. Tries, in order:
      1. Re-select target_parts + fit(0) — re-anchors the fit baseline to
         exactly what is displayed for this contour, and as a side effect
         overrides any earlier zoom() call.
      2. Set the orthographic camera's parallel/view scale directly from
         padded_bounds, corrected for image aspect ratio (the clipped
         dimension is the streamwise/horizontal one, so the constraint from
         half_x must be divided by aspect, not compared against half_y raw —
         otherwise a tall-narrow viewport can shortchange exactly the
         dimension being fixed).
    Returns (method_used, status, error_msg). status is "applied" (a scale
    attribute was set without raising — not proof it visually changed
    anything), "partial" (only the fit(0) re-anchor succeeded), "skipped"
    (no bounds available), or "failed".
    """
    if raw_bounds is None or padded_bounds is None:
        return "none", "skipped", "bounds unavailable"

    errors: List[str] = []
    fit_ok = False
    try:
        session.ensight.utils.parts.select_parts(target_parts)
        session.ensight.view_transf.fit(0)
        fit_ok = True
    except Exception as exc:
        errors.append(f"reselect+fit(0): {exc}")

    xmin, xmax, ymin, ymax, _zmin, _zmax = padded_bounds
    half_x = (xmax - xmin) / 2.0
    half_y = (ymax - ymin) / 2.0
    aspect = (image_width / image_height) if image_height else 1.0
    # Ortho "parallel scale" is conventionally half the vertical extent; the
    # horizontal (streamwise) half-extent must be converted via aspect so it
    # is not silently under-covered.
    required_half_height = max(half_y, (half_x / aspect) if aspect else half_x)

    before_diag = _read_vport_zoom_diag(session)
    try:
        vport = session.ensight.objs.core.VPORTS[0]
        cam = getattr(vport, "CAMERA", None) or getattr(vport, "CURRENTCAMERA", None)
        if cam is not None:
            # Only try attribute names whose EnSight/VTK convention is
            # unambiguously "world-unit half-extent" (PARALLELSCALE/
            # ORTHOSCALE). Deliberately excludes SCALE/ZOOM-style names:
            # those are almost certainly multipliers (1.0 = fit), and setting
            # a multiplier to required_half_height (a sub-mm world value for
            # this membrane geometry) would silently zoom in to a tiny
            # fraction of the view while still reporting status="applied".
            for attr in ("PARALLELSCALE", "ORTHOSCALE"):
                try:
                    if hasattr(cam, attr):
                        setattr(cam, attr, required_half_height)
                        after_diag = _read_vport_zoom_diag(session)
                        print(
                            f"  bounds_fit_diag before='{before_diag}' after='{after_diag}' "
                            f"attr={attr} value={required_half_height:.6g}"
                        )
                        return f"camera.{attr}=padded_half_extent", "applied", ""
                except Exception as exc:
                    errors.append(f"camera.{attr}: {exc}")
        else:
            errors.append("camera object not available")
    except Exception as exc:
        errors.append(f"camera scale set: {exc}")

    if fit_ok:
        return "reselect_fit_only", "partial", "; ".join(errors)
    return "failed", "failed", "; ".join(errors)


# ---------------------------------------------------------------------------
# Export-time crop/viewport inspection — requirement 5: verify (or disable)
# any tight-crop/crop-to-content export behavior that could undo the fit
# above. Best-effort signature introspection; never raises.
# ---------------------------------------------------------------------------

def _inspect_export_image_options(session: Any) -> Tuple[bool, str, str]:
    """Return (export_crop_disabled, export_viewport_method, export_region_used)."""
    method = "utils.export.image(width,height,passes=4)"
    region = "full_viewport_as_rendered"
    try:
        sig = inspect.signature(session.ensight.utils.export.image)
        crop_like = [
            p for p in sig.parameters
            if any(kw in p.lower() for kw in ("crop", "tight", "bbox", "trim"))
        ]
        if crop_like:
            return False, method, f"crop-like_params_found_unset:{crop_like}"
        return True, method, region
    except Exception as exc:
        return True, method, f"{region} (signature_inspect_failed:{exc})"


# ---------------------------------------------------------------------------
# Debug bounds sweep — requirement 7. Diagnostic-only extra exports; never
# touches or replaces the main output.
# ---------------------------------------------------------------------------

# Fifth "zoomcmd" variant is a diagnostic-only probe: server testing
# confirmed session.ensight.view_transf.zoom(v) with v > 1 zooms OUT (not
# the 1.0/v formula apply_zoom_out uses for --zoom-out), so this calls it
# directly with the confirmed-correct sign. This guarantees the sweep shows
# a real zoom-out gradient even if fit_view_to_padded_bounds' camera-scale
# strategy finds no usable world-unit attribute (falls back to plain
# fit(0), which would otherwise make dbg_02/03/04 look identical). It does
# NOT change --zoom-out's CLI semantics or apply_zoom_out — confined here.
DEBUG_SWEEP_CONFIGS: List[Tuple[str, Optional[Any]]] = [
    ("bounds_dbg_01_current", None),
    ("bounds_dbg_02_margin_1p20", 1.20),
    ("bounds_dbg_03_margin_1p40", 1.40),
    ("bounds_dbg_04_margin_1p60", 1.60),
    ("bounds_dbg_05_zoomcmd_direct_out", "zoomcmd"),
]


def run_bounds_debug_sweep(
    session: Any,
    target_parts: List[Any],
    raw_bounds: Optional[Tuple[float, float, float, float, float, float]],
    output_file: Path,
    membrane_side: str,
    image_width: int,
    image_height: int,
    view_margin: float,
    zoom_out: float,
) -> List[str]:
    """
    Export extra diagnostic images at several fit strategies/margins for
    server-side visual comparison, using filenames like
    <stem>_<side>_bounds_dbg_02_margin_1p20.png. Never raises; a failed
    variant is skipped rather than aborting the sweep or touching the main
    output (which has already been written by the time this runs).
    """
    written: List[str] = []
    for label, margin_override in DEBUG_SWEEP_CONFIGS:
        dbg_path = output_file.parent / (
            f"{output_file.stem}_{membrane_side}_{label}{output_file.suffix}"
        )
        try:
            session.ensight.utils.parts.select_parts(target_parts)
            session.ensight.view_transf.fit(0)
        except Exception:
            pass

        try:
            if margin_override is None:
                # "current": replicate the OLD (pre-padded-bounds-fit)
                # behavior — plain fit + the existing view-margin/zoom-out
                # zoom() call only — so the server can visually compare it
                # against the new padded-bounds path below.
                combined = max(view_margin, 1.0) * max(zoom_out, 1.0)
                apply_zoom_out(session, combined)
            elif margin_override == "zoomcmd":
                session.ensight.view_transf.zoom(max(view_margin, 1.0))
            elif raw_bounds is not None:
                padded = compute_padded_bounds(raw_bounds, margin_override)
                fit_view_to_padded_bounds(
                    session, target_parts, raw_bounds, padded,
                    image_width, image_height,
                )
        except Exception:
            pass

        try:
            png_path = str(dbg_path).replace("\\", "/")
            session.ensight.utils.export.image(
                png_path, width=image_width, height=image_height, passes=4
            )
            print(f"Bounds debug output: {dbg_path}")
            if dbg_path.is_file():
                written.append(str(dbg_path))
        except Exception:
            pass

    # Leave the view in a known state for the visibility-restore step that
    # follows the sweep; harmless if fit() itself fails.
    try:
        session.ensight.view_transf.fit(0)
    except Exception:
        pass

    return written


# ---------------------------------------------------------------------------
# Open case in PyEnSight
# ---------------------------------------------------------------------------

def open_case_in_pyensight(paths: dict) -> Tuple[Optional[Any], str]:
    """Return (session, error_msg). error_msg is '' on success."""
    if not _PYENSIGHT_AVAILABLE:
        return None, (
            "ansys.pyensight.core is not installed or could not be imported.\n"
            f"  Install : pip install ansys-pyensight-core\n"
            f"  Error   : {_PYENSIGHT_IMPORT_ERROR}"
        )

    from ansys.pyensight.core import LocalLauncher

    case_file = Path(paths["final_case_file"])
    data_file = Path(paths["final_data_file"])

    if not case_file.is_file():
        return None, f"Case file not found: {case_file}"
    if not data_file.is_file():
        return None, f"Data file not found: {data_file}"

    session = None
    try:
        print("Launching PyEnSight (batch/headless mode)...")
        session = LocalLauncher().start()
        print("PyEnSight session started.")

        case_str = str(case_file).replace("\\", "/")
        data_str = str(data_file).replace("\\", "/")
        print(f"Loading case : {case_str}")
        print(f"Loading data : {data_str}")

        errors: List[str] = []
        loaded = False

        try:
            session.load_data(case_str, result_file=data_str)
            loaded = True
            print("Case loaded (attempt 1: dual-file via load_data).")
        except Exception as e1:
            errors.append(f"attempt1 (dual-file): {e1}")

        if not loaded:
            try:
                session.load_data(case_str)
                loaded = True
                print("Case loaded (attempt 2: case file only).")
            except Exception as e2:
                errors.append(f"attempt2 (case only): {e2}")

        if not loaded:
            try:
                session.load_data(case_str, result_file=data_str, file_format="Fluent Fluent")
                loaded = True
                print("Case loaded (attempt 3: explicit Fluent reader).")
            except Exception as e3:
                errors.append(f"attempt3 (explicit format): {e3}")

        if not loaded:
            return session, "All load attempts failed:\n  " + "\n  ".join(errors)

        return session, ""

    except Exception as exc:
        return session, f"Failed to start PyEnSight or load case: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Primitive variable detection helpers
# ---------------------------------------------------------------------------

def find_primitive_variable(
    session: Any,
    candidates: List[str],
    purpose: str = "",
) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
    """Return (var_obj, matched_desc, norm_desc) or (None, None, None)."""
    try:
        all_vars = list(session.ensight.objs.core.VARIABLES)
        desc_to_var = {v.DESCRIPTION: v for v in all_vars if v.DESCRIPTION}

        # Exact match
        for c in candidates:
            if c in desc_to_var:
                return desc_to_var[c], c, _normalize(c)

        # Normalized match
        norm_map = {_normalize(k): (k, v) for k, v in desc_to_var.items()}
        for c in candidates:
            cn = _normalize(c)
            if cn in norm_map:
                orig, v = norm_map[cn]
                return v, orig, cn

        # Substring match (lower-case)
        all_descs = list(desc_to_var.keys())
        for c in candidates:
            c_low = c.lower()
            for desc in all_descs:
                if c_low in desc.lower():
                    return desc_to_var[desc], desc, _normalize(desc)

        return None, None, None
    except Exception:
        return None, None, None


def find_fluid_volume_parts(session: Any) -> List[Any]:
    """Return list of 3-D (fluid volume) part objects."""
    try:
        vol_parts = list(session.ensight.utils.parts.select_parts_by_dimension(3))
        if vol_parts:
            return vol_parts
    except Exception:
        pass
    try:
        all_parts = list(session.ensight.objs.core.PARTS)
        return [p for p in all_parts if getattr(p, "DIMENSIONALITY", None) == 3]
    except Exception:
        return []


def _accumulate_parts_extents(
    parts: List[Any],
) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Shared bounds accumulation for get_part_bounding_box and
    get_membrane_view_bounds. Returns (xmin, xmax, ymin, ymax, zmin, zmax), or
    None if no part exposed a usable unambiguous bounding box."""
    usable_bounds: List[List[float]] = []
    for p in parts:
        first_success, _attempts = _first_success_for_object(p)
        if first_success and first_success.get("bounds"):
            usable_bounds.append(list(first_success["bounds"]))
    return _accumulate_bounds(usable_bounds)


def get_part_bounding_box(parts: List[Any]) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Return (xmin, xmax, ymin, ymax, zmin, zmax) from part EXTENTS, or None.
    Requires zmin < zmax — used for fluid-volume (3-D) center-plane z lookup,
    where a degenerate z-extent means the bounding box is not usable."""
    bounds = _accumulate_parts_extents(parts)
    if bounds is not None and bounds[4] < bounds[5]:
        return bounds
    return None


def get_membrane_view_bounds(
    parts: List[Any],
) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Return (xmin, xmax, ymin, ymax, zmin, zmax) from part EXTENTS for a
    near-planar membrane surface. Unlike get_part_bounding_box, does not
    require z-depth — a flat membrane can have zmin == zmax."""
    return _accumulate_parts_extents(parts)


def compute_padded_bounds(
    bounds: Tuple[float, float, float, float, float, float],
    view_margin: float,
) -> Tuple[float, float, float, float, float, float]:
    """Expand the x/y (in-plane, on-screen) extents of bounds around their
    center by view_margin. z (membrane-normal direction) is left unchanged —
    it is not the dimension that gets clipped."""
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    margin = max(view_margin, 1.0)
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    half_x = (xmax - xmin) / 2.0 * margin
    half_y = (ymax - ymin) / 2.0 * margin
    return (cx - half_x, cx + half_x, cy - half_y, cy + half_y, zmin, zmax)


def apply_legend_reserve_right(
    raw_bounds: Optional[Tuple[float, float, float, float, float, float]],
    padded_bounds: Optional[Tuple[float, float, float, float, float, float]],
    reserve_right_frac: Optional[float],
) -> Tuple[Optional[Tuple[float, float, float, float, float, float]], bool, str, str]:
    """
    Best-effort: widen the x-extent used for the camera fit by reserve_right_frac
    of the raw membrane width, so the presentation legend preset has visual
    whitespace to occupy. NOTE: fit_view_to_padded_bounds only sets a single
    symmetric camera scale (PARALLELSCALE/ORTHOSCALE) with no pan/lookat
    adjustment, so this cannot shift whitespace to the right side only — it
    adds extra zoom-out margin evenly around the already-centered geometry,
    which still gives the fixed-position right-side legend more room to avoid
    the membrane. Returns (new_padded_bounds, applied, method, error).
    """
    if not reserve_right_frac or reserve_right_frac <= 0.0:
        return padded_bounds, False, "none", ""
    if raw_bounds is None or padded_bounds is None:
        return padded_bounds, False, "none", "bounds unavailable; reserve not applied"

    try:
        xmin, xmax, ymin, ymax, zmin, zmax = padded_bounds
        raw_width = raw_bounds[1] - raw_bounds[0]
        extra = raw_width * reserve_right_frac
        new_bounds = (xmin, xmax + extra, ymin, ymax, zmin, zmax)
        return (
            new_bounds, True,
            "padded_bounds_xmax_extend_symmetric_scale_only", "",
        )
    except Exception as exc:
        return padded_bounds, False, "failed", f"{type(exc).__name__}: {exc}"


def _try_create_clip_at_z(
    session: Any,
    z_val: float,
    source_parts: List[Any],
    plane_name: str,
    parts_before: set,
) -> Tuple[Optional[Any], str, str]:
    """Create a z-normal clip plane at z=z_val using command language.
    Returns (plane_part_or_None, method_used, diag)."""
    diag: List[str] = []

    try:
        part_nums = [p.PARTNUMBER for p in source_parts if hasattr(p, "PARTNUMBER")]
    except Exception as e:
        return None, "", f"part_nums_err:{e}"
    if not part_nums:
        return None, "", "no_part_numbers"

    try:
        session.ensight.part.select_begin(*part_nums)
        session.ensight.clip.begin()
        session.ensight.clip.axis("z")
        session.ensight.clip.value(z_val)
        session.ensight.clip.end()
        diag.append(f"clip_cmd=ok,z={z_val:.6g}")
    except Exception as e:
        diag.append(f"clip_cmd=err:{e}")

    try:
        all_parts_after = list(session.ensight.objs.core.PARTS)
        new_parts = [
            p for p in all_parts_after
            if hasattr(p, "PARTNUMBER") and p.PARTNUMBER not in parts_before
        ]
        if new_parts:
            plane_part = new_parts[-1]
            try:
                plane_part.DESCRIPTION = plane_name
            except Exception:
                pass
            diag.append(f"new_part='{getattr(plane_part, 'DESCRIPTION', '?')}'")
            return plane_part, "clip_cmd", "; ".join(diag)
        diag.append("no_new_parts_after_clip")
    except Exception as e:
        diag.append(f"new_part_detect_err:{e}")

    return None, "", "; ".join(diag)


def compute_area_weighted_average_on_part(
    session: Any,
    salt_var_desc: str,
    plane_part: Any,
) -> Tuple[Optional[float], str]:
    """Compute area-weighted mean of salt_var_desc on plane_part via AMEAN.
    Returns (value, diag_str)."""
    diag: List[str] = []
    safe_var = (
        f"'{salt_var_desc}'"
        if (" " in salt_var_desc or "-" in salt_var_desc)
        else salt_var_desc
    )

    try:
        pnum = plane_part.PARTNUMBER
    except Exception as e:
        return None, f"PARTNUMBER_err:{e}"

    for fn_name in ("AMEAN", "Area_Mean", "AREA_MEAN", "amean"):
        try:
            temp_name = f"pp_bulk_avg_{fn_name.lower()}"
            session.ensight.part.select_begin(pnum)
            session.ensight.variables.evaluate(f"{temp_name} = {fn_name}({safe_var})")
            temp_var, _ = find_ensight_variable(session, [temp_name])
            if temp_var is not None:
                minmax = getattr(temp_var, "MINMAX", None)
                if minmax is not None:
                    vals = list(minmax)
                    if len(vals) >= 2 and vals[0] is not None:
                        avg = 0.5 * (float(vals[0]) + float(vals[1]))
                        diag.append(f"{fn_name}=success,val={avg:.6g}")
                        return avg, "; ".join(diag)
                diag.append(f"{fn_name}=var_found_MINMAX_empty")
            else:
                diag.append(f"{fn_name}=var_not_found")
        except Exception as e:
            diag.append(f"{fn_name}=err:{e}")

    return None, "; ".join(diag)


def compute_bulk_center_average(
    session: Any,
    salt_var_desc: str,
    fluid_parts: List[Any],
) -> Tuple[Optional[float], Optional[str], str, dict]:
    """Create a center bulk plane and compute area-weighted average of salt_var_desc.

    Tries multiple z candidates in order:
      1. z_mid from fluid-part bounding box
      2. z = 0.0  (channel-centred origin; confirmed by mesh + probe)
      3. z = CHANNEL_HEIGHT_M / 2  (legacy bottom-origin fallback only)

    For each candidate the plausibility of the area-average is verified before
    accepting, so implausible values from an off-location plane are skipped.

    Returns (avg_value, plane_name, summary_diag, full_diag_dict).
    full_diag_dict keys: strategy_attempts, candidate_z_values_tried,
    z_mid_from_bbox, center_plane_name, center_plane_creation_method,
    area_average_method, raw_area_average_value, plausibility_check,
    final_result.
    """
    plane_name = "pp_center_bulk_plane"
    full_diag: dict = {
        "strategy_attempts": [],
        "candidate_z_values_tried": [],
        "z_mid_from_bbox": None,
        "center_plane_name": None,
        "center_plane_creation_method": None,
        "area_average_method": None,
        "raw_area_average_value": None,
        "plausibility_check": None,
        "final_result": "failed",
    }

    # Reuse an already-created plane (avoids duplicate clip parts across fields)
    try:
        for p in list(session.ensight.objs.core.PARTS):
            if getattr(p, "DESCRIPTION", "") == plane_name:
                avg_val, avg_diag = compute_area_weighted_average_on_part(
                    session, salt_var_desc, p
                )
                if avg_val is not None:
                    full_diag.update({
                        "center_plane_name": plane_name,
                        "center_plane_creation_method": "reused_existing",
                        "area_average_method": avg_diag,
                        "raw_area_average_value": avg_val,
                        "final_result": "success_reuse",
                    })
                    full_diag["strategy_attempts"].append(
                        f"reused_existing: avg={avg_val:.6g}"
                    )
                    return avg_val, plane_name, f"reused_existing: avg={avg_val:.6g}", full_diag
    except Exception:
        pass

    # Build source part list for clip
    source_parts = fluid_parts if fluid_parts else []
    if not source_parts:
        try:
            source_parts = list(session.ensight.objs.core.PARTS)
        except Exception:
            pass

    # Build z-candidate list: bbox first, then known geometry fallbacks
    z_candidates: List[float] = []

    bbox = get_part_bounding_box(source_parts)
    if bbox is not None:
        _, _, _, _, z0, z1 = bbox
        z_mid = 0.5 * (z0 + z1)
        full_diag["z_mid_from_bbox"] = z_mid
        z_candidates.append(z_mid)
        full_diag["strategy_attempts"].append(
            f"bbox: z_mid={z_mid:.6g} from z=[{z0:.4g},{z1:.4g}]"
        )
    else:
        full_diag["strategy_attempts"].append("bbox: failed_to_get_bounding_box")

    for z_cand in CENTER_PLANE_Z_CANDIDATES:
        if not any(abs(z_cand - z) < 1e-9 for z in z_candidates):
            z_candidates.append(z_cand)

    full_diag["candidate_z_values_tried"] = list(z_candidates)

    is_conc = any(
        kw in _normalize(salt_var_desc)
        for kw in ["mol", "conc", "concentration"]
    )

    # Snapshot current part numbers before any clip
    try:
        parts_before = {
            p.PARTNUMBER
            for p in session.ensight.objs.core.PARTS
            if hasattr(p, "PARTNUMBER")
        }
    except Exception:
        parts_before = set()

    for z_cand in z_candidates:
        attempt_key = f"z={z_cand:.6g}"

        plane_part, method, clip_diag = _try_create_clip_at_z(
            session, z_cand, source_parts, plane_name, parts_before
        )

        if plane_part is None:
            full_diag["strategy_attempts"].append(
                f"{attempt_key}: clip_failed({clip_diag})"
            )
            continue

        # Update snapshot so next candidate detects only newly added parts
        try:
            parts_before = {
                p.PARTNUMBER
                for p in session.ensight.objs.core.PARTS
                if hasattr(p, "PARTNUMBER")
            }
        except Exception:
            pass

        avg_val, avg_diag = compute_area_weighted_average_on_part(
            session, salt_var_desc, plane_part
        )

        if avg_val is None:
            full_diag["strategy_attempts"].append(
                f"{attempt_key}: avg_failed({avg_diag})"
            )
            continue

        if is_conc:
            plausible = 50.0 <= avg_val <= 3000.0
            range_str = "[50,3000] mol/m3"
        else:
            plausible = 1e-4 <= avg_val <= 0.20
            range_str = "[1e-4,0.20] mass-frac"

        if not plausible:
            full_diag["strategy_attempts"].append(
                f"{attempt_key}: implausible(val={avg_val:.6g}, range={range_str})"
            )
            continue

        # Success
        full_diag.update({
            "center_plane_name": plane_name,
            "center_plane_creation_method": method,
            "area_average_method": avg_diag,
            "raw_area_average_value": avg_val,
            "plausibility_check": f"ok (val={avg_val:.6g}, range={range_str})",
            "final_result": "success",
        })
        full_diag["strategy_attempts"].append(
            f"{attempt_key}: success(method={method}, avg={avg_val:.6g})"
        )
        summary = f"ok: z={z_cand:.6g}, method={method}, avg={avg_val:.6g}"
        return avg_val, plane_name, summary, full_diag

    full_diag["final_result"] = "all_candidates_failed"
    summary = "all_candidates_failed: " + " | ".join(full_diag["strategy_attempts"])
    return None, None, summary, full_diag


def create_cp_wall_direct(
    session: Any,
    salt_var_desc: str,
    bulk_avg_value: float,
) -> Tuple[Optional[Any], Optional[str], str]:
    """Create CP_WALL_DIRECT = salt_var / bulk_avg_value on all parts.
    Returns (var_obj, var_desc, diag_str)."""
    derived_name = "CP_WALL_DIRECT"
    safe_var = (
        f"'{salt_var_desc}'"
        if (" " in salt_var_desc or "-" in salt_var_desc)
        else salt_var_desc
    )
    try:
        session.ensight.part.select_all()
        expr = f"{derived_name} = {safe_var} / {bulk_avg_value:.12g}"
        session.ensight.variables.evaluate(expr)
        var_obj, var_desc = find_ensight_variable(session, [derived_name])
        if var_obj is not None:
            return var_obj, derived_name, f"ok,expr='{expr}'"
        return None, None, f"evaluate_ok_but_var_not_found,expr='{expr}'"
    except Exception as e:
        return None, None, f"calculator_err:{e}"


def find_pressure_variable(
    session: Any,
    p_op: float,
) -> Tuple[Optional[str], bool, str]:
    """Find pressure variable suitable for LMH formula.
    Returns (pabs_expr_var_desc, is_absolute, diag_str).
    pabs_expr_var_desc is the EnSight variable name to use as absolute pressure
    (or None if unavailable).  is_absolute=True means variable is already absolute."""
    diag: List[str] = []

    # Prefer absolute pressure if exported by Fluent
    abs_candidates = [
        "absolute-pressure", "absolute_pressure",
        "Absolute Pressure", "absolute pressure",
        "Total Pressure", "total-pressure",
    ]
    abs_var, abs_desc = find_ensight_variable(session, abs_candidates)
    if abs_var is not None:
        diag.append(f"found_absolute='{abs_desc}'")
        return abs_desc, True, "; ".join(diag)

    # Fall back to gauge pressure + p_op
    gauge_candidates = [
        "pressure", "Pressure", "Static Pressure", "static-pressure",
        "gauge-pressure", "gauge_pressure",
    ]
    gauge_var, gauge_desc = find_ensight_variable(session, gauge_candidates)
    if gauge_var is not None:
        diag.append(f"found_gauge='{gauge_desc}',p_op={p_op:.0f}")
        # Create intermediate absolute pressure variable
        safe_g = (
            f"'{gauge_desc}'"
            if (" " in gauge_desc or "-" in gauge_desc)
            else gauge_desc
        )
        pabs_name = "pp_lmh_pabs"
        try:
            session.ensight.part.select_all()
            session.ensight.variables.evaluate(
                f"{pabs_name} = {safe_g} + {p_op:.2f}"
            )
            check, _ = find_ensight_variable(session, [pabs_name])
            if check is not None:
                diag.append(f"pabs_var_created='{pabs_name}'")
                return pabs_name, True, "; ".join(diag)
            diag.append("pabs_var_not_found_after_evaluate")
        except Exception as e:
            diag.append(f"pabs_create_err:{e}")
        # pp_lmh_pabs creation failed — do not return raw gauge desc; that would
        # silently feed dp = gauge - p_perm (wrong by p_op) to create_lmh_wall_direct.
        # Fall through to the "no pressure variable" path so LMH degrades to UDM_8.
        diag.append("gauge_found_but_pabs_build_failed; degrading_to_udm_fallback")
        return None, False, "; ".join(diag)

    diag.append("no_pressure_variable_found")
    return None, False, "; ".join(diag)


def create_water_flux_wall_direct(
    session: Any,
    salt_var_desc: str,
    pabs_var_desc: str,
    plan_item: dict,
) -> Tuple[Optional[Any], Optional[str], str, str]:
    """Create WATER_FLUX_WALL_DIRECT (Jw [m/s]) via the UDF solution-diffusion formula.
    Also creates intermediate pp_lmh_* variables shared with LMH and salt-flux derivation.
    Reuses any step variable already present from a prior field in the same session.
    Returns (var_obj, var_desc, diag_str, formula_summary)."""
    A      = plan_item["udf_a_perm"]
    B      = plan_item["udf_b_perm"]
    k      = plan_item["udf_kappa"]
    pperm  = plan_item["udf_p_perm"]
    mw     = plan_item["udf_mw_salt"]
    rho    = plan_item["udf_rho_ref"]

    formula_summary = (
        f"WATER_FLUX_WALL_DIRECT [m/s]: Jw=0.5*(S-B+sqrt((S+B)^2+4ABkC)); "
        f"S=A*(pabs-p_perm-k*C); C=rho*Y/mw; "
        f"A={A:.2e}, B={B:.2e}, k={k:.0f}, p_perm={pperm:.0f}, "
        f"mw={mw}, rho={rho}"
    )

    safe_salt = (
        f"'{salt_var_desc}'"
        if (" " in salt_var_desc or "-" in salt_var_desc)
        else salt_var_desc
    )
    safe_pabs = (
        f"'{pabs_var_desc}'"
        if (" " in pabs_var_desc or "-" in pabs_var_desc)
        else pabs_var_desc
    )

    diag: List[str] = []
    steps = [
        # molar concentration [mol/m³]
        ("pp_lmh_cm",
         f"pp_lmh_cm = {rho:.4f} * {safe_salt} / {mw:.5f}"),
        # transmembrane pressure [Pa]
        ("pp_lmh_dp",
         f"pp_lmh_dp = {safe_pabs} - {pperm:.2f}"),
        # S = A*(dp - k*cm)
        ("pp_lmh_sval",
         f"pp_lmh_sval = {A:.10e} * (pp_lmh_dp - {k:.4f} * pp_lmh_cm)"),
        # discriminant (>= 0 for physical concentrations)
        ("pp_lmh_disc",
         f"pp_lmh_disc = (pp_lmh_sval + {B:.10e}) * (pp_lmh_sval + {B:.10e}) "
         f"+ 4.0 * {A:.10e} * {B:.10e} * {k:.4f} * pp_lmh_cm"),
        # sqrt(max(disc, 0)) via 0.5*(disc + |disc|)
        ("pp_lmh_disc_safe",
         "pp_lmh_disc_safe = 0.5 * (pp_lmh_disc + abs(pp_lmh_disc))"),
        # Jw_raw (may be negative near very high-concentration regions)
        ("pp_lmh_jw_raw",
         f"pp_lmh_jw_raw = 0.5 * (pp_lmh_sval - {B:.10e} + sqrt(pp_lmh_disc_safe))"),
        # Jw = max(Jw_raw, 0) = 0.5*(Jw_raw + |Jw_raw|)
        ("WATER_FLUX_WALL_DIRECT",
         "WATER_FLUX_WALL_DIRECT = 0.5 * (pp_lmh_jw_raw + abs(pp_lmh_jw_raw))"),
    ]

    try:
        session.ensight.part.select_all()
        for step_var, expr in steps:
            existing, _ = find_ensight_variable(session, [step_var])
            if existing is not None:
                diag.append(f"{step_var}=reused")
                continue
            session.ensight.variables.evaluate(expr)
            check, _ = find_ensight_variable(session, [step_var])
            if check is None:
                diag.append(f"step_not_found:'{step_var}'")
                return None, None, "; ".join(diag), formula_summary
            diag.append(f"{step_var}=ok")

        var_obj, var_desc = find_ensight_variable(session, ["WATER_FLUX_WALL_DIRECT"])
        if var_obj is not None:
            return var_obj, "WATER_FLUX_WALL_DIRECT", "; ".join(diag), formula_summary
        return None, None, "WATER_FLUX_WALL_DIRECT_not_found", formula_summary
    except Exception as e:
        diag.append(f"calculator_err:{e}")
        return None, None, "; ".join(diag), formula_summary


def create_lmh_wall_direct(
    session: Any,
    salt_var_desc: str,
    pabs_var_desc: str,
    plan_item: dict,
) -> Tuple[Optional[Any], Optional[str], str, str]:
    """Create LMH_WALL_DIRECT [LMH] = WATER_FLUX_WALL_DIRECT * ms2lmh.
    Calls create_water_flux_wall_direct internally (reuses variables if already present).
    Returns (var_obj, var_desc, diag_str, formula_summary)."""
    ms2lmh = plan_item["udf_ms_to_lmh"]

    jw_var, _, jw_diag, jw_formula = create_water_flux_wall_direct(
        session, salt_var_desc, pabs_var_desc, plan_item
    )

    formula_summary = (
        f"LMH_WALL_DIRECT = WATER_FLUX_WALL_DIRECT * {ms2lmh:.1f} [m/s -> LMH]; "
        f"{jw_formula}"
    )

    if jw_var is None:
        return None, None, f"water_flux_step_failed: {jw_diag}", formula_summary

    existing, _ = find_ensight_variable(session, ["LMH_WALL_DIRECT"])
    if existing is not None:
        return existing, "LMH_WALL_DIRECT", f"{jw_diag}; LMH_WALL_DIRECT=reused", formula_summary

    try:
        session.ensight.part.select_all()
        session.ensight.variables.evaluate(
            f"LMH_WALL_DIRECT = WATER_FLUX_WALL_DIRECT * {ms2lmh:.1f}"
        )
        var_obj, var_desc = find_ensight_variable(session, ["LMH_WALL_DIRECT"])
        if var_obj is not None:
            return var_obj, "LMH_WALL_DIRECT", f"{jw_diag}; LMH_WALL_DIRECT=ok", formula_summary
        return None, None, f"{jw_diag}; LMH_WALL_DIRECT_not_found", formula_summary
    except Exception as e:
        return None, None, f"{jw_diag}; LMH_WALL_DIRECT_err:{e}", formula_summary


def create_salt_flux_wall_direct(
    session: Any,
    plan_item: dict,
) -> Tuple[Optional[Any], Optional[str], str, str]:
    """Create SALT_FLUX_WALL_DIRECT [kg/m2/s] = B*cm*Jw/(Jw+B)*MW_SALT.
    Requires pp_lmh_cm and WATER_FLUX_WALL_DIRECT to be present
    (call create_water_flux_wall_direct first).
    Returns (var_obj, var_desc, diag_str, formula_summary)."""
    B  = plan_item["udf_b_perm"]
    mw = plan_item["udf_mw_salt"]

    formula_summary = (
        f"SALT_FLUX_WALL_DIRECT [kg/m2/s]: "
        f"Js=B*cm*Jw/(Jw+B)*MW_SALT; "
        f"B={B:.2e}, MW_salt={mw:.5f}"
    )

    diag: List[str] = []

    existing, _ = find_ensight_variable(session, ["SALT_FLUX_WALL_DIRECT"])
    if existing is not None:
        diag.append("SALT_FLUX_WALL_DIRECT=reused")
        return existing, "SALT_FLUX_WALL_DIRECT", "; ".join(diag), formula_summary

    jw_var, _ = find_ensight_variable(session, ["WATER_FLUX_WALL_DIRECT"])
    cm_var, _ = find_ensight_variable(session, ["pp_lmh_cm"])

    if jw_var is None:
        return None, None, "prerequisite_missing:WATER_FLUX_WALL_DIRECT", formula_summary
    if cm_var is None:
        return None, None, "prerequisite_missing:pp_lmh_cm", formula_summary

    try:
        session.ensight.part.select_all()
        expr = (
            f"SALT_FLUX_WALL_DIRECT = "
            f"{B:.10e} * pp_lmh_cm * WATER_FLUX_WALL_DIRECT / "
            f"(WATER_FLUX_WALL_DIRECT + {B:.10e}) * {mw:.5f}"
        )
        session.ensight.variables.evaluate(expr)
        var_obj, var_desc = find_ensight_variable(session, ["SALT_FLUX_WALL_DIRECT"])
        if var_obj is not None:
            diag.append("ok")
            return var_obj, "SALT_FLUX_WALL_DIRECT", "; ".join(diag), formula_summary
        diag.append("var_not_found_after_evaluate")
        return None, None, "; ".join(diag), formula_summary
    except Exception as e:
        diag.append(f"calculator_err:{e}")
        return None, None, "; ".join(diag), formula_summary


def create_shear_rate_wall_direct(
    session: Any,
    wall_shear_var_desc: str,
    mu: float,
) -> Tuple[Optional[Any], Optional[str], str, str]:
    """Create SHEAR_RATE_WALL_DIRECT [1/s] = wall_shear_var / mu.
    Returns (var_obj, var_desc, diag_str, formula_summary)."""
    formula_summary = (
        f"SHEAR_RATE_WALL_DIRECT [1/s] = {wall_shear_var_desc} / mu; "
        f"mu={mu:.4e} Pa*s"
    )
    diag: List[str] = []

    existing, _ = find_ensight_variable(session, ["SHEAR_RATE_WALL_DIRECT"])
    if existing is not None:
        diag.append("SHEAR_RATE_WALL_DIRECT=reused")
        return existing, "SHEAR_RATE_WALL_DIRECT", "; ".join(diag), formula_summary

    safe_src = (
        f"'{wall_shear_var_desc}'"
        if ("-" in wall_shear_var_desc or " " in wall_shear_var_desc)
        else wall_shear_var_desc
    )

    try:
        session.ensight.part.select_all()
        expr = f"SHEAR_RATE_WALL_DIRECT = {safe_src} / {mu:.6e}"
        session.ensight.variables.evaluate(expr)
        var_obj, var_desc = find_ensight_variable(session, ["SHEAR_RATE_WALL_DIRECT"])
        if var_obj is not None:
            diag.append("ok")
            return var_obj, "SHEAR_RATE_WALL_DIRECT", "; ".join(diag), formula_summary
        diag.append("var_not_found_after_evaluate")
        return None, None, "; ".join(diag), formula_summary
    except Exception as e:
        diag.append(f"calculator_err:{e}")
        return None, None, "; ".join(diag), formula_summary


# ---------------------------------------------------------------------------
# PyFluent report CSV reader for CP bulk reference
# ---------------------------------------------------------------------------

def _read_pyfluent_bulk_center_avg(
    plan_item: dict,
) -> Tuple[Optional[float], str, str]:
    """Read c_bulk_center_area_avg from the PyFluent summary_metrics_wide.csv.

    Returns (value, units_or_type, diag). value is None when unavailable.
    units_or_type is 'mass_fraction' or 'molar_mol_m3' as written by 01_pyfluent_report_extract.py.
    """
    import csv as _csv

    case_path = Path(plan_item.get("case_path", ""))
    if not case_path.is_dir():
        return None, "", f"case_path_not_dir:{case_path}"

    csv_path = case_path / "post" / "reports" / "summary_metrics_wide.csv"
    if not csv_path.is_file():
        return None, "", f"csv_not_found:{csv_path}"

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)
    except Exception as exc:
        return None, "", f"csv_read_err:{exc}"

    if not rows:
        return None, "", "csv_empty"

    row = rows[0]
    explicit_candidates = [
        ("c_bulk_center_mass_fraction_avg", "mass_fraction"),
        ("c_bulk_center_mol_m3_avg", "molar_mol_m3"),
    ]
    for column_name, units_str in explicit_candidates:
        val_str = (row.get(column_name) or "").strip()
        if not val_str or val_str.lower() in ("none", "nan", ""):
            continue
        try:
            value = float(val_str)
        except ValueError:
            return None, units_str, f"{column_name}_not_numeric:{val_str!r}"
        return (
            value,
            units_str,
            f"read_ok,column={column_name},val={value:.6g},units={units_str}",
        )

    val_str = (row.get("c_bulk_center_area_avg") or "").strip()
    units_str = (row.get("c_bulk_center_area_avg_units_or_type") or "").strip()
    if not val_str or val_str.lower() in ("none", "nan", ""):
        return None, units_str, f"c_bulk_center_area_avg_missing_in_csv"

    try:
        value = float(val_str)
    except ValueError:
        return None, units_str, f"c_bulk_center_area_avg_not_numeric:{val_str!r}"

    return value, units_str, f"read_ok,val={value:.6g},units={units_str}"


# ---------------------------------------------------------------------------
# EnSight variable inventory dump (for shear_rate diagnostics)
# ---------------------------------------------------------------------------

def _dump_variable_inventory(session: Any, output_path: Path) -> str:
    """Dump all EnSight variable DESCRIPTION/TYPE/LOCATION metadata to JSON.

    Returns the path string on success, or an error note string on failure.
    """
    inventory: List[dict] = []
    try:
        for var in session.ensight.objs.core.VARIABLES:
            entry: dict = {}
            for attr in ("DESCRIPTION", "TYPE", "LOCATION", "DIMENSION", "UNITS", "VARTYPE"):
                try:
                    entry[attr] = str(getattr(var, attr, ""))
                except Exception:
                    entry[attr] = ""
            inventory.append(entry)
    except Exception as exc:
        inventory = [{"error": str(exc)}]

    _safe_mkdir(output_path.parent)
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(
                {"variable_count": len(inventory), "variables": inventory},
                fh,
                indent=2,
            )
        return str(output_path)
    except Exception as exc:
        return f"write_failed:{exc}"


# ---------------------------------------------------------------------------
# Export one contour image
# ---------------------------------------------------------------------------

def export_contour(
    session: Any,
    plan_item: dict,
    image_width: int,
    image_height: int,
    membrane_side: str,
    records: List[ExportRecord],
    geo_name: str,
    case_name: str,
    scene_setup_warnings: List[str],
    view_margin: float = 1.20,
    zoom_out: float = 1.15,
    bounds_debug_sweep: bool = False,
    bounds_diagnostics: bool = False,
    bounds_diagnostics_path: Optional[Path] = None,
    bounds_diagnostics_records: Optional[List[dict]] = None,
    manual_view_bounds: Optional[Tuple[float, float, float, float]] = None,
    manual_view_plane: str = "xy",
    legend_opts: Optional[dict] = None,
) -> None:
    field_key       = plan_item["field_key"]
    field_name      = plan_item["field_name"]
    output_file     = Path(plan_item["output_file"])
    surface_type    = plan_item["surface_type"]
    var_candidates  = plan_item["var_candidates"]
    derive_shear_rate = plan_item["derive_shear_rate"]
    mu              = plan_item["mu"]
    color_range_min = plan_item["color_range_min"]
    color_range_max = plan_item["color_range_max"]
    color_range_mode = plan_item["color_range_mode"]

    _safe_mkdir(output_file.parent)
    warnings: List[str] = []
    info_msgs: List[str] = []
    selected_var_str      = ""
    selected_surfaces_str = ""
    applied_mode  = color_range_mode
    applied_min: Optional[float] = color_range_min
    applied_max: Optional[float] = color_range_max
    palette_diag_str: Optional[str] = None
    derived_variable_mode: str = ""
    primitive_variables_used_str: str = ""
    bulk_reference_mode_str: str = ""
    bulk_reference_value_float: Optional[float] = None
    bulk_reference_units_str: str = ""
    center_plane_name_str: str = ""
    formula_summary_str: str = ""
    center_plane_diagnostics_dict: Optional[dict] = None
    zoom_out_applied_bool: bool = False
    zoom_out_method_str: str = ""
    zoom_out_status_str: str = ""
    zoom_out_error_str: str = ""
    membrane_bounds_raw_list: Optional[List[float]] = None
    membrane_bounds_padded_list: Optional[List[float]] = None
    membrane_bounds_source_str: str = ""
    fit_target_used_str: str = ""
    bounds_fit_attempted_bool: bool = False
    bounds_fit_status_str: str = ""
    bounds_fit_method_str: str = ""
    bounds_fit_error_str: str = ""
    bounds_candidate_attempts_list: List[dict] = []
    bounds_candidate_successes_list: List[dict] = []
    chosen_bounds_candidate_str: str = ""
    chosen_bounds_format_str: str = ""
    manual_view_bounds_requested_list: Optional[List[float]] = (
        list(manual_view_bounds) if manual_view_bounds else None
    )
    manual_view_plane_requested_str: str = manual_view_plane
    export_crop_disabled_bool: bool = True
    export_viewport_method_str: str = ""
    export_region_used_str: str = ""
    bounds_debug_sweep_attempted_bool: bool = False
    bounds_debug_sweep_outputs_list: List[str] = []
    legend_opts_eff: dict = legend_opts if legend_opts is not None else DEFAULT_LEGEND_OPTS
    legend_mode_str: str = str(legend_opts_eff.get("mode") or "hide").strip().lower()
    legend_preset_requested_str: str = str(legend_opts_eff.get("preset") or "")
    legend_preset_applied_str: str = ""
    legend_layout_status_str: str = ""
    legend_layout_method_str: str = ""
    legend_layout_error_str: str = ""
    legend_x_val: Optional[float] = None
    legend_y_val: Optional[float] = None
    legend_width_val: Optional[float] = None
    legend_height_val: Optional[float] = None
    legend_text_size_val: Optional[float] = None
    legend_title_size_val: Optional[float] = None
    legend_label_count_val: Optional[int] = None
    legend_reserve_right_requested_float: Optional[float] = legend_opts_eff.get("reserve_right")
    legend_reserve_right_applied_bool: bool = False
    legend_reserve_right_method_str: str = ""
    legend_reserve_right_error_str: str = ""
    legend_hide_attempted_bool: bool = False
    legend_hide_status_str: str = ""
    legend_hide_method_str: str = ""
    legend_hide_error_str: str = ""
    legend_visible_after_hide_val: Optional[bool] = None
    colorbar_range_min_val: Optional[float] = None
    colorbar_range_max_val: Optional[float] = None
    colorbar_range_source_str: str = ""
    colorbar_units_str: str = ""
    colorbar_title_str: str = ""
    colorbar_palette_name_str: str = ""

    def _record(status: str, surface_desc: str, message: str) -> None:
        records.append(ExportRecord(
            geo_name=geo_name,
            case_name=case_name,
            field_key=field_key,
            field_name=field_name,
            target_surface_or_plane=surface_desc,
            output_file=str(output_file),
            status=status,
            message=message,
            selected_variable=selected_var_str,
            selected_surfaces=selected_surfaces_str,
            color_range_mode=applied_mode,
            color_range_min=applied_min,
            color_range_max=applied_max,
            clean_scene_status="; ".join(scene_setup_warnings) if scene_setup_warnings else "ok",
            palette_diagnostics=palette_diag_str,
            contour_target_type="wall",
            derived_variable_mode=derived_variable_mode,
            primitive_variables_used=primitive_variables_used_str,
            bulk_reference_mode=bulk_reference_mode_str,
            bulk_reference_value=bulk_reference_value_float,
            bulk_reference_units_or_type=bulk_reference_units_str,
            center_plane_name=center_plane_name_str,
            formula_summary=formula_summary_str,
            center_plane_diagnostics=center_plane_diagnostics_dict,
            view_margin_requested=view_margin,
            zoom_out_requested=zoom_out,
            zoom_out_applied=zoom_out_applied_bool,
            zoom_out_method=zoom_out_method_str,
            zoom_out_status=zoom_out_status_str,
            zoom_out_error=zoom_out_error_str,
            view_orientation_preserved=True,
            membrane_bounds_raw=membrane_bounds_raw_list,
            membrane_bounds_padded=membrane_bounds_padded_list,
            membrane_bounds_source=membrane_bounds_source_str,
            fit_target_used=fit_target_used_str,
            bounds_fit_attempted=bounds_fit_attempted_bool,
            bounds_fit_status=bounds_fit_status_str,
            bounds_fit_method=bounds_fit_method_str,
            bounds_fit_error=bounds_fit_error_str,
            bounds_candidate_attempts=list(bounds_candidate_attempts_list),
            bounds_candidate_successes=list(bounds_candidate_successes_list),
            chosen_bounds_candidate=chosen_bounds_candidate_str,
            chosen_bounds_format=chosen_bounds_format_str,
            manual_view_bounds_requested=manual_view_bounds_requested_list,
            manual_view_plane_requested=manual_view_plane_requested_str,
            export_crop_disabled=export_crop_disabled_bool,
            export_viewport_method=export_viewport_method_str,
            export_region_used=export_region_used_str,
            bounds_debug_sweep_attempted=bounds_debug_sweep_attempted_bool,
            bounds_debug_sweep_outputs=list(bounds_debug_sweep_outputs_list),
            legend_preset_requested=legend_preset_requested_str,
            legend_preset_applied=legend_preset_applied_str,
            legend_layout_status=legend_layout_status_str,
            legend_layout_method=legend_layout_method_str,
            legend_layout_error=legend_layout_error_str,
            legend_x=legend_x_val,
            legend_y=legend_y_val,
            legend_width=legend_width_val,
            legend_height=legend_height_val,
            legend_text_size=legend_text_size_val,
            legend_title_size=legend_title_size_val,
            legend_label_count=legend_label_count_val,
            legend_reserve_right_requested=legend_reserve_right_requested_float,
            legend_reserve_right_applied=legend_reserve_right_applied_bool,
            legend_reserve_right_method=legend_reserve_right_method_str,
            legend_reserve_right_error=legend_reserve_right_error_str,
            legend_mode=legend_mode_str,
            legend_hide_attempted=legend_hide_attempted_bool,
            legend_hide_status=legend_hide_status_str,
            legend_hide_method=legend_hide_method_str,
            legend_hide_error=legend_hide_error_str,
            legend_visible_after_hide=legend_visible_after_hide_val,
            colorbar_range_min=colorbar_range_min_val,
            colorbar_range_max=colorbar_range_max_val,
            colorbar_range_source=colorbar_range_source_str,
            colorbar_units=colorbar_units_str,
            colorbar_title=colorbar_title_str,
            colorbar_palette_name=colorbar_palette_name_str,
        ))

    # 1. Locate surfaces
    surface_names, surface_warn = find_surfaces(session, surface_type, plan_item)
    if surface_warn:
        warnings.append(surface_warn)
    surface_desc = ", ".join(surface_names) if surface_names else "none"

    if not surface_names:
        _record(STATUS_WARN, surface_desc, f"No surfaces found: {surface_warn}")
        return

    # 2. Apply membrane-side filter for membrane fields
    if surface_type == "membrane":
        surface_names, side_warn = filter_membrane_surface(surface_names, membrane_side)
        if side_warn:
            warnings.append(side_warn)
        surface_desc = ", ".join(surface_names) if surface_names else "none"
        if not surface_names:
            _record(STATUS_WARN, surface_desc, f"Membrane-side filter removed all surfaces: {side_warn}")
            return

    selected_surfaces_str = surface_desc

    # 3. Locate UDM variable (used as fallback for cp_inlet / lmh)
    var_obj, matched_var_desc = find_ensight_variable(session, var_candidates)
    if var_obj is None:
        if field_key == "shear_rate":
            derived_variable_mode = "wall_shear_unavailable"
            _inv_path = output_file.parent / "ensight_variable_inventory.json"
            _inv_written = _dump_variable_inventory(session, _inv_path)
            _record(
                STATUS_WARN, surface_desc,
                f"Wall shear stress not found in EnSight inventory (candidates: {var_candidates}). "
                f"EnSight variable inventory written to: {_inv_written}. "
                "Use 03b_pyfluent_shear_contour_export.py to export shear_rate via PyFluent (wall-shear / mu)."
            )
        else:
            _record(
                STATUS_WARN, surface_desc,
                f"Variable not found among candidates {var_candidates}. "
                "Ensure the .dat.h5 contains UDM/field data and loaded correctly."
            )
        return

    # 3b. Attempt to build a direct derived variable from primitive EnSight fields.
    #     Falls back to UDM candidate (matched_var_desc from step 3) when any step fails.
    if field_key in ("cp_inlet", "lmh", "water_flux", "salt_flux"):
        salt_obj, salt_desc, _ = find_primitive_variable(
            session, SALT_MASS_FRAC_CANDIDATES, "salt mass fraction"
        )

        # ---- CP wall direct ----
        if field_key == "cp_inlet":
            if salt_obj is not None:
                primitive_variables_used_str = salt_desc

                # Strategy 0: pre-computed bulk avg from PyFluent CSV (most reliable).
                pf_val, pf_units, pf_diag = _read_pyfluent_bulk_center_avg(plan_item)
                bulk_avg: Optional[float] = None
                cp_full_diag: dict = {}
                cp_plane: Optional[str] = None
                plane_diag: str = ""

                if pf_val is not None:
                    is_conc = any(
                        kw in _normalize(salt_desc)
                        for kw in ["mol", "conc", "concentration"]
                    )
                    pf_ok = (50 <= pf_val <= 3000) if is_conc else (1e-4 <= pf_val <= 0.20)
                    if pf_ok:
                        bulk_avg = pf_val
                        bulk_reference_mode_str = (
                            "center_plane_area_weighted_average_from_pyfluent_report"
                        )
                        bulk_reference_value_float = pf_val
                        bulk_reference_units_str = pf_units
                        center_plane_name_str = "pyfluent_report_csv"
                        cp_full_diag = {"pyfluent_source": pf_diag, "units": pf_units}
                        info_msgs.append(
                            f"CP bulk avg from PyFluent CSV: {pf_val:.6g} "
                            f"({pf_units}); {pf_diag}"
                        )
                    else:
                        info_msgs.append(
                            f"PyFluent CSV val={pf_val:.4g} ({pf_units}) not plausible "
                            f"for salt_desc={salt_desc} (is_conc={is_conc}); "
                            "trying EnSight AMEAN"
                        )
                else:
                    info_msgs.append(
                        f"PyFluent CSV unavailable ({pf_diag}); trying EnSight AMEAN"
                    )

                # Strategy 1: center-plane area-weighted average from EnSight.
                if bulk_avg is None:
                    fluid_vol_parts = find_fluid_volume_parts(session)
                    bulk_avg, cp_plane, plane_diag, cp_full_diag = (
                        compute_bulk_center_average(session, salt_desc, fluid_vol_parts)
                    )
                center_plane_diagnostics_dict = cp_full_diag

                if bulk_avg is not None and bulk_avg > 0:
                    if not center_plane_name_str:
                        center_plane_name_str = cp_plane or ""
                    cp_var, cp_desc, cp_diag = create_cp_wall_direct(
                        session, salt_desc, bulk_avg
                    )
                    if cp_var is not None:
                        var_obj = cp_var
                        matched_var_desc = cp_desc
                        derived_variable_mode = "direct_cp_center_avg"
                        if not bulk_reference_mode_str:
                            bulk_reference_mode_str = "center_plane_area_weighted_average"
                        if bulk_reference_value_float is None:
                            bulk_reference_value_float = bulk_avg
                        if not bulk_reference_units_str:
                            bulk_reference_units_str = "same_as_salt_variable"
                        formula_summary_str = (
                            f"CP_WALL_DIRECT={salt_desc}/{bulk_avg:.6g} "
                            f"(center-plane area-wtd avg)"
                        )
                        info_msgs.append(
                            f"CP_WALL_DIRECT created: {cp_diag}; "
                            f"bulk_avg={bulk_avg:.6g}"
                        )
                    else:
                        warnings.append(f"WARN: CP_WALL_DIRECT calculator failed ({cp_diag})")
                        derived_variable_mode = "calculator_failed"
                        bulk_avg = None  # trigger inlet-ref fallback below
                else:
                    fr = cp_full_diag.get("final_result", "")
                    derived_variable_mode = (
                        "center_plane_failed"
                        if "all_candidates_failed" in fr or "clip_failed" in fr
                        else "area_average_failed"
                    )
                    warnings.append(f"WARN: bulk center avg failed ({plane_diag})")

                # Fallback 1: inlet reference
                if bulk_avg is None and salt_obj is not None:
                    c_inlet_ref_mol = float(plan_item.get("c_inlet_ref_mol", UDF_C_INLET_REF))
                    is_conc = any(
                        kw in _normalize(salt_desc)
                        for kw in ["mol", "conc", "concentration"]
                    )
                    if is_conc:
                        inlet_ref = c_inlet_ref_mol
                        ref_type = f"inlet_molar_conc_{c_inlet_ref_mol:.4g}_mol_m3"
                    else:
                        inlet_ref = INLET_SALT_MASS_FRAC_REF
                        ref_type = f"inlet_mass_frac_{INLET_SALT_MASS_FRAC_REF:.5g}"

                    cp_var2, cp_desc2, cp_diag2 = create_cp_wall_direct(
                        session, salt_desc, inlet_ref
                    )
                    if cp_var2 is not None:
                        var_obj = cp_var2
                        matched_var_desc = cp_desc2
                        derived_variable_mode = "bulk_reference_fallback_to_inlet"
                        bulk_reference_mode_str = "inlet_reference_fallback"
                        bulk_reference_value_float = inlet_ref
                        bulk_reference_units_str = ref_type
                        formula_summary_str = (
                            f"CP_WALL_DIRECT={salt_desc}/{inlet_ref:.5g} "
                            f"(inlet reference fallback)"
                        )
                        warnings.append(
                            f"WARN: CP using inlet reference ({ref_type}), "
                            f"center-plane avg unavailable"
                        )
                    else:
                        warnings.append(
                            f"WARN: inlet-ref CP also failed ({cp_diag2}); "
                            f"falling back to UDM_9"
                        )
                        derived_variable_mode = "udm_fallback"
                        warnings.append(
                            "WARN: fallback to UDM_9; wall mapping may be unreliable"
                        )
            else:
                derived_variable_mode = "primitive_unavailable"
                warnings.append(
                    "WARN: no primitive salt variable found; "
                    "fallback to UDM_9; wall mapping may be unreliable"
                )

        # ---- LMH wall direct ----
        elif field_key == "lmh":
            if salt_obj is not None:
                primitive_variables_used_str = salt_desc
                p_op = float(plan_item.get("operating_pressure", 101325.0))
                pabs_desc, _is_abs, pres_diag = find_pressure_variable(session, p_op)

                if pabs_desc is not None:
                    lmh_var, lmh_desc, lmh_diag, formula_summary_str = create_lmh_wall_direct(
                        session, salt_desc, pabs_desc, plan_item
                    )
                    if lmh_var is not None:
                        var_obj = lmh_var
                        matched_var_desc = lmh_desc
                        derived_variable_mode = "direct_lmh"
                        info_msgs.append(
                            f"LMH_WALL_DIRECT created: {lmh_diag}; "
                            f"pressure={pres_diag}"
                        )
                    else:
                        derived_variable_mode = "calculator_failed"
                        warnings.append(
                            f"WARN: LMH_WALL_DIRECT calculator failed ({lmh_diag}); "
                            f"fallback to UDM_8; wall mapping may be unreliable"
                        )
                else:
                    derived_variable_mode = "primitive_unavailable"
                    warnings.append(
                        f"WARN: no pressure variable found ({pres_diag}); "
                        f"fallback to UDM_8; wall mapping may be unreliable"
                    )
            else:
                derived_variable_mode = "primitive_unavailable"
                warnings.append(
                    "WARN: no primitive salt variable found; "
                    "fallback to UDM_8; wall mapping may be unreliable"
                )

        # ---- water_flux wall direct ----
        elif field_key == "water_flux":
            if salt_obj is not None:
                primitive_variables_used_str = salt_desc
                p_op = float(plan_item.get("operating_pressure", 101325.0))
                pabs_desc, _is_abs, pres_diag = find_pressure_variable(session, p_op)
                if pabs_desc is not None:
                    jw_var, jw_desc, jw_diag, formula_summary_str = \
                        create_water_flux_wall_direct(
                            session, salt_desc, pabs_desc, plan_item
                        )
                    if jw_var is not None:
                        var_obj = jw_var
                        matched_var_desc = jw_desc
                        derived_variable_mode = "direct_water_flux"
                        info_msgs.append(
                            f"WATER_FLUX_WALL_DIRECT created: {jw_diag}; "
                            f"pressure={pres_diag}"
                        )
                    else:
                        derived_variable_mode = "calculator_failed"
                        warnings.append(
                            f"WARN: WATER_FLUX_WALL_DIRECT calculator failed ({jw_diag}); "
                            f"fallback to UDM_6; wall mapping may be unreliable"
                        )
                else:
                    derived_variable_mode = "primitive_unavailable"
                    warnings.append(
                        f"WARN: no pressure variable found ({pres_diag}); "
                        f"fallback to UDM_6; wall mapping may be unreliable"
                    )
            else:
                derived_variable_mode = "primitive_unavailable"
                warnings.append(
                    "WARN: no primitive salt variable found; "
                    "fallback to UDM_6; wall mapping may be unreliable"
                )

        # ---- salt_flux wall direct ----
        elif field_key == "salt_flux":
            if salt_obj is not None:
                primitive_variables_used_str = salt_desc
                p_op = float(plan_item.get("operating_pressure", 101325.0))
                pabs_desc, _is_abs, pres_diag = find_pressure_variable(session, p_op)
                if pabs_desc is not None:
                    jw_var, _, jw_diag, _ = create_water_flux_wall_direct(
                        session, salt_desc, pabs_desc, plan_item
                    )
                    if jw_var is not None:
                        sf_var, sf_desc, sf_diag, formula_summary_str = \
                            create_salt_flux_wall_direct(session, plan_item)
                        if sf_var is not None:
                            var_obj = sf_var
                            matched_var_desc = sf_desc
                            derived_variable_mode = "direct_salt_flux"
                            info_msgs.append(
                                f"SALT_FLUX_WALL_DIRECT created: {sf_diag}"
                            )
                        else:
                            derived_variable_mode = "calculator_failed"
                            warnings.append(
                                f"WARN: SALT_FLUX_WALL_DIRECT calculator failed ({sf_diag}); "
                                f"fallback to UDM_10; wall mapping may be unreliable"
                            )
                    else:
                        derived_variable_mode = "calculator_failed"
                        warnings.append(
                            f"WARN: WATER_FLUX_WALL_DIRECT failed ({jw_diag}); "
                            f"fallback to UDM_10; wall mapping may be unreliable"
                        )
                else:
                    derived_variable_mode = "primitive_unavailable"
                    warnings.append(
                        f"WARN: no pressure variable found ({pres_diag}); "
                        f"fallback to UDM_10; wall mapping may be unreliable"
                    )
            else:
                derived_variable_mode = "primitive_unavailable"
                warnings.append(
                    "WARN: no primitive salt variable found; "
                    "fallback to UDM_10; wall mapping may be unreliable"
                )

    # ---- shear_rate wall direct (membrane only, no salt needed) ----
    elif field_key == "shear_rate":
        primitive_variables_used_str = matched_var_desc
        sr_var, sr_desc, sr_diag, formula_summary_str = create_shear_rate_wall_direct(
            session, matched_var_desc, mu
        )
        if sr_var is not None:
            var_obj = sr_var
            matched_var_desc = sr_desc
            derived_variable_mode = "direct_shear_rate"
            info_msgs.append(f"SHEAR_RATE_WALL_DIRECT created: {sr_diag}")
        else:
            derived_variable_mode = "wall_shear_unavailable"
            warnings.append(
                f"WARN: SHEAR_RATE_WALL_DIRECT calculator failed ({sr_diag}); "
                f"wall shear unavailable; result will use raw wall-shear [Pa]"
            )

    # 4. Optionally derive wall shear rate (wall-shear / mu)
    display_var_desc = matched_var_desc
    if derive_shear_rate:
        try:
            derived_name = "pp_wall_shear_rate"
            safe_src = (
                f"'{matched_var_desc}'"
                if ("-" in matched_var_desc or " " in matched_var_desc)
                else matched_var_desc
            )
            session.ensight.part.select_all()
            session.ensight.variables.evaluate(f"{derived_name} = {safe_src} / {mu}")
            derived_var, derived_desc = find_ensight_variable(session, [derived_name])
            if derived_var is not None:
                var_obj = derived_var
                display_var_desc = derived_desc
                warnings.append(f"Derived {derived_name} = '{matched_var_desc}' / mu={mu} Pa*s.")
            else:
                warnings.append(
                    f"Derived variable not found after evaluate(); "
                    f"using '{matched_var_desc}' (Pa, not 1/s)."
                )
        except Exception as exc_derive:
            warnings.append(
                f"Derivation failed ({exc_derive}); using '{matched_var_desc}' (Pa, not 1/s)."
            )

    selected_var_str = display_var_desc

    # 5. Hide all parts, show only target parts
    all_parts = session.ensight.objs.core.PARTS
    surface_name_set = set(surface_names)
    target_parts = [p for p in all_parts if p.DESCRIPTION in surface_name_set]

    try:
        for p in all_parts:
            try:
                p.VISIBLE = 0
            except Exception:
                pass
        for p in target_parts:
            try:
                p.VISIBLE = 1
            except Exception:
                pass
    except Exception as exc_vis:
        warnings.append(f"Part visibility control failed: {exc_vis}")

    if not target_parts:
        for p in all_parts:
            try:
                p.VISIBLE = 1
            except Exception:
                pass
        _record(
            STATUS_WARN, surface_desc,
            f"Found {len(surface_names)} surface name(s) but none matched a loaded part."
        )
        return

    # 6. Select target parts and apply COLORBYPALETTE
    # Take a pre-snapshot of palette DESCRIPTIONs for snapshot-diff (Strategy 4).
    # DESCRIPTIONs are used as keys rather than Python id() because PyEnSight may
    # return fresh wrapper objects on each PALETTES access.
    pre_palette_descs: FrozenSet[str] = frozenset()
    try:
        pre_palette_descs = frozenset(
            getattr(p, "DESCRIPTION", "") for p in session.ensight.objs.core.PALETTES
        )
    except Exception:
        pass

    try:
        all_parts.set_attr("SELECTED", False)
        session.ensight.utils.parts.select_parts(target_parts)
        for p in target_parts:
            p.COLORBYPALETTE = display_var_desc
    except Exception as exc_color:
        for p in all_parts:
            try:
                p.VISIBLE = 1
            except Exception:
                pass
        _record(STATUS_FAILED, surface_desc, f"Part selection/coloring failed: {exc_color}")
        return

    # 7. Apply color range and colorbar label
    mode_str, rmin, rmax, range_warn, found_palette = apply_palette_range(
        session, var_obj, display_var_desc, target_parts,
        color_range_min, color_range_max,
        auto_range=(color_range_mode == RANGE_MODE_AUTO),
        pre_palette_descs=pre_palette_descs,
    )
    applied_mode = mode_str
    applied_min  = rmin
    applied_max  = rmax
    if range_warn:
        warnings.append(range_warn)
        if applied_mode == RANGE_MODE_NONE:
            palette_diag_str = range_warn

    label_warn = apply_palette_label(session, display_var_desc, field_name, found_palette)
    if label_warn:
        info_msgs.append(label_warn)

    # 7a. Capture colorbar range/unit metadata for the separate metadata
    #     export (contour_colorbar_ranges.*), independent of whether the
    #     legend itself ends up shown or hidden below. For auto-range fields,
    #     read back the palette's actual MINMAX after set_range_to_part_minmax()
    #     so the exported metadata reflects the real applied range rather than
    #     just "auto".
    colorbar_units_str = _field_units_from_label(field_name)
    colorbar_title_str = field_name
    colorbar_palette_name_str = str(getattr(found_palette, "DESCRIPTION", "") or "") if found_palette is not None else ""
    if applied_mode in (RANGE_MODE_FIXED, RANGE_MODE_CLI):
        colorbar_range_source_str = "fixed_user_or_config"
        colorbar_range_min_val = applied_min
        colorbar_range_max_val = applied_max
    elif applied_mode == RANGE_MODE_AUTO:
        colorbar_range_source_str = "auto"
        if found_palette is not None:
            try:
                actual_minmax = list(found_palette.MINMAX)
                colorbar_range_min_val = float(actual_minmax[0])
                colorbar_range_max_val = float(actual_minmax[1])
                colorbar_range_source_str = "palette_minmax"
            except Exception as exc_mm:
                info_msgs.append(
                    f"colorbar_metadata: could not read back auto-range MINMAX: {exc_mm}"
                )
    else:
        colorbar_range_source_str = "unavailable"

    # 7b. Legend/colorbar layout OR hide, depending on --legend-mode.
    #     Best-effort only: never allowed to fail the contour export itself.
    print(f"Legend mode: {legend_mode_str}")
    if legend_mode_str == "show":
        print(f"Configuring legend layout: preset={legend_opts_eff.get('preset')}")
        legend_layout_result = configure_legend_layout(
            session, field_key, var_obj, display_var_desc, legend_opts_eff
        )
        legend_preset_requested_str = str(legend_layout_result.get("preset_requested") or "")
        legend_preset_applied_str = str(legend_layout_result.get("preset_applied") or "")
        legend_layout_status_str = str(legend_layout_result.get("status") or "")
        legend_layout_method_str = str(legend_layout_result.get("method") or "")
        legend_layout_error_str = str(legend_layout_result.get("error") or "")
        legend_x_val = legend_layout_result.get("x")
        legend_y_val = legend_layout_result.get("y")
        legend_width_val = legend_layout_result.get("width")
        legend_height_val = legend_layout_result.get("height")
        legend_text_size_val = legend_layout_result.get("text_size")
        legend_title_size_val = legend_layout_result.get("title_size")
        legend_label_count_val = legend_layout_result.get("label_count")
        print(f"Legend layout status: {legend_layout_status_str.lower() or 'unknown'}")
        if legend_x_val is not None or legend_width_val is not None:
            print(
                f"  Legend location=({legend_x_val},{legend_y_val}) "
                f"size=({legend_width_val},{legend_height_val}) "
                f"text_size={legend_text_size_val} title_size={legend_title_size_val} "
                f"label_count={legend_label_count_val}"
            )
        if legend_layout_error_str:
            info_msgs.append(f"legend_layout: {legend_layout_error_str}")
    else:
        legend_preset_requested_str = str(legend_opts_eff.get("preset") or "")
        print("Legend positioning skipped (--legend-mode hide).")
        legend_hide_attempted_bool = True
        print(f"Hiding PyEnSight legend for field: {field_key}")
        legend_hide_result = hide_legend_annotation(session, var_obj, display_var_desc)
        legend_hide_status_str = str(legend_hide_result.get("status") or "")
        legend_hide_method_str = str(legend_hide_result.get("method") or "")
        legend_hide_error_str = str(legend_hide_result.get("error") or "")
        legend_visible_after_hide_val = legend_hide_result.get("visible_after")
        print(f"Legend hide status: {legend_hide_status_str.lower() or 'unknown'}")
        if legend_hide_error_str and legend_hide_status_str != STATUS_SUCCESS:
            warnings.append(f"legend_hide: {legend_hide_error_str}")
        elif legend_hide_error_str:
            info_msgs.append(f"legend_hide: {legend_hide_error_str}")

    # 8. Fit view to visible geometry, then apply view margin and zoom-out as
    #    ONE combined post-fit zoom call (see apply_zoom_out docstring for why
    #    two sequential zoom() calls would be ambiguous).
    try:
        session.ensight.view_transf.fit(0)
    except Exception:
        pass
    combined_zoom_out_factor = max(view_margin, 1.0) * max(zoom_out, 1.0)
    print(
        f"Applying PyEnSight zoom-out factor: {zoom_out} "
        f"(combined with view-margin {view_margin} -> {combined_zoom_out_factor:.4g})"
    )
    zoom_out_method_str, zoom_out_status_str, zoom_out_error_str = apply_zoom_out(
        session, combined_zoom_out_factor
    )
    zoom_out_applied_bool = zoom_out_status_str == "applied"
    print(f"Zoom-out method: {zoom_out_method_str} ({zoom_out_status_str})")
    if zoom_out_error_str:
        info_msgs.append(f"zoom_out attempts: {zoom_out_error_str}")

    # 8c. Compute the actual bounds of the SAME parts displayed above, pad
    #     them by --view-margin, and fit the camera to that padded region as
    #     the FINAL camera operation before export — i.e. it runs AFTER (and
    #     overrides) the view-margin/zoom-out zoom() call in step 8. This is
    #     the primary anti-clipping mechanism; see fit_view_to_padded_bounds
    #     docstring for why it does not rely on zoom() at all.
    bounds_selection = select_membrane_view_bounds(session, target_parts)
    bounds_candidate_attempts_list = list(bounds_selection["bounds_candidate_attempts"])
    bounds_candidate_successes_list = list(bounds_selection["bounds_candidate_successes"])
    chosen_bounds_candidate_str = str(bounds_selection["chosen_bounds_candidate"] or "")
    chosen_bounds_format_str = str(bounds_selection["chosen_bounds_format"] or "")

    raw_bounds = bounds_selection["bounds"]
    membrane_bounds_raw_list = list(raw_bounds) if raw_bounds else None
    membrane_bounds_source_str = chosen_bounds_candidate_str if raw_bounds else "unavailable"
    padded_bounds = compute_padded_bounds(raw_bounds, view_margin) if raw_bounds else None
    membrane_bounds_padded_list = list(padded_bounds) if padded_bounds else None
    fit_target_used_str = (
        f"automatic:{chosen_bounds_candidate_str}" if raw_bounds else "unavailable"
    )

    prefit_errors: List[str] = []
    manual_bounds_used = False
    if manual_view_bounds is not None:
        plane = (manual_view_plane or "xy").strip().lower()
        if plane != "xy":
            prefit_errors.append(
                f"unsupported --manual-view-plane '{manual_view_plane}'; only 'xy' is implemented"
            )
            fit_target_used_str = (
                f"automatic:{chosen_bounds_candidate_str}"
                if raw_bounds else "manual_view_bounds_unsupported_plane"
            )
        else:
            zmin, zmax = (raw_bounds[4], raw_bounds[5]) if raw_bounds else (0.0, 0.0)
            raw_bounds = (
                manual_view_bounds[0], manual_view_bounds[1],
                manual_view_bounds[2], manual_view_bounds[3],
                zmin, zmax,
            )
            padded_bounds = compute_padded_bounds(raw_bounds, view_margin)
            membrane_bounds_source_str = "manual_view_bounds"
            membrane_bounds_raw_list = list(manual_view_bounds)
            membrane_bounds_padded_list = list(padded_bounds[:4])
            fit_target_used_str = "manual_view_bounds"
            chosen_bounds_candidate_str = "manual_view_bounds"
            chosen_bounds_format_str = "manual_xy_2d"
            manual_bounds_used = True

    # 8c-i. Optional extra right-side reserve, so the presentation legend
    #       preset has whitespace clear of the membrane. Only meaningful when
    #       a legend is actually shown (--legend-mode show) and with manual
    #       view bounds (see apply_legend_reserve_right docstring for why it
    #       cannot be made strictly one-sided). Whether it actually affected
    #       the final view depends on which camera-fit method fires below
    #       (reselect_fit_only ignores extent, camera.* uses it) — the
    #       "applied" flag is finalized after that call, not here.
    bounds_extended = False
    reserve_extend_method = "not_applicable"
    reserve_extend_error = ""
    if legend_mode_str != "show":
        reserve_extend_method = "skipped_legend_mode_hide"
        reserve_extend_error = "legend_reserve_right skipped because --legend-mode hide"
    elif manual_bounds_used:
        padded_bounds, bounds_extended, reserve_extend_method, reserve_extend_error = (
            apply_legend_reserve_right(
                raw_bounds, padded_bounds, legend_reserve_right_requested_float
            )
        )
        if bounds_extended:
            membrane_bounds_padded_list = list(padded_bounds[:4])
    elif legend_reserve_right_requested_float:
        reserve_extend_error = "legend_reserve_right only applies when --manual-view-bounds is set"

    bounds_fit_attempted_bool = True
    bounds_fit_method_str, bounds_fit_status_str, fit_error = fit_view_to_padded_bounds(
        session, target_parts, raw_bounds, padded_bounds, image_width, image_height,
    )
    if manual_bounds_used:
        bounds_fit_status_str = "success" if bounds_fit_status_str == "applied" else "warn"

    # Finalize legend_reserve_right_applied now that the fit method is known:
    # a "camera.*" method means required_half_height was actually derived from
    # the extended padded_bounds; "reselect_fit_only" means the extension had
    # no visual effect even though the bounds tuple itself was widened.
    legend_reserve_right_method_str = reserve_extend_method
    if bounds_extended:
        legend_reserve_right_applied_bool = bounds_fit_method_str.startswith("camera.")
        if legend_reserve_right_applied_bool:
            legend_reserve_right_error_str = reserve_extend_error
        else:
            legend_reserve_right_error_str = (
                f"bounds extended by reserve fraction, but camera fit used "
                f"method='{bounds_fit_method_str}' (status={bounds_fit_status_str}); "
                "extension had no confirmed visual effect."
            )
    else:
        legend_reserve_right_applied_bool = False
        legend_reserve_right_error_str = reserve_extend_error
    print(
        f"Legend reserve-right: requested={legend_reserve_right_requested_float} "
        f"applied={legend_reserve_right_applied_bool} method={legend_reserve_right_method_str}"
    )
    if legend_reserve_right_error_str:
        info_msgs.append(f"legend_reserve_right: {legend_reserve_right_error_str}")
    bounds_fit_error_str = "; ".join([m for m in prefit_errors + ([fit_error] if fit_error else []) if m])
    print(
        f"Bounds fit: raw={membrane_bounds_raw_list} padded={membrane_bounds_padded_list} "
        f"method={bounds_fit_method_str} status={bounds_fit_status_str}"
    )
    if bounds_fit_error_str:
        info_msgs.append(f"bounds_fit attempts: {bounds_fit_error_str}")

    if bounds_diagnostics and bounds_diagnostics_path is not None:
        try:
            diagnostic_selection = dict(bounds_selection)
            diagnostic_selection.update({
                "final_fit_target_used": fit_target_used_str,
                "final_membrane_bounds_source": membrane_bounds_source_str,
                "final_membrane_bounds_raw": membrane_bounds_raw_list,
                "final_membrane_bounds_padded": membrane_bounds_padded_list,
                "final_bounds_fit_method": bounds_fit_method_str,
                "final_bounds_fit_status": bounds_fit_status_str,
                "final_bounds_fit_error": bounds_fit_error_str,
            })
            diag_entry = collect_pyensight_bounds_diagnostics(
                session=session,
                field_key=field_key,
                display_var_desc=display_var_desc,
                surface_desc=surface_desc,
                target_parts=target_parts,
                bounds_selection=diagnostic_selection,
                manual_view_bounds=manual_view_bounds,
                manual_view_plane=manual_view_plane,
            )
            print_pyensight_bounds_diagnostics(diag_entry)
            if bounds_diagnostics_records is not None:
                bounds_diagnostics_records.append(diag_entry)
                write_pyensight_bounds_diagnostics(
                    bounds_diagnostics_path,
                    bounds_diagnostics_records,
                    geo_name,
                    case_name,
                )
        except Exception as exc_diag:
            info_msgs.append(f"bounds_diagnostics failed: {exc_diag}")

    export_crop_disabled_bool, export_viewport_method_str, export_region_used_str = (
        _inspect_export_image_options(session)
    )

    # 9. Render and save image
    try:
        png_path = str(output_file).replace("\\", "/")
        session.ensight.utils.export.image(png_path, width=image_width, height=image_height, passes=4)
    except Exception as exc_export:
        for p in all_parts:
            try:
                p.VISIBLE = 1
            except Exception:
                pass
        _record(STATUS_FAILED, surface_desc, f"Image export failed: {exc_export}")
        return

    # 9b. Optional debug bounds sweep — extra diagnostic images only; runs
    #     after the main output above and never replaces or blocks it.
    if bounds_debug_sweep:
        bounds_debug_sweep_attempted_bool = True
        try:
            bounds_debug_sweep_outputs_list = run_bounds_debug_sweep(
                session, target_parts, raw_bounds, output_file, membrane_side,
                image_width, image_height, view_margin, zoom_out,
            )
        except Exception as exc_sweep:
            info_msgs.append(f"bounds_debug_sweep failed: {exc_sweep}")
        print(f"Bounds debug sweep wrote {len(bounds_debug_sweep_outputs_list)} image(s).")

    # 10. Restore full part visibility for next contour
    for p in all_parts:
        try:
            p.VISIBLE = 1
        except Exception:
            pass

    if output_file.is_file():
        final_status = STATUS_WARN if warnings else STATUS_SUCCESS
        if warnings:
            msg_parts = list(warnings)
        else:
            msg_parts = [f"Exported {output_file.name}"]
        if info_msgs:
            msg_parts += [f"[INFO] {m}" for m in info_msgs]
        _record(final_status, surface_desc, "; ".join(msg_parts))
    else:
        _record(
            STATUS_FAILED, surface_desc,
            "export.image() completed without error but output file was not created."
        )


# ---------------------------------------------------------------------------
# Colorbar / range metadata export (JSON + txt + CSV) — written separately
# from the PNGs so the colorbar can be recreated outside PyEnSight when
# --legend-mode hide removes the on-image legend/colorbar annotation.
# ---------------------------------------------------------------------------

def _field_units_from_label(field_name: str) -> str:
    """Extract the trailing '[units]' from a display label, e.g. 'LMH [LMH]' -> 'LMH'."""
    m = re.search(r"\[([^\]]*)\]\s*$", field_name or "")
    return m.group(1) if m else ""


def _colorbar_entry_from_record(r: ExportRecord, timestamp: str) -> dict:
    notes_parts: List[str] = []
    if r.field_key == "cp_inlet" and r.bulk_reference_mode:
        bulk_val = f"={r.bulk_reference_value:.6g}" if r.bulk_reference_value is not None else ""
        notes_parts.append(
            f"CP bulk reference: mode={r.bulk_reference_mode}{bulk_val} "
            f"({r.bulk_reference_units_or_type or 'n/a'})"
        )
    if r.status not in (STATUS_SUCCESS, STATUS_WARN):
        notes_parts.append(f"Field not exported (status={r.status}); range metadata may be unavailable.")

    range_min = r.colorbar_range_min
    range_max = r.colorbar_range_max
    range_source = r.colorbar_range_source or "unavailable"
    if (range_min is None or range_max is None) and range_source != "unavailable":
        notes_parts.append(f"range not available (source={range_source})")

    units = r.colorbar_units or _field_units_from_label(r.field_name)

    return {
        "geo_name": r.geo_name,
        "case_name": r.case_name,
        "field_key": r.field_key,
        "selected_variable": r.selected_variable,
        "derived_variable_mode": r.derived_variable_mode,
        "output_file": r.output_file,
        "units": units,
        "range_min": range_min,
        "range_max": range_max,
        "range_source": range_source,
        "range_is_fixed": range_source == "fixed_user_or_config",
        "palette_name": r.colorbar_palette_name,
        "legend_title": r.colorbar_title or r.field_name,
        "timestamp": timestamp,
        "notes": "; ".join(notes_parts),
    }


def save_colorbar_metadata(
    records: List[ExportRecord],
    figures_dir: Path,
    geo_name: str,
    case_name: str,
) -> List[Path]:
    """
    Write colorbar/range metadata (JSON + human-readable txt + CSV) for every
    planned field, independent of --legend-mode. Best-effort: never raises.
    Returns the list of file paths actually written.
    """
    if not _safe_mkdir(figures_dir):
        return []

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    entries = [_colorbar_entry_from_record(r, timestamp) for r in records]

    written: List[Path] = []

    json_path = figures_dir / "contour_colorbar_ranges.json"
    try:
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "geo_name": geo_name,
                    "case_name": case_name,
                    "generated_at": timestamp,
                    "fields": entries,
                },
                fh, indent=2, default=str,
            )
        written.append(json_path)
    except Exception as exc:
        print(f"WARNING: could not write {json_path}: {exc}")

    txt_path = figures_dir / "contour_colorbar_ranges.txt"
    try:
        lines = [
            f"Colorbar / range metadata - {geo_name} / {case_name}  (generated {timestamp})",
            "=" * 72,
        ]
        for e in entries:
            lines.append(f"\n{e['field_key']}:")
            lines.append(f"  selected_variable    : {e['selected_variable']}")
            lines.append(f"  derived_variable_mode: {e['derived_variable_mode']}")
            lines.append(f"  units                : {e['units']}")
            lines.append(f"  range                : {e['range_min']} - {e['range_max']}")
            lines.append(f"  range_source         : {e['range_source']}")
            lines.append(f"  range_is_fixed       : {e['range_is_fixed']}")
            lines.append(f"  palette_name         : {e['palette_name']}")
            lines.append(f"  legend_title         : {e['legend_title']}")
            lines.append(f"  output_file          : {e['output_file']}")
            if e["notes"]:
                lines.append(f"  notes                : {e['notes']}")
        txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(txt_path)
    except Exception as exc:
        print(f"WARNING: could not write {txt_path}: {exc}")

    csv_path = figures_dir / "contour_colorbar_ranges.csv"
    try:
        fieldnames = [
            "geo_name", "case_name", "field_key", "selected_variable",
            "derived_variable_mode", "output_file", "units", "range_min",
            "range_max", "range_source", "range_is_fixed", "palette_name",
            "legend_title", "timestamp", "notes",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for e in entries:
                writer.writerow({k: e.get(k, "") for k in fieldnames})
        written.append(csv_path)
    except Exception as exc:
        print(f"WARNING: could not write {csv_path}: {exc}")

    return written


# ---------------------------------------------------------------------------
# Save status JSON
# ---------------------------------------------------------------------------

def save_status(
    records: List[ExportRecord],
    figures_dir: Path,
    geo_name: str,
    case_name: str,
    view_margin: float = 1.20,
    zoom_out: float = 1.15,
    bounds_debug_sweep: bool = False,
    bounds_diagnostics: bool = False,
    manual_view_bounds: Optional[Tuple[float, float, float, float]] = None,
    manual_view_plane: str = "xy",
    legend_preset: str = "presentation_right",
    legend_reserve_right: float = 0.12,
    legend_mode: str = "hide",
) -> None:
    status_file = figures_dir / "contour_export_status.json"

    # Guard: don't create Windows-style paths on Linux/WSL
    if not _safe_mkdir(figures_dir):
        print(
            f"  NOTE: Output directory contains a Windows drive path ({figures_dir}); "
            "skipping directory creation and status JSON write on Linux/WSL. "
            "Status will be written on the server after pull."
        )
        return

    n_success = sum(1 for r in records if r.status == STATUS_SUCCESS)
    n_warn    = sum(1 for r in records if r.status == STATUS_WARN)
    n_failed  = sum(1 for r in records if r.status == STATUS_FAILED)
    n_skipped = sum(1 for r in records if r.status in (STATUS_SKIPPED_EXISTING, STATUS_DRY_RUN))

    payload = {
        "geo_name": geo_name,
        "case_name": case_name,
        "requested_view_margin": view_margin,
        "effective_view_margin": view_margin,
        "view_margin_applied": view_margin > 1.0,
        "requested_zoom_out": zoom_out,
        "zoom_out_applied_any": any(r.zoom_out_applied for r in records),
        "bounds_debug_sweep_requested": bounds_debug_sweep,
        "bounds_diagnostics_requested": bounds_diagnostics,
        "manual_view_bounds_requested": list(manual_view_bounds) if manual_view_bounds else None,
        "manual_view_plane_requested": manual_view_plane,
        "bounds_fit_applied_any": any(
            r.bounds_fit_status in ("applied", "success") for r in records
        ),
        "view_orientation_preserved": True,
        "requested_legend_mode": legend_mode,
        "requested_legend_preset": legend_preset,
        "requested_legend_reserve_right": legend_reserve_right,
        "legend_layout_applied_any": any(
            r.legend_layout_status == STATUS_SUCCESS for r in records
        ),
        "legend_hide_applied_any": any(
            r.legend_hide_status == STATUS_SUCCESS for r in records
        ),
        "colorbar_metadata_written_any": any(r.colorbar_metadata_written for r in records),
        "colorbar_metadata_files": sorted(
            {f for r in records for f in r.colorbar_metadata_files}
        ),
        "summary": {
            "total":   len(records),
            "success": n_success,
            "warn":    n_warn,
            "failed":  n_failed,
            "skipped": n_skipped,
        },
        "records": [asdict(r) for r in records],
    }

    with status_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    print(f"Status saved: {status_file}")


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------

def _print_summary(records: List[ExportRecord], figures_dir: Path) -> None:
    n_success = sum(1 for r in records if r.status == STATUS_SUCCESS)
    n_warn    = sum(1 for r in records if r.status == STATUS_WARN)
    n_failed  = sum(1 for r in records if r.status == STATUS_FAILED)
    n_skipped = sum(1 for r in records if r.status in (STATUS_SKIPPED_EXISTING, STATUS_DRY_RUN))

    print()
    print("=" * 72)
    print("CONTOUR EXPORT SUMMARY")
    print("=" * 72)
    print(f"  Total     : {len(records)}")
    print(f"  SUCCESS   : {n_success}")
    print(f"  WARN      : {n_warn}")
    print(f"  FAILED    : {n_failed}")
    print(f"  SKIPPED   : {n_skipped}")
    print(f"  Output dir: {figures_dir}")
    print("-" * 72)
    for r in records:
        rng = (
            f"[{r.color_range_min:.3g},{r.color_range_max:.3g}]"
            if (r.color_range_min is not None and r.color_range_max is not None)
            else f"[{r.color_range_mode}]"
        )
        var_str = f" var={r.selected_variable[:24]}" if r.selected_variable else ""
        print(f"  [{r.status:<18}] {r.field_key:<22} range={rng:<16}{var_str}")
        if r.derived_variable_mode:
            bulk_str = ""
            if r.bulk_reference_mode:
                bval = (
                    f"={r.bulk_reference_value:.5g}" if r.bulk_reference_value is not None else ""
                )
                bulk_str = f"  bulk={r.bulk_reference_mode}{bval}"
            print(f"    derive={r.derived_variable_mode}{bulk_str}")
        if r.message:
            msg_short = r.message[:120] if len(r.message) > 120 else r.message
            print(f"    {msg_short}")
    print("=" * 72)


def _write_colorbar_metadata_and_update_records(
    records: List[ExportRecord], figures_dir: Path, geo_name: str, case_name: str,
) -> None:
    """Write contour_colorbar_ranges.* and record the outcome on every ExportRecord."""
    written = save_colorbar_metadata(records, figures_dir, geo_name, case_name)
    written_names = [p.name for p in written]
    for r in records:
        r.colorbar_metadata_written = bool(written)
        r.colorbar_metadata_files = list(written_names)
    if written_names:
        print(f"Colorbar metadata written: {', '.join(written_names)}")
    else:
        print("Colorbar metadata: nothing written (see WARNING messages above, if any).")


def resolve_contour_export_exit_code(records: List[ExportRecord]) -> int:
    if any(r.status == STATUS_FAILED for r in records):
        return 2
    if any(r.status == STATUS_WARN for r in records):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def configure_text_output_encoding() -> None:
    import sys
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> int:
    configure_text_output_encoding()
    args = parse_args()

    manual_view_bounds: Optional[Tuple[float, float, float, float]] = None
    if args.manual_view_bounds:
        try:
            manual_view_bounds = _parse_manual_view_bounds(args.manual_view_bounds)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 3
    manual_view_plane = (args.manual_view_plane or "xy").strip().lower()

    # Resolve color ranges (raises ValueError on bad CLI input)
    try:
        field_ranges = _resolve_field_ranges(args)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 3

    config_path = Path(args.config).resolve()
    try:
        cfg = load_python_config(config_path)
    except Exception as exc:
        print(f"ERROR: Cannot load config from {config_path}: {exc}")
        return 3

    print(f"Config : {config_path}")

    try:
        paths = get_case_paths(cfg, geo_name_override=args.geo_name, case_name_override=args.case_name)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 3

    geo_name   = paths["geo_name"]
    case_name  = paths["case_name"]
    figures_dir = Path(paths["figures_dir"])

    print(f"Geo       : {geo_name}")
    print(f"Case      : {case_name}")
    print(f"Case file : {paths['final_case_file']}")
    print(f"Data file : {paths['final_data_file']}")
    print(f"Output    : {figures_dir}")
    print(f"Background: {args.background}  |  Triad: {'show' if args.show_triad else 'hide'}")
    print(f"Membrane  : {args.membrane_surface}")
    print(f"View margin: {args.view_margin}  |  Zoom-out: {args.zoom_out}")
    print(f"Bounds debug sweep: {'on' if args.bounds_debug_sweep else 'off'}")
    print(f"Bounds diagnostics: {'on' if args.bounds_diagnostics else 'off'}")
    if manual_view_bounds:
        print(f"Manual view bounds ({manual_view_plane}): {list(manual_view_bounds)}")
    legend_opts = {
        "mode": args.legend_mode,
        "preset": args.legend_preset,
        "x": args.legend_x,
        "y": args.legend_y,
        "width": args.legend_width,
        "height": args.legend_height,
        "text_size": args.legend_text_size,
        "title_size": args.legend_title_size,
        "label_count": args.legend_label_count,
        "reserve_right": args.legend_reserve_right,
    }
    print(f"Legend mode: {args.legend_mode}")
    print(
        f"Legend preset: {args.legend_preset}  |  "
        f"Legend reserve-right: {args.legend_reserve_right}"
    )

    if args.fields:
        field_keys = [k.strip() for k in args.fields.split(",") if k.strip()]
    else:
        field_keys = list(DEFAULT_FIELDS)
    print(f"Fields    : {field_keys}")

    # Show planned ranges
    for k in field_keys:
        rng = field_ranges.get(k)
        rng_str = f"{rng[0]} - {rng[1]}" if rng else "auto"
        print(f"  {k}: range = {rng_str}")

    plan = build_export_plan(
        cfg, paths, field_keys, field_ranges,
        auto_range=args.auto_range,
        operating_pressure=args.operating_pressure,
    )
    records: List[ExportRecord] = []
    bounds_diagnostics_records: List[dict] = []
    bounds_diagnostics_path = figures_dir / "pyensight_bounds_diagnostics.json"
    status_kwargs = {
        "view_margin": args.view_margin,
        "zoom_out": args.zoom_out,
        "bounds_debug_sweep": args.bounds_debug_sweep,
        "bounds_diagnostics": args.bounds_diagnostics,
        "manual_view_bounds": manual_view_bounds,
        "manual_view_plane": manual_view_plane,
        "legend_preset": args.legend_preset,
        "legend_reserve_right": args.legend_reserve_right,
        "legend_mode": args.legend_mode,
    }

    # ---- Dry-run ----
    if args.dry_run:
        print("\nDRY RUN - PyEnSight will not be launched; no images will be written.\n")
        _safe_mkdir(figures_dir)
        for item in plan:
            records.append(ExportRecord(
                geo_name=geo_name,
                case_name=case_name,
                field_key=item["field_key"],
                field_name=item["field_name"],
                target_surface_or_plane=item["surface_type"],
                output_file=str(item["output_file"]),
                status=STATUS_DRY_RUN,
                message="dry-run: no export performed",
                color_range_mode=item["color_range_mode"],
                color_range_min=item["color_range_min"],
                color_range_max=item["color_range_max"],
            ))
        _write_colorbar_metadata_and_update_records(records, figures_dir, geo_name, case_name)
        save_status(records, figures_dir, geo_name, case_name, **status_kwargs)
        _print_summary(records, figures_dir)
        return 0

    # ---- Skip existing ----
    remaining: List[dict] = []
    if args.skip_existing:
        for item in plan:
            if Path(item["output_file"]).is_file():
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_SKIPPED_EXISTING,
                    message=f"Already exists: {Path(item['output_file']).name}",
                    color_range_mode=item["color_range_mode"],
                    color_range_min=item["color_range_min"],
                    color_range_max=item["color_range_max"],
                ))
            else:
                remaining.append(item)
        plan = remaining

    if not plan:
        _safe_mkdir(figures_dir)
        _write_colorbar_metadata_and_update_records(records, figures_dir, geo_name, case_name)
        save_status(records, figures_dir, geo_name, case_name, **status_kwargs)
        _print_summary(records, figures_dir)
        return 0

    if not _PYENSIGHT_AVAILABLE:
        print(f"\nFATAL: PyEnSight is not importable.\n  {_PYENSIGHT_IMPORT_ERROR}")
        print("  Install: pip install ansys-pyensight-core")
        return 2

    _safe_mkdir(figures_dir)
    session = None

    try:
        session, open_error = open_case_in_pyensight(paths)

        if open_error:
            print(f"\nFATAL: Could not open case in PyEnSight:\n  {open_error}")
            for item in plan:
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_FAILED,
                    message=f"Case load failed: {open_error[:120]}",
                ))
            _write_colorbar_metadata_and_update_records(records, figures_dir, geo_name, case_name)
            save_status(records, figures_dir, geo_name, case_name, **status_kwargs)
            _print_summary(records, figures_dir)
            return 2

        # Apply clean scene settings once, after loading
        print("Applying clean scene settings...")
        scene_warnings = setup_clean_scene(
            session,
            background_mode=args.background,
            show_triad=args.show_triad,
        )
        if scene_warnings:
            print("  Scene setup warnings:")
            for w in scene_warnings:
                print(f"    {w}")

        for item in plan:
            try:
                export_contour(
                    session=session,
                    plan_item=item,
                    image_width=args.image_width,
                    image_height=args.image_height,
                    membrane_side=args.membrane_surface,
                    records=records,
                    geo_name=geo_name,
                    case_name=case_name,
                    scene_setup_warnings=scene_warnings,
                    view_margin=args.view_margin,
                    zoom_out=args.zoom_out,
                    bounds_debug_sweep=args.bounds_debug_sweep,
                    bounds_diagnostics=args.bounds_diagnostics,
                    bounds_diagnostics_path=bounds_diagnostics_path,
                    bounds_diagnostics_records=bounds_diagnostics_records,
                    manual_view_bounds=manual_view_bounds,
                    manual_view_plane=manual_view_plane,
                    legend_opts=legend_opts,
                )
            except Exception as exc_item:
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_FAILED,
                    message=f"Unhandled exception: {exc_item}",
                    color_range_mode=item["color_range_mode"],
                    color_range_min=item["color_range_min"],
                    color_range_max=item["color_range_max"],
                ))

    except Exception as exc_outer:
        print(f"\nFATAL: Unhandled exception:\n{traceback.format_exc()}")
        return 3

    finally:
        if session is not None:
            try:
                session.close()
                print("PyEnSight session closed.")
            except Exception as exc_close:
                print(f"Warning: session.close() raised: {exc_close}")

    _write_colorbar_metadata_and_update_records(records, figures_dir, geo_name, case_name)
    save_status(records, figures_dir, geo_name, case_name, **status_kwargs)
    _print_summary(records, figures_dir)

    return resolve_contour_export_exit_code(records)


if __name__ == "__main__":
    raise SystemExit(main())
