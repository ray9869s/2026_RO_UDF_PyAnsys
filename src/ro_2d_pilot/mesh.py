"""Mesh-fidelity specification.

Low and high fidelity share the geometry from ``describe_geometry`` and
differ only in these discretization knobs. The division counts are an
execution floor so the first low-fidelity case can see the obstacle.
They are not a completed fidelity study, and they are not the 3D
poly-hexcore campaign (``max085_min006_cpg5_bl4_peel2``).

The algebraic O-grid mesher does not extrude prism layers. ``boundary_layers``
and ``boundary_layers_actual`` are the number generated (0).
``boundary_layers_requested`` records a later intent that this mesher
does not apply. The high mesh is a uniformly refined structured quad
mesh, named as a fine reference for the first LF/HF comparison.

``min_size_m`` is ``max_size_m`` divided by a recorded factor. The mesher
does not read it. The shortest edge is whatever the O-grid produces and
is stored later as ``actual_min_edge_m``.
"""

from __future__ import annotations

from ro_2d_pilot.config import (
    FIDELITY_HIGH,
    FIDELITY_LOW,
    MESH_LEVEL_COARSE,
    MESH_LEVEL_FINE,
    MESH_LEVEL_MEDIUM,
    MESH_LEVEL_VERY_FINE,
    PilotConfig,
)

# One target edge length, H / divisions, sets every interval. There is
# no separate aspect-ratio control. Integer rounding means a doubled
# division count is not an exact refinement ratio.
_HEIGHT_DIVISIONS = {
    MESH_LEVEL_COARSE: 24,
    MESH_LEVEL_MEDIUM: 48,
    MESH_LEVEL_FINE: 96,
    MESH_LEVEL_VERY_FINE: 192,
}
# Requested prism layers. This mesher generates none.
_BOUNDARY_LAYERS_REQUESTED = {
    FIDELITY_LOW: 0,
    FIDELITY_HIGH: 4,
}
# min edge = max edge / this factor. High also refines the smallest edge.
_MIN_SIZE_FACTOR = {
    FIDELITY_LOW: 4.0,
    FIDELITY_HIGH: 8.0,
}
# Recorded so a design with a thin filament is visible before meshing.
_OBSTACLE_CELLS_OK = 6.0

MESH_ROLE = {
    FIDELITY_LOW: "coarse structured execution floor",
    FIDELITY_HIGH: "fine reference mesh for initial LF/HF qualification",
}
MESH_LEVEL_ROLE = {
    MESH_LEVEL_COARSE: "mesh-study level; uniform spacing target shared with low",
    MESH_LEVEL_MEDIUM: "mesh-study level",
    MESH_LEVEL_FINE: "mesh-study level; uniform spacing target shared with high",
    MESH_LEVEL_VERY_FINE: "mesh-study level; finest uniform spacing in the ladder",
}
BOUNDARY_LAYER_TREATMENT = "uniform_structured_no_prism_layers"


def square_half_size_m(radius_m: float, half_height_m: float, half_pitch_m: float) -> float:
    """Square half-size strictly between the circle and the channel limits."""
    limit = min(half_height_m, half_pitch_m)
    if not radius_m < limit:
        raise ValueError(
            "The filament does not fit inside one pitch of the channel "
            f"(radius_m={radius_m!r}, limit_m={limit!r})."
        )
    return 0.5 * (radius_m + limit)


def interval_count(length_m: float, max_size_m: float, minimum: int) -> int:
    count = int(round(length_m / max_size_m))
    return max(minimum, count)


def resolution_layout(config: PilotConfig) -> dict[str, float | int]:
    """Interval counts the algebraic mesher will use for ``config``."""
    divisions = _HEIGHT_DIVISIONS[config.mesh_level]
    max_size_m = config.channel_height_m / divisions
    radius_m = 0.5 * config.d_m
    half_height_m = 0.5 * config.channel_height_m
    half_pitch_m = 0.5 * config.L_m
    square_half = square_half_size_m(radius_m, half_height_m, half_pitch_m)
    n_side = interval_count(2.0 * square_half, max_size_m, 4)
    n_radial = interval_count(square_half - radius_m, max_size_m, 2)
    n_gap = interval_count(half_height_m - square_half, max_size_m, 2)
    n_stream = interval_count(half_pitch_m - square_half, max_size_m, 2)
    height_cells = n_side + 2 * n_gap
    per_pitch = (
        4 * n_side * n_radial
        + 2 * n_stream * height_cells
        + 2 * n_side * n_gap
    )
    return {
        "max_size_m": max_size_m,
        "square_half_size_m": square_half,
        "n_side": n_side,
        "n_radial": n_radial,
        "n_gap": n_gap,
        "n_stream": n_stream,
        "cells_across_channel_height": height_cells,
        "circumferential_segments": 4 * n_side,
        "streamwise_intervals_per_half_pitch": n_stream,
        "near_membrane_intervals": n_gap,
        "near_membrane_spacing_m": (half_height_m - square_half) / n_gap,
        "topology_cell_count": config.n_pitches * per_pitch,
    }


def mesh_spec(config: PilotConfig) -> dict[str, object]:
    layout = resolution_layout(config)
    level = config.mesh_level
    max_size_m = float(layout["max_size_m"])
    min_size_factor = _MIN_SIZE_FACTOR.get(config.fidelity, 4.0)
    min_size_m = max_size_m / min_size_factor
    cells_across_diameter = config.d_m / max_size_m
    requested_layers = _BOUNDARY_LAYERS_REQUESTED.get(config.fidelity, 0)
    return {
        "fidelity": config.fidelity,
        "mesh_level": level,
        "mesh_role": MESH_ROLE.get(config.fidelity, MESH_LEVEL_ROLE[level]),
        "height_divisions": _HEIGHT_DIVISIONS[level],
        "channel_height_divisions": _HEIGHT_DIVISIONS[level],
        "streamwise_resolution": layout["streamwise_intervals_per_half_pitch"],
        "obstacle_circumferential_resolution": layout["circumferential_segments"],
        "obstacle_radial_resolution": layout["n_radial"],
        "near_wall_spacing_m": layout["near_membrane_spacing_m"],
        "estimated_cell_count": layout["topology_cell_count"],
        "cells_across_channel_height": layout["cells_across_channel_height"],
        "circumferential_segments": layout["circumferential_segments"],
        "streamwise_intervals_per_half_pitch": layout[
            "streamwise_intervals_per_half_pitch"
        ],
        "near_membrane_intervals": layout["near_membrane_intervals"],
        "near_membrane_spacing_m": layout["near_membrane_spacing_m"],
        "n_side": layout["n_side"],
        "n_radial": layout["n_radial"],
        "n_gap": layout["n_gap"],
        "n_stream": layout["n_stream"],
        "target_max_size_m": max_size_m,
        "max_size_m": max_size_m,
        "min_size_m": min_size_m,
        "min_size_used_by_mesher": False,
        "boundary_layers": 0,
        "boundary_layers_actual": 0,
        "boundary_layers_requested": requested_layers,
        "boundary_layer_treatment": BOUNDARY_LAYER_TREATMENT,
        "first_layer_height_m": None,
        "cells_across_diameter_estimate": cells_across_diameter,
        "obstacle_resolution_ok": cells_across_diameter >= _OBSTACLE_CELLS_OK,
        "mesher": "algebraic_o_grid_quad",
        "topology_cell_count": layout["topology_cell_count"],
        "cell_count": None,
        "fidelity_study_status": "unvalidated_execution_floor",
    }
