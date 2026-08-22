"""Canonical project and RO data-tree paths."""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any


FAMILY_RE = re.compile(r"^(?:diamond|ml|pillar|sin|empty)$")
GEO_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")
MESH_ID_RE = re.compile(r"^max\d{3}_min\d{3}_cpg\d+_bl\d+_peel\d+$")
RUN_ID_RE = re.compile(r"^u\d+p\d+_p\d+M$")
_GEO_ID_FORBIDDEN = re.compile(r"(?:_brg\d+|_\d+c)(?:_|$)")

_PROJECT_ROOT_ENV = "PYFLUENT_PROJECT_ROOT"
_DATA_ROOT_ENV = "RO_DATA_ROOT"


def project_root() -> Path:
    """Return the configured project root or discover it from this module."""
    override = os.environ.get(_PROJECT_ROOT_ENV)
    if override:
        return Path(override)

    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("Could not locate project root containing pyproject.toml.")


def data_root() -> Path:
    """Return the absolute external data root."""
    value = os.environ.get(_DATA_ROOT_ENV)
    if not value:
        raise ValueError("RO_DATA_ROOT must be set to an absolute path.")
    root = Path(value)
    if not root.is_absolute():
        raise ValueError(f"RO_DATA_ROOT must be absolute, got {value!r}.")
    return root


def templates_dir() -> Path:
    return project_root() / "templates"


def udfs_dir() -> Path:
    return project_root() / "udfs"


def geometries_root() -> Path:
    return data_root() / "geometries"


def meshes_root() -> Path:
    return data_root() / "meshes"


def runs_root() -> Path:
    return data_root() / "runs"


def _validate_family(family: str) -> None:
    if not isinstance(family, str) or FAMILY_RE.fullmatch(family) is None:
        raise ValueError(f"Invalid family: {family!r}.")


def _validate_geo_id(geo_id: str) -> None:
    if (
        not isinstance(geo_id, str)
        or len(geo_id) > 64
        or GEO_ID_RE.fullmatch(geo_id) is None
        or _GEO_ID_FORBIDDEN.search(geo_id) is not None
    ):
        raise ValueError(f"Invalid geo_id: {geo_id!r}.")


def _validate_mesh_id(mesh_id: str) -> None:
    if not isinstance(mesh_id, str) or MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"Invalid mesh_id: {mesh_id!r}.")


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")


def geometry_dir(family: str, geo_id: str) -> Path:
    _validate_family(family)
    _validate_geo_id(geo_id)
    return geometries_root() / family / geo_id


def mesh_dir(family: str, geo_id: str, mesh_id: str) -> Path:
    _validate_family(family)
    _validate_geo_id(geo_id)
    _validate_mesh_id(mesh_id)
    return meshes_root() / family / geo_id / mesh_id


def run_dir(family: str, geo_id: str, mesh_id: str, run_id: str) -> Path:
    _validate_family(family)
    _validate_geo_id(geo_id)
    _validate_mesh_id(mesh_id)
    _validate_run_id(run_id)
    return runs_root() / family / geo_id / mesh_id / run_id


def complete_run_identity(
    family: str | None,
    geo_id: str | None,
    mesh_id: str | None,
    run_id: str | None,
) -> tuple[str, str, str, str] | None:
    """Return the four ids when every selector is a non-empty string."""
    if family and geo_id and mesh_id and run_id:
        return family, geo_id, mesh_id, run_id
    return None


def require_existing_run(
    family: str, geo_id: str, mesh_id: str, run_id: str
) -> Path:
    """Return ``run_dir(...)`` or raise if that leaf has no manifest."""
    directory = run_dir(family, geo_id, mesh_id, run_id)
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"No run manifest at {manifest}.")
    return directory


def resolve_selected_run_directory(
    *,
    family: str | None = None,
    geo_id: str | None = None,
    mesh_id: str | None = None,
    run_id: str | None = None,
    case_path: str | Path | None = None,
) -> Path:
    """Locate a run from the four ids or an explicit ``case_path``.

    ``geo_name`` / ``case_name`` are filename labels and are not accepted here.
    """
    if case_path not in (None, ""):
        return Path(case_path)
    identity = complete_run_identity(family, geo_id, mesh_id, run_id)
    if identity is None:
        raise ValueError(
            "Cannot locate the run directory. Pass --family --geo-id "
            "--mesh-id --run-id, or set case_path. --geo-name/--case-name "
            "are filename labels only."
        )
    return require_existing_run(*identity)


def _filter_values(value: str | Collection[str] | None) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {value} if value else None
    filtered = {item for item in value if item}
    return filtered or None


def record_matches_id_filters(
    record: Mapping[str, Any],
    *,
    family: str | Collection[str] | None = None,
    geo_id: str | Collection[str] | None = None,
    mesh_id: str | Collection[str] | None = None,
    run_id: str | Collection[str] | None = None,
) -> bool:
    """True when ``record`` matches every provided id filter."""
    checks = (
        ("family", family),
        ("geo_id", geo_id),
        ("mesh_id", mesh_id),
        ("run_id", run_id),
    )
    for key, wanted in checks:
        allowed = _filter_values(wanted)
        if allowed is None:
            continue
        if str(record.get(key, "")) not in allowed:
            return False
    return True


def any_id_filter(
    *,
    family: str | Collection[str] | None = None,
    geo_id: str | Collection[str] | None = None,
    mesh_id: str | Collection[str] | None = None,
    run_id: str | Collection[str] | None = None,
) -> bool:
    return any(
        _filter_values(value) is not None
        for value in (family, geo_id, mesh_id, run_id)
    )
