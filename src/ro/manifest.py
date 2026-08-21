"""Validated mesh and run manifests for the external RO data tree."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ro.paths import mesh_dir, meshes_root, run_dir, runs_root


class ManifestError(ValueError):
    """Raised when a manifest is invalid, stale, or unsafe to overwrite."""


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
    "membrane_wall_base_names",
    "buffer_wall_base_names",
    "n_lead_excluded",
    "n_trail_excluded",
    "max_size",
    "min_size",
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
)

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
    "solver_settings",
    "stop_reason",
    "created_utc",
)

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
    "membrane_wall_base_names",
    "buffer_wall_base_names",
    "n_lead_excluded",
    "n_trail_excluded",
    "max_size",
    "min_size",
    "cpg",
    "bl",
    "peel",
)

_RUN_PARAMETER_FIELDS = (
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
    "solver_settings",
)

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


def _require_fields(
    payload: Mapping[str, Any],
    required_fields: tuple[str, ...],
    kind: str,
) -> None:
    missing = tuple(field for field in required_fields if field not in payload)
    if missing:
        raise ManifestError(f"{kind} manifest missing required fields: {missing!r}.")
    if payload["schema_version"] != 1:
        raise ManifestError(
            f"Unsupported {kind} manifest schema_version: "
            f"{payload['schema_version']!r}."
        )


def _require_number(payload: Mapping[str, Any], field: str, kind: str) -> None:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(
            f"{kind} manifest field {field!r} must be numeric, got {value!r}."
        )


def _validate_mesh_payload(payload: Mapping[str, Any]) -> None:
    _require_fields(payload, MESH_MANIFEST_REQUIRED_FIELDS, "Mesh")
    mesh_sha256 = payload["mesh_sha256"]
    if mesh_sha256 is not None:
        if not isinstance(mesh_sha256, str) or not mesh_sha256:
            raise ManifestError("Mesh manifest mesh_sha256 must be null or non-empty.")
        missing_quality = tuple(
            field for field in _MESH_QUALITY_FIELDS if payload[field] is None
        )
        if missing_quality:
            raise ManifestError(
                "Mesh quality fields may not be null once mesh_sha256 is set: "
                f"{missing_quality!r}."
            )
    inlet_profile_g = payload["inlet_profile_G"]
    if inlet_profile_g is not None:
        _require_number(payload, "inlet_profile_G", "Mesh")


def _validate_run_payload(payload: Mapping[str, Any]) -> None:
    _require_fields(payload, RUN_MANIFEST_REQUIRED_FIELDS, "Run")
    for field in ("u_mean_ms", "p_gauge_pa", "u_target_ms"):
        _require_number(payload, field, "Run")

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
        changed = _changed_fields(existing, payload, _MESH_PARAMETER_FIELDS)
        if changed:
            raise ManifestError(
                "Refusing to overwrite mesh manifest with changed parameters: "
                f"{changed!r}."
            )
    return _atomic_write(manifest_path, payload)


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
        changed = _changed_fields(existing, payload, _RUN_PARAMETER_FIELDS)
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


def iter_run_manifests() -> Iterator[tuple[Path, dict[str, Any]]]:
    for manifest_path in sorted(runs_root().rglob("manifest.json")):
        yield manifest_path, read_run_manifest(manifest_path.parent)
