"""Deterministic 2D channel description.

Coordinates differ from the 3D campaign on purpose. Streamwise is ``x``
and wall-normal is ``y``, with membranes at ``y = ±h/2`` and the channel
mid-line at ``y = 0``. The 3D campaign uses ``z`` as wall-normal. A unit
cell of length ``L`` holds one circular obstacle centered on the mid-line.
``n_pitches`` repeats that cell. Inlet and outlet stay velocity/pressure
boundaries, matching the 3D operating condition rather than a streamwise
periodic pair.

This module does not write CAD. Ansys Discovery ``.dsco`` import is the 3D
path and is not used here. A later mesher should consume ``to_dict()``.
"""

from __future__ import annotations

from ro_2d_pilot.config import PilotConfig

# 1 nm. geo_id tokens round to this grid so the same inputs always match.
LENGTH_RESOLUTION_M = 1e-9

_BOUNDARIES = (
    {"name": "inlet", "role": "velocity_inlet", "location": "x=0"},
    {"name": "outlet", "role": "pressure_outlet", "location": "x=length"},
    {
        "name": "wall_bottom_mem",
        "role": "membrane",
        "location": "y=-height/2",
    },
    {
        "name": "wall_top_mem",
        "role": "membrane",
        "location": "y=+height/2",
    },
    {"name": "wall_spacer", "role": "no_slip_wall", "location": "obstacle"},
)


def length_token(metres: float, prefix: str) -> str:
    """Encode a positive length as ``{prefix}{mm}p{frac6}mm`` at 1 nm."""
    nanometres = int(round(float(metres) / LENGTH_RESOLUTION_M))
    if nanometres <= 0:
        raise ValueError(
            f"{prefix} length token requires a positive length, got {metres!r}."
        )
    whole_mm = nanometres // 1_000_000
    frac_mm = nanometres % 1_000_000
    return f"{prefix}{whole_mm}p{frac_mm:06d}mm"


def geo_id_for(config: PilotConfig) -> str:
    return (
        f"{length_token(config.d_m, 'd')}_"
        f"{length_token(config.L_m, 'L')}_"
        f"{length_token(config.channel_height_m, 'h')}_"
        f"n{config.n_pitches}"
    )


def membrane_area_m2(config: PilotConfig) -> float:
    """Top plus bottom membrane area at the Fluent 2D unit depth."""
    return 2.0 * config.n_pitches * config.L_m * config.unit_depth_m


def describe_geometry(config: PilotConfig) -> dict[str, object]:
    """Return the geometry shared by every fidelity of this design."""
    length_m = config.n_pitches * config.L_m
    half_height = 0.5 * config.channel_height_m
    obstacles = tuple(
        {
            "center_x_m": (index + 0.5) * config.L_m,
            "center_y_m": 0.0,
            "diameter_m": config.d_m,
        }
        for index in range(config.n_pitches)
    )
    return {
        "geo_id": geo_id_for(config),
        "dimension": 2,
        "shape": "rectangle_with_circular_obstacles",
        "d_m": config.d_m,
        "L_m": config.L_m,
        "channel_height_m": config.channel_height_m,
        "n_pitches": config.n_pitches,
        "length_m": length_m,
        "unit_depth_m": config.unit_depth_m,
        "x_min_m": 0.0,
        "x_max_m": length_m,
        "y_min_m": -half_height,
        "y_max_m": half_height,
        "membrane_area_m2": membrane_area_m2(config),
        "membrane_blocked_area_frac": 0.0,
        "obstacles": obstacles,
        "boundaries": _BOUNDARIES,
        "coordinate_note": (
            "2D wall-normal axis is y. The 3D campaign wall-normal axis is z."
        ),
    }
