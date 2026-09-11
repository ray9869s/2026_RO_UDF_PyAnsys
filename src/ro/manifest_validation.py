"""Manifest geometry and zone validation (loud failure, manifest-only inputs)."""

from __future__ import annotations

import ast
import math
import re
import warnings
from typing import Any, Collection, Mapping

from ro.campaign_geometry import CAMPAIGN_H_M
from ro.manifest_errors import ManifestError

_PERIODIC_SHIFT_SOURCES = frozenset({"derived_from_angle", "explicit"})
_SPACER_WALL_PREFIX = "wall_spacer_"
_DETECTED_SPACER_WALL_ZONES_RE = re.compile(
    r"^Detected spacer wall zones:\s*(.+)\s*$",
    re.MULTILINE,
)
_ALL_BOUNDARY_ZONES_RE = re.compile(
    r"^All boundary zones:\s*(.+)\s*$",
    re.MULTILINE,
)
# Three-band curvature gate. 1.0 is the geometric self-intersection limit
# (centerline curvature radius / tube radius). 1.2 is an undocumented former
# hard floor, now a warning band — see docs/GEOMETRY_DESIGN.md.
_CURVATURE_MARGIN_NOMINAL = 1.2
_CURVATURE_MARGIN_SELF_INTERSECTION = 1.0
# S_a193_l1733 is the one campaign case with R < r.
# R = lambda^2/(4 pi^2 a) = 0.39496 mm versus tube radius 0.4 mm, ratio
# 0.9874 (code curvature_margin=0.9874065974644699). Mesh 4,977,088 cells,
# ortho_min 0.121754, AR_max 48.41, skew_max 0.602 (inside campaign range;
# S_a193_l6930 is worse at ortho 0.1118 / skew 0.640). Solid volume
# 18.53702 mm3 vs a144_l1733 17.90907 mm3 (measured +0.628 mm3 vs nominal
# +0.589 mm3, 6.6% over); CAD healing of the self-intersection is a surface
# crease at 29 extrema, not volume removal. porosity 0.80629 vs 0.79949.
_CURVATURE_MARGIN_ACKNOWLEDGED_GEO_IDS = frozenset({"S_a193_l1733"})
_MEMBRANE_TRIM_TOLERANCE_M = 1.0e-9
_JOINT_SPHERE_RATIO_TOLERANCE_M = 1.0e-9
_POROSITY_EPS_MIN = 0.3
_POROSITY_EPS_MAX = 0.99
# Matches mesh_common._POROSITY_CLAMP_TOLERANCE: parse_mesh_metrics clamps
# porosity to exactly 1.0 when abs(raw - 1.0) <= this value.
_POROSITY_EPS_EMPTY_TOLERANCE = 1.0e-6

# Plausible absolute ranges for ``*_m`` fields (metres). Exact 0.0 is allowed
# (e.g. overlap_m, bridge_radius_m on empty/pillar); nonzero values must sit
# inside the band. Unknown ``*_m`` keys get the generic band.
_DOMAIN_SCALE_M = (1.0e-4, 1.0e-1)
_FEATURE_SCALE_M = (1.0e-6, 1.0e-2)
_GENERIC_SCALE_M = (1.0e-9, 1.0)
_DOMAIN_SCALE_FIELDS = frozenset({
    "cell_length_x_m",
    "periodic_shift_y_m",
    "buffer_length_in_m",
    "buffer_length_out_m",
    "domain_extent_x_m",
    "domain_extent_y_m",
    "domain_extent_z_m",
})
_FEATURE_SCALE_FIELDS = frozenset({
    "filament_d_m",
    "bridge_radius_m",
    "overlap_m",
    "layer_diameters_m",
    "layer_axis_z_m",
    "joint_sphere_z_m",
    "joint_sphere_R_m",
    "joint_sphere_r_min_m",
    "membrane_trim_m",
    "membrane_contact_width_m",
    "Sigma_d_nominal_m",
})


class ManifestValidationError(ManifestError):
    """Raised when manifest geometry validation fails."""


def _is_empty_family_payload(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("family") == "empty" or payload.get("geo_id") == "REF_empty"
    )


def _iter_metre_named_scalars(
    payload: Mapping[str, Any],
):
    """Yield (label, base_field, numeric_value) for every ``*_m`` scalar."""
    for key, value in payload.items():
        if not isinstance(key, str) or not key.endswith("_m"):
            continue
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if item is None:
                    continue
                yield f"{key}[{index}]", key, item
        else:
            yield key, key, value


def _metre_band_for_field(field: str) -> tuple[float, float]:
    if field in _DOMAIN_SCALE_FIELDS:
        return _DOMAIN_SCALE_M
    if field in _FEATURE_SCALE_FIELDS:
        return _FEATURE_SCALE_M
    return _GENERIC_SCALE_M


def _unit_leak_hint(abs_value: float, lo: float, hi: float) -> str:
    if abs_value > hi:
        return "value is ~1000x high; mm leaked into a metre field?"
    if abs_value < lo:
        return "value is ~1000x low; micrometres leaked into a metre field?"
    return "value outside expected metre band"


def validate_metre_field_scales(
    payload: Mapping[str, Any],
    *,
    kind: str = "Mesh",
) -> None:
    """Raise if any ``*_m`` field is outside its plausible metre band."""
    for label, field, raw in _iter_metre_named_scalars(payload):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ManifestValidationError(
                f"{kind} manifest field {label!r} must be numeric metres, "
                f"got {raw!r}."
            )
        value = float(raw)
        if not math.isfinite(value):
            raise ManifestValidationError(
                f"{kind} manifest field {label!r} must be finite metres, "
                f"got {raw!r}."
            )
        # Exact zero is a valid unset/no-feature value for several lengths.
        if value == 0.0:
            continue
        lo, hi = _metre_band_for_field(field)
        abs_value = abs(value)
        if abs_value < lo or abs_value > hi:
            raise ManifestValidationError(
                f"{kind} manifest field {label!r}={raw!r} is outside the "
                f"expected band [{lo:g}, {hi:g}] m; {_unit_leak_hint(abs_value, lo, hi)}"
            )


def derive_periodic_shift_y_from_angle_m(
    lf_m: float,
    attack_angle_deg: float,
) -> float:
    """W = L_f / sin(theta). Inputs are manifest fields, not geo_id tokens."""
    if not math.isfinite(lf_m) or lf_m <= 0.0:
        raise ManifestValidationError(
            f"lf_m must be a positive finite float for angle derivation, got {lf_m!r}."
        )
    if not math.isfinite(attack_angle_deg):
        raise ManifestValidationError(
            "attack_angle_deg must be finite for angle derivation, "
            f"got {attack_angle_deg!r}."
        )
    sin_theta = math.sin(math.radians(attack_angle_deg))
    if sin_theta <= 0.0:
        raise ManifestValidationError(
            "attack_angle_deg must yield sin(theta) > 0 for periodic derivation, "
            f"got angle={attack_angle_deg!r}, sin={sin_theta!r}."
        )
    return lf_m / sin_theta


def validate_periodic_shift_y_storage(
    payload: Mapping[str, Any],
    *,
    kind: str = "Mesh",
) -> None:
    """Validate periodic_shift_y_m/source without angle derivation."""
    source = payload["periodic_shift_y_source"]
    if source not in _PERIODIC_SHIFT_SOURCES:
        raise ManifestValidationError(
            f"{kind} manifest periodic_shift_y_source must be "
            f"{sorted(_PERIODIC_SHIFT_SOURCES)!r}, got {source!r}."
        )
    periodic_shift_y_m = float(payload["periodic_shift_y_m"])
    if not math.isfinite(periodic_shift_y_m) or periodic_shift_y_m <= 0.0:
        raise ManifestValidationError(
            f"{kind} manifest periodic_shift_y_m must be positive and finite, "
            f"got {payload['periodic_shift_y_m']!r}."
        )


def validate_periodic_shift_y(
    payload: Mapping[str, Any],
    *,
    kind: str = "Mesh",
    forbid_derivation: bool = False,
) -> None:
    """Validate periodic_shift_y_m against periodic_shift_y_source."""
    source = payload["periodic_shift_y_source"]
    if source not in _PERIODIC_SHIFT_SOURCES:
        raise ManifestValidationError(
            f"{kind} manifest periodic_shift_y_source must be "
            f"{sorted(_PERIODIC_SHIFT_SOURCES)!r}, got {source!r}."
        )
    periodic_shift_y_m = float(payload["periodic_shift_y_m"])
    if not math.isfinite(periodic_shift_y_m) or periodic_shift_y_m <= 0.0:
        raise ManifestValidationError(
            f"{kind} manifest periodic_shift_y_m must be positive and finite, "
            f"got {payload['periodic_shift_y_m']!r}."
        )

    if source == "derived_from_angle":
        if forbid_derivation:
            raise ManifestValidationError(
                f"{kind} manifest forbids angle-based periodic_shift_y derivation "
                f"when periodic_shift_y_source is {source!r}."
            )
        attack_angle_deg = float(payload["attack_angle_deg"])
        if not math.isfinite(attack_angle_deg):
            raise ManifestValidationError(
                f"{kind} manifest attack_angle_deg must be finite for "
                f"derived_from_angle, got {payload['attack_angle_deg']!r}."
            )
        sin_theta = math.sin(math.radians(attack_angle_deg))
        if sin_theta <= 0.0:
            raise ManifestValidationError(
                f"{kind} manifest attack_angle_deg must yield sin(theta) > 0 "
                f"for derived_from_angle, got angle={attack_angle_deg!r}."
            )
    elif source == "explicit":
        pass
    else:
        raise ManifestValidationError(
            f"Unhandled periodic_shift_y_source: {source!r}."
        )


def validate_sigma_d_invariant(payload: Mapping[str, Any], *, kind: str = "Mesh") -> None:
    """Check membrane_trim_m against independent Sigma_d_nominal_m.

    Sigma_d is the independent stacked-filament height. Expected trim is
    (Sigma_d - h) / 2. Pillar uses Sigma_d=None and membrane_trim_m=0.
    """
    membrane_trim_m = float(payload["membrane_trim_m"])
    if not math.isfinite(membrane_trim_m) or membrane_trim_m < 0.0:
        raise ManifestValidationError(
            f"{kind} manifest membrane_trim_m must be a finite float >= 0, "
            f"got {payload['membrane_trim_m']!r}."
        )
    sigma_d = payload["Sigma_d_nominal_m"]
    if sigma_d is None:
        if membrane_trim_m != 0.0:
            raise ManifestValidationError(
                f"{kind} manifest Sigma_d_nominal_m is null but "
                f"membrane_trim_m={membrane_trim_m!r}; Pillar expects trim=0."
            )
        return
    sigma_d_f = float(sigma_d)
    if not math.isfinite(sigma_d_f) or sigma_d_f <= 0.0:
        raise ManifestValidationError(
            f"{kind} manifest Sigma_d_nominal_m must be a positive finite float "
            f"when set, got {sigma_d!r}."
        )
    expected_trim = (sigma_d_f - CAMPAIGN_H_M) / 2.0
    if not math.isclose(
        membrane_trim_m,
        expected_trim,
        rel_tol=0.0,
        abs_tol=_MEMBRANE_TRIM_TOLERANCE_M,
    ):
        raise ManifestValidationError(
            f"{kind} manifest membrane_trim_m={membrane_trim_m!r} violates "
            f"(Sigma_d_nominal_m - h) / 2 = {expected_trim!r} "
            f"(Sigma_d={sigma_d_f!r}, h={CAMPAIGN_H_M!r})."
        )


def _parse_python_printed_str_list(payload: str):
    try:
        value = ast.literal_eval(payload.strip())
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    return value


def parse_detected_spacer_wall_zones(text: str):
    """Last ``Detected spacer wall zones:`` Python list in solver stdout, or None."""
    matches = list(_DETECTED_SPACER_WALL_ZONES_RE.finditer(text))
    if not matches:
        return None
    return _parse_python_printed_str_list(matches[-1].group(1))


def parse_all_boundary_zones(text: str):
    """Last ``All boundary zones:`` Python list in solver stdout, or None."""
    matches = list(_ALL_BOUNDARY_ZONES_RE.finditer(text))
    if not matches:
        return None
    return _parse_python_printed_str_list(matches[-1].group(1))


def inspect_solver_log_spacer_zones(
    text: str,
    *,
    declared_zones: Collection[str],
    geo_id: str,
    kind: str = "Mesh",
):
    """Compare solver-printed zone lists to declared spacer_wall_zones.

    Returns (status, reason): NO_LINE, PASS, NAME_ONLY, or REJECT.
    ``All boundary zones: []`` is REJECT (empty discovery must not pass
    REF_empty). NAME_ONLY means the spacer line matched but the boundary
    inventory line is missing, so this is not wiring evidence.
    """
    detected = parse_detected_spacer_wall_zones(text)
    boundaries = parse_all_boundary_zones(text)
    if detected is None:
        return "NO_LINE", None
    if boundaries is not None and len(boundaries) == 0:
        return "REJECT", "All boundary zones: [] (discovery empty)"
    fluent = boundaries if boundaries is not None else detected
    try:
        validate_spacer_wall_zones(
            declared_zones, fluent, geo_id=geo_id, kind=kind
        )
    except ManifestValidationError as exc:
        return "REJECT", str(exc)
    if boundaries is None:
        return "NAME_ONLY", None
    return "PASS", None


def collect_spacer_wall_zones_from_fluent(
    fluent_zone_names: Collection[str],
) -> list[str]:
    """Return sorted wall_spacer_* zones present in a Fluent zone list."""
    if not fluent_zone_names:
        raise ManifestValidationError(
            "Fluent zone list is empty; cannot collect spacer wall zones."
        )
    collected = sorted(
        name
        for name in fluent_zone_names
        if name.startswith(_SPACER_WALL_PREFIX) or name == "wall_spacer"
    )
    if not collected:
        raise ManifestValidationError(
            "No wall_spacer zones found in Fluent zone list "
            f"(prefix {_SPACER_WALL_PREFIX!r} or exact 'wall_spacer')."
        )
    return collected


def validate_spacer_wall_zones(
    declared_zones: Collection[str],
    fluent_zone_names: Collection[str],
    *,
    geo_id: str,
    kind: str = "Mesh",
) -> None:
    """Cross-check manifest spacer_wall_zones against Fluent zone names."""
    if not isinstance(declared_zones, (list, tuple)) or not declared_zones:
        if geo_id == "REF_empty":
            collected = [
                name
                for name in fluent_zone_names
                if name.startswith(_SPACER_WALL_PREFIX) or name == "wall_spacer"
            ]
            if collected:
                raise ManifestValidationError(
                    f"{kind} manifest REF_empty declares no spacer_wall_zones but "
                    f"Fluent has {collected!r}."
                )
            return
        raise ManifestValidationError(
            f"{kind} manifest spacer_wall_zones must be a non-empty list, "
            f"got {declared_zones!r}."
        )

    declared = list(declared_zones)
    fluent_set = set(fluent_zone_names)
    declared_set = set(declared)

    collected = collect_spacer_wall_zones_from_fluent(fluent_zone_names)
    collected_set = set(collected)

    if collected_set != declared_set:
        undeclared = sorted(collected_set - declared_set)
        missing = sorted(declared_set - fluent_set)
        parts: list[str] = []
        if undeclared:
            parts.append(f"undeclared Fluent zones {undeclared!r}")
        if missing:
            parts.append(f"declared zones absent in Fluent {missing!r}")
        raise ManifestValidationError(
            f"{kind} manifest spacer_wall_zones mismatch for {geo_id!r}: "
            + "; ".join(parts)
        )

    # Pillar hole-zone boolean guards (h00 has no bore; h15/h30 do).
    has_hole_declared = "wall_spacer_hole" in declared_set
    is_h00 = "_h00" in geo_id and geo_id.startswith("P_")
    is_bored = (
        geo_id.startswith("P_")
        and ("_h15" in geo_id or "_h30" in geo_id)
    )

    if is_h00 and has_hole_declared:
        raise ManifestValidationError(
            f"{kind} manifest {geo_id!r} is an h00 case but declares "
            "wall_spacer_hole (boolean misapplied)."
        )
    if is_bored and not has_hole_declared:
        raise ManifestValidationError(
            f"{kind} manifest {geo_id!r} is h15/h30 but lacks wall_spacer_hole "
            "(bore cut silently failed)."
        )


def validate_curvature_margin(payload: Mapping[str, Any], *, kind: str = "Mesh") -> None:
    """Sinusoidal curvature_margin three-band gate; others must be null.

    margin >= 1.2: pass silently.
    1.0 <= margin < 1.2: pass with a warning (reduced margin).
    margin < 1.0: pass with a louder warning only for geo_ids in
    ``_CURVATURE_MARGIN_ACKNOWLEDGED_GEO_IDS``; raise otherwise.
    """
    family = payload.get("family")
    geo_id = str(payload.get("geo_id", ""))
    margin = payload["curvature_margin"]
    is_sinusoidal = family == "sin" or geo_id.startswith("S")

    if not is_sinusoidal:
        if margin is not None:
            raise ManifestValidationError(
                f"{kind} manifest curvature_margin must be null for non-sinusoidal "
                f"family {family!r}, got {margin!r}."
            )
        return

    if margin is None:
        raise ManifestValidationError(
            f"{kind} manifest sinusoidal geo_id {geo_id!r} requires curvature_margin."
        )
    margin_f = float(margin)
    if not math.isfinite(margin_f):
        raise ManifestValidationError(
            f"{kind} manifest curvature_margin must be finite, got {margin!r}."
        )
    if margin_f >= _CURVATURE_MARGIN_NOMINAL:
        return
    if margin_f >= _CURVATURE_MARGIN_SELF_INTERSECTION:
        warnings.warn(
            f"{kind} manifest {geo_id} curvature_margin={margin_f} is below "
            f"{_CURVATURE_MARGIN_NOMINAL} (reduced margin against sweep "
            "self-intersection).",
            UserWarning,
            stacklevel=2,
        )
        return
    if geo_id in _CURVATURE_MARGIN_ACKNOWLEDGED_GEO_IDS:
        warnings.warn(
            f"WARNING: {kind} manifest {geo_id} curvature_margin={margin_f} "
            f"< {_CURVATURE_MARGIN_SELF_INTERSECTION}: the swept surface "
            "self-intersects and the CAD heals it.",
            UserWarning,
            stacklevel=2,
        )
        return
    raise ManifestValidationError(
        f"{kind} manifest curvature_margin={margin_f!r} < "
        f"{_CURVATURE_MARGIN_SELF_INTERSECTION} (swept-surface "
        f"self-intersection for {geo_id!r}; geo_id is not in the "
        "acknowledged set)."
    )


def validate_u_mean_source_mesh_id(
    run_payload: Mapping[str, Any],
    *,
    kind: str = "Run",
) -> None:
    """u_mean_source_mesh_id must match the run manifest mesh_id."""
    source_mesh_id = run_payload["u_mean_source_mesh_id"]
    mesh_id = run_payload["mesh_id"]
    if not isinstance(source_mesh_id, str) or not source_mesh_id.strip():
        raise ManifestValidationError(
            f"{kind} manifest u_mean_source_mesh_id must be a non-empty string, "
            f"got {source_mesh_id!r}."
        )
    if source_mesh_id != mesh_id:
        raise ManifestValidationError(
            f"{kind} manifest u_mean_source_mesh_id={source_mesh_id!r} does not "
            f"match mesh_id={mesh_id!r}."
        )


def validate_ml_layer_fields(payload: Mapping[str, Any], *, kind: str = "Mesh") -> None:
    """ML-only layer lists must be present; other families must leave them null."""
    family = payload["family"]
    layer_diameters = payload["layer_diameters_m"]
    layer_axis_z = payload["layer_axis_z_m"]
    joint_z = payload["joint_sphere_z_m"]

    if family == "ml":
        for field_name, value, expected_len in (
            ("layer_diameters_m", layer_diameters, 3),
            ("layer_axis_z_m", layer_axis_z, 3),
            ("joint_sphere_z_m", joint_z, 2),
        ):
            if not isinstance(value, list) or len(value) != expected_len:
                raise ManifestValidationError(
                    f"{kind} manifest {field_name} must be a length-{expected_len} "
                    f"list for ml, got {value!r}."
                )
            # Diameters must be positive. Axis/joint z are campaign-frame
            # (mid-plane at 0), so bottom-layer and lower-sphere values are negative.
            if field_name == "layer_diameters_m":
                bad = any(
                    not math.isfinite(float(v)) or float(v) <= 0.0 for v in value
                )
            else:
                bad = any(not math.isfinite(float(v)) for v in value)
            if bad:
                raise ManifestValidationError(
                    f"{kind} manifest {field_name} must be a length-{expected_len} "
                    f"list of finite floats for ml"
                    + (
                        " (positive diameters)"
                        if field_name == "layer_diameters_m"
                        else " (campaign-frame z; may be negative)"
                    )
                    + f", got {value!r}."
                )
        return

    for field_name, value in (
        ("layer_diameters_m", layer_diameters),
        ("layer_axis_z_m", layer_axis_z),
        ("joint_sphere_z_m", joint_z),
    ):
        if value is not None:
            raise ManifestValidationError(
                f"{kind} manifest {field_name} must be null for family {family!r}, "
                f"got {value!r}."
            )


def validate_joint_sphere_consistency(
    payload: Mapping[str, Any],
    *,
    kind: str = "Mesh",
) -> None:
    """Rule 3-6: joint-sphere count and R = R_ratio * r_min identity.

    ML spheres may exceed r_min and may interpenetrate each other: they are
    subtracted from a solid box with the filaments, so those conditions only
    reshape the fluid boundary. Do not reinstate R < r_min or separation > 2R
    guards for this family.
    """
    count = payload["joint_sphere_count"]
    sphere_r = payload["joint_sphere_R_m"]
    ratio = payload["joint_sphere_R_ratio"]
    r_min = payload["joint_sphere_r_min_m"]
    family = payload["family"]

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ManifestValidationError(
            f"{kind} manifest joint_sphere_count must be an integer >= 0, "
            f"got {count!r}."
        )

    sphere_fields = (
        ("joint_sphere_R_m", sphere_r),
        ("joint_sphere_R_ratio", ratio),
        ("joint_sphere_r_min_m", r_min),
    )

    if count == 0:
        for field_name, value in sphere_fields:
            if value is not None:
                raise ManifestValidationError(
                    f"{kind} manifest {field_name} must be null when "
                    f"joint_sphere_count is 0, got {value!r}."
                )
        return

    for field_name, value in sphere_fields:
        if value is None or not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ManifestValidationError(
                f"{kind} manifest {field_name} must be a positive finite float when "
                f"joint_sphere_count > 0, got {value!r}."
            )

    expected_r = float(ratio) * float(r_min)
    if not math.isclose(float(sphere_r), expected_r, abs_tol=_JOINT_SPHERE_RATIO_TOLERANCE_M):
        raise ManifestValidationError(
            f"{kind} manifest joint_sphere_R_m={sphere_r!r} is inconsistent with "
            f"joint_sphere_R_ratio * joint_sphere_r_min_m = {expected_r!r}."
        )

    if family != "ml":
        return

    if count != 2:
        raise ManifestValidationError(
            f"{kind} manifest ml family requires joint_sphere_count=2, got {count!r}."
        )

    joint_z = payload["joint_sphere_z_m"]
    if not isinstance(joint_z, list) or len(joint_z) != 2:
        raise ManifestValidationError(
            f"{kind} manifest joint_sphere_z_m must be a length-2 list for ml, "
            f"got {joint_z!r}."
        )


def validate_mesh_geometry_fields(payload: Mapping[str, Any]) -> None:
    """Run all mesh-leaf geometry validations."""
    validate_metre_field_scales(payload, kind="Mesh")
    validate_periodic_shift_y(payload, kind="Mesh")
    validate_sigma_d_invariant(payload, kind="Mesh")
    validate_curvature_margin(payload, kind="Mesh")
    validate_ml_layer_fields(payload, kind="Mesh")
    validate_joint_sphere_consistency(payload, kind="Mesh")
    blocked = float(payload["membrane_blocked_area_frac"])
    if not math.isfinite(blocked) or not 0.0 <= blocked < 1.0:
        raise ManifestValidationError(
            "Mesh manifest membrane_blocked_area_frac must be in [0, 1), "
            f"got {payload['membrane_blocked_area_frac']!r}."
        )
    blocked_geom = payload["membrane_blocked_area_frac_geometric"]
    if blocked_geom is not None:
        blocked_geom_f = float(blocked_geom)
        if not math.isfinite(blocked_geom_f) or not 0.0 <= blocked_geom_f < 1.0:
            raise ManifestValidationError(
                "Mesh manifest membrane_blocked_area_frac_geometric must be in "
                f"[0, 1) when set, got {payload['membrane_blocked_area_frac_geometric']!r}."
            )
    contact_width = payload["membrane_contact_width_m"]
    if contact_width is not None:
        contact_width_f = float(contact_width)
        if not math.isfinite(contact_width_f) or contact_width_f < 0.0:
            raise ManifestValidationError(
                "Mesh manifest membrane_contact_width_m must be a finite float "
                f">= 0 when set, got {payload['membrane_contact_width_m']!r}."
            )
    porosity = payload["porosity_eps"]
    if porosity is not None:
        porosity_f = float(porosity)
        if _is_empty_family_payload(payload):
            if not math.isfinite(porosity_f) or not math.isclose(
                porosity_f,
                1.0,
                rel_tol=0.0,
                abs_tol=_POROSITY_EPS_EMPTY_TOLERANCE,
            ):
                raise ManifestValidationError(
                    "Mesh manifest porosity_eps must be 1.0 (within "
                    f"{_POROSITY_EPS_EMPTY_TOLERANCE:g} absolute tolerance) "
                    f"for empty family, got {payload['porosity_eps']!r}."
                )
        elif (
            not math.isfinite(porosity_f)
            or not _POROSITY_EPS_MIN <= porosity_f <= _POROSITY_EPS_MAX
        ):
            raise ManifestValidationError(
                f"Mesh manifest porosity_eps must be in "
                f"[{_POROSITY_EPS_MIN}, {_POROSITY_EPS_MAX}] when set, "
                f"got {payload['porosity_eps']!r}."
            )
    joint_count = payload["joint_sphere_count"]
    if isinstance(joint_count, bool) or not isinstance(joint_count, int) or joint_count < 0:
        raise ManifestValidationError(
            "Mesh manifest joint_sphere_count must be an integer >= 0, "
            f"got {joint_count!r}."
        )
    layer_angles = payload["layer_angles_deg"]
    family = payload["family"]
    if family == "ml":
        if not isinstance(layer_angles, list) or not layer_angles:
            raise ManifestValidationError(
                "Mesh manifest layer_angles_deg must be a non-empty list for ml."
            )
    elif layer_angles is not None:
        raise ManifestValidationError(
            f"Mesh manifest layer_angles_deg must be null for family {family!r}."
        )
    zones = payload["spacer_wall_zones"]
    if not isinstance(zones, list):
        raise ManifestValidationError(
            "Mesh manifest spacer_wall_zones must be a list, "
            f"got {zones!r}."
        )
    geo_id = str(payload.get("geo_id", ""))
    if geo_id != "REF_empty" and not zones:
        raise ManifestValidationError(
            f"Mesh manifest spacer_wall_zones must be non-empty for {geo_id!r}."
        )


def validate_run_geometry_fields(run_payload: Mapping[str, Any]) -> None:
    """Run all run-leaf geometry validations."""
    validate_metre_field_scales(run_payload, kind="Run")
    validate_u_mean_source_mesh_id(run_payload)
    validate_periodic_shift_y_storage(run_payload, kind="Run")
    validate_sigma_d_invariant(run_payload, kind="Run")
    validate_curvature_margin(run_payload, kind="Run")
    validate_ml_layer_fields(run_payload, kind="Run")
    validate_joint_sphere_consistency(run_payload, kind="Run")
    needs_recheck = run_payload["needs_lead_recheck"]
    if not isinstance(needs_recheck, bool):
        raise ManifestValidationError(
            "Run manifest needs_lead_recheck must be a bool, "
            f"got {needs_recheck!r}."
        )
    family = run_payload["family"]
    if family == "pillar" and not needs_recheck:
        raise ManifestValidationError(
            "Run manifest pillar family requires needs_lead_recheck=True."
        )
    if family == "diamond" and needs_recheck:
        raise ManifestValidationError(
            "Run manifest diamond family requires needs_lead_recheck=False."
        )
    zones = run_payload["spacer_wall_zones"]
    if not isinstance(zones, list):
        raise ManifestValidationError(
            f"Run manifest spacer_wall_zones must be a list, got {zones!r}."
        )
    geo_id = str(run_payload.get("geo_id", ""))
    if geo_id != "REF_empty" and not zones:
        raise ManifestValidationError(
            f"Run manifest spacer_wall_zones must be non-empty for {geo_id!r}."
        )


CAMPAIGN_MEMBRANE_BLOCKED_AREA_FRAC = 0.0


def campaign_blocked_frac_block_reason(payload: Mapping[str, Any]):
    """Return why campaign blocked-frac fails, or None.

    Schema stays ``[0, 1)``. Campaign policy is exact 0.0. Missing key is
    not a campaign reject here — that leaf is unreadable by ``read_*``.
    """
    if "membrane_blocked_area_frac" not in payload:
        return None
    value = payload["membrane_blocked_area_frac"]
    try:
        blocked = float(value)
    except (TypeError, ValueError):
        return f"membrane_blocked_area_frac is not a number: {value!r}"
    if isinstance(value, bool) or blocked != CAMPAIGN_MEMBRANE_BLOCKED_AREA_FRAC:
        return (
            "campaign membrane_blocked_area_frac must be exactly "
            f"{CAMPAIGN_MEMBRANE_BLOCKED_AREA_FRAC}, got {value!r}"
        )
    return None


def require_campaign_membrane_blocked_area_frac(
    payload: Mapping[str, Any],
    *,
    kind: str = "Manifest",
) -> None:
    reason = campaign_blocked_frac_block_reason(payload)
    if reason is not None:
        raise ManifestValidationError(f"{kind} {reason}")


def mesh_run_blocked_frac_block_reason(
    mesh_payload: Mapping[str, Any],
    run_payload: Mapping[str, Any],
):
    """Return why mesh/run blocked-frac disagree, or None."""
    if (
        "membrane_blocked_area_frac" not in mesh_payload
        or "membrane_blocked_area_frac" not in run_payload
    ):
        return None
    mesh_value = mesh_payload["membrane_blocked_area_frac"]
    run_value = run_payload["membrane_blocked_area_frac"]
    if mesh_value != run_value:
        return (
            "mesh and run membrane_blocked_area_frac disagree: "
            f"mesh={mesh_value!r} run={run_value!r}"
        )
    return None


def require_mesh_run_blocked_frac_agree(
    mesh_payload: Mapping[str, Any],
    run_payload: Mapping[str, Any],
) -> None:
    reason = mesh_run_blocked_frac_block_reason(mesh_payload, run_payload)
    if reason is not None:
        raise ManifestValidationError(reason)
