# ==========================================================
# Cell 1. Import packages and load config
# ==========================================================

import os
import re
import json
import math
import importlib.util
from pathlib import Path
from pprint import pprint

import pandas as pd
import ansys.fluent.core as pyfluent

try:
    from IPython.display import display
    import IPython
    if not IPython.get_ipython():
        raise RuntimeError("Not in Jupyter")
except Exception:
    def display(obj):
        if hasattr(obj, "to_string"):
            print(obj.to_string(index=False))
        else:
            print(obj)


# ----------------------------------------------------------
# Edit this path if needed.
# Use the same Windows/Python environment that successfully ran meshing/solver.
# Override with the PYFLUENT_POST_CONFIG environment variable for batch runs.
# ----------------------------------------------------------
SCRIPT_DIR = Path(r"C:/PyFluent/My_CFD_Project/01_Scripts/post_processing")
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
CONFIG_PATH = Path(os.environ.get("PYFLUENT_POST_CONFIG", str(DEFAULT_CONFIG_PATH)))


def load_python_config(config_path):
    """Load a Python config file whose filename may start with a number."""
    config_path = Path(config_path)

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


cfg = load_python_config(CONFIG_PATH)

print("Config loaded from:")
print(CONFIG_PATH)

print("\nConfig summary:")
print("project_root:", cfg.project_root)
print("geo_name:", cfg.geo_name)
print("case_name:", cfg.case_name)
print("active membrane base names:", cfg.active_membrane_base_names)
print("buffer wall base names:", cfg.buffer_wall_base_names)
print("rho:", cfg.rho)
print("mu:", cfg.mu)

# ==========================================================
# Cell 2. Build paths and create output folders
# ==========================================================

project_root = Path(cfg.project_root)
geo_name = cfg.geo_name
case_name = cfg.case_name

case_path = project_root / "03_Results" / geo_name / case_name

final_case_file = Path(
    getattr(
        cfg,
        "final_case_file",
        case_path / f"{geo_name}_{case_name}_final.cas.h5",
    )
)

final_data_file = Path(
    getattr(
        cfg,
        "final_data_file",
        case_path / f"{geo_name}_{case_name}_final.dat.h5",
    )
)

post_path = case_path / "post"
report_path = post_path / "reports"

post_path.mkdir(parents=True, exist_ok=True)
report_path.mkdir(parents=True, exist_ok=True)

summary_csv_path = report_path / "summary_metrics.csv"
mass_balance_csv_path = report_path / "mass_balance.csv"
pressure_csv_path = report_path / "pressure_report.csv"
raw_report_json_path = report_path / "raw_report_values.json"

print("Case path:", case_path)
print("Final case file:", final_case_file)
print("Final data file:", final_data_file)
print("Report output folder:", report_path)

if not final_case_file.is_file():
    raise FileNotFoundError(f"Final case file not found: {final_case_file}")

if not final_data_file.is_file():
    raise FileNotFoundError(f"Final data file not found: {final_data_file}")


def as_fluent_path(path):
    """Convert a path to a Fluent-friendly absolute path."""
    return os.path.abspath(path).replace("\\", "/")

# ==========================================================
# Cell 3. Helper functions for zones and report definitions
# ==========================================================

def list_named_object_names(named_object, object_label=""):
    """
    Return names from a PyFluent named object.

    This follows the same style as the working solver automation code.
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
    cell_zone_type_names = ["fluid", "solid"]
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
    """Find zones matching any base names, supporting split zone suffixes."""
    matched = []
    for base_name in base_names:
        matched.extend(find_zones_by_base_name(zone_names, base_name))
    return sorted(set(matched))


def delete_report_definition_if_exists(report_group, report_name, group_label):
    """Delete a report definition if it exists."""
    report_names = list_named_object_names(
        named_object=report_group,
        object_label=group_label,
    )

    if report_name not in report_names:
        return False

    try:
        report_group.delete(report_name)
        print(f"Deleted existing report definition: {report_name}")
        return True
    except Exception as e:
        print(f"Could not delete report definition {report_name}. Error: {e}")
        return False


def create_or_update_flux_massflow_report(solution, report_name, boundaries):
    """Create/update a flux-massflow report definition."""
    if not boundaries:
        raise ValueError(f"Cannot create {report_name}: no boundaries.")

    group = solution.report_definitions.flux
    names = list_named_object_names(group, "solution.report_definitions.flux")

    if report_name in names:
        rd = group[report_name]
        print(f"Updating flux report: {report_name}")
    else:
        rd = group.create(report_name)
        print(f"Creating flux report: {report_name}")

    rd.report_type = "flux-massflow"
    rd.boundaries = list(boundaries)
    rd.per_zone = False

    return report_name


def create_or_update_surface_report(solution, report_name, report_type, field_name, surface_names):
    """Create/update a surface report definition."""
    if not surface_names:
        raise ValueError(f"Cannot create {report_name}: no surfaces.")

    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")

    if report_name in names:
        rd = group[report_name]
        print(f"Updating surface report: {report_name}")
    else:
        rd = group.create(report_name)
        print(f"Creating surface report: {report_name}")

    rd.report_type = report_type

    # surface-area does not need a field. Other surface reports do.
    if field_name is not None:
        rd.field = field_name

    rd.surface_names = list(surface_names)
    rd.per_surface = False

    return report_name


def create_or_update_volume_report(solution, report_name, report_type, field_name, cell_zones):
    """Create/update a volume report definition."""
    if not cell_zones:
        raise ValueError(f"Cannot create {report_name}: no cell zones.")

    group = solution.report_definitions.volume
    names = list_named_object_names(group, "solution.report_definitions.volume")

    if report_name in names:
        rd = group[report_name]
        print(f"Updating volume report: {report_name}")
    else:
        rd = group.create(report_name)
        print(f"Creating volume report: {report_name}")

    rd.report_type = report_type
    rd.field = field_name

    # In some Fluent versions, this can be a list; in others, it behaves like single-select.
    # For the current RO cases, there is usually one fluid zone named "solid".
    if len(cell_zones) == 1:
        rd.cell_zones = cell_zones[0]
    else:
        rd.cell_zones = list(cell_zones)

    return report_name


def create_or_update_single_expression_report(solution, report_name, definition):
    """Create/update a single-valued expression report."""
    group = solution.report_definitions.single_valued_expression
    names = list_named_object_names(
        group,
        "solution.report_definitions.single_valued_expression",
    )

    if report_name in names:
        rd = group[report_name]
        print(f"Updating expression report: {report_name}")
    else:
        rd = group.create(report_name)
        print(f"Creating expression report: {report_name}")

    rd.definition = definition

    return report_name


def find_first_number(obj):
    """Recursively find the first numeric value inside PyFluent compute result."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)

    if isinstance(obj, dict):
        for value in obj.values():
            found = find_first_number(value)
            if found is not None:
                return found

    if isinstance(obj, (list, tuple)):
        for value in obj:
            found = find_first_number(value)
            if found is not None:
                return found

    return None


def compute_one_report(solution, report_name, verbose=True):
    """Compute one report definition and return its first numeric value."""
    result = solution.report_definitions.compute(report_defs=[report_name])

    if verbose:
        print(f"\nRaw compute result for {report_name}:")
        pprint(result)

    value = find_first_number(result)

    if value is None:
        print(f"Warning: could not extract numeric value from report {report_name}.")

    return value, result


def create_x_normal_plane(solver_obj, surface_name, x_value_m):
    """Create an x-normal iso-surface at x_value_m (meters). Overwrites if it exists."""
    # Try settings API first.
    e_settings = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list_named_object_names(iso_group, "results.surfaces.iso_surface")
        if surface_name in existing:
            try:
                iso_group.delete(surface_name)
                print(f"Deleted existing iso-surface: {surface_name}")
            except Exception as del_e:
                print(f"Could not delete iso-surface {surface_name}: {del_e}")

        iso_group.create(surface_name)
        iso_group[surface_name].field = "x-coordinate"
        iso_group[surface_name].iso_values = [x_value_m]
        print(f"Created iso-surface '{surface_name}' at x = {x_value_m:.6e} m (settings API)")
        return
    except Exception as exc:
        e_settings = exc
        print(f"Settings API failed for iso-surface '{surface_name}': {exc}")

    # Fallback: TUI iso-surface command.
    try:
        solver_obj.tui.surface.iso_surface(
            "x-coordinate",
            surface_name,
            "()",
            "()",
            str(x_value_m),
            "0",
        )
        print(f"Created iso-surface '{surface_name}' at x = {x_value_m:.6e} m (TUI fallback)")
    except Exception as e_tui:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {e_settings}. TUI error: {e_tui}"
        )

# ==========================================================
# Cell 4. Launch Fluent through meshing mode and switch to solver
# ==========================================================

product_version = getattr(cfg, "product_version", "25.1.0")

# For post-processing, 1 core is enough for the first test.
# You can increase this later if needed.
processor_count = getattr(cfg, "processor_count", 1)

graphics_driver = getattr(cfg, "graphics_driver", "dx11")

fluent_start_timeout = getattr(cfg, "fluent_start_timeout", 600)
fluent_health_timeout = getattr(cfg, "fluent_health_timeout", 600)

pyfluent.config.check_health_timeout = fluent_health_timeout

meshing = None
solver = None
setup = None
solution = None
transcript_is_running = False
original_working_directory = os.getcwd()

try:
    print("Launching Fluent in meshing mode, then switching to solver...")
    print(f"product_version = {product_version}")
    print(f"processor_count = {processor_count}")
    print(f"start_timeout = {fluent_start_timeout}")
    print(f"health_timeout = {fluent_health_timeout}")
    print(f"working directory = {case_path}")

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

    print("Meshing session launched successfully.")
    print("Switching to solver...")

    solver = meshing.switch_to_solver()
    meshing = None

    setup = solver.settings.setup
    solution = solver.settings.solution

    print("Switched to solver successfully.")

    print("Reading final case/data...")
    solver.settings.file.read_case_data(
        file_name=as_fluent_path(final_case_file)
    )

    print("Final case/data loaded.")

    # ==========================================================
    # Cell 5. Detect zones
    # ==========================================================

    boundary_zones_by_type = collect_boundary_zones(setup)
    cell_zones_by_type = collect_cell_zones(setup)

    print("Boundary zones by type:")
    for zone_type, names in boundary_zones_by_type.items():
        print(f"  {zone_type}: {names}")

    print("\nCell zones by type:")
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

    inlet_zones = find_zones_by_base_name(boundary_zone_names, "inlet")
    outlet_zones = find_zones_by_base_name(boundary_zone_names, "outlet")

    active_membrane_zones = find_zones_by_base_names(
        boundary_zone_names,
        cfg.active_membrane_base_names,
    )

    buffer_wall_zones = find_zones_by_base_names(
        boundary_zone_names,
        cfg.buffer_wall_base_names,
    )

    wall_zone_names = boundary_zones_by_type.get("wall", [])
    spacer_wall_zones = sorted(
        zone_name for zone_name in wall_zone_names
        if zone_name.startswith("wall_spacer")
    )

    fluid_zones = cell_zones_by_type.get("fluid", [])

    print("\nDetected zones:")
    print("Inlet zones:", inlet_zones)
    print("Outlet zones:", outlet_zones)
    print("Active membrane zones:", active_membrane_zones)
    print("Buffer wall zones:", buffer_wall_zones)
    print("Spacer wall zones:", spacer_wall_zones)
    print("Fluid zones:", fluid_zones)

    if not inlet_zones:
        raise ValueError("No inlet zones found.")
    if not outlet_zones:
        raise ValueError("No outlet zones found.")
    if not active_membrane_zones:
        raise ValueError("No active membrane zones found.")
    if not buffer_wall_zones:
        raise ValueError("No buffer wall zones found.")
    if not fluid_zones:
        raise ValueError("No fluid zones found.")

    # ==========================================================
    # Cell 6. Define report field names
    # ==========================================================

    rho = cfg.rho
    mu = cfg.mu

    # Basic field names.
    FIELD_PRESSURE = "pressure"
    FIELD_ABSOLUTE_PRESSURE = "absolute-pressure"
    FIELD_VELOCITY_MAG = "velocity-magnitude"

    # UDM field names from Fluent field list.
    FIELD_UDM_SM = "udm-1"          # water mass source
    FIELD_UDM_SI = "udm-0"          # salt mass source
    FIELD_UDM_TOTAL_S = "udm-2"     # total mass source

    FIELD_UDM_JW = "udm-6"
    FIELD_UDM_CM = "udm-7"
    FIELD_UDM_LMH = "udm-8"
    FIELD_UDM_CP_INLET = "udm-9"
    FIELD_UDM_CELL_STRAIN_RATE = "udm-10"
    FIELD_UDM_MEMBRANE_AREA_ACC = "udm-11"
    FIELD_UDM_SALT_FLUX = "udm-12"

    # Wall shear stress magnitude field.
    # Wall shear rate will be calculated later as wall_shear / mu.
    FIELD_WALL_SHEAR = "wall-shear"

    print("Field names selected:")
    for name, value in {
        "FIELD_PRESSURE": FIELD_PRESSURE,
        "FIELD_ABSOLUTE_PRESSURE": FIELD_ABSOLUTE_PRESSURE,
        "FIELD_VELOCITY_MAG": FIELD_VELOCITY_MAG,
        "FIELD_UDM_SM": FIELD_UDM_SM,
        "FIELD_UDM_SI": FIELD_UDM_SI,
        "FIELD_UDM_TOTAL_S": FIELD_UDM_TOTAL_S,
        "FIELD_UDM_JW": FIELD_UDM_JW,
        "FIELD_UDM_CM": FIELD_UDM_CM,
        "FIELD_UDM_LMH": FIELD_UDM_LMH,
        "FIELD_UDM_CP_INLET": FIELD_UDM_CP_INLET,
        "FIELD_UDM_CELL_STRAIN_RATE": FIELD_UDM_CELL_STRAIN_RATE,
        "FIELD_UDM_MEMBRANE_AREA_ACC": FIELD_UDM_MEMBRANE_AREA_ACC,
        "FIELD_UDM_SALT_FLUX": FIELD_UDM_SALT_FLUX,
        "FIELD_WALL_SHEAR": FIELD_WALL_SHEAR,
    }.items():
        print(f"  {name}: {value}")

    # ==========================================================
    # Cell 6.5. Compute spacer geometry parameters
    # ==========================================================

    domain_x_min_m = getattr(cfg, "domain_x_min_m", 0.0)
    domain_length_m = getattr(cfg, "domain_length_m", 0.017325)
    buffer_length_m = getattr(cfg, "buffer_length_m", 0.003465)

    domain_x_max_m = domain_x_min_m + domain_length_m
    spacer_x_in_m = domain_x_min_m + buffer_length_m
    spacer_x_out_m = domain_x_max_m - buffer_length_m
    spacer_length_m = domain_length_m - 2.0 * buffer_length_m

    if spacer_length_m <= 0.0:
        raise ValueError(
            f"spacer_length_m must be > 0, got {spacer_length_m}. "
            f"Check domain_length_m={domain_length_m} and buffer_length_m={buffer_length_m}."
        )

    print("\nSpacer plane locations:")
    print(f"  spacer_x_in_m   = {spacer_x_in_m:.6e} m")
    print(f"  spacer_x_out_m  = {spacer_x_out_m:.6e} m")
    print(f"  spacer_length_m = {spacer_length_m:.6e} m")

    # ==========================================================
    # Cell 7. Create report definitions
    # ==========================================================

    report_names = []
    failed_report_specs = []

    # ----------------------------------------------------------
    # Surface report type names used by this PyFluent version.
    # Based on the allowed values shown in the error message:
    # surface-areawtui, surface-facetmax, surface-facetmin, etc.
    # ----------------------------------------------------------
    SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
    SURFACE_FACET_MAX = "surface-facetmax"
    SURFACE_FACET_MIN = "surface-facetmin"
    SURFACE_AREA = "surface-area"

    # Basic mass flow and membrane area.
    report_names.append(
        create_or_update_flux_massflow_report(
            solution,
            "pp_m_in",
            inlet_zones,
        )
    )

    report_names.append(
        create_or_update_flux_massflow_report(
            solution,
            "pp_m_out",
            outlet_zones,
        )
    )

    report_names.append(
        create_or_update_surface_report(
            solution,
            "pp_area_mem",
            SURFACE_AREA,
            None,
            active_membrane_zones,
        )
    )

    # LMH from mass imbalance.
    lmh_definition = f"abs(pp_m_in + pp_m_out) / ({rho} * pp_area_mem) * 3.6e6"

    report_names.append(
        create_or_update_single_expression_report(
            solution,
            "pp_lmh_mass_balance",
            lmh_definition,
        )
    )

    # Pressure reports.
    report_names.append(
        create_or_update_surface_report(
            solution,
            "pp_p_in_avg",
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_PRESSURE,
            inlet_zones,
        )
    )

    report_names.append(
        create_or_update_surface_report(
            solution,
            "pp_p_out_avg",
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_PRESSURE,
            outlet_zones,
        )
    )

    pressure_drop_definition = "pp_p_in_avg - pp_p_out_avg"

    report_names.append(
        create_or_update_single_expression_report(
            solution,
            "pp_pressure_drop",
            pressure_drop_definition,
        )
    )

    # ----------------------------------------------------------
    # Spacer internal plane surfaces and spacer pressure reports.
    # ----------------------------------------------------------

    print("\nCreating internal plane surfaces for spacer pressure drop...")
    create_x_normal_plane(solver, "pp_plane_spacer_in", spacer_x_in_m)
    create_x_normal_plane(solver, "pp_plane_spacer_out", spacer_x_out_m)

    report_names.append(
        create_or_update_surface_report(
            solution,
            "pp_p_spacer_in_avg",
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_PRESSURE,
            ["pp_plane_spacer_in"],
        )
    )

    report_names.append(
        create_or_update_surface_report(
            solution,
            "pp_p_spacer_out_avg",
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_PRESSURE,
            ["pp_plane_spacer_out"],
        )
    )

    pressure_drop_spacer_definition = "pp_p_spacer_in_avg - pp_p_spacer_out_avg"

    report_names.append(
        create_or_update_single_expression_report(
            solution,
            "pp_pressure_drop_spacer",
            pressure_drop_spacer_definition,
        )
    )

    # Membrane UDM and wall shear reports.
    # These are evaluated on active membrane zones only.
    surface_report_specs = [
        ("pp_jw_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_JW),
        ("pp_jw_max", SURFACE_FACET_MAX, FIELD_UDM_JW),
        ("pp_jw_min", SURFACE_FACET_MIN, FIELD_UDM_JW),

        ("pp_cm_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_CM),
        ("pp_cm_max", SURFACE_FACET_MAX, FIELD_UDM_CM),
        ("pp_cm_min", SURFACE_FACET_MIN, FIELD_UDM_CM),

        ("pp_lmh_udm_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_LMH),
        ("pp_lmh_udm_max", SURFACE_FACET_MAX, FIELD_UDM_LMH),
        ("pp_lmh_udm_min", SURFACE_FACET_MIN, FIELD_UDM_LMH),

        ("pp_cp_inlet_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_CP_INLET),
        ("pp_cp_inlet_max", SURFACE_FACET_MAX, FIELD_UDM_CP_INLET),
        ("pp_cp_inlet_min", SURFACE_FACET_MIN, FIELD_UDM_CP_INLET),

        ("pp_salt_flux_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_SALT_FLUX),
        ("pp_salt_flux_max", SURFACE_FACET_MAX, FIELD_UDM_SALT_FLUX),
        ("pp_salt_flux_min", SURFACE_FACET_MIN, FIELD_UDM_SALT_FLUX),

        # Wall shear stress magnitude [Pa].
        # Wall shear rate [1/s] will be calculated as wall_shear / mu in Cell 9.
        ("pp_wall_shear_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_WALL_SHEAR),
        ("pp_wall_shear_max", SURFACE_FACET_MAX, FIELD_WALL_SHEAR),
        ("pp_wall_shear_min", SURFACE_FACET_MIN, FIELD_WALL_SHEAR),
    ]

    for report_name, report_type, field_name in surface_report_specs:
        try:
            report_names.append(
                create_or_update_surface_report(
                    solution,
                    report_name,
                    report_type,
                    field_name,
                    active_membrane_zones,
                )
            )
        except Exception as e:
            print(f"Warning: failed to create {report_name}. Error: {e}")
            failed_report_specs.append((report_name, report_type, field_name, str(e)))

    # Volume integral reports for source balance.
    # This part may still need adjustment depending on allowed volume report_type names.
    volume_report_specs = [
        ("pp_volint_water_mass_source", "volume-integral", FIELD_UDM_SM),
        ("pp_volint_salt_mass_source", "volume-integral", FIELD_UDM_SI),
        ("pp_volint_total_mass_source", "volume-integral", FIELD_UDM_TOTAL_S),
    ]

    for report_name, report_type, field_name in volume_report_specs:
        try:
            report_names.append(
                create_or_update_volume_report(
                    solution,
                    report_name,
                    report_type,
                    field_name,
                    fluid_zones,
                )
            )
        except Exception as e:
            print(f"Warning: failed to create {report_name}. Error: {e}")
            failed_report_specs.append((report_name, report_type, field_name, str(e)))

    print("\nCreated/updated report definitions:")
    for name in report_names:
        print(" ", name)

    print("\nFailed report specs:")
    pprint(failed_report_specs)

    # ==========================================================
    # Cell 8. Compute reports
    # ==========================================================

    computed_values = {}
    raw_results = {}

    for report_name in report_names:
        try:
            value, raw = compute_one_report(
                solution=solution,
                report_name=report_name,
                verbose=False,
            )
            computed_values[report_name] = value
            raw_results[report_name] = raw
            print(f"{report_name}: {value}")
        except Exception as e:
            computed_values[report_name] = None
            raw_results[report_name] = {"error": str(e)}
            print(f"FAILED: {report_name}. Error: {e}")

    print("\nComputed values:")
    pprint(computed_values)

    # ==========================================================
    # Cell 9. Build summary tables
    # ==========================================================

    # Display small values in scientific notation.
    pd.set_option("display.float_format", "{:.6e}".format)


    def get_value(name):
        """Safely get one computed report value."""
        return computed_values.get(name, None)


    def safe_abs_sum(a, b):
        """Return abs(a + b), or None if either value is missing."""
        if a is None or b is None:
            return None
        return abs(a + b)


    def safe_divide(numerator, denominator):
        """Return numerator / denominator safely."""
        if numerator is None or denominator is None:
            return None
        if denominator == 0:
            return None
        return numerator / denominator


    def safe_subtract(a, b):
        """Return a - b safely."""
        if a is None or b is None:
            return None
        return a - b


    # ----------------------------------------------------------
    # Basic values
    # ----------------------------------------------------------

    m_in = get_value("pp_m_in")
    m_out = get_value("pp_m_out")
    area_mem = get_value("pp_area_mem")
    lmh_mass_balance = get_value("pp_lmh_mass_balance")

    p_in_avg = get_value("pp_p_in_avg")
    p_out_avg = get_value("pp_p_out_avg")
    pressure_drop = get_value("pp_pressure_drop")

    # Boundary permeate mass flow from mass imbalance.
    boundary_permeate_mass_flow = safe_abs_sum(m_in, m_out)

    # ----------------------------------------------------------
    # Membrane transport values
    # ----------------------------------------------------------

    jw_avg = get_value("pp_jw_avg")
    jw_max = get_value("pp_jw_max")
    jw_min = get_value("pp_jw_min")

    cm_avg = get_value("pp_cm_avg")
    cm_max = get_value("pp_cm_max")
    cm_min = get_value("pp_cm_min")

    lmh_udm_avg = get_value("pp_lmh_udm_avg")
    lmh_udm_max = get_value("pp_lmh_udm_max")
    lmh_udm_min = get_value("pp_lmh_udm_min")

    cp_inlet_avg = get_value("pp_cp_inlet_avg")
    cp_inlet_max = get_value("pp_cp_inlet_max")
    cp_inlet_min = get_value("pp_cp_inlet_min")

    salt_flux_avg = get_value("pp_salt_flux_avg")
    salt_flux_max = get_value("pp_salt_flux_max")
    salt_flux_min = get_value("pp_salt_flux_min")

    # ----------------------------------------------------------
    # Wall shear stress and wall shear rate
    # ----------------------------------------------------------

    wall_shear_avg = get_value("pp_wall_shear_avg")
    wall_shear_max = get_value("pp_wall_shear_max")
    wall_shear_min = get_value("pp_wall_shear_min")

    # Wall shear rate = wall shear stress / dynamic viscosity.
    wall_shear_rate_avg = safe_divide(wall_shear_avg, mu)
    wall_shear_rate_max = safe_divide(wall_shear_max, mu)
    wall_shear_rate_min = safe_divide(wall_shear_min, mu)

    # ----------------------------------------------------------
    # Mass source balance
    # ----------------------------------------------------------

    water_sink_volint = get_value("pp_volint_water_mass_source")
    salt_sink_volint = get_value("pp_volint_salt_mass_source")
    total_sink_volint = get_value("pp_volint_total_mass_source")

    mass_balance_error = None
    if boundary_permeate_mass_flow is not None and total_sink_volint is not None:
        mass_balance_error = boundary_permeate_mass_flow - abs(total_sink_volint)

    mass_balance_relative_error = None
    if mass_balance_error is not None and total_sink_volint not in [None, 0.0]:
        mass_balance_relative_error = mass_balance_error / abs(total_sink_volint)

    # ----------------------------------------------------------
    # Pressure drop per length (full inlet-to-outlet)
    # ----------------------------------------------------------

    pressure_drop_per_m = safe_divide(pressure_drop, domain_length_m)

    # ----------------------------------------------------------
    # Spacer-only pressure drop
    # ----------------------------------------------------------

    p_spacer_in_avg = get_value("pp_p_spacer_in_avg")
    p_spacer_out_avg = get_value("pp_p_spacer_out_avg")
    pressure_drop_spacer = get_value("pp_pressure_drop_spacer")

    pressure_drop_spacer_per_m = safe_divide(pressure_drop_spacer, spacer_length_m)

    # ----------------------------------------------------------
    # LMH consistency check
    # ----------------------------------------------------------

    lmh_difference = safe_subtract(lmh_mass_balance, lmh_udm_avg)

    lmh_relative_difference = None
    if lmh_difference is not None and lmh_mass_balance not in [None, 0.0]:
        lmh_relative_difference = lmh_difference / lmh_mass_balance

    # ----------------------------------------------------------
    # Expected outlet pressure
    # ----------------------------------------------------------

    expected_outlet_pressure = getattr(cfg, "outlet_gauge_pressure", 6.0e6)

    # ----------------------------------------------------------
    # Summary table
    # ----------------------------------------------------------

    summary_rows = [
        {"metric": "geo_name", "value": geo_name, "unit": "-"},
        {"metric": "case_name", "value": case_name, "unit": "-"},

        {"metric": "m_in", "value": m_in, "unit": "kg/s"},
        {"metric": "m_out", "value": m_out, "unit": "kg/s"},
        {"metric": "boundary_permeate_mass_flow", "value": boundary_permeate_mass_flow, "unit": "kg/s"},

        {"metric": "area_mem", "value": area_mem, "unit": "m2"},

        {"metric": "lmh_mass_balance", "value": lmh_mass_balance, "unit": "LMH"},
        {"metric": "lmh_udm_avg", "value": lmh_udm_avg, "unit": "LMH"},
        {"metric": "lmh_difference_mass_balance_minus_udm", "value": lmh_difference, "unit": "LMH"},
        {"metric": "lmh_relative_difference", "value": lmh_relative_difference, "unit": "-"},

        {"metric": "p_in_avg", "value": p_in_avg, "unit": "Pa"},
        {"metric": "p_out_avg", "value": p_out_avg, "unit": "Pa"},
        {"metric": "pressure_drop", "value": pressure_drop, "unit": "Pa"},
        {"metric": "domain_length_m", "value": domain_length_m, "unit": "m"},
        {"metric": "pressure_drop_per_m", "value": pressure_drop_per_m, "unit": "Pa/m"},
        {"metric": "spacer_x_in_m", "value": spacer_x_in_m, "unit": "m"},
        {"metric": "spacer_x_out_m", "value": spacer_x_out_m, "unit": "m"},
        {"metric": "spacer_length_m", "value": spacer_length_m, "unit": "m"},
        {"metric": "p_spacer_in_avg", "value": p_spacer_in_avg, "unit": "Pa"},
        {"metric": "p_spacer_out_avg", "value": p_spacer_out_avg, "unit": "Pa"},
        {"metric": "pressure_drop_spacer", "value": pressure_drop_spacer, "unit": "Pa"},
        {"metric": "pressure_drop_spacer_per_m", "value": pressure_drop_spacer_per_m, "unit": "Pa/m"},

        {"metric": "jw_avg", "value": jw_avg, "unit": "m/s"},
        {"metric": "jw_max", "value": jw_max, "unit": "m/s"},
        {"metric": "jw_min", "value": jw_min, "unit": "m/s"},

        {"metric": "cm_avg", "value": cm_avg, "unit": "mol/m3"},
        {"metric": "cm_max", "value": cm_max, "unit": "mol/m3"},
        {"metric": "cm_min", "value": cm_min, "unit": "mol/m3"},

        {"metric": "lmh_udm_max", "value": lmh_udm_max, "unit": "LMH"},
        {"metric": "lmh_udm_min", "value": lmh_udm_min, "unit": "LMH"},

        {"metric": "cp_inlet_avg", "value": cp_inlet_avg, "unit": "-"},
        {"metric": "cp_inlet_max", "value": cp_inlet_max, "unit": "-"},
        {"metric": "cp_inlet_min", "value": cp_inlet_min, "unit": "-"},

        {"metric": "salt_flux_avg", "value": salt_flux_avg, "unit": "kg/m2/s"},
        {"metric": "salt_flux_max", "value": salt_flux_max, "unit": "kg/m2/s"},
        {"metric": "salt_flux_min", "value": salt_flux_min, "unit": "kg/m2/s"},

        {"metric": "wall_shear_avg", "value": wall_shear_avg, "unit": "Pa"},
        {"metric": "wall_shear_max", "value": wall_shear_max, "unit": "Pa"},
        {"metric": "wall_shear_min", "value": wall_shear_min, "unit": "Pa"},

        {"metric": "wall_shear_rate_avg", "value": wall_shear_rate_avg, "unit": "1/s"},
        {"metric": "wall_shear_rate_max", "value": wall_shear_rate_max, "unit": "1/s"},
        {"metric": "wall_shear_rate_min", "value": wall_shear_rate_min, "unit": "1/s"},

        {"metric": "water_sink_volume_integral_UDM1", "value": water_sink_volint, "unit": "kg/s"},
        {"metric": "salt_sink_volume_integral_UDM0", "value": salt_sink_volint, "unit": "kg/s"},
        {"metric": "total_sink_volume_integral_UDM2", "value": total_sink_volint, "unit": "kg/s"},
        {"metric": "mass_balance_error_boundary_minus_total_sink", "value": mass_balance_error, "unit": "kg/s"},
        {"metric": "mass_balance_relative_error", "value": mass_balance_relative_error, "unit": "-"},
    ]

    # ----------------------------------------------------------
    # Mass balance table
    # ----------------------------------------------------------

    mass_balance_rows = [
        {"metric": "m_in", "value": m_in, "unit": "kg/s"},
        {"metric": "m_out", "value": m_out, "unit": "kg/s"},
        {"metric": "boundary_permeate_mass_flow_abs_m_in_plus_m_out", "value": boundary_permeate_mass_flow, "unit": "kg/s"},
        {"metric": "water_sink_volume_integral_UDM1", "value": water_sink_volint, "unit": "kg/s"},
        {"metric": "salt_sink_volume_integral_UDM0", "value": salt_sink_volint, "unit": "kg/s"},
        {"metric": "total_sink_volume_integral_UDM2", "value": total_sink_volint, "unit": "kg/s"},
        {"metric": "mass_balance_error_boundary_minus_total_sink", "value": mass_balance_error, "unit": "kg/s"},
        {"metric": "mass_balance_relative_error", "value": mass_balance_relative_error, "unit": "-"},
    ]

    # ----------------------------------------------------------
    # Pressure table
    # ----------------------------------------------------------

    pressure_rows = [
        {"metric": "p_in_avg", "value": p_in_avg, "unit": "Pa"},
        {"metric": "p_out_avg", "value": p_out_avg, "unit": "Pa"},
        {"metric": "pressure_drop", "value": pressure_drop, "unit": "Pa"},
        {"metric": "domain_length_m", "value": domain_length_m, "unit": "m"},
        {"metric": "pressure_drop_per_m", "value": pressure_drop_per_m, "unit": "Pa/m"},
        {"metric": "p_spacer_in_avg", "value": p_spacer_in_avg, "unit": "Pa"},
        {"metric": "p_spacer_out_avg", "value": p_spacer_out_avg, "unit": "Pa"},
        {"metric": "pressure_drop_spacer", "value": pressure_drop_spacer, "unit": "Pa"},
        {"metric": "spacer_length_m", "value": spacer_length_m, "unit": "m"},
        {"metric": "pressure_drop_spacer_per_m", "value": pressure_drop_spacer_per_m, "unit": "Pa/m"},
        {"metric": "expected_outlet_gauge_pressure", "value": expected_outlet_pressure, "unit": "Pa"},
    ]

    # ----------------------------------------------------------
    # Shear table
    # ----------------------------------------------------------

    shear_rows = [
        {"metric": "wall_shear_avg", "value": wall_shear_avg, "unit": "Pa"},
        {"metric": "wall_shear_max", "value": wall_shear_max, "unit": "Pa"},
        {"metric": "wall_shear_min", "value": wall_shear_min, "unit": "Pa"},
        {"metric": "mu", "value": mu, "unit": "Pa s"},
        {"metric": "wall_shear_rate_avg", "value": wall_shear_rate_avg, "unit": "1/s"},
        {"metric": "wall_shear_rate_max", "value": wall_shear_rate_max, "unit": "1/s"},
        {"metric": "wall_shear_rate_min", "value": wall_shear_rate_min, "unit": "1/s"},
    ]

    # ----------------------------------------------------------
    # Create DataFrames
    # ----------------------------------------------------------

    summary_df = pd.DataFrame(summary_rows)
    mass_balance_df = pd.DataFrame(mass_balance_rows)
    pressure_df = pd.DataFrame(pressure_rows)
    shear_df = pd.DataFrame(shear_rows)

    # ----------------------------------------------------------
    # Display
    # ----------------------------------------------------------

    print("Summary metrics:")
    display(summary_df)

    print("Mass balance:")
    display(mass_balance_df)

    print("Pressure report:")
    display(pressure_df)

    print("Wall shear report:")
    display(shear_df)

    # ==========================================================
    # Cell 10. Save CSV and raw report JSON
    # ==========================================================

    # ----------------------------------------------------------
    # Output file paths
    # ----------------------------------------------------------

    summary_csv_path = report_path / "summary_metrics.csv"
    summary_wide_csv_path = report_path / "summary_metrics_wide.csv"

    mass_balance_csv_path = report_path / "mass_balance.csv"
    pressure_csv_path = report_path / "pressure_report.csv"
    shear_csv_path = report_path / "wall_shear_report.csv"

    raw_report_json_path = report_path / "raw_report_values.json"

    # ----------------------------------------------------------
    # Save long-format CSV files
    # ----------------------------------------------------------

    summary_df.to_csv(summary_csv_path, index=False, encoding="utf-8-sig")
    mass_balance_df.to_csv(mass_balance_csv_path, index=False, encoding="utf-8-sig")
    pressure_df.to_csv(pressure_csv_path, index=False, encoding="utf-8-sig")
    shear_df.to_csv(shear_csv_path, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------
    # Save wide-format summary CSV
    # This is useful for batch comparison across multiple cases.
    # Each metric becomes one column.
    # ----------------------------------------------------------

    summary_wide_df = summary_df.pivot_table(
        index=None,
        columns="metric",
        values="value",
        aggfunc="first",
    ).reset_index(drop=True)

    # Keep geometry/case columns near the front if they exist.
    front_columns = ["geo_name", "case_name"]
    existing_front_columns = [col for col in front_columns if col in summary_wide_df.columns]
    other_columns = [col for col in summary_wide_df.columns if col not in existing_front_columns]
    summary_wide_df = summary_wide_df[existing_front_columns + other_columns]

    summary_wide_df.to_csv(summary_wide_csv_path, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------
    # Save raw report values for debugging/reproducibility
    # ----------------------------------------------------------

    raw_save_dict = {
        "case_info": {
            "geo_name": geo_name,
            "case_name": case_name,
            "case_path": str(case_path),
            "final_case_file": str(final_case_file),
            "final_data_file": str(final_data_file),
        },
        "computed_values": computed_values,
        "failed_report_specs": failed_report_specs,
        "derived_values": {
            "boundary_permeate_mass_flow": boundary_permeate_mass_flow,
            "mass_balance_error": mass_balance_error,
            "mass_balance_relative_error": mass_balance_relative_error,
            "lmh_difference_mass_balance_minus_udm": lmh_difference,
            "lmh_relative_difference": lmh_relative_difference,
            "domain_length_m": domain_length_m,
            "pressure_drop_per_m": pressure_drop_per_m,
            "spacer_x_in_m": spacer_x_in_m,
            "spacer_x_out_m": spacer_x_out_m,
            "spacer_length_m": spacer_length_m,
            "pressure_drop_spacer_per_m": pressure_drop_spacer_per_m,
            "wall_shear_rate_avg": wall_shear_rate_avg,
            "wall_shear_rate_max": wall_shear_rate_max,
            "wall_shear_rate_min": wall_shear_rate_min,
        },
        "raw_results": raw_results,
    }

    with open(raw_report_json_path, "w", encoding="utf-8") as f:
        json.dump(
            raw_save_dict,
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    # ----------------------------------------------------------
    # Print saved files
    # ----------------------------------------------------------

    print("Saved files:")
    print("  Summary metrics       :", summary_csv_path)
    print("  Summary metrics wide  :", summary_wide_csv_path)
    print("  Mass balance          :", mass_balance_csv_path)
    print("  Pressure report       :", pressure_csv_path)
    print("  Wall shear report     :", shear_csv_path)
    print("  Raw report JSON       :", raw_report_json_path)

    print("\nSummary wide table:")
    display(summary_wide_df)

except Exception as e:
    print("\n" + "=" * 72)
    print("ERROR: PyFluent post-processing report extraction failed.")
    print("=" * 72)
    print(e)
    print("=" * 72 + "\n")
    raise

finally:
    if solver is not None and transcript_is_running:
        try:
            solver.transcript.stop()
            transcript_is_running = False
        except Exception as cleanup_error:
            print(f"Warning: could not stop solver transcript during cleanup: {cleanup_error}")

    if meshing is not None:
        try:
            meshing.exit()
        except Exception as cleanup_error:
            print(f"Warning: could not exit meshing session during cleanup: {cleanup_error}")

    if solver is not None:
        try:
            solver.exit()
        except Exception as cleanup_error:
            print(f"Warning: could not exit solver session during cleanup: {cleanup_error}")

    try:
        os.chdir(original_working_directory)
        print(f"Working directory restored: {original_working_directory}")
    except Exception as cleanup_error:
        print(f"Warning: could not restore working directory during cleanup: {cleanup_error}")
