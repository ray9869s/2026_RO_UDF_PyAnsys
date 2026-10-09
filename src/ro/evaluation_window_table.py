"""Per-geometry evaluation window and the two window LMH bases.

Importing this module does not read a data root, the window table, or Fluent.
The table is ``configs/evaluation_window_table.json``. A campaign ``geo_id``
that is not in it is an error. A registered MFBO pillar id may use the
pillar ``family_defaults`` entry. Diamond, ml, sin, and empty have no
family default. ``--legacy-3-cell-window`` is the only fixed lead of 3,
and it does not read the table.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ro.geometry_registry import is_mfbo_pillar_geo_id
from ro.lmh_metrics import MS_TO_LMH
from ro.paths import project_root

LENGTH_TOL_M = 1e-9
LEGACY_N_LEAD = 3
LEGACY_WINDOW_TABLE_VERSION = "legacy-3-cell"
WINDOW_SOURCE_GEO_ID = "geo_id"
WINDOW_SOURCE_FAMILY_DEFAULT = "family_default"
WINDOW_SOURCE_LEGACY = "legacy-3-cell"
_FAMILY_DEFAULTS_KEY = "family_defaults"
_FAMILY_DEFAULT_ALLOWED = frozenset({"pillar"})
_FAMILY_DEFAULT_FORBIDDEN = frozenset({"diamond", "ml", "sin", "empty"})
_FAMILY_DEFAULT_KEYS = (
    "n_lead_excluded",
    "excluded_length_m",
    "basis",
    "date",
)
_TABLE_NAME = "evaluation_window_table.json"
_RECORD_KEYS = (
    "n_lead_excluded",
    "excluded_length_m",
    "window_length_m",
    "mesh_id",
    "date",
)


@dataclass(frozen=True)
class EvaluationWindowSelection:
    """Window written onto the run manifest and the wide summary."""

    n_lead_excluded: int
    excluded_length_m: float
    window_length_m: float
    window_table_version: str
    window_source: str

    def manifest_fields(self) -> dict[str, Any]:
        return {
            "n_lead_excluded": self.n_lead_excluded,
            "excluded_length_m": self.excluded_length_m,
            "window_length_m": self.window_length_m,
            "window_table_version": self.window_table_version,
            "window_source": self.window_source,
        }


def default_evaluation_window_table_path() -> Path:
    """Repo table. Called only when a window is selected, not at import."""
    return project_root() / "configs" / _TABLE_NAME


def apply_selection_to_config(cfg: Any, selection: EvaluationWindowSelection) -> None:
    """Overwrite the three post-config window fields. Trail stays 0."""
    cfg.n_lead_excluded = selection.n_lead_excluded
    cfg.n_trail_excluded = 0
    cfg.n_inlet_spacer_cells_excluded = selection.n_lead_excluded


def select_evaluation_window(
    *,
    geo_id: str,
    mesh_id: str,
    n_active: int,
    cell_length_x_m: float,
    legacy_3_cell_window: bool,
    table_path: Path | None = None,
) -> EvaluationWindowSelection:
    """Table row for ``geo_id``, or the legacy lead of 3 when asked."""
    if legacy_3_cell_window:
        return legacy_3_cell_selection(n_active, cell_length_x_m)
    path = (
        default_evaluation_window_table_path()
        if table_path is None
        else Path(table_path)
    )
    return selection_from_table(
        path,
        geo_id,
        mesh_id=mesh_id,
        n_active=n_active,
        cell_length_x_m=cell_length_x_m,
    )


def legacy_3_cell_selection(
    n_active: int,
    cell_length_x_m: float,
) -> EvaluationWindowSelection:
    """Fixed lead of 3. Does not read the table."""
    _require_count("n_active", n_active)
    if n_active <= LEGACY_N_LEAD:
        raise ValueError(
            "legacy 3-cell window needs n_active > 3, "
            f"got n_active={n_active}."
        )
    dx = _require_positive_length("cell_length_x_m", cell_length_x_m)
    return EvaluationWindowSelection(
        n_lead_excluded=LEGACY_N_LEAD,
        excluded_length_m=LEGACY_N_LEAD * dx,
        window_length_m=(n_active - LEGACY_N_LEAD) * dx,
        window_table_version=LEGACY_WINDOW_TABLE_VERSION,
        window_source=WINDOW_SOURCE_LEGACY,
    )


def selection_from_table(
    path: Path,
    geo_id: str,
    *,
    mesh_id: str,
    n_active: int,
    cell_length_x_m: float,
) -> EvaluationWindowSelection:
    """One geometry's row, checked against this mesh's pitch."""
    table = load_evaluation_window_table(path)
    defaults = _family_defaults(table, path)
    records = {
        key: value
        for key, value in table.items()
        if key != _FAMILY_DEFAULTS_KEY
    }
    if geo_id in records:
        return _selection_from_geo_record(
            records[geo_id],
            geo_id,
            mesh_id=mesh_id,
            n_active=n_active,
            cell_length_x_m=cell_length_x_m,
        )
    if is_mfbo_pillar_geo_id(geo_id):
        if "pillar" not in defaults:
            raise KeyError(
                f"geo_id {geo_id!r} is not in {path}, and pillar has no "
                "family_defaults entry."
            )
        return _selection_from_family_default(
            defaults["pillar"],
            geo_id,
            n_active=n_active,
            cell_length_x_m=cell_length_x_m,
        )
    raise KeyError(f"geo_id {geo_id!r} is not in {path}.")


def _selection_from_geo_record(
    record,
    geo_id,
    *,
    mesh_id,
    n_active,
    cell_length_x_m,
):
    if not isinstance(record, dict):
        raise ValueError(
            f"evaluation window record for {geo_id!r} must be an object, "
            f"got {type(record).__name__}."
        )
    missing = [key for key in _RECORD_KEYS if key not in record]
    if missing:
        raise ValueError(
            f"evaluation window record for {geo_id!r} is missing {missing}."
        )
    n_lead = record["n_lead_excluded"]
    _require_count("n_lead_excluded", n_lead)
    _require_count("n_active", n_active)
    if n_lead >= n_active:
        raise ValueError(
            f"n_lead_excluded={n_lead} must be < n_active={n_active} "
            f"for {geo_id!r}."
        )
    if record["mesh_id"] != mesh_id:
        raise ValueError(
            f"evaluation window table mesh_id {record['mesh_id']!r} "
            f"does not match run mesh_id {mesh_id!r} for {geo_id!r}."
        )
    dx = _require_positive_length("cell_length_x_m", cell_length_x_m)
    excluded = _require_length("excluded_length_m", record["excluded_length_m"])
    window = _require_positive_length("window_length_m", record["window_length_m"])
    _require_close(geo_id, "excluded_length_m", excluded, n_lead * dx)
    _require_close(geo_id, "window_length_m", window, (n_active - n_lead) * dx)
    version = record["date"]
    if not isinstance(version, str) or version.strip() == "":
        raise ValueError(
            f"evaluation window date for {geo_id!r} must be a non-empty "
            f"string, got {version!r}."
        )
    return EvaluationWindowSelection(
        n_lead_excluded=n_lead,
        excluded_length_m=excluded,
        window_length_m=window,
        window_table_version=version.strip(),
        window_source=WINDOW_SOURCE_GEO_ID,
    )


def _selection_from_family_default(
    record,
    geo_id,
    *,
    n_active,
    cell_length_x_m,
):
    if not isinstance(record, dict):
        raise ValueError(
            f"pillar family default must be an object, got {type(record).__name__}."
        )
    missing = [key for key in _FAMILY_DEFAULT_KEYS if key not in record]
    if missing:
        raise ValueError(f"pillar family default is missing {missing}.")
    n_lead = record["n_lead_excluded"]
    _require_count("n_lead_excluded", n_lead)
    _require_count("n_active", n_active)
    if n_lead >= n_active:
        raise ValueError(
            f"n_lead_excluded={n_lead} must be < n_active={n_active} "
            f"for {geo_id!r}."
        )
    basis = record["basis"]
    if not isinstance(basis, str) or basis.strip() == "":
        raise ValueError(
            f"pillar family default basis must be a non-empty string, got {basis!r}."
        )
    version = record["date"]
    if not isinstance(version, str) or version.strip() == "":
        raise ValueError(
            f"pillar family default date must be a non-empty string, got {version!r}."
        )
    dx = _require_positive_length("cell_length_x_m", cell_length_x_m)
    excluded = _require_length("excluded_length_m", record["excluded_length_m"])
    _require_close(geo_id, "excluded_length_m", excluded, n_lead * dx)
    return EvaluationWindowSelection(
        n_lead_excluded=n_lead,
        excluded_length_m=excluded,
        window_length_m=(n_active - n_lead) * dx,
        window_table_version=version.strip(),
        window_source=WINDOW_SOURCE_FAMILY_DEFAULT,
    )


def _family_defaults(table: Mapping[str, Any], path: Path) -> dict[str, Any]:
    if _FAMILY_DEFAULTS_KEY not in table:
        return {}
    raw = table[_FAMILY_DEFAULTS_KEY]
    if not isinstance(raw, dict):
        raise ValueError(
            "family_defaults must be an object, "
            f"got {type(raw).__name__} in {path}."
        )
    refused = [
        name
        for name in raw
        if name in _FAMILY_DEFAULT_FORBIDDEN or name not in _FAMILY_DEFAULT_ALLOWED
    ]
    if refused:
        raise ValueError(
            "family_defaults has no entry for "
            + ", ".join(sorted(refused))
            + ". Campaign diamond, ml, sin, and empty geometries need their own "
            "geo_id row."
        )
    return raw


def load_evaluation_window_table(path: Path) -> dict[str, Any]:
    """Read the geo_id map. A missing file is an error."""
    table_path = Path(path)
    if not table_path.is_file():
        raise FileNotFoundError(f"evaluation window table not found: {table_path}")
    try:
        payload = json.loads(table_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"evaluation window table is not valid JSON: {table_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "evaluation window table must be a JSON object of geo_id records, "
            f"got {type(payload).__name__}."
        )
    return payload


def window_global_cell_numbers(
    n_buffer_in: int,
    n_active: int,
    n_lead_excluded: int,
) -> list[int]:
    """Global 1-based cells after the lead exclusion. Trail exclusion is 0."""
    _require_count("n_buffer_in", n_buffer_in)
    _require_count("n_active", n_active)
    _require_count("n_lead_excluded", n_lead_excluded)
    if n_buffer_in < 1:
        raise ValueError(f"n_buffer_in must be >= 1, got {n_buffer_in}.")
    if n_lead_excluded >= n_active:
        raise ValueError(
            f"n_lead_excluded={n_lead_excluded} must be < n_active={n_active}."
        )
    first = n_buffer_in + n_lead_excluded + 1
    last = n_buffer_in + n_active
    return list(range(first, last + 1))


def window_lmh(
    jw_m_per_s: Sequence[float],
    membrane_area_m2: Sequence[float],
    window_length_m: float,
    periodic_shift_y_m: float,
) -> tuple[float, float]:
    """Exposed-area and module-area window LMH.

    exposed = sum(Jw_i * A_mem_i) / sum(A_mem_i) * 3.6e6
    module  = sum(Jw_i * A_mem_i) / (2 * L_window * periodic_shift_y_m) * 3.6e6
    """
    fluxes = list(jw_m_per_s)
    areas = list(membrane_area_m2)
    if len(fluxes) == 0 or len(fluxes) != len(areas):
        raise ValueError(
            "window LMH needs one finite flux and one positive area per "
            f"window cell, got {len(fluxes)} fluxes and {len(areas)} areas."
        )
    flux_area = 0.0
    area_sum = 0.0
    for index, (jw, area) in enumerate(zip(fluxes, areas)):
        jw_value = _require_finite(f"Jw[{index}]", jw)
        area_value = _require_positive_length(f"A_mem[{index}]", area)
        flux_area += jw_value * area_value
        area_sum += area_value
    if area_sum <= 0.0:
        raise ValueError("exposed-area window LMH denominator must be > 0.")
    length = _require_positive_length("window_length_m", window_length_m)
    shift = _require_positive_length("periodic_shift_y_m", periodic_shift_y_m)
    module_area = 2.0 * length * shift
    exposed = flux_area / area_sum * MS_TO_LMH
    module = flux_area / module_area * MS_TO_LMH
    return exposed, module


def per_cell_flux_and_area(
    wide: Mapping[str, Any],
    cells: Sequence[int],
) -> tuple[list[float], list[float]]:
    """Read ``pp_jw_m_per_s_cell_N`` and ``pp_membrane_area_cell_N_m2``."""
    missing: list[str] = []
    fluxes: list[float] = []
    areas: list[float] = []
    for cell in cells:
        jw_key = f"pp_jw_m_per_s_cell_{cell}"
        area_key = f"pp_membrane_area_cell_{cell}_m2"
        jw = wide.get(jw_key) if jw_key in wide else None
        area = wide.get(area_key) if area_key in wide else None
        if jw is None or (isinstance(jw, str) and jw.strip() == ""):
            missing.append(jw_key)
        else:
            fluxes.append(jw)
        if area is None or (isinstance(area, str) and area.strip() == ""):
            missing.append(area_key)
        else:
            areas.append(area)
    if missing:
        raise KeyError("missing per-cell columns: " + ", ".join(missing))
    return fluxes, areas


def _require_count(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {value!r}.")


def _require_finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError as exc:
                raise ValueError(f"{name} must be finite, got {value!r}.") from exc
        else:
            raise TypeError(f"{name} must be a float, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return number


def _require_length(name: str, value: Any) -> float:
    number = _require_finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0, got {value!r}.")
    return number


def _require_positive_length(name: str, value: Any) -> float:
    number = _require_finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be > 0, got {value!r}.")
    return number


def _require_close(geo_id: str, name: str, actual: float, expected: float) -> None:
    if abs(actual - expected) > LENGTH_TOL_M:
        raise ValueError(
            f"{name}={actual!r} for {geo_id!r} is not n_lead or window "
            f"times cell_length_x_m ({expected!r}) within {LENGTH_TOL_M} m."
        )
