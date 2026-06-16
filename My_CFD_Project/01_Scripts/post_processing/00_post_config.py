project_root = r"C:/PyFluent/My_CFD_Project"

REQUIRED = "===== Edit here ====="

geo_name = REQUIRED
case_name = REQUIRED

rho = 998.2
mu = 8.93e-4
c_inlet_ref = 597.8268309

active_membrane_base_names = ["wall_top_mem", "wall_bottom_mem"]
buffer_wall_base_names = ["wall_top_buffer", "wall_bottom_buffer"]

udm_indices = {
    "salt_mass_source": 0,
    "water_mass_source": 1,
    "total_mass_source": 2,
    "jw": 6,
    "cm": 7,
    "lmh": 8,
    "cp_inlet": 9,
    "cell_strain_rate": 10,
    "membrane_area_acc": 11,
    "salt_mass_flux": 12,
}

product_version = "25.1.0"
processor_count = 1
graphics_driver = "dx11"

fluent_start_timeout = 300
fluent_health_timeout = 300