# TEMPORARY: D2450_a45 m_max grid study (max120 / hashed max085 / max060).
# Do not push this file to main as the campaign config. Restore the
# production lists at the bottom after the study (one revert of the
# study-config commit also restores them).
#
# Surface size only: m_max varies; m_min, m_cpg, bl_layers, peel_layers,
# and the 0.7 vol_hex_max factor stay fixed. Do not remesh hashed max085.
# periodic_after_surface_mesh is pinned False so the new meshes match the
# hashed reference, not the campaign default True.
#
# dry_run is True: read both driver plans before flipping it and launching
# Fluent. skip_existing_final_data keeps the hashed u0p2_p6M solve.
#
# buffer_wall_base_names MUST be overridden: this geometry splits the buffer
# walls into _in/_out, and zone_matches_base_name only accepts "base" or
# "base.N", so the run_config default of two names raises ValueError before
# the UDF loads.
#
# domain_length_m / buffer_length_m are deliberately NOT set here.

from ro.solver_common import make_base_case_name

dry_run = True
continue_on_failure = True
skip_existing_mesh = True
skip_existing_final_data = True

_FAMILY = "diamond"
_GEO_ID = "D2450_a45"

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

# Coarse then fine. Hashed max085 is not listed.
mesh_batch_cases = []
for _m_max, _mesh_id in (
    (0.120, "max120_min006_cpg5_bl4_peel2"),
    (0.060, "max060_min006_cpg5_bl4_peel2"),
):
    mesh_batch_cases.append({
        "family": _FAMILY,
        "geo_id": _GEO_ID,
        "mesh_id": _mesh_id,
        "spacing_code": "D2450",
        "attack_angle_deg": 45,
        "n_active_cells": 7,
        "cell_length_x_m": 0.003465,
        "periodic_shift_y": 3.465,
        "m_max": _m_max,
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

# max085 first so skip_existing_final_data is exercised on a live filename.
_STUDY_MESH_IDS = (
    "max085_min006_cpg5_bl4_peel2",
    "max120_min006_cpg5_bl4_peel2",
    "max060_min006_cpg5_bl4_peel2",
)
_STUDY_U = 0.2
_STUDY_P = 6.0e6
_STUDY_RUN_ID = make_base_case_name(_STUDY_U, _STUDY_P)

solver_sweep_cases = []
for _mesh_id in _STUDY_MESH_IDS:
    solver_sweep_cases.append({
        "family": _FAMILY,
        "geo_id": _GEO_ID,
        "mesh_id": _mesh_id,
        "run_id": _STUDY_RUN_ID,
        "geo_name": _GEO_ID,
        "case_name": _STUDY_RUN_ID,
        "inlet_velocity_value": _STUDY_U,
        "outlet_gauge_pressure": _STUDY_P,
    })

# ---------------------------------------------------------------------------
# Campaign lists — restore after the study.
#
# In common_mesh_settings: put back "m_max": 0.085 and drop
# periodic_after_surface_mesh (run_config default True for unmeshed geos).
# Then replace mesh_batch_cases / solver_sweep_cases with the loops below.
# Do not add D2450_a45 to the mesh list (already hashed).
# ---------------------------------------------------------------------------

_PRODUCTION_MESH_ID = "max085_min006_cpg5_bl4_peel2"

# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm)
_PRODUCTION_MESH_LAYOUTS = (
    ("D0817_a45", 21, 1.155, 1.155),
    ("D2450_a60", 5, 4.900249992, 2.8291606522),
    ("D2450_a30", 9, 2.8291606522, 4.900249992),
    ("D1225_a30", 18, 1.4145803261, 2.450124996),
    ("D1225_a45", 14, 1.7325, 1.7325),
    ("D1225_a60", 10, 2.450124996, 1.4145803261),
    ("D0817_a30", 27, 0.9430535507, 1.633416664),
    ("D0817_a60", 15, 1.633416664, 0.9430535507),
)

# mesh_batch_cases = []
# for _geo_id, _n_active, _pitch_mm, _periodic_dy_mm in _PRODUCTION_MESH_LAYOUTS:
#     _spacing_code, _angle_token = _geo_id.split("_")
#     mesh_batch_cases.append({
#         "family": _FAMILY,
#         "geo_id": _geo_id,
#         "mesh_id": _PRODUCTION_MESH_ID,
#         "spacing_code": _spacing_code,
#         "attack_angle_deg": int(_angle_token[1:]),
#         "n_active_cells": _n_active,
#         "cell_length_x_m": _pitch_mm * 1.0e-3,
#         "periodic_shift_y": _periodic_dy_mm,
#     })
#
# solver_sweep_cases = []
# for _u in (0.1, 0.2, 0.3):
#     for _p in (4.0e6, 6.0e6, 8.0e6):
#         _run_id = make_base_case_name(_u, _p)
#         solver_sweep_cases.append({
#             "family": _FAMILY,
#             "geo_id": _GEO_ID,
#             "mesh_id": _PRODUCTION_MESH_ID,
#             "run_id": _run_id,
#             "geo_name": _GEO_ID,
#             "case_name": _run_id,
#             "inlet_velocity_value": _u,
#             "outlet_gauge_pressure": _p,
#         })
