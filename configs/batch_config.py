# TEMPORARY: single-case smoke test after schema v2 + fresh data root.
# Reproduces the archived D2450_a45 mesh (cell_count 796009) to confirm the
# schema v2 write path runs under a live Fluent session.
#
# periodic_after_surface_mesh is pinned False to match the archived reference.
# The campaign default is True (avoids shadow-copy slivers at periodic
# boundaries) — flip it before generating the production matrix.
#
# buffer_wall_base_names MUST be overridden: this geometry splits buffer walls
# into _in/_out, and zone_matches_base_name only accepts "base" or "base.N".
from ro.solver_common import make_base_case_name

dry_run = False                    # flip to False after reading the plan
continue_on_failure = True
skip_existing_mesh = True
skip_existing_final_data = True

_FAMILY = "diamond"
_GEO_ID = "D2450_a45"
_MESH_ID = "max085_min006_cpg5_bl4_peel2"

common_mesh_settings = {
    "filament_d_m": 4.0e-4,
    "bridge_radius_m": 1.10e-4,
    "overlap_m": 0.0,
    "n_buffer_in": 1,
    "n_buffer_out": 2,
    "buffer_length_in_m": 0.003465,
    "buffer_length_out_m": 0.00693,
    "n_lead_excluded": 3,
    "n_trail_excluded": 0,
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "peel_layers": 2,
    "periodic_after_surface_mesh": False,
    "wall_spacer_labels": ["wall_spacer"],
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

mesh_batch_cases = [{
    "family": _FAMILY,
    "geo_id": _GEO_ID,
    "mesh_id": _MESH_ID,
    "spacing_code": "D2450",
    "attack_angle_deg": 45,
    "n_active_cells": 7,
    "cell_length_x_m": 0.003465,
    "periodic_shift_y": 3.465,
}]

common_solver_settings = {
    "run_calculation_enabled": True,
    "max_iterations": 2000,
    "residual_target": 1e-7,
    "operating_pressure": 101325.0,
    "buffer_wall_base_names": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
    "use_inlet_velocity_profile": True,
}

_U = 0.2
_P = 6.0e6
_RUN_ID = make_base_case_name(_U, _P)

solver_sweep_cases = [{
    "family": _FAMILY,
    "geo_id": _GEO_ID,
    "mesh_id": _MESH_ID,
    "run_id": _RUN_ID,
    "geo_name": _GEO_ID,
    "case_name": _RUN_ID,
    "inlet_velocity_value": _U,
    "outlet_gauge_pressure": _P,
}]

# ---------------------------------------------------------------------------
# Reference data for the production matrix. Not used by this smoke config.
# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm)
# ---------------------------------------------------------------------------
_PRODUCTION_MESH_LAYOUTS = (
    ("D0817_a45", 21, 1.155, 1.155),
    ("D2450_a60",  5, 4.900249992, 2.8291606522),
    ("D2450_a30",  9, 2.8291606522, 4.900249992),
    ("D1225_a30", 18, 1.4145803261, 2.450124996),
    ("D1225_a45", 14, 1.7325, 1.7325),
    ("D1225_a60", 10, 2.450124996, 1.4145803261),
    ("D0817_a30", 27, 0.9430535507, 1.633416664),
    ("D0817_a60", 15, 1.633416664, 0.9430535507),
)