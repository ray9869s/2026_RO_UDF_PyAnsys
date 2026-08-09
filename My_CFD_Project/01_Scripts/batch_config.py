# Solver sweep on D2450_a45_7c_brg110: u = 0.1/0.2/0.3 m/s x outlet gauge
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
# wrong pressure-drop baseline. Handle it in 00_post_config.py later.
#
# Mesh lists left empty so this file cannot accidentally trigger a remesh.

dry_run = False
continue_on_failure = True
skip_existing_final_data = True

common_mesh_settings = {}
mesh_batch_cases = []

common_solver_settings = {
    "run_calculation_enabled": True,
    "max_iterations": 2000,
    "residual_target": 1e-7,
    "operating_pressure": 101325.0,
    "buffer_wall_base_names": [
        "wall_top_buffer_in", "wall_top_buffer_out",
        "wall_bottom_buffer_in", "wall_bottom_buffer_out",
    ],
}

_GEO = "D2450_a45_7c_brg110"
_MESH = "mesh_max085_min006_cpg5_bl4"

solver_sweep_cases = []
for _u in (0.1, 0.2, 0.3):
    for _p in (4.0e6, 6.0e6, 8.0e6):
        solver_sweep_cases.append({
            "geo_name": _GEO,
            "mesh_case_name": _MESH,
            "inlet_velocity_value": _u,       # m/s
            "outlet_gauge_pressure": _p,      # Pa gauge
            # case_name omitted -> u0p1_p4M__mesh_max085_min006_cpg5_bl4
        })
