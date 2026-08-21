"""Validated mesh and run manifests for the external RO data tree."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ro.paths import mesh_dir, meshes_root, run_dir, runs_root
from ro.solver_common import STOP_REASON_VALUES


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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
        "max_size",
        "min_size",
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
    for field in ("membrane_wall_base_names", "buffer_wall_base_names"):
        _require_string_list(payload, field, "Mesh")

    positive_fields = ("filament_d_m", "cell_length_x_m", "max_size", "min_size")
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
    if float(payload["min_size"]) > float(payload["max_size"]):
        raise ManifestError("Mesh manifest min_size must be <= max_size.")
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


def iter_run_manifests() -> Iterator[tuple[Path, dict[str, Any]]]:
    for manifest_path in sorted(runs_root().rglob("manifest.json")):
        yield manifest_path, read_run_manifest(manifest_path.parent)
