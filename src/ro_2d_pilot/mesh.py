"""Mesh-fidelity specification.

Low and high fidelity share the geometry from ``describe_geometry`` and
differ only in these discretization knobs. The division counts are an
execution floor so the first low-fidelity case can see the obstacle.
They are not a completed fidelity study, and they are not the 3D
poly-hexcore campaign (``max085_min006_cpg5_bl4_peel2``).

The algebraic O-grid mesher does not extrude prism layers. ``boundary_layers``
is the number actually generated (0). ``boundary_layers_requested`` records
the later high-fidelity intent.
"""

from __future__ import annotations

from ro_2d_pilot.config import FIDELITY_HIGH, FIDELITY_LOW, PilotConfig

# Cells across the channel height. High is four times finer than low.
# 24 divisions puts about 12 cells across a 0.40 mm filament in the
# 0.77 mm campaign channel. 8 divisions did not.
_HEIGHT_DIVISIONS = {
    FIDELITY_LOW: 24,
    FIDELITY_HIGH: 96,
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


def mesh_spec(config: PilotConfig) -> dict[str, object]:
    divisions = _HEIGHT_DIVISIONS[config.fidelity]
    max_size_m = config.channel_height_m / divisions
    min_size_m = max_size_m / _MIN_SIZE_FACTOR[config.fidelity]
    cells_across_diameter = config.d_m / max_size_m
    requested_layers = _BOUNDARY_LAYERS_REQUESTED[config.fidelity]
    return {
        "fidelity": config.fidelity,
        "height_divisions": divisions,
        "target_max_size_m": max_size_m,
        "max_size_m": max_size_m,
        "min_size_m": min_size_m,
        "boundary_layers": 0,
        "boundary_layers_requested": requested_layers,
        "first_layer_height_m": None,
        "cells_across_diameter_estimate": cells_across_diameter,
        "obstacle_resolution_ok": cells_across_diameter >= _OBSTACLE_CELLS_OK,
        "mesher": "algebraic_o_grid_quad",
        "cell_count": None,
        "fidelity_study_status": "unvalidated_execution_floor",
    }
