# Remaining 8 Diamond meshes. D2450_a45 is already built and verified
# (cell_count 796009, u_mean 0.1992807, G 1.00360939613 — all match archive).
#
# periodic_after_surface_mesh = True is the campaign default: setting periodic
# boundaries before surface meshing produces shadow-copy node slivers
# (observed here as max skewness 0.88 vs 0.67 in the archived reference).
#
# buffer_wall_base_names MUST be overridden: this geometry splits buffer walls
# into _in/_out, and zone_matches_base_name only accepts "base" or "base.N".
from ro.solver_common import make_base_case_name

dry_run = False
continue_on_failure = True
skip_existing_mesh = True
skip_existing_final_data = True

# Batch meshing session lifecycle (AttachAssembly / socket-reset contention).
# inter_case_delay_s: settle time after one worker exits before the next starts.
# post_failure_settle_s: floor applied after any non-SUCCESS case before the
#   next case (even when inter_case_delay_s is 0). Leftover Fluent/Discovery/
#   CADReaders processes are scanned after this settle.
# transient_failure_max_retries: retries after CAD AttachAssembly or socket-
#   reset (2 => up to 3 total worker invocations for that case).
# clean_fm_scratch_on_success: remove FM_<HOST>_<PID>/ dirs after SUCCESS*.
inter_case_delay_s = 0.0
post_failure_settle_s = 15.0
transient_failure_max_retries = 2
clean_fm_scratch_on_success = True

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
    "periodic_after_surface_mesh": True,
    "wall_spacer_labels": ["wall_spacer"],
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

# D0817_a45 first: it failed with a twin-face error in the 8/25 batch, which
# was built with periodic_after_surface_mesh False. That setting is now known
# to produce shadow-copy slivers, so this batch tests whether True alone
# resolves the failure.
# (geo_id, n_active_cells, pitch_mm, periodic_dy_mm)
# a60 with brg156 bridge spheres. brg110 hit the surface skewness gate on
# D0817_a60 (0.86115 > 0.85), and the config calibration note already uses
# D0817_a60_15c_brg156 as the worst passing mesh for the sff threshold.
_MESH_LAYOUTS = (
    ("D2450_a60",  5, 4.900249992,  2.8291606522),
    ("D1225_a60", 10, 2.450124996,  1.4145803261),
    ("D0817_a60", 15, 1.633416664,  0.9430535507),
)

mesh_batch_cases = []
for _geo_id, _n_active, _pitch_mm, _periodic_dy_mm in _MESH_LAYOUTS:
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
        "bridge_radius_m": 1.56e-4,
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
