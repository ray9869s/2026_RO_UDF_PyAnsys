# Solver sweep on D2450_a45: u = 0.1/0.2/0.3 m/s x outlet gauge
# p = 4/6/8 MPa. 9 cases, Fluent relaunched per case.
#
# operating_pressure 101325.0 matches the 260615 campaign, so absolute outlet
# pressure is gauge + 101325 as before and results stay comparable.
#
# buffer_wall_base_names MUST be overridden: this geometry splits the buffer
# walls into _in/_out, and zone_matches_base_name only accepts "base" or
# "base.N", so the run_config default of two names raises ValueError before
# the UDF loads.
#
# domain_length_m / buffer_length_m are deliberately NOT set here. Those are
# post-processing keys and the current code derives spacer_x_in/out from a
# single symmetric buffer_length_m, which cannot express this domain
# (1 inlet buffer cell, 2 outlet buffer cells). Setting them would produce a
# wrong pressure-drop baseline. Handle it in post_config.py later.
#
# Remaining diamond meshes after D2450_a45 (already hashed). Order: D0817_a45,
# then D2450_a60, then the rest. Layout from Discovery 2026-08-24: pitch =
# mem_dx/n_active, periodic_shift_y in mm (complementary-angle pitch), buffers
# 3.465/6.93 mm, split _in/_out named selections on all nine.
# Artifact names are {geo_id}_{run_id} and {geo_id}_{mesh_id}; mesh identity
# lives in the path and the mesh manifest, not in the filename suffix.

from ro.solver_common import make_base_case_name

dry_run = False
continue_on_failure = True
skip_existing_final_data = True

_FAMILY = "diamond"
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
    "wall_spacer_labels": ["wall_spacer"],
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm)
mesh_batch_cases = []
for _geo_id, _n_active, _pitch_mm, _periodic_dy_mm in (
    ("D0817_a45", 21, 1.155, 1.155),
    ("D2450_a60", 5, 4.900249992, 2.8291606522),
    ("D2450_a30", 9, 2.8291606522, 4.900249992),
    ("D1225_a30", 18, 1.4145803261, 2.450124996),
    ("D1225_a45", 14, 1.7325, 1.7325),
    ("D1225_a60", 10, 2.450124996, 1.4145803261),
    ("D0817_a30", 27, 0.9430535507, 1.633416664),
    ("D0817_a60", 15, 1.633416664, 0.9430535507),
):
    _spacing_code, _angle_token = _geo_id.split("_")
    mesh_batch_cases.append({
        "family": _FAMILY,
        "geo_id": _geo_id,
        "mesh_id": _MESH_ID,
        "spacing_code": _spacing_code,
        "attack_angle_deg": int(_angle_token[1:]),
        "n_active_cells": _n_active,
        "cell_length_x_m": _pitch_mm * 1.0e-3,
        "periodic_shift_y": _periodic_dy_mm,
    })

common_solver_settings = {
    "run_calculation_enabled": True,
    "max_iterations": 2000,
    "residual_target": 1e-7,
    "operating_pressure": 101325.0,
    "buffer_wall_base_names": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
    # Campaign BC is parabolic. Omitting this key falls through to
    # run_config's False (plug). batch_solver_sweep refuses to start if unset.
    "use_inlet_velocity_profile": True,
}

_GEO_ID = "D2450_a45"

solver_sweep_cases = []
for _u in (0.1, 0.2, 0.3):
    for _p in (4.0e6, 6.0e6, 8.0e6):
        _run_id = make_base_case_name(_u, _p)
        solver_sweep_cases.append({
            "family": _FAMILY,
            "geo_id": _GEO_ID,
            "mesh_id": _MESH_ID,
            "run_id": _run_id,
            "geo_name": _GEO_ID,
            "case_name": _run_id,
            "inlet_velocity_value": _u,       # m/s
            "outlet_gauge_pressure": _p,      # Pa gauge
        })
