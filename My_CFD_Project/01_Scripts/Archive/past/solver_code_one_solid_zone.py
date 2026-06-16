# ==========================================================
# ##### [0] Import Required Packages #####
# ==========================================================
import ansys.fluent.core as pyfluent
import os
import shutil
import re

# ==========================================================
# ##### [1] User Defined Parameters #####
# ==========================================================

# Project paths
project_root = "C:/PyFluent/My_CFD_Project"
geo_name = "===== Edit here ====="
case_name = "===== Edit here ====="

case_path = os.path.join(project_root, "03_Results", geo_name, case_name)
mesh_file_path = os.path.join(case_path, f"{geo_name}_{case_name}.msh.h5")

# Template case path
# This template case already contains the RO material/species setup.
template_case_path = os.path.join(
    project_root,
    "01_Templates",
    "template_RO_setup.cas.h5",
)

# Fluent launch settings
product_version = "25.1.0"
processor_count = 8
graphics_driver = "dx11"
fluent_start_timeout = 300
fluent_health_timeout = 300

# UDF source
# Use the ramp-enabled UDF with the ramp-safe solver run below.
# If you use a no-ramp UDF, this solver run logic still works, but the ramp safety phase is not strictly necessary.
udf_master_path = os.path.join(project_root, "02_UDFs", "260428_RO_UDF.c")
udf_case_path = os.path.join(case_path, os.path.basename(udf_master_path))
udf_library_name = "libudf"

# Boundary and zone names
membrane_wall_name = "wall"

# Membrane wall zone ID will be parsed automatically after replace_mesh.
# The named selection / boundary zone name must remain consistent across geometries.
membrane_thread_id = None

# Separate log used only for parsing zone-name to zone-id mapping after replace_mesh.
solver_mesh_replace_log_path = os.path.join(
    case_path,
    f"solver_mesh_replace_log_{case_name}.txt",
)

# Species/material settings
# In the template, the material name is "salt", but the actual volumetric species name is "nacl".
# Boundary species lists and SALT_YI_INDEX must use the actual species name: "nacl".
target_species_name = "nacl"
salt_material_name = "salt"
salt_chemical_formula = "nacl"
salt_mass_fraction = 0.035

# Manual/template material setup values
salt_density = 998.2
salt_viscosity = 8.93e-4
salt_molecular_weight = 58.44

mixture_name = "mixture-template"
mixture_density = 998.2
mixture_viscosity = 8.93e-4
mass_diffusivity = 2.0e-9

# Boundary values
inlet_velocity = 0.2
operating_pressure = 101325.0
outlet_gauge_pressure = 6.0e6

# UDM and UDF hooks
udm_count = 12

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
residual_target = 1e-7
max_iterations = 2000

# Set True for production runs.
run_calculation_enabled = True

# Ramp/convergence safety.
# 260428_RO_UDF.c uses a source ramp that reaches full strength after 150 iterations.
# This solver logic disables residual convergence stopping until the ramp is fully applied,
# then re-enables residual convergence checks for the final full-source solution.
use_ramp_convergence_safety = True
ramp_full_iteration = 150
post_ramp_buffer_iterations = 50
minimum_full_source_iterations = ramp_full_iteration + post_ramp_buffer_iterations

# Output files
solver_log_path = os.path.join(case_path, f"solver_log_{case_name}.txt")
setup_case_file = os.path.join(case_path, f"{geo_name}_{case_name}_setup.cas.h5")
final_case_file = os.path.join(case_path, f"{geo_name}_{case_name}_final.cas.h5")
final_data_file = final_case_file.replace(".cas.h5", ".dat.h5")


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


def copy_and_patch_udf_to_case_folder(
    source_path,
    destination_path,
    membrane_thread_id_value,
    salt_yi_index_value,
):
    """Copy UDF to case folder and patch MEMB_THREAD_ID and SALT_YI_INDEX."""
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
        "MEMB_THREAD_ID",
        membrane_thread_id_value,
    )

    udf_text = replace_define_int(
        udf_text,
        "SALT_YI_INDEX",
        salt_yi_index_value,
    )

    with open(destination_path, "w", encoding="utf-8", newline="\n") as file:
        file.write(udf_text)

    print(f"Patched MEMB_THREAD_ID = {membrane_thread_id_value}")
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

membrane_thread_id = parse_zone_id_from_log(
    log_path=solver_mesh_replace_log_path,
    zone_name=membrane_wall_name,
)

print(f"Automatic MEMB_THREAD_ID selected: {membrane_thread_id}")

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

required_boundary_zones = [
    "inlet",
    "outlet",
    membrane_wall_name,
]

require_items(
    required_items=required_boundary_zones,
    available_items=boundary_zone_names,
    item_type="boundary zones",
)

wall_zone_names = boundary_zones_by_type["wall"]

wall_spacer_zones = sorted(
    zone_name for zone_name in wall_zone_names
    if zone_name.startswith("wall_spacer")
)

print("Wall zones:", wall_zone_names)
print("Wall spacer zones:", wall_spacer_zones)
print(f"Membrane wall zone name: {membrane_wall_name}")


# ======================================================
# ##### [6] Confirm Boundary Zone Types #####
# ======================================================

boundary_zones_by_type = collect_boundary_zones(setup)

velocity_inlet_zones = boundary_zones_by_type["velocity_inlet"]
pressure_outlet_zones = boundary_zones_by_type["pressure_outlet"]

print("Current velocity-inlet zones:", velocity_inlet_zones)
print("Current pressure-outlet zones:", pressure_outlet_zones)

if "inlet" not in velocity_inlet_zones:
    print("Setting inlet zone type to velocity-inlet.")
    setup.boundary_conditions.set_zone_type(
        zone_list=["inlet"],
        new_type="velocity-inlet",
    )
else:
    print("Inlet is already velocity-inlet. Skipping type change.")

if "outlet" not in pressure_outlet_zones:
    print("Setting outlet zone type to pressure-outlet.")
    setup.boundary_conditions.set_zone_type(
        zone_list=["outlet"],
        new_type="pressure-outlet",
    )
else:
    print("Outlet is already pressure-outlet. Skipping type change.")

boundary_zones_by_type = collect_boundary_zones(setup)
cell_zones_by_type = collect_cell_zones(setup)

print("Boundary zones after type confirmation:")
for zone_type, names in boundary_zones_by_type.items():
    print(f"  {zone_type}: {names}")

print("Cell zones after type confirmation:")
for zone_type, names in cell_zones_by_type.items():
    print(f"  {zone_type}: {names}")


# ======================================================
# ##### [7] Select Fluid Cell Zone #####
# ======================================================

cell_zones_by_type = collect_cell_zones(setup)
fluid_zone_names = cell_zones_by_type["fluid"]

print("Fluid cell zones:", fluid_zone_names)

if not fluid_zone_names:
    raise ValueError(
        f"No fluid cell zone found. "
        f"Cell zones by type: {cell_zones_by_type}"
    )

main_fluid_zone = "solid" if "solid" in fluid_zone_names else fluid_zone_names[0]

print(f"Main fluid zone selected: {main_fluid_zone}")

fluid_zone_object = setup.cell_zone_conditions.fluid[main_fluid_zone]

print("Fluid zone object selected successfully.")
print("Fluid zone state:")
print(fluid_zone_object.get_state())


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

print("Fluid zone state:")
print(setup.cell_zone_conditions.fluid[main_fluid_zone].get_state())


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

vin = setup.boundary_conditions.velocity_inlet["inlet"]

vin.momentum.velocity_magnitude.value = inlet_velocity
print(f"Inlet velocity set to {inlet_velocity} m/s")

print("Velocity inlet state:")
print(vin.get_state())

inlet_species_state = vin.species.species_mass_fraction.get_state()
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

vin.species.species_mass_fraction[species_name].value = salt_mass_fraction

print(
    f"Inlet species mass fraction set: "
    f"{species_name} = {salt_mass_fraction}"
)

pout = setup.boundary_conditions.pressure_outlet["outlet"]

pout.momentum.gauge_pressure.value = outlet_gauge_pressure
print(f"Outlet gauge pressure set to {outlet_gauge_pressure} Pa")

print("Pressure outlet state:")
print(pout.get_state())

outlet_species_state = pout.species.backflow_species_mass_fraction.get_state()
outlet_species_names = list(outlet_species_state.keys())

print("Available outlet backflow species:")
print(outlet_species_names)

if target_species_name in outlet_species_names:
    pout.species.backflow_species_mass_fraction[target_species_name].value = salt_mass_fraction
    print(
        f"Outlet backflow species mass fraction set: "
        f"{target_species_name} = {salt_mass_fraction}"
    )
else:
    print(f"Warning: {target_species_name} was not found in outlet backflow species list.")

wall_spacer_wall_objects = [
    setup.boundary_conditions.wall[zone_name]
    for zone_name in wall_spacer_zones
]

membrane_wall = setup.boundary_conditions.wall[membrane_wall_name]

print(f"Membrane wall zone selected: {membrane_wall_name}")
print(f"Number of spacer wall zones: {len(wall_spacer_wall_objects)}")

print("Membrane wall state:")
print(membrane_wall.get_state())


# ======================================================
# ##### [11] UDM Allocation + UDF Compile/Load #####
# ======================================================

if membrane_thread_id is None:
    raise RuntimeError(
        "membrane_thread_id was not set. "
        "Check that Cell [4] parsed the membrane wall zone ID successfully."
    )

print(f"Using MEMB_THREAD_ID = {membrane_thread_id}")
print(f"Using SALT_YI_INDEX = {salt_yi_index}")

# Allocate User-Defined Memory using Fluent TUI.
# The settings API path setup.user_defined.memory is not available in this solver tree.
solver.execute_tui(f"/define/user-defined/user-defined-memory {udm_count}")

print(f"Requested UDM memory locations: {udm_count}")

udf_case_path = copy_and_patch_udf_to_case_folder(
    source_path=udf_master_path,
    destination_path=udf_case_path,
    membrane_thread_id_value=membrane_thread_id,
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

# Enable source terms for the selected fluid cell zone.
fluid_zone_object.sources.enable = True

print("Fluid zone sources state before source-term hooking:")
print(fluid_zone_object.sources.get_state())

# Use the species source key corresponding to SALT_YI_INDEX.
species_source_key = f"species-{salt_yi_index}"

source_term_map = {
    "mass": source_function_names["mass"],
    species_source_key: source_function_names["species_salt"],
    "x-momentum": source_function_names["x_momentum"],
    "y-momentum": source_function_names["y_momentum"],
    "z-momentum": source_function_names["z_momentum"],
}

available_source_terms = list(fluid_zone_object.sources.terms.get_state().keys())

print("\nAvailable source term keys:")
print(available_source_terms)

print("\nRequested source term hooks:")
print(source_term_map)

missing_source_terms = [
    term_key for term_key in source_term_map
    if term_key not in available_source_terms
]

if missing_source_terms:
    raise ValueError(
        f"Missing source term keys: {missing_source_terms}. "
        f"Available source term keys: {available_source_terms}"
    )


def hook_udf_source_term(term_key, udf_name):
    """Hook one UDF source term to the selected fluid cell zone."""
    term = fluid_zone_object.sources.terms[term_key]

    # Use one source entry for each equation.
    term.resize(1)
    entry = term[0]

    print(f"\nHooking source term: {term_key}")
    print("Before:")
    print(entry.get_state())

    entry.option.set_state("udf")
    entry.udf.set_state(udf_name)

    print("After:")
    print(entry.get_state())


for term_key, udf_name in source_term_map.items():
    hook_udf_source_term(
        term_key=term_key,
        udf_name=udf_name,
    )

print("\nFull source terms state after hooking:")
print(fluid_zone_object.sources.terms.get_state())

print("\nFull fluid zone sources state after hooking:")
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
    cell_zones=[main_fluid_zone],
    registers=[],
    variable=species_patch_variable,
    reference_frame="Relative to Cell Zone",
    use_custom_field_function=False,
    custom_field_function_name="",
    value=salt_mass_fraction,
)

print(
    f"Patched {main_fluid_zone}: "
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
