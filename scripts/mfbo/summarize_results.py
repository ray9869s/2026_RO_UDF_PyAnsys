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

MISSING = "MISSING"
NOT_RECORDED = "not recorded"

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
    "viscosity_ratio_volavg",
    "diff_ratio_volavg",
    "notes",
)

_MESH_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
    "porosity_eps",
)
_RUN_FIELDS = (
    "stop_reason",
    "continuity_final",
    "convergence_quality",
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
        numeric = key == "continuity_final"
        row[key] = _take(run_manifest, key, "run manifest", notes, numeric=numeric)
    row["viscous_model"] = _viscous_model(run_manifest, notes)
    row["lmh_module_area"] = _module_area_cell(mesh_manifest, wide, notes)
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
