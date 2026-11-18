"""Identity for study meshes using an explicit hard global aspect ceiling."""

from __future__ import annotations

import math
import re


def aspect_ceiling_suffix(limit):
    """An integral ceiling is encoded exactly, without rounding or collisions."""
    if limit is None:
        return ""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, (int, float))
        or not math.isfinite(limit)
        or limit < 1
        or int(limit) != limit
    ):
        raise ValueError("max-aspect-ratio must be a positive integer ceiling.")
    return f"_ar{int(limit)}"


def recorded_aspect_ceiling_suffix(mesh_id, limit):
    """Validate a suffixed mesh against its recorded/configured ceiling."""
    match = re.search(r"_ar([1-9][0-9]*)$", mesh_id)
    if match is None:
        return ""
    suffix = aspect_ceiling_suffix(limit)
    if suffix != match.group(0):
        raise ValueError("mesh_id aspect ceiling suffix must match max_aspect_ratio_threshold.")
    return suffix


def aspect_ceiling_from_mesh_id(mesh_id):
    match = re.search(r"_ar([1-9][0-9]*)$", mesh_id)
    return None if match is None else int(match.group(1))
