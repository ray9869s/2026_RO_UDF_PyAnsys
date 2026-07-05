# ============================================================
# Batch post-processing control file
# Edit this file to configure the batch run.
# This file is read by 01_batch_report_extract.py.
# ============================================================

# --- Run control flags ---

DRY_RUN = False
CONTINUE_ON_FAILURE = True
SKIP_EXISTING_REPORTS = True
MAX_CASES = None

# Cross-case aggregate outputs (status CSV + merged summary CSV below) live
# outside the per-case folders, so writing them is opt-in.
WRITE_AGGREGATE_OUTPUTS = False

# --- Paths ---

project_root = "C:/PyFluent/My_CFD_Project"
post_processing_dir = "C:/PyFluent/My_CFD_Project/01_Scripts/post_processing"
single_case_worker = "C:/PyFluent/My_CFD_Project/01_Scripts/post_processing/01_pyfluent_report_extract.py"
base_post_config = "C:/PyFluent/My_CFD_Project/01_Scripts/post_processing/00_post_config.py"

merged_summary_csv = "C:/PyFluent/My_CFD_Project/03_Results/all_cases_post_summary.csv"
status_csv = "C:/PyFluent/My_CFD_Project/03_Results/all_cases_post_status.csv"

# --- Case definitions ---

geometries = [
    # "Empty",
    # "Diamond_Spacer",
    # "Pillar",
    # "Hole_Pillar",
    # "Multi_Layer_equal",
    # "Multi_Layer_diff",
    "Sin_ST",
    "Sin_SL"
]

inlet_velocities = [0.1, 0.2, 0.3]

outlet_gauge_pressures = [4.0e6, 6.0e6, 8.0e6]

case_prefix = ""

# --- Known non-converged cases ---

non_converged_cases = {
    # ("Diamond_Spacer", "u0p1_p4M"),
    # ("Diamond_Spacer", "u0p1_p6M"),
    # ("Diamond_Spacer", "u0p1_p8M"),
    # ("Multi_Layer_equal", "u0p3_p4M"),
    # ("Multi_Layer_equal", "u0p3_p6M"),
    # ("Multi_Layer_equal", "u0p3_p8M"),
    # ("Multi_Layer_diff", "u0p3_p4M"),
    # ("Multi_Layer_diff", "u0p3_p6M"),
    # ("Multi_Layer_diff", "u0p3_p8M"),
}
