# ==========================================================
# ##### [0] Import Required Packages #####
# ==========================================================
import ansys.fluent.core as pyfluent
import os
import shutil
import re
import json
import sys
import math
import time
import uuid
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from ro.campaign_geometry import merge_geometry_into_run_manifest
from ro.manifest import (
    MANIFEST_SCHEMA_VERSION,
    read_mesh_manifest,
    read_run_manifest,
    write_mesh_manifest,
    write_run_manifest,
)
from ro.solver_common import normalize_path, path_to_fluent_str as as_fluent_path
from ro.solver_common import (
    SOLVER_EXIT_ARTIFACT_FAILURE,
    SOLVER_EXIT_SUCCESS,
    STOP_REASON_DETERMINATION_FAILED,
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    classify_solver_stop_reason,
    collect_solver_final_artifact_failures,
    fluent_report_relative_window_met,
    format_stop_reason_marker,
    parse_fluent_convergence_marker,
    first_fluent_report_file_iteration,
    last_fluent_report_file_iteration,
    parse_fluent_report_file_series,
    parse_first_residual_iteration_from_transcript_text,
    parse_last_residual_iteration_from_transcript_text,
    require_mesh_sha256_matches_file,
    require_run_id_matches_operating_point,
    require_u_mean_profile_identity,
    resolve_solver_final_artifact_exit_code,
    sha256_file,
)
from ro.domain_layout import (
    layout_from_mesh_manifest,
    require_mesh_manifest_x_extent_matches_layout,
)
from ro.manifest_validation import (
    require_campaign_membrane_blocked_area_frac,
    require_mesh_run_blocked_frac_agree,
)
from ro.lmh_metrics import lmh_mass_balance_expression
from ro.fluent_report_helpers import create_x_normal_plane
from ro.paths import mesh_dir, project_root, run_dir, templates_dir, udfs_dir
from ro.udm_layout import (
    parse_ro_analytic_cwall_from_case,
    parse_ro_analytic_cwall_from_udf_path,
)


def _utc_now_string():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_run_manifest_payload(cfg, mesh_manifest, *, created_utc=None, solver_attempt_id=None):
    inlet_bc_type = (
        "parabolic" if bool(cfg.use_inlet_velocity_profile) else "plug"
    )
    u_target_ms = float(cfg.inlet_velocity_value)
    u_mean_ms = u_target_ms if inlet_bc_type == "plug" else None
    udf_path = udfs_dir() / cfg.udf_source_file_name
    analytic_cwall = parse_ro_analytic_cwall_from_udf_path(udf_path)
    base = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "family": cfg.family,
        "geo_id": cfg.geo_id,
        "mesh_id": cfg.mesh_id,
        "mesh_sha256": mesh_manifest["mesh_sha256"],
        "run_id": cfg.run_id,
        "u_mean_ms": u_mean_ms,
        "p_gauge_pa": cfg.outlet_gauge_pressure,
        "u_target_ms": u_target_ms,
        "inlet_bc_type": inlet_bc_type,
        "udf_version": cfg.udf_source_file_name,
        "analytic_cwall": analytic_cwall,
        "solver_settings": {
            "max_iterations": cfg.max_iterations,
            "residual_target": cfg.residual_target,
            "operating_pressure": cfg.operating_pressure,
        },
        "stop_reason": "RUNNING",
        "created_utc": created_utc or _utc_now_string(),
        SOLVER_ATTEMPT_ID_FIELD: solver_attempt_id or str(uuid.uuid4()),
    }
    verbosity = getattr(cfg, "pseudo_time_verbosity", "preserve")
    scale_factor = getattr(
        cfg, "pseudo_time_time_step_size_scale_factor", "preserve"
    )
    if not _config_is_preserve(verbosity):
        base["solver_settings"]["pseudo_time_verbosity"] = verbosity
    if not _config_is_preserve(scale_factor):
        base["solver_settings"]["pseudo_time_time_step_size_scale_factor"] = (
            scale_factor
        )
    return merge_geometry_into_run_manifest(
        base,
        cfg.geo_id,
        mesh_id=cfg.mesh_id,
    )


def write_worker_run_manifest(
    cfg,
    mesh_directory,
    run_directory,
    *,
    created_utc=None,
):
    mesh_manifest = read_mesh_manifest(mesh_directory)
    require_campaign_membrane_blocked_area_frac(mesh_manifest, kind="Mesh")
    payload = build_run_manifest_payload(
        cfg,
        mesh_manifest,
        created_utc=created_utc,
    )
    require_campaign_membrane_blocked_area_frac(payload, kind="Run")
    require_mesh_run_blocked_frac_agree(mesh_manifest, payload)
    require_run_id_matches_operating_point(
        payload["run_id"],
        payload["u_target_ms"],
        payload["p_gauge_pa"],
    )
    existing_path = Path(run_directory) / "manifest.json"
    if existing_path.is_file():
        existing = read_run_manifest(run_directory)
        if existing["u_mean_ms"] is not None and payload["u_mean_ms"] is None:
            payload["u_mean_ms"] = existing["u_mean_ms"]
    require_u_mean_profile_identity(
        payload.get("u_mean_ms"),
        mesh_manifest.get("inlet_profile_G"),
        payload["u_target_ms"],
        inlet_bc_type=payload["inlet_bc_type"],
    )
    return write_run_manifest(run_directory, payload)


def _resolve_analytic_cwall_for_run(run_directory, udf_version: str) -> int:
    """Prefer case-local UDF; fall back to repo udfs/ copy by manifest name."""
    directory = Path(run_directory)
    try:
        return parse_ro_analytic_cwall_from_case(directory)
    except FileNotFoundError:
        return parse_ro_analytic_cwall_from_udf_path(udfs_dir() / udf_version)


def finalize_worker_run_manifest(run_directory, stop_reason, *, inlet_profile_g=None):
    payload = read_run_manifest(run_directory)
    payload["stop_reason"] = stop_reason
    payload["analytic_cwall"] = _resolve_analytic_cwall_for_run(
        run_directory,
        payload["udf_version"],
    )
    if payload["inlet_bc_type"] == "parabolic":
        if inlet_profile_g is None:
            if stop_reason != STOP_REASON_DETERMINATION_FAILED:
                raise RuntimeError(
                    "Cannot finalize a parabolic run manifest without "
                    "inlet_profile_G from fluent-*.trn."
                )
        elif payload["u_mean_ms"] is None:
            payload["u_mean_ms"] = (
                float(payload["u_target_ms"]) / float(inlet_profile_g)
            )
        require_u_mean_profile_identity(
            payload["u_mean_ms"],
            inlet_profile_g,
            payload["u_target_ms"],
            inlet_bc_type=payload["inlet_bc_type"],
        )
    return write_run_manifest(run_directory, payload)


def failed_attempt_case_path(run_directory, attempt_id):
    return Path(run_directory) / f"failed_attempt_{attempt_id}.cas.h5"


def isolate_failed_attempt_write(solver, run_directory, *, as_fluent_path):
    """Write case/data to an attempt-scoped name, not the canonical finals."""
    payload = read_run_manifest(run_directory)
    attempt_id = payload.get(SOLVER_ATTEMPT_ID_FIELD) or "unknown"
    dest = failed_attempt_case_path(run_directory, attempt_id)
    solver.settings.file.write_case_data(file_name=as_fluent_path(dest))
    print(f"Isolated failed-attempt case/data: {dest}")
    return dest


def stamp_run_manifest_final_artifact_hashes(
    run_directory,
    final_case_file,
    final_data_file,
):
    payload = read_run_manifest(run_directory)
    payload[FINAL_CASE_SHA256_FIELD] = sha256_file(final_case_file)
    payload[FINAL_DATA_SHA256_FIELD] = sha256_file(final_data_file)
    return write_run_manifest(run_directory, payload)


def publish_stop_reason_and_finals(
    solver,
    run_directory,
    solver_stop_reason,
    *,
    inlet_profile_g,
    final_case_file,
    as_fluent_path,
):
    """Finalize the run manifest, then publish canonical finals or isolate.

    ``None`` stop reason becomes ``stop_reason_determination_failed``. That
    path never writes the canonical ``*_final.cas.h5`` pair. Isolated write
    failure is warned and does not replace the determination failure.
    """
    if not solver_stop_reason:
        solver_stop_reason = STOP_REASON_DETERMINATION_FAILED
        print(format_stop_reason_marker(solver_stop_reason))
    run_manifest_path = finalize_worker_run_manifest(
        run_directory,
        solver_stop_reason,
        inlet_profile_g=inlet_profile_g,
    )
    print(
        f"Run manifest finalized with stop_reason={solver_stop_reason}: "
        f"{run_manifest_path}"
    )
    if solver_stop_reason == STOP_REASON_DETERMINATION_FAILED:
        try:
            isolate_failed_attempt_write(
                solver,
                run_directory,
                as_fluent_path=as_fluent_path,
            )
        except Exception as isolate_exc:
            print(
                "WARNING: isolated failed-attempt write failed: "
                f"{type(isolate_exc).__name__}: {isolate_exc}"
            )
        raise RuntimeError("stop_reason_determination_failed")
    solver.settings.file.write_case_data(file_name=as_fluent_path(final_case_file))
    print(f"Final case/data write command completed: {final_case_file}")
    return solver_stop_reason


def resolve_solver_paths(cfg):
    run_directory = run_dir(
        cfg.family,
        cfg.geo_id,
        cfg.mesh_id,
        cfg.run_id,
    )
    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    udf_master = udfs_dir() / cfg.udf_source_file_name
    return {
        "run_directory": run_directory,
        "mesh_directory": mesh_directory,
        "mesh_file": mesh_directory / f"{cfg.geo_id}_{cfg.mesh_id}.msh.h5",
        "template_case": templates_dir() / cfg.template_case_file_name,
        "udf_master": udf_master,
        "udf_case": run_directory / udf_master.name,
    }

# ==========================================================
# ##### [1] Load Run Configuration #####
# ==========================================================

# The script body below runs only when this file is executed directly.
# Importing this module must not launch Fluent or write any files.
if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    _default_config = project_root() / "configs" / "run_config.py"
    _env = os.environ.get("PYFLUENT_RUN_CONFIG")
    CONFIG_PATH = Path(_env or str(_default_config)).resolve()

    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Run config file not found: {CONFIG_PATH}. "
            "Set PYFLUENT_RUN_CONFIG or place run_config.py under <project>/configs/."
        )

    config_spec = importlib.util.spec_from_file_location("active_run_config", CONFIG_PATH)
    cfg = importlib.util.module_from_spec(config_spec)
    config_spec.loader.exec_module(cfg)

    print(f"Loaded run config: {CONFIG_PATH}")

    # Per-case overrides from the batch drivers (JSON dict). Applied to the
    # config module before validation and parameter binding below.
    # PYFLUENT_RUN_CONFIG selects the base module; PYFLUENT_SKIP_VALIDATION=1
    # opts out of validate_for_solver() after overrides are applied.
    _overrides_env = os.environ.get("PYFLUENT_RUN_OVERRIDES")
    if _overrides_env:
        try:
            _overrides = json.loads(_overrides_env)
        except json.JSONDecodeError as e:
            raise ValueError(f"PYFLUENT_RUN_OVERRIDES is not valid JSON: {e}")
        if not isinstance(_overrides, dict):
            raise ValueError("PYFLUENT_RUN_OVERRIDES must be a JSON object.")
        cfg.apply_run_config_overrides(cfg, _overrides)
        print(f"Applied config overrides: {sorted(_overrides)}")

    if cfg.run_config_validation_skipped():
        print("WARNING: Skipping run_config validation (PYFLUENT_SKIP_VALIDATION is set).")
    else:
        cfg.validate_for_solver()

    # Project paths. Artifact names are {geo_id}_{run_id}; labels stay in sync.
    geo_name = cfg.geo_id
    case_name = cfg.run_id

    resolved_paths = resolve_solver_paths(cfg)
    case_path = resolved_paths["run_directory"]
    mesh_case_path = resolved_paths["mesh_directory"]
    mesh_file_path = resolved_paths["mesh_file"]

    def _optional_path_from_config(name):
        value = getattr(cfg, name, None)
        if value is None:
            return None
        if not str(value).strip():
            raise ValueError(f"{name} must be a non-empty path when provided.")
        return Path(value).expanduser()

    restart_from_case_file = _optional_path_from_config("restart_from_case_file")
    restart_from_data_file = _optional_path_from_config("restart_from_data_file")

    has_restart_case_file = restart_from_case_file is not None
    has_restart_data_file = restart_from_data_file is not None

    if has_restart_case_file != has_restart_data_file:
        raise ValueError(
            "restart_from_case_file and restart_from_data_file must be provided together."
        )

    input_mode = "restart_continuation" if has_restart_case_file else "mesh_initialization"

    # Inlet velocity setting
    inlet_velocity_value = cfg.inlet_velocity_value
    inlet_velocity = float(inlet_velocity_value)

    # Template case path.
    # This template case already contains the RO material/species setup.
    template_case_path = resolved_paths["template_case"]

    # Fluent launch settings
    product_version = cfg.product_version
    processor_count = cfg.processor_count
    graphics_driver = cfg.graphics_driver
    fluent_start_timeout = cfg.fluent_start_timeout
    fluent_health_timeout = cfg.fluent_health_timeout

    # UDF source
    udf_master_path = resolved_paths["udf_master"]
    udf_case_path = resolved_paths["udf_case"]
    udf_library_name = cfg.udf_library_name
    use_inlet_velocity_profile = bool(
        getattr(cfg, "use_inlet_velocity_profile", False)
    )
    debug_inlet_bc_api = bool(getattr(cfg, "debug_inlet_bc_api", False))
    inlet_profile_function_name = f"inlet_x_velocity_profile::{udf_library_name}"
    inlet_probe_function_name = f"probe_inlet_profile::{udf_library_name}"
    INLET_PROFILE_MARKER = "=== RO_UDF inlet_x_velocity_profile ==="
    INLET_PROBE_MARKER = "=== RO_UDF probe_inlet_profile ==="

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
    relaxation_profile = cfg.relaxation_profile
    species_implicit_under_relaxation = cfg.species_implicit_under_relaxation
    pseudo_time_verbosity = cfg.pseudo_time_verbosity
    pseudo_time_time_step_size_scale_factor = (
        cfg.pseudo_time_time_step_size_scale_factor
    )

    # Ramp/convergence safety.
    use_ramp_convergence_safety = cfg.use_ramp_convergence_safety
    ramp_full_iteration = cfg.ramp_full_iteration
    post_ramp_buffer_iterations = cfg.post_ramp_buffer_iterations
    minimum_full_source_iterations = ramp_full_iteration + post_ramp_buffer_iterations

    # QoI convergence stop
    enable_qoi_convergence_stop = cfg.enable_qoi_convergence_stop
    qoi_convergence_report_name = cfg.qoi_convergence_report_name
    qoi_stop_criterion = cfg.qoi_stop_criterion
    qoi_previous_values_to_consider = cfg.qoi_previous_values_to_consider
    qoi_initial_values_to_ignore = cfg.qoi_initial_values_to_ignore
    enable_lmh_udm_avg_report_file = cfg.enable_lmh_udm_avg_report_file
    lmh_udm_avg_report_file_name = cfg.lmh_udm_avg_report_file_name
    enable_pressure_drop_spacer_report_file = (
        cfg.enable_pressure_drop_spacer_report_file
    )
    pressure_drop_spacer_report_file_name = (
        cfg.pressure_drop_spacer_report_file_name
    )

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
    lmh_signed_report_name = cfg.lmh_signed_report_name
    m_in_report_name = cfg.m_in_report_name
    m_out_report_name = cfg.m_out_report_name
    enable_solve_time_qoi_reports = cfg.enable_solve_time_qoi_reports
    domain_x_min_m = cfg.domain_x_min_m

# ==========================================================
# ##### [2] Helper Functions #####
# ==========================================================

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


def fluent_mass_diffusivity_value(state):
    """Extract the numeric mixture mass diffusivity from a Fluent get_state dict.

    Raises if the state is missing or not a numeric `value`. The solver does
    not write this property; it only reads the template.
    """
    if not isinstance(state, dict):
        raise TypeError(
            f"Mixture mass diffusivity state is {type(state).__name__}, "
            f"expected dict. State={state!r}"
        )
    if "value" not in state:
        raise ValueError(
            f"Mixture mass diffusivity state has no 'value' key: {state!r}"
        )
    try:
        return float(state["value"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Mixture mass diffusivity 'value' is not numeric: {state!r}"
        ) from exc


def assert_fluent_mass_diffusivity_matches_config(
    state,
    expected,
    *,
    rel_tol=1e-9,
):
    """Raise if the template diffusivity does not match run_config."""
    actual = fluent_mass_diffusivity_value(state)
    expected_value = float(expected)
    option = state.get("option")
    if option is not None and option != "constant-dilute-appx":
        raise ValueError(
            f"Fluent mixture mass diffusivity option is {option!r}, "
            f"expected 'constant-dilute-appx'. State={state!r}. "
            "UDF D_SALT is a constant and only matches that setting."
        )
    if not math.isclose(actual, expected_value, rel_tol=rel_tol, abs_tol=0.0):
        raise ValueError(
            f"Fluent mixture mass diffusivity {actual!r} does not match "
            f"run_config.mass_diffusivity {expected_value!r}. State={state!r}. "
            "The solver does not write this property; the template, "
            "run_config.mass_diffusivity, and UDF D_SALT must stay in lockstep."
        )
    return actual


def replace_define_real(text, macro_name, new_value):
    """Replace a real/float #define value in a C source string.

    Matches one decimal or scientific literal after `#define NAME`.
    Raises if the macro is missing or appears more than once.
    """
    formatted = format(float(new_value), ".10g")
    pattern = (
        rf"(^\s*#define\s+{re.escape(macro_name)}\s+)"
        rf"([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)"
        rf"(.*$)"
    )
    replacement = rf"\g<1>{formatted}\g<3>"

    found = list(re.finditer(pattern, text, flags=re.MULTILINE))
    if len(found) != 1:
        raise ValueError(
            f'Could not find exactly one real macro definition for "{macro_name}".'
        )

    new_text, count = re.subn(
        pattern,
        replacement,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(
            f'Could not find exactly one real macro definition for "{macro_name}".'
        )

    return new_text


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


def fluent_directory_text_is_absolute(path_text):
    """True for POSIX abs paths and Windows drive/UNC paths on any host."""
    text = str(path_text).strip()
    if os.path.isabs(text):
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))


def fluent_working_directory_error(
    actual,
    expected_case_path,
    *,
    restart_source_dir=None,
):
    """Return a RuntimeError message, or None when cwd is the target folder."""
    if actual is None or not str(actual).strip():
        return "Fluent working directory is empty"
    actual_text = str(actual).strip()
    if not fluent_directory_text_is_absolute(actual_text):
        return (
            f"Fluent working directory {actual!r} is not an absolute path"
        )
    actual_n = normalize_path(actual_text)
    expected_n = normalize_path(expected_case_path)
    if restart_source_dir is not None:
        source_n = normalize_path(restart_source_dir)
        if actual_n == source_n:
            return (
                "Fluent working directory is the restart source folder "
                f"{restart_source_dir!r}; refusing to write into a finished "
                f"case. Expected target case_path {expected_case_path!r}."
            )
    if actual_n != expected_n:
        return (
            f"Fluent working directory {actual!r} is not the target case_path "
            f"{expected_case_path!r}."
        )
    return None


def restart_staged_case_path(case_path, geo_name, run_id):
    """Target-leaf copy of the source final case, named for the new run_id."""
    return os.path.join(case_path, f"{geo_name}_{run_id}_restart_from.cas.h5")


def restart_staged_data_path(case_path, geo_name, run_id):
    """Target-leaf copy of the source final data, named for the new run_id."""
    return os.path.join(case_path, f"{geo_name}_{run_id}_restart_from.dat.h5")


def copy_restart_source_into_case_folder(
    source_case_file,
    source_data_file,
    staged_case_file,
    staged_data_file,
):
    """Copy only the two source finals into the target leaf. Never the folder."""
    if normalize_path(source_case_file) == normalize_path(staged_case_file):
        raise RuntimeError(
            "Restart staging case path collides with the source file: "
            f"{source_case_file}"
        )
    if normalize_path(source_data_file) == normalize_path(staged_data_file):
        raise RuntimeError(
            "Restart staging data path collides with the source file: "
            f"{source_data_file}"
        )
    dest_dir = os.path.dirname(os.path.abspath(staged_case_file))
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(source_case_file, staged_case_file)
    shutil.copy2(source_data_file, staged_data_file)
    print(f"Copied restart case into target leaf: {staged_case_file}")
    print(f"Copied restart data into target leaf: {staged_data_file}")
    return staged_case_file, staged_data_file


def require_restart_read_not_source_folder(
    read_case_file,
    read_data_file,
    case_path,
    restart_source_dir,
    *,
    source_case_file=None,
    source_data_file=None,
):
    """Raise if Fluent would open files in the restart source folder."""
    for path, label in (
        (read_case_file, "case"),
        (read_data_file, "data"),
    ):
        parent = os.path.dirname(os.path.abspath(path))
        error = fluent_working_directory_error(
            parent,
            case_path,
            restart_source_dir=restart_source_dir,
        )
        if error:
            raise RuntimeError(
                f"Restart {label} {path} would not keep Fluent in the target "
                f"case folder: {error}"
            )
    if source_case_file is not None and (
        normalize_path(read_case_file) == normalize_path(source_case_file)
    ):
        raise RuntimeError(
            "Restart would open the source case file "
            f"{source_case_file}; refusing to read a finished case in place."
        )
    if source_data_file is not None and (
        normalize_path(read_data_file) == normalize_path(source_data_file)
    ):
        raise RuntimeError(
            "Restart would open the source data file "
            f"{source_data_file}; refusing to read a finished case in place."
        )
    print(
        "Restart read confirmed in target case folder: "
        f"{read_case_file}, {read_data_file}"
    )


def remove_staged_restart_copies(*paths):
    """Delete target-leaf restart copies after canonical finals exist."""
    for path in paths:
        if not path:
            continue
        if os.path.isfile(path):
            os.remove(path)
            print(f"Removed staged restart copy: {path}")


def copy_and_patch_udf_to_case_folder(
    source_path,
    destination_path,
    salt_yi_index_value,
    u_target_value,
):
    """Copy UDF to the case folder and patch SALT_YI_INDEX and U_TARGET.

    Rewrites only destination_path (the case-local copy). The master under
    udfs/ is never modified.

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
    udf_text = replace_define_real(
        udf_text,
        "U_TARGET",
        u_target_value,
    )

    with open(destination_path, "w", encoding="utf-8", newline="\n") as file:
        file.write(udf_text)

    print(f"Patched SALT_YI_INDEX = {salt_yi_index_value}")
    print(f"Patched U_TARGET = {u_target_value}")
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


def zone_discovery_log_text(boundary_zone_names, wall_spacer_zones):
    """Same two lines the spacer-gate log parser reads."""
    return (
        f"All boundary zones: {list(boundary_zone_names)}\n"
        f"Detected spacer wall zones: {list(wall_spacer_zones)}\n"
    )


def append_zone_discovery_to_solver_log(
    solver_log_path,
    boundary_zone_names,
    wall_spacer_zones,
):
    """Append zone lists to solver_log_*.txt. Python print() is not in the transcript."""
    text = zone_discovery_log_text(boundary_zone_names, wall_spacer_zones)
    with open(solver_log_path, "a", encoding="utf-8") as log_handle:
        log_handle.write(text)
        if not text.endswith("\n"):
            log_handle.write("\n")


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


def _transcript_path_diag(path):
    """Return a one-line path/size/mtime diagnostic for assert failures."""
    path = Path(path)
    if not path.is_file():
        return f"{path}: missing"
    try:
        st = path.stat()
    except OSError as exc:
        return f"{path}: stat failed ({exc})"
    return f"{path}: size={st.st_size} bytes, mtime={st.st_mtime}"


def _marker_in_transcript_file(path):
    """Return file text if readable, else None."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        if path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def list_solver_transcript_paths(case_dir, solver_log_path):
    """Return fluent-*.trn (newest mtime first), then the PyFluent solver log."""
    case_dir = Path(case_dir)
    solver_log_path = Path(solver_log_path)
    trn_files = []
    try:
        trn_files = [p for p in case_dir.glob("fluent-*.trn") if p.is_file()]
    except OSError:
        trn_files = []
    trn_files.sort(
        key=lambda p: p.stat().st_mtime if p.is_file() else 0.0,
        reverse=True,
    )
    return list(trn_files) + [solver_log_path]


def read_solver_transcripts(case_dir, solver_log_path):
    """Yield (path, text) for non-empty transcripts in search order."""
    results = []
    for path in list_solver_transcript_paths(case_dir, solver_log_path):
        text = _marker_in_transcript_file(path)
        if text is None:
            continue
        results.append((path, text))
    return results


def select_solve_transcript(case_dir, solver_log_path):
    """Pick the newest transcript that has a convergence marker or residual table.

    Newest fluent-*.trn first, then the PyFluent solver log. The first file
    that looks like a solve transcript is used so an older rerun's marker is
    not preferred over a newer max-iter file with no marker.
    """
    for path, text in read_solver_transcripts(case_dir, solver_log_path):
        marker_reason, _marker_iter = parse_fluent_convergence_marker(text)
        table_iter = parse_last_residual_iteration_from_transcript_text(text)
        if marker_reason or table_iter is not None:
            return path, text
    return None, ""


def assert_transcript_contains(
    marker,
    context,
    *,
    case_dir,
    solver_log_path,
    poll_interval_s=0.5,
    timeout_s=30.0,
):
    """Raise if a required UDF marker is missing from Fluent transcripts.

    Primary source is Fluent's own fluent-*.trn in the case folder (newest
    mtime first). The PyFluent-managed solver_log_*.txt is a secondary source
    only when non-empty. Polls and re-reads until the marker appears or
    timeout_s elapses.

    If libudf is not loaded when a UDF BC is set, Fluent can silently drop the
    hook and keep a constant panel value — a 0 m/s run can still converge.
    """
    deadline = time.monotonic() + float(timeout_s)

    while True:
        search_paths = list_solver_transcript_paths(case_dir, solver_log_path)
        for path in search_paths:
            text = _marker_in_transcript_file(path)
            if text is None:
                continue
            if marker in text:
                print(
                    f"Transcript marker OK for {context}: {marker!r} "
                    f"(found in {path})"
                )
                return

        if time.monotonic() >= deadline:
            diag_lines = [_transcript_path_diag(p) for p in search_paths]
            raise RuntimeError(
                f"Missing required transcript marker for {context}: {marker!r} "
                f"after {timeout_s:g}s. Searched (fluent-*.trn newest first, "
                f"then solver_log if present):\n  "
                + "\n  ".join(diag_lines)
                + ". UDF may not have loaded or executed."
            )

        time.sleep(float(poll_interval_s))


INLET_PROFILE_G_TOKEN_RE = re.compile(r"RO_UDF_INLET_PROFILE_G=([^\s]+)")
INLET_PROFILE_G_REL_TOL = 1e-6
INLET_PROBE_MARKER = "=== RO_UDF probe_inlet_profile ==="
PROBE_U_TARGET_RE = re.compile(
    r"U_TARGET\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*m/s"
)


def list_fluent_trn_paths(case_dir, solver_log_path):
    """Return fluent-*.trn only. Never solver_log_*.txt."""
    return [
        path
        for path in list_solver_transcript_paths(case_dir, solver_log_path)
        if path.name.startswith("fluent-") and path.suffix == ".trn"
    ]


def collect_inlet_profile_g_tokens(case_dir, solver_log_path):
    """All RO_UDF_INLET_PROFILE_G tokens from fluent-*.trn, never solver_log."""
    tokens = []
    for path in list_fluent_trn_paths(case_dir, solver_log_path):
        text = _marker_in_transcript_file(path)
        if text is None:
            continue
        tokens.extend(INLET_PROFILE_G_TOKEN_RE.findall(text))
    return tokens


def agreed_inlet_profile_g(tokens):
    """Require at least one token; all tokens must be identical strings."""
    if not tokens:
        raise RuntimeError(
            "Missing required transcript marker RO_UDF_INLET_PROFILE_G in "
            "fluent-*.trn. UDF may not have loaded, or G fell back to 1."
        )
    unique = list(dict.fromkeys(tokens))
    if len(unique) != 1:
        raise RuntimeError(
            "Disagreeing RO_UDF_INLET_PROFILE_G values in fluent-*.trn: "
            f"{tokens!r}. The inlet profile changed mid-session."
        )
    try:
        value = float(unique[0])
    except ValueError as exc:
        raise RuntimeError(
            f"Could not parse RO_UDF_INLET_PROFILE_G token {unique[0]!r}."
        ) from exc
    if not (value > 0.0) or value != value:
        raise RuntimeError(
            f"RO_UDF_INLET_PROFILE_G token {unique[0]!r} is not a positive finite G."
        )
    return value


def wait_for_agreed_inlet_profile_g(
    *,
    case_dir,
    solver_log_path,
    poll_interval_s=0.5,
    timeout_s=30.0,
):
    """Poll fluent-*.trn for G. Raise on disagreement immediately; missing is an error."""
    deadline = time.monotonic() + float(timeout_s)
    while True:
        tokens = collect_inlet_profile_g_tokens(case_dir, solver_log_path)
        if tokens:
            value = agreed_inlet_profile_g(tokens)
            print(
                f"Transcript marker OK for inlet_profile_G: "
                f"RO_UDF_INLET_PROFILE_G={value:.12g} "
                f"({len(tokens)} occurrence(s) in fluent-*.trn)"
            )
            return value
        if time.monotonic() >= deadline:
            search_paths = list_fluent_trn_paths(case_dir, solver_log_path)
            diag_lines = [_transcript_path_diag(p) for p in search_paths]
            if not diag_lines:
                diag_lines = ["(no fluent-*.trn files)"]
            raise RuntimeError(
                "Missing required transcript marker RO_UDF_INLET_PROFILE_G in "
                f"fluent-*.trn after {timeout_s:g}s. solver_log_*.txt is not "
                "searched. Searched:\n  "
                + "\n  ".join(diag_lines)
                + ". UDF may not have loaded, or G fell back to 1."
            )
        time.sleep(float(poll_interval_s))


def count_inlet_probe_markers(case_dir, solver_log_path):
    """Count probe_inlet_profile banners in fluent-*.trn, never solver_log."""
    count = 0
    for path in list_fluent_trn_paths(case_dir, solver_log_path):
        text = _marker_in_transcript_file(path)
        if text is None:
            continue
        count += text.count(INLET_PROBE_MARKER)
    return count


def parse_last_probe_u_target_token(text):
    """U_TARGET token from the last probe_inlet_profile block, or None."""
    idx = text.rfind(INLET_PROBE_MARKER)
    if idx < 0:
        return None
    match = PROBE_U_TARGET_RE.search(text[idx:])
    if match is None:
        return None
    return match.group(1)


def collect_last_probe_u_target_token(case_dir, solver_log_path):
    """Last probe-block U_TARGET in fluent-*.trn (newest first). Never solver_log."""
    for path in list_fluent_trn_paths(case_dir, solver_log_path):
        text = _marker_in_transcript_file(path)
        if text is None:
            continue
        token = parse_last_probe_u_target_token(text)
        if token is not None:
            return token
    return None


def probe_u_target_mismatch_error(observed, requested):
    if observed is None:
        return "probe U_TARGET missing from fluent-*.trn"
    try:
        observed_value = float(observed)
    except (TypeError, ValueError):
        return f"probe U_TARGET {observed!r} is not a number"
    if not math.isclose(
        observed_value, float(requested), rel_tol=1e-9, abs_tol=1e-12
    ):
        return (
            f"Compiled U_TARGET {observed!r} from probe does not match "
            f"inlet_velocity_value {requested!r}"
        )
    return None


def wait_for_agreed_probe_u_target(
    *,
    case_dir,
    solver_log_path,
    expected,
    min_probe_blocks=1,
    poll_interval_s=0.5,
    timeout_s=30.0,
):
    """Require a new probe block whose U_TARGET matches inlet_velocity_value.

    Restart ``read_case`` can print the source library's U_TARGET before
    compile. The last probe block after compile/load is the compiled constant.
    """
    deadline = time.monotonic() + float(timeout_s)
    last_token = None
    while True:
        n_blocks = count_inlet_probe_markers(case_dir, solver_log_path)
        last_token = collect_last_probe_u_target_token(case_dir, solver_log_path)
        if n_blocks >= int(min_probe_blocks) and last_token is not None:
            error = probe_u_target_mismatch_error(last_token, expected)
            if error:
                raise RuntimeError(error)
            value = float(last_token)
            print(
                f"Transcript marker OK for compiled U_TARGET: "
                f"U_TARGET={value:.12g} m/s "
                f"(matches inlet_velocity_value {float(expected):.12g})"
            )
            return value
        if time.monotonic() >= deadline:
            search_paths = list_fluent_trn_paths(case_dir, solver_log_path)
            diag_lines = [_transcript_path_diag(p) for p in search_paths]
            if not diag_lines:
                diag_lines = ["(no fluent-*.trn files)"]
            raise RuntimeError(
                "Missing compiled U_TARGET in fluent-*.trn probe block after "
                f"{timeout_s:g}s (need {min_probe_blocks} probe block(s), "
                f"last token {last_token!r}). solver_log_*.txt is not "
                "searched. Searched:\n  "
                + "\n  ".join(diag_lines)
            )
        time.sleep(float(poll_interval_s))


def apply_parsed_inlet_profile_g(
    mesh_directory,
    g,
    *,
    u_mean_ms=None,
    u_target_ms=None,
    inlet_bc_type="parabolic",
):
    """Lazy-fill mesh inlet_profile_G once; later runs compare at 1e-6 relative."""
    payload = read_mesh_manifest(mesh_directory)
    stored = payload["inlet_profile_G"]
    g = float(g)
    if stored is None:
        payload["inlet_profile_G"] = g
        write_mesh_manifest(mesh_directory, payload)
        print(f"Mesh manifest inlet_profile_G filled: {g:.12g}")
        agreed = g
    else:
        stored = float(stored)
        relative = abs(g - stored) / abs(stored)
        if relative > INLET_PROFILE_G_REL_TOL:
            raise RuntimeError(
                f"inlet_profile_G mismatch: transcript {g:.12g} vs mesh manifest "
                f"{stored:.12g} (relative {relative:.3g} > {INLET_PROFILE_G_REL_TOL}). "
                "Aborting before iterate."
            )
        print(
            f"Mesh manifest inlet_profile_G matches transcript "
            f"({stored:.12g}, relative {relative:.3g})."
        )
        agreed = stored
    require_u_mean_profile_identity(
        u_mean_ms,
        agreed,
        u_target_ms,
        inlet_bc_type=inlet_bc_type,
    )
    return agreed


def unwrap_fluent_setting(raw):
    """Flatten a PyFluent settings leaf to the stored value.

    Fluent 25.1.0 ``get_state()`` on ``velocity_magnitude`` returns
    ``{'option': 'value', 'value': 0.2}`` (D0817_a60/a30 solver logs).
    Nested dicts that still have a ``value`` key are unwrapped the same
    way. Scalars and other types pass through unchanged.
    """
    current = raw
    for _ in range(8):
        if isinstance(current, dict) and "value" in current:
            current = current["value"]
            continue
        return current
    return current


def _inlet_option_leaf(raw):
    """x-velocity option: wrapped ``value`` leaf, or ``{'option': 'udf', ...}``."""
    raw = unwrap_fluent_setting(raw)
    if isinstance(raw, dict) and "option" in raw:
        return raw["option"]
    return raw


def _inlet_udf_leaf(raw):
    """x-velocity UDF name: wrapped ``value`` leaf, or ``{'option': 'udf', 'udf': ...}``."""
    raw = unwrap_fluent_setting(raw)
    if isinstance(raw, dict) and "udf" in raw:
        return raw["udf"]
    return raw


def _inlet_setting_state(obj):
    """Read a Fluent settings child: get_state, call, or .value.

    The first successful reader is passed through
    :func:`unwrap_fluent_setting` so ``{'option': ..., 'value': ...}``
    does not reach ``float()`` or string compares.
    """
    errors = []
    readers = []
    get_state = getattr(obj, "get_state", None)
    if callable(get_state):
        readers.append(get_state)
    if callable(obj):
        readers.append(obj)
    raw_value = getattr(obj, "value", None)
    if raw_value is not None and not callable(raw_value):
        readers.append(lambda: raw_value)
    elif callable(raw_value):
        readers.append(raw_value)
    for reader in readers:
        try:
            return unwrap_fluent_setting(reader())
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(
            "inlet setting unreadable (" + "; ".join(errors) + ")"
        )
    raise RuntimeError(f"inlet setting unreadable: {obj!r}")


def read_inlet_plug_magnitude(vin):
    mag = vin.momentum.velocity_magnitude
    raw = getattr(mag, "value", None)
    if raw is not None and not callable(raw):
        return float(unwrap_fluent_setting(raw))
    return float(_inlet_setting_state(mag))


def inlet_plug_readback_error(observed, requested):
    if observed is None:
        return "velocity_magnitude unreadable"
    if not math.isclose(
        float(observed), float(requested), rel_tol=1e-9, abs_tol=1e-12
    ):
        return (
            f"velocity_magnitude {observed!r} != requested {requested!r}"
        )
    return None


def velocity_spec_is_components(vin):
    """True when the inlet already uses a Components specification.

    Restart finals keep Components+UDF, which deactivates
    ``velocity_magnitude``. Unwrap Fluent ``{'option': 'value', 'value': ...}``
    the same way as plug/profile readback.
    """
    mom = vin.momentum
    spec = None
    try:
        spec = unwrap_fluent_setting(mom.velocity_specification_method())
    except Exception:
        try:
            spec = unwrap_fluent_setting(
                mom.velocity_specification_method.get_state()
            )
        except Exception:
            spec = None
    if isinstance(spec, str) and "component" in spec.lower():
        return True
    try:
        return bool(mom.velocity_components.is_active())
    except Exception:
        return False


def read_inlet_profile_state(vin):
    mom = vin.momentum
    spec = None
    try:
        spec = unwrap_fluent_setting(mom.velocity_specification_method())
    except Exception:
        try:
            spec = unwrap_fluent_setting(
                mom.velocity_specification_method.get_state()
            )
        except Exception as exc:
            spec = f"<unreadable: {type(exc).__name__}: {exc}>"
    try:
        active = bool(mom.velocity_components.is_active())
    except Exception:
        active = False
    state = {
        "specification": spec,
        "components_active": active,
        "x_option": None,
        "x_udf": None,
        "y_value": None,
        "z_value": None,
    }
    if not active:
        return state
    comps = mom.velocity_components
    try:
        state["x_option"] = _inlet_option_leaf(
            _inlet_setting_state(comps[0].option)
        )
        state["x_udf"] = _inlet_udf_leaf(_inlet_setting_state(comps[0].udf))
        state["y_value"] = _inlet_component_numeric(comps[1])
        state["z_value"] = _inlet_component_numeric(comps[2])
    except Exception:
        pass
    return state


def _inlet_component_numeric(comp):
    raw = getattr(comp, "value", None)
    if raw is not None and not callable(raw):
        return float(unwrap_fluent_setting(raw))
    if callable(raw):
        return float(unwrap_fluent_setting(raw()))
    return float(_inlet_setting_state(comp))


def inlet_profile_readback_error(observed, profile_udf_name):
    spec = observed.get("specification")
    if not (isinstance(spec, str) and "component" in spec.lower()):
        return f"specification {spec!r} is not Components"
    if not observed.get("components_active"):
        return "velocity_components inactive"
    x_option = observed.get("x_option")
    if str(x_option).lower() != "udf":
        return f"x-velocity option {x_option!r} is not udf"
    x_udf = observed.get("x_udf")
    if x_udf != profile_udf_name:
        return f"x-velocity UDF {x_udf!r} != requested {profile_udf_name!r}"
    for axis, key in (("y", "y_value"), ("z", "z_value")):
        value = observed.get(key)
        if value is None or not math.isclose(
            float(value), 0.0, rel_tol=0.0, abs_tol=1e-12
        ):
            return f"{axis}-velocity {value!r} != 0"
    return None


def apply_inlet_velocity_boundary(
    vin,
    inlet_zone_name,
    inlet_velocity,
    *,
    use_profile,
    profile_udf_name,
    solver=None,
    debug_inlet_bc_api=False,
):
    """Set one velocity-inlet zone to plug magnitude or Components+UDF.

    Live-verified on Fluent 25.1.0 (this host): set_state("Components") is a
    valid allowed value and activates velocity_components. allowed_values()
    returned ['Magnitude and Direction', 'Components',
    'Magnitude, Normal to Boundary']. velocity_components is a fixed-length
    3-element ListObject in 3D — do not call resize(). TUI fallback below is
    retained but untested on this host (settings API path works).

    If the zone is already Components (restart from a profiled final),
    ``velocity_magnitude`` is inactive. Do not switch to Magnitude just to
    write a plug that the later profile pass would overwrite.
    """
    already_components = velocity_spec_is_components(vin)
    if not use_profile and not already_components:
        vin.momentum.velocity_magnitude.value = inlet_velocity
        err = inlet_plug_readback_error(
            read_inlet_plug_magnitude(vin), inlet_velocity
        )
        if err is not None:
            raise RuntimeError(
                f"Inlet BC readback failed on {inlet_zone_name}: {err}"
            )
        print(
            f"Inlet BC set on {inlet_zone_name}: "
            f"velocity_magnitude={inlet_velocity} m/s"
        )
        return

    tag = f"[inlet-bc-api:{inlet_zone_name}]"

    def _probe(label, fn):
        """Print a diagnostic; never abort the BC path."""
        if not debug_inlet_bc_api:
            try:
                return fn()
            except Exception:
                return None
        try:
            value = fn()
            print(f"{tag} {label}: {value!r}")
            return value
        except Exception as exc:
            print(f"{tag} {label}: EXCEPTION {type(exc).__name__}: {exc}")
            return None

    def _components_active():
        try:
            return bool(vin.momentum.velocity_components.is_active())
        except Exception as exc:
            if debug_inlet_bc_api:
                print(
                    f"{tag} velocity_components.is_active(): "
                    f"EXCEPTION {type(exc).__name__}: {exc}"
                )
            return False

    def _spec_value():
        try:
            return vin.momentum.velocity_specification_method()
        except Exception:
            try:
                return vin.momentum.velocity_specification_method.get_state()
            except Exception as exc:
                return f"<unreadable: {type(exc).__name__}: {exc}>"

    # --- Read-only probes before any mutation (debug only prints) ---
    _probe("1. vin.momentum() full state", lambda: vin.momentum())
    _probe(
        "2. velocity_specification_method() current",
        lambda: vin.momentum.velocity_specification_method(),
    )
    allowed_via_method = _probe(
        "3. velocity_specification_method.allowed_values()",
        lambda: vin.momentum.velocity_specification_method.allowed_values(),
    )
    allowed_via_attr = _probe(
        "4. velocity_specification_method.get_attr('allowed-values')",
        lambda: vin.momentum.velocity_specification_method.get_attr(
            "allowed-values"
        ),
    )
    _probe(
        "5. velocity_components.is_active() BEFORE any set",
        lambda: vin.momentum.velocity_components.is_active(),
    )

    # Collect allowed strings for activation trials (quiet if debug off).
    if allowed_via_method is None and not debug_inlet_bc_api:
        try:
            allowed_via_method = (
                vin.momentum.velocity_specification_method.allowed_values()
            )
        except Exception:
            allowed_via_method = None
    if allowed_via_attr is None and not debug_inlet_bc_api:
        try:
            allowed_via_attr = (
                vin.momentum.velocity_specification_method.get_attr(
                    "allowed-values"
                )
            )
        except Exception:
            allowed_via_attr = None

    allowed_candidates = []
    for source in (allowed_via_method, allowed_via_attr):
        if source is None:
            continue
        if isinstance(source, (list, tuple, set)):
            allowed_candidates.extend(list(source))
        else:
            allowed_candidates.append(source)

    skip_spec_activation = already_components and _components_active()
    # --- Activation attempts ---
    if skip_spec_activation:
        if debug_inlet_bc_api:
            print(
                f"{tag} already Components; skipping spec-method activation"
            )
    else:
        if debug_inlet_bc_api:
            print(f"{tag} 6. attempting set_state('Components')")
        try:
            vin.momentum.velocity_specification_method.set_state("Components")
        except Exception as exc:
            print(
                f"{tag} 6. set_state('Components'): "
                f"EXCEPTION {type(exc).__name__}: {exc}"
            )
        if debug_inlet_bc_api:
            print(f"{tag} 6. after Components: spec={_spec_value()!r}")
            print(
                f"{tag} 6. after Components: "
                f"components_active={_components_active()!r}"
            )

        if not _components_active():
            component_like = [
                c for c in allowed_candidates
                if isinstance(c, str) and "omponent" in c.lower()
            ]
            # De-duplicate while preserving order.
            seen = set()
            component_like_unique = []
            for cand in component_like:
                if cand in seen:
                    continue
                seen.add(cand)
                component_like_unique.append(cand)
            if "Components" in seen:
                # Already tried; skip repeat.
                component_like_unique = [
                    c for c in component_like_unique if c != "Components"
                ]
            if debug_inlet_bc_api:
                print(
                    f"{tag} 7. components still inactive; trying allowed "
                    f"strings containing 'omponent': {component_like_unique!r}"
                )
            for cand in component_like_unique:
                if debug_inlet_bc_api:
                    print(f"{tag} 7. attempting set_state({cand!r})")
                try:
                    vin.momentum.velocity_specification_method.set_state(cand)
                except Exception as exc:
                    print(
                        f"{tag} 7. set_state({cand!r}): "
                        f"EXCEPTION {type(exc).__name__}: {exc}"
                    )
                    continue
                active_now = _components_active()
                if debug_inlet_bc_api:
                    print(
                        f"{tag} 7. after {cand!r}: spec={_spec_value()!r}, "
                        f"components_active={active_now!r}"
                    )
                if active_now:
                    if debug_inlet_bc_api:
                        print(
                            f"{tag} 7. ACTIVATED velocity_components via "
                            f"set_state({cand!r})"
                        )
                    break

    if _components_active():
        components = vin.momentum.velocity_components
        if debug_inlet_bc_api:
            _probe("8. type(velocity_components)", lambda: type(components))
            _probe(
                "8. velocity_components state",
                lambda: components.get_state()
                if hasattr(components, "get_state")
                else components(),
            )
            _probe(
                "8. len(velocity_components)",
                lambda: len(components),
            )
        # Fixed-length 3-vector in 3D; resize() is permanently inactive.
        n_comp = len(components)
        if n_comp != 3:
            print(
                f"{tag} unexpected velocity_components length: "
                f"len={n_comp} (expected 3)"
            )
            raise AssertionError(
                f"velocity_components length must be 3 in 3D, got {n_comp}"
            )
        # x-velocity <- profile UDF; y,z <- 0
        try:
            components[0].option.set_state("udf")
            components[0].udf.set_state(profile_udf_name)
            components[1].option.set_state("value")
            components[1].value = 0.0
            components[2].option.set_state("value")
            components[2].value = 0.0
        except Exception as exc:
            print(
                f"{tag} 10. option/udf assignment: "
                f"EXCEPTION {type(exc).__name__}: {exc}"
            )
            # Fall through to TUI if settings child assignment failed.
        else:
            err = inlet_profile_readback_error(
                read_inlet_profile_state(vin), profile_udf_name
            )
            if err is None:
                print(
                    f"Inlet BC set on {inlet_zone_name}: Components; "
                    f"x-velocity UDF={profile_udf_name}; y=z=0 "
                    f"(U_TARGET patched from inlet_velocity_value={inlet_velocity})"
                )
                return
            print(
                f"{tag} settings assignment returned but readback failed: {err}"
            )

    # Settings API path unavailable — TUI fallback.
    # Dead on this host: settings API path confirmed working on Fluent 25.1.0
    # (set_state("Components") activates velocity_components). Fallback kept
    # but untested; prompt sequence may still vary by Fluent version.
    print("=" * 72)
    print(
        f"{tag} SETTINGS API PATH UNAVAILABLE: velocity_components is "
        "inactive (or assignment failed) after velocity_specification_method "
        "trials. Falling back to TUI."
    )
    print("=" * 72)

    tui_cmd = (
        f"/define/boundary-conditions/velocity-inlet {inlet_zone_name} "
        f", , , , yes , , components yes no no yes "
        f'"{profile_udf_name}" no 0 no 0'
    )
    print(f"{tag} TUI fallback command (untested on this host): {tui_cmd}")
    if solver is None:
        raise RuntimeError(
            f"Inlet profile BC could not be applied on {inlet_zone_name}: "
            "settings path unmatched and solver session was not passed "
            "for TUI fallback."
        )
    try:
        solver.execute_tui(tui_cmd)
        print(f"{tag} TUI fallback execute_tui completed.")
    except Exception as exc:
        print(
            f"{tag} TUI fallback execute_tui: "
            f"EXCEPTION {type(exc).__name__}: {exc}"
        )
        raise RuntimeError(
            f"Inlet profile BC TUI fallback failed on {inlet_zone_name}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    err = inlet_profile_readback_error(
        read_inlet_profile_state(vin), profile_udf_name
    )
    if err is not None:
        raise RuntimeError(
            f"Inlet BC readback failed on {inlet_zone_name} after TUI: {err}"
        )
    print(
        f"Inlet BC set on {inlet_zone_name}: TUI Components+UDF fallback; "
        f"profile_udf_name={profile_udf_name} "
        f"(U_TARGET patched from inlet_velocity_value={inlet_velocity}; "
        f"untested fallback)"
    )


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


def configure_report_definition_for_transcript(report_definition):
    """Enable transcript printing without creating report files or plots."""
    for attr_name, value in (
        ("create_report_file", False),
        ("create_report_plot", False),
        ("print", True),
    ):
        try:
            setattr(report_definition, attr_name, value)
        except Exception:
            pass
    return report_definition


def create_or_update_surface_field_report(
    surface_report_definitions,
    report_name,
    report_type,
    field_name,
    surface_names,
):
    """Create/update one printed surface-field report definition."""
    if not surface_names:
        raise ValueError(
            f"Cannot create/update {report_name} without surface names."
        )
    existing_reports = list_named_object_names(
        named_object=surface_report_definitions,
        object_label="solution.report_definitions.surface",
    )
    if report_name in existing_reports:
        report_definition = surface_report_definitions[report_name]
        print(f"Surface field report already exists. Updating: {report_name}")
    else:
        report_definition = surface_report_definitions.create(report_name)
        print(f"Surface field report created: {report_name}")

    report_definition.report_type = report_type
    report_definition.field = field_name
    report_definition.surface_names = list(surface_names)
    report_definition.per_surface = False
    configure_report_definition_for_transcript(report_definition)
    return report_definition


def solve_time_wall_qoi_report_specs(membrane_wall_zones):
    """Return the data-independent wall report specifications for Phase A."""
    if not membrane_wall_zones:
        raise ValueError(
            "Cannot create solve-time QoI reports without membrane walls."
        )
    return [
        (
            "cm_membrane_avg",
            "surface-areaavg",
            "udm-7",
            membrane_wall_zones,
        ),
        (
            "cm_membrane_max",
            "surface-facetmax",
            "udm-7",
            membrane_wall_zones,
        ),
        (
            "cp_membrane_avg",
            "surface-areaavg",
            "udm-9",
            membrane_wall_zones,
        ),
        (
            "cp_membrane_max",
            "surface-facetmax",
            "udm-9",
            membrane_wall_zones,
        ),
        (
            "wall_shear_membrane_avg",
            "surface-areaavg",
            "wall-shear",
            membrane_wall_zones,
        ),
        (
            "lmh_udm_avg",
            "surface-areaavg",
            "udm-8",
            membrane_wall_zones,
        ),
    ]


def create_solve_time_surface_reports(
    solution,
    report_specs,
    *,
    defer_failures,
):
    """Create surface reports, optionally deferring failures until data exists."""
    surface_reports = solution.report_definitions.surface
    created = []
    deferred = []
    for spec in report_specs:
        report_name, report_type, field_name, surfaces = spec
        try:
            create_or_update_surface_field_report(
                surface_report_definitions=surface_reports,
                report_name=report_name,
                report_type=report_type,
                field_name=field_name,
                surface_names=surfaces,
            )
            created.append(report_name)
        except Exception as exc:
            if not defer_failures:
                raise RuntimeError(
                    "Solve-time QoI report still failed after initialization: "
                    f"{report_name}: {type(exc).__name__}: {exc}"
                ) from exc
            print(
                "WARNING: deferring solve-time QoI report until after "
                f"initialization: {report_name}: {type(exc).__name__}: {exc}"
            )
            deferred.append(spec)
    return created, deferred


def update_solve_time_wall_qoi_report_definitions(
    solution,
    membrane_wall_zones,
):
    """Phase A: create reports on existing membrane wall zones."""
    created, deferred = create_solve_time_surface_reports(
        solution,
        solve_time_wall_qoi_report_specs(membrane_wall_zones),
        defer_failures=True,
    )
    print("\nSolve-time wall QoI Phase A report definitions updated:")
    print(created)
    if deferred:
        print(
            "Solve-time wall QoI reports deferred to Phase B: "
            f"{[spec[0] for spec in deferred]}"
        )
    return created, deferred


def retry_deferred_solve_time_qoi_report_definitions(
    solution,
    deferred_report_specs,
):
    """Phase B: retry wall reports that required initialized solution data."""
    if not deferred_report_specs:
        return []
    created, _ = create_solve_time_surface_reports(
        solution,
        deferred_report_specs,
        defer_failures=False,
    )
    print("\nDeferred solve-time QoI report definitions updated:")
    print(created)
    return created


def update_solve_time_pressure_qoi_report_definitions(
    solver,
    solution,
    layout,
    domain_x_min_m,
):
    """Phase B: create initialized-data planes and spacer pressure reports.

    Planes come from ``layout.active_span``, matching post-extract.
    """
    spacer_x_in_m, spacer_x_out_m = layout.active_span(domain_x_min_m)
    spacer_length_m = spacer_x_out_m - spacer_x_in_m
    if spacer_length_m <= 0.0:
        raise ValueError(
            "Cannot create solve-time QoI reports with non-positive "
            f"spacer length: {spacer_length_m}."
        )

    pressure_report_names = [
        "pressure_spacer_in_avg",
        "pressure_spacer_out_avg",
        "pressure_drop_spacer",
    ]
    plane_names = ["plane_spacer_in", "plane_spacer_out"]
    created = []
    try:
        create_x_normal_plane(solver, plane_names[0], spacer_x_in_m)
        create_x_normal_plane(solver, plane_names[1], spacer_x_out_m)

        surface_reports = solution.report_definitions.surface
        create_or_update_surface_field_report(
            surface_report_definitions=surface_reports,
            report_name=pressure_report_names[0],
            report_type="surface-areaavg",
            field_name="pressure",
            surface_names=[plane_names[0]],
        )
        created.append(pressure_report_names[0])
        create_or_update_surface_field_report(
            surface_report_definitions=surface_reports,
            report_name=pressure_report_names[1],
            report_type="surface-areaavg",
            field_name="pressure",
            surface_names=[plane_names[1]],
        )
        created.append(pressure_report_names[1])

        pressure_drop_report = (
            create_or_update_single_valued_expression_report(
                single_expression_report_definitions=(
                    solution.report_definitions.single_valued_expression
                ),
                report_name=pressure_report_names[2],
                definition=(
                    "pressure_spacer_in_avg - pressure_spacer_out_avg"
                ),
            )
        )
        configure_report_definition_for_transcript(pressure_drop_report)
        created.append(pressure_report_names[2])
    except Exception as exc:
        missing = [
            name for name in pressure_report_names
            if name not in created
        ]
        print(
            "WARNING: solve-time spacer pressure monitors are unavailable; "
            f"missing reports={missing}; required planes={plane_names}; "
            "continuing solver run. "
            f"Error: {type(exc).__name__}: {exc}"
        )
        return created

    print("\nSolve-time spacer pressure Phase B reports updated:")
    print(created)
    return created


def update_transport_report_definitions_for_current_zones(
    solution,
    inlet_zones,
    outlet_zones,
    membrane_wall_zones,
    density_value,
    membrane_blocked_area_frac,
    m_in_name="m_in",
    m_out_name="m_out",
    area_mem_name="area_mem",
    lmh_name="lmh",
    lmh_signed_name="lmh_signed",
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

    # LMH from mass imbalance on effective membrane area (blocked fraction excluded).
    lmh_definition = lmh_mass_balance_expression(
        m_in_name=m_in_name,
        m_out_name=m_out_name,
        density_value=density_value,
        area_mem_name=area_mem_name,
        membrane_blocked_area_frac=membrane_blocked_area_frac,
        signed=False,
    )

    create_or_update_single_valued_expression_report(
        single_expression_report_definitions=single_expression_report_definitions,
        report_name=lmh_name,
        definition=lmh_definition,
    )
    lmh_signed_definition = lmh_mass_balance_expression(
        m_in_name=m_in_name,
        m_out_name=m_out_name,
        density_value=density_value,
        area_mem_name=area_mem_name,
        membrane_blocked_area_frac=membrane_blocked_area_frac,
        signed=True,
    )
    lmh_signed_report = create_or_update_single_valued_expression_report(
        single_expression_report_definitions=single_expression_report_definitions,
        report_name=lmh_signed_name,
        definition=lmh_signed_definition,
    )
    configure_report_definition_for_transcript(lmh_signed_report)

    print("\nTransport report definitions after update:")
    print(f"{m_in_name}:", flux_report_definitions[m_in_name].get_state())
    print(f"{m_out_name}:", flux_report_definitions[m_out_name].get_state())
    print(f"{area_mem_name}:", surface_report_definitions[area_mem_name].get_state())
    print(f"{lmh_name}:", single_expression_report_definitions[lmh_name].get_state())
    print(
        f"{lmh_signed_name}:",
        single_expression_report_definitions[lmh_signed_name].get_state(),
    )


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


def _list_named_object_names_best_effort(named_object):
    try:
        names = named_object.get_object_names()
        if names is not None:
            return [str(name) for name in names]
    except Exception:
        pass
    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(str(key) for key in state.keys())
    except Exception:
        pass
    return []


def ensure_lmh_udm_avg_report_file(solution, report_name, file_name):
    """Create/update a Fluent report file for one QoI report definition.

    Fluent 25.1 surface report definitions do not expose create_report_file;
    history is written through solution.monitor.report_files instead.
    The file is written relative to Fluent's working directory (the case dir).
    """
    report_files = solution.monitor.report_files
    object_name = f"{report_name}_rfile"
    existing = _list_named_object_names_best_effort(report_files)
    if object_name in existing:
        report_file = report_files[object_name]
        print(f"Report file already exists. Updating: {object_name}")
    else:
        try:
            report_file = report_files.create(object_name)
        except TypeError:
            report_file = report_files.create(name=object_name)
        print(f"Report file created: {object_name}")

    report_file.report_defs = [report_name]
    report_file.file_name = file_name
    try:
        report_file.frequency = 1
    except Exception:
        pass
    try:
        report_file.print = False
    except Exception:
        pass
    try:
        report_file.active = True
    except Exception:
        pass
    try:
        report_file.write_instantaneous_values = True
    except Exception:
        pass
    print(f"Report file '{object_name}' -> {file_name} (report_defs={report_name})")
    return object_name


QOI_CONVERGENCE_CONDITION = "all-conditions-are-met"
QOI_STOP_PRESSURE_REPORT_NAME = "pressure_drop_spacer"


def configure_qoi_convergence_condition(
    solution,
    *,
    report_name,
    stop_criterion,
    previous_values_to_consider,
    initial_values_to_ignore,
    active,
):
    """Configure one Fluent report-definition convergence condition.

    Sets the global selector to all-conditions-are-met. UG 37.18 All/Any
    is across active report conditions AND enabled residual checks, so
    with residual check_convergence left on the live stop is every active
    QoI report AND residuals. Call once per report; the selector is
    overwritten to the same value each time.
    """
    convergence = solution.monitor.convergence_conditions
    convergence.condition = QOI_CONVERGENCE_CONDITION
    try:
        convergence.check_for = "solution-convergence"
    except Exception as exc:
        print(f"Could not set convergence_conditions.check_for: {exc}")
    try:
        convergence.frequency = 1
    except Exception as exc:
        print(f"Could not set convergence_conditions.frequency: {exc}")

    reports = convergence.convergence_reports
    object_name = f"{report_name}_conv"
    existing = _list_named_object_names_best_effort(reports)
    if object_name in existing:
        report = reports[object_name]
        print(f"Convergence report already exists. Updating: {object_name}")
    else:
        try:
            report = reports.create(object_name)
        except TypeError:
            report = reports.create(name=object_name)
        print(f"Convergence report created: {object_name}")

    report.report_defs = report_name
    report.stop_criterion = float(stop_criterion)
    report.previous_values_to_consider = int(previous_values_to_consider)
    report.initial_values_to_ignore = int(initial_values_to_ignore)
    report.active = bool(active)
    try:
        report.print = True
    except Exception:
        pass
    print(
        "QoI convergence condition configured: "
        f"report={report_name}, stop_criterion={stop_criterion}, "
        f"Np={previous_values_to_consider}, ignore={initial_values_to_ignore}, "
        f"active={active}, condition={QOI_CONVERGENCE_CONDITION}"
    )
    return object_name


def set_qoi_convergence_condition_active(solution, object_name, active):
    """Activate or deactivate a named QoI convergence report."""
    report = solution.monitor.convergence_conditions.convergence_reports[object_name]
    report.active = bool(active)
    print(f"QoI convergence condition '{object_name}' active={active}")


def qoi_met_from_report_file(report_file_path, previous_values_to_consider, stop_criterion):
    """Evaluate Fluent's relative window criterion from a written report file.

    Returns (evaluable, met, final_iteration).
    evaluable is False when the file is missing, unreadable, or has fewer than
    Np+1 numeric rows (so the UG formula cannot be applied).
    """
    path = Path(report_file_path)
    if not path.is_file():
        print(f"QoI report file not found for stop-reason check: {path}")
        return False, False, None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        print(f"Could not read QoI report file {path}: {exc}")
        return False, False, None
    series = parse_fluent_report_file_series(text)
    if not series:
        print(f"QoI report file had no numeric rows: {path}")
        return False, False, None
    final_iteration = int(series[-1][0])
    required = int(previous_values_to_consider) + 1
    if len(series) < required:
        print(
            f"QoI report file too short for window check: rows={len(series)}, "
            f"required={required}, path={path}"
        )
        return False, False, final_iteration
    window = [value for _iteration, value in series[-required:]]
    met = fluent_report_relative_window_met(window, stop_criterion)
    print(
        f"QoI report-file window check: path={path}, rows={len(series)}, "
        f"window_len={len(window)}, met={met}"
    )
    return True, met, final_iteration


def qoi_met_from_report_files(
    report_file_paths,
    previous_values_to_consider,
    stop_criterion,
):
    """AND-combine UG window checks across report files.

    Returns (evaluable, met, final_iteration). evaluable is True only when
    every path is evaluable. met is True only when every path is met.
    final_iteration is the max last-row iteration among readable files.
    """
    if not report_file_paths:
        return False, False, None
    evaluable_all = True
    met_all = True
    last_iterations = []
    for path in report_file_paths:
        evaluable, met, final_iteration = qoi_met_from_report_file(
            path,
            previous_values_to_consider,
            stop_criterion,
        )
        if final_iteration is not None:
            last_iterations.append(int(final_iteration))
        if not evaluable:
            evaluable_all = False
            met_all = False
        elif not met:
            met_all = False
    combined_iteration = max(last_iterations) if last_iterations else None
    return evaluable_all, met_all, combined_iteration


def determine_and_print_stop_reason(
    *,
    case_dir,
    solver_log_path,
    max_iterations,
    qoi_enabled,
    qoi_report_file_paths,
    qoi_previous_values_to_consider,
    qoi_stop_criterion,
    diverged,
    calculation_ran=True,
):
    """Classify stop reason from the Fluent transcript, print the marker.

    Intended to run immediately after iterate() returns or raises, before later
    post-iterate steps (write_case_data, transcript assertions), so a partial
    failure still records why iteration ended.

    Primary determination is the console phrases
    'report definition solution is converged' (QoI) and
    'solution is converged' (residuals), but only when the printed
    iteration is strictly after this session's start. The start is the
    first data line of the QoI .out files (fallback: first residual-table
    row). A marker at that same iteration is inherited from a restart
    source and is ignored. Report-file window checks are a cross-check
    only.
    """
    if not calculation_ran:
        reason = classify_solver_stop_reason(
            diverged=False,
            residuals_met=False,
            qoi_met=False,
            final_iteration=None,
            max_iterations=max_iterations,
            calculation_ran=False,
        )
        marker = format_stop_reason_marker(reason)
        print(marker)
        print("Stop-reason details: calculation_ran=False, qoi_check_enabled=False")
        return reason

    transcript_path, transcript_text = select_solve_transcript(
        case_dir,
        solver_log_path,
    )
    run_start_iteration = first_fluent_report_file_iteration(qoi_report_file_paths)
    if run_start_iteration is None:
        run_start_iteration = parse_first_residual_iteration_from_transcript_text(
            transcript_text
        )
    transcript_reason, transcript_iteration = parse_fluent_convergence_marker(
        transcript_text,
        after_iteration=run_start_iteration,
    )
    table_iteration = parse_last_residual_iteration_from_transcript_text(
        transcript_text
    )
    residuals_met = transcript_reason == "residual_converged"
    qoi_met = transcript_reason == "qoi_converged"
    final_iteration = transcript_iteration
    if final_iteration is None:
        final_iteration = table_iteration

    report_file_evaluable = False
    report_file_qoi_met = False
    report_file_iteration = None
    discrepancy = None
    if qoi_enabled:
        report_file_evaluable, report_file_qoi_met, report_file_iteration = (
            qoi_met_from_report_files(
                qoi_report_file_paths,
                qoi_previous_values_to_consider,
                qoi_stop_criterion,
            )
        )
        if qoi_met and not report_file_qoi_met:
            discrepancy = (
                "transcript qoi_converged but report-file window check "
                f"disagrees (evaluable={report_file_evaluable}, "
                f"report_file_qoi_met={report_file_qoi_met})"
            )
            print(f"Stop-reason discrepancy: {discrepancy}")
        if final_iteration is None:
            final_iteration = report_file_iteration

    if final_iteration is None:
        final_iteration = last_fluent_report_file_iteration(qoi_report_file_paths)

    reason = classify_solver_stop_reason(
        diverged=bool(diverged),
        residuals_met=residuals_met,
        qoi_met=qoi_met,
        final_iteration=final_iteration,
        max_iterations=max_iterations,
        calculation_ran=True,
    )
    marker = format_stop_reason_marker(reason)
    print(marker)
    detail_parts = [
        f"transcript_file={transcript_path}",
        f"transcript_reason={transcript_reason}",
        f"transcript_iteration={transcript_iteration}",
        f"run_start_iteration={run_start_iteration}",
        f"table_iteration={table_iteration}",
        f"residuals_met={residuals_met}",
        f"qoi_met={qoi_met}",
        f"qoi_check_enabled={bool(qoi_enabled)}",
        f"final_iteration={final_iteration}",
        f"max_iterations={max_iterations}",
        f"diverged={diverged}",
    ]
    if qoi_enabled:
        detail_parts.extend(
            [
                f"report_file_qoi_met={report_file_qoi_met}",
                f"qoi_report_evaluable={report_file_evaluable}",
                f"report_file_iteration={report_file_iteration}",
            ]
        )
        if discrepancy:
            detail_parts.append(f"discrepancy={discrepancy}")
    else:
        detail_parts.append(
            "qoi_report_check=skipped (enable_qoi_convergence_stop=False)"
        )
    print("Stop-reason details: " + ", ".join(detail_parts))
    return reason


RELAXATION_PROFILES = {
    "conservative": {
        "explicit_pressure_under_relaxation": 0.2,
        "explicit_momentum_under_relaxation": 0.3,
        "species_pseudo_relaxation": 0.5,
    },
    "strong": {
        "explicit_pressure_under_relaxation": 0.1,
        "explicit_momentum_under_relaxation": 0.2,
        "species_pseudo_relaxation": 0.3,
    },
}


def set_and_verify_leaf(parent, attr_name, value, label):
    """Set a scalar settings-API leaf and confirm it via readback."""
    outcome = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    try:
        before_state = parent.get_state()
    except Exception as exc:
        outcome["error"] = (
            f"could not read parent state: {type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    before = before_state.get(attr_name) if isinstance(before_state, dict) else None
    outcome["before"] = before
    print(f"URF before {label}: {before}")

    if isinstance(before_state, dict) and attr_name not in before_state:
        outcome["error"] = (
            f"{attr_name} not present in state keys "
            f"{list(before_state.keys())}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        setattr(parent, attr_name, value)
    except Exception as exc:
        outcome["error"] = f"set failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        after_state = parent.get_state()
        after = after_state.get(attr_name) if isinstance(after_state, dict) else None
    except Exception as exc:
        outcome["error"] = f"readback failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    outcome["after"] = after
    print(f"URF after {label}: {after}")
    confirmed = (
        isinstance(after, (int, float))
        and not isinstance(after, bool)
        and abs(float(after) - float(value)) < 1.0e-9
    )
    outcome["status"] = (
        "APPLIED_CONFIRMED"
        if confirmed
        else "WARN_APPLY_URF_FAILED"
    )
    if not confirmed:
        print(
            f"WARN_APPLY_URF_FAILED ({label}): readback {after} "
            f"does not confirm requested {value}"
        )
    return outcome


def set_and_verify_dict_entry(container, key, value, label):
    """Set one dict-like settings entry and confirm it via readback."""
    full_label = f"{label}[{key}]"
    outcome = {
        "label": full_label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    try:
        before_state = container.get_state()
    except Exception as exc:
        outcome["error"] = (
            f"could not read container state: {type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {outcome['error']}")
        return outcome

    before = before_state.get(key) if isinstance(before_state, dict) else None
    outcome["before"] = before
    print(f"URF before {full_label}: {before}")

    set_ok = False
    set_error = ""
    try:
        container[key] = value
        set_ok = True
    except Exception as exc_item:
        set_error = (
            "container[key]=value failed: "
            f"{type(exc_item).__name__}: {exc_item}"
        )
        try:
            container.set_state({key: value})
            set_ok = True
        except Exception as exc_state:
            set_error += (
                f"; set_state failed: {type(exc_state).__name__}: {exc_state}"
            )

    if not set_ok:
        outcome["error"] = set_error
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {set_error}")
        return outcome

    try:
        after_state = container.get_state()
        after = after_state.get(key) if isinstance(after_state, dict) else None
    except Exception as exc:
        outcome["error"] = f"readback failed: {type(exc).__name__}: {exc}"
        print(f"WARN_APPLY_URF_FAILED ({full_label}): {outcome['error']}")
        return outcome

    outcome["after"] = after
    print(f"URF after {full_label}: {after}")
    confirmed = (
        isinstance(after, (int, float))
        and not isinstance(after, bool)
        and abs(float(after) - float(value)) < 1.0e-9
    )
    outcome["status"] = (
        "APPLIED_CONFIRMED"
        if confirmed
        else "WARN_APPLY_URF_FAILED"
    )
    if not confirmed:
        print(
            f"WARN_APPLY_URF_FAILED ({full_label}): readback {after} "
            f"does not confirm requested {value}"
        )
    return outcome


def apply_pseudo_time_species_relaxation(solution, species_name, value):
    """Best-effort species pseudo-time relaxation update with readback."""
    label = "pseudo_time_species_relaxation"
    outcome = {
        "label": label,
        "requested": value,
        "status": "SKIPPED_SPECIES_UNAVAILABLE",
    }
    try:
        container = (
            solution.controls
            .pseudo_time_explicit_relaxation_factor
            .global_dt_pseudo_relax
        )
    except Exception as exc:
        outcome["error"] = (
            f"container not found: {type(exc).__name__}: {exc}"
        )
        outcome["status"] = "WARN_APPLY_URF_FAILED"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    candidate_keys = []
    for key in (species_name, "species-0"):
        if key and key not in candidate_keys:
            candidate_keys.append(key)

    try:
        state = container.get_state()
        available_keys = list(state) if isinstance(state, dict) else []
    except Exception:
        available_keys = list_named_object_names(
            container,
            "global_dt_pseudo_relax",
        )

    print(f"Pseudo-time species relaxation available keys: {available_keys}")
    matched_key = next(
        (key for key in candidate_keys if key in available_keys),
        None,
    )
    if matched_key is None:
        outcome["error"] = (
            f"none of {candidate_keys} present in {available_keys}"
        )
        print(f"SKIPPED_SPECIES_UNAVAILABLE ({label}): {outcome['error']}")
        return outcome

    return set_and_verify_dict_entry(
        container,
        matched_key,
        value,
        label,
    )


def apply_species_implicit_under_relaxation(solution, species_name, value):
    """Apply orthogonal species-0 implicit URF via the expert path.

    Independent of RELAXATION_PROFILES. value=="preserve" leaves the leaf
    untouched. Candidate keys prefer "species-0", then the configured species
    name, matching the probe-confirmed naming of the expert subtree.
    """
    label = "species_implicit_under_relaxation"
    outcome = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    if isinstance(value, str) and value.strip().lower() == "preserve":
        print(
            "\nSpecies implicit under-relaxation preserve: "
            "leaving expert leaf unchanged."
        )
        outcome["status"] = "PRESERVED"
        return outcome

    try:
        requested = float(value)
    except (TypeError, ValueError) as exc:
        outcome["error"] = (
            f"value must be 'preserve' or a positive float: {value!r} "
            f"({type(exc).__name__}: {exc})"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome
    if requested <= 0.0:
        outcome["error"] = f"value must be positive: {value!r}"
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        global_dt = (
            solution.controls.advanced.expert
            .pseudo_time_method_usage.global_dt
        )
    except Exception as exc:
        outcome["error"] = (
            f"global_dt container not found: {type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    candidate_keys = []
    for key in ("species-0", species_name):
        if key and key not in candidate_keys:
            candidate_keys.append(key)

    try:
        state = global_dt.get_state()
        available_keys = list(state) if isinstance(state, dict) else []
    except Exception:
        available_keys = list_named_object_names(global_dt, "global_dt")

    print(
        "Species implicit under-relaxation available keys: "
        f"{available_keys}"
    )
    matched_key = next(
        (key for key in candidate_keys if key in available_keys),
        None,
    )
    if matched_key is None:
        outcome["error"] = (
            f"none of {candidate_keys} present in {available_keys}"
        )
        outcome["status"] = "SKIPPED_SPECIES_UNAVAILABLE"
        print(f"SKIPPED_SPECIES_UNAVAILABLE ({label}): {outcome['error']}")
        return outcome

    print(f"Species implicit under-relaxation using key: {matched_key!r}")
    outcome["matched_key"] = matched_key
    try:
        parent = global_dt[matched_key]
    except Exception as exc:
        outcome["error"] = (
            f"could not resolve global_dt[{matched_key!r}]: "
            f"{type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    leaf_outcome = set_and_verify_leaf(
        parent,
        "implicit_under_relaxation_factor",
        requested,
        f"{label}[{matched_key}]",
    )
    leaf_outcome["matched_key"] = matched_key
    # Keep a stable top-level label for CSV flattening.
    leaf_outcome["label"] = label
    return leaf_outcome


class GtsTimeStepScaleFactorError(RuntimeError):
    """Pilot abort: GTS scale factor is inactive or did not read back."""


def _config_is_preserve(value):
    return isinstance(value, str) and value.strip().lower() == "preserve"


def _require_active_leaf(leaf, label):
    checker = getattr(leaf, "is_active", None)
    if not callable(checker):
        raise GtsTimeStepScaleFactorError(
            f"{label} is_active() is unavailable"
        )
    try:
        active = bool(checker())
    except Exception as exc:
        raise GtsTimeStepScaleFactorError(
            f"{label} is_active() failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not active:
        raise GtsTimeStepScaleFactorError(f"{label} is inactive")


def _require_get_state(obj, label):
    try:
        state = obj.get_state()
    except Exception as exc:
        raise GtsTimeStepScaleFactorError(
            f"{label} get_state failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(state, dict):
        raise GtsTimeStepScaleFactorError(
            f"{label} get_state is not a dict: {state!r}"
        )
    return state


def apply_gts_time_step_size_scale_factor(solution, requested_factor):
    """Set automatic GTS time_step_size_scale_factor fail-closed.

    Coupled / global-time-step come from solution.methods. Automatic
    method, conservative length scale, and the scale factor come from
    solution.run_calculation.pseudo_time_settings.time_step_method
    (Fluent 25.1). Writes only time_step_size_scale_factor. Raises
    GtsTimeStepScaleFactorError if that leaf is inactive or readback
    is not the requested value.
    """
    if _config_is_preserve(requested_factor):
        print(
            "PT_GTS_PILOT preserve: leaving time_step_size_scale_factor unchanged."
        )
        return {"label": "time_step_size_scale_factor", "status": "PRESERVED"}

    try:
        requested = float(requested_factor)
    except (TypeError, ValueError) as exc:
        raise GtsTimeStepScaleFactorError(
            f"time_step_size_scale_factor must be a positive number, "
            f"got {requested_factor!r}"
        ) from exc
    if requested <= 0.0:
        raise GtsTimeStepScaleFactorError(
            f"time_step_size_scale_factor must be positive, got {requested!r}"
        )

    print(
        "PT_GTS_PILOT requested "
        f"flow_scheme=Coupled formulation=global-time-step "
        f"time_step_method=automatic length_scale_methods=conservative "
        f"time_step_size_scale_factor={requested}"
    )

    try:
        p_v_coupling = solution.methods.p_v_coupling
        pseudo_time_method = solution.methods.pseudo_time_method
        time_step_method = (
            solution.run_calculation.pseudo_time_settings.time_step_method
        )
    except Exception as exc:
        raise GtsTimeStepScaleFactorError(
            f"Coupled/GTS settings missing: {type(exc).__name__}: {exc}"
        ) from exc

    p_v_state = _require_get_state(p_v_coupling, "p_v_coupling")
    flow_scheme = p_v_state.get("flow_scheme")
    if flow_scheme != "Coupled":
        raise GtsTimeStepScaleFactorError(
            f"flow_scheme readback {flow_scheme!r} is not Coupled"
        )

    pt_state = _require_get_state(pseudo_time_method, "pseudo_time_method")
    formulation = pt_state.get("formulation")
    coupled_solver = (
        formulation.get("coupled_solver")
        if isinstance(formulation, dict)
        else None
    )
    if coupled_solver != "global-time-step":
        raise GtsTimeStepScaleFactorError(
            f"formulation readback {formulation!r} is not global-time-step"
        )

    tsm_state = _require_get_state(time_step_method, "time_step_method")
    method_value = tsm_state.get("time_step_method")
    length_value = tsm_state.get("length_scale_methods")
    factor_before = tsm_state.get("time_step_size_scale_factor")
    if method_value != "automatic":
        raise GtsTimeStepScaleFactorError(
            f"time_step_method readback {method_value!r} is not automatic"
        )
    if length_value != "conservative":
        raise GtsTimeStepScaleFactorError(
            f"length_scale_methods readback {length_value!r} is not conservative"
        )

    try:
        factor_leaf = time_step_method.time_step_size_scale_factor
    except Exception as exc:
        raise GtsTimeStepScaleFactorError(
            "time_step_size_scale_factor leaf missing: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    _require_active_leaf(factor_leaf, "time_step_size_scale_factor")

    print(
        "PT_GTS_PILOT observed "
        f"flow_scheme={flow_scheme!r} formulation={formulation!r} "
        f"time_step_method={method_value!r} "
        f"length_scale_methods={length_value!r} "
        f"time_step_size_scale_factor={factor_before!r}"
    )

    outcome = set_and_verify_leaf(
        time_step_method,
        "time_step_size_scale_factor",
        requested,
        "time_step_size_scale_factor",
    )
    print(
        "PT_GTS_PILOT readback "
        f"requested={requested} before={outcome.get('before')!r} "
        f"after={outcome.get('after')!r} status={outcome.get('status')}"
    )
    if outcome.get("status") != "APPLIED_CONFIRMED":
        raise GtsTimeStepScaleFactorError(
            "time_step_size_scale_factor readback failed: "
            f"{outcome.get('error', outcome)}"
        )
    after = outcome.get("after")
    if not isinstance(after, (int, float)) or abs(float(after) - requested) >= 1.0e-9:
        raise GtsTimeStepScaleFactorError(
            f"time_step_size_scale_factor readback {after!r} is not {requested}"
        )
    return outcome


def apply_pseudo_time_verbosity(solution, value):
    """Optionally raise run_calculation.pseudo_time_settings.verbosity.

    Default preserve leaves Fluent unchanged. Verbosity 1 is documented to
    print the pseudo time step size; 2 prints additional calculation details.
    No transcript parser is wired here — capture the live line shape first.
    """
    label = "pseudo_time_verbosity"
    outcome = {
        "label": label,
        "requested": value,
        "status": "WARN_APPLY_URF_FAILED",
    }
    if isinstance(value, str) and value.strip().lower() == "preserve":
        print(
            "\nPseudo-time verbosity preserve: "
            "leaving run_calculation.pseudo_time_settings.verbosity unchanged."
        )
        outcome["status"] = "PRESERVED"
        return outcome

    try:
        requested = int(value)
    except (TypeError, ValueError) as exc:
        outcome["error"] = (
            f"value must be 'preserve' or an integer 0/1/2: {value!r} "
            f"({type(exc).__name__}: {exc})"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome
    if float(value) != float(requested) or requested not in {0, 1, 2}:
        outcome["error"] = (
            f"value must be 'preserve' or an integer in {{0, 1, 2}}: {value!r}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    try:
        parent = solution.run_calculation.pseudo_time_settings
    except Exception as exc:
        outcome["error"] = (
            "pseudo_time_settings not found: "
            f"{type(exc).__name__}: {exc}"
        )
        print(f"WARN_APPLY_URF_FAILED ({label}): {outcome['error']}")
        return outcome

    return set_and_verify_leaf(parent, "verbosity", float(requested), label)


def apply_real_under_relaxation(solver, profile, species_name):
    """Apply a named under-relaxation profile with readback verification."""
    result = {"profile": profile, "applied": []}
    if profile == "baseline":
        print(
            "\nRelaxation profile baseline: leaving under-relaxation "
            "controls unchanged."
        )
        return result

    values = RELAXATION_PROFILES.get(profile)
    if values is None:
        print(
            f"Unknown relaxation profile {profile!r}; "
            "leaving controls unchanged."
        )
        return result

    print(f"\nApplying real under-relaxation profile: {profile}")
    solution = solver.settings.solution
    try:
        p_v_controls = solution.controls.p_v_controls
    except Exception as exc:
        print(
            "WARN_APPLY_URF_FAILED (p_v_controls): "
            f"could not access p_v_controls: {exc}"
        )
        p_v_controls = None

    if p_v_controls is not None:
        result["applied"].append(
            set_and_verify_leaf(
                p_v_controls,
                "explicit_pressure_under_relaxation",
                values["explicit_pressure_under_relaxation"],
                "explicit_pressure_under_relaxation",
            )
        )
        result["applied"].append(
            set_and_verify_leaf(
                p_v_controls,
                "explicit_momentum_under_relaxation",
                values["explicit_momentum_under_relaxation"],
                "explicit_momentum_under_relaxation",
            )
        )
    else:
        result["applied"].extend([
            {
                "label": "explicit_pressure_under_relaxation",
                "status": "WARN_APPLY_URF_FAILED",
                "error": "p_v_controls unavailable",
            },
            {
                "label": "explicit_momentum_under_relaxation",
                "status": "WARN_APPLY_URF_FAILED",
                "error": "p_v_controls unavailable",
            },
        ])

    result["applied"].append(
        apply_pseudo_time_species_relaxation(
            solution,
            species_name,
            values["species_pseudo_relaxation"],
        )
    )

    for entry in result["applied"]:
        if entry.get("status") not in {
            "APPLIED_CONFIRMED",
            "SKIPPED_SPECIES_UNAVAILABLE",
        }:
            print(
                f"WARN_APPLY_URF_FAILED summary: {entry.get('label')}: "
                f"{entry.get('error', 'readback mismatch')}"
            )

    return result


# ==========================================================
# ##### [3] Path Checks #####
# ==========================================================

if __name__ == "__main__":
    print("=" * 72)
    print("Solver input summary")
    print("=" * 72)
    print(f"Input mode: {input_mode}")
    print("Launch path: meshing_to_solver")
    print(f"Restart case file path: {restart_from_case_file if restart_from_case_file is not None else '(n/a)'}")
    print(f"Restart data file path: {restart_from_data_file if restart_from_data_file is not None else '(n/a)'}")
    print(f"Target case folder: {case_path}")
    print(f"Target case_name: {case_name}")
    print(f"Target setup case: {setup_case_file}")
    print(f"Target final case: {final_case_file}")
    print(f"Target final data: {final_data_file}")
    print("=" * 72)

    staged_restart_case_file = None
    staged_restart_data_file = None
    restart_source_dir = None
    if input_mode == "restart_continuation":
        if not os.path.isfile(restart_from_case_file):
            raise FileNotFoundError(
                f"Restart case file not found: {restart_from_case_file}"
            )

        if not os.path.isfile(restart_from_data_file):
            raise FileNotFoundError(
                f"Restart data file not found: {restart_from_data_file}"
            )

        restart_source_dir = os.path.dirname(os.path.abspath(restart_from_case_file))
        if normalize_path(restart_source_dir) == normalize_path(case_path):
            raise ValueError(
                "Restart source folder must not be the target case_path: "
                f"{restart_source_dir}. Choose a different target case_name."
            )

        restart_source_paths = {
            normalize_path(restart_from_case_file),
            normalize_path(restart_from_data_file),
        }
        target_output_paths = {
            normalize_path(setup_case_file),
            normalize_path(final_case_file),
            normalize_path(final_data_file),
        }
        overlapping_paths = restart_source_paths & target_output_paths
        if overlapping_paths:
            raise ValueError(
                "Restart source files must not be overwritten by target outputs: "
                f"{sorted(overlapping_paths)}. Choose a different target case_name."
            )

        if not os.path.exists(case_path):
            os.makedirs(case_path)

        staged_restart_case_file = restart_staged_case_path(
            case_path, geo_name, case_name
        )
        staged_restart_data_file = restart_staged_data_path(
            case_path, geo_name, case_name
        )
    else:
        if not os.path.exists(case_path):
            os.makedirs(case_path)

        if not os.path.isfile(mesh_file_path):
            raise FileNotFoundError(f"Mesh file not found: {mesh_file_path}")

        if not os.path.isfile(template_case_path):
            raise FileNotFoundError(f"Template case file not found: {template_case_path}")

    if not os.path.isfile(udf_master_path):
        raise FileNotFoundError(f"UDF source file not found: {udf_master_path}")

    require_mesh_manifest_x_extent_matches_layout(mesh_case_path)
    mesh_manifest = read_mesh_manifest(mesh_case_path)
    require_campaign_membrane_blocked_area_frac(mesh_manifest, kind="Mesh")
    if os.path.isfile(mesh_file_path):
        require_mesh_sha256_matches_file(
            mesh_manifest.get("mesh_sha256"),
            mesh_file_path,
        )
    run_manifest_path = Path(case_path) / "manifest.json"
    if run_manifest_path.is_file():
        run_payload = read_run_manifest(case_path)
        require_campaign_membrane_blocked_area_frac(run_payload, kind="Run")
        require_mesh_run_blocked_frac_agree(mesh_manifest, run_payload)
        require_run_id_matches_operating_point(
            run_payload["run_id"],
            run_payload["u_target_ms"],
            run_payload["p_gauge_pa"],
        )
        if os.path.isfile(mesh_file_path):
            require_mesh_sha256_matches_file(
                run_payload.get("mesh_sha256"),
                mesh_file_path,
            )
        require_u_mean_profile_identity(
            run_payload.get("u_mean_ms"),
            mesh_manifest.get("inlet_profile_G"),
            run_payload["u_target_ms"],
            inlet_bc_type=run_payload["inlet_bc_type"],
        )

    print(f"Case path: {case_path}")
    if input_mode == "restart_continuation":
        print(f"Restart case file: {restart_from_case_file}")
        print(f"Restart data file: {restart_from_data_file}")
        print(f"Staged restart case: {staged_restart_case_file}")
        print(f"Staged restart data: {staged_restart_data_file}")
        print("Mesh file: (not used for restart_continuation)")
        print("Template case: (not used for restart_continuation)")
        copy_restart_source_into_case_folder(
            restart_from_case_file,
            restart_from_data_file,
            staged_restart_case_file,
            staged_restart_data_file,
        )
        require_restart_read_not_source_folder(
            staged_restart_case_file,
            staged_restart_data_file,
            case_path,
            restart_source_dir,
            source_case_file=restart_from_case_file,
            source_data_file=restart_from_data_file,
        )
    else:
        print(f"Mesh file: {mesh_file_path}")
        print(f"Template case: {template_case_path}")
    print(f"UDF master source: {udf_master_path}")
    print(f"UDF case copy: {udf_case_path}")
    if input_mode == "mesh_initialization":
        print(f"Solver mesh-replace log: {solver_mesh_replace_log_path}")
    else:
        print("Solver mesh-replace log: (not used for restart_continuation)")
    print(f"Solver log: {solver_log_path}")


    # ==========================================================
    # ##### [4] Launch Fluent and Load Input Case #####
    # ==========================================================

    pyfluent.config.check_health_timeout = fluent_health_timeout

    meshing = None
    solver = None
    setup = None
    solution = None
    transcript_is_running = False
    original_working_directory = os.getcwd()
    inlet_profile_g = None

    try:
        os.chdir(case_path)

        if input_mode == "mesh_initialization":
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

            run_manifest_path = write_worker_run_manifest(
                cfg,
                mesh_case_path,
                case_path,
            )
            print(
                "Run manifest written with stop_reason=RUNNING: "
                f"{run_manifest_path}"
            )

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
        else:
            # Direct solver-mode launch failed on the Windows server with
            # "Failed to construct hwtree for collect command. 0x8000ffff".
            # Reuse the stable meshing-mode launch + switch_to_solver() path
            # from the mesh workflow instead.
            print("Launching Fluent in meshing mode for restart continuation...", flush=True)

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

            run_manifest_path = write_worker_run_manifest(
                cfg,
                mesh_case_path,
                case_path,
            )
            print(
                "Run manifest written with stop_reason=RUNNING: "
                f"{run_manifest_path}"
            )

            setup = solver.settings.setup
            solution = solver.settings.solution

            print("Reading restart case...", flush=True)
            solver.settings.file.read_case(
                file_name=as_fluent_path(staged_restart_case_file)
            )
            print("Restart case loaded successfully.", flush=True)

            print("Reading restart data...", flush=True)
            solver.settings.file.read_data(
                file_name=as_fluent_path(staged_restart_data_file)
            )
            print("Restart data loaded successfully.", flush=True)

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
        append_zone_discovery_to_solver_log(
            solver_log_path,
            boundary_zone_names,
            wall_spacer_zones,
        )


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
        diffusivity_state = mixture_object.mass_diffusivity.get_state()
        print(diffusivity_state)
        assert_fluent_mass_diffusivity_matches_config(
            diffusivity_state,
            mass_diffusivity,
        )
        print(
            f"Mixture mass diffusivity matches run_config.mass_diffusivity="
            f"{mass_diffusivity}"
        )

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
            # Before libudf compile/load: write plug when the spec is
            # Magnitude (template). A restart final is already Components, so
            # apply_inlet_velocity_boundary skips the inactive magnitude leaf
            # and sets the x-UDF hook instead. Profile is re-applied after load.
            apply_inlet_velocity_boundary(
                vin,
                inlet_zone_name,
                inlet_velocity,
                use_profile=False,
                profile_udf_name=inlet_profile_function_name,
                solver=solver,
                debug_inlet_bc_api=debug_inlet_bc_api,
            )
            vin.species.species_mass_fraction[species_name].value = salt_mass_fraction
            print(f"Inlet species set on {inlet_zone_name}: {species_name}={salt_mass_fraction}")
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
        membrane_blocked_area_frac = float(
            read_mesh_manifest(mesh_case_path)["membrane_blocked_area_frac"]
        )
        update_transport_report_definitions_for_current_zones(
            solution=solution,
            inlet_zones=inlet_zone_names,
            outlet_zones=outlet_zone_names,
            membrane_wall_zones=membrane_wall_zone_names,
            density_value=mixture_density,
            membrane_blocked_area_frac=membrane_blocked_area_frac,
            m_in_name=m_in_report_name,
            m_out_name=m_out_report_name,
            area_mem_name=area_mem_report_name,
            lmh_name=lmh_report_name,
            lmh_signed_name=lmh_signed_report_name,
        )


        # ======================================================
        # ##### [11] UDM Allocation + UDF Compile/Load #####
        # ======================================================

        print(f"UDF active membrane wall base names (hardcoded in UDF): {membrane_wall_base_names}")
        print(
            f"UDF UDM layout (must match {cfg.udf_source_file_name} "
            "UDM_COUNT=13 default dual):"
        )
        print("  0 SI, 1 TOTAL_S, 2-4 X/Y/ZMOM, 5 STRAIN_RATE (cell)")
        print("  6 JW, 7 CM, 8 LMH, 9 CP (film-theory), 10 SALT_FLUX (face+optional cell)")
        print("  11 AREA (cell-diag accumulator)")
        print("  12 Y1 (face: wall-to-adjacent-centroid distance [m])")
        print("  UDF-5  = cell-centered strain rate magnitude [1/s] (not wall shear rate)")
        print("  UDF-9  = film-theory CP, (cm-cp_perm)/(C_INLET_REF-cp_perm), Jw unramped")
        print("  UDF-10 = salt mass flux [kg/m2/s] (was UDF-12)")
        print("  UDF-12 = y1 wall-to-centroid distance [m]")
        print("  Live compare face vs cell LMH: surface-areaavg(udm-8) with FACE/CELL switches")
        print(f"Using SALT_YI_INDEX = {salt_yi_index}")

        # Allocate User-Defined Memory using Fluent TUI.
        # The settings API path setup.user_defined.memory is not available in this solver tree.
        # Hardcoded in run_config.py as udm_count (keep in sync with UDF UDM_COUNT).
        solver.execute_tui(f"/define/user-defined/user-defined-memory {udm_count}")

        print(f"Requested UDM memory locations: {udm_count}")

        if input_mode == "restart_continuation":
            require_restart_read_not_source_folder(
                staged_restart_case_file,
                staged_restart_data_file,
                case_path,
                restart_source_dir,
                source_case_file=restart_from_case_file,
                source_data_file=restart_from_data_file,
            )

        udf_case_path = copy_and_patch_udf_to_case_folder(
            source_path=udf_master_path,
            destination_path=udf_case_path,
            salt_yi_index_value=salt_yi_index,
            u_target_value=inlet_velocity,
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

        # Always-on geometry probe (read-only; does not change BCs).
        # Must run after libudf is loaded. Writes to the main solver transcript.
        # Not gated on run_inlet_profile_probe / use_inlet_velocity_profile:
        # a False probe flag must not skip RO_UDF_INLET_PROFILE_G.
        probe_blocks_before = count_inlet_probe_markers(
            case_path, solver_log_path
        )
        probe_tui = (
            f'/define/user-defined/execute-on-demand '
            f'"{inlet_probe_function_name}"'
        )
        print("Executing inlet profile probe TUI command:")
        print(probe_tui)
        solver.execute_tui(probe_tui)
        # Do not stop/restart the PyFluent transcript here: Transcript.start
        # in ansys-fluent-core 0.38.0 truncates solver_log_*.txt.
        assert_transcript_contains(
            INLET_PROBE_MARKER,
            "probe_inlet_profile",
            case_dir=case_path,
            solver_log_path=solver_log_path,
        )
        inlet_profile_g = wait_for_agreed_inlet_profile_g(
            case_dir=case_path,
            solver_log_path=solver_log_path,
        )
        wait_for_agreed_probe_u_target(
            case_dir=case_path,
            solver_log_path=solver_log_path,
            expected=inlet_velocity,
            min_probe_blocks=probe_blocks_before + 1,
        )
        run_payload = read_run_manifest(case_path)
        apply_parsed_inlet_profile_g(
            mesh_case_path,
            inlet_profile_g,
            u_mean_ms=run_payload.get("u_mean_ms"),
            u_target_ms=run_payload["u_target_ms"],
            inlet_bc_type=run_payload["inlet_bc_type"],
        )

        if use_inlet_velocity_profile:
            print("\nRe-applying inlet BC as Components + UDF after libudf load...")
            for inlet_zone_name in inlet_zone_names:
                vin = setup.boundary_conditions.velocity_inlet[inlet_zone_name]
                apply_inlet_velocity_boundary(
                    vin,
                    inlet_zone_name,
                    inlet_velocity,
                    use_profile=True,
                    profile_udf_name=inlet_profile_function_name,
                    solver=solver,
                    debug_inlet_bc_api=debug_inlet_bc_api,
                )
                print(vin.get_state())


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

        deferred_solve_time_qoi_report_specs = []
        if enable_solve_time_qoi_reports:
            (
                _created_wall_qoi_reports,
                deferred_solve_time_qoi_report_specs,
            ) = update_solve_time_wall_qoi_report_definitions(
                solution=solution,
                membrane_wall_zones=membrane_wall_zone_names,
            )
        else:
            print("Skipping solve-time QoI report definition update.")

        # ======================================================
        # ##### [14] Initialization and Species Patch #####
        # ======================================================

        if input_mode == "restart_continuation":
            print(
                "Restart continuation mode: skipping hybrid initialization and species patch "
                "so the loaded restart data remains the initial solution."
            )
            print("Initialization state from restart data:")
            print(solution.initialization.get_state())
        else:
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

        if enable_solve_time_qoi_reports:
            retry_deferred_solve_time_qoi_report_definitions(
                solution=solution,
                deferred_report_specs=(
                    deferred_solve_time_qoi_report_specs
                ),
            )
            qoi_layout = layout_from_mesh_manifest(mesh_case_path).layout
            update_solve_time_pressure_qoi_report_definitions(
                solver=solver,
                solution=solution,
                layout=qoi_layout,
                domain_x_min_m=domain_x_min_m,
            )


        # ======================================================
        # ##### [15] Residual Settings #####
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

        qoi_convergence_object_names = []
        qoi_stop_report_file_paths = []
        lmh_report_file_path = os.path.join(case_path, lmh_udm_avg_report_file_name)
        dp_report_file_path = os.path.join(
            case_path, pressure_drop_spacer_report_file_name
        )
        if enable_solve_time_qoi_reports and enable_lmh_udm_avg_report_file:
            ensure_lmh_udm_avg_report_file(
                solution=solution,
                report_name=qoi_convergence_report_name,
                file_name=lmh_udm_avg_report_file_name,
            )
            qoi_stop_report_file_paths.append(lmh_report_file_path)
            print(f"QoI report file path (case dir): {lmh_report_file_path}")
        if enable_solve_time_qoi_reports and enable_pressure_drop_spacer_report_file:
            ensure_lmh_udm_avg_report_file(
                solution=solution,
                report_name=QOI_STOP_PRESSURE_REPORT_NAME,
                file_name=pressure_drop_spacer_report_file_name,
            )
            qoi_stop_report_file_paths.append(dp_report_file_path)
            print(f"QoI report file path (case dir): {dp_report_file_path}")

        if enable_qoi_convergence_stop:
            qoi_stop_report_names = [
                qoi_convergence_report_name,
                QOI_STOP_PRESSURE_REPORT_NAME,
            ]
            print(
                "QoI convergence stop: "
                f"{QOI_CONVERGENCE_CONDITION} on {qoi_stop_report_names} "
                f"(relative stop_criterion={qoi_stop_criterion}) "
                "with residual check_convergence left on."
            )
            for report_name in qoi_stop_report_names:
                qoi_convergence_object_names.append(
                    configure_qoi_convergence_condition(
                        solution=solution,
                        report_name=report_name,
                        stop_criterion=qoi_stop_criterion,
                        previous_values_to_consider=qoi_previous_values_to_consider,
                        initial_values_to_ignore=qoi_initial_values_to_ignore,
                        active=not use_ramp_convergence_safety,
                    )
                )

        relaxation_result = apply_real_under_relaxation(
            solver=solver,
            profile=relaxation_profile,
            species_name=species_name,
        )
        relaxation_result.setdefault("applied", []).append(
            apply_species_implicit_under_relaxation(
                solution=solution,
                species_name=species_name,
                value=species_implicit_under_relaxation,
            )
        )
        verbosity_result = apply_pseudo_time_verbosity(
            solution=solution,
            value=pseudo_time_verbosity,
        )
        print("Relaxation profile application result:")
        print(relaxation_result)
        print("Pseudo-time verbosity application result:")
        print(verbosity_result)
        gts_scale_result = apply_gts_time_step_size_scale_factor(
            solution,
            pseudo_time_time_step_size_scale_factor,
        )
        print("GTS time_step_size_scale_factor application result:")
        print(gts_scale_result)
        if not _config_is_preserve(pseudo_time_time_step_size_scale_factor):
            if (
                _config_is_preserve(pseudo_time_verbosity)
                or int(pseudo_time_verbosity) != 1
            ):
                raise GtsTimeStepScaleFactorError(
                    "GTS scale-factor pilot requires pseudo_time_verbosity=1"
                )
            if verbosity_result.get("status") != "APPLIED_CONFIRMED":
                raise GtsTimeStepScaleFactorError(
                    "pseudo_time_verbosity=1 readback failed: "
                    f"{verbosity_result}"
                )


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
        #   Phase 1 runs a fixed number of iterations with residual convergence stopping disabled
        #   and QoI convergence conditions inactive.
        #   Phase 2 activates LMH and spacer-dP QoI conditions
        #   (all-conditions-are-met) and re-enables residual
        #   check_convergence so Fluent stops on LMH AND dP AND residual.

        solver_stop_reason = None
        calculation_diverged = False
        if run_calculation_enabled:
            print("Starting solver calculation.")
            print(f"Maximum iterations requested: {max_iterations}")
            print(f"Residual target: {residual_target}")
            print(f"QoI convergence stop enabled: {enable_qoi_convergence_stop}")

            if use_ramp_convergence_safety:
                pre_convergence_iterations = minimum_full_source_iterations
                remaining_iterations = max_iterations - pre_convergence_iterations
                if remaining_iterations <= 0:
                    raise ValueError(
                        "max_iterations must be larger than minimum_full_source_iterations. "
                        f"max_iterations={max_iterations}, "
                        f"minimum_full_source_iterations={minimum_full_source_iterations}"
                    )

            try:
                if use_ramp_convergence_safety:
                    print(
                        "\nRunning ramp-up phase without residual/QoI convergence stopping.\n"
                        f"Ramp full iteration: {ramp_full_iteration}\n"
                        f"Post-ramp buffer iterations: {post_ramp_buffer_iterations}\n"
                        f"Ramp-up phase iterations: {pre_convergence_iterations}"
                    )

                    set_residual_convergence_check(
                        solution=solution,
                        species_name=species_name,
                        enable=False,
                    )
                    for object_name in qoi_convergence_object_names:
                        set_qoi_convergence_condition_active(
                            solution,
                            object_name,
                            False,
                        )

                    solution.run_calculation.iterate(
                        iter_count=pre_convergence_iterations
                    )

                    print(
                        "\nRamp-up phase completed. "
                        "The membrane source ramp should now be fully applied."
                    )

                    print(
                        "\nEnabling convergence-phase stopping: "
                        f"QoI reports active={bool(qoi_convergence_object_names)}, "
                        "residual check_convergence=True."
                    )

                    set_residual_convergence_check(
                        solution=solution,
                        species_name=species_name,
                        enable=True,
                    )
                    for object_name in qoi_convergence_object_names:
                        set_qoi_convergence_condition_active(
                            solution,
                            object_name,
                            True,
                        )

                    print(
                        f"\nRunning convergence phase. "
                        f"Maximum additional iterations: {remaining_iterations}"
                    )

                    solution.run_calculation.iterate(iter_count=remaining_iterations)

                else:
                    print("\nRamp convergence safety is disabled.")
                    set_residual_convergence_check(
                        solution=solution,
                        species_name=species_name,
                        enable=True,
                    )
                    for object_name in qoi_convergence_object_names:
                        set_qoi_convergence_condition_active(
                            solution,
                            object_name,
                            True,
                        )
                    solution.run_calculation.iterate(iter_count=max_iterations)
            except Exception as calc_exc:
                calculation_diverged = True
                print(f"Solver calculation raised: {type(calc_exc).__name__}: {calc_exc}")
                raise
            finally:
                try:
                    # Printed here (after iterate, before write_case_data /
                    # transcript assertions) so post-iterate failures still
                    # retain why iteration ended.
                    solver_stop_reason = determine_and_print_stop_reason(
                        case_dir=case_path,
                        solver_log_path=solver_log_path,
                        max_iterations=max_iterations,
                        qoi_enabled=bool(enable_qoi_convergence_stop),
                        qoi_report_file_paths=qoi_stop_report_file_paths,
                        qoi_previous_values_to_consider=(
                            qoi_previous_values_to_consider
                        ),
                        qoi_stop_criterion=qoi_stop_criterion,
                        diverged=calculation_diverged,
                        calculation_ran=True,
                    )
                except Exception as stop_exc:
                    print(
                        "WARNING: could not determine stop reason: "
                        f"{type(stop_exc).__name__}: {stop_exc}"
                    )
                    solver_stop_reason = None

            print("\nSolver calculation completed.")

            print("\nFinal residual equations state:")
            print(solution.monitor.residual.equations.get_state())

            if use_inlet_velocity_profile:
                # Profile diagnostic prints on first profile-hook evaluation
                # (typically during iterate). Prefer fluent-*.trn; do not bounce
                # the PyFluent transcript (start truncates the log file).
                assert_transcript_contains(
                    INLET_PROFILE_MARKER,
                    "inlet_x_velocity_profile",
                    case_dir=case_path,
                    solver_log_path=solver_log_path,
                )

            inlet_profile_g = wait_for_agreed_inlet_profile_g(
                case_dir=case_path,
                solver_log_path=solver_log_path,
            )

        else:
            print("Run calculation is disabled. Skipping solver iterations.")
            try:
                solver_stop_reason = determine_and_print_stop_reason(
                    case_dir=case_path,
                    solver_log_path=solver_log_path,
                    max_iterations=max_iterations,
                    qoi_enabled=False,
                    qoi_report_file_paths=qoi_stop_report_file_paths,
                    qoi_previous_values_to_consider=qoi_previous_values_to_consider,
                    qoi_stop_criterion=qoi_stop_criterion,
                    diverged=False,
                    calculation_ran=False,
                )
            except Exception as stop_exc:
                print(
                    "WARNING: could not determine stop reason: "
                    f"{type(stop_exc).__name__}: {stop_exc}"
                )
                solver_stop_reason = None

        publish_stop_reason_and_finals(
            solver,
            case_path,
            solver_stop_reason,
            inlet_profile_g=inlet_profile_g,
            final_case_file=final_case_file,
            as_fluent_path=as_fluent_path,
        )
        if input_mode == "restart_continuation":
            remove_staged_restart_copies(
                staged_restart_case_file,
                staged_restart_data_file,
            )


    except Exception as e:
        print("\n" + "=" * 72)
        print("ERROR: Solver automation failed.")
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

    # Artifact verification runs after try/except/finally so cleanup always
    # executes. SystemExit is not raised inside the try block (except Exception
    # would not catch it anyway, but placement keeps failure semantics clear).
    artifact_failures = collect_solver_final_artifact_failures(
        final_case_file,
        final_data_file,
    )
    if artifact_failures:
        print("\n" + "=" * 72)
        print("ERROR: Final case/data artifacts are missing or empty after write.")
        print("=" * 72)
        for message in artifact_failures:
            print(message)
        print("=" * 72 + "\n")
        sys.exit(SOLVER_EXIT_ARTIFACT_FAILURE)

    print(f"Verified final case file: {final_case_file}")
    print(f"Verified final data file: {final_data_file}")
    try:
        stamp_run_manifest_final_artifact_hashes(
            case_path,
            final_case_file,
            final_data_file,
        )
    except Exception as stamp_exc:
        print("\n" + "=" * 72)
        print("ERROR: Could not record current-attempt final artifact hashes.")
        print("=" * 72)
        print(f"{type(stamp_exc).__name__}: {stamp_exc}")
        print("=" * 72 + "\n")
        sys.exit(1)
