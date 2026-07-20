# Auto-generated temporary batch_config.py for Sin_ST / Sin_SL 3-mesh solver sweep.
# Original config is backed up at:
# My_CFD_Project/01_Scripts/batch_config_before_sin_3mesh_20260716_231030.py

from pathlib import Path
import importlib.util

_backup_path = Path(__file__).with_name("batch_config_before_sin_3mesh_20260716_231030.py")
_spec = importlib.util.spec_from_file_location("_backup_batch_config", _backup_path)
_backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backup)

dry_run = False
continue_on_failure = True
skip_existing_mesh = True
skip_existing_final_data = True

common_mesh_settings = getattr(_backup, "common_mesh_settings", {})
mesh_batch_cases = []

common_solver_settings = getattr(_backup, "common_solver_settings", {})

_P_OPERATING = 101325.0
_GEOMETRIES = ["Sin_ST", "Sin_SL"]
_MESHES = [
    "mesh_max100_min006_cpg3_bl3",
    "mesh_max100_min006_cpg5_bl4",
    "mesh_max085_min006_cpg5_bl4",
]
_VELOCITIES = [0.1, 0.2, 0.3]
_PRESSURES = [4.0e6, 6.0e6, 8.0e6]

solver_sweep_cases = []

for _geo in _GEOMETRIES:
    for _mesh in _MESHES:
        for _u in _VELOCITIES:
            for _p_out in _PRESSURES:
                solver_sweep_cases.append({
                    "geo_name": _geo,
                    "mesh_case_name": _mesh,
                    "inlet_velocity_value": _u,
                    "operating_pressure": _P_OPERATING,
                    "outlet_gauge_pressure": _p_out,
                })
