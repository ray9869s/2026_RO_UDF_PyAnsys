# ==========================================================
# run_config.py
# Common configuration for meshing and solver automation
# Location: configs/run_config.py
# ==========================================================

# Edit only this file when changing geometry/case/run parameters.
# meshing_code_*.py and solver_code_*.py should read values from this config.
#
# Environment variables used by meshing/solver workers:
#   PYFLUENT_PROJECT_ROOT      - override project_root path
#   PYFLUENT_RUN_CONFIG        - path to an alternate Python config module
#   PYFLUENT_RUN_OVERRIDES     - JSON object of per-run config overrides
#   PYFLUENT_SKIP_VALIDATION   - set to 1/true/yes to skip validate_for_* after overrides

import os
import re
import types

from ro.mesh_common import (
    make_canonical_mesh_case_name as _make_canonical_mesh_case_name,
)
from ro import paths as ro_paths
from ro.solver_common import require_run_id_matches_operating_point

REQUIRED = "===== Edit here ====="

# Batch/worker keys allowed even when a substituted config omits declarations.
RUN_CONFIG_OVERRIDE_EXTENSIONS = frozenset({
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "spacing_code",
    "attack_angle_deg",
    "filament_d_m",
    "bridge_radius_m",
    "overlap_m",
    "n_active_cells",
    "n_buffer_in",
    "n_buffer_out",
    "cell_length_x_m",
    "buffer_length_in_m",
    "buffer_length_out_m",
    "n_lead_excluded",
    "n_trail_excluded",
    "mesh_case_name",
    "run_label",
    "restart_from_case_file",
    "restart_from_data_file",
})


# ==========================================================
# [1] Common project/case settings
# ==========================================================

# Frozen config value retained for override compatibility. Worker data locations
# come from RO_DATA_ROOT builders and do not read this value.
project_root = str(ro_paths.project_root())

family = REQUIRED
geo_id = REQUIRED
mesh_id = REQUIRED
run_id = REQUIRED
geo_name = REQUIRED
case_name = REQUIRED

# Explicit mesh-manifest geometry and layout metadata. These values are never
# inferred from family, ids, or legacy names.
spacing_code = REQUIRED
attack_angle_deg = REQUIRED
filament_d_m = REQUIRED
bridge_radius_m = REQUIRED
overlap_m = REQUIRED
n_active_cells = REQUIRED
n_buffer_in = REQUIRED
n_buffer_out = REQUIRED
cell_length_x_m = REQUIRED
buffer_length_in_m = REQUIRED
buffer_length_out_m = REQUIRED
n_lead_excluded = REQUIRED
n_trail_excluded = REQUIRED


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

# When False, spacer walls keep local proximity sizing but do not receive
# boundary layers. Default True preserves current BL face-label composition.
include_spacer_in_boundary_layers = True

# Fluent Meshing boundary-layer offset method.
# Common options:
#   "smooth-transition"
#   "uniform"
bl_offset_method = "smooth-transition"
bl_growth_rate = 1.2

# Volume mesh controls
vol_hex_max_factor = 0.7
# peel_layers is NOT a near-wall knob. It only changes the interior
# prism-to-hexcore transition. Measured on D2450_a45_7c_brg110 with
# bl_layers 4, bl_height_factor 0.4:
#   peel_layers 2 -> 796,009 cells, 749 inlet faces,
#                    skewness 0.67063399, min orthogonal 0.102087
#   peel_layers 0 -> 987,599 cells, 749 inlet faces,
#                    skewness 0.67063399, min orthogonal 0.102087
# Identical inlet-face count and quality to eight significant figures;
# 24% more cells. Do not retune peel_layers to refine the membrane BL.
# bl_layers and bl_height DO change near-wall resolution (749 -> 914
# inlet faces from bl4 to bl6), but window CP was still not grid-converged
# between those two (1.039 vs 1.051). That is why 260813 measures y1 and
# optionally reconstructs c_wall.
# Measured first-cell centroid distance y1 at the membrane wall on
# D2450_a45_7c_brg110 (260813 first-ADJUST diagnostic, area-weighted):
#   bl4  mean 5.736 um  min 0.9652  max 16.14
#   bl6  mean 3.985 um  min 0.5629  max 6.19
# NOT the 1.2 / 0.6 um that bl_height/2 would imply: the prism layer at
# the membrane wall is 5-7x thicker than bl_height specifies.
peel_layers = 2

# Mesh quality gate
min_orthogonal_quality_threshold = 0.05
# Boundary-layer meshes are anisotropic by design. The first prism layer is
# 2.4 um against an 85 um lateral size, so a geometric aspect ratio around 35
# is intended; Fluent's reported aspect ratio is not a simple edge ratio
# (a perfect cube reports about 1.732). Measured maxima so far: 62.8, 64.8,
# 83.3, 84.8. The a30 geometries have the widest periodic span and may
# exceed 100. 150 is a campaign ceiling, not a quality target.
max_aspect_ratio_threshold = 150.0
# Surface max skewness. Ansys guidance is that a surface mesh with maximum
# skewness below 0.7 tends to produce a good volume mesh. Our two points
# are consistent with that:
#   D2450_a45  surface max skew 0.6706  ->  volume min ortho 0.1021
#   D0817_a60  surface max skew 0.8678  ->  volume min ortho 0.0664
# Keep this surface gate even though the solver sees the volume mesh.
# Measured Diamond-family meshes are ~0.64–0.67; 0.85 sits above Fluent's
# skewed-cell highlight band (0.80) with campaign headroom, while still
# rejecting genuinely bad surfaces near 0.90+.
max_skewness_threshold = 0.85
# Fraction of surface faces above Fluent's fixed 0.80 skewness highlight.
# Both this and max_skewness_threshold must pass. Calibration from the
# rebuild, including incomplete D2450_a45_7c_test meshes that never
# produced volume metrics:
#   passing, worst   9.25e-06  D0817_a60_15c_brg156
#   failed, cpg7     8.22e-05  max skew 0.9996
#   failed, cpg5     1.32e-04  max skew 0.9966
#   failed, cpg3     2.33e-04  max skew 0.9995
# 3e-5 sits in that 9x gap: 3.2x headroom over the worst passing mesh,
# 2.7x margin below the best failing one. 1e-4 would let cpg7 pass the
# fraction gate outright.
skewed_face_fraction_threshold = 3.0e-5
fail_if_quality_not_parsed = False

# When True: surface mesh first (no shadow-copy constraint), then
# Set Up Periodic Boundaries with Automatic + both labels.
# D0817_a45 surface max skewness was 0.956 with False (Manual-before-surface)
# and 0.743 with True. The 21c diagnostic that completed a volume mesh at
# this 1.155 mm pitch used True. D2450_a45 stays on its existing False
# mesh (hashed; runs cite mesh_sha256). The ledger column records which
# path produced each mesh.
periodic_after_surface_mesh = True

# Checkpoint option
save_surface_mesh_checkpoint = True
allow_legacy_mesh_case_name_mismatch = False


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
# 260822 = production. ASCII, RO_ANALYTIC_CWALL=1, FACE diagnostics off,
# y1 on the cell path, RO_UDF_INLET_PROFILE_G marker from ensure_inlet_G.
# 260816 = ASCII-only copy of 260815; frozen after the Windows compile fix.
# 260815 = same UDF behavior, retained as the UTF-8 regression reference.
# 260814 = reconstruction ON, FACE still on (results sibling).
# 260813 = cell-centre (flag off) sibling.
udf_source_file_name = "260822_RO_UDF.c"
udf_library_name = "libudf"

# Inlet velocity profile (DEFINE_PROFILE inlet_x_velocity_profile).
# False = legacy plug inlet via velocity_magnitude (default, regression-safe).
# True  = Components + UDF on x-velocity (needs-live-verification on Fluent 25.1).
use_inlet_velocity_profile = False
# After UDF load, execute probe_inlet_profile on-demand and assert its marker
# in the solver transcript. The solver always runs the probe so a False
# value cannot silently skip RO_UDF_INLET_PROFILE_G; the flag is retained
# for config compatibility.
run_inlet_profile_probe = False
# Verbose settings-API probes + allowed-value trials when applying the
# Components+UDF inlet BC. Default False after Fluent 25.1.0 confirmed
# set_state("Components") activates velocity_components; set True to re-probe.
debug_inlet_bc_api = False

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
relaxation_profile = "baseline"
# Orthogonal to relaxation_profile: do not redefine conservative/strong.
# "preserve" leaves the expert implicit species URF untouched.
species_implicit_under_relaxation = "preserve"
# Orthogonal observability control. "preserve" leaves Fluent's verbosity
# unchanged (typically 0). Values 1/2 print pseudo-time step details per UG.
pseudo_time_verbosity = "preserve"
# Coupled GTS automatic scale factor. "preserve" leaves Fluent unchanged.
# A positive number is the D0817_a30 u0p3_p6M_ptgts3 pilot only.
pseudo_time_time_step_size_scale_factor = "preserve"

# Ramp/convergence safety.
# 260612_RO_UDF.c uses a source ramp that reaches full strength after 150 iterations.
use_ramp_convergence_safety = True
ramp_full_iteration = 150
post_ramp_buffer_iterations = 50

# QoI-based convergence stop (Fluent monitor.convergence_conditions).
# condition = all-conditions-are-met; residual check_convergence stays True
# (1e-7). UG 37.18 All includes enabled residuals, so the live stop is
# LMH AND spacer dP AND residuals — not (LMH AND dP) OR residual, which
# Fluent cannot express. Both report conditions use the same relative
# window (UG 37.18: max_k |m(n)-m(n-k)|/|m(n)| < stop_criterion). 1e-3
# is 0.1% of the current value for LMH and for dP in Pa; an absolute Pa
# threshold is not available on this condition. QoI is inactive during
# the 200-iteration ramp. Earlier bl6 / u=0.2 evidence that AND-ing dP
# bought nothing is superseded: u=0.3 D2450_a45 stopped at the first
# legal LMH window (iter 301) while continuity was still 6.4e-3.
enable_qoi_convergence_stop = True
qoi_convergence_report_name = "lmh_udm_avg"
qoi_stop_criterion = 1e-3
qoi_previous_values_to_consider = 100
qoi_initial_values_to_ignore = 200
# Write per-iteration history (Fluent report files).
enable_lmh_udm_avg_report_file = True
lmh_udm_avg_report_file_name = "lmh_udm_avg.out"
enable_pressure_drop_spacer_report_file = True
pressure_drop_spacer_report_file_name = "pressure_drop_spacer.out"

# Report definitions
update_rho_avg_report_definition = True
rho_avg_report_name = "rho_avg"
rho_avg_report_field = "density"
area_mem_report_name = "area_mem"
lmh_report_name = "lmh"
lmh_signed_report_name = "lmh_signed"
m_in_report_name = "m_in"
m_out_report_name = "m_out"
enable_solve_time_qoi_reports = True
domain_x_min_m = 0.0
domain_length_m = 0.017325
buffer_length_m = 0.003465


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


_SKIP_VALIDATION_TRUTHY = frozenset({"1", "true", "yes"})


def run_config_validation_skipped():
    """Return True when PYFLUENT_SKIP_VALIDATION opts out of validate_for_* calls."""
    value = os.environ.get("PYFLUENT_SKIP_VALIDATION", "").strip().lower()
    return value in _SKIP_VALIDATION_TRUTHY


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


def _require_exact_zero_number(name, value):
    """Raise ValueError when a config value is not exactly zero."""
    _require_set(name, value)

    if isinstance(value, bool):
        raise TypeError(
            f"run_config.py value must be numeric, not bool: {name}={value!r}"
        )

    if not isinstance(value, (int, float)):
        raise TypeError(
            f"run_config.py value must be numeric: {name}={value!r}"
        )

    if value != 0:
        raise ValueError(
            f"run_config.py empty-family value must be exactly 0.0: "
            f"{name}={value!r}"
        )


def _require_positive_float(name, value):
    """Raise when a config value is not a positive float (numeric or numeric string)."""
    _require_set(name, value)

    if isinstance(value, bool):
        raise TypeError(
            f"run_config.py value must be numeric, not bool: {name}={value!r}"
        )

    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "run_config.py value must be convertible to float: "
                f"{name}={value!r}"
            ) from exc
    else:
        raise TypeError(
            "run_config.py value must be numeric or a numeric string: "
            f"{name}={value!r}"
        )

    if numeric <= 0:
        raise ValueError(
            f"run_config.py value must be positive: {name}={value!r}"
        )


def _require_choice(name, value, choices):
    """Raise when a config value is not one of the allowed strings."""
    _require_set(name, value)
    if value not in choices:
        raise ValueError(
            f"run_config.py value must be one of {sorted(choices)}: "
            f"{name}={value!r}"
        )


def _require_preserve_or_positive_number(name, value):
    """Raise unless value is the literal 'preserve' or a positive number."""
    _require_set(name, value)
    if isinstance(value, str) and value.strip().lower() == "preserve":
        return
    _require_positive_number(name, value)


def _require_preserve_or_verbosity(name, value):
    """Raise unless value is 'preserve' or an integer verbosity in {0, 1, 2}."""
    _require_set(name, value)
    if isinstance(value, str) and value.strip().lower() == "preserve":
        return
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(
            "run_config.py value must be 'preserve' or an integer 0/1/2: "
            f"{name}={value!r}"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "run_config.py value must be 'preserve' or an integer 0/1/2: "
            f"{name}={value!r}"
        ) from exc
    if float(value) != float(parsed) or parsed not in {0, 1, 2}:
        raise ValueError(
            "run_config.py value must be 'preserve' or an integer in "
            f"{{0, 1, 2}}: {name}={value!r}"
        )


def _require_bool(name, value):
    """Raise when a config value is not a bool."""
    _require_set(name, value)
    if not isinstance(value, bool):
        raise TypeError(
            f"run_config.py value must be bool: {name}={value!r}"
        )


def _require_number(name, value):
    """Raise when a config value is not numeric."""
    _require_set(name, value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"run_config.py value must be numeric: {name}={value!r}"
        )


def _require_integer(name, value, *, minimum):
    """Raise when a config value is not an integer at or above minimum."""
    _require_set(name, value)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TypeError(
            f"run_config.py value must be an integer >= {minimum}: "
            f"{name}={value!r}"
        )


def validate_common():
    """Validate settings shared by meshing and solver scripts."""
    _require_set("project_root", project_root)
    _require_set("family", family)
    _require_set("geo_id", geo_id)
    if not isinstance(family, str) or ro_paths.FAMILY_RE.fullmatch(family) is None:
        raise ValueError(f"run_config.py family is invalid: {family!r}")
    if (
        not isinstance(geo_id, str)
        or ro_paths.GEO_ID_RE.fullmatch(geo_id) is None
        or ro_paths._GEO_ID_FORBIDDEN.search(geo_id) is not None
    ):
        raise ValueError(f"run_config.py geo_id is invalid: {geo_id!r}")
    _require_set("geo_name", geo_name)
    _require_set("case_name", case_name)
    _require_positive_number("processor_count", processor_count)


def validate_for_meshing():
    """Validate settings required by meshing automation."""
    validate_common()
    _require_set("mesh_id", mesh_id)
    if not isinstance(mesh_id, str) or ro_paths.MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"run_config.py mesh_id is invalid: {mesh_id!r}")
    _require_set("spacing_code", spacing_code)
    if not isinstance(spacing_code, str) or not spacing_code.strip():
        raise ValueError(
            f"run_config.py spacing_code must be a non-empty string: "
            f"{spacing_code!r}"
        )
    _require_number("attack_angle_deg", attack_angle_deg)
    if family == "empty":
        _require_exact_zero_number("filament_d_m", filament_d_m)
        _require_exact_zero_number("bridge_radius_m", bridge_radius_m)
        _require_exact_zero_number("overlap_m", overlap_m)
        from ro.campaign_geometry import geometry_parameters_for_geo_id

        geometry = geometry_parameters_for_geo_id(geo_id)
        _require_exact_zero_number(
            "membrane_trim_m", geometry["membrane_trim_m"]
        )
        _require_exact_zero_number(
            "membrane_contact_width_m",
            geometry["membrane_contact_width_m"],
        )
    else:
        _require_positive_number("filament_d_m", filament_d_m)
        _require_nonnegative_number("bridge_radius_m", bridge_radius_m)
        _require_nonnegative_number("overlap_m", overlap_m)
    _require_integer("n_active_cells", n_active_cells, minimum=1)
    _require_integer("n_buffer_in", n_buffer_in, minimum=1)
    _require_integer("n_buffer_out", n_buffer_out, minimum=1)
    _require_positive_number("cell_length_x_m", cell_length_x_m)
    _require_positive_number("buffer_length_in_m", buffer_length_in_m)
    _require_positive_number("buffer_length_out_m", buffer_length_out_m)
    _require_integer("n_lead_excluded", n_lead_excluded, minimum=0)
    _require_integer("n_trail_excluded", n_trail_excluded, minimum=0)
    if n_lead_excluded + n_trail_excluded >= n_active_cells:
        raise ValueError(
            "run_config.py lead/trail exclusions must leave an active cell. "
            f"n_active_cells={n_active_cells!r}, "
            f"n_lead_excluded={n_lead_excluded!r}, "
            f"n_trail_excluded={n_trail_excluded!r}"
        )

    _require_positive_number("m_max", m_max)
    _require_positive_number("m_min", m_min)
    _require_positive_number("m_cpg", m_cpg)
    _require_positive_number("bl_layers", bl_layers)
    _require_integer("peel_layers", peel_layers, minimum=0)
    if not mesh_id.endswith(f"_peel{peel_layers}"):
        raise ValueError(
            "run_config.py mesh_id peel token must match peel_layers. "
            f"mesh_id={mesh_id!r}, peel_layers={peel_layers!r}"
        )

    if m_min > m_max:
        raise ValueError(
            "run_config.py m_min must be <= m_max. "
            f"m_min={m_min!r}, m_max={m_max!r}"
        )

    _require_bool(
        "allow_legacy_mesh_case_name_mismatch",
        allow_legacy_mesh_case_name_mismatch,
    )
    mesh_stem = _make_canonical_mesh_case_name(
        m_max,
        m_min,
        m_cpg,
        bl_layers,
    ).removeprefix("mesh_")
    # Optional _fNNN (bl_height_factor ×1000 token) between bl and peel.
    expected_mesh_id = re.compile(
        rf"^{re.escape(mesh_stem)}(?:_f\d{{3}})?_peel{int(peel_layers)}$"
    )
    if expected_mesh_id.fullmatch(mesh_id) is None:
        raise ValueError(
            "run_config.py mesh_id does not match the supplied mesh parameters: "
            f"mesh_id={mesh_id!r}, expected {mesh_stem}_peel{peel_layers} "
            f"or {mesh_stem}_fNNN_peel{peel_layers}."
        )

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
    _require_set("mesh_id", mesh_id)
    _require_set("run_id", run_id)
    if not isinstance(mesh_id, str) or ro_paths.MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"run_config.py mesh_id is invalid: {mesh_id!r}")
    if not isinstance(run_id, str) or ro_paths.RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"run_config.py run_id is invalid: {run_id!r}")

    _require_positive_float("inlet_velocity_value", inlet_velocity_value)

    _require_positive_number("operating_pressure", operating_pressure)
    _require_nonnegative_number("outlet_gauge_pressure", outlet_gauge_pressure)
    require_run_id_matches_operating_point(
        run_id,
        inlet_velocity_value,
        outlet_gauge_pressure,
    )

    _require_set("template_case_file_name", template_case_file_name)
    _require_set("udf_source_file_name", udf_source_file_name)
    _require_set("udf_library_name", udf_library_name)
    _require_bool("use_inlet_velocity_profile", use_inlet_velocity_profile)
    _require_bool("run_inlet_profile_probe", run_inlet_profile_probe)
    _require_bool("debug_inlet_bc_api", debug_inlet_bc_api)

    _require_set("membrane_wall_base_names", membrane_wall_base_names)
    _require_set("buffer_wall_base_names", buffer_wall_base_names)

    _require_set("target_species_name", target_species_name)
    _require_positive_number("salt_mass_fraction", salt_mass_fraction)
    if salt_mass_fraction > 1.0:
        raise ValueError(
            "run_config.py value must be <= 1.0: "
            f"salt_mass_fraction={salt_mass_fraction!r}"
        )
    _require_positive_number("salt_density", salt_density)
    _require_positive_number("salt_viscosity", salt_viscosity)
    _require_positive_number("salt_molecular_weight", salt_molecular_weight)
    _require_positive_number("mixture_density", mixture_density)
    _require_positive_number("mixture_viscosity", mixture_viscosity)
    _require_positive_number("mass_diffusivity", mass_diffusivity)

    _require_positive_number("udm_count", udm_count)
    _require_positive_number("residual_target", residual_target)
    _require_positive_number("max_iterations", max_iterations)
    _require_choice(
        "relaxation_profile",
        relaxation_profile,
        {"baseline", "conservative", "strong"},
    )
    _require_preserve_or_positive_number(
        "species_implicit_under_relaxation",
        species_implicit_under_relaxation,
    )
    _require_preserve_or_verbosity(
        "pseudo_time_verbosity",
        pseudo_time_verbosity,
    )
    _require_preserve_or_positive_number(
        "pseudo_time_time_step_size_scale_factor",
        pseudo_time_time_step_size_scale_factor,
    )
    _require_bool("use_ramp_convergence_safety", use_ramp_convergence_safety)
    _require_positive_number("ramp_full_iteration", ramp_full_iteration)
    _require_nonnegative_number(
        "post_ramp_buffer_iterations",
        post_ramp_buffer_iterations,
    )
    _require_bool("enable_qoi_convergence_stop", enable_qoi_convergence_stop)
    if enable_qoi_convergence_stop:
        if not enable_solve_time_qoi_reports:
            raise ValueError(
                "enable_qoi_convergence_stop requires enable_solve_time_qoi_reports=True"
            )
        _require_set("qoi_convergence_report_name", qoi_convergence_report_name)
        _require_positive_float("qoi_stop_criterion", qoi_stop_criterion)
        _require_positive_number(
            "qoi_previous_values_to_consider",
            qoi_previous_values_to_consider,
        )
        _require_nonnegative_number(
            "qoi_initial_values_to_ignore",
            qoi_initial_values_to_ignore,
        )
    _require_bool(
        "enable_lmh_udm_avg_report_file",
        enable_lmh_udm_avg_report_file,
    )
    if enable_lmh_udm_avg_report_file:
        _require_set("lmh_udm_avg_report_file_name", lmh_udm_avg_report_file_name)
    _require_bool(
        "enable_pressure_drop_spacer_report_file",
        enable_pressure_drop_spacer_report_file,
    )
    if enable_pressure_drop_spacer_report_file:
        _require_set(
            "pressure_drop_spacer_report_file_name",
            pressure_drop_spacer_report_file_name,
        )
    _require_bool(
        "enable_solve_time_qoi_reports",
        enable_solve_time_qoi_reports,
    )
    if enable_solve_time_qoi_reports:
        _require_number("domain_x_min_m", domain_x_min_m)
        _require_positive_number("domain_length_m", domain_length_m)
        _require_nonnegative_number("buffer_length_m", buffer_length_m)
        if domain_length_m - 2.0 * buffer_length_m <= 0.0:
            raise ValueError(
                "run_config.py spacer length must be positive: "
                f"domain_length_m={domain_length_m!r}, "
                f"buffer_length_m={buffer_length_m!r}"
            )
