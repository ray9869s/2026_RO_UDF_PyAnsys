# -*- coding: utf-8 -*-
"""
08_pyensight_extra_figures.py

Optional, standalone PyEnSight script that exports extra presentation figures
for fouling/mixing analysis from ONE solved final case:

  1. yz cross-section (x = constant) slice contours INSIDE the active
     membrane region only:
       - concentration (NaCl mass fraction / species / CP UDM)
       - velocity magnitude
       - x-velocity (if available)
       - vorticity magnitude (if available)
  2. Vortex figures restricted to the active membrane x-range:
       - Q-criterion iso-surface colored by velocity magnitude
       - vorticity magnitude on the channel z-mid plane (active region only)
       - Q-criterion on the yz slice at active x/L = 0.50

Slice visual quality:
  yz slice figures use smooth shading, hidden mesh edges, nodal-averaged
  display of cell-centered (Fluent) variables via ElemToNode, a continuous
  high-level-count palette, a right-outside colorbar, and a small camera fit
  margin — all configurable below and all best-effort (WARN, never fail).
  Q-criterion / vortex figures keep their previous rendering.

Q-criterion / vorticity fallback:
  Existing result variables are searched first. If Q-criterion (or vorticity)
  is not in the loaded results, it is computed from the velocity vector via
  the EnSight Calculator during the real run (components -> Grad ->
  Q_criteria; Vort for vorticity), on the active-region clipped volume —
  never silently over the full domain including the buffers. Failures warn
  and skip only the affected figures (unless strict_qcriterion_required).

Buffer exclusion:
  The fluid domain contains empty inlet/outlet buffer regions before and
  after the spacer/membrane region. The active membrane x-range is detected
  from the wall_top_mem / wall_bottom_mem wall parts (never from the buffer
  walls and never from the full-domain extents). All slice x positions are
  fractions of the ACTIVE membrane x-range, and vortex visualizations are
  clipped to that x-range.

This script does NOT modify or replace any existing post-processing script.
It only ever writes PNG images under:
  03_Results/<geo_name>/<case_name>/post/figures/extra/{slices,vortex}/

Usage:
  python 08_pyensight_extra_figures.py                       # dry run (default)
  python 08_pyensight_extra_figures.py --geo-name Diamond_Spacer --case-name u0p2_p6M --run
  PYFLUENT_EXTRA_FIGURES_OVERRIDES='{"geo_name":"Diamond_Spacer","case_name":"u0p2_p6M","dry_run":false}' \
      python 08_pyensight_extra_figures.py
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import traceback
from pathlib import Path
from typing import Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# PyEnSight import guard — only required for a real run (not for dry runs)
# ---------------------------------------------------------------------------

_PYENSIGHT_AVAILABLE = False
_PYENSIGHT_IMPORT_ERROR = ""

try:
    import ansys.pyensight.core as _pyensight_pkg  # noqa: F401
    _PYENSIGHT_AVAILABLE = True
except ImportError as _exc:
    _PYENSIGHT_IMPORT_ERROR = str(_exc)

# ---------------------------------------------------------------------------
# Paths — relative to this script; never hard-code C:\ or Windows paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(
    os.environ.get("PYFLUENT_PROJECT_ROOT", str(SCRIPT_DIR.parents[1]))
).resolve()

# ===========================================================================
# ===== Edit here: CONFIG ===================================================
# ===========================================================================

CONFIG: dict = {
    # --- case selection ---
    "geo_name": "Diamond_Spacer",
    "case_name": "u0p2_p6M",

    # --- safety: dry run by default (no EnSight launch, no files written) ---
    "dry_run": True,

    # --- active membrane x-range ---
    # Leave both as None for automatic detection from the active membrane
    # wall parts. If BOTH are set (in metres), they override auto-detection.
    "active_x_min": None,
    "active_x_max": None,

    # Wall parts used for automatic active x-range detection. Buffer walls
    # are listed only so they can be explicitly EXCLUDED from detection.
    "active_membrane_part_candidates": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_part_candidates": ["wall_top_buffer", "wall_bottom_buffer"],

    # --- yz slice positions, as fractions of the ACTIVE membrane x-range ---
    # x = active_x_min + f * (active_x_max - active_x_min)
    "slice_x_fractions": [0.05, 0.25, 0.50, 0.75, 0.95],

    # --- which fields/figures to export ---
    "include_concentration": True,
    "include_velocity_magnitude": True,
    "include_x_velocity": True,
    "include_vorticity": True,
    "include_qcriterion": True,

    # --- image size ---
    "image_width": 1920,
    "image_height": 1080,

    # --- Q-criterion iso-surface threshold ---
    # qcriterion_threshold: explicit iso value (manual). None = use mode below.
    # qcriterion_threshold_mode:
    #   "auto_active_max_fraction" — threshold = qcriterion_auto_fraction *
    #       (max Q read back from the ACTIVE-region clip's palette range).
    #       A true percentile would require pulling full field arrays, so this
    #       max-fraction heuristic is the supported automatic mode.
    #   "manual" — require qcriterion_threshold; skip the iso figure if None.
    "qcriterion_threshold": None,
    "qcriterion_threshold_mode": "auto_active_max_fraction",
    "qcriterion_auto_fraction": 0.02,
    # qcriterion_threshold_list: explicit list of iso thresholds. When
    # non-empty it wins over qcriterion_threshold and the auto mode; one
    # figure is exported per value as
    #   qcriterion_iso_velocity_colored_thr_<value>.png
    # (existing threshold-specific files are never overwritten). When every
    # threshold source is unavailable (no list, no single value, auto Q max
    # readback fails), a conservative default sweep is exported instead of
    # skipping the iso figure entirely — see DEFAULT_Q_THRESHOLD_SWEEP.
    "qcriterion_threshold_list": [],

    # --- EnSight Calculator fallbacks for missing variables ---
    # When Q-criterion is not present in the loaded results, compute it from
    # the velocity vector during the real run (never during dry run):
    #   Vel_x = Velocity[X], Vel_y = Velocity[Y], Vel_z = Velocity[Z]
    #   Grad_Vel_x = Grad(plist, Vel_x)   (same for y, z)
    #   Q = Q_criteria(plist, Grad_Vel_x, Grad_Vel_y, Grad_Vel_z)
    # plist = the active-region clipped volume when available (never silently
    # the full domain including the inlet/outlet buffers).
    "compute_qcriterion_if_missing": True,
    "qcriterion_variable_name": "Q_criterion_computed",
    # strict_qcriterion_required=True turns a failed/unavailable Q into a hard
    # error (non-zero exit) instead of "warn and skip only the Q figures".
    "strict_qcriterion_required": False,
    # Same idea for vorticity: Vorticity_computed = Vort(plist, Velocity)
    # (vector; EnSight colors by its magnitude). Used for the vortex mid-plane
    # figure only — yz vorticity slices still require a dataset variable.
    "compute_vorticity_if_missing": True,

    # --- yz slice visual quality (slice figures ONLY; Q/vortex figures keep
    #     their previous rendering) ---
    # smooth_slice_rendering: smooth-shaded surfaces AND nodal display of
    # cell-centered variables (Fluent data is element-centered, which renders
    # as flat per-cell "blocky" patches even on a fine mesh; ElemToNode
    # averaging gives a continuous interpolated field).
    "smooth_slice_rendering": True,
    # hide_slice_edges: hide the element/mesh edge overlay on slice parts.
    "hide_slice_edges": True,
    # contour_level_count: number of palette levels (more = smoother ramp).
    "contour_level_count": 64,
    # use_continuous_palette: continuous color interpolation instead of bands.
    "use_continuous_palette": True,
    # colorbar_position: "right_outside" moves the legend to the right edge so
    # it does not overlap the slice; any other value leaves EnSight defaults.
    "colorbar_position": "right_outside",
    "colorbar_width_fraction": 0.05,
    # camera_fit_margin: extra zoom-out after fit (0.08 = ~8 % margin) so the
    # slice does not touch the viewport border. 0.0 disables.
    "camera_fit_margin": 0.08,

    # --- Q iso-surface presentation style (Q iso figures ONLY:
    #     qcriterion_iso_velocity_colored[_thr_*].png; slices untouched) ---
    # qiso_clean_scene is the master switch for the scene cleanup below; the
    # individual flags control each effect. All are best-effort (WARN, never
    # fail) because EnSight builds differ.
    "qiso_clean_scene": True,
    "qiso_white_background": True,
    "qiso_background_rgb": [1.0, 1.0, 1.0],
    "qiso_disable_shadows": True,
    "qiso_disable_reflections": True,
    "qiso_disable_ground_plane": True,
    "qiso_hide_axes": True,
    "qiso_hide_grid": True,
    "qiso_hide_bounding_box": True,
    # Iso-surface part style
    "qiso_smooth_shading": True,
    "qiso_hide_edges": True,
    # The iso SURFACE is a Q-criterion level set, but its COLOR is velocity
    # magnitude by default — so "Velocity [m/s]" is the correct colorbar
    # title. Set qiso_color_by_velocity=False to color by Q instead (the
    # colorbar title then becomes "Q-criterion [1/s^2]").
    "qiso_color_by_velocity": True,
    "qiso_colorbar_title": "Velocity [m/s]",
    "qiso_camera_fit_margin": 0.08,
}

# Keys that may be overridden via the PYFLUENT_EXTRA_FIGURES_OVERRIDES env
# variable (JSON object), mirroring PYFLUENT_POST_OVERRIDES elsewhere.
OVERRIDES_ENV_VAR = "PYFLUENT_EXTRA_FIGURES_OVERRIDES"
OVERRIDABLE_KEYS = {
    "geo_name", "case_name", "dry_run",
    "active_x_min", "active_x_max", "slice_x_fractions",
    "include_concentration", "include_velocity_magnitude",
    "include_x_velocity", "include_vorticity", "include_qcriterion",
    "image_width", "image_height",
    "qcriterion_threshold", "qcriterion_threshold_mode",
    "qcriterion_auto_fraction", "qcriterion_threshold_list",
    "compute_qcriterion_if_missing", "qcriterion_variable_name",
    "strict_qcriterion_required", "compute_vorticity_if_missing",
    "smooth_slice_rendering", "hide_slice_edges", "contour_level_count",
    "use_continuous_palette", "colorbar_position", "colorbar_width_fraction",
    "camera_fit_margin",
    "qiso_clean_scene", "qiso_white_background", "qiso_background_rgb",
    "qiso_disable_shadows", "qiso_disable_reflections",
    "qiso_disable_ground_plane", "qiso_hide_axes", "qiso_hide_grid",
    "qiso_hide_bounding_box", "qiso_smooth_shading", "qiso_hide_edges",
    "qiso_color_by_velocity", "qiso_colorbar_title", "qiso_camera_fit_margin",
}

# ---------------------------------------------------------------------------
# Variable name candidates — tried in order (exact, normalized, substring)
# against ENS_VAR.DESCRIPTION. Do not assume exact names blindly.
# ---------------------------------------------------------------------------

FIELD_VAR_CANDIDATES: dict = {
    "concentration": [
        "nacl", "NaCl", "salt", "Salt",
        "mass-fraction-of-nacl", "mass-fraction-of-NaCl", "mass-fraction-of-salt",
        "mass fraction of nacl", "nacl-mass-fraction", "salt-mass-fraction",
        "species-0", "species_0", "yi-0", "yi_0", "yi-nacl", "yi_nacl",
        "molar-concentration-of-nacl", "nacl-concentration",
        "species mass fraction", "species", "concentration",
        # CP UDM (concentration polarization) as last resort
        "udm-9", "UDM-9", "cp", "CP",
    ],
    "velocity_mag": [
        "velocity-magnitude", "Velocity Magnitude", "velocity_magnitude",
        "Velocity", "velocity", "speed", "Speed",
    ],
    "x_velocity": [
        "x-velocity", "velocity-x", "X Velocity", "x_velocity",
        "Velocity X", "u-velocity",
    ],
    "vorticity_mag": [
        "vorticity-mag", "vorticity magnitude", "Vorticity Magnitude",
        "vorticity-magnitude", "vorticity", "Vorticity", "curl",
    ],
    "qcriterion": [
        "q-criterion", "Q Criterion", "Q-Criterion", "q_criterion",
        "Velocity.Invariant Q", "invariant-q", "criterion",
    ],
}

# Vector velocity variable candidates for the Calculator Q/vorticity fallback
# (component extraction needs the VECTOR variable, not a magnitude scalar).
VELOCITY_VECTOR_CANDIDATES: List[str] = [
    "velocity", "Velocity", "VELOCITY", "velocity-vector", "Velocity Vector",
]

# Names of the intermediate Calculator variables (reused if already present)
COMPUTED_VORTICITY_NAME = "Vorticity_computed"

# Fallback Q iso-surface threshold sweep [1/s^2], used when no explicit
# threshold(s) are configured and the automatic active-region Q max readback
# fails. Spans the range typical for sub-mm spacer channels at these Re.
DEFAULT_Q_THRESHOLD_SWEEP: List[float] = [
    100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0,
]

FIELD_DISPLAY_LABELS: dict = {
    "concentration": "Concentration (NaCl)",
    "velocity_mag": "Velocity magnitude",
    "x_velocity": "x-velocity",
    "vorticity_mag": "Vorticity magnitude",
    "qcriterion": "Q-criterion",
}

# Slice output filename suffix per field key
SLICE_FILE_SUFFIX: dict = {
    "concentration": "concentration",
    "velocity_mag": "velocity_mag",
    "x_velocity": "x_velocity",
    "vorticity_mag": "vorticity_mag",
}


# ---------------------------------------------------------------------------
# Config assembly: CONFIG defaults < env JSON overrides < CLI args
# ---------------------------------------------------------------------------

def apply_env_overrides(cfg: dict) -> dict:
    raw = os.environ.get(OVERRIDES_ENV_VAR)
    if not raw:
        return cfg
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{OVERRIDES_ENV_VAR} is not valid JSON: {exc}") from exc
    if not isinstance(overrides, dict):
        raise ValueError(f"{OVERRIDES_ENV_VAR} must be a JSON object.")
    out = dict(cfg)
    for key, value in overrides.items():
        if key in OVERRIDABLE_KEYS:
            out[key] = value
        else:
            print(f"WARNING: {OVERRIDES_ENV_VAR} key '{key}' is not overridable; ignored.")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export extra fouling/mixing presentation figures (active-membrane "
            "yz slices + vortex figures) from one final case via PyEnSight."
        ),
    )
    parser.add_argument("--geo-name", type=str, default=None, help="Override geo_name.")
    parser.add_argument("--case-name", type=str, default=None, help="Override case_name.")
    parser.add_argument(
        "--run", action="store_true",
        help="Actually launch PyEnSight and export images (dry_run=False).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Force dry run (plan only). Wins over --run.",
    )
    parser.add_argument(
        "--active-x-min", type=float, default=None,
        help="Manual active membrane x_min [m]; needs --active-x-max too.",
    )
    parser.add_argument(
        "--active-x-max", type=float, default=None,
        help="Manual active membrane x_max [m]; needs --active-x-min too.",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> dict:
    cfg = apply_env_overrides(dict(CONFIG))
    if args.geo_name is not None:
        cfg["geo_name"] = args.geo_name
    if args.case_name is not None:
        cfg["case_name"] = args.case_name
    if args.run:
        cfg["dry_run"] = False
    if args.dry_run:
        cfg["dry_run"] = True
    if args.active_x_min is not None:
        cfg["active_x_min"] = args.active_x_min
    if args.active_x_max is not None:
        cfg["active_x_max"] = args.active_x_max
    return cfg


def build_paths(cfg: dict) -> dict:
    geo_name = str(cfg["geo_name"])
    case_name = str(cfg["case_name"])
    case_path = PROJECT_ROOT / "03_Results" / geo_name / case_name
    extra_dir = case_path / "post" / "figures" / "extra"
    return {
        "case_path": case_path,
        "final_case_file": case_path / f"{geo_name}_{case_name}_final.cas.h5",
        "final_data_file": case_path / f"{geo_name}_{case_name}_final.dat.h5",
        "slices_dir": extra_dir / "slices",
        "vortex_dir": extra_dir / "vortex",
    }


# ---------------------------------------------------------------------------
# Small shared helpers (same conventions as 03_pyensight_contour_export.py)
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _has_windows_drive(p: Path) -> bool:
    return any(re.match(r"^[A-Za-z]:$", part) for part in p.parts)


def _safe_mkdir(p: Path) -> bool:
    if platform.system() != "Windows" and _has_windows_drive(p):
        return False
    p.mkdir(parents=True, exist_ok=True)
    return True


def _frac_tag(fraction: float) -> str:
    """0.05 -> 'x005', 0.50 -> 'x050', 0.95 -> 'x095'."""
    return f"x{int(round(fraction * 100)):03d}"


def _threshold_tag(value: float) -> str:
    """Filename-safe threshold tag: 100.0 -> '100', 523.7 -> '523p7',
    2.5e-05 -> '2p5em05'."""
    v = float(value)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:g}".replace("-", "m").replace("+", "").replace(".", "p")


def requested_slice_fields(cfg: dict) -> List[str]:
    fields = []
    if cfg["include_concentration"]:
        fields.append("concentration")
    if cfg["include_velocity_magnitude"]:
        fields.append("velocity_mag")
    if cfg["include_x_velocity"]:
        fields.append("x_velocity")
    if cfg["include_vorticity"]:
        fields.append("vorticity_mag")
    return fields


def manual_extents(cfg: dict) -> Optional[Tuple[float, float]]:
    """Return (x_min, x_max) if BOTH manual values are set and valid."""
    x_min, x_max = cfg["active_x_min"], cfg["active_x_max"]
    if x_min is None or x_max is None:
        return None
    x_min, x_max = float(x_min), float(x_max)
    if not x_min < x_max:
        raise ValueError(
            f"Manual active extents invalid: active_x_min={x_min} must be < active_x_max={x_max}."
        )
    return x_min, x_max


# ---------------------------------------------------------------------------
# Part / variable lookup in a live PyEnSight session
# ---------------------------------------------------------------------------

def list_all_parts(session: Any) -> List[Any]:
    return [p for p in session.ensight.objs.core.PARTS if getattr(p, "DESCRIPTION", None)]


def find_parts_by_base_names(all_parts: List[Any], base_names: List[str]) -> List[Any]:
    """Match part DESCRIPTION against base names: exact, 'base.suffix', then
    normalized exact / normalized-prefix."""
    matched = []
    for part in all_parts:
        desc = str(part.DESCRIPTION)
        norm = _normalize(desc)
        for base in base_names:
            base_norm = _normalize(base)
            if (
                desc == base
                or desc.startswith(base + ".")
                or norm == base_norm
                or norm.startswith(base_norm + "_")
                or norm.startswith(base_norm + ".")
            ):
                matched.append(part)
                break
    return matched


def find_fluid_volume_parts(session: Any) -> List[Any]:
    try:
        vol_parts = list(session.ensight.utils.parts.select_parts_by_dimension(3))
        if vol_parts:
            return vol_parts
    except Exception:
        pass
    try:
        return [
            p for p in session.ensight.objs.core.PARTS
            if getattr(p, "DIMENSIONALITY", None) == 3
        ]
    except Exception:
        return []


def find_variable(session: Any, candidates: List[str]) -> Tuple[Optional[Any], Optional[str]]:
    """Return (ENS_VAR, matched DESCRIPTION): exact, then normalized, then substring."""
    try:
        all_vars = list(session.ensight.objs.core.VARIABLES)
        desc_to_var = {v.DESCRIPTION: v for v in all_vars if v.DESCRIPTION}
    except Exception:
        return None, None

    for cand in candidates:
        if cand in desc_to_var:
            return desc_to_var[cand], cand

    norm_map = {_normalize(k): (k, v) for k, v in desc_to_var.items()}
    for cand in candidates:
        hit = norm_map.get(_normalize(cand))
        if hit is not None:
            return hit[1], hit[0]

    for cand in candidates:
        cand_low = cand.lower()
        for desc, var in desc_to_var.items():
            if cand_low in desc.lower():
                return var, desc
    return None, None


def print_variable_inventory(session: Any) -> None:
    try:
        descs = sorted(
            str(v.DESCRIPTION)
            for v in session.ensight.objs.core.VARIABLES
            if v.DESCRIPTION
        )
    except Exception as exc:
        print(f"WARNING: could not list EnSight variables: {exc}")
        return
    print(f"\nVariable inventory ({len(descs)} variables):")
    for desc in descs:
        print(f"  - {desc}")


# ---------------------------------------------------------------------------
# Part extents (bounding box) — compact multi-API attempt, same layouts as
# 03_pyensight_contour_export.py accepts
# ---------------------------------------------------------------------------

_BOUNDS_ATTEMPTS: List[Tuple[str, str]] = [
    ("attr", "BOUNDS"), ("attr", "bounds"),
    ("attr", "EXTENTS"), ("attr", "extents"),
    ("attr", "BOUNDINGBOX"), ("attr", "BOUNDING_BOX"),
    ("method", "get_bounds"), ("method", "get_extents"),
]


def _flatten_numeric(value: Any, limit: int = 24) -> List[float]:
    out: List[float] = []

    def _walk(v: Any) -> None:
        if len(out) >= limit or v is None or isinstance(v, (str, bytes, bool)):
            return
        if isinstance(v, (int, float)):
            out.append(float(v))
            return
        if hasattr(v, "tolist"):
            try:
                _walk(v.tolist())
                return
            except Exception:
                pass
        try:
            for item in v:
                _walk(item)
                if len(out) >= limit:
                    break
        except TypeError:
            return

    _walk(value)
    return out


def _normalise_bounds(value: Any) -> Optional[Tuple[float, float, float, float, float, float]]:
    """Accept exactly one unambiguous 6-value layout:
      A: [xmin, xmax, ymin, ymax, zmin, zmax]
      B: [xmin, ymin, zmin, xmax, ymax, zmax]
    """
    nums = _flatten_numeric(value)
    if len(nums) != 6:
        return None

    def _valid(b: Tuple[float, ...]) -> bool:
        return b[0] < b[1] and b[2] < b[3] and b[4] <= b[5]

    layout_a = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
    layout_b = (nums[0], nums[3], nums[1], nums[4], nums[2], nums[5])
    valid = [b for b in (layout_a, layout_b) if _valid(b)]
    if len(valid) == 1:
        return valid[0]
    return None  # ambiguous or invalid — refuse rather than guess


def get_part_extents(part: Any) -> Optional[Tuple[float, float, float, float, float, float]]:
    for kind, name in _BOUNDS_ATTEMPTS:
        try:
            raw = getattr(part, name)
            if kind == "method":
                if not callable(raw):
                    continue
                raw = raw()
        except Exception:
            continue
        bounds = _normalise_bounds(raw)
        if bounds is not None:
            return bounds
    return None


def accumulate_x_extents(parts: List[Any]) -> Optional[Tuple[float, float]]:
    x_mins, x_maxs = [], []
    for part in parts:
        bounds = get_part_extents(part)
        if bounds is not None:
            x_mins.append(bounds[0])
            x_maxs.append(bounds[1])
    if not x_mins:
        return None
    return min(x_mins), max(x_maxs)


# ---------------------------------------------------------------------------
# Active membrane x-range detection
# ---------------------------------------------------------------------------

def detect_active_x_range(session: Any, cfg: dict) -> Tuple[float, float]:
    """Detect (active_x_min, active_x_max) from the active membrane wall
    parts only. Buffer wall parts are explicitly excluded. Raises RuntimeError
    (never falls back to full-domain extents) if detection fails."""
    all_parts = list_all_parts(session)
    all_names = [str(p.DESCRIPTION) for p in all_parts]

    buffer_parts = find_parts_by_base_names(all_parts, list(cfg["buffer_part_candidates"]))
    buffer_ids = {id(p) for p in buffer_parts}

    membrane_parts = [
        p for p in find_parts_by_base_names(
            all_parts, list(cfg["active_membrane_part_candidates"])
        )
        if id(p) not in buffer_ids
    ]

    manual_hint = (
        "Set manual config values at the top of this script (or via "
        f"{OVERRIDES_ENV_VAR} / --active-x-min/--active-x-max):\n"
        "  active_x_min = <x of membrane leading edge [m]>\n"
        "  active_x_max = <x of membrane trailing edge [m]>"
    )

    if not membrane_parts:
        raise RuntimeError(
            "Active membrane wall parts not found. Looked for "
            f"{cfg['active_membrane_part_candidates']} (excluding buffers "
            f"{cfg['buffer_part_candidates']}).\n"
            f"Available parts ({len(all_names)}): {all_names}\n" + manual_hint
        )

    print("Active membrane parts used for x-range detection:")
    for p in membrane_parts:
        print(f"  - {p.DESCRIPTION}")

    x_range = accumulate_x_extents(membrane_parts)
    if x_range is None or not x_range[0] < x_range[1]:
        raise RuntimeError(
            "Could not read usable x-extents from the active membrane parts "
            f"{[str(p.DESCRIPTION) for p in membrane_parts]} "
            f"(got {x_range}). NOT falling back to the full domain, because "
            "that would include the inlet/outlet buffer regions.\n" + manual_hint
        )
    return x_range


# ---------------------------------------------------------------------------
# Scene setup and rendering helpers (all best-effort)
# ---------------------------------------------------------------------------

def setup_clean_scene(session: Any) -> None:
    ens = session.ensight
    try:
        vport = ens.objs.core.VPORTS[0]
        vport.BACKGROUNDTYPE = ens.objs.enums.VPORT_CONS
        vport.CONSTANTRGB = [1.0, 1.0, 1.0]
    except Exception as exc:
        print(f"WARNING: white background not applied: {exc}")
    try:
        ens.view.perspective("OFF")
        ens.objs.core.VPORTS[0].PERSPECTIVE = False
    except Exception:
        pass
    try:
        ens.objs.core.VPORTS[0].GLOBALAXISVISIBLE = 0
        ens.annotation.axis_global("off")
        ens.annotation.axis_local("off")
        ens.annotation.axis_model("off")
    except Exception:
        pass
    try:
        for light in ens.objs.core.LIGHTSOURCES:
            light.CASTS_SHADOWS = 0
    except Exception:
        pass


def show_only_parts(session: Any, visible_parts: List[Any]) -> None:
    visible_ids = {id(p) for p in visible_parts}
    for p in session.ensight.objs.core.PARTS:
        try:
            p.VISIBLE = 1 if id(p) in visible_ids else 0
        except Exception:
            pass


def color_part_by_variable(session: Any, part: Any, var_desc: str) -> None:
    part.COLORBYPALETTE = var_desc
    # Best-effort: rescale the palette to the visible (active-region) part so
    # the color range reflects the active membrane region, not the buffers.
    try:
        for palette in session.ensight.objs.core.PALETTES:
            if _normalize(palette.DESCRIPTION) == _normalize(var_desc):
                palette.set_range_to_part_minmax()
                break
    except Exception:
        pass


def read_palette_max(session: Any, var_desc: str) -> Optional[float]:
    try:
        for palette in session.ensight.objs.core.PALETTES:
            if _normalize(palette.DESCRIPTION) == _normalize(var_desc):
                minmax = _flatten_numeric(palette.MINMAX)
                if len(minmax) == 2:
                    return minmax[1]
    except Exception:
        pass
    return None


def set_view(session: Any, direction: Tuple[float, float, float],
             up_axis: Tuple[float, float, float],
             fit_margin: float = 0.0) -> None:
    try:
        session.ensight.utils.views.set_view_direction(
            direction[0], direction[1], direction[2],
            perspective=False, up_axis=up_axis,
        )
    except Exception as exc:
        print(f"WARNING: set_view_direction{direction} failed: {exc}")
    try:
        session.ensight.view_transf.fit(0)
    except Exception as exc:
        print(f"WARNING: view fit failed: {exc}")
    if fit_margin and fit_margin > 0.0:
        # Server testing of 03_pyensight_contour_export.py empirically
        # confirmed that on this EnSight build view_transf.zoom(v) zooms OUT
        # for v > 1 — so 1.0 + margin adds whitespace around the fitted view.
        try:
            session.ensight.view_transf.zoom(1.0 + float(fit_margin))
        except Exception as exc:
            print(f"WARNING: camera fit margin ({fit_margin}) not applied: {exc}")


def export_png(session: Any, output_file: Path, cfg: dict) -> bool:
    try:
        png_path = str(output_file).replace("\\", "/")
        session.ensight.utils.export.image(
            png_path,
            width=int(cfg["image_width"]),
            height=int(cfg["image_height"]),
            passes=4,
        )
    except Exception as exc:
        print(f"WARNING: image export failed for {output_file.name}: {exc}")
        return False
    if not output_file.is_file():
        print(f"WARNING: export reported success but file missing: {output_file}")
        return False
    print(f"Exported: {output_file}")
    return True


# ---------------------------------------------------------------------------
# yz slice visual quality (slice figures only — Q/vortex figures unchanged)
# ---------------------------------------------------------------------------

_PRINTED_ONCE: set = set()


def _print_once(tag: str, message: str) -> None:
    """Print a per-run status line only the first time (5 slice positions x
    4 fields would otherwise repeat every rendering message 20 times)."""
    if tag not in _PRINTED_ONCE:
        _PRINTED_ONCE.add(tag)
        print(message)


def apply_slice_part_style(session: Any, part: Any, cfg: dict) -> None:
    """Hide mesh/element edges and enable smooth shading on one slice part.
    Failures are warnings only — the figure is still exported."""
    enums = session.ensight.objs.enums

    if cfg["hide_slice_edges"]:
        try:
            part.HIDDENLINE = 0
            _print_once("edges", "  Slice style: element/mesh edge overlay hidden (HIDDENLINE=0).")
        except Exception as exc:
            _print_once("edges", f"WARNING: could not hide slice edges: {exc}")

    if cfg["smooth_slice_rendering"]:
        applied = None
        for enum_name in ("SHAD_SMOOTH_REFINED", "SHAD_SMOOTH", "SHAD_GOURAUD"):
            try:
                part.SHADING = getattr(enums, enum_name)
                applied = enum_name
                break
            except Exception:
                continue
        if applied:
            _print_once("shading", f"  Slice style: smooth shading applied (SHADING={applied}).")
        else:
            _print_once("shading", "WARNING: smooth shading could not be applied; "
                                   "using the part's default shading.")


def resolve_slice_display_variable(
    session: Any,
    var_obj: Any,
    var_desc: str,
    source_parts: List[Any],
    cfg: dict,
) -> str:
    """Return the variable DESCRIPTION to color slices by. Fluent variables
    are usually element(cell)-centered, which EnSight renders as flat per-cell
    patches ("blocky"). When smooth_slice_rendering is on and the variable is
    element-centered, create a nodal-averaged copy via the Calculator:
        <name>_nodal = ElemToNode(plist, <var>)
    Falls back to the original variable with a WARNING on any failure."""
    if not cfg["smooth_slice_rendering"]:
        return var_desc

    try:
        elem_enum = session.ensight.objs.enums.ENS_VAR_ELEM
        location = var_obj.LOCATION
    except Exception as exc:
        _print_once(f"nodal:{var_desc}",
                    f"WARNING: could not read centering of '{var_desc}' ({exc}); "
                    "rendering it as-is.")
        return var_desc

    if location != elem_enum:
        _print_once(f"nodal:{var_desc}",
                    f"  Slice style: '{var_desc}' is already node-based; "
                    "interpolated rendering needs no conversion.")
        return var_desc

    nodal_name = re.sub(r"\W", "_", var_desc) + "_nodal"
    try:
        _get_or_create_calc_variable(
            session, nodal_name, f"ElemToNode(plist,{var_desc})",
            source_parts, step=f"ElemToNode for '{var_desc}'",
        )
        _print_once(f"nodal:{var_desc}",
                    f"  Slice style: '{var_desc}' is cell-centered — using "
                    f"nodal-averaged '{nodal_name}' for smooth interpolated rendering.")
        return nodal_name
    except RuntimeError as exc:
        _print_once(f"nodal:{var_desc}",
                    f"WARNING: nodal conversion of '{var_desc}' failed ({exc}); "
                    "rendering the cell-centered variable (may look blocky).")
        return var_desc


def _find_palette(session: Any, var_desc: str) -> Optional[Any]:
    try:
        for palette in session.ensight.objs.core.PALETTES:
            if _normalize(palette.DESCRIPTION) == _normalize(var_desc):
                return palette
    except Exception:
        pass
    return None


def tune_slice_palette(session: Any, var_desc: str, cfg: dict) -> None:
    """Continuous interpolation + more levels for a smooth color ramp."""
    palette = _find_palette(session, var_desc)
    if palette is None:
        _print_once(f"pal:{var_desc}",
                    f"WARNING: palette for '{var_desc}' not found; palette "
                    "interpolation/levels left at EnSight defaults.")
        return

    if cfg["use_continuous_palette"]:
        try:
            palette.INTERP = session.ensight.objs.enums.PALETTE_CONTINUOUS
            _print_once(f"interp:{var_desc}",
                        f"  Slice palette '{var_desc}': continuous interpolation.")
        except Exception as exc:
            _print_once(f"interp:{var_desc}",
                        f"WARNING: continuous palette for '{var_desc}' failed: {exc}")

    level_count = int(cfg["contour_level_count"])
    if level_count > 0:
        applied = False
        try:
            palette.NLEVELS = level_count
            applied = True
        except Exception:
            try:
                palette.COLORS_PER_LEVEL = level_count
                applied = True
            except Exception as exc:
                _print_once(f"levels:{var_desc}",
                            f"WARNING: contour level count for '{var_desc}' "
                            f"not applied: {exc}")
        if applied:
            _print_once(f"levels:{var_desc}",
                        f"  Slice palette '{var_desc}': contour level count = {level_count}.")


def position_slice_colorbar(session: Any, var_desc: str, cfg: dict) -> None:
    """Move the legend/colorbar to the right edge of the viewport so it does
    not overlap the slice. Only the 'right_outside' preset is implemented;
    any other value leaves the EnSight default placement."""
    if str(cfg["colorbar_position"]) != "right_outside":
        _print_once("legend", f"  Slice colorbar: position '{cfg['colorbar_position']}' "
                              "not a known preset; leaving EnSight default.")
        return

    enums = session.ensight.objs.enums
    try:
        legends = [
            a for a in session.ensight.objs.core.ANNOTS
            if getattr(a, "ANNOTTYPE", None) == enums.ANNOT_LEGEND
        ]
    except Exception as exc:
        _print_once("legend", f"WARNING: could not list legend annotations: {exc}")
        return
    if not legends:
        _print_once("legend", "WARNING: no legend/colorbar annotation found to reposition.")
        return

    # Prefer the legend tied to this variable (VARCOMP back-reference);
    # otherwise fall back to the first legend.
    target = None
    for legend in legends:
        try:
            varcomp = legend.VARCOMP
            if varcomp and _normalize(varcomp[0][0].DESCRIPTION) == _normalize(var_desc):
                target = legend
                break
        except Exception:
            continue
    if target is None:
        target = legends[0]

    width = float(cfg["colorbar_width_fraction"])
    layout = {
        "WIDTH": width,
        "HEIGHT": 0.70,
        "LOCATIONX": 0.98 - width,  # flush to the right edge, outside the slice
        "LOCATIONY": 0.15,
    }
    failures = []
    for attr, value in layout.items():
        try:
            setattr(target, attr, value)
        except Exception as exc:
            failures.append(f"{attr}: {exc}")
    if failures:
        _print_once("legend", "WARNING: colorbar placement partly failed: "
                              + "; ".join(failures))
    else:
        _print_once("legend", f"  Slice colorbar: placed right-outside "
                              f"(x={layout['LOCATIONX']:.2f}, width={width}).")


# ---------------------------------------------------------------------------
# Q iso-surface presentation style (Q iso figures only — slices untouched)
# ---------------------------------------------------------------------------

def _try_scene_setting(label: str, func: Any) -> None:
    """Apply one scene/rendering setting; print applied/WARNING, never raise.
    EnSight builds differ, so every attribute is best-effort."""
    try:
        func()
        print(f"  Q iso scene: {label}: applied")
    except Exception as exc:
        print(f"WARNING: Q iso scene: {label} unavailable: {exc}")


def setup_qiso_scene(session: Any, cfg: dict) -> None:
    """Presentation cleanup for Q iso-surface figures: solid white background,
    no shadows/reflections/ground plane/grid/axes/bounding box. Global
    viewport state — called once, right before iso export (all slice figures
    are already written by then). A failed setting never blocks the export."""
    if not cfg["qiso_clean_scene"]:
        print("  Q iso scene: qiso_clean_scene=False — scene left as-is.")
        return
    try:
        ens = session.ensight
    except Exception as exc:
        print(f"WARNING: Q iso scene cleanup unavailable (no session API): {exc}")
        return

    # Master switch for EnSight's environment "scene" (floor + effects) —
    # belt-and-braces before the individual toggles below.
    _try_scene_setting("environment scene off", lambda: ens.scene.active("OFF"))

    if cfg["qiso_white_background"]:
        rgb = [float(c) for c in cfg["qiso_background_rgb"]]

        def _solid_background() -> None:
            vport = ens.objs.core.VPORTS[0]
            vport.BACKGROUNDTYPE = ens.objs.enums.VPORT_CONS  # solid (no gradient)
            vport.CONSTANTRGB = rgb

        _try_scene_setting(f"solid background rgb={rgb} (gradient off)",
                           _solid_background)

    if cfg["qiso_disable_shadows"]:
        _try_scene_setting("scene shadows off", lambda: ens.scene.shadow("OFF"))
        _try_scene_setting("ground-plane shadow off",
                           lambda: ens.scene.ground_plane_shadow("OFF"))

        def _lights_no_shadows() -> None:
            for light in ens.objs.core.LIGHTSOURCES:
                light.CASTS_SHADOWS = 0

        _try_scene_setting("light-source shadow casting off", _lights_no_shadows)

    if cfg["qiso_disable_reflections"]:
        _try_scene_setting("ground-plane reflections off",
                           lambda: ens.scene.ground_plane_reflection("OFF"))

    if cfg["qiso_disable_ground_plane"]:
        _try_scene_setting("ground plane off",
                           lambda: ens.scene.ground_plane_visible("OFF"))

    if cfg["qiso_hide_grid"]:
        _try_scene_setting("ground-plane grid off",
                           lambda: ens.scene.ground_plane_grid("OFF"))

    if cfg["qiso_hide_axes"]:
        _try_scene_setting("scene axes off", lambda: ens.scene.axis_visible("OFF"))

        def _global_axes_off() -> None:
            ens.objs.core.VPORTS[0].GLOBALAXISVISIBLE = 0
            ens.annotation.axis_global("off")

        _try_scene_setting("global axis triad off", _global_axes_off)

    if cfg["qiso_hide_bounding_box"]:
        _try_scene_setting("bounding box display off",
                           lambda: ens.view.bounds("OFF"))


def apply_qiso_part_style(session: Any, part: Any, cfg: dict) -> None:
    """Edge hiding + smooth shading for one Q iso-surface part."""
    enums = session.ensight.objs.enums

    if cfg["qiso_hide_edges"]:
        try:
            part.HIDDENLINE = 0
            _print_once("qiso_edges",
                        "  Q iso style: element/mesh edge overlay hidden (HIDDENLINE=0).")
        except Exception as exc:
            _print_once("qiso_edges",
                        f"WARNING: could not hide Q iso edges: {exc}")

    if cfg["qiso_smooth_shading"]:
        applied = None
        for enum_name in ("SHAD_SMOOTH_REFINED", "SHAD_SMOOTH", "SHAD_GOURAUD"):
            try:
                part.SHADING = getattr(enums, enum_name)
                applied = enum_name
                break
            except Exception:
                continue
        if applied:
            _print_once("qiso_shading",
                        f"  Q iso style: smooth shading applied (SHADING={applied}).")
        else:
            _print_once("qiso_shading",
                        "WARNING: Q iso smooth shading could not be applied; "
                        "using the part's default shading.")


def apply_qiso_colorbar_title(session: Any, var_desc: str, title: str) -> None:
    """Set the legend/colorbar title for the palette coloring the iso surface
    (legend command class: select_palette_begin / title / select_palette_end)."""
    try:
        legend = session.ensight.legend
        legend.select_palette_begin(var_desc)
        legend.title(title)
        legend.select_palette_end()
        _print_once("qiso_title",
                    f"  Q iso colorbar: title set to '{title}'.")
    except Exception as exc:
        _print_once("qiso_title",
                    f"WARNING: could not set Q iso colorbar title '{title}': {exc}")


# ---------------------------------------------------------------------------
# Clip / iso-surface part creation
# ---------------------------------------------------------------------------

def create_x_plane_part(
    session: Any,
    x_value: float,
    name: str,
    source_parts: List[Any],
    domain: str = "intersect",
) -> Optional[Any]:
    """Create a clip at x = x_value from source_parts.
    domain: 'intersect' -> 2-D yz slice; 'inside'/'outside' -> volume clip
    keeping one side of the plane."""
    ens = session.ensight
    enums = ens.objs.enums
    domain_enum = {
        "intersect": enums.CLIP_DOMAIN_INTER,
        "inside": enums.CLIP_DOMAIN_IN,
        "outside": enums.CLIP_DOMAIN_OUT,
    }[domain]
    try:
        clip_default = ens.objs.core.DEFAULTPARTS[ens.PART_CLIP_PLANE]
        clip_default.TOOL = enums.CT_XYZ
        clip_default.MESHPLANEXYZ = enums.MESH_SLICE_X
        clip_default.VALUEXYZ = float(x_value)
        clip_default.DOMAIN = domain_enum
        created = clip_default.createpart(name=name, sources=source_parts)
        return created[0] if created else None
    except Exception as exc:
        print(f"WARNING: clip '{name}' at x={x_value:.6g} failed: {exc}")
        return None


def create_z_plane_part(
    session: Any,
    z_value: float,
    name: str,
    source_parts: List[Any],
) -> Optional[Any]:
    ens = session.ensight
    enums = ens.objs.enums
    try:
        clip_default = ens.objs.core.DEFAULTPARTS[ens.PART_CLIP_PLANE]
        clip_default.TOOL = enums.CT_XYZ
        clip_default.MESHPLANEXYZ = enums.MESH_SLICE_Z
        clip_default.VALUEXYZ = float(z_value)
        clip_default.DOMAIN = enums.CLIP_DOMAIN_INTER
        created = clip_default.createpart(name=name, sources=source_parts)
        return created[0] if created else None
    except Exception as exc:
        print(f"WARNING: z-mid clip '{name}' at z={z_value:.6g} failed: {exc}")
        return None


def create_active_volume_parts(
    session: Any,
    active_x_min: float,
    active_x_max: float,
    fluid_parts: List[Any],
) -> List[Any]:
    """Best-effort volume restriction to active_x_min <= x <= active_x_max via
    two chained one-sided plane clips. Assumes CLIP_DOMAIN_IN keeps the +x
    side of an x-normal plane and CLIP_DOMAIN_OUT the -x side (side
    orientation is not documented; verify visually on the first real run).
    Returns [] if clipping is unavailable."""
    clip_lo = create_x_plane_part(
        session, active_x_min, "active_vol_xmin", fluid_parts, domain="inside",
    )
    if clip_lo is None:
        return []
    clip_hi = create_x_plane_part(
        session, active_x_max, "active_vol_xmax", [clip_lo], domain="outside",
    )
    if clip_hi is None:
        return []
    print(
        "Active-region volume clip created "
        f"({active_x_min:.6g} <= x <= {active_x_max:.6g}); side-orientation "
        "assumption (inside=+x) should be verified visually once."
    )
    return [clip_hi]


def create_iso_surface_part(
    session: Any,
    var_obj: Any,
    iso_value: float,
    name: str,
    source_parts: List[Any],
) -> Optional[Any]:
    ens = session.ensight
    try:
        iso_default = ens.objs.core.DEFAULTPARTS[ens.PART_ISO_SURFACE]
        iso_default.VARIABLE = var_obj
        iso_default.VALUE = float(iso_value)
        created = iso_default.createpart(name=name, sources=source_parts)
        return created[0] if created else None
    except Exception as exc:
        print(f"WARNING: iso-surface '{name}' at value={iso_value:.6g} failed: {exc}")
        return None


def resolve_qcriterion_thresholds(
    session: Any,
    cfg: dict,
    q_desc: str,
    active_volume_parts: List[Any],
) -> Tuple[List[float], str]:
    """Return (thresholds, source_label) for the Q iso-surface figures.
    Priority: qcriterion_threshold_list > qcriterion_threshold > automatic
    active-region Q max fraction > DEFAULT_Q_THRESHOLD_SWEEP. Never returns
    an empty list — Q iso export is no longer skipped outright."""
    raw_list = list(cfg.get("qcriterion_threshold_list") or [])
    if raw_list:
        thresholds: List[float] = []
        for raw in raw_list:
            try:
                thresholds.append(float(raw))
            except (TypeError, ValueError):
                print(f"WARNING: qcriterion_threshold_list entry {raw!r} is not "
                      "a number; ignored.")
        if thresholds:
            return thresholds, "config qcriterion_threshold_list"
        print("WARNING: qcriterion_threshold_list contained no usable numbers; "
              "falling through to the other threshold sources.")

    if cfg["qcriterion_threshold"] is not None:
        return [float(cfg["qcriterion_threshold"])], "config qcriterion_threshold"

    if str(cfg["qcriterion_threshold_mode"]) == "auto_active_max_fraction":
        # Read max Q of the ACTIVE region (visible clipped volume), not of
        # the full domain including the buffers.
        q_max = None
        try:
            show_only_parts(session, active_volume_parts)
            color_part_by_variable(session, active_volume_parts[0], q_desc)
            q_max = read_palette_max(session, q_desc)
        except Exception as exc:
            print(f"WARNING: active-region Q max readback raised: {exc}")
        if q_max is not None and q_max > 0.0:
            threshold = float(cfg["qcriterion_auto_fraction"]) * q_max
            print(f"Q-criterion auto threshold: {cfg['qcriterion_auto_fraction']} "
                  f"* active-region max ({q_max:.6g}) = {threshold:.6g}")
            return [threshold], "auto_active_max_fraction"
        print("WARNING: Could not read Q max; exporting default threshold "
              "sweep instead.")
        return list(DEFAULT_Q_THRESHOLD_SWEEP), "default sweep (auto Q max unavailable)"

    print("WARNING: qcriterion_threshold_mode='manual' but no threshold or "
          "threshold list was given; exporting default threshold sweep instead.")
    return list(DEFAULT_Q_THRESHOLD_SWEEP), "default sweep (no manual threshold)"


# ---------------------------------------------------------------------------
# EnSight Calculator fallbacks for missing Q-criterion / vorticity
# ---------------------------------------------------------------------------

def find_velocity_vector_variable(session: Any) -> Tuple[Optional[Any], Optional[str]]:
    """Find the VECTOR velocity variable needed for component extraction.
    Prefers exact/normalized candidate names; falls back to any vector-typed
    variable whose name contains 'velocity'."""
    try:
        all_vars = [v for v in session.ensight.objs.core.VARIABLES if v.DESCRIPTION]
    except Exception:
        return None, None

    vec_enum = None
    try:
        vec_enum = session.ensight.objs.enums.ENS_VAR_VECTOR
    except Exception:
        pass

    def _is_vector(var: Any) -> Optional[bool]:
        """True/False when the type is readable, None when it is not."""
        if vec_enum is None:
            return None
        for attr in ("VARTYPEENUM", "VARTYPE"):
            try:
                return getattr(var, attr) == vec_enum
            except Exception:
                continue
        return None

    for cand in VELOCITY_VECTOR_CANDIDATES:
        cand_norm = _normalize(cand)
        for var in all_vars:
            if _normalize(var.DESCRIPTION) != cand_norm:
                continue
            is_vec = _is_vector(var)
            if is_vec is False:
                continue  # scalar of the same name (e.g. a magnitude) — skip
            if is_vec is None:
                print(f"WARNING: could not confirm '{var.DESCRIPTION}' is a "
                      "vector variable; using it for component extraction anyway.")
            return var, str(var.DESCRIPTION)

    for var in all_vars:
        if _is_vector(var) is True and "velocity" in _normalize(var.DESCRIPTION):
            return var, str(var.DESCRIPTION)
    return None, None


def _lookup_variable_by_name(session: Any, name: str) -> Optional[Any]:
    try:
        for var in session.ensight.objs.core.VARIABLES:
            if var.DESCRIPTION == name:
                return var
    except Exception:
        pass
    return None


def _get_or_create_calc_variable(
    session: Any,
    name: str,
    expression: str,
    source_parts: List[Any],
    step: str,
) -> Any:
    """Create Calculator variable `name = expression` over source_parts
    ('plist' in the expression refers to those parts). Reuses an existing
    variable with the same name. Raises RuntimeError naming the failed step."""
    existing = _lookup_variable_by_name(session, name)
    if existing is not None:
        print(f"  Calculator: reusing existing variable '{name}'.")
        return existing

    errors: List[str] = []
    try:
        var = session.ensight.objs.core.create_variable(
            name, expression, sources=source_parts,
        )
        if var is not None:
            return var
        errors.append("create_variable returned None")
    except Exception as exc:
        errors.append(f"create_variable: {exc}")

    # Fallback: select the parts, then evaluate the expression string
    try:
        session.ensight.utils.parts.select_parts(source_parts)
        session.ensight.variables.evaluate(f"{name} = {expression}")
        var = _lookup_variable_by_name(session, name)
        if var is not None:
            return var
        errors.append("variables.evaluate ran but variable not found afterwards")
    except Exception as exc:
        errors.append(f"variables.evaluate: {exc}")

    raise RuntimeError(
        f"Calculator step '{step}' ({name} = {expression}) failed: "
        + "; ".join(errors)
    )


def compute_qcriterion_variable(
    session: Any,
    source_parts: List[Any],
    cfg: dict,
) -> Tuple[Any, str]:
    """Compute Q-criterion from the velocity vector via the EnSight Calculator
    (documented pattern: components -> Grad -> Q_criteria). Raises RuntimeError
    naming the failed step."""
    vel_var, vel_desc = find_velocity_vector_variable(session)
    if vel_var is None:
        raise RuntimeError(
            "Calculator step 'find velocity vector' failed: no vector velocity "
            f"variable found (tried {VELOCITY_VECTOR_CANDIDATES})."
        )
    print(f"  Calculator: velocity vector variable = '{vel_desc}'")

    for comp in ("x", "y", "z"):
        _get_or_create_calc_variable(
            session, f"Vel_{comp}", f"{vel_desc}[{comp.upper()}]",
            source_parts, step=f"velocity component Vel_{comp}",
        )
    for comp in ("x", "y", "z"):
        _get_or_create_calc_variable(
            session, f"Grad_Vel_{comp}", f"Grad(plist,Vel_{comp})",
            source_parts, step=f"gradient Grad_Vel_{comp}",
        )

    q_name = str(cfg["qcriterion_variable_name"])
    q_var = _get_or_create_calc_variable(
        session, q_name,
        "Q_criteria(plist,Grad_Vel_x,Grad_Vel_y,Grad_Vel_z)",
        source_parts, step="Q_criteria",
    )
    print(f"  Calculator: Q-criterion computed as '{q_name}'.")
    return q_var, q_name


def compute_vorticity_variable(
    session: Any,
    source_parts: List[Any],
) -> Tuple[Any, str]:
    """Compute the vorticity VECTOR via Vort(plist, velocity). EnSight colors
    parts by a vector's magnitude, so it can stand in for vorticity magnitude.
    Raises RuntimeError naming the failed step."""
    vel_var, vel_desc = find_velocity_vector_variable(session)
    if vel_var is None:
        raise RuntimeError(
            "Calculator step 'find velocity vector' failed: no vector velocity "
            f"variable found (tried {VELOCITY_VECTOR_CANDIDATES})."
        )
    vort_var = _get_or_create_calc_variable(
        session, COMPUTED_VORTICITY_NAME, f"Vort(plist,{vel_desc})",
        source_parts, step="Vort",
    )
    print(f"  Calculator: vorticity vector computed as '{COMPUTED_VORTICITY_NAME}' "
          "(figures are colored by its magnitude).")
    return vort_var, COMPUTED_VORTICITY_NAME


# ---------------------------------------------------------------------------
# Session open (same 3-attempt loading strategy as the 03 script)
# ---------------------------------------------------------------------------

def open_case_in_pyensight(paths: dict) -> Tuple[Optional[Any], str]:
    if not _PYENSIGHT_AVAILABLE:
        return None, (
            "ansys.pyensight.core is not installed or could not be imported.\n"
            "  Install : pip install ansys-pyensight-core\n"
            f"  Error   : {_PYENSIGHT_IMPORT_ERROR}"
        )

    from ansys.pyensight.core import LocalLauncher

    case_str = str(paths["final_case_file"]).replace("\\", "/")
    data_str = str(paths["final_data_file"]).replace("\\", "/")

    session = None
    try:
        print("Launching PyEnSight (batch/headless mode)...")
        session = LocalLauncher().start()
        print("PyEnSight session started.")
        print(f"Loading case : {case_str}")
        print(f"Loading data : {data_str}")

        errors: List[str] = []
        for label, kwargs in (
            ("dual-file via load_data", {"result_file": data_str}),
            ("case file only", {}),
            ("explicit Fluent reader", {"result_file": data_str, "file_format": "Fluent Fluent"}),
        ):
            try:
                session.load_data(case_str, **kwargs)
                print(f"Case loaded ({label}).")
                return session, ""
            except Exception as exc:
                errors.append(f"{label}: {exc}")

        return session, "All load attempts failed:\n  " + "\n  ".join(errors)
    except Exception as exc:
        return session, f"Failed to start PyEnSight or load case: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Dry-run plan report
# ---------------------------------------------------------------------------

def print_plan(cfg: dict, paths: dict) -> None:
    fractions = [float(f) for f in cfg["slice_x_fractions"]]
    slice_fields = requested_slice_fields(cfg)
    manual = manual_extents(cfg)

    print("=" * 76)
    print("08_pyensight_extra_figures — plan")
    print("=" * 76)
    print(f"geo_name / case_name : {cfg['geo_name']} / {cfg['case_name']}")
    print(f"dry_run              : {cfg['dry_run']}")
    print(f"Input case file      : {paths['final_case_file']}"
          f"  [{'exists' if paths['final_case_file'].is_file() else 'MISSING'}]")
    print(f"Input data file      : {paths['final_data_file']}"
          f"  [{'exists' if paths['final_data_file'].is_file() else 'MISSING'}]")
    print(f"Output (slices)      : {paths['slices_dir']}")
    print(f"Output (vortex)      : {paths['vortex_dir']}")
    print(f"Image size           : {cfg['image_width']} x {cfg['image_height']}")

    print("\nSlice rendering quality (yz slice figures only):")
    print(f"  smooth_slice_rendering : {cfg['smooth_slice_rendering']}"
          "  (smooth shading + nodal ElemToNode display of cell-centered variables)")
    print(f"  hide_slice_edges       : {cfg['hide_slice_edges']}")
    print(f"  contour_level_count    : {cfg['contour_level_count']}")
    print(f"  use_continuous_palette : {cfg['use_continuous_palette']}")
    print(f"  colorbar_position      : {cfg['colorbar_position']} "
          f"(width fraction {cfg['colorbar_width_fraction']})")
    print(f"  camera_fit_margin      : {cfg['camera_fit_margin']}")

    print("\nActive membrane region:")
    print(f"  active membrane part candidates : {cfg['active_membrane_part_candidates']}")
    print(f"  buffer part candidates (EXCLUDED): {cfg['buffer_part_candidates']}")
    if manual is not None:
        print(f"  extents mode : MANUAL  active_x_min={manual[0]:.6g}, active_x_max={manual[1]:.6g}")
    else:
        print("  extents mode : AUTO (detected from active membrane wall parts;"
              " errors out rather than falling back to full domain)")

    print(f"\nyz slice fractions of active x-range: {fractions}")
    if manual is not None:
        length = manual[1] - manual[0]
        print("Planned slice x positions [m]:")
        for f in fractions:
            print(f"  active x/L = {f:.3f}  ->  x = {manual[0] + f * length:.8g}")
    else:
        print("Planned slice x positions: computed after auto-detection at run time.")

    print("\nRequested slice fields:")
    for key in ("concentration", "velocity_mag", "x_velocity", "vorticity_mag"):
        included = key in slice_fields
        print(f"  {FIELD_DISPLAY_LABELS[key]:24s}: {'yes' if included else 'no (disabled)'}")
        if included:
            for f in fractions:
                print(f"    slices/yz_active_{_frac_tag(f)}_{SLICE_FILE_SUFFIX[key]}.png")

    print("\nRequested vortex figures:")
    if cfg["include_qcriterion"]:
        thr_list = list(cfg.get("qcriterion_threshold_list") or [])
        if thr_list:
            print("  Q iso thresholds (qcriterion_threshold_list) — one figure each:")
            for t in thr_list:
                try:
                    print("    vortex/qcriterion_iso_velocity_colored_thr_"
                          f"{_threshold_tag(float(t))}.png")
                except (TypeError, ValueError):
                    print(f"    (invalid threshold entry {t!r} — will be ignored)")
        elif cfg["qcriterion_threshold"] is not None:
            print("  vortex/qcriterion_iso_velocity_colored_thr_"
                  f"{_threshold_tag(float(cfg['qcriterion_threshold']))}.png "
                  "(explicit qcriterion_threshold)")
        else:
            print(f"  Q iso threshold: mode={cfg['qcriterion_threshold_mode']}; "
                  "if the active-region Q max cannot be read, a default sweep "
                  f"{[f'{t:g}' for t in DEFAULT_Q_THRESHOLD_SWEEP]} is exported "
                  "as ..._thr_<value>.png")
        print("  vortex/qcriterion_iso_velocity_colored.png "
              "(generic copy of the first successful threshold)")
        print("  vortex/qcriterion_yz_active_x050.png")
        if cfg["qiso_color_by_velocity"]:
            print("  Q iso rendering: surface colored by VELOCITY MAGNITUDE, "
                  f"colorbar title '{cfg['qiso_colorbar_title']}'")
        else:
            print("  Q iso rendering: surface colored by Q-CRITERION "
                  "(qiso_color_by_velocity=False), colorbar title "
                  "'Q-criterion [1/s^2]'")
        print(f"  Q iso clean scene: {cfg['qiso_clean_scene']} "
              f"(white_bg={cfg['qiso_white_background']}, "
              f"no_shadows={cfg['qiso_disable_shadows']}, "
              f"no_reflections={cfg['qiso_disable_reflections']}, "
              f"no_ground_plane={cfg['qiso_disable_ground_plane']}, "
              f"no_axes={cfg['qiso_hide_axes']}, no_grid={cfg['qiso_hide_grid']}, "
              f"no_bbox={cfg['qiso_hide_bounding_box']})")
        print(f"  Q iso part style: smooth_shading={cfg['qiso_smooth_shading']}, "
              f"hide_edges={cfg['qiso_hide_edges']}, "
              f"fit_margin={cfg['qiso_camera_fit_margin']}")
        print("  Q-criterion variable strategy (real run only):")
        print("    1) search existing result variables "
              f"({', '.join(FIELD_VAR_CANDIDATES['qcriterion'][:5])}, ...)")
        if cfg["compute_qcriterion_if_missing"]:
            print("    2) if missing: compute from the velocity vector via the "
                  "EnSight Calculator")
            print("       (Vel_x=Velocity[X] ... Grad(plist,Vel_x) ... "
                  f"{cfg['qcriterion_variable_name']}=Q_criteria(plist,...)),")
            print("       on the active-region clipped volume (full domain "
                  "only as a loudly-warned last resort)")
        else:
            print("    2) compute-if-missing disabled: missing Q skips Q figures")
        print(f"    strict_qcriterion_required: {cfg['strict_qcriterion_required']}")
    else:
        print("  Q-criterion figures: no (disabled)")
    if cfg["include_vorticity"]:
        print("  vortex/vorticity_mag_active_mid.png")
        if cfg["compute_vorticity_if_missing"]:
            print("    (if no vorticity variable exists, "
                  f"{COMPUTED_VORTICITY_NAME}=Vort(plist,Velocity) is computed "
                  "on the active clip during the real run)")
    else:
        print("  vorticity mid-plane figure: no (disabled)")

    if cfg["dry_run"]:
        print("\nDRY RUN: no EnSight launch, no folders or files created.")
        print("Set dry_run=False (or pass --run) to export.")
    print("=" * 76)


# ---------------------------------------------------------------------------
# Real export run
# ---------------------------------------------------------------------------

def run_export(cfg: dict, paths: dict) -> int:
    if not paths["final_case_file"].is_file():
        print(f"ERROR: case file not found: {paths['final_case_file']}")
        return 1
    if not paths["final_data_file"].is_file():
        print(f"ERROR: data file not found: {paths['final_data_file']}")
        return 1

    session, err = open_case_in_pyensight(paths)
    if err:
        print(f"ERROR: {err}")
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        return 1

    exported: List[Path] = []
    try:
        print_variable_inventory(session)

        # --- variable detection ---------------------------------------
        slice_fields = requested_slice_fields(cfg)
        wanted_keys = list(slice_fields)
        if cfg["include_qcriterion"]:
            wanted_keys.append("qcriterion")
            if "velocity_mag" not in wanted_keys:
                wanted_keys.append("velocity_mag")  # iso-surface coloring

        found_vars: dict = {}
        print("\nVariable matching:")
        for key in wanted_keys:
            var_obj, matched = find_variable(session, FIELD_VAR_CANDIDATES[key])
            if var_obj is None:
                if key == "qcriterion" and cfg["compute_qcriterion_if_missing"]:
                    note = "NOT FOUND — will try to compute it from velocity."
                elif key == "vorticity_mag" and cfg["compute_vorticity_if_missing"]:
                    note = ("NOT FOUND — yz slices skipped; z-mid figure will "
                            "try a computed fallback.")
                else:
                    note = "NOT FOUND — its figures will be skipped."
                print(f"  {FIELD_DISPLAY_LABELS[key]:24s}: {note}")
            else:
                found_vars[key] = (var_obj, matched)
                print(f"  {FIELD_DISPLAY_LABELS[key]:24s}: '{matched}'")

        available_slice_fields = [k for k in slice_fields if k in found_vars]
        q_available = "qcriterion" in found_vars
        q_may_be_computed = (
            cfg["include_qcriterion"]
            and not q_available
            and cfg["compute_qcriterion_if_missing"]
        )
        if not found_vars and not q_may_be_computed:
            print("ERROR: none of the requested variables were found in this case.")
            return 1

        # --- parts + active x-range ------------------------------------
        fluid_parts = find_fluid_volume_parts(session)
        if not fluid_parts:
            print("ERROR: no 3-D fluid volume parts found; cannot create slices.")
            return 1
        print(f"\nFluid volume parts: {[str(p.DESCRIPTION) for p in fluid_parts]}")

        manual = manual_extents(cfg)
        if manual is not None:
            active_x_min, active_x_max = manual
            print(f"Active x-range (MANUAL): [{active_x_min:.8g}, {active_x_max:.8g}] m")
        else:
            try:
                active_x_min, active_x_max = detect_active_x_range(session, cfg)
            except RuntimeError as exc:
                print(f"ERROR: {exc}")
                return 1
            print(f"Active x-range (AUTO): [{active_x_min:.8g}, {active_x_max:.8g}] m")

        active_len = active_x_max - active_x_min
        fractions = [float(f) for f in cfg["slice_x_fractions"]]
        bad = [f for f in fractions if not 0.0 <= f <= 1.0]
        if bad:
            print(f"ERROR: slice_x_fractions outside [0, 1] would leave the "
                  f"active membrane region: {bad}")
            return 1
        slice_positions = [(f, active_x_min + f * active_len) for f in fractions]
        print("Slice positions (all inside the active membrane region):")
        for f, x in slice_positions:
            print(f"  active x/L = {f:.3f}  ->  x = {x:.8g} m")

        # --- output folders (only now — never in dry run) ---------------
        for d in (paths["slices_dir"], paths["vortex_dir"]):
            if not _safe_mkdir(d):
                print(f"ERROR: refusing to create unsafe output path: {d}")
                return 1

        setup_clean_scene(session)

        # --- 1. yz slices ----------------------------------------------
        # Slice-only visual quality: nodal display variables are resolved once
        # per field (cell-centered Fluent variables render as flat per-cell
        # patches; ElemToNode gives a continuous interpolated field). Computed
        # on the parent fluid parts so every slice position inherits them.
        slice_display_desc: dict = {}
        if available_slice_fields:
            print("\nSlice rendering quality "
                  f"(smooth={cfg['smooth_slice_rendering']}, "
                  f"hide_edges={cfg['hide_slice_edges']}, "
                  f"levels={cfg['contour_level_count']}, "
                  f"continuous={cfg['use_continuous_palette']}, "
                  f"colorbar={cfg['colorbar_position']}, "
                  f"fit_margin={cfg['camera_fit_margin']}):")
            for key in available_slice_fields:
                var_obj, var_desc = found_vars[key]
                slice_display_desc[key] = resolve_slice_display_variable(
                    session, var_obj, var_desc, fluid_parts, cfg,
                )

        for f, x_pos in slice_positions:
            if not available_slice_fields:
                break
            tag = _frac_tag(f)
            slice_part = create_x_plane_part(
                session, x_pos, f"yz_active_{tag}", fluid_parts, domain="intersect",
            )
            if slice_part is None:
                print(f"WARNING: skipping all figures at active x/L={f:.3f} (clip failed).")
                continue
            apply_slice_part_style(session, slice_part, cfg)
            for key in available_slice_fields:
                _var_obj, var_desc = found_vars[key]
                display_desc = slice_display_desc.get(key, var_desc)
                out_file = paths["slices_dir"] / f"yz_active_{tag}_{SLICE_FILE_SUFFIX[key]}.png"
                show_only_parts(session, [slice_part])
                color_part_by_variable(session, slice_part, display_desc)
                tune_slice_palette(session, display_desc, cfg)
                position_slice_colorbar(session, display_desc, cfg)
                set_view(session, (1.0, 0.0, 0.0), up_axis=(0.0, 0.0, 1.0),
                         fit_margin=float(cfg["camera_fit_margin"]))
                if export_png(session, out_file, cfg):
                    exported.append(out_file)

        # --- 2. vortex figures ------------------------------------------
        active_volume_parts: List[Any] = []
        if cfg["include_qcriterion"] or cfg["include_vorticity"]:
            active_volume_parts = create_active_volume_parts(
                session, active_x_min, active_x_max, fluid_parts,
            )
            if not active_volume_parts:
                print(
                    "WARNING: full-volume clipping to the active x-range is "
                    "unavailable in this PyEnSight session. Volume-based vortex "
                    "figures (Q iso-surface, z-mid vorticity plane) will be "
                    "skipped; only active-region yz slices are produced."
                )

        # 2a-0. Calculator fallback: compute Q-criterion from velocity if the
        # results contain no Q variable. Computed over the active-region clip
        # when available; the full domain (incl. buffers) is used ONLY as a
        # last resort and never silently.
        q_slice_sources = fluid_parts  # sources for the mid yz Q slice
        if (
            cfg["include_qcriterion"]
            and not q_available
            and cfg["compute_qcriterion_if_missing"]
        ):
            if active_volume_parts:
                q_compute_sources = active_volume_parts
                q_slice_sources = active_volume_parts
                print("\nQ-criterion not in results — computing it via the "
                      "EnSight Calculator on the ACTIVE-REGION clipped volume:")
            else:
                q_compute_sources = fluid_parts
                print("\nQ-criterion not in results — computing it via the "
                      "EnSight Calculator.")
                print("WARNING: active-region volume clip unavailable, so Q is "
                      "computed over the FULL fluid domain INCLUDING the "
                      "inlet/outlet buffers. Figures still show only the "
                      "active x-range, but check the color range.")
            try:
                q_var, q_desc = compute_qcriterion_variable(
                    session, q_compute_sources, cfg,
                )
                found_vars["qcriterion"] = (q_var, q_desc)
                q_available = True
            except RuntimeError as exc:
                print(f"WARNING: Q-criterion computation failed — {exc}")
                print("WARNING: only the Q-criterion figures are skipped; other "
                      "figures are unaffected.")

        if cfg["include_qcriterion"] and not q_available:
            if cfg["strict_qcriterion_required"]:
                print("ERROR: Q-criterion is unavailable (not in results and "
                      "not computable) and strict_qcriterion_required=True.")
                return 1
            print("WARNING: Q-criterion variable not found; Q figures skipped.")

        # 2a. Q-criterion on the mid yz slice (needs no volume clip)
        if cfg["include_qcriterion"] and q_available:
            x_mid = active_x_min + 0.5 * active_len
            mid_slice = create_x_plane_part(
                session, x_mid, "qcrit_yz_active_x050", q_slice_sources,
                domain="intersect",
            )
            if mid_slice is not None:
                _q_obj, q_desc = found_vars["qcriterion"]
                out_file = paths["vortex_dir"] / "qcriterion_yz_active_x050.png"
                show_only_parts(session, [mid_slice])
                color_part_by_variable(session, mid_slice, q_desc)
                set_view(session, (1.0, 0.0, 0.0), up_axis=(0.0, 0.0, 1.0))
                if export_png(session, out_file, cfg):
                    exported.append(out_file)

        # 2b-0. Calculator fallback for vorticity. Only computed when the
        # active-region clip exists, because (a) the only consumer without a
        # dataset vorticity variable is the z-mid figure built from that clip,
        # and (b) computing on the clip keeps the buffers out. The yz vorticity
        # slices intentionally still require a dataset variable (unchanged).
        if (
            cfg["include_vorticity"]
            and "vorticity_mag" not in found_vars
            and cfg["compute_vorticity_if_missing"]
            and active_volume_parts
        ):
            print("\nVorticity not in results — computing it via the EnSight "
                  "Calculator on the ACTIVE-REGION clipped volume "
                  "(used for the z-mid vortex figure only; yz vorticity "
                  "slices remain skipped):")
            try:
                v_var, v_desc = compute_vorticity_variable(
                    session, active_volume_parts,
                )
                found_vars["vorticity_mag"] = (v_var, v_desc)
            except RuntimeError as exc:
                print(f"WARNING: vorticity computation failed — {exc}")
                print("WARNING: only the vorticity mid-plane figure is skipped.")

        # 2b. vorticity magnitude on the channel z-mid plane, active region only
        if cfg["include_vorticity"]:
            if "vorticity_mag" not in found_vars:
                print("WARNING: vorticity variable not found; vorticity_mag_active_mid skipped.")
            elif active_volume_parts:
                z_mid = None
                bounds = get_part_extents(active_volume_parts[0])
                if bounds is None:
                    for p in fluid_parts:
                        bounds = get_part_extents(p)
                        if bounds is not None:
                            break
                if bounds is not None and bounds[4] < bounds[5]:
                    z_mid = 0.5 * (bounds[4] + bounds[5])
                if z_mid is None:
                    print("WARNING: channel z-mid could not be determined; "
                          "vorticity_mag_active_mid skipped.")
                else:
                    mid_plane = create_z_plane_part(
                        session, z_mid, "vorticity_active_zmid", active_volume_parts,
                    )
                    if mid_plane is not None:
                        _v_obj, v_desc = found_vars["vorticity_mag"]
                        out_file = paths["vortex_dir"] / "vorticity_mag_active_mid.png"
                        show_only_parts(session, [mid_plane])
                        color_part_by_variable(session, mid_plane, v_desc)
                        set_view(session, (0.0, 0.0, 1.0), up_axis=(0.0, 1.0, 0.0))
                        if export_png(session, out_file, cfg):
                            exported.append(out_file)

        # 2c. Q-criterion iso-surface(s) colored by velocity magnitude — one
        # figure per resolved threshold; a failed threshold never stops the
        # remaining ones or the rest of the script.
        q_iso_strict_failure = False
        if cfg["include_qcriterion"] and q_available and active_volume_parts:
            q_obj, q_desc = found_vars["qcriterion"]
            print(f"\nQ iso-surface export: Q variable = '{q_desc}'")

            # Surface = Q level set; COLOR = velocity magnitude by default,
            # so the colorbar title stays "Velocity [m/s]". Optionally color
            # by Q itself (qiso_color_by_velocity=False).
            color_desc: Optional[str] = None
            colorbar_title = ""
            if cfg["qiso_color_by_velocity"]:
                if "velocity_mag" in found_vars:
                    color_desc = found_vars["velocity_mag"][1]
                    colorbar_title = str(cfg["qiso_colorbar_title"])
                    print(f"Q iso coloring: velocity magnitude ('{color_desc}'), "
                          f"colorbar title '{colorbar_title}'.")
                else:
                    print("WARNING: velocity-magnitude variable not found; "
                          "Q iso-surface figures skipped.")
                    q_iso_strict_failure = True
            else:
                color_desc = q_desc
                colorbar_title = "Q-criterion [1/s^2]"
                print(f"Q iso coloring: Q-criterion itself ('{color_desc}'), "
                      f"colorbar title '{colorbar_title}' "
                      "(qiso_color_by_velocity=False).")

            if color_desc is not None:
                print("Q iso scene cleanup:")
                setup_qiso_scene(session, cfg)
                thresholds, thr_source = resolve_qcriterion_thresholds(
                    session, cfg, q_desc, active_volume_parts,
                )
                print(f"Q iso thresholds ({thr_source}): "
                      f"[{', '.join(f'{t:g}' for t in thresholds)}]")

                generic_file = paths["vortex_dir"] / "qcriterion_iso_velocity_colored.png"
                generic_written = False
                iso_exported_count = 0
                iso_skipped_existing = 0
                for threshold in thresholds:
                    tag = _threshold_tag(threshold)
                    thr_file = (paths["vortex_dir"]
                                / f"qcriterion_iso_velocity_colored_thr_{tag}.png")
                    if thr_file.is_file():
                        print(f"  threshold {threshold:g}: {thr_file.name} "
                              "already exists — not overwritten.")
                        iso_skipped_existing += 1
                        continue
                    print(f"  threshold {threshold:g} -> {thr_file.name}")
                    iso_part = create_iso_surface_part(
                        session, q_obj, float(threshold),
                        f"qcrit_iso_thr_{tag}", active_volume_parts,
                    )
                    if iso_part is None:
                        print(f"WARNING: iso-surface creation failed at "
                              f"threshold {threshold:g}; continuing with the next one.")
                        continue
                    if get_part_extents(iso_part) is None:
                        print(f"WARNING: iso-surface at threshold {threshold:g} "
                              "has no readable extents — it may be empty (no Q "
                              "above this value); exporting anyway.")
                    show_only_parts(session, [iso_part])
                    apply_qiso_part_style(session, iso_part, cfg)
                    color_part_by_variable(session, iso_part, color_desc)
                    apply_qiso_colorbar_title(session, color_desc, colorbar_title)
                    set_view(session, (1.0, 1.0, 1.0), up_axis=(0.0, 0.0, 1.0),
                             fit_margin=float(cfg["qiso_camera_fit_margin"]))
                    if export_png(session, thr_file, cfg):
                        exported.append(thr_file)
                        iso_exported_count += 1
                        if not generic_written:
                            # Keep the generic filename pointing at the first
                            # successful threshold of this run (overwrite OK).
                            try:
                                shutil.copyfile(thr_file, generic_file)
                                exported.append(generic_file)
                                print(f"Exported: {generic_file} "
                                      f"(copy of {thr_file.name})")
                                generic_written = True
                            except OSError as exc:
                                print("WARNING: could not update generic iso "
                                      f"filename: {exc}")
                    else:
                        print(f"WARNING: export failed at threshold "
                              f"{threshold:g}; continuing with the next one.")

                if iso_exported_count == 0 and iso_skipped_existing == 0:
                    print("WARNING: no Q iso-surface figure could be exported "
                          "at any threshold (see warnings above).")
                    q_iso_strict_failure = True

        # --- summary ------------------------------------------------------
        print("\n" + "=" * 76)
        print(f"Exported {len(exported)} figure(s):")
        for p in exported:
            print(f"  {p}")
        if not exported:
            print("  (none — see warnings above)")
        print("=" * 76)
        if q_iso_strict_failure and cfg["strict_qcriterion_required"]:
            print("ERROR: Q iso-surface export failed and "
                  "strict_qcriterion_required=True.")
            return 1
        return 0 if exported else 1
    finally:
        try:
            session.close()
            print("PyEnSight session closed.")
        except Exception as exc:
            print(f"Warning: session.close() raised: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    try:
        cfg = build_config(args)
        manual_extents(cfg)  # validate early (raises on min >= max)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    paths = build_paths(cfg)
    print_plan(cfg, paths)

    if cfg["dry_run"]:
        return 0
    return run_export(cfg, paths)


if __name__ == "__main__":
    raise SystemExit(main())
