"""Result paths for the 2D pilot.

Data layout, under an absolute ``RO_2D_DATA_ROOT`` that is not the git
repository and not the 3D campaign tree::

    geometries/{geo_id}/geometry.json
    meshes/{geo_id}/{fidelity}/mesh_spec.json
    runs/{geo_id}/{fidelity}/{run_id}/result.json

Folder names are labels. Parameters are read from the JSON files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ro.paths import project_root
from ro.solver_common import reject_windows_drive_paths_on_non_windows
from ro_2d_pilot.config import RUN_LABELS

DATA_ROOT_ENV = "RO_2D_DATA_ROOT"
_CAMPAIGN_ROOT_ENV = "RO_DATA_ROOT"
_CAMPAIGN_TOP_LEVEL = frozenset(
    {"geometries", "meshes", "runs", "inventory", "archive"}
)

_GEO_ID_RE_TEXT = (
    r"^d\d+p\d{6}mm_L\d+p\d{6}mm_h\d+p\d{6}mm_n[1-9]\d*$"
)
_FIDELITY_NAMES = frozenset(RUN_LABELS)


def campaign_root_from_env() -> Path | None:
    """Absolute 3D data root, if one is configured."""
    raw = os.environ.get(_CAMPAIGN_ROOT_ENV)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    return path


def assert_data_root_allowed(
    root: Path,
    *,
    host_os_name: str | None = None,
    project: Path | None = None,
    campaign_root: Path | None = None,
) -> Path:
    """Return ``root`` when it is an absolute path safe to write 2D cases into."""
    if not isinstance(root, Path):
        root = Path(root)
    reject_windows_drive_paths_on_non_windows(
        [root],
        host_os_name=host_os_name,
    )
    if not root.is_absolute():
        raise ValueError(
            f"{DATA_ROOT_ENV} must be absolute, got {str(root)!r}."
        )
    project_path = project_root() if project is None else project
    resolved = root.resolve()
    project_resolved = project_path.resolve()
    if resolved == project_resolved or project_resolved in resolved.parents:
        raise ValueError(
            f"{DATA_ROOT_ENV} must not point inside the git repository "
            f"({project_resolved})."
        )
    if campaign_root is not None:
        _refuse_campaign_tree(resolved, campaign_root)
    return root


def data_root(*, host_os_name: str | None = None) -> Path:
    """Return the absolute 2D data root. Never defaults to the repository."""
    value = os.environ.get(DATA_ROOT_ENV)
    if not value:
        raise ValueError(
            f"{DATA_ROOT_ENV} must be set to an absolute path."
        )
    return assert_data_root_allowed(
        Path(value),
        host_os_name=host_os_name,
        campaign_root=campaign_root_from_env(),
    )


def _refuse_campaign_tree(root: Path, campaign_root: Path) -> None:
    campaign = campaign_root.resolve()
    if root == campaign:
        raise ValueError(
            f"{DATA_ROOT_ENV} must not be the 3D {_CAMPAIGN_ROOT_ENV}."
        )
    try:
        relative = root.relative_to(campaign)
    except ValueError:
        return
    first = relative.parts[0] if relative.parts else ""
    if first in _CAMPAIGN_TOP_LEVEL:
        raise ValueError(
            f"{DATA_ROOT_ENV} must not sit inside 3D {first}/ "
            f"({campaign})."
        )


def validate_geo_id(geo_id: str) -> str:
    if not isinstance(geo_id, str) or re.fullmatch(_GEO_ID_RE_TEXT, geo_id) is None:
        raise ValueError(f"Invalid 2D geo_id: {geo_id!r}.")
    return geo_id


def validate_fidelity(fidelity: str) -> str:
    if fidelity not in _FIDELITY_NAMES:
        raise ValueError(f"Invalid fidelity: {fidelity!r}.")
    return fidelity


def validate_run_id(run_id: str) -> str:
    from ro.paths import RUN_ID_RE

    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")
    return run_id


def geometry_dir(root: Path, geo_id: str) -> Path:
    return root / "geometries" / validate_geo_id(geo_id)


def mesh_dir(root: Path, geo_id: str, fidelity: str) -> Path:
    return (
        root
        / "meshes"
        / validate_geo_id(geo_id)
        / validate_fidelity(fidelity)
    )


def run_dir(root: Path, geo_id: str, fidelity: str, run_id: str) -> Path:
    return (
        root
        / "runs"
        / validate_geo_id(geo_id)
        / validate_fidelity(fidelity)
        / validate_run_id(run_id)
    )


def mesh_file(root: Path, geo_id: str, fidelity: str) -> Path:
    return mesh_dir(root, geo_id, fidelity) / (
        f"{validate_geo_id(geo_id)}_{validate_fidelity(fidelity)}.msh"
    )


def relative_geometry_dir(geo_id: str) -> str:
    return f"geometries/{validate_geo_id(geo_id)}"


def relative_mesh_dir(geo_id: str, fidelity: str) -> str:
    return (
        f"meshes/{validate_geo_id(geo_id)}/{validate_fidelity(fidelity)}"
    )


def relative_run_dir(geo_id: str, fidelity: str, run_id: str) -> str:
    return (
        f"runs/{validate_geo_id(geo_id)}/"
        f"{validate_fidelity(fidelity)}/{validate_run_id(run_id)}"
    )
