import types

from ro.paths import project_root as _discover_project_root

# Batch/worker keys not declared as module-level settings in this file.
POST_CONFIG_OVERRIDE_EXTENSIONS = frozenset({
    "final_case_file",
    "final_data_file",
    "inlet_velocity_value",
    "outlet_gauge_pressure",
    "channel_height_m",
    "results_dir",
    "case_path",
})


def _is_blocked_override_target(value):
    """Return True when an existing module attribute must not be overwritten."""
    return callable(value) or isinstance(value, types.ModuleType)


def post_config_override_keys(cfg_module):
    """Return override keys allowed for post_config-style modules."""
    keys = set(POST_CONFIG_OVERRIDE_EXTENSIONS)
    for name, value in vars(cfg_module).items():
        if name.startswith("_"):
            continue
        if _is_blocked_override_target(value):
            continue
        keys.add(name)
    return frozenset(keys)


def apply_post_config_overrides(cfg_module, overrides):
    """Apply JSON override dict to a loaded post config module.

    Rejects unknown keys and refuses to overwrite callables or imported modules.
    """
    if not isinstance(overrides, dict):
        raise TypeError(
            "post config overrides must be a dict, "
            f"got {type(overrides).__name__}"
        )

    allowed = post_config_override_keys(cfg_module)
    unknown = sorted(set(overrides) - allowed)
    if unknown:
        raise ValueError(
            "Unknown post config override key(s): "
            + ", ".join(repr(key) for key in unknown)
        )

    for key, value in overrides.items():
        existing = getattr(cfg_module, key, None)
        if _is_blocked_override_target(existing):
            raise TypeError(
                f"Cannot override non-config attribute: {key!r}"
            )
        setattr(cfg_module, key, value)

# Repo root via PYFLUENT_PROJECT_ROOT or pyproject.toml walk. Do not recover
# this from a fixed __file__ depth — that depth is wrong after reshuffle.
project_root = str(_discover_project_root())

REQUIRED = "===== Edit here ====="

geo_name = REQUIRED
case_name = REQUIRED
results_dir = None
case_path = None

rho = 998.2
mu = 8.93e-4
c_inlet_ref = 597.8268309
salt_molecular_weight_kg_per_mol = 0.05844
salt_permeability_m_per_s = 2.50e-8

active_membrane_base_names = ["wall_top_mem", "wall_bottom_mem"]
buffer_wall_base_names = ["wall_top_buffer", "wall_bottom_buffer"]

# Wire post_config indices from the canonical layout module when available.
# Fallback literals keep the file loadable if imported before sys.path is set.
try:
    from ro.udm_layout import UDM_INDEX_BY_ROLE as udm_indices  # type: ignore
except ImportError:
    udm_indices = {
        "salt_mass_source": 0,
        "total_mass_source": 1,
        "jw": 6,
        "cm": 7,
        "lmh": 8,
        "cp": 9,
        "cell_strain_rate": 5,
        "membrane_area_acc": 11,
        "salt_mass_flux": 10,
        "wall_centroid_distance": 12,
    }

product_version = "25.1.0"
processor_count = 1
graphics_driver = "dx11"

fluent_start_timeout = 300
fluent_health_timeout = 300

domain_x_min_m = 0.0
# Asymmetric layout keys (authoritative for 01_pyfluent_report_extract).
# Default matches the legacy 1+3+1 generation; batch overrides replace these.
n_buffer_in = 1
n_active = 3
n_buffer_out = 1
cell_length_x_m = 0.003465
# Legacy length/count keys — must stay consistent with the asymmetric keys above.
# For asymmetric (n_buffer_in != n_buffer_out) overrides, n_buffer_cells_each_end
# is cleared to None so it cannot contradict the layout.
domain_length_m = 0.017325
buffer_length_m = 0.003465
n_unit_cells = 5
n_buffer_cells_each_end = 1
n_inlet_spacer_cells_excluded = 1
# Provenance keys written by 01_batch_report_extract / 06 layout resolution.
mesh_case_name = None
mesh_resolution_source = None
salt_mass_fraction_upper_threshold = 0.99
salt_mass_fraction_lower_threshold = 1.0e-6
