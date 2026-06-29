# -*- coding: utf-8 -*-
"""
03_pyensight_contour_export.py

Export presentation-quality contour images from a solved Fluent case using
PyEnSight (ansys.pyensight.core v0.11+, EnSight 25.1).

Fields exported:
  cp_inlet         - UDM-9  on membrane walls
  lmh              - UDM-8  on membrane walls
  wall_shear_rate  - wall-shear / mu on membrane + spacer walls
  velocity_midplane- velocity-magnitude on best-available plane/fluid surface

Usage:
  python 03_pyensight_contour_export.py --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03_pyensight_contour_export.py --dry-run --fields cp_inlet,lmh
  python 03_pyensight_contour_export.py --cp-range 1.00,1.15 --lmh-range 20,30
  python 03_pyensight_contour_export.py --auto-range --membrane-surface both
  python 03_pyensight_contour_export.py --skip-existing --image-width 2560 --image-height 1440
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, FrozenSet, List, Optional, Tuple

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
PROJECT_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
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

DEFAULT_FIELDS: List[str] = ["cp_inlet", "lmh", "wall_shear_rate", "velocity_midplane"]

# Candidates are tried in order (exact, then normalised) against ENS_VAR.DESCRIPTION.
FIELD_SPECS: dict = {
    "cp_inlet": {
        "display_label": "CP_inlet [-]",
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
    "lmh":               (20.0, 30.0),
    "wall_shear_rate":   None,           # None = auto-range
    "velocity_midplane": (0.0,  0.7),
}

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
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_range(s: str) -> Tuple[float, float]:
    """Parse 'min,max' string. Raises ValueError on bad input."""
    parts = s.strip().split(",")
    if len(parts) != 2:
        raise ValueError(f"Expected 'min,max' but got '{s}'.")
    return float(parts[0]), float(parts[1])


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
            "  cp_inlet:          1.00 – 1.15\n"
            "  lmh:               20.0 – 30.0\n"
            "  wall_shear_rate:   auto\n"
            "  velocity_midplane: 0.0  – 0.7\n\n"
            "Examples:\n"
            "  python 03_pyensight_contour_export.py "
            "--geo-name Diamond_Spacer --case-name u0p2_p6M\n"
            "  python 03_pyensight_contour_export.py --dry-run --fields cp_inlet,lmh\n"
            "  python 03_pyensight_contour_export.py "
            "--cp-range 1.00,1.15 --membrane-surface top\n"
        ),
    )

    # --- core ---
    parser.add_argument(
        "--config", type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help=f"Post-processing config Python file. Defaults to ${CONFIG_ENV_VAR} or 00_post_config.py.",
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
        "lmh":               args.lmh_range,
        "velocity_midplane": args.velocity_range,
        "wall_shear_rate":   args.wall_shear_rate_range,
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
            if abs(actual[0] - range_min) < 1e-9 and abs(actual[1] - range_max) < 1e-9:
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
    selected_var_str      = ""
    selected_surfaces_str = ""
    applied_mode  = color_range_mode
    applied_min: Optional[float] = color_range_min
    applied_max: Optional[float] = color_range_max
    palette_diag_str: Optional[str] = None

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

    # 3. Locate variable
    var_obj, matched_var_desc = find_ensight_variable(session, var_candidates)
    if var_obj is None:
        _record(
            STATUS_WARN, surface_desc,
            f"Variable not found among candidates {var_candidates}. "
            "Ensure the .dat.h5 contains UDM/field data and loaded correctly."
        )
        return

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
                warnings.append(f"Derived {derived_name} = '{matched_var_desc}' / mu={mu} Pa·s.")
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
        warnings.append(label_warn)

    # 8. Fit view to visible geometry
    try:
        session.ensight.view_transf.fit(0)
    except Exception:
        pass

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

    # 10. Restore full part visibility for next contour
    for p in all_parts:
        try:
            p.VISIBLE = 1
        except Exception:
            pass

    if output_file.is_file():
        final_status = STATUS_WARN if warnings else STATUS_SUCCESS
        msg = "; ".join(warnings) if warnings else f"Exported {output_file.name}"
        _record(final_status, surface_desc, msg)
    else:
        _record(
            STATUS_FAILED, surface_desc,
            "export.image() completed without error but output file was not created."
        )


# ---------------------------------------------------------------------------
# Save status JSON
# ---------------------------------------------------------------------------

def save_status(
    records: List[ExportRecord],
    figures_dir: Path,
    geo_name: str,
    case_name: str,
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
        var_str = f" var={r.selected_variable[:20]}" if r.selected_variable else ""
        print(f"  [{r.status:<18}] {r.field_key:<22} range={rng:<16}{var_str}")
        if r.message:
            msg_short = r.message[:100] if len(r.message) > 100 else r.message
            print(f"    {msg_short}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

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

    if args.fields:
        field_keys = [k.strip() for k in args.fields.split(",") if k.strip()]
    else:
        field_keys = list(DEFAULT_FIELDS)
    print(f"Fields    : {field_keys}")

    # Show planned ranges
    for k in field_keys:
        rng = field_ranges.get(k)
        rng_str = f"{rng[0]} – {rng[1]}" if rng else "auto"
        print(f"  {k}: range = {rng_str}")

    plan = build_export_plan(cfg, paths, field_keys, field_ranges, auto_range=args.auto_range)
    records: List[ExportRecord] = []

    # ---- Dry-run ----
    if args.dry_run:
        print("\nDRY RUN — PyEnSight will not be launched; no images will be written.\n")
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
        save_status(records, figures_dir, geo_name, case_name)
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
        save_status(records, figures_dir, geo_name, case_name)
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
            save_status(records, figures_dir, geo_name, case_name)
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

    save_status(records, figures_dir, geo_name, case_name)
    _print_summary(records, figures_dir)

    n_failed = sum(1 for r in records if r.status == STATUS_FAILED)
    return 2 if n_failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
