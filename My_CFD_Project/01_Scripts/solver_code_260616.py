# ==========================================================
# ##### [0] Import Required Packages #####
# ==========================================================
import ansys.fluent.core as pyfluent
import os
import shutil
import re
import importlib.util
from pathlib import Path

# ==========================================================
# ##### [1] Load Run Configuration #####
# ==========================================================

SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
CONFIG_PATH = SCRIPT_DIR / "run_config.py"

if not CONFIG_PATH.is_file():
    raise FileNotFoundError(
        f"Mesh/solver config file not found: {CONFIG_PATH}. "
        "Place run_config.py in the same folder as this solver script."
    )

config_spec = importlib.util.spec_from_file_location("run_config", CONFIG_PATH)
cfg = importlib.util.module_from_spec(config_spec)
config_spec.loader.exec_module(cfg)

cfg.validate_for_solver()

# Project paths
project_root = cfg.project_root
geo_name = cfg.geo_name
case_name = cfg.case_name

case_path = os.path.join(project_root, "03_Results", geo_name, case_name)
mesh_file_path = os.path.join(case_path, f"{geo_name}_{case_name}.msh.h5")

# Inlet velocity setting
inlet_velocity_value = cfg.inlet_velocity_value
inlet_velocity = float(inlet_velocity_value)

# Template case path.
# This template case already contains the RO material/species setup.
template_case_path = os.path.join(
    project_root,
    "01_Templates",
    cfg.template_case_file_name,
)

# Fluent launch settings
product_version = cfg.product_version
processor_count = cfg.processor_count
graphics_driver = cfg.graphics_driver
fluent_start_timeout = cfg.fluent_start_timeout
fluent_health_timeout = cfg.fluent_health_timeout

# UDF source
udf_master_path = os.path.join(project_root, "02_UDFs", cfg.udf_source_file_name)
udf_case_path = os.path.join(case_path, os.path.basename(udf_master_path))
udf_library_name = cfg.udf_library_name

# Active membrane and buffer wall base names.
membrane_wall_base_names = list(cfg.membrane_wall_base_names)
buffer_wall_base_names = list(cfg.buffer_wall_base_names)

# Separate log used for mesh replacement diagnostics.
solver_mesh_replace_log_path = os.path.join(
    case_path,
    f"solver_mesh_replace_log_{case_name}.txt",
)

# Species/material settings
target_species_name = cfg.target_species_name
salt_material_name = cfg.salt_material_name
salt_chemical_formula = cfg.salt_chemical_formula
salt_mass_fraction = cfg.salt_mass_fraction

salt_density = cfg.salt_density
salt_viscosity = cfg.salt_viscosity
salt_molecular_weight = cfg.salt_molecular_weight

mixture_name = cfg.mixture_name
mixture_density = cfg.mixture_density
mixture_viscosity = cfg.mixture_viscosity
mass_diffusivity = cfg.mass_diffusivity

# Boundary values
operating_pressure = cfg.operating_pressure
outlet_gauge_pressure = cfg.outlet_gauge_pressure

# UDM and UDF hooks
udm_count = cfg.udm_count

adjust_function_name = f"RO_membrane_adjust::{udf_library_name}"
init_function_name = f"RO_UDF_init::{udf_library_name}"

source_function_names = {
    "mass": f"mass_source::{udf_library_name}",
    "species_salt": f"species_salt_source::{udf_library_name}",
    "x_momentum": f"x_mom_source::{udf_library_name}",
    "y_momentum": f"y_mom_source::{udf_library_name}",
    "z_momentum": f"z_mom_source::{udf_library_name}",
}

# Solver run settings
residual_target = cfg.residual_target
max_iterations = cfg.max_iterations
run_calculation_enabled = cfg.run_calculation_enabled

# Ramp/convergence safety.
use_ramp_convergence_safety = cfg.use_ramp_convergence_safety
ramp_full_iteration = cfg.ramp_full_iteration
post_ramp_buffer_iterations = cfg.post_ramp_buffer_iterations
minimum_full_source_iterations = ramp_full_iteration + post_ramp_buffer_iterations

# Output files
solver_log_path = os.path.join(case_path, f"solver_log_{case_name}.txt")
setup_case_file = os.path.join(case_path, f"{geo_name}_{case_name}_setup.cas.h5")
final_case_file = os.path.join(case_path, f"{geo_name}_{case_name}_final.cas.h5")
final_data_file = final_case_file.replace(".cas.h5", ".dat.h5")

# Report definitions
update_rho_avg_report_definition = cfg.update_rho_avg_report_definition
rho_avg_report_name = cfg.rho_avg_report_name
rho_avg_report_field = cfg.rho_avg_report_field
area_mem_report_name = cfg.area_mem_report_name
lmh_report_name = cfg.lmh_report_name
m_in_report_name = cfg.m_in_report_name
m_out_report_name = cfg.m_out_report_name

# ==========================================================
# ##### [2] Helper Functions #####
# ==========================================================

def as_fluent_path(path):
    """Convert a path to a Fluent-friendly absolute path."""
    return os.path.abspath(path).replace("\\", "/")


def normalize_path(path):
    """Normalize a path for comparison."""
    return os.path.normcase(os.path.abspath(path))


def require_items(required_items, available_items, item_type):
    """Raise an error if required items are missing."""
    missing_items = [
        item for item in required_items
        if item not in available_items
    ]

    if missing_items:
        raise ValueError(
            f"Missing required {item_type}: {missing_items}. "
            f"Available {item_type}: {available_items}"
        )


def replace_define_int(text, macro_name, new_value):
    """Replace an integer #define value in a C source string."""
    pattern = rf"(^\s*#define\s+{re.escape(macro_name)}\s+)([-+]?\d+)(.*$)"
    replacement = rf"\g<1>{int(new_value)}\g<3>"

    new_text, count = re.subn(
        pattern,
        replacement,
        text,
        count=1,
        flags=re.MULTILINE,
    )

    if count != 1:
        raise ValueError(
            f'Could not find exactly one integer macro definition for "{macro_name}".'
        )

    return new_text


def replace_define_string(text, macro_name, new_value):
    """Replace a string #define value in a C source string."""
    escaped_value = str(new_value).replace('\\', '\\\\').replace('"', '\"')
    pattern = rf'(^\s*#define\s+{re.escape(macro_name)}\s+)("[^"]*"|\S+)(.*$)'
    replacement = rf'\g<1>"{escaped_value}"\g<3>'

    new_text, count = re.subn(
        pattern,
        replacement,
        text,
        count=1,
        flags=re.MULTILINE,
    )

    if count != 1:
        raise ValueError(
            f'Could not find exactly one string macro definition for "{macro_name}".'
        )

    return new_text


def zone_matches_base_name(zone_name, base_name):
    """Return True for base, base.1, base.2, ... zone naming."""
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def find_zones_by_base_name(zone_names, base_name):
    """Find zones whose names are base or base.N, sorted with base first."""
    matched = [name for name in zone_names if zone_matches_base_name(name, base_name)]

    def sort_key(name):
        if name == base_name:
            return (0, 0)
        suffix = name[len(base_name) + 1:]
        if suffix.isdigit():
            return (1, int(suffix))
        return (2, suffix)

    return sorted(matched, key=sort_key)


def find_zones_by_base_names(zone_names, base_names):
    """Find zones matching any of the given base names (base or base.N), sorted."""
    matched = []
    for base_name in base_names:
        matched.extend(find_zones_by_base_name(zone_names, base_name))
    return sorted(set(matched))


def update_solver_thread_names(solver_session):
    """Ask Fluent to update solver-side thread names for THREAD_NAME(t) in UDFs."""
    scheme_command = "(update-solver-thread-names)"

    try:
        solver_session.execute_tui(scheme_command)
        print("Executed Scheme command through execute_tui: (update-solver-thread-names)")
        return
    except Exception as first_error:
        print(f"Warning: execute_tui failed for update-solver-thread-names: {first_error}")

    try:
        solver_session.scheme_eval.scheme_eval(scheme_command)
        print("Executed Scheme command through scheme_eval.scheme_eval: (update-solver-thread-names)")
        return
    except Exception as second_error:
        print(f"Warning: scheme_eval.scheme_eval failed: {second_error}")

    try:
        solver_session.scheme_eval.string_eval(scheme_command)
        print("Executed Scheme command through scheme_eval.string_eval: (update-solver-thread-names)")
        return
    except Exception as third_error:
        print(f"Warning: scheme_eval.string_eval failed: {third_error}")

    print(
        "Warning: Could not execute (update-solver-thread-names). "
        "If THREAD_NAME(t) is unavailable, the UDF will not find membrane wall zones."
    )


def copy_and_patch_udf_to_case_folder(
    source_path,
    destination_path,
    salt_yi_index_value,
):
    """Copy UDF to case folder and patch SALT_YI_INDEX.

    MEMB_THREAD_BASE_NAME_TOP and MEMB_THREAD_BASE_NAME_BOTTOM are hardcoded in
    the UDF as "wall_top_mem" and "wall_bottom_mem" and do not require runtime patching.
    """
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"UDF source file not found: {source_path}")

    if normalize_path(source_path) != normalize_path(destination_path):
        shutil.copy2(source_path, destination_path)
        print(f"UDF copied to case folder: {destination_path}")
    else:
        print(f"UDF source is already in the case folder: {destination_path}")

    if not os.path.isfile(destination_path):
        raise FileNotFoundError(f"UDF copy failed: {destination_path}")

    with open(destination_path, "r", encoding="utf-8", errors="ignore") as file:
        udf_text = file.read()

    udf_text = replace_define_int(
        udf_text,
        "SALT_YI_INDEX",
        salt_yi_index_value,
    )

    with open(destination_path, "w", encoding="utf-8", newline="\n") as file:
        file.write(udf_text)

    print(f"Patched SALT_YI_INDEX = {salt_yi_index_value}")
    print(f"Case-specific UDF ready: {destination_path}")

    return destination_path


def list_named_object_names(named_object, object_label=""):
    """
    Return names from a PyFluent named object.

    Priority:
    1. get_object_names()
    2. get_state() keys
    3. list_1() return value

    Some Fluent commands print names to the Fluent console but return None
    to Python, so list_1() must not be blindly wrapped with list().
    """
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass

    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(list(state.keys()))
        if isinstance(state, list):
            return list(state)
    except Exception:
        pass

    try:
        names = named_object.list_1()
        if names is None:
            print(f"Warning: {object_label}.list_1() returned None.")
            return []
        return list(names)
    except Exception as e:
        print(f"Could not list names for {object_label}. Error: {e}")
        return []


def collect_boundary_zones(setup):
    """Collect boundary zone names grouped by boundary type."""
    boundary_type_names = [
        "velocity_inlet",
        "pressure_outlet",
        "pressure_inlet",
        "mass_flow_inlet",
        "mass_flow_outlet",
        "wall",
        "periodic",
        "shadow",
        "symmetry",
        "interface",
        "interior",
        "outflow",
    ]

    zones_by_type = {}

    for boundary_type in boundary_type_names:
        try:
            boundary_object = getattr(setup.boundary_conditions, boundary_type)
        except Exception:
            continue

        names = list_named_object_names(
            named_object=boundary_object,
            object_label=f"boundary_conditions.{boundary_type}",
        )

        if names:
            zones_by_type[boundary_type] = names

    return zones_by_type


def collect_cell_zones(setup):
    """Collect cell zone names grouped by cell zone type."""
    cell_zone_type_names = [
        "fluid",
        "solid",
    ]

    zones_by_type = {}

    for cell_zone_type in cell_zone_type_names:
        try:
            cell_zone_object = getattr(setup.cell_zone_conditions, cell_zone_type)
        except Exception:
            continue

        names = list_named_object_names(
            named_object=cell_zone_object,
            object_label=f"cell_zone_conditions.{cell_zone_type}",
        )

        if names:
            zones_by_type[cell_zone_type] = names

    return zones_by_type


def print_solver_object_info(object_name, solver_object):
    """Print basic information about a PyFluent settings object."""
    print(f"{object_name}: {solver_object}")

    try:
        print(f"{object_name}.child_names:", solver_object.child_names)
    except Exception as e:
        print(f"Could not read {object_name}.child_names. Error: {e}")

    try:
        print(f"{object_name}.command_names:", solver_object.command_names)
    except Exception as e:
        print(f"Could not read {object_name}.command_names. Error: {e}")

    try:
        print(f"{object_name}.get_state():", solver_object.get_state())
    except Exception as e:
        print(f"Could not read {object_name}.get_state(). Error: {e}")


def verify_file_exists(path, description):
    """Verify that a file exists."""
    if os.path.isfile(path):
        print(f"Verified {description}: {path}")
    else:
        print(f"Warning: {description} was not found: {path}")


def parse_zone_id_from_log(log_path, zone_name):
    """
    Parse Fluent zone ID from transcript text.

    Expected Fluent transcript line:
        Setting zone id of wall to 46.

    The last match is used because replace_mesh may print zone mappings
    after the template case is loaded.
    """
    if not os.path.isfile(log_path):
        raise FileNotFoundError(f"Solver mesh-replace log not found: {log_path}")

    with open(log_path, "r", encoding="utf-8", errors="ignore") as file:
        text = file.read()

    pattern = rf"Setting\s+zone\s+id\s+of\s+{re.escape(zone_name)}\s+to\s+(\d+)\."

    matches = re.findall(pattern, text)

    if not matches:
        raise RuntimeError(
            f"Could not parse zone ID for zone '{zone_name}' from log: {log_path}. "
            "Check that replace_mesh printed zone mapping lines and that "
            "membrane_wall_name matches the Fluent boundary zone name."
        )

    zone_id = int(matches[-1])

    print(f"Parsed zone ID from log: {zone_name} -> {zone_id}")

    return zone_id


def sanitize_report_suffix(name):
    """Make a Fluent report-definition-friendly suffix from a zone name."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_")


def create_or_update_single_zone_volume_average_report(
    volume_report_definitions,
    report_name,
    field_name,
    cell_zone_name,
):
    """Create/update one volume-average report definition for one cell zone."""
    existing_volume_reports = list_named_object_names(
        named_object=volume_report_definitions,
        object_label="solution.report_definitions.volume",
    )

    if report_name in existing_volume_reports:
        report_definition = volume_report_definitions[report_name]
        print(f"Report definition already exists. Updating: {report_name}")
    else:
        report_definition = volume_report_definitions.create(report_name)
        print(f"Report definition created: {report_name}")

    print(f"  Field: {field_name}")
    print(f"  Cell zone: {cell_zone_name}")

    try:
        print("  State before update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"  Could not read pre-update report state: {e}")

    report_definition.report_type = "volume-average"
    report_definition.field = field_name

    # In Fluent 2025 R1, this PyFluent settings object behaves like a single-select
    # field, even when multiple cell zones exist. Passing a Python list can raise:
    #   wta(1st) to string->symbol / allowed values are [...]
    # Therefore, create one report definition per fluid zone and assign a string here.
    report_definition.cell_zones = cell_zone_name

    # Avoid failing if these optional settings do not exist in a given Fluent version.
    for attr_name, value in [
        ("create_report_file", False),
        ("create_report_plot", False),
        ("print", True),
    ]:
        try:
            setattr(report_definition, attr_name, value)
        except Exception:
            pass

    try:
        print("  State after update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"  Could not read post-update report state: {e}")

    return report_definition


def delete_report_definition_if_exists(report_group, report_name, group_label):
    """Delete a report definition if it exists."""
    report_names = list_named_object_names(
        named_object=report_group,
        object_label=group_label,
    )

    if report_name not in report_names:
        print(f"Report definition not present, skipping delete: {report_name}")
        return False

    try:
        report_group.delete(report_name)
        print(f"Deleted report definition: {report_name}")
        return True
    except Exception as e:
        print(f"Could not delete report definition {report_name} using settings API: {e}")
        return False


def recreate_volume_average_report_definition(solution, report_name, field_name, cell_zones):
    """
    Rebuild density volume-average report definitions after replace_mesh.

    The clean template may contain a stale rho_avg report pointing to the old single
    cell zone, for example solid. After replace_mesh, split-fluid geometries can
    create solid.1, solid.2, solid.3, etc. Fluent 2025 R1 PyFluent may reject a
    Python list for volume-report cell_zones, so this function deletes the stale
    base report and creates one density report per fluid zone instead.
    """
    if not cell_zones:
        raise ValueError("Cannot create/update report definition without cell zones.")

    cell_zones = list(cell_zones)

    print(f"\nUpdating per-zone volume-average report definitions for: {field_name}")
    print(f"  Stale/base report name to remove: {report_name}")
    print(f"  Cell zones: {cell_zones}")

    volume_report_definitions = solution.report_definitions.volume

    # Remove the stale template report. It can otherwise remain as cell_zones=[False]
    # and cause Fluent GUI validation issues or invalid LMH expressions.
    delete_report_definition_if_exists(
        report_group=volume_report_definitions,
        report_name=report_name,
        group_label="solution.report_definitions.volume",
    )

    updated_report_names = []

    for zone_name in cell_zones:
        safe_suffix = sanitize_report_suffix(zone_name)
        per_zone_report_name = f"{report_name}_{safe_suffix}"

        print(f"\nUpdating per-zone report '{per_zone_report_name}'")
        create_or_update_single_zone_volume_average_report(
            volume_report_definitions=volume_report_definitions,
            report_name=per_zone_report_name,
            field_name=field_name,
            cell_zone_name=zone_name,
        )
        updated_report_names.append(per_zone_report_name)

    print("\nPer-zone volume-average report definitions updated:")
    print(updated_report_names)
    print(
        "Use these rho_avg_* reports to confirm density in all split fluid regions. "
        "The stale template rho_avg report is intentionally removed."
    )

    return updated_report_names


def create_or_update_flux_massflow_report(flux_report_definitions, report_name, boundaries):
    """Create/update a flux-massflow report definition over one or more boundaries."""
    if not boundaries:
        raise ValueError(f"Cannot create/update {report_name} without boundaries.")

    existing_reports = list_named_object_names(
        named_object=flux_report_definitions,
        object_label="solution.report_definitions.flux",
    )

    if report_name in existing_reports:
        report_definition = flux_report_definitions[report_name]
        print(f"Flux report already exists. Updating: {report_name}")
    else:
        report_definition = flux_report_definitions.create(report_name)
        print(f"Flux report created: {report_name}")

    try:
        print(f"{report_name} state before update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read pre-update state for {report_name}: {e}")

    report_definition.report_type = "flux-massflow"
    report_definition.boundaries = list(boundaries)
    report_definition.per_zone = False

    try:
        print(f"{report_name} state after update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read post-update state for {report_name}: {e}")

    return report_definition


def create_or_update_surface_area_report(surface_report_definitions, report_name, surface_names):
    """Create/update a surface-area report definition over membrane wall zones."""
    if not surface_names:
        raise ValueError(f"Cannot create/update {report_name} without surface names.")

    existing_reports = list_named_object_names(
        named_object=surface_report_definitions,
        object_label="solution.report_definitions.surface",
    )

    if report_name in existing_reports:
        report_definition = surface_report_definitions[report_name]
        print(f"Surface report already exists. Updating: {report_name}")
    else:
        report_definition = surface_report_definitions.create(report_name)
        print(f"Surface report created: {report_name}")

    try:
        print(f"{report_name} state before update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read pre-update state for {report_name}: {e}")

    report_definition.report_type = "surface-area"
    report_definition.surface_names = list(surface_names)
    report_definition.per_surface = False

    try:
        print(f"{report_name} state after update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read post-update state for {report_name}: {e}")

    return report_definition


def create_or_update_single_valued_expression_report(
    single_expression_report_definitions,
    report_name,
    definition,
):
    """Create/update a single-valued expression report definition."""
    existing_reports = list_named_object_names(
        named_object=single_expression_report_definitions,
        object_label="solution.report_definitions.single_valued_expression",
    )

    if report_name in existing_reports:
        report_definition = single_expression_report_definitions[report_name]
        print(f"Single-valued expression report already exists. Updating: {report_name}")
    else:
        report_definition = single_expression_report_definitions.create(report_name)
        print(f"Single-valued expression report created: {report_name}")

    try:
        print(f"{report_name} state before update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read pre-update state for {report_name}: {e}")

    report_definition.definition = definition

    try:
        print(f"{report_name} state after update:")
        print(report_definition.get_state())
    except Exception as e:
        print(f"Could not read post-update state for {report_name}: {e}")

    return report_definition


def update_transport_report_definitions_for_current_zones(
    solution,
    inlet_zones,
    outlet_zones,
    membrane_wall_zones,
    density_value,
    m_in_name="m_in",
    m_out_name="m_out",
    area_mem_name="area_mem",
    lmh_name="lmh",
):
    """Rebuild report definitions that depend on current boundary zone names."""
    print("\nUpdating transport report definitions for current zones...")
    print("  Inlet zones:", inlet_zones)
    print("  Outlet zones:", outlet_zones)
    print("  Membrane wall zones:", membrane_wall_zones)

    if not inlet_zones:
        raise ValueError("No inlet zones were provided for m_in report definition.")
    if not outlet_zones:
        raise ValueError("No outlet zones were provided for m_out report definition.")
    if not membrane_wall_zones:
        raise ValueError("No membrane wall zones were provided for area_mem report definition.")

    flux_report_definitions = solution.report_definitions.flux
    surface_report_definitions = solution.report_definitions.surface
    single_expression_report_definitions = solution.report_definitions.single_valued_expression

    create_or_update_flux_massflow_report(
        flux_report_definitions=flux_report_definitions,
        report_name=m_in_name,
        boundaries=inlet_zones,
    )

    create_or_update_flux_massflow_report(
        flux_report_definitions=flux_report_definitions,
        report_name=m_out_name,
        boundaries=outlet_zones,
    )

    create_or_update_surface_area_report(
        surface_report_definitions=surface_report_definitions,
        report_name=area_mem_name,
        surface_names=membrane_wall_zones,
    )

    # Use a constant density in LMH monitor to avoid stale rho_avg references from
    # the template and to keep the monitor robust for split fluid zones.
    lmh_definition = f"abs({m_in_name} + {m_out_name}) / ({density_value} * {area_mem_name}) * 3.6e6"

    create_or_update_single_valued_expression_report(
        single_expression_report_definitions=single_expression_report_definitions,
        report_name=lmh_name,
        definition=lmh_definition,
    )

    print("\nTransport report definitions after update:")
    print(f"{m_in_name}:", flux_report_definitions[m_in_name].get_state())
    print(f"{m_out_name}:", flux_report_definitions[m_out_name].get_state())
    print(f"{area_mem_name}:", surface_report_definitions[area_mem_name].get_state())
    print(f"{lmh_name}:", single_expression_report_definitions[lmh_name].get_state())


def set_residual_convergence_check(solution, species_name, enable):
    """Enable or disable residual convergence checks while keeping residual monitors on."""
    residual_equations_state = solution.monitor.residual.equations.get_state()
    available_residual_equations = list(residual_equations_state.keys())

    target_residual_equations = [
        "continuity",
        "x-velocity",
        "y-velocity",
        "z-velocity",
        species_name,
    ]

    for eq in target_residual_equations:
        if eq not in available_residual_equations:
            print(f"Residual equation not found. Skipping convergence-check update: {eq}")
            continue

        res_eq = solution.monitor.residual.equations[eq]
        res_eq.monitor = True
        res_eq.check_convergence = enable

    print(f"Residual convergence check set to: {enable}")


# ==========================================================
# ##### [3] Path Checks #####
# ==========================================================

if not os.path.exists(case_path):
    os.makedirs(case_path)

if not os.path.isfile(mesh_file_path):
    raise FileNotFoundError(f"Mesh file not found: {mesh_file_path}")

if not os.path.isfile(template_case_path):
    raise FileNotFoundError(f"Template case file not found: {template_case_path}")

if not os.path.isfile(udf_master_path):
    raise FileNotFoundError(f"UDF source file not found: {udf_master_path}")

print(f"Case path: {case_path}")
print(f"Mesh file: {mesh_file_path}")
print(f"Template case: {template_case_path}")
print(f"UDF master source: {udf_master_path}")
print(f"UDF case copy: {udf_case_path}")
print(f"Solver mesh-replace log: {solver_mesh_replace_log_path}")
print(f"Solver log: {solver_log_path}")


# ==========================================================
# ##### [4] Launch Fluent, Read Template Case, Replace Mesh #####
# ==========================================================

pyfluent.config.check_health_timeout = fluent_health_timeout

meshing = None
solver = None
setup = None
solution = None
transcript_is_running = False
original_working_directory = os.getcwd()

os.chdir(case_path)

print("Launching Fluent in meshing mode...", flush=True)

meshing = pyfluent.launch_fluent(
    product_version=product_version,
    mode="meshing",
    dimension=3,
    precision="double",
    processor_count=processor_count,
    ui_mode="gui",
    graphics_driver=graphics_driver,
    start_timeout=fluent_start_timeout,
    cwd=as_fluent_path(case_path),
)

print("Meshing session launched successfully.", flush=True)
print("Switching from meshing mode to solver mode...", flush=True)

solver = meshing.switch_to_solver()

print("Switched to solver mode successfully.", flush=True)

meshing = None

setup = solver.settings.setup
solution = solver.settings.solution

# Start a dedicated transcript for template loading and mesh replacement.
# This log is used to parse the zone-name to zone-id mapping.
solver.transcript.start(file_name=as_fluent_path(solver_mesh_replace_log_path))
transcript_is_running = True

print("Reading template case...", flush=True)

solver.settings.file.read_case(
    file_name=as_fluent_path(template_case_path)
)

print("Template case loaded successfully.", flush=True)

print("Replacing mesh with target geometry mesh...", flush=True)

solver.settings.file.replace_mesh(
    file_name=as_fluent_path(mesh_file_path)
)

print("Mesh replaced successfully.", flush=True)

solver.execute_tui(r"/mesh/check")
print("Solver-side mesh check completed.")

print("Skipping /mesh/check-quality in solver mode because this TUI command was invalid in the tested solver session.")

# Stop the mesh-replace transcript so that zone mapping lines are flushed to file.
solver.transcript.stop()
transcript_is_running = False

# Update solver-side thread names so the name-based UDF can use THREAD_NAME(t).
update_solver_thread_names(solver)

print(f"Active membrane wall base names for UDF (hardcoded in UDF): {membrane_wall_base_names}")
print(f"Buffer wall base names (no-slip, not membrane): {buffer_wall_base_names}")

# Start the main solver transcript for the remaining setup and calculation.
solver.transcript.start(file_name=as_fluent_path(solver_log_path))
transcript_is_running = True

print(f"Main solver log will be saved to: {solver_log_path}")


# ======================================================
# ##### [5] Check Zones #####
# ======================================================

boundary_zones_by_type = collect_boundary_zones(setup)
cell_zones_by_type = collect_cell_zones(setup)

print("Boundary zones by type:")
for zone_type, names in boundary_zones_by_type.items():
    print(f"  {zone_type}: {names}")

print("Cell zones by type:")
for zone_type, names in cell_zones_by_type.items():
    print(f"  {zone_type}: {names}")

boundary_zone_names = sorted(
    zone_name
    for names in boundary_zones_by_type.values()
    for zone_name in names
)

cell_zone_names = sorted(
    zone_name
    for names in cell_zones_by_type.values()
    for zone_name in names
)

print("All boundary zones:", boundary_zone_names)
print("All cell zones:", cell_zone_names)

inlet_zone_names = find_zones_by_base_name(boundary_zone_names, "inlet")
outlet_zone_names = find_zones_by_base_name(boundary_zone_names, "outlet")
membrane_wall_zone_names = find_zones_by_base_names(boundary_zone_names, membrane_wall_base_names)
buffer_wall_zone_names = find_zones_by_base_names(boundary_zone_names, buffer_wall_base_names)

if not inlet_zone_names:
    raise ValueError(f"No inlet boundary zones found. Available boundary zones: {boundary_zone_names}")
if not outlet_zone_names:
    raise ValueError(f"No outlet boundary zones found. Available boundary zones: {boundary_zone_names}")
if not membrane_wall_zone_names:
    raise ValueError(f"No active membrane wall zones found for base names {membrane_wall_base_names}. Available boundary zones: {boundary_zone_names}")
if not buffer_wall_zone_names:
    raise ValueError(f"No buffer wall zones found for base names {buffer_wall_base_names}. Available boundary zones: {boundary_zone_names}")

wall_zone_names = boundary_zones_by_type["wall"]

wall_spacer_zones = sorted(
    zone_name for zone_name in wall_zone_names
    if zone_name.startswith("wall_spacer")
)

print("Inlet zones:", inlet_zone_names)
print("Outlet zones:", outlet_zone_names)
print("Detected active membrane wall zones:", membrane_wall_zone_names)
print("Detected buffer wall zones:", buffer_wall_zone_names)
print("Detected spacer wall zones:", wall_spacer_zones)
print("All wall zones:", wall_zone_names)
print(f"Active membrane wall base names: {membrane_wall_base_names}")
print(f"Buffer wall base names: {buffer_wall_base_names}")


# ======================================================
# ##### [6] Confirm Boundary Zone Types #####
# ======================================================

boundary_zones_by_type = collect_boundary_zones(setup)

velocity_inlet_zones = boundary_zones_by_type.get("velocity_inlet", [])
pressure_outlet_zones = boundary_zones_by_type.get("pressure_outlet", [])
all_boundary_zone_names_for_type = sorted(
    zone_name
    for names in boundary_zones_by_type.values()
    for zone_name in names
)

inlet_zone_names = find_zones_by_base_name(all_boundary_zone_names_for_type, "inlet")
outlet_zone_names = find_zones_by_base_name(all_boundary_zone_names_for_type, "outlet")

print("Current velocity-inlet zones:", velocity_inlet_zones)
print("Current pressure-outlet zones:", pressure_outlet_zones)
print("Detected inlet zones:", inlet_zone_names)
print("Detected outlet zones:", outlet_zone_names)

inlet_zones_to_convert = [name for name in inlet_zone_names if name not in velocity_inlet_zones]
outlet_zones_to_convert = [name for name in outlet_zone_names if name not in pressure_outlet_zones]

if inlet_zones_to_convert:
    print(f"Setting inlet zones to velocity-inlet: {inlet_zones_to_convert}")
    setup.boundary_conditions.set_zone_type(
        zone_list=inlet_zones_to_convert,
        new_type="velocity-inlet",
    )
else:
    print("All inlet zones are already velocity-inlet. Skipping type change.")

if outlet_zones_to_convert:
    print(f"Setting outlet zones to pressure-outlet: {outlet_zones_to_convert}")
    setup.boundary_conditions.set_zone_type(
        zone_list=outlet_zones_to_convert,
        new_type="pressure-outlet",
    )
else:
    print("All outlet zones are already pressure-outlet. Skipping type change.")

boundary_zones_by_type = collect_boundary_zones(setup)
cell_zones_by_type = collect_cell_zones(setup)

print("Boundary zones after type confirmation:")
for zone_type, names in boundary_zones_by_type.items():
    print(f"  {zone_type}: {names}")

print("Cell zones after type confirmation:")
for zone_type, names in cell_zones_by_type.items():
    print(f"  {zone_type}: {names}")


# ======================================================
# ##### [7] Select Fluid Cell Zones #####
# ======================================================

cell_zones_by_type = collect_cell_zones(setup)
fluid_zone_names = cell_zones_by_type.get("fluid", [])

print("Fluid cell zones:", fluid_zone_names)

if not fluid_zone_names:
    raise ValueError(
        f"No fluid cell zone found. "
        f"Cell zones by type: {cell_zones_by_type}"
    )

# Geometry can contain multiple disconnected fluid bodies.
# Apply UDF source terms and species patch to every fluid cell zone.
target_fluid_zones = list(fluid_zone_names)

print(f"Target fluid zones for source terms and patch: {target_fluid_zones}")

for fluid_zone_name in target_fluid_zones:
    fluid_zone_object = setup.cell_zone_conditions.fluid[fluid_zone_name]
    print(f"Fluid zone state: {fluid_zone_name}")
    print(fluid_zone_object.get_state())

if update_rho_avg_report_definition:
    recreate_volume_average_report_definition(
        solution=solution,
        report_name=rho_avg_report_name,
        field_name=rho_avg_report_field,
        cell_zones=target_fluid_zones,
    )
else:
    print("Skipping rho_avg report definition update.")


# ======================================================
# ##### [8] Verify Template Physics and Materials #####
# ======================================================

print("Viscous model state:")
print(setup.models.viscous.get_state())

print("Species model state:")
print(setup.models.species.get_state())

print("Energy model state:")
print(setup.models.energy.get_state())

mixture_materials = setup.materials.mixture.get_object_names()
print("Mixture materials:", mixture_materials)

if "mixture-template" in mixture_materials:
    mixture_name = "mixture-template"
else:
    mixture_name = mixture_materials[0]

mixture_object = setup.materials.mixture[mixture_name]

print("Mixture species state:")
print(mixture_object.species.get_state())

print("Mixture density state:")
print(mixture_object.density.get_state())

print("Mixture viscosity state:")
print(mixture_object.viscosity.get_state())

print("Mixture mass diffusivity state:")
print(mixture_object.mass_diffusivity.get_state())

print("Fluid zone states:")
for fluid_zone_name in target_fluid_zones:
    print(f"Fluid zone state: {fluid_zone_name}")
    print(setup.cell_zone_conditions.fluid[fluid_zone_name].get_state())


# ======================================================
# ##### [9] Operating Conditions #####
# ======================================================

setup.general.operating_conditions.operating_pressure = operating_pressure

print("Operating conditions state:")
print(setup.general.operating_conditions.get_state())

print(f"Operating pressure set to: {operating_pressure} Pa")
print(f"Target outlet gauge pressure value: {outlet_gauge_pressure} Pa")


# ======================================================
# ##### [10] Boundary Conditions #####
# ======================================================

# Refresh boundary zone lists after any type conversion.
boundary_zones_by_type = collect_boundary_zones(setup)
boundary_zone_names = sorted(
    zone_name
    for names in boundary_zones_by_type.values()
    for zone_name in names
)

inlet_zone_names = find_zones_by_base_name(boundary_zone_names, "inlet")
outlet_zone_names = find_zones_by_base_name(boundary_zone_names, "outlet")
membrane_wall_zone_names = find_zones_by_base_names(boundary_zone_names, membrane_wall_base_names)
buffer_wall_zone_names = find_zones_by_base_names(boundary_zone_names, buffer_wall_base_names)

print("Applying inlet BC to zones:", inlet_zone_names)
print("Applying outlet BC to zones:", outlet_zone_names)
print("Detected active membrane wall zones for UDF name matching:", membrane_wall_zone_names)
print("Detected buffer wall zones (no-slip, not membrane):", buffer_wall_zone_names)

if not inlet_zone_names or not outlet_zone_names or not membrane_wall_zone_names:
    raise ValueError(
        "Missing inlet/outlet/membrane zones after type confirmation. "
        f"Inlets={inlet_zone_names}, outlets={outlet_zone_names}, "
        f"active membranes={membrane_wall_zone_names}"
    )
if not buffer_wall_zone_names:
    raise ValueError(
        f"No buffer wall zones found for base names {buffer_wall_base_names} "
        f"after type confirmation. Available boundary zones: {boundary_zone_names}"
    )

# Determine species order from the first inlet, then apply the same BC to every inlet zone.
first_inlet_zone = inlet_zone_names[0]
vin_first = setup.boundary_conditions.velocity_inlet[first_inlet_zone]

inlet_species_state = vin_first.species.species_mass_fraction.get_state()
inlet_species_names = list(inlet_species_state.keys())

print("Available inlet species:")
print(inlet_species_names)

if target_species_name not in inlet_species_names:
    raise ValueError(
        f'Target species "{target_species_name}" was not found at the inlet. '
        f"Available inlet species: {inlet_species_names}. "
        "Check the template material/species setup before continuing."
    )

species_name = target_species_name
salt_yi_index = inlet_species_names.index(species_name)

print(f"Target species selected: {species_name}")
print(f"SALT_YI_INDEX inferred from inlet species list: {salt_yi_index}")

for inlet_zone_name in inlet_zone_names:
    vin = setup.boundary_conditions.velocity_inlet[inlet_zone_name]
    vin.momentum.velocity_magnitude.value = inlet_velocity
    vin.species.species_mass_fraction[species_name].value = salt_mass_fraction
    print(f"Inlet BC set on {inlet_zone_name}: velocity={inlet_velocity} m/s, {species_name}={salt_mass_fraction}")
    print(vin.get_state())

for outlet_zone_name in outlet_zone_names:
    pout = setup.boundary_conditions.pressure_outlet[outlet_zone_name]
    pout.momentum.gauge_pressure.value = outlet_gauge_pressure
    print(f"Outlet gauge pressure set on {outlet_zone_name}: {outlet_gauge_pressure} Pa")

    outlet_species_state = pout.species.backflow_species_mass_fraction.get_state()
    outlet_species_names = list(outlet_species_state.keys())

    if target_species_name in outlet_species_names:
        pout.species.backflow_species_mass_fraction[target_species_name].value = salt_mass_fraction
        print(
            f"Outlet backflow species mass fraction set on {outlet_zone_name}: "
            f"{target_species_name} = {salt_mass_fraction}"
        )
    else:
        print(f"Warning: {target_species_name} was not found in outlet backflow species list for {outlet_zone_name}.")

    print(pout.get_state())

wall_spacer_wall_objects = [
    setup.boundary_conditions.wall[zone_name]
    for zone_name in wall_spacer_zones
]

membrane_wall_objects = [
    setup.boundary_conditions.wall[zone_name]
    for zone_name in membrane_wall_zone_names
]

print(f"Membrane wall zones selected: {membrane_wall_zone_names}")
print(f"Number of membrane wall zones: {len(membrane_wall_objects)}")
print(f"Number of spacer wall zones: {len(wall_spacer_wall_objects)}")

for membrane_wall_zone_name, membrane_wall in zip(membrane_wall_zone_names, membrane_wall_objects):
    print(f"Membrane wall state: {membrane_wall_zone_name}")
    print(membrane_wall.get_state())

# Rebuild report definitions that depend on current boundary names.
# This fixes split-zone geometries where inlet/outlet/wall become inlet.1, outlet.1, wall.1, etc.
update_transport_report_definitions_for_current_zones(
    solution=solution,
    inlet_zones=inlet_zone_names,
    outlet_zones=outlet_zone_names,
    membrane_wall_zones=membrane_wall_zone_names,
    density_value=mixture_density,
    m_in_name=m_in_report_name,
    m_out_name=m_out_report_name,
    area_mem_name=area_mem_report_name,
    lmh_name=lmh_report_name,
)


# ======================================================
# ##### [11] UDM Allocation + UDF Compile/Load #####
# ======================================================

print(f"UDF active membrane wall base names (hardcoded in UDF): {membrane_wall_base_names}")
print(f"UDF-9  = inlet-referenced CP, Cm/C_INLET_REF")
print(f"UDF-10 = cell-centered strain rate magnitude [1/s] (not wall shear rate)")
print(f"Using SALT_YI_INDEX = {salt_yi_index}")

# Allocate User-Defined Memory using Fluent TUI.
# The settings API path setup.user_defined.memory is not available in this solver tree.
solver.execute_tui(f"/define/user-defined/user-defined-memory {udm_count}")

print(f"Requested UDM memory locations: {udm_count}")

udf_case_path = copy_and_patch_udf_to_case_folder(
    source_path=udf_master_path,
    destination_path=udf_case_path,
    salt_yi_index_value=salt_yi_index,
)

solver.tui.define.user_defined.compiled_functions(
    "compile",
    udf_library_name,
    "yes",
    as_fluent_path(udf_case_path),
    "",
    "",
)

print("UDF compile command completed.")

solver.tui.define.user_defined.compiled_functions(
    "load",
    udf_library_name,
)

print("UDF library loaded.")


# ======================================================
# ##### [12] Hook Adjust and Init Functions #####
# ======================================================

adjust_hook_tui = f'/define/user-defined/function-hooks/adjust "{adjust_function_name}"'
init_hook_tui = f'/define/user-defined/function-hooks/initialization "{init_function_name}"'

print("Trying adjust hook TUI command:")
print(adjust_hook_tui)
solver.execute_tui(adjust_hook_tui)
print("Adjust hook TUI command completed.")

print("Trying initialization hook TUI command:")
print(init_hook_tui)
solver.execute_tui(init_hook_tui)
print("Initialization hook TUI command completed.")


# ======================================================
# ##### [13] Enable and Hook Cell Zone UDF Source Terms #####
# ======================================================

# Use the species source key corresponding to SALT_YI_INDEX.
species_source_key = f"species-{salt_yi_index}"

source_term_map = {
    "mass": source_function_names["mass"],
    species_source_key: source_function_names["species_salt"],
    "x-momentum": source_function_names["x_momentum"],
    "y-momentum": source_function_names["y_momentum"],
    "z-momentum": source_function_names["z_momentum"],
}

print("\nRequested source term hooks:")
print(source_term_map)


def hook_udf_source_term(fluid_zone_object, fluid_zone_name, term_key, udf_name):
    """Hook one UDF source term to one fluid cell zone."""
    term = fluid_zone_object.sources.terms[term_key]

    # Use one source entry for each equation.
    term.resize(1)
    entry = term[0]

    print(f"\nHooking source term for fluid zone '{fluid_zone_name}': {term_key}")
    print("Before:")
    print(entry.get_state())

    entry.option.set_state("udf")
    entry.udf.set_state(udf_name)

    print("After:")
    print(entry.get_state())


for fluid_zone_name in target_fluid_zones:
    fluid_zone_object = setup.cell_zone_conditions.fluid[fluid_zone_name]

    # Enable source terms for every fluid cell zone.
    fluid_zone_object.sources.enable = True

    print(f"\nFluid zone sources state before source-term hooking: {fluid_zone_name}")
    print(fluid_zone_object.sources.get_state())

    available_source_terms = list(fluid_zone_object.sources.terms.get_state().keys())

    print(f"\nAvailable source term keys for fluid zone '{fluid_zone_name}':")
    print(available_source_terms)

    missing_source_terms = [
        term_key for term_key in source_term_map
        if term_key not in available_source_terms
    ]

    if missing_source_terms:
        raise ValueError(
            f"Missing source term keys in fluid zone '{fluid_zone_name}': "
            f"{missing_source_terms}. "
            f"Available source term keys: {available_source_terms}"
        )

    for term_key, udf_name in source_term_map.items():
        hook_udf_source_term(
            fluid_zone_object=fluid_zone_object,
            fluid_zone_name=fluid_zone_name,
            term_key=term_key,
            udf_name=udf_name,
        )

    print(f"\nFull source terms state after hooking for fluid zone '{fluid_zone_name}':")
    print(fluid_zone_object.sources.terms.get_state())

    print(f"\nFull fluid zone sources state after hooking for fluid zone '{fluid_zone_name}':")
    print(fluid_zone_object.sources.get_state())


# ======================================================
# ##### [14] Residual Settings #####
# ======================================================

residual_equations_state = solution.monitor.residual.equations.get_state()
available_residual_equations = list(residual_equations_state.keys())

print("Available residual equations:")
print(available_residual_equations)

target_residual_equations = [
    "continuity",
    "x-velocity",
    "y-velocity",
    "z-velocity",
    species_name,
]

print("Target residual equations:")
print(target_residual_equations)

for eq in target_residual_equations:
    if eq in available_residual_equations:
        res_eq = solution.monitor.residual.equations[eq]

        print(f"\nSetting residual equation: {eq}")
        print("Before:")
        print(res_eq.get_state())

        res_eq.monitor = True
        res_eq.check_convergence = True

        current_state = res_eq.get_state()

        if "absolute_criteria" in current_state:
            res_eq.absolute_criteria = residual_target
        elif "relative_criteria" in current_state:
            res_eq.relative_criteria = residual_target
        else:
            raise AttributeError(
                f"No residual criteria field found for equation '{eq}'. "
                f"Current state: {current_state}"
            )

        print("After:")
        print(res_eq.get_state())
        print(f"Residual criterion set: {eq} = {residual_target}")

    else:
        print(f"Residual equation not found. Skipping: {eq}")

print("\nResidual equations state after setting:")
print(solution.monitor.residual.equations.get_state())


# ======================================================
# ##### [15] Initialization and Species Patch #####
# ======================================================

print("Initialization state before hybrid initialization:")
print(solution.initialization.get_state())

solution.initialization.initialization_type = "hybrid"

solution.initialization.hybrid_initialize()

print("\nHybrid initialization completed.")

species_patch_variable = f"species-{salt_yi_index}"

patch_command = solution.initialization.patch.calculate_patch

patch_command(
    domain="mixture",
    cell_zones=target_fluid_zones,
    registers=[],
    variable=species_patch_variable,
    reference_frame="Relative to Cell Zone",
    use_custom_field_function=False,
    custom_field_function_name="",
    value=salt_mass_fraction,
)

print(
    f"Patched fluid zones {target_fluid_zones}: "
    f"{species_patch_variable} ({species_name}) = {salt_mass_fraction}"
)

print("\nInitialization state after hybrid initialization and patch:")
print(solution.initialization.get_state())


# ======================================================
# ##### [16] Save Setup Case #####
# ======================================================

solver.settings.file.write_case(file_name=as_fluent_path(setup_case_file))
print(f"Setup case saved: {setup_case_file}")

verify_file_exists(setup_case_file, "setup case file")


# ======================================================
# ##### [17] Run Calculation #####
# ======================================================

# This cell runs the solver calculation.
# If use_ramp_convergence_safety is True:
#   Phase 1 runs a fixed number of iterations with residual convergence stopping disabled.
#   Phase 2 re-enables convergence checks and lets Fluent stop early when residual criteria are met.

if run_calculation_enabled:
    print("Starting solver calculation.")
    print(f"Maximum iterations requested: {max_iterations}")
    print(f"Residual target: {residual_target}")

    if use_ramp_convergence_safety:
        pre_convergence_iterations = minimum_full_source_iterations
        remaining_iterations = max_iterations - pre_convergence_iterations

        if remaining_iterations <= 0:
            raise ValueError(
                "max_iterations must be larger than minimum_full_source_iterations. "
                f"max_iterations={max_iterations}, "
                f"minimum_full_source_iterations={minimum_full_source_iterations}"
            )

        print(
            "\nRunning ramp-up phase without residual convergence stopping.\n"
            f"Ramp full iteration: {ramp_full_iteration}\n"
            f"Post-ramp buffer iterations: {post_ramp_buffer_iterations}\n"
            f"Ramp-up phase iterations: {pre_convergence_iterations}"
        )

        set_residual_convergence_check(
            solution=solution,
            species_name=species_name,
            enable=False,
        )

        solution.run_calculation.iterate(iter_count=pre_convergence_iterations)

        print(
            "\nRamp-up phase completed. "
            "The membrane source ramp should now be fully applied."
        )

        print("\nRe-enabling residual convergence check for full-source convergence phase.")

        set_residual_convergence_check(
            solution=solution,
            species_name=species_name,
            enable=True,
        )

        print(f"\nRunning convergence phase. Maximum additional iterations: {remaining_iterations}")

        solution.run_calculation.iterate(iter_count=remaining_iterations)

    else:
        print("\nRamp convergence safety is disabled.")
        solution.run_calculation.iterate(iter_count=max_iterations)

    print("\nSolver calculation completed.")

    print("\nFinal residual equations state:")
    print(solution.monitor.residual.equations.get_state())

else:
    print("Run calculation is disabled. Skipping solver iterations.")


# ======================================================
# ##### [18] Save Final Case/Data #####
# ======================================================

solver.settings.file.write_case_data(file_name=as_fluent_path(final_case_file))
print(f"Final case/data write command completed: {final_case_file}")

verify_file_exists(final_case_file, "final case file")
verify_file_exists(final_data_file, "final data file")


# ======================================================
# ##### Cleanup #####
# ======================================================

if transcript_is_running:
    solver.transcript.stop()
    transcript_is_running = False
    print("Solver transcript stopped.")

if solver is not None:
    solver.exit()
    print("Fluent solver exited.")

os.chdir(original_working_directory)
print(f"Working directory restored: {original_working_directory}")
