"""Concentration-statistics CP from per-face membrane cm and Jw.

Existing canonical CP columns are unchanged. These metrics use the cell-stored
membrane UDMs (udm-7, udm-6) on the evaluation-window face set.

``B_perm`` is parsed from ``udfs/260822_RO_UDF.c`` (``static real B_perm``).
"""

from __future__ import annotations

import math

from ro.paths import project_root
from ro.udf_constants import parse_udf_membrane_constants

PRODUCTION_UDF_RELATIVE = "udfs/260822_RO_UDF.c"
CM_QUANTILE_HI = 0.999
CM_QUANTILE_99 = 0.99
QUANTILE_DEFINITION = (
    "smallest cm with cumulative area fraction >= p after sorting by cm"
)


def _production_udf_constants() -> dict:
    path = project_root() / PRODUCTION_UDF_RELATIVE
    source = path.read_text(encoding="utf-8")
    return parse_udf_membrane_constants(source)


def production_b_perm_m_per_s() -> float:
    """Salt permeability from the production UDF source, in m/s."""
    return float(_production_udf_constants()["b_perm"])


def production_c_inlet_ref_mol_per_m3() -> float:
    """Inlet reference concentration from the production UDF, in mol/m3."""
    return float(_production_udf_constants()["c_inlet_ref"])


_PRODUCTION_UDF_CONSTANTS = _production_udf_constants()
B_PERM_M_PER_S = float(_PRODUCTION_UDF_CONSTANTS["b_perm"])
C_INLET_REF_MOL_PER_M3 = float(_PRODUCTION_UDF_CONSTANTS["c_inlet_ref"])


def polygon_area(points):
    """Area of a 3D polygon (Newell)."""
    count = len(points)
    if count < 3:
        return 0.0
    nx = ny = nz = 0.0
    for index in range(count):
        x1, y1, z1 = points[index]
        x2, y2, z2 = points[(index + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def cell_x_bounds(boundaries, cell_number):
    """x-range of one global unit cell, matching the extraction iso-clip."""
    cell = int(cell_number)
    if cell < 1 or cell >= len(boundaries):
        raise ValueError(
            f"cell {cell_number} is outside {len(boundaries) - 1} unit cells."
        )
    return float(boundaries[cell - 1]), float(boundaries[cell])


def window_x_bounds(boundaries, cell_numbers):
    """x-range covering a contiguous evaluation window in one clip.

    A single clip counts each face once. A non-contiguous window has no
    single clip that matches the cells extraction uses.
    """
    cells = [int(cell) for cell in cell_numbers]
    if not cells:
        raise ValueError("evaluation window has no cells.")
    if cells != list(range(cells[0], cells[-1] + 1)):
        raise ValueError(
            "evaluation cells must be a contiguous x-range for one window "
            f"clip, got {cells}."
        )
    x_min, _x_max = cell_x_bounds(boundaries, cells[0])
    _x_min, x_max = cell_x_bounds(boundaries, cells[-1])
    return x_min, x_max


def area_weighted_quantile(values, areas, p):
    """Smallest value whose cumulative area fraction is at least ``p``.

    Sort by value (ties keep input order). Walk positive-area samples and
    return the first value at which the cumulative area reaches ``p`` times
    the total positive area. ``p == 0`` is the minimum and ``p == 1`` is the
    maximum. This is one quantile of the whole sample, not an average of
    quantiles computed on subsets.
    """
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise TypeError(f"quantile p must be a float in [0, 1], got {p!r}.")
    if p < 0.0 or p > 1.0:
        raise ValueError(f"quantile p must be in [0, 1], got {p!r}.")
    pairs = []
    for value, area in zip(values, areas):
        if value is None or area is None:
            continue
        value_f = float(value)
        area_f = float(area)
        if not math.isfinite(value_f) or not math.isfinite(area_f):
            continue
        if area_f <= 0.0:
            continue
        pairs.append((value_f, area_f))
    if not pairs:
        raise ValueError("Area quantile needs at least one face with positive area.")
    pairs.sort(key=lambda item: item[0])
    total = sum(area for _value, area in pairs)
    if total <= 0.0:
        raise ValueError("Area quantile total area is not positive.")
    covered = 0.0
    target = float(p) * total
    for value, area in pairs:
        covered += area
        if covered >= target:
            return value
    return pairs[-1][0]


def cp_face_mol_per_m3(cm, jw, b_perm=B_PERM_M_PER_S):
    """``B_perm * cm / (Jw + B_perm)`` for one face."""
    b_perm = float(b_perm)
    cm = float(cm)
    jw = float(jw)
    if not math.isfinite(b_perm) or b_perm == 0.0:
        raise ValueError(f"B_perm must be finite and non-zero, got {b_perm!r}.")
    if not math.isfinite(cm) or not math.isfinite(jw):
        raise ValueError(f"cm and Jw must be finite, got cm={cm!r}, Jw={jw!r}.")
    denominator = jw + b_perm
    if denominator == 0.0:
        raise ValueError(f"Jw + B_perm is zero (Jw={jw!r}, B_perm={b_perm!r}).")
    return b_perm * cm / denominator


def _require_faces(faces):
    if not faces:
        raise ValueError("concentration CP needs at least one membrane face.")
    return list(faces)


def reference_permeate(faces, *, b_perm=B_PERM_M_PER_S):
    """Area-mean and flux-weighted permeate concentration.

    ``cp_ref_flux`` uses only faces with ``Jw > 0``. Faces with ``Jw <= 0``
    are counted and their area is summed. Raises if ``sum(Jw*A)`` over
    ``Jw > 0`` is not positive.
    """
    prepared = []
    n_nonpositive = 0
    area_nonpositive = 0.0
    for face in _require_faces(faces):
        cm = float(face["cm"])
        jw = float(face["jw"])
        area = float(face["area"])
        if not math.isfinite(area):
            raise ValueError(f"face area must be finite, got {area!r}.")
        cp = cp_face_mol_per_m3(cm, jw, b_perm)
        prepared.append({"cm": cm, "jw": jw, "area": area, "cp": cp})
        if jw <= 0.0:
            n_nonpositive += 1
            area_nonpositive += area
    area_cp = 0.0
    area_cm = 0.0
    area_sum = 0.0
    flux_cp = 0.0
    flux_weight = 0.0
    for face in prepared:
        if face["area"] <= 0.0:
            continue
        area_sum += face["area"]
        area_cp += face["cp"] * face["area"]
        area_cm += face["cm"] * face["area"]
        if face["jw"] > 0.0:
            weight = face["jw"] * face["area"]
            flux_weight += weight
            flux_cp += face["cp"] * weight
    if area_sum <= 0.0:
        raise ValueError("membrane window has no positive face area.")
    if flux_weight <= 0.0:
        raise ValueError(
            "sum(Jw*A) over faces with Jw > 0 is not positive "
            f"({flux_weight!r})."
        )
    return {
        "faces": prepared,
        "cp_ref_area": area_cp / area_sum,
        "cp_ref_flux": flux_cp / flux_weight,
        "cm_area_mean": area_cm / area_sum,
        "n_faces_jw_nonpositive": n_nonpositive,
        "area_jw_nonpositive": area_nonpositive,
    }


def _cp_ratio(numerator_cm, cp_ref, c_b, *, label):
    denominator = float(c_b) - float(cp_ref)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError(
            f"c_b - cp_ref_{label} must be positive, got c_b={c_b!r}, "
            f"cp_ref={cp_ref!r}."
        )
    return (float(numerator_cm) - float(cp_ref)) / denominator


def distribution_cp_metrics(faces, c_b, *, b_perm=B_PERM_M_PER_S):
    """CP ratios for one face distribution against one bulk concentration.

    Quantiles are :func:`area_weighted_quantile` on this distribution.
    """
    if c_b is None or not math.isfinite(float(c_b)):
        raise ValueError(f"c_b must be finite, got {c_b!r}.")
    refs = reference_permeate(faces, b_perm=b_perm)
    prepared = refs["faces"]
    cm_values = [face["cm"] for face in prepared]
    areas = [face["area"] for face in prepared]
    cm_q999 = area_weighted_quantile(cm_values, areas, CM_QUANTILE_HI)
    cm_q99 = area_weighted_quantile(cm_values, areas, CM_QUANTILE_99)
    metrics = {
        "cm_area_mean": refs["cm_area_mean"],
        "cm_q999": cm_q999,
        "cm_q99": cm_q99,
        "cp_ref_area": refs["cp_ref_area"],
        "cp_ref_flux": refs["cp_ref_flux"],
        "n_faces_jw_nonpositive": refs["n_faces_jw_nonpositive"],
        "area_jw_nonpositive": refs["area_jw_nonpositive"],
        "c_b": float(c_b),
    }
    for reference in ("area", "flux"):
        cp_ref = refs[f"cp_ref_{reference}"]
        metrics[f"cpc_avg_{reference}"] = _cp_ratio(
            refs["cm_area_mean"], cp_ref, c_b, label=reference
        )
        metrics[f"cp_q999_{reference}"] = _cp_ratio(
            cm_q999, cp_ref, c_b, label=reference
        )
        metrics[f"cp_q99_{reference}"] = _cp_ratio(
            cm_q99, cp_ref, c_b, label=reference
        )
    return metrics


LIVE_EXCLUSION_RULE = "c_p >= min(c_0, c_b)"
# udm-8 is Jw * MS_TO_LMH. Same factor as udfs/260822_RO_UDF.c.
LMH_PER_METRE_PER_SECOND = 3_600_000.0


def live_exclusion_threshold(c_b, c_0):
    """Permeate concentration at or above which a face is outside the canon.

    The modulus divides by ``c_b - c_p``. The UDF dead zone is ``c_p >= c_0``
    (udm-9 stored as 0). When the window bulk sits below the inlet reference,
    a face with ``c_b <= c_p < c_0`` is still in the udm-9 test and makes the
    denominator non-positive. The exclusion is the same value the denominator
    uses: ``min(c_0, c_b)``.
    """
    bulk = float(c_b)
    inlet = float(c_0)
    if not math.isfinite(bulk) or not math.isfinite(inlet) or inlet <= 0.0:
        raise ValueError(
            f"c_b and c_0 must be finite and c_0 positive, got c_b={c_b!r}, "
            f"c_0={inlet!r}."
        )
    return min(bulk, inlet)


def select_positive_jw(columns):
    """First Jw column with a sample above zero.

    ``columns`` is ``(label, values)`` in read order. A column that is not
    above zero but contains a negative sample is the last resort, with the
    sign flipped: a membrane wall with no spacer on the other side can
    report that flux on the inward normal. Returns None when every sample
    is zero.
    """
    flipped = None
    for label, values in columns:
        numbers = [float(value) for value in values]
        if any(value > 0.0 for value in numbers):
            return label, numbers
        if flipped is None and any(value < 0.0 for value in numbers):
            flipped = (f"{label} absolute", [-value for value in numbers])
    return flipped


def live_canon_window(
    faces,
    c_b,
    *,
    b_perm=B_PERM_M_PER_S,
    c_inlet_ref=C_INLET_REF_MOL_PER_M3,
):
    """Area-weighted local canon on faces below the exclusion threshold.

    Each live face uses its own permeate concentration
    ``c_p = B c_m / (J_w + B)`` and the window mixing-cup ``c_b``:

        M = (c_m - c_p) / (c_b - c_p)

    A face with ``c_p >= min(c_0, c_b)`` is the dead zone. The UDF stores
    udm-9 as 0 when ``c_p >= c_0``. When ``c_b < c_0``, faces with
    ``c_b <= c_p < c_0`` are excluded too, because they are the faces that
    make ``c_b - c_p`` non-positive. Those faces stay in
    ``dead_zone_area_fraction`` and are left out of the average.
    """
    c_b = float(c_b)
    c0 = float(c_inlet_ref)
    threshold = live_exclusion_threshold(c_b, c0)
    total_area = 0.0
    dead_area = 0.0
    live_area = 0.0
    weighted = 0.0
    for face in _require_faces(faces):
        area = float(face["area"])
        if not math.isfinite(area):
            raise ValueError(f"face area must be finite, got {area!r}.")
        if area <= 0.0:
            continue
        cp = cp_face_mol_per_m3(face["cm"], face["jw"], b_perm)
        total_area += area
        if cp >= threshold:
            dead_area += area
            continue
        denominator = c_b - cp
        if not math.isfinite(denominator) or denominator <= 0.0:
            raise ValueError(
                "live canon needs c_b - c_p > 0 on every face below "
                f"{LIVE_EXCLUSION_RULE}, got c_b={c_b!r}, "
                f"c_p={cp!r}, cm={face['cm']!r}."
            )
        modulus = (float(face["cm"]) - cp) / denominator
        weighted += modulus * area
        live_area += area
    if total_area <= 0.0:
        raise ValueError("membrane window has no positive face area.")
    if live_area <= 0.0:
        raise ValueError(
            "live canon has no face below the exclusion "
            f"{LIVE_EXCLUSION_RULE} "
            f"(threshold {threshold!r}, dead area {dead_area!r} of "
            f"{total_area!r})."
        )
    return {
        "cp_canon_live_avg": weighted / live_area,
        "dead_zone_area_fraction": dead_area / total_area,
        "live_exclusion_threshold_mol_m3": threshold,
        "live_exclusion_rule": LIVE_EXCLUSION_RULE,
    }


def monitor_style_cm_means(
    faces,
    c_b,
    *,
    b_perm=B_PERM_M_PER_S,
    c_inlet_ref=C_INLET_REF_MOL_PER_M3,
):
    """Area-weighted udm-7 means with and without dead-zone faces.

    The solve-time ``cp_window`` monitor is the all-faces mean divided by
    the window bulk concentration. A face at or above
    ``min(c_0, c_b)`` is the dead zone and is omitted from the live mean.
    That is the same test as :func:`live_canon_window`.
    ``cm_area_mean_window_live`` is None when every face is dead.
    """
    threshold = live_exclusion_threshold(c_b, c_inlet_ref)
    total_area = 0.0
    total_weighted = 0.0
    live_area = 0.0
    live_weighted = 0.0
    dead_area = 0.0
    for face in _require_faces(faces):
        area = float(face["area"])
        if not math.isfinite(area):
            raise ValueError(f"face area must be finite, got {area!r}.")
        if area <= 0.0:
            continue
        cm = float(face["cm"])
        if not math.isfinite(cm):
            raise ValueError(f"cm must be finite, got {cm!r}.")
        total_area += area
        total_weighted += cm * area
        cp = cp_face_mol_per_m3(face["cm"], face["jw"], b_perm)
        if cp >= threshold:
            dead_area += area
            continue
        live_area += area
        live_weighted += cm * area
    if total_area <= 0.0:
        raise ValueError("membrane window has no positive face area.")
    return {
        "cm_area_mean_window": total_weighted / total_area,
        "cm_area_mean_window_live": (
            None if live_area <= 0.0 else live_weighted / live_area
        ),
        "dead_zone_area_fraction": dead_area / total_area,
    }


def monitor_style_cp(means, c_b):
    """``cp_window``-style ratio: area-mean udm-7 / window ``c_b``.

    This is not the live canon. ``c_b`` is one value for the window, so
    the ratio of means equals the mean of ``cm / c_b``.
    """
    bulk = float(c_b)
    if not math.isfinite(bulk) or bulk == 0.0:
        raise ValueError(f"c_b must be finite and non-zero, got {c_b!r}.")
    live = means["cm_area_mean_window_live"]
    return {
        "cp_monitor_style_window": float(means["cm_area_mean_window"]) / bulk,
        "cp_monitor_style_live": None if live is None else float(live) / bulk,
    }


def concentration_cp_report(
    window_faces,
    cell_faces,
    c_b_window,
    c_b_by_cell,
    *,
    b_perm=B_PERM_M_PER_S,
    c_inlet_ref=C_INLET_REF_MOL_PER_M3,
):
    """Window metrics plus per-cell diagnostics.

    The window quantile is computed on ``window_faces`` directly. Per-cell
    quantiles are stored separately and are not averaged into the window
    value. Window ratios use the window mid-plane ``c_b``. Each cell uses
    that cell's mid-plane ``c_b``.
    """
    window = distribution_cp_metrics(window_faces, c_b_window, b_perm=b_perm)
    live = live_canon_window(
        window_faces,
        c_b_window,
        b_perm=b_perm,
        c_inlet_ref=c_inlet_ref,
    )
    monitor_cm = monitor_style_cm_means(
        window_faces,
        c_b_window,
        b_perm=b_perm,
        c_inlet_ref=c_inlet_ref,
    )
    cells = {}
    for cell_number, faces in cell_faces.items():
        cell = int(cell_number)
        if cell not in c_b_by_cell and str(cell) not in c_b_by_cell:
            raise ValueError(f"c_b_by_cell missing evaluation cell {cell}.")
        c_b_cell = c_b_by_cell[cell] if cell in c_b_by_cell else c_b_by_cell[str(cell)]
        cells[str(cell)] = distribution_cp_metrics(faces, c_b_cell, b_perm=b_perm)
    return {
        "b_perm_m_per_s": float(b_perm),
        "quantile_definition": QUANTILE_DEFINITION,
        "c_b_window_mol_m3": float(c_b_window),
        "c_inlet_ref_mol_m3": float(c_inlet_ref),
        "cp_canon_live_window_avg": live["cp_canon_live_avg"],
        "dead_zone_area_fraction_window": live["dead_zone_area_fraction"],
        "live_exclusion_threshold_mol_m3": live["live_exclusion_threshold_mol_m3"],
        "live_exclusion_rule": live["live_exclusion_rule"],
        "monitor_cm": monitor_cm,
        "monitor_cp": monitor_style_cp(monitor_cm, c_b_window),
        "window": window,
        "cells": cells,
    }


def _row(metric, value, unit):
    return {"metric": metric, "value": value, "unit": unit}


def concentration_cp_summary_rows(report, *, c1_area_mean_window=None):
    """New summary columns only. Does not repeat existing CP or c_b names.

    ``c1_area_mean_window`` is the raw first-cell concentration on the same
    faces as ``cm_area_mean_window`` (reconstructed UDM_CM). It is omitted
    when the salt mass fraction was not read.
    """
    window = report["window"]
    rows = [
        _row("cm_area_mean_window", window["cm_area_mean"], "mol/m3"),
    ]
    if c1_area_mean_window is not None:
        rows.append(
            _row("c1_area_mean_window", c1_area_mean_window, "mol/m3")
        )
    rows.extend([
        _row("cm_q999_window", window["cm_q999"], "mol/m3"),
        _row("cm_q99_window", window["cm_q99"], "mol/m3"),
        _row("cp_ref_area", window["cp_ref_area"], "mol/m3"),
        _row("cp_ref_flux", window["cp_ref_flux"], "mol/m3"),
        _row("n_faces_jw_nonpositive", window["n_faces_jw_nonpositive"], "-"),
        _row("area_jw_nonpositive", window["area_jw_nonpositive"], "m2"),
        _row("cpc_window_avg_area", window["cpc_avg_area"], "-"),
        _row("cpc_window_avg_flux", window["cpc_avg_flux"], "-"),
        _row("cp_q999_window_area", window["cp_q999_area"], "-"),
        _row("cp_q999_window_flux", window["cp_q999_flux"], "-"),
        _row("cp_q99_window_area", window["cp_q99_area"], "-"),
        _row("cp_q99_window_flux", window["cp_q99_flux"], "-"),
        _row(
            "cp_canon_live_window_avg",
            report["cp_canon_live_window_avg"],
            "-",
        ),
        _row(
            "dead_zone_area_fraction_window",
            report["dead_zone_area_fraction_window"],
            "-",
        ),
        _row(
            "live_exclusion_threshold_mol_m3",
            report.get("live_exclusion_threshold_mol_m3"),
            "mol/m3",
        ),
        _row(
            "live_exclusion_rule",
            report.get("live_exclusion_rule"),
            "-",
        ),
    ])
    if "monitor_cm" in report and "monitor_cp" in report:
        rows.extend([
            _row(
                "cm_area_mean_window_live",
                report["monitor_cm"]["cm_area_mean_window_live"],
                "mol/m3",
            ),
            _row(
                "cp_monitor_style_window",
                report["monitor_cp"]["cp_monitor_style_window"],
                "-",
            ),
            _row(
                "cp_monitor_style_live",
                report["monitor_cp"]["cp_monitor_style_live"],
                "-",
            ),
        ])
    for cell, metrics in sorted(report["cells"].items()):
        rows.extend(
            [
                _row(f"cm_area_mean_cell_{cell}", metrics["cm_area_mean"], "mol/m3"),
                _row(f"cm_q999_cell_{cell}", metrics["cm_q999"], "mol/m3"),
                _row(f"cm_q99_cell_{cell}", metrics["cm_q99"], "mol/m3"),
                _row(f"cp_ref_area_cell_{cell}", metrics["cp_ref_area"], "mol/m3"),
                _row(f"cp_ref_flux_cell_{cell}", metrics["cp_ref_flux"], "mol/m3"),
                _row(
                    f"n_faces_jw_nonpositive_cell_{cell}",
                    metrics["n_faces_jw_nonpositive"],
                    "-",
                ),
                _row(
                    f"area_jw_nonpositive_cell_{cell}",
                    metrics["area_jw_nonpositive"],
                    "m2",
                ),
                _row(f"cpc_cell_{cell}_avg_area", metrics["cpc_avg_area"], "-"),
                _row(f"cpc_cell_{cell}_avg_flux", metrics["cpc_avg_flux"], "-"),
                _row(f"cp_q999_cell_{cell}_area", metrics["cp_q999_area"], "-"),
                _row(f"cp_q999_cell_{cell}_flux", metrics["cp_q999_flux"], "-"),
                _row(f"cp_q99_cell_{cell}_area", metrics["cp_q99_area"], "-"),
                _row(f"cp_q99_cell_{cell}_flux", metrics["cp_q99_flux"], "-"),
            ]
        )
    return rows
