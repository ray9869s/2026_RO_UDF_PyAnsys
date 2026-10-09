"""Per-geometry CP development length and evaluation-window exclusion.

The fixed ``n_lead_excluded = 3`` rule is not applied here. Domain and CAD
are unchanged. For each geometry this script measures the development
distance from PASS ``u0p1_p6M``, ``u0p2_p6M``, and ``u0p3_p6M`` leaves and
writes the exclusion the 2026-10-09 decision specified.

Per active cell, CP excess is ``(c_m - c_p) / (c_b - c_p) - 1`` from that
cell's wide-summary columns. The cell mean is unweighted. Area versus flux
weighting was left open at the 2026-10-08 meeting and is not chosen here.

The downstream reference is the mean excess of the cells in the last
6.93 mm (two 3.465 mm reference cells), and at least 2 cells. A 3-cell
rolling mean is within band when it is within 3% of that reference
(relative). The rolling window that starts at active cell ``m + 1`` sits
after the cell boundary ``m * cell_length``. ``d`` is the first such
boundary for which every rolling window from there on stays in band. If
no boundary qualifies, the operating point has not developed.

``L_base`` is 10.395 mm (three reference cells). When the cell boundary
just below ``L_base`` is at or beyond ``d``, exclusion stops on that
boundary. Otherwise it stops on the first cell boundary at or beyond
``max(L_base, d)``. ``d`` is the maximum over the PASS operating points
that exist. The evaluation window is every remaining active cell. A
window shorter than 9.0 mm is flagged.

Importing this module does not launch Fluent and does not read a data root.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.campaign_geo_ids import (  # noqa: E402
    CAMPAIGN_GEO_ID_ORDER,
    family_for_geo_id,
    validate_campaign_geo_id,
)
from ro.paths import MESH_ID_RE  # noqa: E402

REFERENCE_CELL_M = 0.003465
L_REF_M = 0.00693
L_BASE_M = 0.010395
SHORT_WINDOW_M = 0.009
RELATIVE_BAND = 0.03
ROLLING_CELLS = 3
MIN_REFERENCE_CELLS = 2
OLD_N_LEAD = 3
PILLAR_DEVELOPMENT_WITHIN_M = 0.0035
_FAMILY_DEFAULT_KEYS = (
    "n_lead_excluded",
    "excluded_length_m",
    "basis",
    "date",
)
LENGTH_TOL_M = 1e-9
OPERATING_RUN_IDS = ("u0p1_p6M", "u0p2_p6M", "u0p3_p6M")
PRODUCTION_MESH_ID = "max085_min006_cpg5_bl4_peel2"
PRODUCTION_MESH_OVERRIDES = {
    "D0817_a60": "max060_min006_cpg5_bl4_peel2",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TABLE_KEYS = (
    "n_lead_excluded",
    "excluded_length_m",
    "window_length_m",
    "source_data_root",
    "mesh_id",
    "date",
    "short_window",
)


def _positive_length(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a positive length, got {value!r}.")
    length = float(value)
    if not math.isfinite(length) or length <= 0.0:
        raise ValueError(f"{label} must be a positive finite length, got {value!r}.")
    return length


def _nonnegative_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be an integer >= 0, got {value!r}.")
    return value


def _positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an integer >= 1, got {value!r}.")
    return value


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number, got {value!r}.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    return number


def _below(boundary_m, target_m):
    return boundary_m < target_m - LENGTH_TOL_M


def _at_least(boundary_m, target_m):
    return boundary_m >= target_m - LENGTH_TOL_M


def reference_cell_count(cell_length_m, n_active):
    """Cells in the last 6.93 mm, and at least two."""
    dx = _positive_length(cell_length_m, "cell_length_x_m")
    n_cells = _positive_int(n_active, "n_active")
    count = 0
    # Count whole cells that fit in the last 6.93 mm: (count + 1) * dx <= L_REF.
    while count < n_cells and _at_least(L_REF_M, (count + 1) * dx):
        count += 1
    count = max(MIN_REFERENCE_CELLS, count)
    if count > n_cells:
        raise ValueError(
            "Downstream reference needs the last "
            f"{count} cells (last {L_REF_M} m, at least {MIN_REFERENCE_CELLS}) "
            f"but n_active={n_cells} and cell_length_x_m={dx!r}."
        )
    return count


def cell_excesses(cm, cp, cb):
    """Per-cell CP excess ``(cm - cp) / (cb - cp) - 1`` in active-cell order."""
    if not (len(cm) == len(cp) == len(cb)):
        raise ValueError(
            "cm, cp, and c_b must have one value per active cell, "
            f"got lengths {len(cm)}, {len(cp)}, {len(cb)}."
        )
    if len(cm) == 0:
        raise ValueError("No active-cell concentrations.")
    excess = []
    for index, (cm_i, cp_i, cb_i) in enumerate(zip(cm, cp, cb), start=1):
        cm_value = _finite_number(cm_i, f"active cell {index} cm")
        cp_value = _finite_number(cp_i, f"active cell {index} cp")
        cb_value = _finite_number(cb_i, f"active cell {index} c_b")
        denominator = cb_value - cp_value
        if denominator == 0.0:
            raise ValueError(f"Active cell {index}: c_b - c_p is 0.")
        excess.append((cm_value - cp_value) / denominator - 1.0)
    return excess


def _rolling_means(excess):
    if len(excess) < ROLLING_CELLS:
        raise ValueError(
            f"A {ROLLING_CELLS}-cell rolling mean needs at least "
            f"{ROLLING_CELLS} active cells, got {len(excess)}."
        )
    means = []
    for start in range(0, len(excess) - ROLLING_CELLS + 1):
        window = excess[start : start + ROLLING_CELLS]
        means.append(sum(window) / float(ROLLING_CELLS))
    return means


def development_distance_m(excess, cell_length_m):
    """Development distance from the start of the active zone.

    ``d_m`` is ``None`` when no cell boundary leaves every later 3-cell
    rolling mean within 3% of the downstream reference.
    """
    values = [float(item) for item in excess]
    dx = _positive_length(cell_length_m, "cell_length_x_m")
    ref_count = reference_cell_count(dx, len(values))
    reference = sum(values[-ref_count:]) / float(ref_count)
    if not math.isfinite(reference) or reference <= 0.0:
        raise ValueError(
            "Downstream reference excess must be finite and positive, "
            f"got {reference!r} from the last {ref_count} cells."
        )
    # 1e-12 slack keeps an exact 3% difference inside the closed band when
    # binary float lands just above the decimal. It is not a wider band.
    band = RELATIVE_BAND * reference + 1e-12 * max(reference, 1.0)
    means = _rolling_means(values)
    start_index = None
    for index in range(len(means)):
        if all(abs(item - reference) <= band for item in means[index:]):
            start_index = index
            break
    developed = start_index is not None
    return {
        "developed": developed,
        "d_m": None if not developed else start_index * dx,
        "start_index": start_index,
        "reference_excess": reference,
        "reference_cell_count": ref_count,
    }


def exclusion_for_distance(n_active, cell_length_m, d_m):
    """Lead exclusion for one development distance.

    ``d_m is None`` means the profile never stayed in band. The whole
    active zone is excluded and the window is empty.
    """
    n_cells = _positive_int(n_active, "n_active")
    dx = _positive_length(cell_length_m, "cell_length_x_m")
    if d_m is None:
        n_lead = n_cells
        rule = "not_developed"
    else:
        distance = _finite_number(d_m, "d_m")
        if distance < 0.0:
            raise ValueError(f"d_m must be >= 0, got {distance!r}.")
        k_below = 0
        for k in range(1, n_cells + 1):
            if _below(k * dx, L_BASE_M):
                k_below = k
            else:
                break
        if _at_least(k_below * dx, distance):
            n_lead = k_below
            rule = "boundary_below_l_base"
        else:
            target = max(L_BASE_M, distance)
            n_lead = None
            for k in range(0, n_cells + 1):
                if _at_least(k * dx, target):
                    n_lead = k
                    break
            if n_lead is None:
                n_lead = n_cells
                rule = "exclusion_exceeds_active_length"
            else:
                rule = "at_least_max_l_base_d"
    excluded = n_lead * dx
    window = (n_cells - n_lead) * dx
    return {
        "n_lead_excluded": n_lead,
        "excluded_length_m": excluded,
        "window_length_m": window,
        "short_window": window < SHORT_WINDOW_M - LENGTH_TOL_M,
        "rule": rule,
    }


def relative_bias(excess, n_lead, reference):
    """``(window mean - reference) / reference``. None when the window is empty."""
    n_excluded = _nonnegative_int(n_lead, "n_lead")
    if n_excluded > len(excess):
        raise ValueError(
            f"n_lead={n_excluded} is past n_active={len(excess)}."
        )
    window = excess[n_excluded:]
    if not window:
        return None
    ref = _finite_number(reference, "reference")
    if ref == 0.0:
        raise ValueError("CP-excess bias is undefined when the reference is 0.")
    mean = sum(window) / float(len(window))
    return (mean - ref) / ref


def _read_json_object(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _require_identity(payload, path, expected):
    for key, value in expected.items():
        if key not in payload:
            raise ValueError(f"{path} is missing {key}.")
        if payload[key] != value:
            raise ValueError(
                f"{path} {key}={payload[key]!r} does not match {value!r}."
            )


def _read_wide_row(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing summary CSV: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if len(rows) != 1:
        raise ValueError(
            f"{path} must contain one data row, got {len(rows)}."
        )
    return rows[0]


def _excess_from_row(row, n_buffer_in, n_active, source):
    cm = []
    cp = []
    cb = []
    for local in range(1, n_active + 1):
        cell = n_buffer_in + local
        cm_key = f"pp_cm_mol_m3_cell_{cell}"
        cp_key = f"pp_cp_perm_mol_m3_cell_{cell}"
        cb_key = f"pp_c_b_midplane_cell_{cell}_mol_m3"
        for key in (cm_key, cp_key, cb_key):
            if key not in row or row[key] is None or str(row[key]).strip() == "":
                raise ValueError(f"{source} is missing {key}.")
        cm.append(row[cm_key])
        cp.append(row[cp_key])
        cb.append(row[cb_key])
    try:
        return cell_excesses(cm, cp, cb)
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from exc


def _global_cells(n_buffer_in, first_local, last_local):
    if first_local > last_local:
        return []
    return list(range(n_buffer_in + first_local, n_buffer_in + last_local + 1))


def _cell_span(cells):
    if not cells:
        return "none"
    if len(cells) == 1:
        return str(cells[0])
    return f"{cells[0]}-{cells[-1]}"


def parse_mesh_overrides(items):
    """``GEO_ID=MESH_ID`` pairs. ``None`` selects the production exception."""
    if items is None:
        return dict(PRODUCTION_MESH_OVERRIDES)
    overrides = {}
    for item in items:
        if not isinstance(item, str) or item.count("=") != 1:
            raise ValueError(
                f"Mesh override must be GEO_ID=MESH_ID, got {item!r}."
            )
        geo_id, mesh_id = item.split("=", 1)
        validate_campaign_geo_id(geo_id)
        if MESH_ID_RE.fullmatch(mesh_id) is None:
            raise ValueError(f"Invalid mesh_id {mesh_id!r}.")
        if geo_id in overrides:
            raise ValueError(f"Repeated mesh override for {geo_id}.")
        overrides[geo_id] = mesh_id
    return overrides


def mesh_id_for_geo(geo_id, mesh_id, overrides):
    validate_campaign_geo_id(geo_id)
    if MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"Invalid mesh_id {mesh_id!r}.")
    if geo_id in overrides:
        return overrides[geo_id]
    return mesh_id


def _require_data_root(raw):
    text = str(raw).replace("\\", "/")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"Data root must be absolute, got {raw!r}.")
    if not path.is_dir():
        raise FileNotFoundError(f"Data root is not a directory: {raw}")
    return text, path


def _find_mesh_manifest(roots, family, geo_id, mesh_id):
    found = []
    for label, root in roots:
        path = root / "meshes" / family / geo_id / mesh_id / "manifest.json"
        if path.is_file():
            found.append((label, root, path))
    if not found:
        searched = ", ".join(label for label, _root in roots)
        raise FileNotFoundError(
            f"No mesh manifest for {geo_id} mesh_id={mesh_id} under {searched}."
        )
    if len(found) > 1:
        paths = ", ".join(str(path) for _label, _root, path in found)
        raise RuntimeError(
            f"{geo_id} mesh {mesh_id} is in more than one data root: {paths}."
        )
    return found[0]


def _load_mesh_layout(path, geo_id, mesh_id):
    payload = _read_json_object(path)
    _require_identity(payload, path, {"geo_id": geo_id, "mesh_id": mesh_id})
    for key in ("n_active_cells", "n_buffer_in", "cell_length_x_m"):
        if key not in payload:
            raise ValueError(f"{path} is missing {key}.")
    return {
        "n_active": _positive_int(payload["n_active_cells"], f"{path} n_active_cells"),
        "n_buffer_in": _nonnegative_int(payload["n_buffer_in"], f"{path} n_buffer_in"),
        "cell_length_x_m": _positive_length(
            payload["cell_length_x_m"], f"{path} cell_length_x_m"
        ),
    }


def _operating_point(root, family, geo_id, mesh_id, run_id, layout):
    leaf = root / "runs" / family / geo_id / mesh_id / run_id
    manifest_path = leaf / "manifest.json"
    if not manifest_path.is_file():
        return None, {"run_id": run_id, "reason": "missing"}
    payload = _read_json_object(manifest_path)
    _require_identity(
        payload,
        manifest_path,
        {"geo_id": geo_id, "mesh_id": mesh_id, "run_id": run_id},
    )
    if "convergence_quality" not in payload:
        raise ValueError(f"{manifest_path} is missing convergence_quality.")
    quality = payload["convergence_quality"]
    if quality != "PASS":
        if quality not in ("FAIL", "UNKNOWN"):
            raise ValueError(
                f"{manifest_path} convergence_quality={quality!r} "
                "is not PASS, FAIL, or UNKNOWN."
            )
        return None, {"run_id": run_id, "reason": quality}
    csv_path = leaf / "post" / "reports" / "summary_metrics_wide.csv"
    row = _read_wide_row(csv_path)
    excess = _excess_from_row(
        row, layout["n_buffer_in"], layout["n_active"], csv_path
    )
    if len(excess) != layout["n_active"]:
        raise ValueError(
            f"{csv_path} produced {len(excess)} active cells, "
            f"mesh n_active_cells={layout['n_active']}."
        )
    distance = development_distance_m(excess, layout["cell_length_x_m"])
    return {
        "run_id": run_id,
        "excess": excess,
        "developed": distance["developed"],
        "d_m": distance["d_m"],
        "reference_excess": distance["reference_excess"],
        "reference_cell_count": distance["reference_cell_count"],
    }, None


def evaluate_geometry(data_root_label, data_root, geo_id, mesh_id):
    """One geometry from the mesh manifest and its PASS p6M leaves."""
    family = family_for_geo_id(geo_id)
    _label, _root, manifest_path = _find_mesh_manifest(
        [(data_root_label, Path(data_root))], family, geo_id, mesh_id
    )
    layout = _load_mesh_layout(manifest_path, geo_id, mesh_id)
    points = []
    skipped = []
    for run_id in OPERATING_RUN_IDS:
        point, skip = _operating_point(
            Path(data_root), family, geo_id, mesh_id, run_id, layout
        )
        if skip is not None:
            skipped.append(skip)
        else:
            points.append(point)
    if not points:
        reasons = ", ".join(f"{item['run_id']}={item['reason']}" for item in skipped)
        raise RuntimeError(
            f"{geo_id} has no PASS run among {OPERATING_RUN_IDS}: {reasons}."
        )
    developed = all(point["developed"] for point in points)
    d_max = None if not developed else max(point["d_m"] for point in points)
    exclusion = exclusion_for_distance(
        layout["n_active"],
        layout["cell_length_x_m"],
        d_max if developed else None,
    )
    for point in points:
        point["bias_old"] = relative_bias(
            point["excess"], OLD_N_LEAD, point["reference_excess"]
        )
        point["bias_new"] = relative_bias(
            point["excess"],
            exclusion["n_lead_excluded"],
            point["reference_excess"],
        )
        del point["excess"]
    n_lead = exclusion["n_lead_excluded"]
    return {
        "geo_id": geo_id,
        "family": family,
        "mesh_id": mesh_id,
        "source_data_root": data_root_label,
        "cell_length_x_m": layout["cell_length_x_m"],
        "n_active": layout["n_active"],
        "n_buffer_in": layout["n_buffer_in"],
        "operating_points": points,
        "skipped": skipped,
        "developed": developed,
        "d_max_m": d_max,
        "n_lead_excluded": n_lead,
        "excluded_length_m": exclusion["excluded_length_m"],
        "excluded_global_cells": _global_cells(
            layout["n_buffer_in"], 1, n_lead
        ),
        "window_global_cells": _global_cells(
            layout["n_buffer_in"], n_lead + 1, layout["n_active"]
        ),
        "window_length_m": exclusion["window_length_m"],
        "short_window": exclusion["short_window"],
        "rule": exclusion["rule"],
    }


def evaluate(data_roots, geo_ids, mesh_id, overrides):
    """Evaluate each geo id. The same geo in two roots is an error."""
    if not data_roots:
        raise ValueError("At least one data root is required.")
    if not geo_ids:
        raise ValueError("At least one geo_id is required.")
    roots = [_require_data_root(root) for root in data_roots]
    if MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"Invalid mesh_id {mesh_id!r}.")
    seen = set()
    results = []
    for geo_id in geo_ids:
        validate_campaign_geo_id(geo_id)
        if geo_id in seen:
            raise ValueError(f"Repeated geo_id {geo_id}.")
        seen.add(geo_id)
        selected_mesh = mesh_id_for_geo(geo_id, mesh_id, overrides)
        family = family_for_geo_id(geo_id)
        label, root, _path = _find_mesh_manifest(
            roots, family, geo_id, selected_mesh
        )
        results.append(evaluate_geometry(label, root, geo_id, selected_mesh))
    return results


def resolve_date(value):
    if value is None:
        return datetime.now().astimezone().date().isoformat()
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        raise ValueError(f"Date must be YYYY-MM-DD, got {value!r}.")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _campaign_pillar_ids():
    return [
        geo_id
        for geo_id in CAMPAIGN_GEO_ID_ORDER
        if family_for_geo_id(geo_id) == "pillar"
    ]


def family_default_records(results, date):
    """Pillar default for registered MFBO ids, from the campaign pillars.

    Written only when every campaign pillar was measured and each
    development distance is within 3.5 mm. The lead is three reference
    cells (``L_base``), which covers that distance on the reference pitch.
    Diamond, ml, sin, and empty are not given a default.
    """
    pillars = _campaign_pillar_ids()
    by_id = {result["geo_id"]: result for result in results}
    if any(geo_id not in by_id for geo_id in pillars):
        return {}
    for geo_id in pillars:
        distance = by_id[geo_id]["d_max_m"]
        if distance is None or distance > PILLAR_DEVELOPMENT_WITHIN_M + LENGTH_TOL_M:
            return {}
    return {
        "pillar": {
            "n_lead_excluded": OLD_N_LEAD,
            "excluded_length_m": L_BASE_M,
            "basis": (
                f"all {len(pillars)} campaign pillars develop within "
                f"{PILLAR_DEVELOPMENT_WITHIN_M * 1000:.1f} mm"
            ),
            "date": date,
        }
    }


def table_payload(results, date):
    """geo_id records plus pillar family_defaults when the campaign data supports it."""
    payload = {}
    defaults = family_default_records(results, date)
    if defaults:
        payload["family_defaults"] = defaults
    for result in results:
        payload[result["geo_id"]] = {
            "n_lead_excluded": result["n_lead_excluded"],
            "excluded_length_m": result["excluded_length_m"],
            "window_length_m": result["window_length_m"],
            "source_data_root": result["source_data_root"],
            "mesh_id": result["mesh_id"],
            "date": date,
            "short_window": result["short_window"],
        }
    return payload


def _mm(metres):
    if metres is None:
        return "not developed"
    return f"{metres * 1000.0:.3f}"


def _percent(bias):
    if bias is None:
        return "undefined"
    return f"{100.0 * bias:+.2f}%"


def render_report(results, date):
    """Markdown record of the rule and one row per geometry."""
    flagged = [item["geo_id"] for item in results if item["short_window"]]
    undeveloped = [item["geo_id"] for item in results if not item["developed"]]
    lines = [
        "# Evaluation window from development length",
        "",
        f"Generated {date} by `scripts/mfbo/development_length.py`.",
        "This file is output. Regenerate it with the script; do not hand-edit it.",
        "",
        "Domain and CAD are unchanged. The per-cell CP excess is",
        "`(c_m - c_p) / (c_b - c_p) - 1` from `summary_metrics_wide.csv`.",
        "The mean is an unweighted mean of those cell values.",
        "",
        f"Downstream reference: mean of the cells in the last {L_REF_M * 1000:.3f} mm,",
        f"and at least {MIN_REFERENCE_CELLS} cells.",
        f"Development distance `d` is the first active-zone cell boundary after",
        f"which every {ROLLING_CELLS}-cell rolling mean stays within",
        f"{RELATIVE_BAND * 100:.0f}% of that reference.",
        "The rolling window starting at the next active cell is the window",
        "after that boundary. `d` is the maximum over PASS",
        "`u0p1_p6M`, `u0p2_p6M`, and `u0p3_p6M` leaves that exist.",
        "A missing, FAIL, or UNKNOWN leaf is not an operating point.",
        "An operating point whose rolling mean never stays in band has not",
        "developed; that geometry excludes the whole active zone.",
        "",
        f"`L_base` = {L_BASE_M * 1000:.3f} mm (three {REFERENCE_CELL_M * 1000:.3f} mm cells).",
        "If the cell boundary just below `L_base` is at or beyond `d`,",
        "exclude up to that boundary. Otherwise exclude up to the first",
        "cell boundary at or beyond `max(L_base, d)`.",
        "The evaluation window is the remaining active cells.",
        f"A window shorter than {SHORT_WINDOW_M * 1000:.1f} mm is flagged.",
        "",
        "Production mesh id is `max085_min006_cpg5_bl4_peel2`.",
        "`D0817_a60` uses `max060_min006_cpg5_bl4_peel2`.",
        "The old bias excludes the first 3 active cells.",
        "Bias is `(window mean excess - downstream reference) / reference`.",
        "Negative bias means the window mean is below the reference.",
        "",
        "## Flags",
        "",
    ]
    defaults = family_default_records(results, date)
    if defaults:
        pillar = defaults["pillar"]
        lines.append(
            "MFBO pillar ids use family default "
            f"`n_lead_excluded={pillar['n_lead_excluded']}`, "
            f"`excluded_length_m={pillar['excluded_length_m']}`: "
            f"{pillar['basis']}. Campaign geo_ids keep their own rows. "
            "Diamond, ml, sin, and empty have no family default."
        )
        lines.append("")
    if flagged:
        lines.append(
            "Window shorter than 9.0 mm: " + ", ".join(f"`{geo}`" for geo in flagged) + "."
        )
    else:
        lines.append("No geometry has a window shorter than 9.0 mm.")
    lines.append("")
    if undeveloped:
        lines.append(
            "Not developed at every PASS operating point: "
            + ", ".join(f"`{geo}`" for geo in undeveloped)
            + "."
        )
    else:
        lines.append("Every PASS operating point developed inside the active zone.")
    lines.extend(
        [
            "",
            "## Exclusion",
            "",
            "| geo_id | cell mm | n_active | d max mm | excluded cells | "
            "excluded mm | window cells | window mm | short | rule |",
            "| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- | --- |",
        ]
    )
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    result["geo_id"],
                    _mm(result["cell_length_x_m"]),
                    str(result["n_active"]),
                    _mm(result["d_max_m"]),
                    _cell_span(result["excluded_global_cells"]),
                    _mm(result["excluded_length_m"]),
                    _cell_span(result["window_global_cells"]),
                    _mm(result["window_length_m"]),
                    "yes" if result["short_window"] else "no",
                    result["rule"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Operating points",
            "",
            "| geo_id | run_id | d mm | reference cells | bias old 3-cell | bias new window |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        for point in result["operating_points"]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        result["geo_id"],
                        point["run_id"],
                        _mm(point["d_m"]),
                        str(point["reference_cell_count"]),
                        _percent(point["bias_old"]),
                        _percent(point["bias_new"]),
                    ]
                )
                + " |"
            )
        for skip in result["skipped"]:
            lines.append(
                f"| {result['geo_id']} | {skip['run_id']} |  |  |  | "
                f"not used ({skip['reason']}) |"
            )
    lines.extend(
        [
            "",
            "## Sources",
            "",
            "| geo_id | mesh_id | data root |",
            "| --- | --- | --- |",
        ]
    )
    for result in results:
        lines.append(
            f"| {result['geo_id']} | {result['mesh_id']} | {result['source_data_root']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(table_path, report_path, results, date):
    payload = table_payload(results, date)
    for key, record in payload.items():
        if key == "family_defaults":
            for family, default in record.items():
                if tuple(default) != _FAMILY_DEFAULT_KEYS:
                    raise RuntimeError(
                        f"Family default keys drifted for {family}: {tuple(default)!r}."
                    )
            continue
        if tuple(record) != _TABLE_KEYS:
            raise RuntimeError(f"Table record keys drifted: {tuple(record)!r}.")
    table = Path(table_path)
    report = Path(report_path)
    table.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    report.write_text(render_report(results, date), encoding="utf-8")
    return table, report


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Per-geometry CP development length from PASS p6M summary CSVs. "
            "Does not launch Fluent."
        )
    )
    parser.add_argument(
        "--data-root",
        nargs="+",
        required=True,
        help="Absolute data root. Repeat for more than one root.",
    )
    parser.add_argument(
        "--mesh-id",
        default=PRODUCTION_MESH_ID,
        help=f"Mesh id used unless a geo override applies. Default: {PRODUCTION_MESH_ID}.",
    )
    parser.add_argument(
        "--mesh-override",
        action="append",
        default=None,
        help=(
            "GEO_ID=MESH_ID. Repeatable. Replaces the production override list. "
            "Default when omitted: D0817_a60=max060_min006_cpg5_bl4_peel2."
        ),
    )
    parser.add_argument(
        "--geo-id",
        action="append",
        default=None,
        help="Limit the run to these campaign geo ids. Default: all 31.",
    )
    parser.add_argument("--table", required=True, help="Output JSON path.")
    parser.add_argument("--report", required=True, help="Output Markdown path.")
    parser.add_argument(
        "--date",
        default=None,
        help="Table date YYYY-MM-DD. Default: today's local date.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    overrides = parse_mesh_overrides(args.mesh_override)
    geo_ids = list(args.geo_id) if args.geo_id is not None else list(CAMPAIGN_GEO_ID_ORDER)
    date = resolve_date(args.date)
    results = evaluate(args.data_root, geo_ids, args.mesh_id, overrides)
    table_path, report_path = write_outputs(args.table, args.report, results, date)
    flagged = [item["geo_id"] for item in results if item["short_window"]]
    print(f"geometries: {len(results)}")
    print(
        "short windows: "
        + (", ".join(flagged) if flagged else "none")
    )
    print(table_path)
    print(report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
