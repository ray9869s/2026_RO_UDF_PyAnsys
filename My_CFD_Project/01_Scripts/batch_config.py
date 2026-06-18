# ==========================================================
# batch_config.py
# Batch run configuration for meshing sweeps and solver parameter sweeps
# Location: My_CFD_Project/01_Scripts/batch_config.py
# ==========================================================

# Edit this file to define your batch cases.
# batch_meshing.py reads mesh_batch_cases and common_mesh_settings.
# batch_solver_sweep.py reads solver_sweep_cases and common_solver_settings.


# ----------------------------------------------------------
# Batch control flags
# ----------------------------------------------------------

# dry_run = True: print planned commands and generated configs, do not launch Fluent.
dry_run = False

# continue_on_failure = False: stop immediately when a case fails.
# continue_on_failure = True: continue after a failed case, summarize failures at the end.
continue_on_failure = False

# skip_existing_mesh = True: if the target .msh.h5 file already exists, skip that case.
skip_existing_mesh = True

# skip_existing_final_data = True: if both final .cas.h5 and .dat.h5 already exist, skip that case.
skip_existing_final_data = True


# ----------------------------------------------------------
# Shared mesh settings applied to every meshing case
# ----------------------------------------------------------
# Any key here can be overridden per case in mesh_batch_cases.

common_mesh_settings = {
    "m_max": 0.085,    # Maximum mesh size [mm]
    "m_min": 0.005,    # Minimum mesh size [mm]
    "m_cpg": 5,       # Cells per gap [-]
    "bl_layers": 3,   # Boundary layer count [-]
}


# ----------------------------------------------------------
# Meshing batch cases
# ----------------------------------------------------------
# Each entry generates one meshing run using meshing_code_260616.py.
#
# Required keys:
#   geo_name         - geometry file name (without .dsco), also names the 03_Results subfolder
#   mesh_case_name   - subfolder and file prefix for mesh output under 03_Results/<geo_name>/
#   wall_spacer_labels - list of face labels for spacer local sizing / boundary layers
#
# Optional: override any key from common_mesh_settings here.
#
# Output: 03_Results/<geo_name>/<mesh_case_name>/<geo_name>_<mesh_case_name>.msh.h5

mesh_batch_cases = [
    # {
    #     "geo_name": "Empty",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
    #     "wall_spacer_labels": [],
    # },
    # {
    #     "geo_name": "Diamond_Spacer",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
    #     "wall_spacer_labels": ["wall_spacer"],
    # },
    # {
    #     "geo_name": "Pillar",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
    #     "wall_spacer_labels": ["wall_spacer_pillar","wall_spacer_filament"],
    # },
    # {
    #     "geo_name": "Hole_Pillar",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
    #     "wall_spacer_labels": ["wall_spacer_pillar","wall_spacer_filament","wall_spacer_hole"],
    # },
    # {
    #     "geo_name": "Multi_Layer_equal",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
    #     "wall_spacer_labels": ["wall_spacer"],
    # },
    # {
    #     "geo_name": "Multi_Layer_diff",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
    #     "wall_spacer_labels": ["wall_spacer"],
    # },
    {
        "geo_name": "Sin_ST",
        "mesh_case_name": "mesh_max085_min005_cpg???_bl???",
        "wall_spacer_labels": ["wall_spacer","wall_spacer_axial","wall_spacer_bridge"],
    },
    {
        "geo_name": "Sin_SL",
        "mesh_case_name": "mesh_max085_min005_cpg???_bl???",
        "wall_spacer_labels": ["wall_spacer","wall_spacer_axial","wall_spacer_bridge"],
    },
]


# ----------------------------------------------------------
# Shared solver settings applied to every solver case
# ----------------------------------------------------------
# Any key here can be overridden per case in solver_sweep_cases.

common_solver_settings = {
    "run_calculation_enabled": True,
    "max_iterations": 2000,
    "residual_target": 1e-7,
}


# ----------------------------------------------------------
# Solver sweep cases
# ----------------------------------------------------------
# Each entry generates one solver run using solver_code_260616.py.
#
# Required keys:
#   geo_name              - geometry folder under 03_Results
#   mesh_case_name        - subfolder containing the mesh file to read
#   case_name             - solver output folder and file prefix
#   inlet_velocity_value  - inlet velocity [m/s]
#   operating_pressure    - absolute operating pressure [Pa]
#   outlet_gauge_pressure - gauge pressure at outlet [Pa]
#
# Optional: override any key from common_solver_settings here.
#
# Required mesh input: 03_Results/<geo_name>/<mesh_case_name>/<geo_name>_<mesh_case_name>.msh.h5
# Solver outputs:      03_Results/<geo_name>/<case_name>/<geo_name>_<case_name>_setup.cas.h5
#                      03_Results/<geo_name>/<case_name>/<geo_name>_<case_name>_final.cas.h5
#                      03_Results/<geo_name>/<case_name>/<geo_name>_<case_name>_final.dat.h5

solver_sweep_cases = [
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Empty",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },

    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Diamond_Spacer",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },

    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },

    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Hole_Pillar",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },

    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_equal",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },

    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p4M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p6M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p1_p8M",
        "inlet_velocity_value": 0.1,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p4M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p2_p8M",
        "inlet_velocity_value": 0.2,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p4M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 4.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p6M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 6.0e6,
    },
    {
        "geo_name": "Multi_Layer_diff",
        "mesh_case_name": "mesh_max085_min005_cpg5_bl3",
        "case_name": "u0p3_p8M",
        "inlet_velocity_value": 0.3,
        "operating_pressure": 101325.0,
        "outlet_gauge_pressure": 8.0e6,
    },
]
