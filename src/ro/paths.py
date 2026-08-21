"""Canonical project and RO data-tree paths."""

from __future__ import annotations

import os
import re
from pathlib import Path


FAMILY_RE = re.compile(r"^(?:diamond|ml|pillar|sin|empty)$")
GEO_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")
MESH_ID_RE = re.compile(r"^max\d{3}_min\d{3}_cpg\d+_bl\d+$")
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
    return project_root() / "My_CFD_Project" / "01_Templates"


def udfs_dir() -> Path:
    return project_root() / "My_CFD_Project" / "02_UDFs"


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
