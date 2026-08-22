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
# Mesh lists left empty so this file cannot accidentally trigger a remesh.
# Artifact names are {geo_id}_{run_id} and {geo_id}_{mesh_id}; mesh identity
# lives in the path and the mesh manifest, not in the filename suffix.

from ro.solver_common import make_base_case_name

dry_run = False
continue_on_failure = True
skip_existing_final_data = True

common_mesh_settings = {
    "peel_layers": 2,
}
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
    # Campaign BC is parabolic. Omitting this key falls through to
    # run_config's False (plug). batch_solver_sweep refuses to start if unset.
    "use_inlet_velocity_profile": True,
}

_FAMILY = "diamond"
_GEO_ID = "D2450_a45"
_MESH_ID = "max085_min006_cpg5_bl4_peel2"

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
