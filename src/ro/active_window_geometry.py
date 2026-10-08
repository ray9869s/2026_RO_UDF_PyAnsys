"""Active-window box, porosity, and the two hydraulic-diameter formulas.

The box is the layout active length times the periodic width times the
campaign channel height. Importing this module does not launch Fluent.
"""

from __future__ import annotations

import math

from ro.campaign_geometry import CAMPAIGN_H_M

ACTIVE_WINDOW_CHANNEL_HEIGHT_M = CAMPAIGN_H_M


def _positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number, got {value!r}.") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be a positive finite number, got {value!r}.")
    return number


def active_window_box_volume_m3(active_length_m, periodic_shift_y_m, channel_height_m):
    """Layout box [m^3]: active length × periodic width × channel height."""
    length = _positive(active_length_m, "active_length_m")
    width = _positive(periodic_shift_y_m, "periodic_shift_y_m")
    height = _positive(channel_height_m, "channel_height_m")
    return length * width * height


def active_window_porosity(fluid_volume_m3, box_volume_m3):
    """Fluid volume divided by the layout box."""
    fluid = _positive(fluid_volume_m3, "fluid_volume_m3")
    box = _positive(box_volume_m3, "box_volume_m3")
    return fluid / box


def projected_membrane_area_m2(active_length_m, periodic_shift_y_m):
    """Projected area of both membranes over the active length, 2 L W [m^2]."""
    length = _positive(active_length_m, "active_length_m")
    width = _positive(periodic_shift_y_m, "periodic_shift_y_m")
    return 2.0 * length * width


def require_active_window_geometry(
    *,
    fluid_volume_m3,
    membrane_area_m2,
    spacer_area_m2,
    box_volume_m3,
    active_length_m,
    periodic_shift_y_m,
    family,
):
    """Abort unless the active-window measurements are physically bounded.

    ``0 < V < V_box``, ``0 < ε <= 1``, membrane area in ``(0, 2 L W]``,
    and spacer area positive for every family other than ``empty``.
    """
    if not isinstance(family, str) or family == "":
        raise RuntimeError(
            f"Active-window family must be a non-empty string, got {family!r}."
        )
    try:
        fluid = float(fluid_volume_m3)
        membrane = float(membrane_area_m2)
        spacer = float(spacer_area_m2)
        box = float(box_volume_m3)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Active-window geometry values must be numeric, "
            f"got volume={fluid_volume_m3!r}, membrane={membrane_area_m2!r}, "
            f"spacer={spacer_area_m2!r}, box={box_volume_m3!r}."
        ) from exc
    if not all(math.isfinite(value) for value in (fluid, membrane, spacer, box)):
        raise RuntimeError(
            "Active-window geometry values must be finite, "
            f"got volume={fluid!r}, membrane={membrane!r}, "
            f"spacer={spacer!r}, box={box!r}."
        )
    if not (0.0 < fluid < box):
        raise RuntimeError(
            "Active-window fluid volume "
            f"{fluid!r} m3 is not inside (0, box {box!r} m3)."
        )
    porosity = fluid / box
    if not (0.0 < porosity <= 1.0):
        raise RuntimeError(
            f"Active-window porosity {porosity!r} is not inside (0, 1]."
        )
    projected = projected_membrane_area_m2(active_length_m, periodic_shift_y_m)
    if not (0.0 < membrane <= projected):
        raise RuntimeError(
            "Active-window membrane area "
            f"{membrane!r} m2 is not inside (0, {projected!r} m2]."
        )
    if family != "empty" and not (spacer > 0.0):
        raise RuntimeError(
            "Active-window spacer area "
            f"{spacer!r} m2 is not positive for family {family!r}."
        )
    if spacer < 0.0:
        raise RuntimeError(
            f"Active-window spacer area {spacer!r} m2 is negative."
        )
    return porosity


def geometric_hydraulic_diameter_m(fluid_volume_m3, membrane_area_m2, spacer_area_m2):
    """d_h = 4 V / (A_membrane + A_spacer). None when an input is unusable."""
    try:
        volume = float(fluid_volume_m3)
        membrane = float(membrane_area_m2)
        spacer = float(spacer_area_m2)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(volume)
        or not math.isfinite(membrane)
        or not math.isfinite(spacer)
        or volume <= 0.0
        or membrane <= 0.0
        or spacer < 0.0
    ):
        return None
    area = membrane + spacer
    if area <= 0.0:
        return None
    return 4.0 * volume / area


def schock_miquel_hydraulic_diameter_m(
    porosity,
    channel_height_m,
    spacer_area_m2,
    box_volume_m3,
):
    """Schock and Miquel cross-check. None when the inputs do not determine it.

    d_h = 4 ε / (2/h + A_spacer / V_box). At ε = 1 the spacer term is
    omitted and d_h = 2 h, which requires A_spacer = 0.
    """
    try:
        eps = float(porosity)
        height = float(channel_height_m)
        spacer = float(spacer_area_m2)
        box = float(box_volume_m3)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(eps)
        or not math.isfinite(height)
        or not math.isfinite(spacer)
        or not math.isfinite(box)
        or eps <= 0.0
        or eps > 1.0
        or height <= 0.0
        or spacer < 0.0
        or box <= 0.0
    ):
        return None
    if eps == 1.0:
        if spacer != 0.0:
            return None
        return 2.0 * height
    denominator = 2.0 / height + spacer / box
    if denominator <= 0.0:
        return None
    return 4.0 * eps / denominator
