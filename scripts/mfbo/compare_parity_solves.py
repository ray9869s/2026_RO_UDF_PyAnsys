"""Compare two MFBO solves of the same production case. No thresholds.

Reads each root's run manifest, mesh manifest, and
``post/reports/summary_metrics_wide.csv``. Does not launch Fluent and does
not read ``C:/ro_data``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

SOURCE_GEO_ID = "P_p100_h30"
SOURCE_MESH_ID = "max085_min006_cpg5_bl4_peel2"
SOURCE_RUN_ID = "u0p2_p6M"
SOURCE_FAMILY = "pillar"

WIDE_FIELDS = (
    "lmh_mass_balance",
    "lmh_mass_balance_signed",
    "pressure_drop_spacer_per_m",
    "cp_canon_window_avg",
    "cp_canon_window_max",
)
RUN_FIELDS = (
    "u_mean_ms",
    "stop_reason",
    "udf_version",
)
MESH_FIELDS = ("inlet_profile_G",)
OPTIONAL_FIELDS = (
    "continuity_final",
    "final_iteration",
    "iteration_count",
    "n_iterations",
    "iterations",
)

COMPARISON_NAME = "parity_solve_comparison.json"


def resolve_data_root(value):
    """Return an absolute data root that is not C:/ro_data or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    folded = text.casefold().rstrip("/")
    if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"data root must be absolute, got {value!r}.")
        path = path.resolve()
    resolved = path.as_posix().casefold().rstrip("/")
    if resolved == "c:/ro_data" or resolved.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if resolved == "/mnt/c/ro_data" or resolved.startswith("/mnt/c/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    return path


def _is_windows_absolute(text):
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def mesh_manifest_path(data_root):
    return (
        Path(data_root)
        / "meshes"
        / SOURCE_FAMILY
        / SOURCE_GEO_ID
        / SOURCE_MESH_ID
        / "manifest.json"
    )


def run_manifest_path(data_root):
    return (
        Path(data_root)
        / "runs"
        / SOURCE_FAMILY
        / SOURCE_GEO_ID
        / SOURCE_MESH_ID
        / SOURCE_RUN_ID
        / "manifest.json"
    )


def summary_wide_path(data_root):
    return (
        Path(data_root)
        / "runs"
        / SOURCE_FAMILY
        / SOURCE_GEO_ID
        / SOURCE_MESH_ID
        / SOURCE_RUN_ID
        / "post"
        / "reports"
        / "summary_metrics_wide.csv"
    )


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def _coerce_csv_value(text):
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return text
    if not math.isfinite(value):
        return None
    return value


def read_summary_wide(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"summary_metrics_wide.csv not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, found {len(rows)}: {path}"
        )
    return {key: _coerce_csv_value(value) for key, value in rows[0].items()}


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def abs_rel(value_a, value_b):
    """Return (abs_diff, rel_diff). rel_diff uses value_b as the denominator."""
    if not _is_number(value_a) or not _is_number(value_b):
        return None, None
    difference = abs(float(value_a) - float(value_b))
    if float(value_b) == 0.0:
        relative = 0.0 if difference == 0.0 else None
    else:
        relative = difference / abs(float(value_b))
    return difference, relative


def _optional_names(run_a, run_b, wide_a, wide_b):
    names = []
    for name in OPTIONAL_FIELDS:
        if name in run_a or name in run_b or name in wide_a or name in wide_b:
            names.append(name)
    for source in (run_a, run_b, wide_a, wide_b):
        for key in source:
            if "residual" in str(key).casefold() and key not in names:
                names.append(key)
    return names


def _lookup(field, source, run_manifest, mesh_manifest, wide):
    if source == "mesh":
        return mesh_manifest.get(field)
    if source == "run":
        if field in run_manifest:
            return run_manifest[field]
        return wide.get(field)
    if source == "wide":
        return wide.get(field)
    raise ValueError(f"Unknown comparison source {source!r}.")


def comparison_rows(side_a, side_b):
    """Rows of value_a, value_b, abs_diff, rel_diff. No pass/fail threshold."""
    run_a, mesh_a, wide_a = side_a
    run_b, mesh_b, wide_b = side_b
    fields = (
        [(name, "wide") for name in WIDE_FIELDS]
        + [(name, "mesh") for name in MESH_FIELDS]
        + [(name, "run") for name in RUN_FIELDS]
        + [
            (name, "run" if name in run_a or name in run_b else "wide")
            for name in _optional_names(run_a, run_b, wide_a, wide_b)
            if name not in WIDE_FIELDS
            and name not in MESH_FIELDS
            and name not in RUN_FIELDS
        ]
    )
    rows = []
    for field, source in fields:
        value_a = _lookup(field, source, run_a, mesh_a, wide_a)
        value_b = _lookup(field, source, run_b, mesh_b, wide_b)
        difference, relative = abs_rel(value_a, value_b)
        rows.append(
            {
                "field": field,
                "value_a": value_a,
                "value_b": value_b,
                "abs_diff": difference,
                "rel_diff": relative,
            }
        )
    return rows


def load_side(data_root):
    root = Path(data_root)
    return (
        _read_json(run_manifest_path(root)),
        _read_json(mesh_manifest_path(root)),
        read_summary_wide(summary_wide_path(root)),
    )


def build_comparison(root_a, root_b):
    rows = comparison_rows(load_side(root_a), load_side(root_b))
    return {
        "root_a": Path(root_a).as_posix(),
        "root_b": Path(root_b).as_posix(),
        "geo_id": SOURCE_GEO_ID,
        "mesh_id": SOURCE_MESH_ID,
        "run_id": SOURCE_RUN_ID,
        "rel_diff_denominator": "b",
        "rows": rows,
    }


def comparison_output_path(root_a, root_b):
    parent_a = Path(root_a).parent
    parent_b = Path(root_b).parent
    parent = parent_a if parent_a == parent_b else parent_a
    return parent / COMPARISON_NAME


def _print_comparison(rows):
    print(f"{'field':<32} {'value_a':<16} {'value_b':<16} {'abs_diff':<16} rel_diff")
    for row in rows:
        rel = "" if row["rel_diff"] is None else f"{row['rel_diff']:.6g}"
        print(
            f"{row['field']:<32} {_cell(row['value_a']):<16} "
            f"{_cell(row['value_b']):<16} {_cell(row['abs_diff']):<16} {rel}"
        )


def _cell(value):
    if isinstance(value, float):
        return f"{value:.12g}"
    if value is None:
        return ""
    return str(value)


def write_comparison(payload, path):
    path = Path(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare two MFBO roots for P_p100_h30 / "
            "max085_min006_cpg5_bl4_peel2 / u0p2_p6M. No thresholds."
        ),
    )
    parser.add_argument("--a", required=True, help="First data root. Not C:/ro_data.")
    parser.add_argument("--b", required=True, help="Second data root. Not C:/ro_data.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root_a = resolve_data_root(args.a)
    root_b = resolve_data_root(args.b)
    payload = build_comparison(root_a, root_b)
    _print_comparison(payload["rows"])
    out = comparison_output_path(root_a, root_b)
    resolve_data_root(str(out.parent))
    write_comparison(payload, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
