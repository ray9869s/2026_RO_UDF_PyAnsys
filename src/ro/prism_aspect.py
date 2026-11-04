"""Prism versus bulk Fluent aspect ratio.

``/mesh/check-quality`` reports one maximum. That number is Fluent's
aspect ratio (a unit cube is about 1.732), not an edge ratio. A
last-ratio membrane prism is anisotropic by design, and a contact
wedge makes it thinner. Orthogonal quality stays the hard limit.
Bulk polyhedra keep the global ceiling.

The split is usable only when cell zones are entirely prism or entirely
bulk. A mixed zone does not waive the global ceiling.
"""

from __future__ import annotations

import math

FLUENT_ASPECT_RATIO = "FLUENT Aspect Ratio"
ORTHOGONAL_QUALITY = "Orthogonal Quality"

_SPLIT_MARKER = "PRISM ASPECT SPLIT"
_SPLIT_END = "END PRISM ASPECT SPLIT"
_SUMMARY_KEYS = (
    "status",
    "prism_limit",
    "prism_zone_count",
    "prism_ar_max",
    "prism_ortho_min",
    "bulk_zone_count",
    "bulk_ar_max",
    "bulk_ortho_min",
    "cells_above_bulk_ar_limit",
)

_PRISM_TOKENS = ("prism", "wedge")
_MIXED_TOKENS = ("mixed", "hybrid")
_BULK_TOKENS = ("poly", "hex", "tet", "pyramid", "cartesian")


def classify_cell_zone_shape(shape):
    """``prism``, ``bulk``, ``mixed``, or ``unknown``."""
    text = str(shape).strip().lower()
    if text.startswith("'"):
        text = text[1:]
    if any(token in text for token in _MIXED_TOKENS):
        return "mixed"
    if any(token in text for token in _PRISM_TOKENS):
        return "prism"
    if any(token in text for token in _BULK_TOKENS):
        return "bulk"
    return "unknown"


def parse_quality_limits(value):
    """``(count, minimum, maximum, average)`` from a scheme return.

    ``None`` when the return is not that four-number list. A printed
    transcript line is not accepted here.
    """
    numbers = _four_numbers(value)
    if numbers is None:
        return None
    count, minimum, maximum, average = numbers
    if count != int(count) or count < 0:
        return None
    if not all(math.isfinite(number) for number in (minimum, maximum, average)):
        return None
    return {
        "count": int(count),
        "minimum": float(minimum),
        "maximum": float(maximum),
        "average": float(average),
    }


def parse_distribution_count(value):
    """Cell count from a one-bin distribution return. ``None`` if unknown."""
    if isinstance(value, bool) or isinstance(value, str):
        return None
    if isinstance(value, (int, float)):
        return _nonnegative_int(value)
    if isinstance(value, (list, tuple)) and value:
        total = 0
        for item in value:
            number = _nonnegative_int(item)
            if number is None:
                return None
            total += number
        return total
    return None


def split_is_usable(prism_limit, aspect_split):
    """True when prism and bulk maxima can replace the global ceiling."""
    if prism_limit is None or not isinstance(aspect_split, dict):
        return False
    if aspect_split.get("status") != "split":
        return False
    return (
        _finite(aspect_split.get("bulk_ar_max"))
        and _finite(aspect_split.get("prism_ar_max"))
    )


def aspect_ratio_exceeds(ar_max, *, bulk_limit, prism_limit, aspect_split):
    """True when the aspect-ratio gate fails.

    A usable split checks bulk cells against ``bulk_limit`` and prism
    cells against ``prism_limit``. Otherwise the global maximum is
    checked against ``bulk_limit``. A missing global maximum is not a
    failure; the caller decides whether an unparsed value fails.
    """
    if bulk_limit is None:
        return False
    if split_is_usable(prism_limit, aspect_split):
        return (
            float(aspect_split["bulk_ar_max"]) > float(bulk_limit)
            or float(aspect_split["prism_ar_max"]) > float(prism_limit)
        )
    if ar_max is None:
        return False
    return float(ar_max) > float(bulk_limit)


def aspect_ratio_failure_message(ar_max, *, bulk_limit, prism_limit, aspect_split):
    """One failure sentence, or ``None`` when the aspect-ratio gate passes."""
    if not aspect_ratio_exceeds(
        ar_max,
        bulk_limit=bulk_limit,
        prism_limit=prism_limit,
        aspect_split=aspect_split,
    ):
        return None
    if split_is_usable(prism_limit, aspect_split):
        parts = []
        bulk = float(aspect_split["bulk_ar_max"])
        prism = float(aspect_split["prism_ar_max"])
        if bulk > float(bulk_limit):
            parts.append(
                f"bulk aspect ratio {bulk} is above the threshold {bulk_limit}"
            )
        if prism > float(prism_limit):
            parts.append(
                f"prism aspect ratio {prism} is above the threshold {prism_limit}"
            )
        return "Mesh quality failed: " + "; ".join(parts) + "."
    return (
        "Mesh quality failed: maximum aspect ratio "
        f"{ar_max} is above the threshold {bulk_limit}."
    )


def format_aspect_split(aspect_split):
    """Transcript block. Zone lines follow the summary keys."""
    if not isinstance(aspect_split, dict):
        raise TypeError(
            f"aspect split must be a dict, got {type(aspect_split).__name__}."
        )
    lines = [_SPLIT_MARKER]
    for key in _SUMMARY_KEYS:
        if key not in aspect_split or aspect_split[key] is None:
            continue
        lines.append(f"{key}: {aspect_split[key]}")
    if aspect_split.get("error"):
        lines.append(f"error: {aspect_split['error']}")
    for zone in aspect_split.get("zones") or []:
        lines.append(
            "zone: "
            f"id={zone['id']} shape={zone['shape']} class={zone['class']} "
            f"count={zone['count']} ar_max={zone['ar_max']} "
            f"ortho_min={zone['ortho_min']}"
        )
    lines.append(_SPLIT_END)
    return "\n".join(lines)


def parse_aspect_split_text(text):
    """The last summary block, or ``None`` when the markers are absent."""
    if not isinstance(text, str) or _SPLIT_MARKER not in text:
        return None
    end = text.rfind(_SPLIT_END)
    start = text.rfind(_SPLIT_MARKER, 0, end if end != -1 else None)
    if start == -1 or end == -1 or end < start:
        return None
    payload = {"zones": []}
    for line in text[start:end].splitlines()[1:]:
        if line.startswith("zone: "):
            payload["zones"].append(_parse_zone_line(line))
            continue
        if ": " not in line:
            continue
        key, raw = line.split(": ", 1)
        if key not in _SUMMARY_KEYS:
            continue
        payload[key] = _parse_summary_value(key, raw.strip())
    if "status" not in payload:
        return None
    if not payload["zones"]:
        payload.pop("zones")
    return payload


def diagnose_aspect_split(meshing, *, bulk_ar_limit, prism_ar_limit):
    """Read-only zone shapes and Fluent quality limits.

    Does not separate zones and does not write a mesh. Quality limits
    use the scheme return value. A return that is not a four-number
    list leaves that zone unparsed, and the global ceiling stays in
    force.
    """
    utilities = meshing.meshing_utilities
    zone_ids = [int(zone_id) for zone_id in utilities.get_cell_zones(filter="*")]
    zones = []
    for zone_id in zone_ids:
        shape = utilities.get_cell_zone_shape(cell_zone_id=zone_id)
        count = utilities.get_cell_zone_count(cell_zone_id_list=[zone_id])
        kind = classify_cell_zone_shape(shape)
        aspect = _zone_limits(meshing, zone_id, FLUENT_ASPECT_RATIO)
        ortho = _zone_limits(meshing, zone_id, ORTHOGONAL_QUALITY)
        zones.append(
            {
                "id": zone_id,
                "shape": str(shape).strip().lstrip("'"),
                "class": kind,
                "count": int(count),
                "ar_max": None if aspect is None else aspect["maximum"],
                "ortho_min": None if ortho is None else ortho["minimum"],
            }
        )
    status = _status_from_zones(zones)
    result = {
        "status": status,
        "prism_limit": float(prism_ar_limit),
        "zones": zones,
    }
    if status == "split":
        result.update(_aggregate(zones))
    high = _cells_above_limit(meshing, zone_ids, bulk_ar_limit)
    if high is not None:
        result["cells_above_bulk_ar_limit"] = high
    return result


def _status_from_zones(zones):
    if not zones:
        return "unavailable"
    kinds = {zone["class"] for zone in zones}
    if "unknown" in kinds:
        return "unknown-shape"
    if "mixed" in kinds:
        return "mixed"
    if "prism" not in kinds:
        return "no-prism"
    if "bulk" not in kinds:
        return "no-bulk"
    for zone in zones:
        if zone["ar_max"] is None or zone["ortho_min"] is None:
            return "unparsed"
    return "split"


def _aggregate(zones):
    def _of(kind):
        selected = [zone for zone in zones if zone["class"] == kind]
        return {
            "count": sum(zone["count"] for zone in selected),
            "ar_max": max(zone["ar_max"] for zone in selected),
            "ortho_min": min(zone["ortho_min"] for zone in selected),
        }

    prism = _of("prism")
    bulk = _of("bulk")
    return {
        "prism_zone_count": prism["count"],
        "prism_ar_max": prism["ar_max"],
        "prism_ortho_min": prism["ortho_min"],
        "bulk_zone_count": bulk["count"],
        "bulk_ar_max": bulk["ar_max"],
        "bulk_ortho_min": bulk["ortho_min"],
    }


def _zone_limits(meshing, zone_id, measure):
    expression = (
        "(tgapi-util-get-cell-quality-limits "
        f"'({int(zone_id)}) \"{measure}\")"
    )
    try:
        value = _eval_scheme(meshing, expression)
    except Exception:
        return None
    return parse_quality_limits(value)


def _cells_above_limit(meshing, zone_ids, bulk_ar_limit):
    if not zone_ids or bulk_ar_limit is None:
        return None
    ids = " ".join(str(int(zone_id)) for zone_id in zone_ids)
    expression = (
        "(tgapi-util-get-cell-mesh-distribution "
        f"'({ids}) \"{FLUENT_ASPECT_RATIO}\" 1 '({float(bulk_ar_limit)} 1e12))"
    )
    try:
        value = _eval_scheme(meshing, expression)
    except Exception:
        return None
    return parse_distribution_count(value)


def _eval_scheme(meshing, expression):
    scheme = getattr(meshing, "scheme", None)
    if scheme is not None and hasattr(scheme, "eval"):
        return scheme.eval(expression)
    nested = getattr(meshing, "scheme_eval", None)
    if nested is not None and hasattr(nested, "scheme_eval"):
        return nested.scheme_eval(expression)
    raise RuntimeError("meshing session has no scheme.eval")


def _four_numbers(value):
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        parts = text.split()
        if len(parts) != 4:
            return None
        try:
            return tuple(float(part) for part in parts)
        except ValueError:
            return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        numbers = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            numbers.append(float(item))
        return tuple(numbers)
    return None


def _parse_summary_value(key, raw):
    if key == "status":
        return raw
    if key in ("prism_zone_count", "bulk_zone_count", "cells_above_bulk_ar_limit"):
        return int(float(raw))
    return float(raw)


def _parse_zone_line(line):
    fields = {}
    for part in line[len("zone: "):].split():
        name, value = part.split("=", 1)
        fields[name] = value
    count = int(fields["count"])
    return {
        "id": int(fields["id"]),
        "shape": fields["shape"],
        "class": fields["class"],
        "count": count,
        "ar_max": _optional_zone_number(fields["ar_max"]),
        "ortho_min": _optional_zone_number(fields["ortho_min"]),
    }


def _optional_zone_number(value):
    if value == "None":
        return None
    return float(value)


def _nonnegative_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or float(value) < 0 or float(value) != int(value):
        return None
    return int(value)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))
