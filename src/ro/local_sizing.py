"""Optional local sizing on top of the global watertight proximity control.

``None`` adds no child, no ``mesh_id`` token, and no manifest field. The
existing proximity child stays on periodic plus spacer labels with the
global cells-per-gap and min size.

A proximity override is one or more label pairs that share a cells-per-gap
and a min size. Each pair is one Add Local Sizing child. Fluent's proximity
control has a single face list, so the child lists both sides and scopes
proximity to faces. Gaps among those labels, including the filament-membrane
contact, use the local cells-per-gap down to the local min size. Max size,
growth rate, and curvature angle stay on the global controls.

A face-size override is one child on the selected labels, with its own size
and growth rate.

Tokens are appended after ``_peelN``, proximity then face size:

- ``_lpx9m003`` — cells per gap 9, min size 0.003 mm
- ``_fs020`` — face size 0.020 mm at growth rate 1.2
- growth rate 1.2 is omitted; any other rate appends ``gNNN``
  (``round(rate * 100)``), for example ``_fs020g115``

The manifest stores the label lists. The token stores the numbers, so two
label sets with the same numbers would share a leaf.
"""

from __future__ import annotations

import math

from ro.mesh_common import _integer_token


# Campaign ``boi_growth_rate``. Omitted from ``_fsNNN`` so the default
# face-size leaf is ``_fs020``, not ``_fs020g120``.
FACE_SIZE_GROWTH_RATE_OMITTED = 1.2
_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12

_PROXIMITY_KEYS = frozenset({"pairs", "cells_per_gap", "min_size_mm"})
_PAIR_KEYS = frozenset({"a", "b"})
_FACE_KEYS = frozenset({"labels", "size_mm", "growth_rate"})


def parse_local_proximity(value):
    """Return ``None`` when unset, or one canonical proximity override."""
    if value is None:
        return None
    _require_object("local_proximity", value, _PROXIMITY_KEYS)
    pairs_value = value["pairs"]
    if not isinstance(pairs_value, (list, tuple)) or not pairs_value:
        raise ValueError(
            "local_proximity pairs must be a non-empty list, "
            f"got {pairs_value!r}."
        )
    pairs = []
    seen = set()
    for index, pair in enumerate(pairs_value):
        name = f"local_proximity.pairs[{index}]"
        _require_object(name, pair, _PAIR_KEYS)
        side_a = _label_list(f"{name}.a", pair["a"])
        side_b = _label_list(f"{name}.b", pair["b"])
        overlap = sorted(set(side_a) & set(side_b))
        if overlap:
            raise ValueError(
                f"{name} lists the same label on both sides: {overlap}."
            )
        identity = (tuple(side_a), tuple(side_b))
        if identity in seen:
            raise ValueError(f"{name} repeats a label pair: {identity!r}.")
        seen.add(identity)
        pairs.append({"a": side_a, "b": side_b})
    cells_per_gap = _positive_int(
        "local_proximity.cells_per_gap", value["cells_per_gap"]
    )
    min_size_mm = _size_mm("local_proximity.min_size_mm", value["min_size_mm"])
    return {
        "pairs": pairs,
        "cells_per_gap": cells_per_gap,
        "min_size_mm": min_size_mm,
    }


def parse_local_face_size(value):
    """Return ``None`` when unset, or one canonical face-size override."""
    if value is None:
        return None
    _require_object("local_face_size", value, _FACE_KEYS)
    labels = _label_list("local_face_size.labels", value["labels"])
    size_mm = _size_mm("local_face_size.size_mm", value["size_mm"])
    growth_rate = _growth_rate(
        "local_face_size.growth_rate", value["growth_rate"]
    )
    return {
        "labels": labels,
        "size_mm": size_mm,
        "growth_rate": growth_rate,
    }


def local_sizing_suffix(local_proximity, local_face_size):
    """``_lpx9m003``, ``_fs020``, both, or ``""`` when both are unset."""
    proximity = parse_local_proximity(local_proximity)
    face = parse_local_face_size(local_face_size)
    parts = []
    if proximity is not None:
        parts.append(
            "_lpx"
            f"{_integer_token('cells_per_gap', proximity['cells_per_gap'])}"
            "m"
            f"{_size_token('min_size_mm', proximity['min_size_mm'])}"
        )
    if face is not None:
        token = f"_fs{_size_token('size_mm', face['size_mm'])}"
        if not _is_omitted_growth(face["growth_rate"]):
            token += (
                "g"
                + _integer_token(
                    "growth_rate", face["growth_rate"], scale=100.0, width=3
                )
            )
        parts.append(token)
    return "".join(parts)


def extra_local_sizing_arguments(
    local_proximity,
    local_face_size,
    *,
    max_size_mm,
    growth_rate,
    curvature_normal_angle,
):
    """Fluent Add Local Sizing children. Empty when both overrides are unset."""
    proximity = parse_local_proximity(local_proximity)
    face = parse_local_face_size(local_face_size)
    if proximity is None and face is None:
        return []
    arguments = []
    if proximity is not None:
        max_size = _positive_float("max_size_mm", max_size_mm)
        growth = _positive_float("growth_rate", growth_rate)
        angle = _positive_float(
            "curvature_normal_angle", curvature_normal_angle
        )
        for index, pair in enumerate(proximity["pairs"], start=1):
            arguments.append(
                {
                    "AddChild": "yes",
                    "BOICellsPerGap": proximity["cells_per_gap"],
                    "BOIControlName": f"proximity_contact_{index}",
                    "BOICurvatureNormalAngle": angle,
                    "BOIExecution": "Proximity",
                    "BOIFaceLabelList": list(pair["a"]) + list(pair["b"]),
                    "BOIGrowthRate": growth,
                    "BOIMaxSize": max_size,
                    "BOIMinSize": proximity["min_size_mm"],
                    "BOIScopeTo": "faces",
                    "BOIZoneorLabel": "label",
                }
            )
    if face is not None:
        arguments.append(
            {
                "AddChild": "yes",
                "BOIControlName": "facesize_local_1",
                "BOIExecution": "Face Size",
                "BOIFaceLabelList": list(face["labels"]),
                "BOIGrowthRate": face["growth_rate"],
                "BOISize": face["size_mm"],
                "BOIZoneorLabel": "label",
            }
        )
    return arguments


def _require_object(name, value, required):
    if not isinstance(value, dict):
        raise ValueError(
            f"{name} must be an object, got {type(value).__name__}."
        )
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise ValueError(
            f"{name} keys mismatch: missing={missing or '-'}, "
            f"unknown={unknown or '-'}."
        )


def _label_list(name, value):
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(
            f"{name} must be a non-empty list of non-empty strings, "
            f"got {value!r}."
        )
    labels = [item for item in value]
    if len(labels) != len(set(labels)):
        raise ValueError(f"{name} has duplicate labels: {labels!r}.")
    return labels


def _positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1, got {value!r}.")
    return value


def _positive_float(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    return number


def _size_mm(name, value):
    number = _positive_float(name, value)
    _size_token(name, number)
    return number


def _size_token(name, value_mm):
    return _integer_token(name, value_mm, scale=1000.0, width=3)


def _growth_rate(name, value):
    number = _positive_float(name, value)
    if _is_omitted_growth(number):
        return FACE_SIZE_GROWTH_RATE_OMITTED
    _integer_token(name, number, scale=100.0, width=3)
    return number


def _is_omitted_growth(value):
    return math.isclose(
        float(value),
        FACE_SIZE_GROWTH_RATE_OMITTED,
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    )
