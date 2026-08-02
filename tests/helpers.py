"""Shared helpers for pure-Python characterization tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "My_CFD_Project" / "01_Scripts"
POST_DIR = SCRIPTS_DIR / "post_processing"


def load_module(name: str, path: Path) -> ModuleType:
    """Load a Python module from an absolute file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_run_config() -> ModuleType:
    return load_module("run_config_under_test", SCRIPTS_DIR / "run_config.py")


def load_batch_solver_sweep() -> ModuleType:
    return load_module("batch_solver_sweep_under_test", SCRIPTS_DIR / "batch_solver_sweep.py")


def load_solver_common() -> ModuleType:
    return load_module("solver_common_under_test", SCRIPTS_DIR / "_solver_common.py")


def load_solver_code(module_name: str = "solver_code_under_test") -> ModuleType:
    """Load the fresh-solve worker without requiring PyFluent in the test host."""
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            module_name,
            SCRIPTS_DIR / "solver_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_batch_report_extract() -> ModuleType:
    return load_module("batch_report_extract_under_test", POST_DIR / "01_batch_report_extract.py")


def load_batch_postprocess() -> ModuleType:
    return load_module("batch_postprocess_under_test", POST_DIR / "06_batch_postprocess_all_cases.py")


def load_case_inventory() -> ModuleType:
    return load_module("case_inventory_under_test", POST_DIR / "00_case_inventory.py")


def load_post_config() -> ModuleType:
    return load_module("post_config_under_test", POST_DIR / "00_post_config.py")


def apply_json_overrides(cfg: ModuleType, overrides: dict) -> None:
    """Mirror meshing/solver workers: restricted run_config override application."""
    cfg.apply_run_config_overrides(cfg, overrides)


def apply_post_json_overrides(cfg: ModuleType, overrides: dict) -> None:
    """Mirror report worker: restricted post_config override application."""
    cfg.apply_post_config_overrides(cfg, overrides)


def populate_valid_common_config(cfg: ModuleType) -> None:
    """Set placeholder fields so validate_common() can run in isolation."""
    cfg.project_root = str(REPO_ROOT / "My_CFD_Project")
    cfg.geo_name = "Sin_ST"
    cfg.case_name = "u0p1_p4M__mesh_max100_min006_cpg3_bl3"


def populate_valid_meshing_config(cfg: ModuleType) -> None:
    populate_valid_common_config(cfg)
    cfg.m_max = 0.085
    cfg.m_min = 0.005
    cfg.m_cpg = 5
    cfg.bl_layers = 4
    cfg.wall_spacer_labels = ["wall_spacer"]
    cfg.active_membrane_wall_labels = ["wall_top_mem", "wall_bottom_mem"]
    cfg.buffer_wall_labels = ["wall_top_buffer", "wall_bottom_buffer"]
    cfg.periodic_labels = ["periodic_l", "periodic_r"]
    cfg.periodic_reference_label = "periodic_r"
    cfg.periodic_shift_x = 0.0
    cfg.periodic_shift_y = 3.465
    cfg.periodic_shift_z = 0.0


def populate_valid_solver_config(cfg: ModuleType) -> None:
    populate_valid_common_config(cfg)
    cfg.inlet_velocity_value = 0.1
    cfg.operating_pressure = 101325.0
    cfg.outlet_gauge_pressure = 4.0e6
    cfg.template_case_file_name = "template_RO_setup.cas.h5"
    cfg.udf_source_file_name = "260612_RO_UDF.c"
    cfg.udf_library_name = "libudf"
    cfg.membrane_wall_base_names = ["wall_top_mem", "wall_bottom_mem"]
    cfg.buffer_wall_base_names = ["wall_top_buffer", "wall_bottom_buffer"]
    cfg.target_species_name = "nacl"
    cfg.salt_density = 998.2
    cfg.salt_viscosity = 8.93e-4
    cfg.salt_molecular_weight = 58.44
    cfg.mixture_density = 998.2
    cfg.mixture_viscosity = 8.93e-4
    cfg.mass_diffusivity = 2.0e-9
    cfg.udm_count = 13
    cfg.residual_target = 1e-7
    cfg.max_iterations = 1000
