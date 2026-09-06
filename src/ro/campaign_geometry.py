"""Campaign geometry parameters keyed by geo_id (manifest migration source).

All lengths in metres. Values are design nominals — not parsed from geo_id
tokens beyond using geo_id as a lookup key.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ro.campaign_geo_ids import CAMPAIGN_GEO_IDS

# Channel height (invariant for Sigma_d / membrane_trim checks).
CAMPAIGN_H_M = 0.00077

_FILAMENT_D_M = 4.0e-4
_DIAMOND_BRIDGE_RADIUS_M = 1.10e-4

# Per-family independent stacked-filament height. Trim is derived:
#   membrane_trim_m = (Sigma_d_nominal_m - h) / 2
# Diamond / ML / Sin: 0.800 mm. Pillar: None (flat ends, trim = 0).
_STACKED_SIGMA_D_NOMINAL_M = 0.00080
_STACKED_MEMBRANE_TRIM_M = (_STACKED_SIGMA_D_NOMINAL_M - CAMPAIGN_H_M) / 2.0

_SINUSOIDAL_WAVE_RADIUS_M = 4.0e-4

_DIAMOND_SPACER_WALL_ZONES = ("wall_spacer",)
# CAD named selections (same as batch_config wall_spacer_labels), not the
# design-discussion guesses (layer_top/mid/bot/node) that never shipped.
_ML_SPACER_WALL_ZONES = (
    "wall_spacer_top",
    "wall_spacer_mid",
    "wall_spacer_bottom",
    "wall_spacer_bridge",
    "wall_spacer_buffer",
)
_PILLAR_SPACER_WALL_ZONES_BASE = (
    "wall_spacer_filament",
    "wall_spacer_pillar",
)
# Appended after optional wall_spacer_hole so h00 never declares the bore zone.
_PILLAR_SPACER_WALL_BUFFER = "wall_spacer_buffer"
# CAD named selections — not design-discussion guesses (wave/rung) that never
# shipped. wall_spacer_buffer is the buffer-cut face, same as ML and Pillar.
_SIN_SPACER_WALL_ZONES = (
    "wall_spacer_axial",
    "wall_spacer_bridge",
    "wall_spacer_buffer",
)

# (n_active_cells, pitch_mm, periodic_dy_mm, attack_angle_deg)
_DIAMOND_LAYOUTS: dict[str, tuple[int, float, float, int]] = {
    "D2450_a45": (7, 3.465, 3.465, 45),
    "D0817_a45": (21, 1.155, 1.155, 45),
    "D2450_a60": (5, 4.900249992, 2.8291606522, 60),
    "D2450_a30": (9, 2.8291606522, 4.900249992, 30),
    "D1225_a30": (18, 1.4145803261, 2.450124996, 30),
    "D1225_a45": (14, 1.7325, 1.7325, 45),
    "D1225_a60": (10, 2.450124996, 1.4145803261, 60),
    "D0817_a30": (27, 0.9430535507, 1.633416664, 30),
    "D0817_a60": (15, 1.633416664, 0.9430535507, 60),
}

_ML_LAYER_ANGLES_DEG = (45.0, 90.0, -45.0)
# LMH uses membrane_blocked_area_frac on a nominal-area basis campaign-wide
# (effective_area = nominal * (1 - frac)). Non-zero values would put one
# family on a different denominator and inflate LMH relative to the rest;
# changing the consumed field would also invalidate D2450_a45 verified
# references (u_mean 0.1992807169514518, inlet_profile_G 1.00360939613).
# Real geometry lives in membrane_blocked_area_frac_geometric (unused).
_MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED = 0.0
_ML_COMMON = {
    "Sigma_d_nominal_m": _STACKED_SIGMA_D_NOMINAL_M,
    "membrane_trim_m": _STACKED_MEMBRANE_TRIM_M,
    "periodic_shift_y_m": 0.003465,
    "periodic_shift_y_source": "explicit",
    "joint_sphere_count": 2,
    "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
    "n_active_cells": 7,
    "cell_length_x_m": 0.003465,
}

# Per-case ML geometry (all lengths in metres; diameters not radii).
# layer_axis_z_m / joint_sphere_z_m are derived in the campaign frame
# (inlet-face-centre origin: mid-plane z = 0, membranes at +/- h/2).
#
# Joint-sphere radii (CAD rebuilt to match). The sphere fills the cusp where
# the middle filament (90 deg) meets an outer filament (+/-45 deg). Size is
# set by how far that cusp extends, not by a ratio to any single filament
# radius. Near tangency the gap is a paraboloid with shallow principal radius
#
#     1/R_s = [ (1/r_mid + 1/r_out) - sqrt(1/r_mid^2 + 1/r_out^2) ] / 2
#
# for a 45 deg crossing, giving R_s = 0.4189 / 0.4552 / 0.5236 mm for
# c160 / c267 / c400 — only 25% variation while r_mid varies 2.5x. Covering
# the region where the gap is below threshold g needs R = sqrt(2 R_s g).
# Upper bound: sphere centre on the cylinder surface, so at the extreme of
# the sphere-cylinder intersection the normals meet at cos(theta) = R/(2 r);
# theta below 45 deg makes a sliver, capping R <= 1.414 * r_min
# (0.1131 / 0.1886 / 0.1414 mm for c160 / c267 / c400).
#
# g = 12 um (twice m_min) is the largest threshold that satisfies both bounds
# on all three: required R = 0.1003 / 0.1045 / 0.1121 mm. At g = 18 um c160
# would need 0.1228 and exceed its cap. Rounded values 0.100 / 0.105 / 0.110
# span only 10%, so contact treatment is effectively identical across a 2.5x
# change in layer thickness. R > r_min on c160 and c400 is intentional:
# spheres and filaments are all subtracted from a solid box, so a sphere
# reaching past the middle-layer surface only changes the outer fluid
# boundary. joint_sphere_R_ratio is R / r_min exactly (rule 3-6).
#
# M_c400 has a second upper bound beyond the 45-degree intersection angle:
# the sphere must not protrude far enough past the outer filament
# (R/r_outer = 1.10 here; c160/c267 are 0.625/0.787) to disturb periodic
# edge pairing. R = 0.112 failed Set Up Periodic Boundaries twice after
# surface meshing (unpaired curve-network after shadow-zone copy);
# R = 0.110 builds. Bound is empirical from those two points — no
# derivation — revisit if mesh settings change. R = 0.110 back-solves to
# g = 0.110^2 / (2 * 0.5236) = 11.55 um (1 um short of the 12 um target).
#
# Diamond stays at R = 0.110 mm (90 deg equal 0.200 radii => R_s = 0.200 mm,
# g = 30 um). Leave it: nine meshes pass; the brg156 trial (g = 61 um) made
# ortho/AR worse; remeshing would invalidate D2450_a45 verified references.
_ML_GEOMETRY: dict[str, dict[str, Any]] = {
    "M_c160": {
        "layer_diameters_m": [0.000320, 0.000160, 0.000320],
        "joint_sphere_R_m": 0.000100,
        "joint_sphere_r_min_m": 0.000080,
        "joint_sphere_R_ratio": 0.000100 / 0.000080,
    },
    "M_c267": {
        "layer_diameters_m": [0.000266670, 0.000266670, 0.000266670],
        "joint_sphere_R_m": 0.000105,
        "joint_sphere_r_min_m": 0.000133335,
        "joint_sphere_R_ratio": 0.000105 / 0.000133335,
    },
    "M_c400": {
        "layer_diameters_m": [0.000200, 0.000400, 0.000200],
        "joint_sphere_R_m": 0.000110,
        "joint_sphere_r_min_m": 0.000100,
        "joint_sphere_R_ratio": 0.000110 / 0.000100,
    },
}

# Pillar unit cell matches ML / Sinusoidal / CAD: 3.465 x 3.465 mm (not 4*D_p).
# has_hole_zone only — geometric blockage is keyed by D_p below.
_PILLAR_UNIT_CELL_M = 0.003465
# Pillar footprint / node area (pitch 2450 @ 45 deg => 6.0025 mm^2 = L^2/2).
# Not consumed by LMH; see _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED.
_PILLAR_BLOCKED_GEOMETRIC = {
    "P_p60": 0.047,
    "P_p80": 0.084,
    "P_p100": 0.131,
}
# Bore axis {0, 0.15, 0.30} mm (geo tokens h00 / h15 / h30). h20 was retired:
# on D_p=0.80 the filament–bore arc gap on the pillar surface is only 3.65 um,
# below m_min, and four mesh attempts failed surface skew. See batch_config
# Pillar notes for the gap table and the D_p=0.60 "hole-pillar" caveat.
_PILLAR_HAS_HOLE: dict[str, bool] = {
    "P_p60_h00": False,
    "P_p60_h15": True,
    "P_p60_h30": True,
    "P_p80_h00": False,
    "P_p80_h15": True,
    "P_p80_h30": True,
    "P_p100_h00": False,
    "P_p100_h15": True,
    "P_p100_h30": True,
    "P_p80_h00_f320": False,
    "P_p80_h15_f320": True,
}


def membrane_contact_width_m(
    filament_d_m: float,
    membrane_trim_m: float,
) -> float:
    """Flat contact-band width where the membrane cuts into a cylinder.

    w = 2 * sqrt(d * t - t**2) with d = contact filament diameter and
    t = membrane_trim_m.
    """
    d = float(filament_d_m)
    t = float(membrane_trim_m)
    if not math.isfinite(d) or d <= 0.0:
        raise ValueError(f"filament_d_m must be positive and finite, got {filament_d_m!r}.")
    if not math.isfinite(t) or t < 0.0:
        raise ValueError(
            f"membrane_trim_m must be finite and >= 0, got {membrane_trim_m!r}."
        )
    if t == 0.0:
        return 0.0
    if t > d:
        raise ValueError(
            f"membrane_trim_m={membrane_trim_m!r} exceeds filament_d_m={filament_d_m!r}."
        )
    return 2.0 * math.sqrt(d * t - t * t)


def _sinusoidal_wavelength_amplitude_m(geo_id: str) -> tuple[float, float]:
    if geo_id == "S_A000":
        return 0.003465, 0.0
    if geo_id == "S3465_A400_p2310":
        return 0.003465, 0.0004
    body = geo_id[1:]
    wavelength_token, amplitude_token = body.split("_A", 1)
    wavelength_m = int(wavelength_token) * 1.0e-6
    amplitude_m = int(amplitude_token) * 1.0e-6
    return wavelength_m, amplitude_m


def compute_curvature_margin(
    wavelength_m: float,
    amplitude_m: float,
    *,
    wave_radius_m: float = _SINUSOIDAL_WAVE_RADIUS_M,
) -> float | None:
    """Return lambda**2 / (4 pi**2 A r) or None when amplitude is zero."""
    if amplitude_m <= 0.0:
        return None
    if wavelength_m <= 0.0 or wave_radius_m <= 0.0:
        raise ValueError(
            "wavelength_m and wave_radius_m must be positive for curvature_margin."
        )
    return wavelength_m**2 / (4.0 * math.pi**2 * amplitude_m * wave_radius_m)


def _ml_layer_axis_and_joint_z_m(
    layer_diameters_m: list[float],
) -> tuple[list[float], list[float]]:
    """Campaign-frame axes and joint centres from stacked radii.

    Mid-plane at z = 0; outer axes at +/-(r_mid + r_outer); spheres at +/-r_mid.
    """
    r_top = float(layer_diameters_m[0]) / 2.0
    r_mid = float(layer_diameters_m[1]) / 2.0
    r_bot = float(layer_diameters_m[2]) / 2.0
    return [r_mid + r_top, 0.0, -(r_mid + r_bot)], [r_mid, -r_mid]


def _pillar_dp_token(geo_id: str) -> str:
    """P_p80_h15_f320 -> P_p80."""
    parts = geo_id.split("_")
    return f"{parts[0]}_{parts[1]}"


def _diamond_geometry_entry(geo_id: str) -> dict[str, Any]:
    n_active, pitch_mm, periodic_dy_mm, angle_deg = _DIAMOND_LAYOUTS[geo_id]
    periodic_shift_y_m = periodic_dy_mm * 1.0e-3
    cell_length_x_m = pitch_mm * 1.0e-3
    spacing_code = geo_id.split("_", 1)[0]
    return {
        "spacing_code": spacing_code,
        "attack_angle_deg": angle_deg,
        "filament_d_m": _FILAMENT_D_M,
        "bridge_radius_m": _DIAMOND_BRIDGE_RADIUS_M,
        "overlap_m": 0.0,
        "n_active_cells": n_active,
        "cell_length_x_m": cell_length_x_m,
        "Sigma_d_nominal_m": _STACKED_SIGMA_D_NOMINAL_M,
        "membrane_trim_m": _STACKED_MEMBRANE_TRIM_M,
        "membrane_contact_width_m": membrane_contact_width_m(
            _FILAMENT_D_M, _STACKED_MEMBRANE_TRIM_M
        ),
        "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
        # Contact-band area needs a defined filament path per cell; not derived yet.
        "membrane_blocked_area_frac_geometric": None,
        # Measured at mesh time from fluid volume / bounding box; not a registry constant.
        "porosity_eps": None,
        "periodic_shift_y_m": periodic_shift_y_m,
        "periodic_shift_y_source": "derived_from_angle",
        "layer_angles_deg": None,
        "layer_diameters_m": None,
        "layer_axis_z_m": None,
        "joint_sphere_z_m": None,
        "joint_sphere_R_m": None,
        "joint_sphere_R_ratio": None,
        "joint_sphere_r_min_m": None,
        "joint_sphere_count": 0,
        "curvature_margin": None,
        "spacer_wall_zones": list(_DIAMOND_SPACER_WALL_ZONES),
        "needs_lead_recheck": False,
    }


def _ml_geometry_entry(geo_id: str) -> dict[str, Any]:
    case = _ML_GEOMETRY[geo_id]
    layer_diameters_m = list(case["layer_diameters_m"])
    layer_axis_z_m, joint_sphere_z_m = _ml_layer_axis_and_joint_z_m(layer_diameters_m)
    middle_diameter_m = layer_diameters_m[1]
    # Outer layers contact the membrane.
    contact_diameter_m = layer_diameters_m[0]
    return {
        "spacing_code": geo_id,
        "attack_angle_deg": 0.0,
        "filament_d_m": middle_diameter_m,
        "bridge_radius_m": case["joint_sphere_R_m"],
        "overlap_m": 0.0,
        "n_active_cells": _ML_COMMON["n_active_cells"],
        "cell_length_x_m": _ML_COMMON["cell_length_x_m"],
        "Sigma_d_nominal_m": _ML_COMMON["Sigma_d_nominal_m"],
        "membrane_trim_m": _ML_COMMON["membrane_trim_m"],
        "membrane_contact_width_m": membrane_contact_width_m(
            contact_diameter_m, _ML_COMMON["membrane_trim_m"]
        ),
        "membrane_blocked_area_frac": _ML_COMMON["membrane_blocked_area_frac"],
        "membrane_blocked_area_frac_geometric": None,
        "porosity_eps": None,
        "periodic_shift_y_m": _ML_COMMON["periodic_shift_y_m"],
        "periodic_shift_y_source": _ML_COMMON["periodic_shift_y_source"],
        "layer_angles_deg": list(_ML_LAYER_ANGLES_DEG),
        "layer_diameters_m": layer_diameters_m,
        "layer_axis_z_m": layer_axis_z_m,
        "joint_sphere_z_m": joint_sphere_z_m,
        "joint_sphere_R_m": case["joint_sphere_R_m"],
        "joint_sphere_R_ratio": case["joint_sphere_R_ratio"],
        "joint_sphere_r_min_m": case["joint_sphere_r_min_m"],
        "joint_sphere_count": _ML_COMMON["joint_sphere_count"],
        "curvature_margin": None,
        "spacer_wall_zones": list(_ML_SPACER_WALL_ZONES),
        "needs_lead_recheck": False,
    }


def _pillar_geometry_entry(geo_id: str) -> dict[str, Any]:
    key = geo_id.split("_f320", 1)[0]
    has_hole = _PILLAR_HAS_HOLE[key if key in _PILLAR_HAS_HOLE else geo_id]
    blocked_geometric = _PILLAR_BLOCKED_GEOMETRIC[_pillar_dp_token(key)]
    zones = list(_PILLAR_SPACER_WALL_ZONES_BASE)
    if has_hole:
        zones.append("wall_spacer_hole")
    zones.append(_PILLAR_SPACER_WALL_BUFFER)
    return {
        "spacing_code": key,
        "attack_angle_deg": 0.0,
        "filament_d_m": _FILAMENT_D_M,
        "bridge_radius_m": 0.0,
        "overlap_m": 0.0,
        "n_active_cells": 7,
        "cell_length_x_m": _PILLAR_UNIT_CELL_M,
        "Sigma_d_nominal_m": None,
        "membrane_trim_m": 0.0,
        # Flat-ended pillars: cylindrical contact-band width is not applicable.
        "membrane_contact_width_m": None,
        "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
        "membrane_blocked_area_frac_geometric": blocked_geometric,
        "porosity_eps": None,
        "periodic_shift_y_m": _PILLAR_UNIT_CELL_M,
        "periodic_shift_y_source": "explicit",
        "layer_angles_deg": None,
        "layer_diameters_m": None,
        "layer_axis_z_m": None,
        "joint_sphere_z_m": None,
        "joint_sphere_R_m": None,
        "joint_sphere_R_ratio": None,
        "joint_sphere_r_min_m": None,
        "joint_sphere_count": 0,
        "curvature_margin": None,
        "spacer_wall_zones": zones,
        "needs_lead_recheck": True,
    }


def _sinusoidal_geometry_entry(geo_id: str) -> dict[str, Any]:
    wavelength_m, amplitude_m = _sinusoidal_wavelength_amplitude_m(geo_id)
    periodic_y = wavelength_m
    margin = compute_curvature_margin(wavelength_m, amplitude_m)
    n_active = 5 if geo_id != "S_A000" else 3
    filament_d_m = _SINUSOIDAL_WAVE_RADIUS_M * 2.0
    return {
        "spacing_code": geo_id.split("_", 1)[0],
        "attack_angle_deg": 0.0,
        "filament_d_m": filament_d_m,
        "bridge_radius_m": 0.0,
        "overlap_m": 0.0,
        "n_active_cells": n_active,
        "cell_length_x_m": wavelength_m,
        "Sigma_d_nominal_m": _STACKED_SIGMA_D_NOMINAL_M,
        "membrane_trim_m": _STACKED_MEMBRANE_TRIM_M,
        "membrane_contact_width_m": membrane_contact_width_m(
            filament_d_m, _STACKED_MEMBRANE_TRIM_M
        ),
        "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
        "membrane_blocked_area_frac_geometric": None,
        "porosity_eps": None,
        "periodic_shift_y_m": periodic_y,
        "periodic_shift_y_source": "explicit",
        "layer_angles_deg": None,
        "layer_diameters_m": None,
        "layer_axis_z_m": None,
        "joint_sphere_z_m": None,
        "joint_sphere_R_m": None,
        "joint_sphere_R_ratio": None,
        "joint_sphere_r_min_m": None,
        "joint_sphere_count": 0,
        "curvature_margin": margin,
        "spacer_wall_zones": list(_SIN_SPACER_WALL_ZONES),
        "needs_lead_recheck": False,
    }


def _reference_geometry_entry() -> dict[str, Any]:
    return {
        "spacing_code": "REF",
        "attack_angle_deg": 0.0,
        "filament_d_m": 0.0,
        "bridge_radius_m": 0.0,
        "overlap_m": 0.0,
        "n_active_cells": 3,
        "cell_length_x_m": 0.003465,
        "Sigma_d_nominal_m": CAMPAIGN_H_M,
        "membrane_trim_m": 0.0,
        "membrane_contact_width_m": 0.0,
        "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
        "membrane_blocked_area_frac_geometric": None,
        "porosity_eps": None,
        "periodic_shift_y_m": 0.003465,
        "periodic_shift_y_source": "explicit",
        "layer_angles_deg": None,
        "layer_diameters_m": None,
        "layer_axis_z_m": None,
        "joint_sphere_z_m": None,
        "joint_sphere_R_m": None,
        "joint_sphere_R_ratio": None,
        "joint_sphere_r_min_m": None,
        "joint_sphere_count": 0,
        "curvature_margin": None,
        "spacer_wall_zones": [],
        "needs_lead_recheck": False,
    }


def geometry_parameters_for_geo_id(geo_id: str) -> dict[str, Any]:
    """Return a deep-copyable geometry parameter dict for manifest migration."""
    if geo_id not in CAMPAIGN_GEO_IDS:
        raise ValueError(f"Unknown campaign geo_id: {geo_id!r}.")
    if geo_id in _DIAMOND_LAYOUTS:
        return _diamond_geometry_entry(geo_id)
    if geo_id in _ML_GEOMETRY:
        return _ml_geometry_entry(geo_id)
    if geo_id in _PILLAR_HAS_HOLE or geo_id.split("_f320", 1)[0] in _PILLAR_HAS_HOLE:
        return _pillar_geometry_entry(geo_id)
    if geo_id.startswith("S") or geo_id == "S_A000":
        return _sinusoidal_geometry_entry(geo_id)
    if geo_id == "REF_empty":
        return _reference_geometry_entry()
    raise ValueError(f"No geometry registry entry for geo_id {geo_id!r}.")


def merge_geometry_into_mesh_manifest(
    payload: Mapping[str, Any],
    geo_id: str,
) -> dict[str, Any]:
    """Add schema-v2 geometry fields without overwriting existing mesh knobs.

    Config-sourced knobs already on the payload win over the registry:
    spacing_code, attack_angle_deg, filament_d_m, bridge_radius_m, overlap_m,
    n_active_cells, cell_length_x_m, periodic_shift_y_m. Fluent meshing uses
    cfg.periodic_shift_y (mm) directly; the manifest metre field must not
    silently diverge. needs_lead_recheck is run-manifest only; porosity_eps is
    measured at mesh time.
    """
    merged = dict(payload)
    geometry = geometry_parameters_for_geo_id(geo_id)
    config_knobs = {
        "spacing_code",
        "attack_angle_deg",
        "filament_d_m",
        "bridge_radius_m",
        "overlap_m",
        "n_active_cells",
        "cell_length_x_m",
        "periodic_shift_y_m",
    }
    always_skip = {
        "needs_lead_recheck",
        # Measured at mesh time; caller overlays from mesh metrics.
        "porosity_eps",
    }
    for key, value in geometry.items():
        if key in always_skip:
            continue
        if key in config_knobs and key in merged:
            continue
        merged[key] = value
    if "porosity_eps" not in merged:
        merged["porosity_eps"] = None
    return merged


def merge_geometry_into_run_manifest(
    payload: Mapping[str, Any],
    geo_id: str,
    *,
    mesh_id: str,
) -> dict[str, Any]:
    """Add run-level schema-v2 fields."""
    merged = dict(payload)
    geometry = geometry_parameters_for_geo_id(geo_id)
    merged["u_mean_source_mesh_id"] = mesh_id
    merged["needs_lead_recheck"] = geometry["needs_lead_recheck"]
    for field in (
        "Sigma_d_nominal_m",
        "membrane_trim_m",
        "membrane_contact_width_m",
        "membrane_blocked_area_frac",
        "membrane_blocked_area_frac_geometric",
        "porosity_eps",
        "periodic_shift_y_m",
        "periodic_shift_y_source",
        "layer_angles_deg",
        "layer_diameters_m",
        "layer_axis_z_m",
        "joint_sphere_z_m",
        "joint_sphere_R_m",
        "joint_sphere_R_ratio",
        "joint_sphere_r_min_m",
        "joint_sphere_count",
        "curvature_margin",
        "spacer_wall_zones",
    ):
        merged[field] = geometry[field]
    return merged