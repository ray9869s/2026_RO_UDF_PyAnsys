"""Single resolution point for campaign and MFBO pillar geometry.

Campaign ids keep ``campaign_geometry``. MFBO pillar ids (``MFP_*``) read the
registry entry stored next to the generated CAD.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ro.campaign_geo_ids import (
    CAMPAIGN_GEO_IDS,
    family_for_geo_id,
    validate_campaign_geo_id,
)
from ro.campaign_geometry import (
    _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
    _PILLAR_HAS_HOLE,
    _PILLAR_SPACER_WALL_BUFFER,
    _PILLAR_SPACER_WALL_ZONES_BASE,
    _PILLAR_UNIT_CELL_M,
)
from ro.solver_common import sha256_file

# Micrometres, four digits each. Example: 0.60 mm, 0.15 mm, 0.40 mm
# -> MFP_d0600_h0150_f0400.
MFBO_PILLAR_GEO_ID_RE = re.compile(r"^MFP_d(\d{4})_h(\d{4})_f(\d{4})$")

PILLAR_REGISTRY_FIELDS = (
    "spacing_code",
    "attack_angle_deg",
    "filament_d_m",
    "bridge_radius_m",
    "overlap_m",
    "n_active_cells",
    "cell_length_x_m",
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
    "needs_lead_recheck",
)

META_REGISTRY_KEY = "registry"
META_PMDB_SHA256_KEY = "pmdb_sha256"


def _millimetres(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}.")
    return number


def _um_token(name: str, millimetres: float) -> str:
    micrometres = int(round(millimetres * 1000.0))
    if micrometres > 9999:
        raise ValueError(
            f"{name} rounds to {micrometres} um, which does not fit 4 digits."
        )
    return f"{micrometres:04d}"


def format_mfbo_pillar_geo_id(d_p_mm: float, d_h_mm: float, d_f_mm: float) -> str:
    """Return ``MFP_dXXXX_hXXXX_fXXXX`` with each diameter rounded to 1 um."""
    return (
        "MFP_"
        f"d{_um_token('d_p_mm', _millimetres('d_p_mm', d_p_mm))}_"
        f"h{_um_token('d_h_mm', _millimetres('d_h_mm', d_h_mm))}_"
        f"f{_um_token('d_f_mm', _millimetres('d_f_mm', d_f_mm))}"
    )


def parse_mfbo_pillar_geo_id(geo_id: str) -> tuple[int, int, int] | None:
    """Return ``(d_p_um, d_h_um, d_f_um)`` or None when ``geo_id`` is not MFP."""
    if not isinstance(geo_id, str):
        return None
    match = MFBO_PILLAR_GEO_ID_RE.fullmatch(geo_id)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_mfbo_pillar_geo_id(geo_id: str) -> bool:
    return parse_mfbo_pillar_geo_id(geo_id) is not None


def pillar_registry_entry(
    geo_id: str,
    d_p_m: float,
    d_h_m: float,
    d_f_m: float,
) -> dict[str, Any]:
    """Pillar manifest geometry. ``d_p_m`` / ``d_h_m`` / ``d_f_m`` are metres."""
    d_p_m = _millimetres("d_p_m", d_p_m)
    d_h_m = _millimetres("d_h_m", d_h_m)
    d_f_m = _millimetres("d_f_m", d_f_m)
    if d_p_m <= 0.0:
        raise ValueError(f"d_p_m must be positive, got {d_p_m}.")
    cell_area_m2 = _PILLAR_UNIT_CELL_M * _PILLAR_UNIT_CELL_M / 2.0
    blocked = math.pi * (d_p_m / 2.0) ** 2 / cell_area_m2
    zones = list(_PILLAR_SPACER_WALL_ZONES_BASE)
    if d_h_m > 0.0:
        zones.append("wall_spacer_hole")
    zones.append(_PILLAR_SPACER_WALL_BUFFER)
    return {
        "spacing_code": geo_id,
        "attack_angle_deg": 0.0,
        "filament_d_m": d_f_m,
        "bridge_radius_m": 0.0,
        "overlap_m": 0.0,
        "n_active_cells": 7,
        "cell_length_x_m": _PILLAR_UNIT_CELL_M,
        "Sigma_d_nominal_m": None,
        "membrane_trim_m": 0.0,
        "membrane_contact_width_m": None,
        "membrane_blocked_area_frac": _MEMBRANE_BLOCKED_AREA_FRAC_CONSUMED,
        "membrane_blocked_area_frac_geometric": blocked,
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


def mfbo_pillar_meta_path(geo_id: str) -> Path:
    """``<data_root>/geometries/pillar/<geo_id>/<geo_id>_meta.json``."""
    from ro.paths import data_root

    return (
        data_root()
        / "geometries"
        / "pillar"
        / geo_id
        / f"{geo_id}_meta.json"
    )


def _read_meta(geo_id: str) -> tuple[Path, dict[str, Any]]:
    import json

    path = mfbo_pillar_meta_path(geo_id)
    if not path.is_file():
        raise FileNotFoundError(f"MFBO pillar meta not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"MFBO pillar meta must be a JSON object: {path}")
    return path, payload


def _registry_from_meta(geo_id: str) -> dict[str, Any]:
    encoded = parse_mfbo_pillar_geo_id(geo_id)
    if encoded is None:
        raise ValueError(f"Not an MFBO pillar geo_id: {geo_id!r}.")
    path, payload = _read_meta(geo_id)
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError(f"MFBO pillar meta {path} is missing inputs.")
    names = ("d_p_mm", "d_h_mm", "d_f_mm")
    for name, micrometres in zip(names, encoded):
        if name not in inputs:
            raise ValueError(f"MFBO pillar meta {path} inputs missing {name!r}.")
        millimetres = _millimetres(name, inputs[name])
        if int(round(millimetres * 1000.0)) != micrometres:
            raise ValueError(
                f"MFBO geo_id {geo_id!r} encodes {name}={micrometres} um "
                f"but meta inputs {name}={inputs[name]!r}."
            )
    registry = payload.get(META_REGISTRY_KEY)
    if not isinstance(registry, dict):
        raise ValueError(f"MFBO pillar meta {path} is missing {META_REGISTRY_KEY!r}.")
    missing = [field for field in PILLAR_REGISTRY_FIELDS if field not in registry]
    if missing:
        raise ValueError(
            f"MFBO pillar meta {path} registry missing fields: {missing!r}."
        )
    copied: dict[str, Any] = {}
    for field in PILLAR_REGISTRY_FIELDS:
        value = registry[field]
        copied[field] = list(value) if isinstance(value, list) else value
    return copied


def resolve_geometry_parameters(geo_id: str) -> dict[str, Any]:
    """Campaign id uses the campaign registry. MFP id uses its meta entry."""
    from ro.campaign_geometry import _campaign_geometry_parameters

    if geo_id in CAMPAIGN_GEO_IDS:
        return _campaign_geometry_parameters(geo_id)
    if is_mfbo_pillar_geo_id(geo_id):
        return _registry_from_meta(geo_id)
    raise ValueError(f"Unknown campaign geo_id: {geo_id!r}.")


def validate_known_geo_id(geo_id: str) -> None:
    """Accept a campaign id or an MFP id. Other ids use today's campaign error."""
    if is_mfbo_pillar_geo_id(geo_id):
        return
    validate_campaign_geo_id(geo_id)


def family_for_known_geo_id(geo_id: str) -> str:
    """``pillar`` for an MFP id. Other ids use ``family_for_geo_id``."""
    if is_mfbo_pillar_geo_id(geo_id):
        return "pillar"
    return family_for_geo_id(geo_id)


def require_pillar_cad_geo_id(
    geo_id: str,
    d_p_mm: float,
    d_h_mm: float,
    d_f_mm: float,
) -> None:
    """Allow a campaign pillar id, or the MFP id formatted from these inputs."""
    if geo_id in _PILLAR_HAS_HOLE:
        return
    formatted = format_mfbo_pillar_geo_id(d_p_mm, d_h_mm, d_f_mm)
    if geo_id == formatted:
        return
    raise ValueError(
        f"geo_id {geo_id!r} is neither an MFP id for these diameters "
        f"({formatted}) nor a campaign pillar id."
    )


def require_mfp_geometry_sha256(geo_id: str, geometry_path) -> None:
    """For an MFP id, require the geometry file sha256 to match meta.

    Campaign ids, including the default ``.dsco`` path, return without reading
    a meta file.
    """
    if not is_mfbo_pillar_geo_id(geo_id):
        return
    path, payload = _read_meta(geo_id)
    expected = payload.get(META_PMDB_SHA256_KEY)
    if not isinstance(expected, str) or not expected:
        raise ValueError(
            f"MFBO pillar meta {path} is missing {META_PMDB_SHA256_KEY!r}."
        )
    actual = sha256_file(geometry_path)
    if actual != expected:
        raise ValueError(
            f"MFBO geometry sha256 mismatch for {geo_id}: "
            f"file {actual} vs meta {expected}."
        )
