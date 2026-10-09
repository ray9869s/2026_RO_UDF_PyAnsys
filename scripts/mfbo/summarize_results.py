"""Build one results table from a data-root run tree.

Reported LMH (``lmh_mass_balance``) is per exposed membrane area
(``area_mem``). ``lmh_module_area`` rescales that value onto the periodic
module area of both membranes:

    A_module = 2 * n_active_cells * cell_length_x_m * periodic_shift_y_m
    lmh_module_area = lmh_mass_balance * area_mem / A_module

``n_active_cells``, ``cell_length_x_m``, and ``periodic_shift_y_m`` come
from the mesh manifest. ``area_mem`` and ``lmh_mass_balance`` come from
``post/reports/summary_metrics_wide.csv``. If any input is absent,
``lmh_module_area`` is ``MISSING``.

``lmh_window_exposed`` and ``lmh_window_module`` are the evaluation-window
LMH on the exposed-membrane and module-area bases. When a leaf already
stores them, they are copied. A leaf extracted before those columns
existed is recomputed from its per-cell ``pp_jw_m_per_s_cell_N`` and
``pp_membrane_area_cell_N_m2`` columns and
``configs/evaluation_window_table.json``, and the notes say
``recomputed_from_cells``. ``n_lead_excluded``, ``excluded_length_m``,
``window_length_m``, and ``window_table_version`` are copied from the
wide summary, or filled from that table when the summary does not have
them. A missing input is ``MISSING``.

Monitor columns are read from ``lmh_udm_avg.out`` and
``pressure_drop_spacer.out`` on the run leaf. They are never written
into ``lmh_mass_balance``, ``lmh_window_module``, or the other converged
columns. ``report_status`` is ``converged`` when the gate is PASS,
``steady_not_converged`` when the gate is FAIL, the LMH monitor has at
least 1000 iterations, and the last-500 LMH range is under 0.2%, and
``failed`` otherwise.

Dimensionless columns (definitions in ``docs/metrics_conventions.md``)
are derived here from those same inputs plus the run manifest
``u_target_ms``. Hydraulic diameter is ``4 V / (A_mem + A_spacer)``
from the active-window columns on the wide CSV. The Schock–Miquel
diameter is a cross-check column only. A missing input leaves that
column ``MISSING``.

Importing this module does not launch Fluent and does not read a data root.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.evaluation_window_table import (  # noqa: E402
    default_evaluation_window_table_path,
    per_cell_flux_and_area,
    selection_from_table,
    window_global_cell_numbers,
    window_lmh,
)
_MONITOR_PATH = Path(__file__).resolve().parent / "monitor_periodicity.py"
_monitor_spec = importlib.util.spec_from_file_location(
    "mfbo_monitor_periodicity",
    _MONITOR_PATH,
)
_monitor_periodicity = importlib.util.module_from_spec(_monitor_spec)
_monitor_spec.loader.exec_module(_monitor_periodicity)

from ro.active_window_geometry import (  # noqa: E402
    geometric_hydraulic_diameter_m,
    schock_miquel_hydraulic_diameter_m,
)

MISSING = "MISSING"
NOT_RECORDED = "not recorded"

# Campaign fluid properties. The solver records these and does not read
# them back from the case; see docs/metrics_conventions.md.
RHO_KG_M3 = 998.2
MU_PA_S = 8.93e-4
DIFFUSIVITY_M2_S = 2.0e-9
MS_TO_LMH = 3.6e6
MONITOR_LAST_N = 500
STEADY_MIN_ITERATIONS = 1000
STEADY_LMH_RANGE_PCT_MAX = 0.2

COLUMNS = (
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
    "porosity_eps",
    "y1_window_median_um",
    "stop_reason",
    "continuity_final",
    "convergence_quality",
    "report_status",
    "monitor_lmh_udm_mean_last500",
    "monitor_lmh_udm_range_pct_last500",
    "monitor_dp_mean_last500",
    "monitor_dp_range_pct_last500",
    "monitor_iterations",
    "viscous_model",
    "lmh_mass_balance",
    "lmh_window_exposed",
    "lmh_window_module",
    "lmh_module_area",
    "n_lead_excluded",
    "excluded_length_m",
    "window_length_m",
    "window_table_version",
    "pressure_drop_spacer_per_m",
    "cp_canon_window_avg",
    "cpc_window_avg_flux",
    "cp_q99_window_flux",
    "cp_q999_window_flux",
    "hydraulic_diameter_m",
    "hydraulic_diameter_schock_miquel_m",
    "re_h",
    "sc",
    "sh_cpc_flux",
    "fanning_friction_factor",
    "darcy_friction_factor",
    "specific_power_dissipation_w_per_kg",
    "viscosity_ratio_volavg",
    "diff_ratio_volavg",
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
    "processor_count",
    "notes",
)

_MESH_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
    "porosity_eps",
    "mesh_wall_time_s",
)
_RUN_FIELDS = (
    "stop_reason",
    "continuity_final",
    "convergence_quality",
    "solver_wall_time_s",
    "extraction_wall_time_s",
)
_CSV_FIELDS = (
    "y1_window_median_um",
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cp_canon_window_avg",
    "cpc_window_avg_flux",
    "cp_q99_window_flux",
    "cp_q999_window_flux",
    "viscosity_ratio_volavg",
    "diff_ratio_volavg",
)
_MODULE_AREA_INPUTS = (
    "lmh_mass_balance",
    "area_mem",
    "n_active_cells",
    "cell_length_x_m",
    "periodic_shift_y_m",
)


def module_area_m2(n_active_cells, cell_length_x_m, periodic_shift_y_m):
    """Projected area of both membranes over the active cells, in m^2."""
    return (
        2.0
        * float(n_active_cells)
        * float(cell_length_x_m)
        * float(periodic_shift_y_m)
    )


def lmh_per_module_area(
    lmh_mass_balance,
    area_mem,
    n_active_cells,
    cell_length_x_m,
    periodic_shift_y_m,
):
    """Rescale exposed-membrane LMH onto ``A_module``.

    ``lmh_mass_balance`` is per exposed membrane area (``area_mem``).
    """
    area = module_area_m2(n_active_cells, cell_length_x_m, periodic_shift_y_m)
    if area == 0.0:
        raise ZeroDivisionError("A_module is 0.")
    return float(lmh_mass_balance) * float(area_mem) / area


def _positive(value):
    number = _finite_number(value)
    if number is None or number <= 0.0:
        return None
    return number


def _nonnegative(value):
    number = _finite_number(value)
    if number is None or number < 0.0:
        return None
    return number


def hydraulic_diameter_m(fluid_volume_m3, membrane_area_m2, spacer_area_m2):
    """d_h = 4 V / (A_membrane + A_spacer) from the active window [m]."""
    return geometric_hydraulic_diameter_m(
        fluid_volume_m3,
        membrane_area_m2,
        spacer_area_m2,
    )


def hydraulic_diameter_schock_miquel_m(
    porosity,
    channel_height_m,
    spacer_area_m2,
    box_volume_m3,
):
    """Schock–Miquel cross-check [m]. Not the primary diameter."""
    return schock_miquel_hydraulic_diameter_m(
        porosity,
        channel_height_m,
        spacer_area_m2,
        box_volume_m3,
    )


def reynolds_h(velocity_m_s, hydraulic_diameter, *, rho=RHO_KG_M3, mu=MU_PA_S):
    """Re_h = ρ u d_h / μ with superficial velocity. None if an input is unusable."""
    velocity = _positive(velocity_m_s)
    diameter = _positive(hydraulic_diameter)
    if velocity is None or diameter is None or rho <= 0.0 or mu <= 0.0:
        return None
    return rho * velocity * diameter / mu


def schmidt_number(*, rho=RHO_KG_M3, mu=MU_PA_S, diffusivity=DIFFUSIVITY_M2_S):
    """Sc = μ / (ρ D)."""
    if rho <= 0.0 or mu <= 0.0 or diffusivity <= 0.0:
        return None
    return mu / (rho * diffusivity)


def film_mass_transfer_coefficient_m_s(lmh, cp_modulus):
    """k = Jw / ln(CP), with Jw = LMH / 3.6e6. None when CP <= 1 or LMH < 0."""
    flux = _finite_number(lmh)
    modulus = _finite_number(cp_modulus)
    if flux is None or modulus is None or flux < 0.0 or modulus <= 1.0:
        return None
    return (flux / MS_TO_LMH) / math.log(modulus)


def sherwood_number(
    lmh,
    cp_modulus,
    hydraulic_diameter,
    *,
    diffusivity=DIFFUSIVITY_M2_S,
):
    """Sh = k d_h / D from the film coefficient."""
    coefficient = film_mass_transfer_coefficient_m_s(lmh, cp_modulus)
    diameter = _positive(hydraulic_diameter)
    if coefficient is None or diameter is None or diffusivity <= 0.0:
        return None
    return coefficient * diameter / diffusivity


def fanning_friction_factor(
    pressure_drop_per_m,
    hydraulic_diameter,
    velocity_m_s,
    *,
    rho=RHO_KG_M3,
):
    """f_Fanning = (dP/L) d_h / (2 ρ u^2)."""
    gradient = _finite_number(pressure_drop_per_m)
    diameter = _positive(hydraulic_diameter)
    velocity = _positive(velocity_m_s)
    if gradient is None or diameter is None or velocity is None or rho <= 0.0:
        return None
    return gradient * diameter / (2.0 * rho * velocity * velocity)


def darcy_friction_factor(
    pressure_drop_per_m,
    hydraulic_diameter,
    velocity_m_s,
    *,
    rho=RHO_KG_M3,
):
    """f_Darcy = 4 f_Fanning = (dP/L) d_h / (ρ u^2 / 2)."""
    fanning = fanning_friction_factor(
        pressure_drop_per_m,
        hydraulic_diameter,
        velocity_m_s,
        rho=rho,
    )
    if fanning is None:
        return None
    return 4.0 * fanning


def specific_power_dissipation_w_per_kg(
    pressure_drop_per_m,
    velocity_m_s,
    *,
    rho=RHO_KG_M3,
):
    """Pumping power per unit mass, u (dP/L) / ρ, superficial velocity [W/kg]."""
    gradient = _finite_number(pressure_drop_per_m)
    velocity = _positive(velocity_m_s)
    if gradient is None or velocity is None or rho <= 0.0:
        return None
    return velocity * gradient / rho


def _format_metric(value):
    if value is None:
        return MISSING
    return format(float(value), ".12g")


def _optional(mapping, key):
    if mapping is None or key not in mapping:
        return None
    return mapping[key]


def _geometric_diameter_gap(fluid_volume_m3, membrane_area_m2, spacer_area_m2):
    missing = []
    if _positive(fluid_volume_m3) is None:
        missing.append("active_window_fluid_volume_m3")
    if _positive(membrane_area_m2) is None:
        missing.append("active_window_membrane_area_m2")
    if _nonnegative(spacer_area_m2) is None:
        missing.append("active_window_spacer_area_m2")
    return missing


def _schock_diameter_m(mesh_manifest, wide):
    """Schock–Miquel diameter from the stored active-window columns.

    Channel height is the layout box divided by active length times
    periodic width, so it is the height the extract used for that box.
    """
    spacer = _optional(wide, "active_window_spacer_area_m2")
    box = _optional(wide, "active_window_box_volume_m3")
    porosity = _optional(wide, "active_window_porosity")
    n_active = _optional(mesh_manifest, "n_active_cells")
    pitch = _optional(mesh_manifest, "cell_length_x_m")
    width = _optional(mesh_manifest, "periodic_shift_y_m")
    try:
        length = float(n_active) * float(pitch)
        width_m = float(width)
        box_m = float(box)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(length)
        or not math.isfinite(width_m)
        or not math.isfinite(box_m)
        or length <= 0.0
        or width_m <= 0.0
        or box_m <= 0.0
    ):
        return None
    height = box_m / (length * width_m)
    return hydraulic_diameter_schock_miquel_m(porosity, height, spacer, box_m)


def _dimensionless_fields(mesh_manifest, wide, run_manifest, notes):
    volume = _optional(wide, "active_window_fluid_volume_m3")
    membrane_area = _optional(wide, "active_window_membrane_area_m2")
    spacer_area = _optional(wide, "active_window_spacer_area_m2")
    velocity = _optional(run_manifest, "u_target_ms")
    lmh = _optional(wide, "lmh_mass_balance")
    cp_modulus = _optional(wide, "cpc_window_avg_flux")
    gradient = _optional(wide, "pressure_drop_spacer_per_m")

    diameter = hydraulic_diameter_m(volume, membrane_area, spacer_area)
    schock = _schock_diameter_m(mesh_manifest, wide)
    fields = {
        "hydraulic_diameter_m": _format_metric(diameter),
        "hydraulic_diameter_schock_miquel_m": _format_metric(schock),
        "re_h": _format_metric(reynolds_h(velocity, diameter)),
        "sc": _format_metric(schmidt_number()),
        "sh_cpc_flux": _format_metric(sherwood_number(lmh, cp_modulus, diameter)),
        "fanning_friction_factor": _format_metric(
            fanning_friction_factor(gradient, diameter, velocity)
        ),
        "darcy_friction_factor": _format_metric(
            darcy_friction_factor(gradient, diameter, velocity)
        ),
        "specific_power_dissipation_w_per_kg": _format_metric(
            specific_power_dissipation_w_per_kg(gradient, velocity)
        ),
    }
    if diameter is None:
        gap = _geometric_diameter_gap(volume, membrane_area, spacer_area)
        detail = ", ".join(gap) if gap else "unusable inputs"
        notes.append(
            "hydraulic_diameter_m missing inputs: "
            f"{detail}; re_h, sh_cpc_flux, fanning_friction_factor, "
            "darcy_friction_factor need hydraulic_diameter_m"
        )
    else:
        if schock is None:
            notes.append(
                "hydraulic_diameter_schock_miquel_m missing "
                "active_window_porosity, active_window_box_volume_m3, "
                "or the layout pitch"
            )
        if fields["re_h"] == MISSING:
            notes.append("re_h missing u_target_ms or it is not positive")
        if fields["fanning_friction_factor"] == MISSING:
            notes.append(
                "fanning_friction_factor and darcy_friction_factor missing "
                "u_target_ms or pressure_drop_spacer_per_m"
            )
        if fields["sh_cpc_flux"] == MISSING:
            notes.append(
                "sh_cpc_flux undefined: cpc_window_avg_flux <= 1 "
                "or lmh_mass_balance < 0"
            )
    if fields["specific_power_dissipation_w_per_kg"] == MISSING:
        notes.append(
            "specific_power_dissipation_w_per_kg missing "
            "u_target_ms or pressure_drop_spacer_per_m"
        )
    return fields


def default_out_dir(data_root, now=None):
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(data_root) / "reports" / stamp


def _matches(name, pattern):
    if pattern is None:
        return True
    return fnmatchcase(name, pattern)


def iter_run_leaves(data_root, *, family=None, geo_id=None, mesh_id=None, run_id=None):
    """Yield ``(family, geo_id, mesh_id, run_id, leaf)`` sorted by the four ids.

    A run leaf is a directory at ``runs/<family>/<geo_id>/<mesh_id>/<run_id>``.
    A missing manifest does not drop the leaf.
    """
    runs = Path(data_root) / "runs"
    if not runs.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs}.")
    leaves = []
    for family_dir in runs.iterdir():
        if not family_dir.is_dir() or not _matches(family_dir.name, family):
            continue
        for geo_dir in family_dir.iterdir():
            if not geo_dir.is_dir() or not _matches(geo_dir.name, geo_id):
                continue
            for mesh_dir in geo_dir.iterdir():
                if not mesh_dir.is_dir() or not _matches(mesh_dir.name, mesh_id):
                    continue
                for run_dir in mesh_dir.iterdir():
                    if not run_dir.is_dir() or not _matches(run_dir.name, run_id):
                        continue
                    leaves.append(
                        (
                            family_dir.name,
                            geo_dir.name,
                            mesh_dir.name,
                            run_dir.name,
                            run_dir,
                        )
                    )
    leaves.sort(key=lambda item: item[:4])
    return leaves


def _read_json_object(path):
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _read_summary_row(path):
    """Return the single wide-CSV row, or None when the file has no row.

    More than one data row raises. A missing file returns None.
    """
    if not path.is_file():
        return None, f"{path.name} missing"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return None, f"{path.name} has no data rows"
    if len(rows) != 1:
        raise ValueError(
            f"{path.name} must have one data row, found {len(rows)}: {path}"
        )
    return rows[0], None


def _finite_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_number(value):
    number = _finite_number(value)
    if number is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return format(number, ".12g")


def _blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def _take(mapping, key, label, notes, *, numeric=False):
    if mapping is None:
        return MISSING
    if key not in mapping or _blank(mapping[key]):
        notes.append(f"{label} missing column {key}")
        return MISSING
    if numeric:
        formatted = _format_number(mapping[key])
        if formatted is None:
            notes.append(f"{label} column {key} is not a finite number")
            return MISSING
        return formatted
    text = mapping[key]
    if isinstance(text, str):
        return text.strip()
    return str(text)


def _processor_count(run_manifest, mesh_manifest, notes):
    """Solver launch count, or the meshing launch count when the run omits it."""
    for mapping, label in (
        (run_manifest, "run manifest"),
        (mesh_manifest, "mesh manifest"),
    ):
        if mapping is None or "processor_count" not in mapping or _blank(
            mapping.get("processor_count")
        ):
            continue
        formatted = _format_number(mapping["processor_count"])
        if formatted is None:
            notes.append(f"{label} column processor_count is not a finite number")
            return MISSING
        return formatted
    if run_manifest is None and mesh_manifest is None:
        return MISSING
    notes.append("processor_count missing")
    return MISSING


def _viscous_model(run_manifest, notes):
    if run_manifest is None:
        return MISSING
    if "solver_settings" not in run_manifest:
        return NOT_RECORDED
    settings = run_manifest["solver_settings"]
    if not isinstance(settings, dict):
        notes.append("run manifest solver_settings is not an object")
        return NOT_RECORDED
    if "viscous_model" not in settings or _blank(settings["viscous_model"]):
        return NOT_RECORDED
    return str(settings["viscous_model"]).strip()


def _module_area_cell(mesh_manifest, wide, notes):
    sources = {
        "lmh_mass_balance": None if wide is None else wide.get("lmh_mass_balance"),
        "area_mem": None if wide is None else wide.get("area_mem"),
        "n_active_cells": None
        if mesh_manifest is None
        else mesh_manifest.get("n_active_cells"),
        "cell_length_x_m": None
        if mesh_manifest is None
        else mesh_manifest.get("cell_length_x_m"),
        "periodic_shift_y_m": None
        if mesh_manifest is None
        else mesh_manifest.get("periodic_shift_y_m"),
    }
    missing = [
        name
        for name in _MODULE_AREA_INPUTS
        if _blank(sources[name]) or _finite_number(sources[name]) is None
    ]
    if missing:
        notes.append(
            "lmh_module_area missing inputs: " + ", ".join(missing)
        )
        return MISSING
    try:
        value = lmh_per_module_area(
            sources["lmh_mass_balance"],
            sources["area_mem"],
            sources["n_active_cells"],
            sources["cell_length_x_m"],
            sources["periodic_shift_y_m"],
        )
    except ZeroDivisionError:
        notes.append("lmh_module_area A_module is 0")
        return MISSING
    return format(value, ".12g")


_WINDOW_IDENTITY = (
    "n_lead_excluded",
    "excluded_length_m",
    "window_length_m",
    "window_table_version",
)
_WINDOW_LMH = ("lmh_window_exposed", "lmh_window_module")
_WINDOW_NUMERIC_IDENTITY = (
    "n_lead_excluded",
    "excluded_length_m",
    "window_length_m",
)


def _stored_number(mapping, key):
    if mapping is None or key not in mapping or _blank(mapping[key]):
        return None
    return _format_number(mapping[key])


def _stored_text(mapping, key):
    if mapping is None or key not in mapping or _blank(mapping[key]):
        return None
    text = mapping[key]
    if isinstance(text, str):
        return text.strip()
    return str(text)


def _whole_int(mapping, key):
    if mapping is None or key not in mapping:
        return None
    number = _finite_number(mapping[key])
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _window_columns(wide, mesh_manifest, geo_id, mesh_id, notes, table_path):
    """Copy stored window fields, or fill old leaves from the table and cells."""
    fields = {}
    identity_stored = all(
        (
            _stored_number(wide, key)
            if key in _WINDOW_NUMERIC_IDENTITY
            else _stored_text(wide, key)
        )
        is not None
        for key in _WINDOW_IDENTITY
    )
    lmh_stored = all(_stored_number(wide, key) is not None for key in _WINDOW_LMH)
    if identity_stored:
        for key in _WINDOW_NUMERIC_IDENTITY:
            fields[key] = _stored_number(wide, key)
        fields["window_table_version"] = _stored_text(wide, "window_table_version")
    if lmh_stored:
        for key in _WINDOW_LMH:
            fields[key] = _stored_number(wide, key)
    if identity_stored and lmh_stored:
        return fields
    if wide is None:
        for key in _WINDOW_IDENTITY + _WINDOW_LMH:
            fields.setdefault(key, MISSING)
        return fields

    selection = None
    lookup_error = None
    try:
        path = (
            Path(table_path)
            if table_path is not None
            else default_evaluation_window_table_path()
        )
        n_active = _whole_int(mesh_manifest, "n_active_cells")
        cell_length = None if mesh_manifest is None else _finite_number(
            mesh_manifest.get("cell_length_x_m")
        )
        if mesh_manifest is None or n_active is None or cell_length is None:
            raise ValueError(
                "mesh manifest is missing n_active_cells or cell_length_x_m"
            )
        selection = selection_from_table(
            path,
            geo_id,
            mesh_id=mesh_id,
            n_active=n_active,
            cell_length_x_m=cell_length,
        )
    except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
        lookup_error = exc

    if not identity_stored:
        if selection is None:
            notes.append(f"evaluation window: {lookup_error}")
            for key in _WINDOW_IDENTITY:
                fields[key] = MISSING
        else:
            fields["n_lead_excluded"] = str(selection.n_lead_excluded)
            fields["excluded_length_m"] = format(selection.excluded_length_m, ".12g")
            fields["window_length_m"] = format(selection.window_length_m, ".12g")
            fields["window_table_version"] = selection.window_table_version
            notes.append("evaluation window fields from the evaluation window table")
    if lmh_stored:
        return fields
    if selection is None:
        if identity_stored:
            notes.append(f"evaluation window: {lookup_error}")
        for key in _WINDOW_LMH:
            fields[key] = MISSING
        return fields
    try:
        n_buffer = _whole_int(mesh_manifest, "n_buffer_in")
        shift = _finite_number(mesh_manifest.get("periodic_shift_y_m"))
        if n_buffer is None or shift is None:
            raise ValueError(
                "mesh manifest is missing n_buffer_in or periodic_shift_y_m"
            )
        cells = window_global_cell_numbers(
            n_buffer,
            n_active,
            selection.n_lead_excluded,
        )
        fluxes, areas = per_cell_flux_and_area(wide, cells)
        exposed, module = window_lmh(
            fluxes, areas, selection.window_length_m, shift
        )
    except (KeyError, ValueError, TypeError) as exc:
        notes.append(f"lmh_window: {exc}")
        for key in _WINDOW_LMH:
            fields[key] = MISSING
        return fields
    fields["lmh_window_exposed"] = format(exposed, ".12g")
    fields["lmh_window_module"] = format(module, ".12g")
    notes.append("recomputed_from_cells")
    return fields


def _format_monitor(value):
    if value is None:
        return MISSING
    return format(float(value), ".12g")


def _read_monitor_series(path, label, notes):
    """Last-500 mean and percent range, plus the last iteration index."""
    if not path.is_file():
        notes.append(f"{label} is not in the run leaf")
        return None
    pairs = _monitor_periodicity.read_report_file(path)
    if not pairs:
        notes.append(f"{label} has no iteration/value rows")
        return None
    full = _monitor_periodicity.last_window(
        pairs,
        max(len(pairs), _monitor_periodicity.MIN_POINTS),
    )
    window = full[-MONITOR_LAST_N:]
    values = [value for _iteration, value in window]
    if not all(math.isfinite(value) for value in values):
        notes.append(f"{label} last-{MONITOR_LAST_N} values are not finite")
        return None
    mean = sum(values) / len(values)
    if mean == 0.0:
        notes.append(f"{label} last-{MONITOR_LAST_N} mean is 0")
        range_pct = None
    else:
        range_pct = (max(values) - min(values)) / abs(mean) * 100.0
    return {
        "mean": mean,
        "range_pct": range_pct,
        "iterations": int(full[-1][0]),
    }


def _report_status(quality, lmh):
    if quality == "PASS":
        return "converged"
    if (
        quality == "FAIL"
        and lmh is not None
        and lmh["iterations"] >= STEADY_MIN_ITERATIONS
        and lmh["range_pct"] is not None
        and lmh["range_pct"] < STEADY_LMH_RANGE_PCT_MAX
    ):
        return "steady_not_converged"
    return "failed"


def _monitor_fields(leaf, run_manifest, notes):
    lmh = _read_monitor_series(
        Path(leaf) / _monitor_periodicity.LMH_REPORT_FILE,
        _monitor_periodicity.LMH_REPORT_FILE,
        notes,
    )
    pressure = _read_monitor_series(
        Path(leaf) / _monitor_periodicity.PRESSURE_REPORT_FILE,
        _monitor_periodicity.PRESSURE_REPORT_FILE,
        notes,
    )
    quality = None
    if run_manifest is not None and not _blank(
        run_manifest.get("convergence_quality")
    ):
        quality = str(run_manifest["convergence_quality"]).strip()
    return {
        "report_status": _report_status(quality, lmh),
        "monitor_lmh_udm_mean_last500": _format_monitor(
            None if lmh is None else lmh["mean"]
        ),
        "monitor_lmh_udm_range_pct_last500": _format_monitor(
            None if lmh is None else lmh["range_pct"]
        ),
        "monitor_dp_mean_last500": _format_monitor(
            None if pressure is None else pressure["mean"]
        ),
        "monitor_dp_range_pct_last500": _format_monitor(
            None if pressure is None else pressure["range_pct"]
        ),
        "monitor_iterations": _format_monitor(
            None if lmh is None else lmh["iterations"]
        ),
    }


def row_for_leaf(
    data_root,
    family,
    geo_id,
    mesh_id,
    run_id,
    leaf,
    window_table_path=None,
):
    notes = []
    run_path = Path(leaf) / "manifest.json"
    mesh_path = (
        Path(data_root) / "meshes" / family / geo_id / mesh_id / "manifest.json"
    )
    csv_path = Path(leaf) / "post" / "reports" / "summary_metrics_wide.csv"
    if not run_path.is_file():
        notes.append("run manifest.json missing")
    if not mesh_path.is_file():
        notes.append("mesh manifest.json missing")
    run_manifest = _read_json_object(run_path)
    mesh_manifest = _read_json_object(mesh_path)
    wide, csv_note = _read_summary_row(csv_path)
    if csv_note:
        notes.append(csv_note)

    row = {
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
    }
    for key in _MESH_FIELDS:
        row[key] = _take(mesh_manifest, key, "mesh manifest", notes, numeric=True)
    for key in _CSV_FIELDS:
        row[key] = _take(wide, key, "summary_metrics_wide.csv", notes)
    for key in _RUN_FIELDS:
        numeric = key in (
            "continuity_final",
            "solver_wall_time_s",
            "extraction_wall_time_s",
        )
        row[key] = _take(run_manifest, key, "run manifest", notes, numeric=numeric)
    row["processor_count"] = _processor_count(run_manifest, mesh_manifest, notes)
    row["viscous_model"] = _viscous_model(run_manifest, notes)
    row["lmh_module_area"] = _module_area_cell(mesh_manifest, wide, notes)
    row.update(
        _window_columns(
            wide,
            mesh_manifest,
            geo_id,
            mesh_id,
            notes,
            window_table_path,
        )
    )
    row.update(_dimensionless_fields(mesh_manifest, wide, run_manifest, notes))
    row.update(_monitor_fields(leaf, run_manifest, notes))
    row["notes"] = "; ".join(notes)
    return row


def rows_to_markdown(rows):
    header = "| " + " | ".join(COLUMNS) + " |"
    separator = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    lines = [header, separator]
    for row in rows:
        cells = []
        for column in COLUMNS:
            text = str(row[column]).replace("|", "\\|").replace("\n", " ")
            cells.append(text)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_summary(out_dir, rows):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "summary.csv"
    md_path = out_dir / "summary.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    markdown = rows_to_markdown(rows)
    md_path.write_text(markdown, encoding="utf-8")
    return csv_path, md_path, markdown


def summarize(
    data_root,
    *,
    family=None,
    geo_id=None,
    mesh_id=None,
    run_id=None,
    out_dir=None,
    now=None,
    window_table_path=None,
):
    root = Path(data_root)
    if not root.is_dir():
        raise FileNotFoundError(f"data root is not a directory: {root}")
    destination = Path(out_dir) if out_dir is not None else default_out_dir(root, now)
    leaves = iter_run_leaves(
        root,
        family=family,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
    )
    rows = [
        row_for_leaf(
            root,
            family_name,
            geo_name,
            mesh_name,
            run_name,
            leaf,
            window_table_path=window_table_path,
        )
        for family_name, geo_name, mesh_name, run_name, leaf in leaves
    ]
    _csv_path, _md_path, markdown = write_summary(destination, rows)
    return destination, rows, markdown


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Summarize run leaves under a data root into summary.csv and "
            "summary.md. Reported LMH is per exposed membrane area (area_mem)."
        )
    )
    parser.add_argument("--data-root", required=True, help="Data root containing runs/ and meshes/.")
    parser.add_argument("--family", default=None, help="Exact or glob family filter.")
    parser.add_argument("--geo-id", default=None, help="Exact or glob geo_id filter.")
    parser.add_argument("--mesh-id", default=None, help="Exact or glob mesh_id filter.")
    parser.add_argument("--run-id", default=None, help="Exact or glob run_id filter.")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Default: <data-root>/reports/<UTC stamp>/.",
    )
    parser.add_argument(
        "--window-table",
        default=None,
        help=(
            "Evaluation window table JSON. Default: "
            "configs/evaluation_window_table.json."
        ),
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    _out_dir, _rows, markdown = summarize(
        args.data_root,
        family=args.family,
        geo_id=args.geo_id,
        mesh_id=args.mesh_id,
        run_id=args.run_id,
        out_dir=args.out_dir,
        window_table_path=args.window_table,
    )
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
