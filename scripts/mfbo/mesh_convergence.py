"""Mesh-independence table for one ordered series (coarse to fine).

The refinement ratio is never inferred from the mesh-id names. Pass
``--cell-count`` to use ``r = (N_fine / N_coarse) ** (1/3)`` on each
successive pair, or ``--ratio`` for one constant ratio (2.0 when y1
halves). Apparent order, the Richardson value, and the fine-grid GCI
use the three finest levels and Celik et al. (2008) with safety factor
1.25. An oscillatory or non-contracting series is flagged and those
three quantities are left blank.

Importing this module does not launch Fluent.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
_SCRIPTS = Path(__file__).resolve().parents[1]
for _path in (_SRC, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ro.geometry_registry import family_for_known_geo_id
from ro.paths import MESH_ID_RE, RUN_ID_RE

import mfbo.summarize_results as summarize_results

SAFETY_FACTOR = 1.25
_ORDER_TOL = 1e-12
_ORDER_ITERS = 50

METRICS = (
    "lmh_mass_balance",
    "lmh_module_area",
    "pressure_drop_spacer_per_m",
    "cpc_window_avg_flux_minus_1",
    "cp_q999_window_flux_minus_1",
)

CSV_COLUMNS = (
    "metric",
    "status",
    "mesh_id",
    "cell_count",
    "value",
    "successive_difference",
    "difference_ratio",
    "refinement_ratio",
    "apparent_order",
    "richardson_extrapolated",
    "gci_fine",
    "safety_factor",
    "note",
)

_SUMMARY_FIELDS = (
    "lmh_mass_balance",
    "area_mem",
    "pressure_drop_spacer_per_m",
    "cpc_window_avg_flux",
    "cp_q999_window_flux",
)
_LAYOUT_FIELDS = (
    "n_active_cells",
    "cell_length_x_m",
    "periodic_shift_y_m",
)


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a number, got {value!r}.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number, got {value!r}.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    return number


def _positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a positive integer, got {value!r}.")
    if value < 1:
        raise ValueError(f"{label} must be a positive integer, got {value!r}.")
    return value


def successive_differences(values):
    """Fine minus the next-coarser neighbour, coarse end first."""
    return [
        float(values[index + 1]) - float(values[index])
        for index in range(len(values) - 1)
    ]


def difference_ratios(differences):
    """Later difference over the previous one, toward the fine end."""
    ratios = []
    for index in range(1, len(differences)):
        previous = differences[index - 1]
        if previous == 0.0:
            ratios.append(None)
        else:
            ratios.append(differences[index] / previous)
    return ratios


def sequence_status(differences):
    """Classify the whole series. A flagged series is not extrapolated.

    ``zero_difference``: a step is exactly zero, so a ratio is undefined.
    ``oscillatory``: two successive steps have opposite signs.
    ``divergent``: a step grows in magnitude toward the fine end.
    ``not_contracting``: a step keeps the same magnitude (apparent order 0).
    ``monotone``: every step has the same sign and shrinks in magnitude.
    """
    if any(step == 0.0 for step in differences):
        return "zero_difference"
    pairs = list(zip(differences, differences[1:]))
    if any(previous * current < 0.0 for previous, current in pairs):
        return "oscillatory"
    magnitudes = [(abs(previous), abs(current)) for previous, current in pairs]
    if any(current > previous for previous, current in magnitudes):
        return "divergent"
    if any(current == previous for previous, current in magnitudes):
        return "not_contracting"
    return "monotone"


def apparent_order(difference_coarse, difference_fine, r32, r21):
    """Celik et al. (2008) apparent order for one monotone triplet.

    ``difference_coarse`` is φ_medium − φ_coarse. ``difference_fine`` is
    φ_fine − φ_medium. ``r32`` is h_coarse/h_medium and ``r21`` is
    h_medium/h_fine. Both ratios are greater than 1.
    """
    if difference_fine == 0.0:
        raise ValueError("Apparent order needs a non-zero fine-end difference.")
    if r21 <= 1.0 or r32 <= 1.0:
        raise ValueError(
            f"Refinement ratios must be > 1, got r21={r21!r}, r32={r32!r}."
        )
    magnitude = abs(difference_coarse / difference_fine)
    if magnitude == 0.0:
        raise ValueError("Apparent order needs a non-zero coarse-end difference.")
    ln_r21 = math.log(r21)
    p = math.log(magnitude) / ln_r21
    if r21 == r32:
        return p
    for _ in range(_ORDER_ITERS):
        numerator = r21**p - 1.0
        denominator = r32**p - 1.0
        if numerator <= 0.0 or denominator <= 0.0:
            raise RuntimeError(
                "Apparent-order iteration left the Celik formula domain: "
                f"p={p!r}, r21={r21!r}, r32={r32!r}."
            )
        correction = math.log(numerator / denominator)
        updated = abs(math.log(magnitude) + correction) / ln_r21
        if abs(updated - p) <= _ORDER_TOL * max(1.0, abs(updated)):
            return updated
        p = updated
    raise RuntimeError(
        "Apparent order did not converge for "
        f"r21={r21!r}, r32={r32!r}, |ε32/ε21|={magnitude!r}."
    )


def richardson_extrapolated(value_fine, value_medium, refinement_ratio, order):
    """φ_ext = (r^p φ_fine − φ_medium) / (r^p − 1)."""
    factor = refinement_ratio**order
    denominator = factor - 1.0
    if denominator == 0.0:
        raise ValueError("Richardson extrapolation needs r^p ≠ 1.")
    return (factor * value_fine - value_medium) / denominator


def gci_fine(value_fine, value_medium, refinement_ratio, order, safety_factor=SAFETY_FACTOR):
    """Relative fine-grid GCI, Celik et al. (2008), Fs = 1.25."""
    if value_fine == 0.0:
        raise ValueError("Relative GCI is undefined when the finest value is 0.")
    denominator = refinement_ratio**order - 1.0
    if denominator == 0.0:
        raise ValueError("GCI needs r^p ≠ 1.")
    relative = abs(value_fine - value_medium) / abs(value_fine)
    return float(safety_factor) * relative / denominator


def analyze_series(values, refinement_ratios):
    """Differences, ratios, and the three-finest estimate when the series contracts."""
    if len(values) < 3:
        raise ValueError(
            f"Mesh convergence needs at least 3 levels, got {len(values)}."
        )
    if len(refinement_ratios) != len(values) - 1:
        raise ValueError(
            "Each successive pair needs one refinement ratio: "
            f"{len(values)} values and {len(refinement_ratios)} ratios."
        )
    for ratio in refinement_ratios:
        if not math.isfinite(ratio) or ratio <= 1.0:
            raise ValueError(f"Refinement ratio must be finite and > 1, got {ratio!r}.")
    differences = successive_differences(values)
    ratios = difference_ratios(differences)
    status = sequence_status(differences)
    result = {
        "status": status,
        "differences": differences,
        "difference_ratios": ratios,
        "apparent_order": None,
        "richardson_extrapolated": None,
        "gci_fine": None,
        "note": "",
    }
    if status != "monotone":
        result["note"] = (
            f"{status}: order, Richardson value, and GCI are not reported"
        )
        return result
    order = apparent_order(
        differences[-2],
        differences[-1],
        refinement_ratios[-2],
        refinement_ratios[-1],
    )
    extrapolated = richardson_extrapolated(
        values[-1],
        values[-2],
        refinement_ratios[-1],
        order,
    )
    result["apparent_order"] = order
    result["richardson_extrapolated"] = extrapolated
    if values[-1] == 0.0:
        result["note"] = "relative GCI is undefined because the finest value is 0"
        return result
    result["gci_fine"] = gci_fine(
        values[-1],
        values[-2],
        refinement_ratios[-1],
        order,
    )
    return result


def cell_count_ratios(cell_counts):
    """r = (N_fine / N_coarse) ** (1/3) for each successive pair."""
    ratios = []
    for coarse, fine in zip(cell_counts, cell_counts[1:]):
        if fine <= coarse:
            raise ValueError(
                "Cell count must increase toward the fine end of an ordered "
                f"series, got N_coarse={coarse} and N_fine={fine}."
            )
        ratios.append((fine / coarse) ** (1.0 / 3.0))
    return ratios


def constant_ratios(count, ratio):
    number = _finite(ratio, "refinement ratio")
    if number <= 1.0:
        raise ValueError(f"Refinement ratio must be > 1, got {ratio!r}.")
    return [number] * count


def _read_json(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return payload


def _read_summary(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"Expected one summary row in {path}, found {len(rows)}.")
    return rows[0]


def _require_summary_number(row, column, path):
    if column not in row or row[column] is None or str(row[column]).strip() == "":
        raise ValueError(f"{path} is missing {column}.")
    return _finite(row[column], f"{path} column {column}")


def metric_values(levels):
    """Map each reported metric to its coarse-to-fine values."""
    series = {name: [] for name in METRICS}
    for level in levels:
        series["lmh_mass_balance"].append(level["lmh_mass_balance"])
        series["lmh_module_area"].append(level["lmh_module_area"])
        series["pressure_drop_spacer_per_m"].append(level["pressure_drop_spacer_per_m"])
        series["cpc_window_avg_flux_minus_1"].append(level["cpc_window_avg_flux"] - 1.0)
        series["cp_q999_window_flux_minus_1"].append(level["cp_q999_window_flux"] - 1.0)
    return series


def load_levels(data_root, geo_id, run_id, mesh_ids):
    """Read mesh manifests and summary CSVs in the given coarse-to-fine order."""
    if not isinstance(mesh_ids, (list, tuple)) or len(mesh_ids) < 3:
        raise ValueError(
            f"Pass at least 3 mesh ids, coarse to fine, got {mesh_ids!r}."
        )
    if len(set(mesh_ids)) != len(mesh_ids):
        raise ValueError(f"mesh ids must be unique, got {mesh_ids!r}.")
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")
    root = Path(data_root)
    if not root.is_dir():
        raise FileNotFoundError(f"data root is not a directory: {root}")
    family = family_for_known_geo_id(geo_id)
    levels = []
    for mesh_id in mesh_ids:
        if MESH_ID_RE.fullmatch(mesh_id) is None:
            raise ValueError(f"Invalid mesh_id: {mesh_id!r}.")
        manifest_path = (
            root / "meshes" / family / geo_id / mesh_id / "manifest.json"
        )
        summary_path = (
            root
            / "runs"
            / family
            / geo_id
            / mesh_id
            / run_id
            / "post"
            / "reports"
            / "summary_metrics_wide.csv"
        )
        manifest = _read_json(manifest_path)
        summary = _read_summary(summary_path)
        if "cell_count" not in manifest:
            raise ValueError(f"{manifest_path} is missing cell_count.")
        cell_count = _positive_int(manifest["cell_count"], f"{manifest_path} cell_count")
        layout = {}
        for field in _LAYOUT_FIELDS:
            if field not in manifest:
                raise ValueError(f"{manifest_path} is missing {field}.")
            layout[field] = manifest[field]
        summary_numbers = {
            column: _require_summary_number(summary, column, summary_path)
            for column in _SUMMARY_FIELDS
        }
        try:
            module_area = summarize_results.lmh_per_module_area(
                summary_numbers["lmh_mass_balance"],
                summary_numbers["area_mem"],
                layout["n_active_cells"],
                layout["cell_length_x_m"],
                layout["periodic_shift_y_m"],
            )
        except ZeroDivisionError as exc:
            raise ValueError(
                f"{manifest_path} gives A_module = 0 for lmh_module_area."
            ) from exc
        levels.append(
            {
                "mesh_id": mesh_id,
                "cell_count": cell_count,
                "lmh_module_area": module_area,
                **summary_numbers,
            }
        )
    return levels


def refinement_ratios_for(levels, *, mode, ratio=None):
    counts = [level["cell_count"] for level in levels]
    if mode == "cell_count":
        return cell_count_ratios(counts)
    if mode == "ratio":
        return constant_ratios(len(levels) - 1, ratio)
    raise ValueError(f"Unknown refinement mode {mode!r}.")


def _blank(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def convergence_rows(levels, analyses):
    """One CSV row per metric and mesh level. Estimates sit on the finest row."""
    rows = []
    for metric in METRICS:
        analysis = analyses[metric]
        values = analysis["values"]
        differences = analysis["differences"]
        ratios = analysis["difference_ratios"]
        refinements = analysis["refinement_ratios"]
        for index, level in enumerate(levels):
            finest = index == len(levels) - 1
            rows.append(
                {
                    "metric": metric,
                    "status": analysis["status"],
                    "mesh_id": level["mesh_id"],
                    "cell_count": str(level["cell_count"]),
                    "value": _blank(values[index]),
                    "successive_difference": (
                        "" if index == 0 else _blank(differences[index - 1])
                    ),
                    "difference_ratio": (
                        ""
                        if index < 2
                        else _blank(ratios[index - 2])
                    ),
                    "refinement_ratio": (
                        "" if index == 0 else _blank(refinements[index - 1])
                    ),
                    "apparent_order": (
                        _blank(analysis["apparent_order"]) if finest else ""
                    ),
                    "richardson_extrapolated": (
                        _blank(analysis["richardson_extrapolated"]) if finest else ""
                    ),
                    "gci_fine": _blank(analysis["gci_fine"]) if finest else "",
                    "safety_factor": format(SAFETY_FACTOR, ".12g") if finest else "",
                    "note": analysis["note"] if finest else "",
                }
            )
    return rows


def analyze_levels(levels, refinement_ratios):
    analyses = {}
    for metric, values in metric_values(levels).items():
        analysis = analyze_series(values, refinement_ratios)
        analysis["values"] = values
        analysis["refinement_ratios"] = list(refinement_ratios)
        analyses[metric] = analysis
    return analyses


def _markdown_number(value):
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value)


def to_markdown(geo_id, run_id, mode, ratio, levels, analyses):
    """One section per metric. Flagged series omit the extrapolated lines."""
    if mode == "cell_count":
        refinement = (
            "cell count, r = (N_fine / N_coarse)^(1/3) on each successive pair"
        )
    else:
        refinement = f"supplied constant ratio {format(float(ratio), '.6g')}"
    lines = [
        "# Mesh convergence",
        "",
        f"geo_id `{geo_id}`, run_id `{run_id}`.",
        "",
        f"Refinement ratio: {refinement}.",
        "",
        "Order, Richardson extrapolation, and fine-grid GCI use the three",
        "finest levels (Celik et al. 2008, safety factor 1.25).",
        "A successive difference is the finer value minus the next-coarser",
        "value. The difference ratio divides that step by the previous one.",
        "An oscillatory, divergent, zero-difference, or non-contracting",
        "series is flagged and those three quantities are omitted.",
        "",
    ]
    for metric in METRICS:
        analysis = analyses[metric]
        lines.append(f"## {metric}")
        lines.append("")
        lines.append(f"Status: `{analysis['status']}`.")
        if analysis["note"]:
            lines.append("")
            lines.append(analysis["note"] + ".")
        lines.append("")
        lines.append(
            "| mesh_id | cell_count | value | successive difference | "
            "difference ratio | refinement ratio |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        differences = analysis["differences"]
        ratios = analysis["difference_ratios"]
        refinements = analysis["refinement_ratios"]
        for index, level in enumerate(levels):
            lines.append(
                "| "
                + " | ".join(
                    [
                        level["mesh_id"],
                        str(level["cell_count"]),
                        _markdown_number(analysis["values"][index]),
                        "" if index == 0 else _markdown_number(differences[index - 1]),
                        "" if index < 2 else _markdown_number(ratios[index - 2]),
                        "" if index == 0 else _markdown_number(refinements[index - 1]),
                    ]
                )
                + " |"
            )
        lines.append("")
        if analysis["status"] == "monotone":
            lines.append(
                f"Apparent order: {_markdown_number(analysis['apparent_order'])}."
            )
            lines.append(
                "Richardson-extrapolated value: "
                f"{_markdown_number(analysis['richardson_extrapolated'])}."
            )
            lines.append(
                "Fine-grid GCI: "
                f"{_markdown_number(analysis['gci_fine'])} "
                f"(safety factor {SAFETY_FACTOR})."
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(out_dir, rows, markdown):
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    csv_path = destination / "mesh_convergence.csv"
    md_path = destination / "mesh_convergence.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    md_path.write_text(markdown, encoding="utf-8")
    return csv_path, md_path


def converge(data_root, geo_id, run_id, mesh_ids, out_dir, *, mode, ratio=None):
    levels = load_levels(data_root, geo_id, run_id, mesh_ids)
    refinements = refinement_ratios_for(levels, mode=mode, ratio=ratio)
    analyses = analyze_levels(levels, refinements)
    rows = convergence_rows(levels, analyses)
    markdown = to_markdown(geo_id, run_id, mode, ratio, levels, analyses)
    csv_path, md_path = write_report(out_dir, rows, markdown)
    return {
        "csv": csv_path,
        "markdown_path": md_path,
        "markdown": markdown,
        "rows": rows,
        "analyses": analyses,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Mesh-independence table for mesh ids ordered coarse to fine. "
            "Pass --cell-count or --ratio. The script does not choose one."
        )
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--geo-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--mesh-id",
        required=True,
        nargs="+",
        help="Mesh ids from coarse to fine.",
    )
    parser.add_argument("--out-dir", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--cell-count",
        action="store_true",
        help="r = (N_fine / N_coarse) ** (1/3) from mesh-manifest cell counts.",
    )
    group.add_argument(
        "--ratio",
        type=float,
        help="Constant refinement ratio between successive levels, for example 2.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    mode = "cell_count" if args.cell_count else "ratio"
    result = converge(
        args.data_root,
        args.geo_id,
        args.run_id,
        args.mesh_id,
        args.out_dir,
        mode=mode,
        ratio=args.ratio,
    )
    print(result["markdown"], end="")
    print(result["csv"])
    print(result["markdown_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
