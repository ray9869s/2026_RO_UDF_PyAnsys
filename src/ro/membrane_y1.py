"""Near-wall spacing on the evaluation-window membrane face set.

``y1`` is the adjacent-cell wall-to-centroid distance in metres (udm-12 /
UDM_Y1). Quantiles are :func:`ro.cp_concentration_stats.area_weighted_quantile`
on the whole window, then converted to micrometres.
"""

from __future__ import annotations

import math

from ro.cp_concentration_stats import area_weighted_quantile

METRES_TO_UM = 1.0e6
Y1_QUANTILE_Q10 = 0.10
Y1_QUANTILE_MEDIAN = 0.50
Y1_QUANTILE_Q90 = 0.90
Y1_WINDOW_COLUMNS = (
    "y1_window_areamean_um",
    "y1_window_q10_um",
    "y1_window_median_um",
    "y1_window_q90_um",
)


def _um(metres):
    return float(metres) * METRES_TO_UM


def y1_window_resolution(faces):
    """Area-weighted y1 mean and Q_0.10 / Q_0.50 / Q_0.90, in micrometres.

    ``faces`` is the evaluation-window membrane face set (both walls). Each
    face needs ``y1`` in metres and ``area`` in m2. A non-positive or
    non-finite ``y1`` raises with the count and total area of those faces.
    """
    if not faces:
        raise ValueError("y1 resolution needs at least one membrane face.")
    bad_count = 0
    bad_area = 0.0
    values = []
    areas = []
    for face in faces:
        if "y1" not in face:
            raise ValueError(
                "membrane window face is missing y1; udm-12 was not read."
            )
        y1 = float(face["y1"])
        area = float(face["area"])
        if not math.isfinite(area):
            raise ValueError(f"face area must be finite, got {area!r}.")
        if not math.isfinite(y1) or y1 <= 0.0:
            bad_count += 1
            bad_area += area
            continue
        values.append(y1)
        areas.append(area)
    if bad_count:
        raise ValueError(
            f"{bad_count} membrane window faces have y1 <= 0 or non-finite "
            f"(total area {bad_area} m2). This indicates a UDM layout mismatch."
        )
    positive_values = []
    positive_areas = []
    for value, area in zip(values, areas):
        if area > 0.0:
            positive_values.append(value)
            positive_areas.append(area)
    if not positive_values:
        raise ValueError("membrane window has no positive face area.")
    total = sum(positive_areas)
    mean_m = sum(
        value * area for value, area in zip(positive_values, positive_areas)
    ) / total
    return {
        "y1_window_areamean_um": _um(mean_m),
        "y1_window_q10_um": _um(
            area_weighted_quantile(positive_values, positive_areas, Y1_QUANTILE_Q10)
        ),
        "y1_window_median_um": _um(
            area_weighted_quantile(
                positive_values, positive_areas, Y1_QUANTILE_MEDIAN
            )
        ),
        "y1_window_q90_um": _um(
            area_weighted_quantile(positive_values, positive_areas, Y1_QUANTILE_Q90)
        ),
    }


def y1_window_summary_rows(metrics):
    """Four new summary columns. Does not repeat existing metric names."""
    return [
        {"metric": name, "value": metrics[name], "unit": "um"}
        for name in Y1_WINDOW_COLUMNS
    ]
