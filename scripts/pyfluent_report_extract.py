# ==========================================================
# Cell 1. Import packages and load config
# ==========================================================

import os
import re
import json
import math
import time
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
# Default config lives under <project>/configs/.
# Override with the PYFLUENT_POST_CONFIG environment variable for batch runs.
# ----------------------------------------------------------
from ro.paths import project_root  # noqa: E402
from ro.campaign_geometry import CAMPAIGN_H_M  # noqa: E402
from ro.lmh_metrics import MS_TO_LMH, lmh_mass_balance_expression
from ro.convergence_quality import (
    continuity_final_from_case_dir,
    evaluate_convergence_quality,
    manifest_quality_payload,
)
from ro.manifest import (  # noqa: E402
    read_run_manifest,
    sync_run_manifest_analytic_cwall,
    update_run_manifest_fields,
)
from ro.extract_profile import (  # noqa: E402
    PROFILE_MFBO,
    filter_mfbo_summary_rows,
    mfbo_mixing_cup_boundary_indices,
    mfbo_pressure_boundary_indices,
    mfbo_required_report_names,
    parse_extract_options,
    parse_extract_profile,
    require_mfbo_summary_columns,
)
from ro.evaluation_window_table import (  # noqa: E402
    apply_selection_to_config,
    per_cell_flux_and_area,
    select_evaluation_window,
    window_lmh,
)
from ro.extract_skip import write_extract_source_record  # noqa: E402
from ro.solver_common import (  # noqa: E402
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    sha256_file,
)

DEFAULT_CONFIG_PATH = project_root() / "configs" / "post_config.py"
CONFIG_PATH = Path(os.environ.get("PYFLUENT_POST_CONFIG", str(DEFAULT_CONFIG_PATH)))

from ro.fluent_report_helpers import (  # noqa: E402
    concentration_range_diagnostics,
    concentration_metric_unit,
    MIDPLANE_CB_MIXING_CUP_REL_TOL,
    assert_midplane_c_b_matches_boundary_mixing_cup,
    apply_surface_report_definition,
    create_channel_midplane_plane,
    create_x_normal_plane as _create_x_normal_plane,
    create_x_range_iso_clip,
    delete_iso_clip,
    derive_periodic_spacer_pressure_metrics_for_layout,
    derive_spacer_cell_metrics_for_layout,
    evaluation_window_midplane_bulk_concentrations,
    exception_details,
    compute_flux_massflow_boundary_report,
    FLUX_BOUNDARY_MASSFLOW_REPORTS,
    LOAD_BEARING_REPORT_NAMES,
    LoadBearingReportComputeError,
    fluid_zone_reduction_locations,
    measure_active_window_geometry,
    mass_fraction_to_molar_concentration,
    midplane_window_bulk_aggregate,
    require_canonical_cp_summary_columns,
    require_extract_identities,
    require_load_bearing_report_computes,
    require_load_bearing_report_definitions,
    require_load_bearing_summary_columns,
    segmented_membrane_cp_metrics,
    molar_concentration_to_mass_fraction,
    resolve_evaluation_window_from_config,
    resolve_scoring_layout_from_config,
    summary_rows_to_wide_record,
    null_turbulence_metrics,
    require_turbulence_scalar_fields,
    scalar_field_names,
    turbulence_extract_plan,
    turbulence_metrics_from_reductions,
    turbulence_summary_rows,
    RANS_VOLUME_REDUCTIONS,
    unit_cell_areaavg_molar_concentration_name,
    unit_cell_concentration_report_name,
    unit_cell_mixing_cup_report_name,
    unit_cell_mixing_cup_molar_concentration_name,
    unit_cell_mixing_cup_report_spec,
    unit_cell_plane_name,
    unit_cell_plane_area_report_name,
    unit_cell_pressure_report_name,
    udm_area_sum_report_spec,
)
from ro.cp_concentration_stats import (  # noqa: E402
    B_PERM_M_PER_S,
    cell_x_bounds,
    concentration_cp_report,
    concentration_cp_summary_rows,
    polygon_area,
    window_x_bounds,
)
from ro.membrane_y1 import (  # noqa: E402
    y1_window_resolution,
    y1_window_summary_rows,
)
from ro.udm_layout import (  # noqa: E402
    FIELD_UDM_CELL_STRAIN_RATE,
    FIELD_UDM_CM,
    FIELD_UDM_CP_INLET,
    FIELD_UDM_JW,
    FIELD_UDM_LMH,
    FIELD_UDM_MEMBRANE_AREA_ACC,
    FIELD_UDM_SALT_FLUX,
    FIELD_UDM_SI,
    FIELD_UDM_TOTAL_S,
    FIELD_UDM_Y1,
)

REPORT_EXTRACT_TIMING_BUCKET_BOUNDARIES = {
    "fluent_launch": (
        "Cell 4: pyfluent.launch_fluent through switch_to_solver "
        "(excludes read_case_data)."
    ),
    "read_case_data": (
        "Cell 4: solver.settings.file.read_case_data only."
    ),
    "cell_7_report_creation": (
        "Cell 7: create report definitions and Fluent surfaces "
        "(includes volume-integral report creation, not compute)."
    ),
    "cell_8_compute": (
        "Cell 8: compute_one_report loop for all Cell-7 definitions "
        "(includes volume-integral computes)."
    ),
        "active_window_geometry": (
        "Active window, both profiles: one surface-area integral of the "
        "membrane walls clipped to the active x-span, one surface-area "
        "integral of wall_spacer_* in that span (area 0 and no integral "
        "when there are no spacer walls), and one volume-zonevol report "
        "per fluid zone. Active fluid volume is that zone total minus the "
        "exact buffer boxes. Porosity is Python: fluid volume divided by "
        "the layout box."
    ),
    "cell_8_25_salt_reduction": (
        "Cell 8.25: salt mass-fraction range diagnostics "
        "(solver.fields.reduction on fluid zones)."
    ),
    "cell_8_4_midplane_cb": (
        "Cell 8.4 and 8.4b: mid-plane c_b clips, boundary cross-check, "
        "and legacy whole-domain center-plane average."
    ),
    "segmented_membrane_cp": (
        "Segmented membrane CP metrics (segmented_membrane_cp_metrics)."
    ),
    "csv_write": (
        "Cell 8.5 through Cell 10: center-plane unit conversion, Python "
        "summary assembly, and CSV/JSON writes."
    ),
}

_extract_phase_seconds: dict[str, float] = {}
_segmented_membrane_cp_subphase_seconds: dict[str, float] = {}
_completed_extract_phases: list[str] = []
_active_extract_phase: str | None = None
_active_extract_phase_start: float | None = None
_failed_extract_phase: str | None = None
_extract_timing_started_at: float | None = None


def _reset_extract_timing() -> None:
    global _active_extract_phase, _active_extract_phase_start
    global _failed_extract_phase, _extract_timing_started_at
    _extract_phase_seconds.clear()
    _segmented_membrane_cp_subphase_seconds.clear()
    _completed_extract_phases.clear()
    _active_extract_phase = None
    _active_extract_phase_start = None
    _failed_extract_phase = None
    _extract_timing_started_at = time.monotonic()


def _begin_extract_phase(name: str) -> None:
    global _active_extract_phase, _active_extract_phase_start
    if _active_extract_phase is not None:
        _end_extract_phase()
    _active_extract_phase = name
    _active_extract_phase_start = time.monotonic()


def _end_extract_phase(*, completed: bool = True) -> None:
    global _active_extract_phase, _active_extract_phase_start
    global _failed_extract_phase
    if _active_extract_phase is None or _active_extract_phase_start is None:
        return
    name = _active_extract_phase
    _extract_phase_seconds[name] = time.monotonic() - _active_extract_phase_start
    if completed:
        _completed_extract_phases.append(name)
    else:
        _failed_extract_phase = name
    _active_extract_phase = None
    _active_extract_phase_start = None


def _abandon_active_extract_phase() -> None:
    _end_extract_phase(completed=False)


def stamp_extraction_wall_time_on_run_manifest(run_directory, wall_time_s):
    """Record extraction wall time on the run manifest.

    ``wall_time_s`` is ``report_extract_timing.json`` ``total_seconds``:
    ``_reset_extract_timing`` starts that clock before the Fluent launch
    phase, and the timing write stops it after the reports. Older runs
    stay without the field.
    """
    if isinstance(wall_time_s, bool) or not isinstance(wall_time_s, (int, float)):
        raise TypeError(
            f"extraction wall time must be a number, got {wall_time_s!r}."
        )
    if not math.isfinite(float(wall_time_s)) or float(wall_time_s) < 0.0:
        raise ValueError(
            f"extraction wall time must be finite and >= 0, got {wall_time_s!r}."
        )
    return update_run_manifest_fields(
        run_directory,
        {"extraction_wall_time_s": float(wall_time_s)},
    )


_extract_profile_name = "full"
_legacy_3_cell_window = False
_evaluation_window_selection = None


def _write_report_extract_timing(report_path: Path):
    if _active_extract_phase is not None and _failed_extract_phase is None:
        _end_extract_phase()
    timing_path = report_path / "report_extract_timing.json"
    total_seconds = None
    if _extract_timing_started_at is not None:
        total_seconds = time.monotonic() - _extract_timing_started_at
    payload = {
        "bucket_boundaries": REPORT_EXTRACT_TIMING_BUCKET_BOUNDARIES,
        "phases_seconds": dict(_extract_phase_seconds),
        "completed_phases": list(_completed_extract_phases),
        "failed_phase": _failed_extract_phase,
        "segmented_membrane_cp_subphases_seconds": dict(
            _segmented_membrane_cp_subphase_seconds
        ),
        "total_seconds": total_seconds,
    }
    if _extract_profile_name == PROFILE_MFBO:
        payload["profile"] = PROFILE_MFBO
    with timing_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"Report extract timing JSON: {timing_path}")
    return total_seconds


def load_python_config(config_path):
    """Load a Python config file whose filename may start with a number."""
    config_path = Path(config_path)

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def resolve_report_case_paths(cfg):
    """Locate the case directory without a 03_Results fallback.

    Preference: cfg.case_path, then cfg.results_dir/<geo>/<case>, then the
    parent of cfg.final_case_file. Missing all three is an error — joining
    project_root/03_Results would silently miss the data tree.

    ``final_case_file`` and ``final_data_file`` are always resolved under
    ``case_path`` when given as bare filenames (batch overrides often supply
    only the leaf name). Absolute paths are preserved.
    """
    geo_name = cfg.geo_name
    case_name = cfg.case_name
    configured_case_path = getattr(cfg, "case_path", None)
    results_dir = getattr(cfg, "results_dir", None)
    configured_final_case = getattr(cfg, "final_case_file", None)
    configured_final_data = getattr(cfg, "final_data_file", None)

    if configured_case_path:
        case_path = Path(configured_case_path)
        case_path_provenance = "case_path_override"
    elif results_dir:
        case_path = Path(results_dir) / str(geo_name) / str(case_name)
        case_path_provenance = "results_dir_builder"
    elif configured_final_case:
        case_path = Path(configured_final_case).parent
        case_path_provenance = "final_case_file_parent"
    else:
        raise ValueError(
            "Cannot locate the case directory. Set case_path, results_dir, "
            "or final_case_file in the post config."
        )

    case_path = case_path.resolve()

    def _resolve_case_artifact(
        configured_value,
        default_name: str,
        provenance_label: str,
    ) -> tuple[Path, str]:
        if configured_value is None:
            resolved = (case_path / default_name).resolve()
            return resolved, f"default_under_case_path:{default_name}"
        raw = Path(configured_value)
        if raw.is_absolute():
            return raw.resolve(), f"{provenance_label}:absolute_path"
        if raw.parent == Path(".") or str(raw.parent) == "":
            return (
                (case_path / raw.name).resolve(),
                f"{provenance_label}:case_path_join_bare_filename",
            )
        return (
            (case_path / raw).resolve(),
            f"{provenance_label}:case_path_join_relative_path",
        )

    final_case_file, final_case_provenance = _resolve_case_artifact(
        configured_final_case,
        f"{geo_name}_{case_name}_final.cas.h5",
        "final_case_file",
    )
    final_data_file, final_data_provenance = _resolve_case_artifact(
        configured_final_data,
        f"{geo_name}_{case_name}_final.dat.h5",
        "final_data_file",
    )
    post_path = case_path / "post"
    report_path = post_path / "reports"
    return {
        "geo_name": geo_name,
        "case_name": case_name,
        "case_path": case_path,
        "case_path_provenance": case_path_provenance,
        "final_case_file": final_case_file,
        "final_case_file_provenance": final_case_provenance,
        "final_data_file": final_data_file,
        "final_data_file_provenance": final_data_provenance,
        "post_path": post_path,
        "report_path": report_path,
    }


def require_explicit_scoring_layout(cfg):
    """Refuse to score when layout keys are still the stock unset defaults."""
    try:
        return resolve_scoring_layout_from_config(cfg)
    except AttributeError as exc:
        raise ValueError(
            "Layout is unset. Set n_buffer_in, n_active, n_buffer_out, "
            "cell_length_x_m, buffer_length_in_m, and buffer_length_out_m "
            "in the post config or PYFLUENT_POST_OVERRIDES. "
            "Stock post_config has no layout default."
        ) from exc


def require_explicit_evaluation_window(cfg):
    """Refuse to score when evaluation-window keys are still the stock unset defaults."""
    try:
        return resolve_evaluation_window_from_config(cfg)
    except AttributeError as exc:
        raise ValueError(
            "Evaluation window is unset. Set n_lead_excluded and "
            "n_trail_excluded in the post config or PYFLUENT_POST_OVERRIDES. "
            "Stock post_config has no window default."
        ) from exc


# The script body below runs only when this file is executed directly.
# Importing this module must not launch Fluent or write any files.
if __name__ == "__main__":
    cfg = load_python_config(CONFIG_PATH)

    # Per-case overrides from the batch runner (JSON dict). Applied to the
    # config module before the parameter binding below.
    _overrides_env = os.environ.get("PYFLUENT_POST_OVERRIDES")
    if _overrides_env:
        try:
            _overrides = json.loads(_overrides_env)
        except json.JSONDecodeError as e:
            raise ValueError(f"PYFLUENT_POST_OVERRIDES is not valid JSON: {e}")
        if not isinstance(_overrides, dict):
            raise ValueError("PYFLUENT_POST_OVERRIDES must be a JSON object.")
        cfg.apply_post_config_overrides(cfg, _overrides)
        print(f"Applied config overrides: {sorted(_overrides)}")

    # Refuse before Fluent if the layout was not explicitly supplied.
    # Stock post_config leaves those keys unset so a direct run cannot
    # silently score a 1+7+2 mesh as 1+3+1. The evaluation window is
    # applied after the run manifest is read.
    _extract_profile_name, _legacy_3_cell_window = parse_extract_options()
    scoring_layout = require_explicit_scoring_layout(cfg)

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

    geo_name = cfg.geo_name
    case_name = cfg.case_name

    paths = resolve_report_case_paths(cfg)
    case_path = paths["case_path"]
    case_path_provenance = paths["case_path_provenance"]
    final_case_file = paths["final_case_file"]
    final_case_file_provenance = paths["final_case_file_provenance"]
    final_data_file = paths["final_data_file"]
    final_data_file_provenance = paths["final_data_file_provenance"]
    post_path = paths["post_path"]
    report_path = paths["report_path"]

    post_path.mkdir(parents=True, exist_ok=True)
    report_path.mkdir(parents=True, exist_ok=True)

    summary_csv_path = report_path / "summary_metrics.csv"
    mass_balance_csv_path = report_path / "mass_balance.csv"
    pressure_csv_path = report_path / "pressure_report.csv"
    raw_report_json_path = report_path / "raw_report_values.json"

    print("Case path:", case_path, f"({case_path_provenance})")
    print(
        "Final case file:",
        final_case_file,
        f"({final_case_file_provenance})",
    )
    print(
        "Final data file:",
        final_data_file,
        f"({final_data_file_provenance})",
    )
    print("Report output folder:", report_path)

    if not final_case_file.is_file():
        raise FileNotFoundError(
            f"Final case file not found: {final_case_file} "
            f"(provenance={final_case_file_provenance}, "
            f"case_path={case_path} from {case_path_provenance})"
        )

    if not final_data_file.is_file():
        raise FileNotFoundError(
            f"Final data file not found: {final_data_file} "
            f"(provenance={final_data_file_provenance}, "
            f"case_path={case_path} from {case_path_provenance})"
        )

    sync_run_manifest_analytic_cwall(case_path)
    _run_manifest_for_window = read_run_manifest(case_path)
    if str(cfg.geo_name) != _run_manifest_for_window["geo_id"]:
        raise ValueError(
            "post config geo_name "
            f"{cfg.geo_name!r} does not match run manifest geo_id "
            f"{_run_manifest_for_window['geo_id']!r}."
        )
    _evaluation_window_selection = select_evaluation_window(
        geo_id=_run_manifest_for_window["geo_id"],
        mesh_id=_run_manifest_for_window["mesh_id"],
        n_active=scoring_layout.layout.n_active,
        cell_length_x_m=scoring_layout.layout.cell_length_x_m,
        legacy_3_cell_window=_legacy_3_cell_window,
    )
    apply_selection_to_config(cfg, _evaluation_window_selection)
    evaluation_window = require_explicit_evaluation_window(cfg)
    evaluation_window.evaluation_local_indices(scoring_layout.layout)
    print(
        "Evaluation window:",
        f"n_lead_excluded={_evaluation_window_selection.n_lead_excluded}",
        f"excluded_length_m={_evaluation_window_selection.excluded_length_m}",
        f"window_length_m={_evaluation_window_selection.window_length_m}",
        "window_table_version="
        f"{_evaluation_window_selection.window_table_version}",
    )

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

    apply_surface_report_definition(rd, report_type, field_name, surface_names)
    return report_name


def mass_diffusivity_m2_s():
    """``configs/run_config.py`` mass_diffusivity. Loaded only for RANS extracts."""
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "pyfluent_extract_run_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load run config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = getattr(module, "mass_diffusivity", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(
            f"run_config.mass_diffusivity must be a number, got {value!r}."
        )
    if not math.isfinite(float(value)) or float(value) == 0.0:
        raise RuntimeError(
            f"run_config.mass_diffusivity must be finite and non-zero, got {value!r}."
        )
    return float(value)


def _as_vectors(array):
    return [[float(item[0]), float(item[1]), float(item[2])] for item in array]


def _as_floats(array):
    return [float(value) for value in array]


def _surface_block(payload, data_type):
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Surface data payload is empty.")
    surface_id = sorted(payload, key=str)[0]
    block = payload[surface_id]
    if isinstance(block, dict):
        if data_type not in block:
            raise RuntimeError(f"Surface data has no {data_type!r}.")
        return block[data_type]
    return block


def _scalar_values(payload):
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Scalar field payload is empty.")
    surface_id = sorted(payload, key=str)[0]
    return _as_floats(payload[surface_id])


_MEMBRANE_FACE_FIELD_KEYS = {
    FIELD_UDM_CM: "cm",
    FIELD_UDM_JW: "jw",
    FIELD_UDM_Y1: "y1",
}


def read_membrane_clip_faces(
    solver,
    surface_names,
    x_min_m,
    x_max_m,
    clip_name,
    extra_fields=(),
):
    """Per-face cm, Jw, and area on one x-range clip of the membrane walls.

    ``boundary_value=False`` reads the adjacent cell UDM. Face UDM slots are
    not written in production (``RO_UDM_FACE_DIAGNOSTICS`` is off); surface
    reports of udm-6 and udm-7 use the cell values. ``extra_fields`` (udm-12
    on the evaluation window) uses the same cell-value read and is not caught.
    """
    from ansys.fluent.core.field_data_interfaces import SurfaceDataType

    field_names = (FIELD_UDM_CM, FIELD_UDM_JW, *extra_fields)
    unknown = [name for name in field_names if name not in _MEMBRANE_FACE_FIELD_KEYS]
    if unknown:
        raise ValueError(f"Unsupported membrane clip fields: {unknown}.")
    create_x_range_iso_clip(
        solver,
        clip_name,
        list(surface_names),
        x_min_m,
        x_max_m,
    )
    try:
        surface_data = solver.fields.field_data.get_surface_data(
            data_types=[
                SurfaceDataType.Vertices,
                SurfaceDataType.FacesConnectivity,
            ],
            surfaces=[clip_name],
        )
        vertices = _as_vectors(_surface_block(surface_data, SurfaceDataType.Vertices))
        connectivity = _surface_block(
            surface_data, SurfaceDataType.FacesConnectivity
        )
        areas = []
        for face in connectivity:
            points = [vertices[int(node)] for node in face]
            areas.append(polygon_area(points))
        columns = {}
        for field_name in field_names:
            scalar = solver.fields.field_data.get_scalar_field_data(
                field_name=field_name,
                surfaces=[clip_name],
                node_value=False,
                boundary_value=False,
            )
            values = _scalar_values(scalar)
            if len(values) != len(areas):
                raise RuntimeError(
                    f"{clip_name} field {field_name}: {len(values)} values "
                    f"for {len(areas)} faces."
                )
            columns[field_name] = values
        faces = []
        for index in range(len(areas)):
            face = {"area": areas[index]}
            for field_name in field_names:
                face[_MEMBRANE_FACE_FIELD_KEYS[field_name]] = columns[field_name][index]
            faces.append(face)
        return faces
    finally:
        delete_iso_clip(solver, clip_name)


def collect_concentration_cp_faces(
    solver,
    wall_surface_names,
    unit_cell_boundary_x_m,
    evaluation_cell_numbers,
    *,
    include_cells=True,
    include_y1=True,
):
    """Window clip of both walls, plus one clip per evaluation cell.

    ``include_cells=False`` skips the per-cell clips. The window metrics
    are read from the window clip, not from those cells.
    ``include_y1=False`` omits the y1 field on that window clip.
    """
    cell_faces = {}
    if include_cells:
        for cell_number in evaluation_cell_numbers:
            x_min_m, x_max_m = cell_x_bounds(unit_cell_boundary_x_m, cell_number)
            cell_faces[int(cell_number)] = read_membrane_clip_faces(
                solver,
                wall_surface_names,
                x_min_m,
                x_max_m,
                f"pp_cpc_cell_{int(cell_number)}",
            )
    x_min_m, x_max_m = window_x_bounds(
        unit_cell_boundary_x_m,
        evaluation_cell_numbers,
    )
    window_faces = read_membrane_clip_faces(
        solver,
        wall_surface_names,
        x_min_m,
        x_max_m,
        "pp_cpc_window",
        extra_fields=(FIELD_UDM_Y1,) if include_y1 else (),
    )
    return window_faces, cell_faces


def compute_rans_volume_reductions(solution, fluid_zones):
    """Max / volume-average over every fluid cell zone. Raises on failure."""
    reductions = {}
    raw = {}
    for metric_name, report_type, field_name in RANS_VOLUME_REDUCTIONS:
        report_name = f"rans_{metric_name}"
        create_or_update_volume_report(
            solution,
            report_name,
            report_type,
            field_name,
            fluid_zones,
        )
        value, report_raw = compute_one_report(
            solution=solution,
            report_name=report_name,
            verbose=False,
        )
        raw[report_name] = report_raw
        if value is None:
            raise RuntimeError(
                f"Volume report {report_name} returned no numeric value."
            )
        reductions[metric_name] = value
    return reductions, raw


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
    return _create_x_normal_plane(solver_obj, surface_name, x_value_m)


def create_z_normal_plane(solver_obj, surface_name, z_value_m):
    """Create a z-normal iso-surface at z_value_m (meters). Overwrites if it exists."""
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
        iso_group[surface_name].field = "z-coordinate"
        iso_group[surface_name].iso_values = [z_value_m]
        print(f"Created iso-surface '{surface_name}' at z = {z_value_m:.6e} m (settings API)")
        return
    except Exception as exc:
        e_settings = exc
        print(f"Settings API failed for iso-surface '{surface_name}': {exc}")

    try:
        solver_obj.tui.surface.iso_surface(
            "z-coordinate",
            surface_name,
            "()",
            "()",
            str(z_value_m),
            "0",
        )
        print(f"Created iso-surface '{surface_name}' at z = {z_value_m:.6e} m (TUI fallback)")
    except Exception as e_tui:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {e_settings}. TUI error: {e_tui}"
        )


# ==========================================================
# Cell 4. Launch Fluent through meshing mode and switch to solver
# ==========================================================

if __name__ == "__main__":
    _extract_profile_name = parse_extract_profile()
    report_profile = _extract_profile_name
    print(f"extract profile = {report_profile}")

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
    _reset_extract_timing()

    try:
        _begin_extract_phase("fluent_launch")
        print("Launching Fluent in meshing mode, then switching to solver...")
        print(f"product_version = {product_version}")
        print(f"processor_count = {processor_count}")
        print(f"start_timeout = {fluent_start_timeout}")
        print(f"health_timeout = {fluent_health_timeout}")
        print(f"working directory = {case_path}")

        # Direct solver-mode launch fails on this host with
        # "Failed to construct hwtree for collect command" during node
        # spawn (LaunchFluentError / health_check Deadline Exceeded).
        # The meshing-then-switch path is required; do not change this
        # to mode="solver".
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

        _end_extract_phase()
        _begin_extract_phase("read_case_data")
        print("Reading final case/data...")
        solver.settings.file.read_case_data(
            file_name=as_fluent_path(final_case_file)
        )

        print("Final case/data loaded.")
        _end_extract_phase()

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
        salt_molecular_weight_kg_per_mol = getattr(
            cfg,
            "salt_molecular_weight_kg_per_mol",
            0.05844,
        )
        salt_permeability_m_per_s = getattr(
            cfg,
            "salt_permeability_m_per_s",
            2.50e-8,
        )

        # Basic field names.
        FIELD_PRESSURE = "pressure"
        FIELD_ABSOLUTE_PRESSURE = "absolute-pressure"
        FIELD_VELOCITY_MAG = "velocity-magnitude"
        FIELD_SALT_MASS_FRACTION = "nacl"

        # UDM field names — imported from _udm_layout (locked to 260813_RO_UDF.c).
        # UDM_SM removed: water sink = volint(TOTAL_S) - volint(SI).

        # Wall shear stress magnitude field.
        # Wall shear rate will be calculated later as wall_shear / mu.
        FIELD_WALL_SHEAR = "wall-shear"

        print("Field names selected:")
        for name, value in {
            "FIELD_PRESSURE": FIELD_PRESSURE,
            "FIELD_ABSOLUTE_PRESSURE": FIELD_ABSOLUTE_PRESSURE,
            "FIELD_VELOCITY_MAG": FIELD_VELOCITY_MAG,
            "FIELD_SALT_MASS_FRACTION": FIELD_SALT_MASS_FRACTION,
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
        print(
            "  water sink = volint(FIELD_UDM_TOTAL_S) - volint(FIELD_UDM_SI) "
            "(UDM_SM removed)"
        )

        # ==========================================================
        # Cell 6.5. Compute spacer geometry parameters
        # ==========================================================

        # Asymmetric DomainLayout is required. Legacy getattr defaults for
        # domain_length_m / n_unit_cells / n_buffer_cells_each_end are gone.
        # Non-layout getattr defaults (salt_*, channel_height_m,
        # outlet_gauge_pressure, Fluent launch settings) are unchanged below.
        scoring_layout = resolve_scoring_layout_from_config(cfg)
        layout = scoring_layout.layout
        domain_x_min_m = scoring_layout.domain_x_min_m
        domain_length_m = scoring_layout.domain_length_m
        unit_cell_boundary_x_m = scoring_layout.unit_cell_boundary_x_m
        spacer_cells = scoring_layout.spacer_cells
        spacer_x_in_m = scoring_layout.spacer_x_in_m
        spacer_x_out_m = scoring_layout.spacer_x_out_m
        spacer_length_m = scoring_layout.spacer_length_m
        n_unit_cells = layout.n_total
        evaluation_window = require_explicit_evaluation_window(cfg)
        n_inlet_spacer_cells_excluded = evaluation_window.n_lead_excluded

        print("\nSpacer plane locations:")
        print(
            f"  layout = {layout.n_buffer_in}+{layout.n_active}+{layout.n_buffer_out}"
            f" (cell_length_x_m={layout.cell_length_x_m})"
        )
        print(
            f"  evaluation_window = lead={evaluation_window.n_lead_excluded}, "
            f"trail={evaluation_window.n_trail_excluded}"
        )
        print(
            f"  evaluation cells = "
            f"{evaluation_window.evaluation_cell_numbers(layout)}"
        )
        print(f"  spacer_x_in_m   = {spacer_x_in_m:.6e} m")
        print(f"  spacer_x_out_m  = {spacer_x_out_m:.6e} m")
        print(f"  spacer_length_m = {spacer_length_m:.6e} m")
        print(f"  unit-cell boundaries = {unit_cell_boundary_x_m}")
        print(f"  spacer cell numbers  = {spacer_cells}")

        _begin_extract_phase("cell_7_report_creation")
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

        run_manifest = read_run_manifest(case_path)
        membrane_blocked_area_frac = float(
            run_manifest["membrane_blocked_area_frac"]
        )

        # LMH from mass imbalance on effective membrane area.
        lmh_definition = lmh_mass_balance_expression(
            m_in_name="pp_m_in",
            m_out_name="pp_m_out",
            density_value=rho,
            area_mem_name="pp_area_mem",
            membrane_blocked_area_frac=membrane_blocked_area_frac,
            signed=False,
        )

        report_names.append(
            create_or_update_single_expression_report(
                solution,
                "pp_lmh_mass_balance",
                lmh_definition,
            )
        )
        if report_profile != PROFILE_MFBO:
            lmh_signed_definition = lmh_mass_balance_expression(
                m_in_name="pp_m_in",
                m_out_name="pp_m_out",
                density_value=rho,
                area_mem_name="pp_area_mem",
                membrane_blocked_area_frac=membrane_blocked_area_frac,
                signed=True,
            )
            report_names.append(
                create_or_update_single_expression_report(
                    solution,
                    "pp_lmh_mass_balance_signed",
                    lmh_signed_definition,
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

        # ----------------------------------------------------------
        # Every unit-cell boundary: pressure and salt mass fraction.
        # Existing spacer-edge plane/report names above stay unchanged.
        # ----------------------------------------------------------

        print("\nCreating unit-cell boundary plane surfaces and reports...")
        _mfbo_evaluation_cells = evaluation_window.evaluation_cell_numbers(layout)
        _mfbo_pressure_boundaries = set()
        _mfbo_mixing_boundaries = set()
        if report_profile == PROFILE_MFBO:
            _mfbo_pressure_boundaries = mfbo_pressure_boundary_indices(
                _mfbo_evaluation_cells
            )
            _mfbo_mixing_boundaries = mfbo_mixing_cup_boundary_indices(
                _mfbo_evaluation_cells
            )
        for boundary_index, boundary_x_m in enumerate(unit_cell_boundary_x_m):
            if report_profile == PROFILE_MFBO and boundary_index not in (
                _mfbo_pressure_boundaries | _mfbo_mixing_boundaries
            ):
                continue
            plane_name = unit_cell_plane_name(boundary_index)
            pressure_report_name = unit_cell_pressure_report_name(boundary_index)
            concentration_report_name = unit_cell_concentration_report_name(
                boundary_index
            )
            (
                mixing_cup_report_name,
                mixing_cup_report_type,
                mixing_cup_field_name,
            ) = unit_cell_mixing_cup_report_spec(
                boundary_index,
                FIELD_SALT_MASS_FRACTION,
            )
            plane_area_report_name = unit_cell_plane_area_report_name(
                boundary_index
            )

            create_x_normal_plane(solver, plane_name, boundary_x_m)
            if (
                report_profile != PROFILE_MFBO
                or boundary_index in _mfbo_pressure_boundaries
            ):
                report_names.append(
                    create_or_update_surface_report(
                        solution,
                        pressure_report_name,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_PRESSURE,
                        [plane_name],
                    )
                )
            if (
                report_profile != PROFILE_MFBO
                or boundary_index in _mfbo_mixing_boundaries
            ):
                report_names.append(
                    create_or_update_surface_report(
                        solution,
                        mixing_cup_report_name,
                        mixing_cup_report_type,
                        mixing_cup_field_name,
                        [plane_name],
                    )
                )
            if report_profile != PROFILE_MFBO:
                report_names.append(
                    create_or_update_surface_report(
                        solution,
                        plane_area_report_name,
                        SURFACE_AREA,
                        None,
                        [plane_name],
                    )
                )
                report_names.append(
                    create_or_update_surface_report(
                        solution,
                        concentration_report_name,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_SALT_MASS_FRACTION,
                        [plane_name],
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
        if report_profile == PROFILE_MFBO:
            surface_report_specs = [
                ("pp_lmh_udm_avg", SURFACE_AREA_WEIGHTED_AVG, FIELD_UDM_LMH),
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
        # Water sink is derived as TOTAL_S - SI (UDM_SM removed); no separate
        # pp_volint_water_mass_source report.
        volume_report_specs = [
            ("pp_volint_salt_mass_source", "volume-integral", FIELD_UDM_SI),
            ("pp_volint_total_mass_source", "volume-integral", FIELD_UDM_TOTAL_S),
        ]
        if report_profile != PROFILE_MFBO:
            volume_report_specs.append(
                udm_area_sum_report_spec(FIELD_UDM_MEMBRANE_AREA_ACC)
            )

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

        _mfbo_required_reports = (
            mfbo_required_report_names(_mfbo_evaluation_cells)
            if report_profile == PROFILE_MFBO
            else None
        )
        require_load_bearing_report_definitions(
            report_names,
            failed_report_specs,
            required_names=_mfbo_required_reports,
        )

        _end_extract_phase()
        _begin_extract_phase("cell_8_compute")
        # ==========================================================
        # Cell 8. Compute reports
        # ==========================================================

        computed_values = {}
        raw_results = {}

        for report_name in report_names:
            try:
                if report_name in FLUX_BOUNDARY_MASSFLOW_REPORTS:
                    value, raw, decomposition = (
                        compute_flux_massflow_boundary_report(
                            solution=solution,
                            report_name=report_name,
                        )
                    )
                    computed_values[report_name] = value
                    computed_values[f"{report_name}_with_sources"] = (
                        decomposition["with_sources"]
                    )
                    computed_values[f"{report_name}_mass_source"] = (
                        decomposition["mass_source"]
                    )
                else:
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
                if (
                    report_name in FLUX_BOUNDARY_MASSFLOW_REPORTS
                    or report_name in LOAD_BEARING_REPORT_NAMES
                ):
                    raise LoadBearingReportComputeError(
                        f"Load-bearing report {report_name!r} failed: {e}"
                    ) from e

        require_load_bearing_report_computes(
            computed_values,
            raw_results,
            report_names=report_names,
            required_names=_mfbo_required_reports,
        )

        if report_profile == PROFILE_MFBO:
            turbulence_metrics = None
            turbulence_plan = None
        else:
            turbulence_plan = turbulence_extract_plan(run_manifest)
        if turbulence_plan == "compute":
            require_turbulence_scalar_fields(
                scalar_field_names(
                    solver.fields.field_info.get_scalar_fields_info()
                )
            )
            rans_reductions, rans_raw = compute_rans_volume_reductions(
                solution,
                fluid_zones,
            )
            raw_results.update(rans_raw)
            turbulence_metrics = turbulence_metrics_from_reductions(
                mass_diffusivity=mass_diffusivity_m2_s(),
                **rans_reductions,
            )
        elif turbulence_plan is not None:
            turbulence_metrics = null_turbulence_metrics(turbulence_plan)

        print("\nComputed values:")
        pprint(computed_values)

        _end_extract_phase()
        _begin_extract_phase("active_window_geometry")
        active_window = measure_active_window_geometry(
            solver,
            solution,
            active_membrane_zones,
            spacer_wall_zones,
            fluid_zones,
            spacer_x_in_m,
            spacer_x_out_m,
            layout.active_length_m,
            layout.buffer_length_in_m,
            layout.buffer_length_out_m,
            run_manifest["periodic_shift_y_m"],
            CAMPAIGN_H_M,
            run_manifest["family"],
        )
        print(
            "Active-window geometry: "
            f"V={active_window['active_window_fluid_volume_m3']:.6e} m3, "
            f"A_mem={active_window['active_window_membrane_area_m2']:.6e} m2, "
            f"A_spacer={active_window['active_window_spacer_area_m2']:.6e} m2, "
            f"V_box={active_window['active_window_box_volume_m3']:.6e} m3, "
            f"porosity={active_window['active_window_porosity']:.6g}; "
            f"cost={active_window['fluent_surface_integrals']} surface-area "
            f"integral(s) and {active_window['fluent_volume_integrals']} "
            "volume report"
        )
        _end_extract_phase()
        _begin_extract_phase("cell_8_25_salt_reduction")
        # ==========================================================
        # Cell 8.25. Salt mass-fraction range diagnostics
        # ==========================================================

        salt_mass_fraction_upper_threshold = getattr(
            cfg,
            "salt_mass_fraction_upper_threshold",
            0.99,
        )
        salt_mass_fraction_lower_threshold = getattr(
            cfg,
            "salt_mass_fraction_lower_threshold",
            1.0e-6,
        )
        concentration_diagnostics = {
            "pp_salt_mass_fraction_cells_above_threshold": None,
            "pp_salt_mass_fraction_cells_below_threshold": None,
            "pp_salt_mass_fraction_max": None,
            "pp_salt_mass_fraction_min": None,
        }
        concentration_diagnostic_error = ""
        concentration_diagnostic_error_type = ""
        concentration_diagnostic_error_message = ""
        if report_profile != PROFILE_MFBO:
            try:
                reduction_locations = fluid_zone_reduction_locations(
                    setup,
                    fluid_zones,
                )
                concentration_diagnostics.update(
                    concentration_range_diagnostics(
                        reduction=solver.fields.reduction,
                        fluid_zone_locations=reduction_locations,
                        species_name=FIELD_SALT_MASS_FRACTION,
                        lower_threshold=salt_mass_fraction_lower_threshold,
                        upper_threshold=salt_mass_fraction_upper_threshold,
                    )
                )
            except Exception as exc:
                error = exception_details(exc)
                concentration_diagnostic_error_type = error["type"]
                concentration_diagnostic_error_message = error["message"]
                concentration_diagnostic_error = error["combined"]
                print(
                    "WARNING: salt mass-fraction range diagnostics failed: "
                    f"{concentration_diagnostic_error}"
                )

        print("\nSalt mass-fraction range diagnostics:")
        pprint(concentration_diagnostics)

        evaluation_cells = evaluation_window.evaluation_cell_numbers(layout)

        _end_extract_phase()
        _begin_extract_phase("cell_8_4_midplane_cb")
        # ==========================================================
        # Cell 8.4. Mid-plane bulk c_b per evaluation cell (window only)
        # ==========================================================
        # Canonical CP denominator. Mixing-cup (surface-massavg) on the
        # channel mid-plane z = 0.5*(z_min+z_max) (origin-agnostic; z = 0 on
        # the campaign channel-centred mesh), clipped in x to each evaluation
        # cell. NOT interchangeable with cp_inlet_avg (L2 / UDM-9 inlet
        # denominator).

        # Mid-plane z from measured fluid bounds: z_mid = 0.5*(z_min+z_max).
        # Origin-agnostic (z=0 on the campaign channel-centred mesh). Do NOT
        # use a candidate list of h/2 vs 0 — any iso-value inside [z_min,z_max]
        # "succeeds", so first-success permanently picks a wrong plane.
        _SALT_FIELD_CANDIDATES_CENTER = [
            "nacl", "mass-fraction-of-nacl", "yi-0", "species-0", "udm-7",
        ]

        c_b_by_cell_mol_per_m3 = {}
        midplane_area_by_cell_m2 = {}
        c_b_window_mol_per_m3 = None
        _midplane_salt_field = None
        _midplane_salt_is_mass_fraction = True
        _found_center_z = None
        _found_center_pname = None
        _midplane_z_diag = None

        try:
            (
                _found_center_pname,
                _found_center_z,
                _midplane_z_diag,
            ) = create_channel_midplane_plane(
                solver,
                setup=setup,
                fluid_zone_names=fluid_zones,
            )
            print(
                f"Mid-plane iso-surface: name={_found_center_pname!r} "
                f"z={_found_center_z!r} diag={_midplane_z_diag}"
            )
        except Exception as _e_plane:
            raise RuntimeError(
                "Canonical CP cannot be computed: mid-plane iso-surface "
                f"creation failed ({_e_plane!r}). "
                "cp_inlet_avg (L2) is not a substitute for canonical CP."
            ) from _e_plane


        _midplane_cell_numbers = (
            list(evaluation_cells)
            if report_profile == PROFILE_MFBO
            else spacer_cells
        )
        midplane_window_error = None
        for _salt_field in _SALT_FIELD_CANDIDATES_CENTER:
            try:
                (
                    c_b_by_cell_mol_per_m3,
                    midplane_area_by_cell_m2,
                    _c_b_window_probe,
                ) = evaluation_window_midplane_bulk_concentrations(
                    solver=solver,
                    solution=solution,
                    midplane_surface_names=[_found_center_pname],
                    unit_cell_boundary_x_m=unit_cell_boundary_x_m,
                    evaluation_cell_numbers=_midplane_cell_numbers,
                    salt_field=_salt_field,
                    density_kg_per_m3=rho,
                    molecular_weight_kg_per_mol=(
                        salt_molecular_weight_kg_per_mol
                    ),
                    salt_is_mass_fraction=True,
                )
                c_b_window_mol_per_m3 = midplane_window_bulk_aggregate(
                    c_b_by_cell_mol_per_m3,
                    midplane_area_by_cell_m2,
                    evaluation_cells,
                )
                _midplane_salt_field = _salt_field
                _midplane_salt_is_mass_fraction = True
                midplane_window_error = None
                break
            except Exception as _e_massfrac:
                midplane_window_error = _e_massfrac
                print(
                    f"Mid-plane c_b with field {_salt_field!r} failed: "
                    f"{_e_massfrac}"
                )
        if not c_b_by_cell_mol_per_m3:
            for _salt_field in _SALT_FIELD_CANDIDATES_CENTER:
                try:
                    (
                        c_b_by_cell_mol_per_m3,
                        midplane_area_by_cell_m2,
                        _c_b_window_probe,
                    ) = evaluation_window_midplane_bulk_concentrations(
                        solver=solver,
                        solution=solution,
                        midplane_surface_names=[_found_center_pname],
                        unit_cell_boundary_x_m=unit_cell_boundary_x_m,
                        evaluation_cell_numbers=_midplane_cell_numbers,
                        salt_field=_salt_field,
                        density_kg_per_m3=rho,
                        molecular_weight_kg_per_mol=(
                            salt_molecular_weight_kg_per_mol
                        ),
                        salt_is_mass_fraction=False,
                    )
                    c_b_window_mol_per_m3 = midplane_window_bulk_aggregate(
                        c_b_by_cell_mol_per_m3,
                        midplane_area_by_cell_m2,
                        evaluation_cells,
                    )
                    _midplane_salt_field = _salt_field
                    _midplane_salt_is_mass_fraction = False
                    midplane_window_error = None
                    break
                except Exception as _e_molar:
                    midplane_window_error = _e_molar
                    print(
                        f"Mid-plane c_b molar try {_salt_field!r} failed: "
                        f"{_e_molar}"
                    )
        if not c_b_by_cell_mol_per_m3:
            detail = (
                f" Last error: {midplane_window_error!r}."
                if midplane_window_error is not None
                else ""
            )
            raise RuntimeError(
                "Canonical CP cannot be computed: window mid-plane c_b "
                "failed for all salt-field candidates."
                f"{detail} "
                "cp_inlet_avg (L2 / UDM-9 inlet denominator) is not a "
                "substitute for canonical CP."
            )

        print(
            f"\nEvaluation-window mid-plane c_b_window: {c_b_window_mol_per_m3} mol/m3"
        )
        print(f"  per-cell c_b: {c_b_by_cell_mol_per_m3}")
        print(f"  salt field: {_midplane_salt_field!r} (surface-massavg)")

        # Guard: mid-plane c_b must agree with independent x-normal mixing-cup
        # molar concentrations on the flanking unit-cell boundaries. A wall-
        # placed plane fails this check (historically ~3.5% high on D2450_a45).
        _mixing_cup_mol_by_boundary = {}
        for _b_idx in range(n_unit_cells + 1):
            _mf = computed_values.get(
                unit_cell_mixing_cup_report_name(_b_idx)
            )
            if _mf is None:
                continue
            _mixing_cup_mol_by_boundary[_b_idx] = mass_fraction_to_molar_concentration(
                _mf,
                rho,
                salt_molecular_weight_kg_per_mol,
            )
        assert_midplane_c_b_matches_boundary_mixing_cup(
            c_b_by_cell_mol_per_m3,
            _mixing_cup_mol_by_boundary,
            evaluation_cells,
        )
        print(
            "  mid-plane c_b cross-check vs boundary mixing-cup: OK "
            f"(rel_tol={MIDPLANE_CB_MIXING_CUP_REL_TOL:.3%})"
        )

        # ==========================================================
        # Cell 8.4b. Legacy whole-domain mid-plane average (NOT c_b)
        # ==========================================================
        # Inventory diagnostic only. Includes inlet buffers; must not be
        # used as the canonical CP denominator.

        c_bulk_center_area_avg = None
        c_bulk_center_area_avg_source = None
        c_bulk_center_area_avg_units_or_type = None
        c_bulk_center_plane_name = None
        _c_bulk_center_diag = "not_attempted"
        if report_profile != PROFILE_MFBO:
            try:
                for _salt_field in _SALT_FIELD_CANDIDATES_CENTER:
                    try:
                        create_or_update_surface_report(
                            solution,
                            "pp_c_bulk_center",
                            SURFACE_AREA_WEIGHTED_AVG,
                            _salt_field,
                            [_found_center_pname],
                        )
                        _val_center, _ = compute_one_report(
                            solution, "pp_c_bulk_center", verbose=False
                        )
                        if _val_center is None:
                            _c_bulk_center_diag = f"field={_salt_field},val=None"
                            continue
                        if 1e-4 <= _val_center <= 0.20:
                            c_bulk_center_area_avg = _val_center
                            c_bulk_center_area_avg_source = _salt_field
                            c_bulk_center_area_avg_units_or_type = "mass_fraction"
                            c_bulk_center_plane_name = _found_center_pname
                            _c_bulk_center_diag = (
                                f"ok,field={_salt_field},z={_found_center_z},"
                                f"val={_val_center:.6g}; "
                                "whole-domain only — not for CP c_b"
                            )
                            break
                        elif 50 <= _val_center <= 3000:
                            c_bulk_center_area_avg = _val_center
                            c_bulk_center_area_avg_source = _salt_field
                            c_bulk_center_area_avg_units_or_type = "molar_mol_m3"
                            c_bulk_center_plane_name = _found_center_pname
                            _c_bulk_center_diag = (
                                f"ok,field={_salt_field},z={_found_center_z},"
                                f"val={_val_center:.6g}; "
                                "whole-domain only — not for CP c_b"
                            )
                            break
                        else:
                            _c_bulk_center_diag = (
                                f"field={_salt_field},val={_val_center:.4g},not_plausible"
                            )
                    except Exception as _e_salt:
                        _c_bulk_center_diag = f"field={_salt_field},err:{_e_salt}"
            except Exception as _e_legacy:
                _c_bulk_center_diag = f"legacy_exception:{_e_legacy}"
                print(
                    "WARNING: legacy whole-domain center-plane average failed "
                    f"(canonical c_b already computed): {_e_legacy}"
                )

        _end_extract_phase()
        _begin_extract_phase("segmented_membrane_cp")
        segmented_cp_values = {}
        segmented_cp_diagnostic_error = ""
        segmented_cp_diagnostic_error_type = ""
        segmented_cp_diagnostic_error_message = ""
        try:
            wall_surfaces_by_name = {
                zone_name: [zone_name]
                for zone_name in active_membrane_zones
            }
            mixing_cup_mass_fraction_by_boundary = {
                boundary_index: computed_values.get(
                    unit_cell_mixing_cup_report_name(boundary_index)
                )
                for boundary_index in range(n_unit_cells + 1)
            }
            _cp_spread = bool(getattr(cfg, "compute_cp_spread", False))
            _segment_cells = spacer_cells
            _segment_walls = wall_surfaces_by_name
            _include_all_active = True
            if report_profile == PROFILE_MFBO:
                _segment_cells = list(evaluation_cells)
                _segment_walls = None
                _include_all_active = False
            print(
                f"\nSegmented membrane CP: compute_cp_spread={_cp_spread}"
            )
            segmented_cp_values = segmented_membrane_cp_metrics(
                solver=solver,
                solution=solution,
                wall_surface_names=list(active_membrane_zones),
                unit_cell_boundary_x_m=unit_cell_boundary_x_m,
                spacer_cells=_segment_cells,
                mixing_cup_mass_fraction_by_boundary=(
                    mixing_cup_mass_fraction_by_boundary
                ),
                density_kg_per_m3=rho,
                molecular_weight_kg_per_mol=(
                    salt_molecular_weight_kg_per_mol
                ),
                c_inlet_ref_mol_per_m3=cfg.c_inlet_ref,
                salt_permeability_m_per_s=salt_permeability_m_per_s,
                evaluation_cell_numbers=evaluation_cells,
                c_b_by_cell_mol_per_m3=c_b_by_cell_mol_per_m3,
                midplane_area_by_cell_m2=midplane_area_by_cell_m2,
                wall_surfaces_by_name=_segment_walls,
                compute_cp_spread=_cp_spread,
                include_all_active=_include_all_active,
                subphase_seconds=_segmented_membrane_cp_subphase_seconds,
            )
            print(
                "Segmented membrane CP fluent_surface_computes="
                f"{segmented_cp_values.get('cp_membrane_segment_fluent_computes')}"
            )
            if (
                segmented_cp_values.get("cp_canon_rescale_delta_status")
                == "not_evaluated"
            ):
                print(
                    "CP scalar-rescale delta bound: NOT EVALUATED "
                    "(compute_cp_spread=False). "
                    "cp_canon_rescale_delta_max is null; "
                    "do not treat a missing delta as a passing delta."
                )
        except Exception as exc:
            error = exception_details(exc)
            segmented_cp_diagnostic_error_type = error["type"]
            segmented_cp_diagnostic_error_message = error["message"]
            segmented_cp_diagnostic_error = error["combined"]
            print(
                "ERROR: segmented membrane CP diagnostics failed: "
                f"{segmented_cp_diagnostic_error}"
            )
            raise RuntimeError(
                "Canonical CP cannot be computed: segmented membrane CP "
                f"failed ({segmented_cp_diagnostic_error}). "
                "cp_inlet_avg (L2) is not a substitute for canonical CP."
            ) from exc

        if (
            "cp_canon_window_avg" not in segmented_cp_values
            or segmented_cp_values.get("c_b_window_mol_m3") is None
        ):
            raise RuntimeError(
                "Canonical CP cannot be computed: segmented_cp_values is "
                "missing cp_canon_window_avg / c_b_window_mol_m3. "
                "cp_inlet_avg (L2) is not a substitute for canonical CP."
            )

        print("\nSegmented membrane CP diagnostics:")
        pprint(segmented_cp_values)

        window_faces, cell_faces = collect_concentration_cp_faces(
            solver,
            active_membrane_zones,
            unit_cell_boundary_x_m,
            evaluation_cells,
            include_cells=report_profile != PROFILE_MFBO,
            include_y1=report_profile != PROFILE_MFBO,
        )
        concentration_cp_metrics = concentration_cp_report(
            window_faces,
            cell_faces,
            c_b_window_mol_per_m3,
            c_b_by_cell_mol_per_m3,
            b_perm=B_PERM_M_PER_S,
        )
        print(
            "Concentration-statistics CP: "
            f"cpc_window_avg_area="
            f"{concentration_cp_metrics['window']['cpc_avg_area']}, "
            f"cpc_window_avg_flux="
            f"{concentration_cp_metrics['window']['cpc_avg_flux']}"
        )
        if report_profile == PROFILE_MFBO:
            membrane_y1_metrics = None
        else:
            membrane_y1_metrics = y1_window_resolution(window_faces)
            print(
                "Membrane y1: "
                f"areamean_um={membrane_y1_metrics['y1_window_areamean_um']}, "
                f"q10_um={membrane_y1_metrics['y1_window_q10_um']}, "
                f"median_um={membrane_y1_metrics['y1_window_median_um']}, "
                f"q90_um={membrane_y1_metrics['y1_window_q90_um']}"
            )

        _end_extract_phase()
        # ==========================================================
        # Cell 8.5. Whole-domain center-plane bulk (inventory only)
        # ==========================================================

        c_bulk_center_mass_fraction_avg = None
        c_bulk_center_mol_m3_avg = None
        if c_bulk_center_area_avg_units_or_type == "mass_fraction":
            c_bulk_center_mass_fraction_avg = c_bulk_center_area_avg
            c_bulk_center_mol_m3_avg = mass_fraction_to_molar_concentration(
                c_bulk_center_area_avg,
                rho,
                salt_molecular_weight_kg_per_mol,
            )
        elif c_bulk_center_area_avg_units_or_type == "molar_mol_m3":
            c_bulk_center_mol_m3_avg = c_bulk_center_area_avg
            c_bulk_center_mass_fraction_avg = (
                molar_concentration_to_mass_fraction(
                    c_bulk_center_area_avg,
                    rho,
                    salt_molecular_weight_kg_per_mol,
                )
            )

        print(
            f"\nCenter-plane bulk salt average: {c_bulk_center_area_avg} "
            f"({c_bulk_center_area_avg_units_or_type})"
        )
        print(f"  Plane : {c_bulk_center_plane_name}  Field: {c_bulk_center_area_avg_source}")
        print(f"  Diag  : {_c_bulk_center_diag}")

        _begin_extract_phase("csv_write")
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
        m_in_with_sources = get_value("pp_m_in_with_sources")
        m_out_with_sources = get_value("pp_m_out_with_sources")
        m_in_mass_source = get_value("pp_m_in_mass_source")
        m_out_mass_source = get_value("pp_m_out_mass_source")
        area_mem = get_value("pp_area_mem")
        udm_area_sum = get_value("pp_udm_area_sum")
        lmh_mass_balance = get_value("pp_lmh_mass_balance")
        lmh_mass_balance_signed = get_value(
            "pp_lmh_mass_balance_signed"
        )

        p_in_avg = get_value("pp_p_in_avg")
        p_out_avg = get_value("pp_p_out_avg")
        pressure_drop = get_value("pp_pressure_drop")

        # Boundary permeate mass flow from mass imbalance.
        boundary_permeate_mass_flow = safe_abs_sum(m_in, m_out)

        # Python-side signed LMH from boundary fluxes (no Fluent expression).
        lmh_mass_balance_signed_python = None
        if (
            m_in is not None
            and m_out is not None
            and area_mem is not None
            and area_mem != 0
        ):
            lmh_mass_balance_signed_python = (
                (m_in + m_out) / (rho * area_mem) * MS_TO_LMH
            )
            if lmh_mass_balance_signed_python < 0.0:
                print(
                    "WARNING: lmh_mass_balance_signed_python is negative "
                    f"({lmh_mass_balance_signed_python:.6e} LMH); using "
                    "without-sources boundary fluxes, the net (m_in + m_out) "
                    "should be positive with a mass sink."
                )

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
        # Water sink = volint(TOTAL_S) - volint(SI); UDM_SM removed.
        # Metric key water_sink_volume_integral_UDM1 kept for CSV schema.
        # ----------------------------------------------------------

        salt_sink_volint = get_value("pp_volint_salt_mass_source")
        total_sink_volint = get_value("pp_volint_total_mass_source")
        if salt_sink_volint is not None and total_sink_volint is not None:
            water_sink_volint = float(total_sink_volint) - float(salt_sink_volint)
        else:
            water_sink_volint = None
            print(
                "WARNING: cannot derive water_sink_volume_integral_UDM1; "
                f"salt_volint={salt_sink_volint!r}, total_volint={total_sink_volint!r}"
            )

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

        unit_cell_derived_metrics = derive_spacer_cell_metrics_for_layout(
            computed_values,
            layout,
        )
        periodic_pressure_metrics = (
            derive_periodic_spacer_pressure_metrics_for_layout(
                unit_cell_derived_metrics,
                layout,
                evaluation_window,
            )
        )

        unit_cell_summary_rows = []
        unit_cell_pressure_rows = []
        for boundary_index, boundary_x_m in enumerate(unit_cell_boundary_x_m):
            pressure_report_name = unit_cell_pressure_report_name(boundary_index)
            concentration_report_name = unit_cell_concentration_report_name(
                boundary_index
            )
            mixing_cup_report_name = unit_cell_mixing_cup_report_name(
                boundary_index
            )
            areaavg_molar_name = unit_cell_areaavg_molar_concentration_name(
                boundary_index
            )
            mixing_cup_molar_name = (
                unit_cell_mixing_cup_molar_concentration_name(
                    boundary_index
                )
            )
            plane_area_report_name = unit_cell_plane_area_report_name(
                boundary_index
            )
            areaavg_mass_fraction = get_value(concentration_report_name)
            mixing_cup_mass_fraction = get_value(mixing_cup_report_name)
            unit_cell_summary_rows.extend([
                {
                    "metric": f"pp_unit_cell_boundary_{boundary_index}_x_m",
                    "value": boundary_x_m,
                    "unit": "m",
                },
                {
                    "metric": pressure_report_name,
                    "value": get_value(pressure_report_name),
                    "unit": "Pa",
                },
                {
                    "metric": concentration_report_name,
                    "value": areaavg_mass_fraction,
                    "unit": "mass_fraction",
                },
                {
                    "metric": mixing_cup_report_name,
                    "value": mixing_cup_mass_fraction,
                    "unit": "mass_fraction",
                },
                {
                    "metric": areaavg_molar_name,
                    "value": mass_fraction_to_molar_concentration(
                        areaavg_mass_fraction,
                        rho,
                        salt_molecular_weight_kg_per_mol,
                    ),
                    "unit": "mol/m3",
                },
                {
                    "metric": mixing_cup_molar_name,
                    "value": mass_fraction_to_molar_concentration(
                        mixing_cup_mass_fraction,
                        rho,
                        salt_molecular_weight_kg_per_mol,
                    ),
                    "unit": "mol/m3",
                },
                {
                    "metric": plane_area_report_name,
                    "value": get_value(plane_area_report_name),
                    "unit": "m2",
                },
            ])
            unit_cell_pressure_rows.extend([
                {
                    "metric": f"pp_unit_cell_boundary_{boundary_index}_x_m",
                    "value": boundary_x_m,
                    "unit": "m",
                },
                {
                    "metric": pressure_report_name,
                    "value": get_value(pressure_report_name),
                    "unit": "Pa",
                },
            ])

        for metric_name, metric_value in unit_cell_derived_metrics.items():
            metric_unit = (
                "Pa"
                if "pressure_drop" in metric_name
                else concentration_metric_unit(metric_name) or "-"
            )
            row = {
                "metric": metric_name,
                "value": metric_value,
                "unit": metric_unit,
            }
            unit_cell_summary_rows.append(row)
            if metric_unit == "Pa":
                unit_cell_pressure_rows.append(row.copy())

        periodic_pressure_rows = [
            {
                "metric": "pp_pressure_drop_periodic_per_m",
                "value": periodic_pressure_metrics[
                    "pp_pressure_drop_periodic_per_m"
                ],
                "unit": "Pa/m",
            },
            {
                "metric": "pp_pressure_drop_cell2_over_cell3",
                "value": periodic_pressure_metrics[
                    "pp_pressure_drop_cell2_over_cell3"
                ],
                "unit": "-",
            },
        ]
        unit_cell_summary_rows.extend(periodic_pressure_rows)
        unit_cell_pressure_rows.extend(
            row.copy() for row in periodic_pressure_rows
        )
        for metric_name, metric_value in segmented_cp_values.items():
            if metric_name in {
                "c_b_window_mol_m3",
            }:
                # Written from the mid-plane path below; avoid duplicate metric.
                continue
            if metric_name.endswith("_m2"):
                metric_unit = "m2"
            elif "_m_per_s_" in metric_name:
                metric_unit = "m/s"
            else:
                metric_unit = concentration_metric_unit(metric_name) or "-"
            unit_cell_summary_rows.append({
                "metric": metric_name,
                "value": metric_value,
                "unit": metric_unit,
            })
        for cell_number, c_b_val in sorted(c_b_by_cell_mol_per_m3.items()):
            metric_name = f"pp_c_b_midplane_cell_{cell_number}_mol_m3"
            if any(row["metric"] == metric_name for row in unit_cell_summary_rows):
                continue
            unit_cell_summary_rows.append({
                "metric": metric_name,
                "value": c_b_val,
                "unit": "mol/m3",
            })

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

        if _evaluation_window_selection is None:
            raise RuntimeError(
                "evaluation window was not selected before the summary "
                "was built."
            )
        _window_fluxes, _window_areas = per_cell_flux_and_area(
            segmented_cp_values,
            evaluation_cells,
        )
        lmh_window_exposed, lmh_window_module = window_lmh(
            _window_fluxes,
            _window_areas,
            _evaluation_window_selection.window_length_m,
            run_manifest["periodic_shift_y_m"],
        )

        # ----------------------------------------------------------
        # Summary table
        # ----------------------------------------------------------

        summary_rows = [
            {"metric": "geo_name", "value": geo_name, "unit": "-"},
            {"metric": "case_name", "value": case_name, "unit": "-"},

            {"metric": "m_in", "value": m_in, "unit": "kg/s"},
            {"metric": "m_out", "value": m_out, "unit": "kg/s"},
            {
                "metric": "m_in_with_sources",
                "value": m_in_with_sources,
                "unit": "kg/s",
            },
            {
                "metric": "m_out_with_sources",
                "value": m_out_with_sources,
                "unit": "kg/s",
            },
            {
                "metric": "m_in_mass_source",
                "value": m_in_mass_source,
                "unit": "kg/s",
            },
            {
                "metric": "m_out_mass_source",
                "value": m_out_mass_source,
                "unit": "kg/s",
            },
            {"metric": "boundary_permeate_mass_flow", "value": boundary_permeate_mass_flow, "unit": "kg/s"},

            {"metric": "area_mem", "value": area_mem, "unit": "m2"},
            {
                "metric": "active_window_fluid_volume_m3",
                "value": active_window["active_window_fluid_volume_m3"],
                "unit": "m3",
            },
            {
                "metric": "active_window_membrane_area_m2",
                "value": active_window["active_window_membrane_area_m2"],
                "unit": "m2",
            },
            {
                "metric": "active_window_spacer_area_m2",
                "value": active_window["active_window_spacer_area_m2"],
                "unit": "m2",
            },
            {
                "metric": "active_window_box_volume_m3",
                "value": active_window["active_window_box_volume_m3"],
                "unit": "m3",
            },
            {
                "metric": "active_window_porosity",
                "value": active_window["active_window_porosity"],
                "unit": "-",
            },
            {"metric": "pp_udm_area_sum", "value": udm_area_sum, "unit": "m2"},

            {"metric": "lmh_mass_balance", "value": lmh_mass_balance, "unit": "LMH"},
            {"metric": "lmh_window_exposed", "value": lmh_window_exposed, "unit": "LMH"},
            {"metric": "lmh_window_module", "value": lmh_window_module, "unit": "LMH"},
            {
                "metric": "n_lead_excluded",
                "value": _evaluation_window_selection.n_lead_excluded,
                "unit": "-",
            },
            {
                "metric": "excluded_length_m",
                "value": _evaluation_window_selection.excluded_length_m,
                "unit": "m",
            },
            {
                "metric": "window_length_m",
                "value": _evaluation_window_selection.window_length_m,
                "unit": "m",
            },
            {
                "metric": "window_table_version",
                "value": _evaluation_window_selection.window_table_version,
                "unit": "-",
            },
            {"metric": "lmh_mass_balance_signed", "value": lmh_mass_balance_signed, "unit": "LMH"},
            {
                "metric": "lmh_mass_balance_signed_python",
                "value": lmh_mass_balance_signed_python,
                "unit": "LMH",
            },
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
            {"metric": "cm_mol_m3_avg", "value": cm_avg, "unit": "mol/m3"},
            {"metric": "cm_mol_m3_max", "value": cm_max, "unit": "mol/m3"},
            {"metric": "cm_mol_m3_min", "value": cm_min, "unit": "mol/m3"},

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

            {"metric": "c_bulk_center_area_avg",               "value": c_bulk_center_area_avg,                          "unit": c_bulk_center_area_avg_units_or_type or "-"},
            {"metric": "c_bulk_center_area_avg_source",        "value": c_bulk_center_area_avg_source or "",              "unit": "-"},
            {"metric": "c_bulk_center_area_avg_units_or_type", "value": c_bulk_center_area_avg_units_or_type or "",       "unit": "-"},
            {"metric": "c_bulk_center_plane_name",             "value": c_bulk_center_plane_name or "",                   "unit": "-"},
            {"metric": "c_bulk_center_mass_fraction_avg", "value": c_bulk_center_mass_fraction_avg, "unit": "mass_fraction"},
            {"metric": "c_bulk_center_mol_m3_avg", "value": c_bulk_center_mol_m3_avg, "unit": "mol/m3"},

            {"metric": "c_b_window_mol_m3", "value": c_b_window_mol_per_m3, "unit": "mol/m3"},
            {"metric": "c_b_window_salt_field", "value": _midplane_salt_field or "", "unit": "-"},
            {
                "metric": "c_b_window_is_mass_fraction_field",
                "value": _midplane_salt_is_mass_fraction,
                "unit": "-",
            },

            {"metric": "pp_salt_mass_fraction_cells_above_threshold", "value": concentration_diagnostics["pp_salt_mass_fraction_cells_above_threshold"], "unit": "cells"},
            {"metric": "pp_salt_mass_fraction_cells_below_threshold", "value": concentration_diagnostics["pp_salt_mass_fraction_cells_below_threshold"], "unit": "cells"},
            {"metric": "pp_salt_mass_fraction_max", "value": concentration_diagnostics["pp_salt_mass_fraction_max"], "unit": "mass_fraction"},
            {"metric": "pp_salt_mass_fraction_min", "value": concentration_diagnostics["pp_salt_mass_fraction_min"], "unit": "mass_fraction"},
            {"metric": "concentration_diagnostic_error", "value": concentration_diagnostic_error, "unit": "-"},
            {"metric": "concentration_diagnostic_error_type", "value": concentration_diagnostic_error_type, "unit": "-"},
            {"metric": "concentration_diagnostic_error_message", "value": concentration_diagnostic_error_message, "unit": "-"},
            {"metric": "c_bulk_center_diagnostic", "value": _c_bulk_center_diag, "unit": "-"},
            {
                "metric": "report_definition_errors_json",
                "value": (
                    json.dumps(failed_report_specs, ensure_ascii=False)
                    if failed_report_specs
                    else ""
                ),
                "unit": "-",
            },
            {
                "metric": "report_compute_errors_json",
                "value": json.dumps(
                    {
                        name: result["error"]
                        for name, result in raw_results.items()
                        if isinstance(result, dict) and "error" in result
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "unit": "-",
            },
            {"metric": "segmented_cp_diagnostic_error", "value": segmented_cp_diagnostic_error, "unit": "-"},
            {"metric": "segmented_cp_diagnostic_error_type", "value": segmented_cp_diagnostic_error_type, "unit": "-"},
            {"metric": "segmented_cp_diagnostic_error_message", "value": segmented_cp_diagnostic_error_message, "unit": "-"},
        ]
        summary_rows.extend(unit_cell_summary_rows)
        if turbulence_metrics is not None:
            summary_rows.extend(turbulence_summary_rows(turbulence_metrics))
        summary_rows.extend(concentration_cp_summary_rows(concentration_cp_metrics))
        if membrane_y1_metrics is not None:
            summary_rows.extend(y1_window_summary_rows(membrane_y1_metrics))

        # ----------------------------------------------------------
        # Mass balance table
        # ----------------------------------------------------------

        mass_balance_rows = [
            {"metric": "m_in", "value": m_in, "unit": "kg/s"},
            {"metric": "m_out", "value": m_out, "unit": "kg/s"},
            {
                "metric": "m_in_with_sources",
                "value": m_in_with_sources,
                "unit": "kg/s",
            },
            {
                "metric": "m_out_with_sources",
                "value": m_out_with_sources,
                "unit": "kg/s",
            },
            {
                "metric": "m_in_mass_source",
                "value": m_in_mass_source,
                "unit": "kg/s",
            },
            {
                "metric": "m_out_mass_source",
                "value": m_out_mass_source,
                "unit": "kg/s",
            },
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
        pressure_rows.extend(unit_cell_pressure_rows)

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

        if report_profile == PROFILE_MFBO:
            summary_rows.append(
                {"metric": "profile", "value": PROFILE_MFBO, "unit": "-"}
            )
            summary_rows = filter_mfbo_summary_rows(summary_rows)
            mass_balance_rows = filter_mfbo_summary_rows(mass_balance_rows)
            pressure_rows = filter_mfbo_summary_rows(pressure_rows)
            shear_rows = filter_mfbo_summary_rows(shear_rows)

        def _metric_frame(rows):
            frame = pd.DataFrame(rows)
            if frame.empty:
                return pd.DataFrame(columns=["metric", "value", "unit"])
            return frame

        summary_df = _metric_frame(summary_rows)
        mass_balance_df = _metric_frame(mass_balance_rows)
        pressure_df = _metric_frame(pressure_rows)
        shear_df = _metric_frame(shear_rows)

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

        summary_wide_df = pd.DataFrame([
            summary_rows_to_wide_record(summary_rows)
        ])

        # Keep geometry/case columns near the front if they exist.
        front_columns = ["geo_name", "case_name"]
        existing_front_columns = [col for col in front_columns if col in summary_wide_df.columns]
        other_columns = sorted(
            col for col in summary_wide_df.columns
            if col not in existing_front_columns
        )
        summary_wide_df = summary_wide_df[existing_front_columns + other_columns]

        wide_record = summary_wide_df.iloc[0].to_dict()

        summary_wide_df.to_csv(summary_wide_csv_path, index=False, encoding="utf-8-sig")

        # Post-hoc convergence quality (independent of stop_reason).
        continuity_final = continuity_final_from_case_dir(case_path)
        quality_result = evaluate_convergence_quality(
            wide_record,
            continuity_final=continuity_final,
            evaluation_cell_numbers=evaluation_cells,
        )

        # ----------------------------------------------------------
        # Save raw report values for debugging/reproducibility
        # ----------------------------------------------------------

        raw_save_dict = {
            "case_info": {
                "geo_name": geo_name,
                "case_name": case_name,
                "case_path": str(case_path),
                "case_path_provenance": case_path_provenance,
                "final_case_file": str(final_case_file),
                "final_case_file_provenance": final_case_file_provenance,
                "final_data_file": str(final_data_file),
                "final_data_file_provenance": final_data_file_provenance,
            },
            "computed_values": computed_values,
            "failed_report_specs": failed_report_specs,
            "derived_values": {
                "boundary_permeate_mass_flow": boundary_permeate_mass_flow,
                "mass_balance_error": mass_balance_error,
                "mass_balance_relative_error": mass_balance_relative_error,
                "lmh_difference_mass_balance_minus_udm": lmh_difference,
                "lmh_relative_difference": lmh_relative_difference,
                "lmh_mass_balance_signed_python": lmh_mass_balance_signed_python,
                "m_in_with_sources": m_in_with_sources,
                "m_out_with_sources": m_out_with_sources,
                "m_in_mass_source": m_in_mass_source,
                "m_out_mass_source": m_out_mass_source,
                "convergence_quality": quality_result["convergence_quality"],
                "needs_longer_solve": quality_result["needs_longer_solve"],
                "convergence_quality_failures": quality_result["failures"],
                "convergence_quality_warnings": quality_result["warnings"],
                "continuity_final": quality_result["continuity_final"],
                "pp_pressure_drop_rel_spread_window": quality_result[
                    "pp_pressure_drop_rel_spread_window"
                ],
                "pp_pressure_drop_rel_spread_cells_4_7": quality_result[
                    "pp_pressure_drop_rel_spread_cells_4_7"
                ],
                "pp_pressure_drop_rel_spread_note": quality_result[
                    "pp_pressure_drop_rel_spread_note"
                ],
                "domain_length_m": domain_length_m,
                "pressure_drop_per_m": pressure_drop_per_m,
                "spacer_x_in_m": spacer_x_in_m,
                "spacer_x_out_m": spacer_x_out_m,
                "spacer_length_m": spacer_length_m,
                "pressure_drop_spacer_per_m": pressure_drop_spacer_per_m,
                "unit_cell_boundary_x_m": unit_cell_boundary_x_m,
                "spacer_cell_numbers": spacer_cells,
                "unit_cell_metrics": unit_cell_derived_metrics,
                "n_inlet_spacer_cells_excluded": n_inlet_spacer_cells_excluded,
                "n_lead_excluded": evaluation_window.n_lead_excluded,
                "n_trail_excluded": evaluation_window.n_trail_excluded,
                "periodic_pressure_metrics": periodic_pressure_metrics,
                "wall_shear_rate_avg": wall_shear_rate_avg,
                "wall_shear_rate_max": wall_shear_rate_max,
                "wall_shear_rate_min": wall_shear_rate_min,
                "c_bulk_center_area_avg": c_bulk_center_area_avg,
                "c_bulk_center_area_avg_source": c_bulk_center_area_avg_source,
                "c_bulk_center_area_avg_units_or_type": c_bulk_center_area_avg_units_or_type,
                "c_bulk_center_mass_fraction_avg": c_bulk_center_mass_fraction_avg,
                "c_bulk_center_mol_m3_avg": c_bulk_center_mol_m3_avg,
                "c_bulk_center_plane_name": c_bulk_center_plane_name,
                "c_bulk_center_diag": _c_bulk_center_diag,
                "salt_mass_fraction_upper_threshold": salt_mass_fraction_upper_threshold,
                "salt_mass_fraction_lower_threshold": salt_mass_fraction_lower_threshold,
                "concentration_range_diagnostics": concentration_diagnostics,
                "concentration_diagnostic_error": concentration_diagnostic_error,
                "concentration_diagnostic_error_type": concentration_diagnostic_error_type,
                "concentration_diagnostic_error_message": concentration_diagnostic_error_message,
                "segmented_cp_values": segmented_cp_values,
                "segmented_cp_diagnostic_error": segmented_cp_diagnostic_error,
                "segmented_cp_diagnostic_error_type": segmented_cp_diagnostic_error_type,
                "segmented_cp_diagnostic_error_message": segmented_cp_diagnostic_error_message,
                "turbulence_metrics": turbulence_metrics,
                "concentration_cp": concentration_cp_metrics,
                "membrane_y1": membrane_y1_metrics,
            },
            "raw_results": raw_results,
        }

        if report_profile == PROFILE_MFBO:
            raw_save_dict["case_info"]["profile"] = PROFILE_MFBO

        with open(raw_report_json_path, "w", encoding="utf-8") as f:
            json.dump(
                raw_save_dict,
                f,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        # Campaign validation gates run after artifacts are written so failed
        # extracts still leave inspectable CSV/JSON (report_compute_errors_json,
        # blank cells, etc.). Non-zero exit marks batch_postprocess FAILED.
        if report_profile == PROFILE_MFBO:
            require_mfbo_summary_columns(wide_record)
            require_canonical_cp_summary_columns(wide_record)
        else:
            require_load_bearing_summary_columns(wide_record)
            require_canonical_cp_summary_columns(wide_record)
            require_extract_identities(
                wide_record,
                active_cell_numbers=layout.active_cell_numbers(),
            )

        try:
            update_run_manifest_fields(
                case_path,
                {
                    **manifest_quality_payload(quality_result),
                    **_evaluation_window_selection.manifest_fields(),
                },
            )
            print(
                "Convergence quality    :",
                quality_result["convergence_quality"],
                (
                    f"(failures={quality_result['failures']})"
                    if quality_result["failures"]
                    else ""
                ),
                (
                    f"(warnings={quality_result['warnings']})"
                    if quality_result["warnings"]
                    else ""
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                "could not write convergence_quality to run manifest: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        write_extract_source_record(
            report_path,
            {
                FINAL_CASE_SHA256_FIELD: sha256_file(final_case_file),
                FINAL_DATA_SHA256_FIELD: sha256_file(final_data_file),
                SOLVER_ATTEMPT_ID_FIELD: run_manifest.get(SOLVER_ATTEMPT_ID_FIELD),
            },
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

        if segmented_cp_values:
            print("\nCP definition metrics (evaluation window):")
            for key in (
                "cp_canon_window_avg",
                "cp_canon_window_max",
                "cp_L1_window_avg",
                "cp_L1_window_max",
                "cp_L2_window_avg",
                "cp_L2_window_max",
                "cp_canon_all_active_avg",
                "cp_canon_all_active_max",
                "c_b_window_mol_m3",
                "cp_canon_rescale_delta_max",
                "cp_canon_rescale_delta_status",
                "cp_scalar_rescale_guard_threshold",
                "cp_membrane_segment_fluent_computes",
                "compute_cp_spread",
            ):
                print(f"  {key}: {segmented_cp_values.get(key)}")

        print("\nSummary wide table:")
        display(summary_wide_df)

    except Exception as e:
        _abandon_active_extract_phase()
        print("\n" + "=" * 72)
        print("ERROR: PyFluent post-processing report extraction failed.")
        print("=" * 72)
        print(e)
        print("=" * 72 + "\n")
        raise

    finally:
        try:
            extraction_wall_time_s = _write_report_extract_timing(report_path)
        except Exception as timing_error:
            print(
                "Warning: could not write report_extract_timing.json: "
                f"{timing_error}"
            )
        else:
            if extraction_wall_time_s is not None:
                try:
                    stamp_extraction_wall_time_on_run_manifest(
                        case_path,
                        extraction_wall_time_s,
                    )
                except Exception as timing_error:
                    print(
                        "Warning: could not write extraction_wall_time_s "
                        f"to the run manifest: {timing_error}"
                    )

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
