# ==========================================================
# run_config.py
# Common configuration for meshing and solver automation
# Location: My_CFD_Project/01_Scripts/run_config.py
# ==========================================================

# Edit only this file when changing geometry/case/run parameters.
# meshing_code_*.py and solver_code_*.py should read values from this config.

import os
import types
from pathlib import Path

REQUIRED = "===== Edit here ====="

# Batch/worker keys not declared as module-level settings in this file.
RUN_CONFIG_OVERRIDE_EXTENSIONS = frozenset({
    "mesh_case_name",
    "run_label",
    "restart_from_case_file",
    "restart_from_data_file",
})


# ==========================================================
# [1] Common project/case settings
# ==========================================================

# My_CFD_Project directory, derived from this file's location
# (C:/PyFluent/My_CFD_Project on the server, the local copy in WSL).
# Override with the PYFLUENT_PROJECT_ROOT environment variable if needed.
project_root = os.environ.get(
    "PYFLUENT_PROJECT_ROOT",
    str(Path(__file__).resolve().parents[1]),
)

geo_name = REQUIRED
case_name = REQUIRED


# ==========================================================
# [2] Fluent launch settings
# ==========================================================

product_version = "25.1.0"
processor_count = 50
graphics_driver = "dx11"

fluent_start_timeout = 300
fluent_health_timeout = 300


# ==========================================================
# [3] Meshing settings
# ==========================================================

# Mesh size in mm
m_max = REQUIRED          # Maximum size [mm]
m_min = REQUIRED          # Minimum size [mm]
m_cpg = REQUIRED          # Cells per gap [-]

# Face labels from Discovery/Fluent Meshing named selections
wall_spacer_labels = [REQUIRED]

# Active RO membrane wall named selections.
# These receive RO flux source terms through the UDF.
active_membrane_wall_labels = ["wall_top_mem", "wall_bottom_mem"]

# Buffer walls are ordinary no-slip walls without permeation.
buffer_wall_labels = ["wall_top_buffer", "wall_bottom_buffer"]

periodic_labels = ["periodic_l", "periodic_r"]
periodic_reference_label = "periodic_r"

# Periodic translation in mm
periodic_shift_x = 0.0
periodic_shift_y = 3.465
periodic_shift_z = 0.0

# Local sizing controls
boi_curvature_normal_angle = 18
boi_growth_rate = 1.2

# Boundary layer controls
bl_height_factor = 0.4
bl_layers = REQUIRED

# Fluent Meshing boundary-layer offset method.
# Common options:
#   "smooth-transition"
#   "uniform"
bl_offset_method = "smooth-transition"
bl_growth_rate = 1.2

# Volume mesh controls
vol_hex_max_factor = 0.7
peel_layers = 2

# Mesh quality gate
min_orthogonal_quality_threshold = 0.05
max_aspect_ratio_threshold = 100.0
fail_if_quality_not_parsed = False

# Checkpoint option
save_surface_mesh_checkpoint = True


# ==========================================================
# [4] Solver case/setup settings
# ==========================================================

# Inlet velocity [m/s].
# Example for fixed-Q normalization:
# inlet_velocity_value = 0.2 * empty_inlet_area_mm2 / current_inlet_area_mm2
# where empty_inlet_area_mm2 = 2.66805 for W = 3.465 mm and H = 0.77 mm.
inlet_velocity_value = REQUIRED

# Boundary values
operating_pressure = 101325.0
outlet_gauge_pressure = REQUIRED

# Template/UDF file names relative to project_root.
template_case_file_name = "template_RO_setup.cas.h5"
udf_source_file_name = "260612_RO_UDF.c"
udf_library_name = "libudf"

# Active membrane and buffer wall base names in the solver.
# Fluent may split these into base, base.1, base.2, ...
membrane_wall_base_names = ["wall_top_mem", "wall_bottom_mem"]
buffer_wall_base_names = ["wall_top_buffer", "wall_bottom_buffer"]

# Species/material settings.
# In the template, the material name can be "salt", but the actual volumetric
# species name used by boundary conditions and SALT_YI_INDEX is "nacl".
target_species_name = "nacl"
salt_material_name = "salt"
salt_chemical_formula = "nacl"
salt_mass_fraction = 0.035

salt_density = 998.2
salt_viscosity = 8.93e-4
salt_molecular_weight = 58.44

mixture_name = "mixture-template"
mixture_density = 998.2
mixture_viscosity = 8.93e-4
mass_diffusivity = 2.0e-9

# UDM/UDF settings
udm_count = 13

# Solver run settings
residual_target = 1e-7
max_iterations = 2000
run_calculation_enabled = True

# Ramp/convergence safety.
# 260612_RO_UDF.c uses a source ramp that reaches full strength after 150 iterations.
use_ramp_convergence_safety = True
ramp_full_iteration = 150
post_ramp_buffer_iterations = 50

# Report definitions
update_rho_avg_report_definition = True
rho_avg_report_name = "rho_avg"
rho_avg_report_field = "density"
area_mem_report_name = "area_mem"
lmh_report_name = "lmh"
m_in_report_name = "m_in"
m_out_report_name = "m_out"


# ==========================================================
# [5] Config override application
# ==========================================================

def _is_blocked_override_target(value):
    """Return True when an existing module attribute must not be overwritten."""
    return callable(value) or isinstance(value, types.ModuleType)


def run_config_override_keys(cfg_module):
    """Return override keys allowed for run_config-style modules."""
    keys = set(RUN_CONFIG_OVERRIDE_EXTENSIONS)
    for name, value in vars(cfg_module).items():
        if name.startswith("_"):
            continue
        if _is_blocked_override_target(value):
            continue
        keys.add(name)
    return frozenset(keys)


def apply_run_config_overrides(cfg_module, overrides):
    """Apply JSON override dict to a loaded run_config module.

    Rejects unknown keys and refuses to overwrite callables or imported modules.
    """
    if not isinstance(overrides, dict):
        raise TypeError(
            "run config overrides must be a dict, "
            f"got {type(overrides).__name__}"
        )

    allowed = run_config_override_keys(cfg_module)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(
            "Unknown run config override key(s): "
            + ", ".join(repr(key) for key in unknown)
        )

    for key, value in overrides.items():
        existing = getattr(cfg_module, key, None)
        if _is_blocked_override_target(existing):
            raise TypeError(
                f"Cannot override non-config attribute: {key!r}"
            )
        setattr(cfg_module, key, value)


# ==========================================================
# [6] Config validation helpers
# ==========================================================

def _is_unset(value):
    """Return True if a config value still has the edit placeholder."""
    if value == REQUIRED:
        return True

    if isinstance(value, (list, tuple, set)):
        return any(_is_unset(item) for item in value)

    return False


def _require_set(name, value):
    """Raise ValueError when a required config value is not edited yet."""
    if _is_unset(value):
        raise ValueError(
            f"run_config.py value is not set: {name}. "
            f"Please replace {REQUIRED!r} with a real value."
        )


def _require_positive_number(name, value):
    """Raise ValueError when a config value is not a positive number."""
    _require_set(name, value)

    if isinstance(value, bool):
        raise TypeError(
            f"run_config.py value must be numeric, not bool: {name}={value!r}"
        )

    if not isinstance(value, (int, float)):
        raise TypeError(
            f"run_config.py value must be numeric: {name}={value!r}"
        )

    if value <= 0:
        raise ValueError(
            f"run_config.py value must be positive: {name}={value!r}"
        )


def _require_nonnegative_number(name, value):
    """Raise ValueError when a config value is not a non-negative number."""
    _require_set(name, value)

    if isinstance(value, bool):
        raise TypeError(
            f"run_config.py value must be numeric, not bool: {name}={value!r}"
        )

    if not isinstance(value, (int, float)):
        raise TypeError(
            f"run_config.py value must be numeric: {name}={value!r}"
        )

    if value < 0:
        raise ValueError(
            f"run_config.py value must be non-negative: {name}={value!r}"
        )


def validate_common():
    """Validate settings shared by meshing and solver scripts."""
    _require_set("project_root", project_root)
    _require_set("geo_name", geo_name)
    _require_set("case_name", case_name)
    _require_positive_number("processor_count", processor_count)


def validate_for_meshing():
    """Validate settings required by meshing automation."""
    validate_common()

    _require_positive_number("m_max", m_max)
    _require_positive_number("m_min", m_min)
    _require_positive_number("m_cpg", m_cpg)
    _require_positive_number("bl_layers", bl_layers)

    _require_set("wall_spacer_labels", wall_spacer_labels)
    _require_set("active_membrane_wall_labels", active_membrane_wall_labels)
    _require_set("buffer_wall_labels", buffer_wall_labels)
    _require_set("periodic_labels", periodic_labels)
    _require_set("periodic_reference_label", periodic_reference_label)

    _require_nonnegative_number("periodic_shift_x", periodic_shift_x)
    _require_nonnegative_number("periodic_shift_y", periodic_shift_y)
    _require_nonnegative_number("periodic_shift_z", periodic_shift_z)

    if periodic_reference_label not in periodic_labels:
        raise ValueError(
            "periodic_reference_label must be included in periodic_labels. "
            f"periodic_reference_label={periodic_reference_label}, "
            f"periodic_labels={periodic_labels}"
        )


def validate_for_solver():
    """Validate settings required by solver automation."""
    validate_common()

    _require_set("inlet_velocity_value", inlet_velocity_value)
    try:
        float(inlet_velocity_value)
    except Exception as exc:
        raise TypeError(
            "run_config.py value must be convertible to float: "
            f"inlet_velocity_value={inlet_velocity_value!r}"
        ) from exc

    _require_set("template_case_file_name", template_case_file_name)
    _require_set("udf_source_file_name", udf_source_file_name)
    _require_set("udf_library_name", udf_library_name)

    _require_set("membrane_wall_base_names", membrane_wall_base_names)
    _require_set("buffer_wall_base_names", buffer_wall_base_names)

    _require_set("target_species_name", target_species_name)
    _require_positive_number("salt_density", salt_density)
    _require_positive_number("salt_viscosity", salt_viscosity)
    _require_positive_number("salt_molecular_weight", salt_molecular_weight)
    _require_positive_number("mixture_density", mixture_density)
    _require_positive_number("mixture_viscosity", mixture_viscosity)
    _require_positive_number("mass_diffusivity", mass_diffusivity)

    _require_positive_number("udm_count", udm_count)
    _require_positive_number("residual_target", residual_target)
    _require_positive_number("max_iterations", max_iterations)
