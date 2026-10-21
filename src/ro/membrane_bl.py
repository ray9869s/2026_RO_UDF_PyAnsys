"""Optional membrane boundary-layer first height.

``None`` adds no control, no ``mesh_id`` token, and no manifest field. The
existing smooth-transition task is unchanged, including its argument keys.

A request is a target wall-to-centroid distance ``y1`` in micrometres, the
quantity the UDF prints as ``y1 area-weighted mean``. The prism centroid sits
halfway through the first layer, so Fluent ``FirstHeight`` is ``2 * y1``.

The membrane labels use ``last-ratio``. That offset method honours
``FirstHeight``. ``smooth-transition`` does not: it sizes the stack from the
transition ratio and the local face size, and a ``FirstHeight`` entry is
ignored. Buffer and spacer labels stay on today's smooth-transition control
(same first-height entry, growth rate, and layer count).

Layer count keeps today's growth rate and is the integer whose geometric
total thickness is closest to the smooth-transition stack at ``m_max``.
Fluent's default transition ratio is 0.272 (last-layer height / local size).
This worker does not pass ``TransitionRatio``, so that default is what the
current meshes use. The reference size is ``m_max`` because the membrane
face size, and therefore today's ``y1``, follows it.

The token is appended after the local-sizing suffix:

- ``_y1h020`` — requested y1 of 2.0 um (``round(y1_um * 10)``, width 3)

The manifest stores the derived stack. The token stores ``y1`` only.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ro.mesh_common import _integer_token


OFFSET_METHOD_LAST_RATIO = "last-ratio"
# Fluent Meshing smooth-transition default. Not an argument we set.
SMOOTH_TRANSITION_RATIO = 0.272
Y1_REQUEST_REL_TOLERANCE = 0.20
_MAX_LAYERS = 40
_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12

_SPEC_KEYS = (
    "y1_um",
    "first_height_mm",
    "offset_method",
    "layers",
    "growth_rate",
    "reference_total_thickness_mm",
    "total_thickness_mm",
    "labels",
)
_FLOAT_KEYS = (
    "y1_um",
    "first_height_mm",
    "growth_rate",
    "reference_total_thickness_mm",
    "total_thickness_mm",
)

_Y1_MEAN_RE = re.compile(
    r"y1 area-weighted mean\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*um"
)
_Y1_TOKEN_RE = re.compile(r"_y1h(\d{3})$")


def geometric_total_thickness_mm(first_height_mm, layers, growth_rate):
    """Sum of a geometric prism stack, in the same unit as ``first_height_mm``."""
    height = _positive_float("first_height_mm", first_height_mm)
    count = _positive_int("layers", layers)
    growth = _positive_float("growth_rate", growth_rate)
    if math.isclose(growth, 1.0, rel_tol=_FLOAT_REL_TOL, abs_tol=_FLOAT_ABS_TOL):
        return height * count
    return height * (growth**count - 1.0) / (growth - 1.0)


def smooth_transition_first_height_mm(
    size_mm,
    layers,
    growth_rate,
    *,
    transition_ratio=SMOOTH_TRANSITION_RATIO,
):
    """First-layer height implied by smooth-transition at one face size."""
    size = _positive_float("size_mm", size_mm)
    count = _positive_int("layers", layers)
    growth = _positive_float("growth_rate", growth_rate)
    ratio = _positive_float("transition_ratio", transition_ratio)
    return ratio * size / (growth ** (count - 1))


def choose_layer_count(first_height_mm, reference_total_mm, growth_rate):
    """Layer count whose total thickness is closest to ``reference_total_mm``.

    Growth rate is held fixed. A tie keeps the smaller count. The search
    fails if the closest count is still improving at the cap.
    """
    height = _positive_float("first_height_mm", first_height_mm)
    reference = _positive_float("reference_total_mm", reference_total_mm)
    growth = _positive_float("growth_rate", growth_rate)
    best_layers = 1
    best_error = abs(
        geometric_total_thickness_mm(height, 1, growth) - reference
    )
    for layers in range(2, _MAX_LAYERS + 1):
        error = abs(
            geometric_total_thickness_mm(height, layers, growth) - reference
        )
        if error < best_error:
            best_layers = layers
            best_error = error
    if best_layers == _MAX_LAYERS:
        beyond = abs(
            geometric_total_thickness_mm(height, _MAX_LAYERS + 1, growth)
            - reference
        )
        if beyond < best_error:
            raise ValueError(
                "membrane BL layer count is still moving at the cap "
                f"{_MAX_LAYERS}."
            )
    return best_layers


def membrane_bl_stack(
    y1_um,
    *,
    size_mm,
    bl_layers,
    growth_rate,
    labels,
):
    """Canonical last-ratio stack for one requested y1."""
    requested = _y1_um("membrane_bl_y1", y1_um)
    membrane_labels = _label_list("membrane labels", labels)
    layers_today = _positive_int("bl_layers", bl_layers)
    growth = _positive_float("growth_rate", growth_rate)
    first_today = smooth_transition_first_height_mm(
        size_mm, layers_today, growth
    )
    reference_total = geometric_total_thickness_mm(
        first_today, layers_today, growth
    )
    first_height_mm = requested * 2.0e-3
    layers = choose_layer_count(first_height_mm, reference_total, growth)
    return {
        "y1_um": requested,
        "first_height_mm": first_height_mm,
        "offset_method": OFFSET_METHOD_LAST_RATIO,
        "layers": layers,
        "growth_rate": growth,
        "reference_total_thickness_mm": reference_total,
        "total_thickness_mm": geometric_total_thickness_mm(
            first_height_mm, layers, growth
        ),
        "labels": membrane_labels,
    }


def resolve_membrane_bl(
    value,
    *,
    size_mm,
    bl_layers,
    growth_rate,
    labels,
):
    """Return ``None``, or the stack for a number or a stack object.

    An object must match the stack derived from its ``y1_um`` and the
    supplied size, baseline layer count, growth rate, and labels.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"membrane_bl_y1 must be a number or an object, got {value!r}."
        )
    if isinstance(value, (int, float)):
        return membrane_bl_stack(
            value,
            size_mm=size_mm,
            bl_layers=bl_layers,
            growth_rate=growth_rate,
            labels=labels,
        )
    if not isinstance(value, dict):
        raise ValueError(
            "membrane_bl_y1 must be a number or an object, "
            f"got {type(value).__name__}."
        )
    _require_spec_keys(value)
    expected = membrane_bl_stack(
        value["y1_um"],
        size_mm=size_mm,
        bl_layers=bl_layers,
        growth_rate=growth_rate,
        labels=labels,
    )
    if not specs_match(value, expected):
        raise ValueError(
            "membrane_bl_y1 does not match the stack for its y1_um "
            f"at size {size_mm!r} mm, bl_layers {bl_layers!r}, "
            f"growth {growth_rate!r}, labels {list(labels)!r}: "
            f"got {value!r}, expected {expected!r}."
        )
    return expected


def membrane_bl_request_um(value):
    """Requested y1 in um, or ``None``. Does not derive the layer count."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"membrane_bl_y1 must be a number or an object, got {value!r}."
        )
    if isinstance(value, (int, float)):
        return _y1_um("membrane_bl_y1", value)
    if isinstance(value, dict):
        _require_spec_keys(value)
        return _y1_um("membrane_bl_y1.y1_um", value["y1_um"])
    raise ValueError(
        "membrane_bl_y1 must be a number or an object, "
        f"got {type(value).__name__}."
    )


def membrane_bl_suffix(y1_um):
    """``_y1h020`` for 2.0 um, or ``""`` when unset."""
    if y1_um is None:
        return ""
    return "_y1h" + _y1_token(y1_um)


def y1_um_from_mesh_id(mesh_id):
    """Decode a trailing ``_y1hNNN`` token. ``None`` when the id has none."""
    if not isinstance(mesh_id, str):
        return None
    match = _Y1_TOKEN_RE.search(mesh_id)
    if match is None:
        return None
    return int(match.group(1)) / 10.0


def membrane_bl_summary_lines(spec):
    """Input-summary lines. Empty when the override is unset."""
    if spec is None:
        return []
    return [
        f"Membrane BL y1 request [um]: {spec['y1_um']}",
        f"Membrane BL offset method: {spec['offset_method']}",
        f"Membrane BL first height [mm]: {spec['first_height_mm']}",
        f"Membrane BL number of layers [-]: {spec['layers']}",
        f"Membrane BL growth rate [-]: {spec['growth_rate']}",
        (
            "Membrane BL total thickness [mm]: "
            f"{spec['total_thickness_mm']}"
        ),
        (
            "Membrane BL reference total thickness [mm]: "
            f"{spec['reference_total_thickness_mm']}"
        ),
        f"Membrane BL labels: {spec['labels']}",
    ]


def membrane_bl_controls(
    spec,
    *,
    buffer_labels,
    wall_spacer_labels,
    bl_height,
    bl_layers,
    spacer_bl_layers,
    bl_offset_method,
    bl_growth_rate,
    include_spacer_in_boundary_layers,
):
    """Last-ratio membrane control, then the unchanged smooth-transition rest.

    Spacer labels keep ``spacer_bl_layers`` when that count differs from
    ``bl_layers``. Otherwise buffer and spacer share one smooth-transition
    control at ``bl_layers``. ``bl_height``, ``bl_offset_method``, and
    ``bl_growth_rate`` on those controls are the values the single control
    uses today.
    """
    if spec is None:
        raise ValueError("membrane BL controls require a stack.")
    _require_spec_keys(spec)
    if spec["offset_method"] != OFFSET_METHOD_LAST_RATIO:
        raise ValueError(
            "membrane BL offset method must be "
            f"{OFFSET_METHOD_LAST_RATIO!r}, got {spec['offset_method']!r}."
        )
    buffer = _label_list("buffer_labels", buffer_labels)
    spacer = list(wall_spacer_labels) if include_spacer_in_boundary_layers else []
    if include_spacer_in_boundary_layers and not spacer:
        raise ValueError(
            "include_spacer_in_boundary_layers requires wall_spacer_labels."
        )
    membrane = {
        "AddChild": "yes",
        "BLControlName": "last_ratio_mem",
        "BlLabelList": list(spec["labels"]),
        "FaceScope": {"GrowOn": "selected-labels"},
        "FirstHeight": spec["first_height_mm"],
        "NumberOfLayers": spec["layers"],
        "OffsetMethodType": OFFSET_METHOD_LAST_RATIO,
        "Rate": spec["growth_rate"],
    }
    split = _spacer_is_split(bl_layers, spacer_bl_layers)
    if split and not include_spacer_in_boundary_layers:
        raise ValueError(
            "spacer_bl_layers differs from bl_layers, so "
            "include_spacer_in_boundary_layers must be True."
        )
    controls = [membrane]
    if split:
        controls.append(
            _smooth_control(
                "smooth_transition_buffer",
                buffer,
                bl_height,
                bl_layers,
                bl_offset_method,
                bl_growth_rate,
            )
        )
        controls.append(
            _smooth_control(
                "smooth_transition_spacer",
                spacer,
                bl_height,
                spacer_bl_layers,
                bl_offset_method,
                bl_growth_rate,
            )
        )
        return controls
    rest = list(buffer)
    if include_spacer_in_boundary_layers:
        rest.extend(spacer)
    name = (
        "smooth_transition_rest"
        if include_spacer_in_boundary_layers
        else "smooth_transition_buffer"
    )
    controls.append(
        _smooth_control(
            name,
            rest,
            bl_height,
            bl_layers,
            bl_offset_method,
            bl_growth_rate,
        )
    )
    return controls


def specs_match(left, right):
    """True when two stacks describe the same control."""
    if set(left) != set(_SPEC_KEYS) or set(right) != set(_SPEC_KEYS):
        return False
    for key in _FLOAT_KEYS:
        if not _close(left[key], right[key]):
            return False
    if left["offset_method"] != right["offset_method"]:
        return False
    if left["layers"] != right["layers"]:
        return False
    return list(left["labels"]) == list(right["labels"])


def parse_y1_area_weighted_mean_um(text):
    """First ``y1 area-weighted mean`` in a transcript, in micrometres.

    The UDF prints that line once, on the first adjust that sees membrane
    faces. The first match is that print. ``None`` when the line is absent.
    """
    if not isinstance(text, str) or not text:
        return None
    match = _Y1_MEAN_RE.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(
            f"y1 area-weighted mean must be positive, got {match.group(1)!r}."
        )
    return value


def first_y1_transcript_text(case_dir):
    """Text of the newest transcript that contains the y1 diagnostic.

    Searches ``fluent-*.trn`` and ``solver_log_*.txt``. Empty string when
    none of them has the line.
    """
    directory = Path(case_dir)
    candidates = [
        path
        for pattern in ("fluent-*.trn", "solver_log_*.txt")
        for path in directory.glob(pattern)
        if path.is_file()
    ]
    candidates.sort(
        key=lambda path: path.stat().st_mtime if path.is_file() else 0.0,
        reverse=True,
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _Y1_MEAN_RE.search(text):
            return text
    return ""


def y1_request_mismatch(realised_um, requested_um, *, tolerance=Y1_REQUEST_REL_TOLERANCE):
    """True when realised y1 is missing or farther than ``tolerance`` from the request."""
    requested = _positive_float("y1_request_um", requested_um)
    if realised_um is None:
        return True
    realised = _positive_float("y1_udf_area_mean_um", realised_um)
    return abs(realised - requested) / requested > float(tolerance)


def y1_diagnostic_fields(mesh_id, transcript_text):
    """Run-manifest fields from a mesh id and a transcript.

    No request and no diagnostic produces an empty dict. A request with no
    diagnostic sets ``y1_request_mismatch`` true.
    """
    requested = y1_um_from_mesh_id(mesh_id)
    realised = parse_y1_area_weighted_mean_um(transcript_text)
    fields = {}
    if realised is not None:
        fields["y1_udf_area_mean_um"] = realised
    if requested is None:
        return fields
    fields["y1_request_um"] = requested
    fields["y1_request_mismatch"] = y1_request_mismatch(realised, requested)
    if realised is not None:
        fields["y1_request_rel_error"] = abs(realised - requested) / requested
    return fields


def format_y1_request_flag(fields):
    """One FLAG line, or ``None`` when the request is within tolerance."""
    if not isinstance(fields, dict) or not fields.get("y1_request_mismatch"):
        return None
    requested = fields.get("y1_request_um")
    realised = fields.get("y1_udf_area_mean_um")
    if realised is None:
        return (
            "FLAG y1 request: transcript has no 'y1 area-weighted mean' "
            f"for requested {requested} um."
        )
    relative = fields.get("y1_request_rel_error")
    return (
        "FLAG y1 request: realised "
        f"{realised} um vs requested {requested} um "
        f"({relative:.1%} relative, limit {Y1_REQUEST_REL_TOLERANCE:.0%})."
    )


def y1_diagnostic_summary_rows(fields):
    """Summary rows for the transcript y1 and, when requested, the flag."""
    if not fields:
        return []
    rows = []
    if "y1_udf_area_mean_um" in fields:
        rows.append(
            {
                "metric": "y1_udf_area_mean_um",
                "value": fields["y1_udf_area_mean_um"],
                "unit": "um",
            }
        )
    if "y1_request_um" in fields:
        rows.append(
            {
                "metric": "y1_request_um",
                "value": fields["y1_request_um"],
                "unit": "um",
            }
        )
    if "y1_request_rel_error" in fields:
        rows.append(
            {
                "metric": "y1_request_rel_error",
                "value": fields["y1_request_rel_error"],
                "unit": "-",
            }
        )
    if "y1_request_mismatch" in fields:
        rows.append(
            {
                "metric": "y1_request_mismatch",
                "value": fields["y1_request_mismatch"],
                "unit": "-",
            }
        )
    return rows


def _smooth_control(name, labels, bl_height, layers, offset_method, growth_rate):
    return {
        "AddChild": "yes",
        "BLControlName": name,
        "BlLabelList": list(labels),
        "FaceScope": {"GrowOn": "selected-labels"},
        "FirstHeight": bl_height,
        "NumberOfLayers": layers,
        "OffsetMethodType": offset_method,
        "Rate": growth_rate,
    }


def _spacer_is_split(bl_layers, spacer_bl_layers):
    if spacer_bl_layers is None:
        return False
    return int(spacer_bl_layers) != int(bl_layers)


def _require_spec_keys(value):
    missing = [key for key in _SPEC_KEYS if key not in value]
    unknown = sorted(set(value) - set(_SPEC_KEYS))
    if missing or unknown:
        raise ValueError(
            "membrane_bl_y1 keys mismatch: "
            f"missing={missing or '-'}, unknown={unknown or '-'}."
        )


def _y1_um(name, value):
    number = _positive_float(name, value)
    _y1_token(number)
    return number


def _y1_token(y1_um):
    token = _integer_token("membrane_bl_y1_um", y1_um, scale=10.0, width=3)
    if len(token) != 3:
        raise ValueError(
            f"membrane_bl_y1_um {y1_um!r} does not fit the _y1hNNN mesh_id token."
        )
    return token


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


def _close(left, right):
    return math.isclose(
        float(left),
        float(right),
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    )
