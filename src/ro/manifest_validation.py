"""Manifest geometry and zone validation (loud failure, manifest-only inputs)."""

from __future__ import annotations

import math
from typing import Any, Collection, Mapping

from ro.campaign_geometry import CAMPAIGN_H_M
from ro.manifest_errors import ManifestError

_PERIODIC_SHIFT_SOURCES = frozenset({"derived_from_angle", "explicit"})
_SPACER_WALL_PREFIX = "wall_spacer_"
_CURVATURE_MARGIN_MIN = 1.2
_MEMBRANE_TRIM_TOLERANCE_M = 1.0e-9
_JOINT_SPHERE_RATIO_TOLERANCE_M = 1.0e-9
_POROSITY_EPS_MIN = 0.3
_POROSITY_EPS_MAX = 0.99

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
    """Sinusoidal cases must carry curvature_margin >= 1.2; others must be null."""
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

    if geo_id == "S_A000":
        if margin is not None:
            raise ManifestValidationError(
                f"{kind} manifest S_A000 control case must have null curvature_margin, "
                f"got {margin!r}."
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
    if margin_f < _CURVATURE_MARGIN_MIN:
        raise ManifestValidationError(
            f"{kind} manifest curvature_margin={margin_f!r} < {_CURVATURE_MARGIN_MIN} "
            f"(self-intersection risk for {geo_id!r})."
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
            if (
                not isinstance(value, list)
                or len(value) != expected_len
                or any(not math.isfinite(float(v)) or float(v) <= 0.0 for v in value)
            ):
                raise ManifestValidationError(
                    f"{kind} manifest {field_name} must be a length-{expected_len} "
                    f"list of positive floats for ml, got {value!r}."
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
        if (
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
