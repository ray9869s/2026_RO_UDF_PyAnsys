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
import json
import math
import sys
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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
    "viscous_model",
    "lmh_mass_balance",
    "lmh_module_area",
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


def row_for_leaf(data_root, family, geo_id, mesh_id, run_id, leaf):
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
    row.update(_dimensionless_fields(mesh_manifest, wide, run_manifest, notes))
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
        row_for_leaf(root, family_name, geo_name, mesh_name, run_name, leaf)
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
    )
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
