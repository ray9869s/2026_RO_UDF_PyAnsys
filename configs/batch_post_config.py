# ============================================================
# Batch post-processing control file
# Edit this file to configure the batch run.
# This file is read by batch_report_extract.py.
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
# Worker scripts live under scripts/ (post_processing/ flattened in step 7c).
# Aggregate CSV paths are resolved by batch_report_extract.py under
# RO_DATA_ROOT/inventory.

from pathlib import Path

from ro.paths import project_root as _discover_project_root

_here = Path(__file__).resolve().parent
_scripts_dir = _discover_project_root() / "scripts"

post_processing_dir = str(_scripts_dir)
single_case_worker = str(_scripts_dir / "pyfluent_report_extract.py")
base_post_config = str(_here / "post_config.py")

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

# --- Explicit post cases (mesh-qualified or custom case names) ---
# When post_cases is non-empty, batch_report_extract.py processes these
# entries directly instead of generating geometries x velocities x pressures.
#
# Each entry may include:
#   geo_name               (required)
#   case_name              (optional; used as-is when given)
#   base_case_name         (optional; case_name = base_case_name + "__" + mesh_case_name)
#   mesh_case_name         (optional; qualifies the derived case_name)
#   inlet_velocity_value   (optional; with outlet_gauge_pressure derives base_case_name)
#   outlet_gauge_pressure  (optional)
#   final_case_file        (optional explicit path; otherwise derived from case_name)
#   final_data_file        (optional explicit path; otherwise derived from case_name)

post_cases = [
    # {
    #     "geo_name": "Sin_ST",
    #     "mesh_case_name": "mesh_max085_min005_cpg5_bl4",
    #     "inlet_velocity_value": 0.1,
    #     "outlet_gauge_pressure": 4.0e6,
    #     # case_name is optional; if omitted:
    #     # u0p1_p4M__mesh_max085_min005_cpg5_bl4
    # },
]

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
