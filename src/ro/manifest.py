"""Validated mesh and run manifests for the external RO data tree.

Schema version 2 extends every mesh/run leaf with campaign geometry fields
(all lengths in metres unless noted):

Common geometry (mesh + run):
  Sigma_d_nominal_m:          float | null
  membrane_trim_m:            float
  membrane_contact_width_m:   float | null
  membrane_blocked_area_frac: float in [0, 1)
                          # LMH consumed; campaign-wide nominal-area basis (0.0)
  membrane_blocked_area_frac_geometric: float in [0, 1) | null
                          # Real footprint/contact geometry; unused by LMH
  porosity_eps:               float in [0.3, 0.99] | null  (measured; not a registry constant)
  periodic_shift_y_m:         float
  periodic_shift_y_source:    "derived_from_angle" | "explicit"
  layer_angles_deg:           list[float] | null
  layer_diameters_m:          list[float] | null  (ML only; diameters in metres)
  layer_axis_z_m:             list[float] | null  (ML only; campaign frame, mid-plane z=0)
  joint_sphere_z_m:           list[float] | null  (ML only; campaign frame)
  joint_sphere_R_m:           float | null
  joint_sphere_R_ratio:       float | null        # R / r_min; recorded, not constrained
  joint_sphere_r_min_m:       float | null        # thinner filament radius at contact
  joint_sphere_count:         int >= 0
  curvature_margin:           float | null (sinusoidal only, >= 1.2 when set)
  spacer_wall_zones:          list[str]

Run-only:
  u_mean_source_mesh_id:      must equal mesh_id
  needs_lead_recheck:         bool (True for pillar)
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ro.campaign_geo_ids import family_for_geo_id, validate_campaign_geo_id
from ro.manifest_validation import (
    validate_mesh_geometry_fields,
    validate_run_geometry_fields,
)
from ro.paths import mesh_dir, meshes_root, run_dir, runs_root
from ro.solver_common import STOP_REASON_VALUES
from ro.udm_layout import parse_ro_analytic_cwall_from_case


from ro.manifest_errors import ManifestError

MANIFEST_SCHEMA_VERSION = 2

_GEOMETRY_FIELDS = (
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
)

MESH_MANIFEST_REQUIRED_FIELDS = (
    "schema_version",
    "family",
    "geo_id",
    "mesh_id",
    "spacing_code",
    "attack_angle_deg",
    "filament_d_m",
    "bridge_radius_m",
    "overlap_m",
    "n_active_cells",
    "n_buffer_in",
    "n_buffer_out",
    "cell_length_x_m",
    "buffer_length_in_m",
    "buffer_length_out_m",
    "membrane_wall_base_names",
    "buffer_wall_base_names",
    "n_lead_excluded",
    "n_trail_excluded",
    "max_size_mm",
    "min_size_mm",
    "cpg",
    "bl",
    "peel",
    "ortho_min",
    "AR_max",
    "skewness_max",
    "skewed_face_fraction",
    "cell_count",
    "inlet_profile_G",
    "mesh_sha256",
    "created_utc",
    "generator_version",
) + _GEOMETRY_FIELDS

RUN_MANIFEST_REQUIRED_FIELDS = (
    "schema_version",
    "family",
    "geo_id",
    "mesh_id",
    "mesh_sha256",
    "run_id",
    "u_mean_ms",
    "p_gauge_pa",
    "u_target_ms",
    "inlet_bc_type",
    "udf_version",
    "analytic_cwall",
    "solver_settings",
    "stop_reason",
    "created_utc",
    "u_mean_source_mesh_id",
    "needs_lead_recheck",
) + _GEOMETRY_FIELDS

_MESH_PARAMETER_FIELDS = (
    "family",
    "geo_id",
    "mesh_id",
    "spacing_code",
    "attack_angle_deg",
    "filament_d_m",
    "bridge_radius_m",
    "overlap_m",
    "n_active_cells",
    "n_buffer_in",
    "n_buffer_out",
    "cell_length_x_m",
    "buffer_length_in_m",
    "buffer_length_out_m",
    "membrane_wall_base_names",
    "buffer_wall_base_names",
    "n_lead_excluded",
    "n_trail_excluded",
    "max_size_mm",
    "min_size_mm",
    "cpg",
    "bl",
    "peel",
) + _GEOMETRY_FIELDS

_RUN_PARAMETER_FIELDS = (
    "family",
    "geo_id",
    "mesh_id",
    "mesh_sha256",
    "run_id",
    "p_gauge_pa",
    "u_target_ms",
    "inlet_bc_type",
    "udf_version",
    "analytic_cwall",
    "solver_settings",
    "needs_lead_recheck",
) + _GEOMETRY_FIELDS

_MESH_QUALITY_FIELDS = (
    "ortho_min",
    "AR_max",
    "skewness_max",
    "skewed_face_fraction",
    "cell_count",
)

_SOLVER_SETTING_FIELDS = (
    "max_iterations",
    "residual_target",
    "operating_pressure",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_fields(
    payload: Mapping[str, Any],
    required_fields: tuple[str, ...],
    kind: str,
) -> None:
    missing = tuple(field for field in required_fields if field not in payload)
    if missing:
        raise ManifestError(f"{kind} manifest missing required fields: {missing!r}.")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"Unsupported {kind} manifest schema_version: "
            f"{payload['schema_version']!r}; expected {MANIFEST_SCHEMA_VERSION}."
        )


def _require_number(payload: Mapping[str, Any], field: str, kind: str) -> None:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(
            f"{kind} manifest field {field!r} must be numeric, got {value!r}."
        )
    if not math.isfinite(float(value)):
        raise ManifestError(
            f"{kind} manifest field {field!r} must be finite, got {value!r}."
        )


def _require_nonempty_string(
    payload: Mapping[str, Any],
    field: str,
    kind: str,
) -> None:
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(
            f"{kind} manifest field {field!r} must be a non-empty string, "
            f"got {value!r}."
        )


def _require_integer(
    payload: Mapping[str, Any],
    field: str,
    kind: str,
    *,
    minimum: int,
) -> None:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(
            f"{kind} manifest field {field!r} must be an integer >= {minimum}, "
            f"got {value!r}."
        )


def _require_string_list(
    payload: Mapping[str, Any],
    field: str,
    kind: str,
) -> None:
    value = payload[field]
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ManifestError(
            f"{kind} manifest field {field!r} must be a non-empty list of "
            f"non-empty strings, got {value!r}."
        )


def _require_sha256(payload: Mapping[str, Any], field: str, kind: str) -> None:
    value = payload[field]
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ManifestError(
            f"{kind} manifest field {field!r} must be a lowercase SHA-256 hex "
            f"digest, got {value!r}."
        )


def _is_empty_family_payload(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("family") == "empty" or payload.get("geo_id") == "REF_empty"
    )


def _require_exact_zero_mesh_field(payload: Mapping[str, Any], field: str) -> None:
    value = payload[field]
    if value is None or float(value) != 0.0:
        raise ManifestError(
            f"Mesh manifest field {field!r} must be exactly 0.0 for empty "
            f"family, got {value!r}."
        )


def _validate_mesh_payload(payload: Mapping[str, Any]) -> None:
    _require_fields(payload, MESH_MANIFEST_REQUIRED_FIELDS, "Mesh")
    for field in ("spacing_code", "created_utc", "generator_version"):
        _require_nonempty_string(payload, field, "Mesh")
    for field in (
        "attack_angle_deg",
        "filament_d_m",
        "bridge_radius_m",
        "overlap_m",
        "cell_length_x_m",
        "buffer_length_in_m",
        "buffer_length_out_m",
        "max_size_mm",
        "min_size_mm",
    ):
        _require_number(payload, field, "Mesh")
    for field in ("n_active_cells", "cpg", "bl"):
        _require_integer(payload, field, "Mesh", minimum=1)
    for field in (
        "n_buffer_in",
        "n_buffer_out",
        "n_lead_excluded",
        "n_trail_excluded",
        "peel",
    ):
        _require_integer(payload, field, "Mesh", minimum=0)
    mesh_id = payload["mesh_id"]
    if not isinstance(mesh_id, str) or not mesh_id.endswith(
        f"_peel{payload['peel']}"
    ):
        raise ManifestError(
            "Mesh manifest mesh_id peel token must match field 'peel'. "
            f"mesh_id={mesh_id!r}, peel={payload['peel']!r}."
        )
    for field in ("membrane_wall_base_names", "buffer_wall_base_names"):
        _require_string_list(payload, field, "Mesh")

    positive_fields = (
        "filament_d_m",
        "cell_length_x_m",
        "buffer_length_in_m",
        "buffer_length_out_m",
        "max_size_mm",
        "min_size_mm",
    )
    empty_exact_zero_fields = (
        "filament_d_m",
        "bridge_radius_m",
        "overlap_m",
        "membrane_trim_m",
        "membrane_contact_width_m",
    )
    if _is_empty_family_payload(payload):
        for field in empty_exact_zero_fields:
            _require_exact_zero_mesh_field(payload, field)
        for field in (
            "cell_length_x_m",
            "buffer_length_in_m",
            "buffer_length_out_m",
            "max_size_mm",
            "min_size_mm",
        ):
            if float(payload[field]) <= 0.0:
                raise ManifestError(
                    f"Mesh manifest field {field!r} must be positive, "
                    f"got {payload[field]!r}."
                )
    else:
        for field in positive_fields:
            if float(payload[field]) <= 0.0:
                raise ManifestError(
                    f"Mesh manifest field {field!r} must be positive, "
                    f"got {payload[field]!r}."
                )
        for field in ("bridge_radius_m", "overlap_m"):
            if float(payload[field]) < 0.0:
                raise ManifestError(
                    f"Mesh manifest field {field!r} must be nonnegative, "
                    f"got {payload[field]!r}."
                )
    if float(payload["min_size_mm"]) > float(payload["max_size_mm"]):
        raise ManifestError("Mesh manifest min_size_mm must be <= max_size_mm.")
    if (
        payload["n_lead_excluded"] + payload["n_trail_excluded"]
        >= payload["n_active_cells"]
    ):
        raise ManifestError(
            "Mesh manifest lead/trail exclusions must leave an active cell."
        )

    mesh_sha256 = payload["mesh_sha256"]
    if mesh_sha256 is not None:
        _require_sha256(payload, "mesh_sha256", "Mesh")
        missing_quality = tuple(
            field for field in _MESH_QUALITY_FIELDS if payload[field] is None
        )
        if missing_quality:
            raise ManifestError(
                "Mesh quality fields may not be null once mesh_sha256 is set: "
                f"{missing_quality!r}."
            )
        for field in _MESH_QUALITY_FIELDS:
            _require_number(payload, field, "Mesh")
        _require_integer(payload, "cell_count", "Mesh", minimum=1)
        if float(payload["AR_max"]) <= 0.0:
            raise ManifestError("Mesh manifest AR_max must be positive.")
        for field in ("ortho_min", "skewness_max", "skewed_face_fraction"):
            value = float(payload[field])
            if not 0.0 <= value <= 1.0:
                raise ManifestError(
                    f"Mesh manifest field {field!r} must be in [0, 1], "
                    f"got {payload[field]!r}."
                )
    inlet_profile_g = payload["inlet_profile_G"]
    if inlet_profile_g is not None:
        _require_number(payload, "inlet_profile_G", "Mesh")
        if float(inlet_profile_g) <= 0.0:
            raise ManifestError("Mesh manifest inlet_profile_G must be positive.")

    geo_id = payload["geo_id"]
    validate_campaign_geo_id(geo_id)
    expected_family = family_for_geo_id(geo_id)
    if payload["family"] != expected_family:
        raise ManifestError(
            f"Mesh manifest family={payload['family']!r} does not match "
            f"geo_id {geo_id!r} (expected {expected_family!r})."
        )
    validate_mesh_geometry_fields(payload)


def _validate_run_payload(payload: Mapping[str, Any]) -> None:
    _require_fields(payload, RUN_MANIFEST_REQUIRED_FIELDS, "Run")
    _require_sha256(payload, "mesh_sha256", "Run")
    for field in ("udf_version", "created_utc"):
        _require_nonempty_string(payload, field, "Run")
    for field in ("p_gauge_pa", "u_target_ms"):
        _require_number(payload, field, "Run")

    inlet_bc_type = payload["inlet_bc_type"]
    if inlet_bc_type not in ("parabolic", "plug"):
        raise ManifestError(
            "Run manifest inlet_bc_type must be 'parabolic' or 'plug', "
            f"got {inlet_bc_type!r}."
        )
    if payload["u_mean_ms"] is not None:
        _require_number(payload, "u_mean_ms", "Run")
    elif inlet_bc_type == "plug":
        raise ManifestError("Run manifest u_mean_ms may not be null for plug inlet.")

    stop_reason = payload["stop_reason"]
    if stop_reason != "RUNNING" and stop_reason not in STOP_REASON_VALUES:
        raise ManifestError(
            "Run manifest stop_reason must be RUNNING or a solver stop reason, "
            f"got {stop_reason!r}."
        )

    solver_settings = payload["solver_settings"]
    if not isinstance(solver_settings, Mapping):
        raise ManifestError("Run manifest solver_settings must be an object.")
    missing = tuple(
        field for field in _SOLVER_SETTING_FIELDS if field not in solver_settings
    )
    if missing:
        raise ManifestError(
            f"Run manifest solver_settings missing required fields: {missing!r}."
        )
    for field in _SOLVER_SETTING_FIELDS:
        _require_number(solver_settings, field, "Run solver_settings")

    analytic_cwall = payload["analytic_cwall"]
    if isinstance(analytic_cwall, bool) or not isinstance(analytic_cwall, int):
        raise ManifestError(
            "Run manifest analytic_cwall must be an integer 0 or 1, "
            f"got {analytic_cwall!r}."
        )
    if analytic_cwall not in (0, 1):
        raise ManifestError(
            "Run manifest analytic_cwall must be 0 or 1, "
            f"got {analytic_cwall!r}."
        )

    geo_id = payload["geo_id"]
    validate_campaign_geo_id(geo_id)
    expected_family = family_for_geo_id(geo_id)
    if payload["family"] != expected_family:
        raise ManifestError(
            f"Run manifest family={payload['family']!r} does not match "
            f"geo_id {geo_id!r} (expected {expected_family!r})."
        )
    validate_run_geometry_fields(payload)


def _validate_mesh_location(directory: Path, payload: Mapping[str, Any]) -> None:
    family = payload["family"]
    geo_id = payload["geo_id"]
    mesh_id = payload["mesh_id"]
    if (
        directory.name != mesh_id
        or directory.parent.name != geo_id
        or directory.parent.parent.name != family
    ):
        raise ManifestError(
            "Mesh manifest ids do not match its directory components."
        )
    try:
        expected = mesh_dir(family, geo_id, mesh_id)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if expected != directory.resolve():
        raise ManifestError(
            f"Stale mesh manifest path: expected {expected}, got {directory.resolve()}."
        )


def _validate_run_location(directory: Path, payload: Mapping[str, Any]) -> None:
    family = payload["family"]
    geo_id = payload["geo_id"]
    mesh_id = payload["mesh_id"]
    run_id = payload["run_id"]
    if (
        directory.name != run_id
        or directory.parent.name != mesh_id
        or directory.parent.parent.name != geo_id
        or directory.parent.parent.parent.name != family
    ):
        raise ManifestError(
            "Run manifest ids do not match its directory components."
        )
    try:
        expected = run_dir(family, geo_id, mesh_id, run_id)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if expected != directory.resolve():
        raise ManifestError(
            f"Stale run manifest path: expected {expected}, got {directory.resolve()}."
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Could not read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"Manifest must contain a JSON object: {path}.")
    return payload


def _changed_fields(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    protected_fields: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        field for field in protected_fields if existing[field] != incoming[field]
    )


def _changed_after_fill(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    field: str,
) -> tuple[str, ...]:
    existing_value = existing[field]
    if existing_value is not None and existing_value != incoming[field]:
        return (field,)
    return ()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> Path:
    if not path.parent.is_dir():
        raise ManifestError(f"Manifest directory does not exist: {path.parent}.")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        raise ManifestError(f"Could not write manifest {path}: {exc}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path


def upgrade_mesh_manifest_in_place(
    mesh_directory: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    """Validate and atomically write a v2 mesh manifest during migration.

    Unlike write_mesh_manifest, this does not read/validate the existing v1 file.
    """
    directory = Path(mesh_directory)
    _validate_mesh_payload(payload)
    _validate_mesh_location(directory, payload)
    return _atomic_write(directory / "manifest.json", payload)


def upgrade_run_manifest_in_place(
    run_directory: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    """Validate and atomically write a v2 run manifest during migration."""
    directory = Path(run_directory)
    _validate_run_payload(payload)
    _validate_run_location(directory, payload)
    return _atomic_write(directory / "manifest.json", payload)


def write_mesh_manifest(
    mesh_directory: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    directory = Path(mesh_directory)
    _validate_mesh_payload(payload)
    _validate_mesh_location(directory, payload)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        existing = read_mesh_manifest(directory)
        changed = (
            _changed_fields(existing, payload, _MESH_PARAMETER_FIELDS)
            + _changed_after_fill(existing, payload, "inlet_profile_G")
        )
        if changed:
            raise ManifestError(
                "Refusing to overwrite mesh manifest with changed parameters: "
                f"{changed!r}."
            )
        old_sha = existing.get("mesh_sha256")
        new_sha = payload.get("mesh_sha256")
        if old_sha and new_sha and old_sha != new_sha:
            dependents = run_dirs_referencing_mesh_sha256(
                existing["family"],
                existing["geo_id"],
                existing["mesh_id"],
                old_sha,
            )
            if dependents:
                run_ids = ", ".join(path.name for path in dependents)
                raise ManifestError(
                    "Refusing to change mesh_sha256 while run manifests still "
                    f"reference the old hash ({old_sha[:12]}…): {run_ids}. "
                    "Use a new mesh_id instead of remeshing in place."
                )
    return _atomic_write(manifest_path, payload)


def run_dirs_referencing_mesh_sha256(
    family: str,
    geo_id: str,
    mesh_id: str,
    mesh_sha256: str,
) -> tuple[Path, ...]:
    """Return run directories under this mesh whose manifest cites mesh_sha256."""
    root = runs_root() / family / geo_id / mesh_id
    if not root.is_dir():
        return ()
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        payload = read_run_manifest(child)
        if payload.get("mesh_sha256") == mesh_sha256:
            found.append(child)
    return tuple(found)


def assert_mesh_file_overwrite_allowed(
    mesh_file: str | Path,
    mesh_directory: str | Path,
    *,
    force: bool = False,
) -> None:
    """Refuse to replace a hashed .msh.h5 unless --force and no dependent runs.

    A mesh file with no manifest, or a manifest whose mesh_sha256 is still
    null, is treated as an incomplete write and is allowed to proceed.
    """
    mesh_file = Path(mesh_file)
    mesh_directory = Path(mesh_directory)
    if not mesh_file.is_file():
        return
    manifest_path = mesh_directory / "manifest.json"
    if not manifest_path.is_file():
        return
    existing = read_mesh_manifest(mesh_directory)
    current_sha = existing.get("mesh_sha256")
    if not current_sha:
        return
    if not force:
        raise ManifestError(
            "Refusing to overwrite existing mesh "
            f"{mesh_file} (mesh_sha256={current_sha[:12]}…). "
            "Pass --force only when no run manifests reference this hash. "
            "To change the mesh, use a new mesh_id."
        )
    dependents = run_dirs_referencing_mesh_sha256(
        existing["family"],
        existing["geo_id"],
        existing["mesh_id"],
        current_sha,
    )
    if dependents:
        run_ids = ", ".join(path.name for path in dependents)
        raise ManifestError(
            "Refusing --force: "
            f"{len(dependents)} run manifest(s) still reference "
            f"mesh_sha256={current_sha[:12]}… ({run_ids}). "
            "Remeshing in place would orphan those runs. Use a new mesh_id."
        )


def sync_run_manifest_analytic_cwall(run_directory: str | Path) -> int:
    """Parse case-local UDF and ensure run manifest records analytic_cwall.

    Existing runs without the field are updated from the case UDF copy.
    Raises if RO_ANALYTIC_CWALL is absent or zero.
    """
    directory = Path(run_directory)
    value = parse_ro_analytic_cwall_from_case(directory)
    if value != 1:
        raise ManifestError(
            f"RO_ANALYTIC_CWALL must be 1 for CP metrics, got {value!r} "
            f"from case UDF in {directory}."
        )
    path = directory / "manifest.json"
    if not path.is_file():
        raise ManifestError(f"Run manifest not found: {path}.")
    payload = _read_json(path)
    if payload.get("analytic_cwall") != value:
        payload["analytic_cwall"] = value
        write_run_manifest(directory, payload)
    return value


def update_run_manifest_fields(
    run_directory: str | Path,
    updates: Mapping[str, Any],
) -> Path:
    """Merge non-parameter fields onto an existing run manifest and rewrite."""
    directory = Path(run_directory)
    payload = read_run_manifest(directory)
    merged = dict(payload)
    merged.update(updates)
    return write_run_manifest(directory, merged)


def read_mesh_manifest(mesh_directory: str | Path) -> dict[str, Any]:
    directory = Path(mesh_directory)
    payload = _read_json(directory / "manifest.json")
    _validate_mesh_payload(payload)
    _validate_mesh_location(directory, payload)
    return payload


def write_run_manifest(
    run_directory: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    directory = Path(run_directory)
    _validate_run_payload(payload)
    _validate_run_location(directory, payload)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        existing = read_run_manifest(directory)
        changed = (
            _changed_fields(existing, payload, _RUN_PARAMETER_FIELDS)
            + _changed_after_fill(existing, payload, "u_mean_ms")
        )
        if changed:
            raise ManifestError(
                "Refusing to overwrite run manifest with changed parameters: "
                f"{changed!r}."
            )
    return _atomic_write(manifest_path, payload)


def read_run_manifest(run_directory: str | Path) -> dict[str, Any]:
    directory = Path(run_directory)
    payload = _read_json(directory / "manifest.json")
    _validate_run_payload(payload)
    _validate_run_location(directory, payload)
    return payload


def iter_mesh_manifests() -> Iterator[tuple[Path, dict[str, Any]]]:
    for manifest_path in sorted(meshes_root().rglob("manifest.json")):
        yield manifest_path, read_mesh_manifest(manifest_path.parent)


_RUN_SCAN_SKIP_NAMES = {
    "_inventory",
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    "post",
    "figures",
    "reports",
    "contours",
    "plots",
    "images",
    "tmp",
    "temp",
}


def _should_skip_run_scan_dir(name: str, include_hidden: bool) -> bool:
    if name.lower() in _RUN_SCAN_SKIP_NAMES:
        return True
    if not include_hidden and (name.startswith(".") or name.startswith("_")):
        return True
    return False


def _iter_child_dirs(parent: Path, *, include_hidden: bool) -> list[Path]:
    try:
        children = [path for path in parent.iterdir() if path.is_dir()]
    except OSError:
        return []
    out: list[Path] = []
    for child in sorted(children, key=lambda path: path.as_posix().lower()):
        if _should_skip_run_scan_dir(child.name, include_hidden):
            continue
        out.append(child)
    return out


def _four_level_run_dirs(
    results_root: Path,
    *,
    include_hidden: bool,
) -> list[Path]:
    """Return every ``family/geo_id/mesh_id/run_id`` leaf under results_root."""
    if not results_root.is_dir():
        raise NotADirectoryError(f"Results root is not a directory: {results_root}")
    run_dirs: list[Path] = []
    for family_dir in _iter_child_dirs(results_root, include_hidden=include_hidden):
        for geo_dir in _iter_child_dirs(family_dir, include_hidden=include_hidden):
            for mesh_directory in _iter_child_dirs(
                geo_dir, include_hidden=include_hidden
            ):
                for run_directory in _iter_child_dirs(
                    mesh_directory, include_hidden=include_hidden
                ):
                    run_dirs.append(run_directory)
    return run_dirs


def iter_run_manifests(
    results_root: str | Path | None = None,
    *,
    include_hidden: bool = False,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield ``(manifest_path, payload)`` for every four-level run directory.

    Each leaf ``family/geo_id/mesh_id/run_id`` must have a valid manifest.
    Missing, invalid, or stale manifests raise ManifestError; they are not
    skipped. Identification is the manifest payload, not directory-name parsing.
    """
    root = Path(results_root) if results_root is not None else runs_root()
    for directory in _four_level_run_dirs(root, include_hidden=include_hidden):
        yield directory / "manifest.json", read_run_manifest(directory)
