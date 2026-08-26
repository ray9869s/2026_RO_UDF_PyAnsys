# ==========================================================
# ##### [0] Import Required Packages #####
# ==========================================================
import ansys.fluent.core as pyfluent
import argparse
import hashlib
import os
import re
import json
import importlib.util
import time
from datetime import datetime, timezone
from pathlib import Path

from ro.campaign_geometry import merge_geometry_into_mesh_manifest
from ro.manifest import (
    MANIFEST_SCHEMA_VERSION,
    assert_mesh_file_overwrite_allowed,
    write_mesh_manifest,
)
from ro.mesh_common import (
    MESH_METRIC_NAMES,
    _parse_surface_skewness_table,
    build_mesh_ledger_record,
    mesh_parameters_from_mapping,
    parse_last_float as _parse_last_float,
    parse_mesh_metrics_from_log,
    write_mesh_run_record,
)
from ro.paths import geometry_dir, mesh_dir, project_root


def _utc_now_string():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mesh_manifest_payload(cfg, mesh_metrics, mesh_sha256, *, created_utc=None):
    base = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "family": cfg.family,
        "geo_id": cfg.geo_id,
        "mesh_id": cfg.mesh_id,
        "spacing_code": cfg.spacing_code,
        "attack_angle_deg": cfg.attack_angle_deg,
        "filament_d_m": cfg.filament_d_m,
        "bridge_radius_m": cfg.bridge_radius_m,
        "overlap_m": cfg.overlap_m,
        "n_active_cells": cfg.n_active_cells,
        "n_buffer_in": cfg.n_buffer_in,
        "n_buffer_out": cfg.n_buffer_out,
        "cell_length_x_m": cfg.cell_length_x_m,
        "buffer_length_in_m": cfg.buffer_length_in_m,
        "buffer_length_out_m": cfg.buffer_length_out_m,
        "membrane_wall_base_names": list(cfg.active_membrane_wall_labels),
        "buffer_wall_base_names": list(cfg.buffer_wall_labels),
        "n_lead_excluded": cfg.n_lead_excluded,
        "n_trail_excluded": cfg.n_trail_excluded,
        "max_size_mm": cfg.m_max,
        "min_size_mm": cfg.m_min,
        "cpg": cfg.m_cpg,
        "bl": cfg.bl_layers,
        "peel": cfg.peel_layers,
        "ortho_min": mesh_metrics["min_orthogonal_quality"],
        "AR_max": mesh_metrics["max_aspect_ratio"],
        "skewness_max": mesh_metrics["max_skewness"],
        "skewed_face_fraction": mesh_metrics["skewed_face_fraction"],
        "cell_count": mesh_metrics["cell_count"],
        "inlet_profile_G": None,
        "mesh_sha256": mesh_sha256,
        "created_utc": created_utc or _utc_now_string(),
        "generator_version": Path(__file__).name,
    }
    return merge_geometry_into_mesh_manifest(base, cfg.geo_id)


def write_worker_mesh_manifest(
    cfg,
    mesh_directory,
    mesh_file,
    mesh_metrics,
    *,
    created_utc=None,
):
    payload = build_mesh_manifest_payload(
        cfg,
        mesh_metrics,
        _sha256_file(mesh_file),
        created_utc=created_utc,
    )
    return write_mesh_manifest(mesh_directory, payload)


def resolve_meshing_paths(cfg):
    geometry_file = geometry_dir(cfg.family, cfg.geo_id) / f"{cfg.geo_id}.dsco"
    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    return {
        "geometry_file": geometry_file,
        "mesh_directory": mesh_directory,
        "mesh_log": mesh_directory / f"mesh_log_{cfg.mesh_id}.txt",
        "mesh_file": mesh_directory / f"{cfg.geo_id}_{cfg.mesh_id}.msh.h5",
        "mesh_run_record": mesh_directory / "mesh_run_record.json",
        "surface_mesh_checkpoint": (
            mesh_directory
            / f"{cfg.geo_id}_{cfg.mesh_id}_surface_checkpoint.msh.h5"
        ),
    }


def parse_meshing_cli(argv=None):
    parser = argparse.ArgumentParser(description="Fluent meshing worker.")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace an existing hashed .msh.h5 that has no dependent run "
            "manifests. Refused if any run still references this mesh_sha256."
        ),
    )
    return parser.parse_args(argv)

# ==========================================================
# ##### [1] Load Run Configuration #####
# ==========================================================

# The script body below runs only when this file is executed directly.
# Importing this module must not launch Fluent or write any files.
if __name__ == "__main__":
    meshing_cli = parse_meshing_cli()
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
    # opts out of validate_for_meshing() after overrides are applied.
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
        cfg.validate_for_meshing()

    # [Common project/case settings]
    geo_name = cfg.geo_id
    case_name = cfg.mesh_id

    # [Fluent launch settings]
    product_version = cfg.product_version
    processor_count = cfg.processor_count
    graphics_driver = cfg.graphics_driver

    # [Mesh size]
    m_max = cfg.m_max
    m_min = cfg.m_min
    m_cpg = cfg.m_cpg

    # [Face labels]
    wall_spacer_labels = list(cfg.wall_spacer_labels)

    # Active RO membrane wall named selections.
    active_membrane_wall_labels = list(cfg.active_membrane_wall_labels)

    # Buffer no-slip wall named selections.
    buffer_wall_labels = list(cfg.buffer_wall_labels)

    # membrane_wall_labels refers to active RO membrane walls only.
    membrane_wall_labels = active_membrane_wall_labels

    periodic_labels = list(cfg.periodic_labels)
    periodic_reference_label = cfg.periodic_reference_label

    include_spacer_in_boundary_layers = bool(
        cfg.include_spacer_in_boundary_layers
    )

    # Boundary layers: membrane + buffer always; spacer optional (local sizing
    # still uses wall_spacer_labels independently).
    boundary_layer_labels = (
        active_membrane_wall_labels
        + buffer_wall_labels
        + (wall_spacer_labels if include_spacer_in_boundary_layers else [])
    )

    # [Periodic condition]
    periodic_shift_x = cfg.periodic_shift_x
    periodic_shift_y = cfg.periodic_shift_y
    periodic_shift_z = cfg.periodic_shift_z

    # [Local sizing controls]
    boi_curvature_normal_angle = cfg.boi_curvature_normal_angle
    boi_growth_rate = cfg.boi_growth_rate

    # [Boundary layer]
    bl_height_factor = cfg.bl_height_factor
    bl_height = m_min * bl_height_factor
    bl_layers = cfg.bl_layers
    bl_offset_method = cfg.bl_offset_method
    bl_growth_rate = cfg.bl_growth_rate

    # [Volume mesh]
    vol_hex_max_factor = cfg.vol_hex_max_factor
    vol_hex_max = m_max * vol_hex_max_factor
    peel_layers = cfg.peel_layers

    # [Mesh quality gate]
    min_orthogonal_quality_threshold = cfg.min_orthogonal_quality_threshold
    max_aspect_ratio_threshold = cfg.max_aspect_ratio_threshold
    max_skewness_threshold = cfg.max_skewness_threshold
    skewed_face_fraction_threshold = cfg.skewed_face_fraction_threshold
    fail_if_quality_not_parsed = cfg.fail_if_quality_not_parsed

    # [Checkpoint options]
    save_surface_mesh_checkpoint = cfg.save_surface_mesh_checkpoint
    periodic_after_surface_mesh = bool(cfg.periodic_after_surface_mesh)

    # ==========================================================
    # ##### [2] Basic Parameter Checks #####
    # ==========================================================

    is_empty_channel = geo_name.strip().lower() == "empty"

    if not wall_spacer_labels:
        if is_empty_channel:
            print(
                "No wall spacer labels were provided. "
                "Running as an empty-channel reference case."
            )
        else:
            raise ValueError(
                "wall_spacer_labels must contain at least one face label "
                "for non-empty spacer geometries. "
                f"geo_name={geo_name}, wall_spacer_labels={wall_spacer_labels}"
            )

    if not membrane_wall_labels:
        raise ValueError("active_membrane_wall_labels must contain at least one face label.")

    if not buffer_wall_labels:
        raise ValueError("buffer_wall_labels must contain at least one face label.")

    if not periodic_labels:
        raise ValueError("periodic_labels must contain at least one face label.")

    if periodic_reference_label not in periodic_labels:
        raise ValueError(
            f"periodic_reference_label must be included in periodic_labels. "
            f"periodic_reference_label={periodic_reference_label}, "
            f"periodic_labels={periodic_labels}"
        )

    if not boundary_layer_labels:
        raise ValueError("boundary_layer_labels must contain at least one face label.")

    if min_orthogonal_quality_threshold <= 0.0:
        raise ValueError("min_orthogonal_quality_threshold must be greater than 0.")

    if max_aspect_ratio_threshold is not None and max_aspect_ratio_threshold <= 0.0:
        raise ValueError("max_aspect_ratio_threshold must be positive or None.")

    if max_skewness_threshold is not None and max_skewness_threshold <= 0.0:
        raise ValueError("max_skewness_threshold must be positive or None.")

    if (
        skewed_face_fraction_threshold is not None
        and not (0.0 < skewed_face_fraction_threshold <= 1.0)
    ):
        raise ValueError(
            "skewed_face_fraction_threshold must be in (0, 1] or None."
        )

    wall_spacer_boundary_layer_overlap = (
        set(wall_spacer_labels) & set(boundary_layer_labels)
    )

    if wall_spacer_boundary_layer_overlap:
        print(
            "Note: Some labels appear in both wall_spacer_labels and "
            "boundary_layer_labels. This is intentional when applying both "
            "local sizing and boundary layers to spacer walls. "
            f"Overlap: {sorted(wall_spacer_boundary_layer_overlap)}"
        )

    periodic_wall_spacer_overlap = set(periodic_labels) & set(wall_spacer_labels)
    if periodic_wall_spacer_overlap:
        raise ValueError(
            f"Labels appear in both periodic_labels and wall_spacer_labels: "
            f"{sorted(periodic_wall_spacer_overlap)}"
        )

    periodic_boundary_layer_overlap = set(periodic_labels) & set(boundary_layer_labels)
    if periodic_boundary_layer_overlap:
        raise ValueError(
            f"Labels appear in both periodic_labels and boundary_layer_labels: "
            f"{sorted(periodic_boundary_layer_overlap)}"
        )

    membrane_wall_periodic_overlap = set(membrane_wall_labels) & set(periodic_labels)
    if membrane_wall_periodic_overlap:
        raise ValueError(
            f"Labels appear in both active_membrane_wall_labels and periodic_labels: "
            f"{sorted(membrane_wall_periodic_overlap)}"
        )

    membrane_wall_spacer_overlap = set(membrane_wall_labels) & set(wall_spacer_labels)
    if membrane_wall_spacer_overlap:
        raise ValueError(
            f"Labels appear in both active_membrane_wall_labels and wall_spacer_labels: "
            f"{sorted(membrane_wall_spacer_overlap)}"
        )

    active_buffer_overlap = set(active_membrane_wall_labels) & set(buffer_wall_labels)
    if active_buffer_overlap:
        raise ValueError(
            f"Labels appear in both active_membrane_wall_labels and buffer_wall_labels: "
            f"{sorted(active_buffer_overlap)}"
        )

    buffer_spacer_overlap = set(buffer_wall_labels) & set(wall_spacer_labels)
    if buffer_spacer_overlap:
        raise ValueError(
            f"Labels appear in both buffer_wall_labels and wall_spacer_labels: "
            f"{sorted(buffer_spacer_overlap)}"
        )

    buffer_periodic_overlap = set(buffer_wall_labels) & set(periodic_labels)
    if buffer_periodic_overlap:
        raise ValueError(
            f"Labels appear in both buffer_wall_labels and periodic_labels: "
            f"{sorted(buffer_periodic_overlap)}"
        )


    # ==========================================================
    # ##### [3] Path and File Name Settings #####
    # ==========================================================

    resolved_paths = resolve_meshing_paths(cfg)
    geo_full_path = resolved_paths["geometry_file"]
    case_path = resolved_paths["mesh_directory"]
    mesh_log_path = resolved_paths["mesh_log"]
    mesh_file_path = resolved_paths["mesh_file"]
    mesh_run_record_path = resolved_paths["mesh_run_record"]
    surface_mesh_checkpoint_path = resolved_paths["surface_mesh_checkpoint"]

    if not os.path.isfile(geo_full_path):
        raise FileNotFoundError(f"Geometry file not found: {geo_full_path}")

    if not os.path.exists(case_path):
        os.makedirs(case_path)

    assert_mesh_file_overwrite_allowed(
        mesh_file_path,
        case_path,
        force=meshing_cli.force,
    )


# ==========================================================
# ##### [4] Helper Functions #####
# ==========================================================

def as_fluent_path(path):
    """Convert an absolute file path to a Fluent-friendly path string."""
    return os.path.abspath(path).replace("\\", "/")


def remove_existing_file(path):
    """Remove an existing output file before writing a new one."""
    try:
        if os.path.isfile(path):
            os.remove(path)
            print(f"Removed existing file: {path}")
    except OSError as e:
        raise OSError(f"Could not remove existing file: {path}. Error: {e}")


def write_mesh_file(meshing_session, output_path, description):
    """Write a mesh file and verify that the file was created."""
    remove_existing_file(output_path)

    fluent_output_path = as_fluent_path(output_path)
    print(f"Writing {description} to: {fluent_output_path}")

    meshing_session.tui.file.write_mesh(fluent_output_path)

    if os.path.isfile(output_path):
        print(f"{description} saved: {output_path}")
    else:
        raise FileNotFoundError(f"{description} was not created: {output_path}")


def print_meshing_input_summary():
    """Print all user-specified meshing controls to the run log/output."""
    print("\n" + "=" * 72)
    print("MESHING INPUT SUMMARY")
    print("=" * 72)
    print(f"Geometry name: {geo_name}")
    print(f"Case name: {case_name}")
    print(f"Geometry file: {geo_full_path}")
    print(f"Maximum size, m_max [mm]: {m_max}")
    print(f"Minimum size, m_min [mm]: {m_min}")
    print(f"Cells per gap, m_cpg [-]: {m_cpg}")
    print(f"Active membrane wall labels: {active_membrane_wall_labels}")
    print(f"Buffer wall labels: {buffer_wall_labels}")
    print(f"Wall spacer labels for local sizing: {wall_spacer_labels}")
    print(f"Periodic labels: {periodic_labels}")
    print(f"Periodic reference label: {periodic_reference_label}")
    print(
        "Periodic translation [mm]: "
        f"dx={periodic_shift_x}, dy={periodic_shift_y}, dz={periodic_shift_z}"
    )
    print(f"BOI curvature normal angle [deg]: {boi_curvature_normal_angle}")
    print(f"BOI growth rate [-]: {boi_growth_rate}")
    print(f"Boundary layer labels: {boundary_layer_labels}")
    print(f"Boundary layer offset method: {bl_offset_method}")
    print(f"Boundary layer first height factor [-]: {bl_height_factor}")
    print(f"Boundary layer first height [mm]: {bl_height}")
    print(f"Boundary layer number of layers [-]: {bl_layers}")
    print(f"Boundary layer growth rate [-]: {bl_growth_rate}")
    print(f"Volume mesh fill type: poly-hexcore")
    print(f"Volume hex max factor [-]: {vol_hex_max_factor}")
    print(f"Volume hex max cell length [mm]: {vol_hex_max}")
    print(f"Peel layers [-]: {peel_layers}")
    print(f"Minimum orthogonal quality threshold [-]: {min_orthogonal_quality_threshold}")
    print(f"Maximum aspect ratio threshold [-]: {max_aspect_ratio_threshold}")
    print(f"Maximum skewness threshold [-]: {max_skewness_threshold}")
    print(f"Skewed face fraction threshold [-]: {skewed_face_fraction_threshold}")
    print("=" * 72 + "\n")


def get_available_labels(task, complete_label_key):
    """Read available face labels from the current workflow task."""
    try:
        state = task.Arguments.get_state()
    except Exception as e:
        print(
            f"Warning: could not read available labels from task arguments. "
            f"Key={complete_label_key}. Error: {e}"
        )
        return []

    labels = state.get(complete_label_key, [])
    return [label for label in labels if isinstance(label, str)]


def validate_requested_labels(task, complete_label_key, requested_labels, label_usage):
    """Validate manually specified labels when the label list is available."""
    available_labels = get_available_labels(task, complete_label_key)

    if not available_labels:
        print(f"Could not read available labels for {label_usage}; skipping pre-check.")
        return

    missing_labels = sorted(set(requested_labels) - set(available_labels))

    if missing_labels:
        raise ValueError(
            f"Invalid {label_usage}: {missing_labels}. "
            f"Available labels: {available_labels}"
        )

    print(f"Validated {label_usage}: {requested_labels}")


def workflow_task_exists(workflow_object, task_name):
    """Check whether a workflow task exists."""
    try:
        workflow_object.TaskObject[task_name]
        return True
    except Exception as e:
        print(
            f"Warning: task existence check raised an exception for "
            f"'{task_name}'. Error: {e}"
        )
        return False


def ensure_periodic_boundary_task(workflow_object):
    """Insert the periodic boundary task only when it is not already present."""
    ensure_periodic_boundary_task_after(workflow_object, "Add Local Sizing")


def ensure_periodic_boundary_task_after(workflow_object, insert_after_task_name):
    """Insert Set Up Periodic Boundaries after a chosen existing task."""
    periodic_task_name = "Set Up Periodic Boundaries"

    if workflow_task_exists(workflow_object, periodic_task_name):
        print(f"Workflow task already exists: {periodic_task_name}")
        return

    workflow_object.TaskObject[insert_after_task_name].InsertNextTask(
        CommandName=r"SetUpPeriodicBoundaries"
    )

    if not workflow_task_exists(workflow_object, periodic_task_name):
        raise RuntimeError(f"Failed to insert workflow task: {periodic_task_name}")

    print(
        f"Inserted workflow task: {periodic_task_name} "
        f"(after {insert_after_task_name})"
    )


def parse_last_float(pattern, text):
    """Parse the last floating-point value matching a regular expression."""
    return _parse_last_float(pattern, text, flags=re.IGNORECASE)


def parse_mesh_quality_from_log(log_path):
    """Parse mesh quality values from the Fluent transcript."""
    metrics = parse_mesh_metrics_from_log(log_path)
    if not os.path.isfile(log_path):
        print(f"Quality log file not found: {log_path}")
    return (
        metrics["min_orthogonal_quality"],
        metrics["max_aspect_ratio"],
    )


def surface_mesh_continuation_log_path(mesh_log_path):
    """Return the sidecar transcript used after the surface-mesh quality flush."""
    mesh_log_path = Path(mesh_log_path)
    return mesh_log_path.with_name(
        f"{mesh_log_path.stem}_after_surface{mesh_log_path.suffix}"
    )


def append_transcript_continuation(log_path, continuation_path):
    """Append a continuation transcript onto the flushed log, then delete it.

    PyFluent's ``transcript.start(file_name=)`` deletes the target file, so the
    volume-mesh portion cannot be restarted onto the original log. The worker
    writes that portion to *continuation_path* and merges it here.
    """
    log_path = Path(log_path)
    continuation_path = Path(continuation_path)
    if not continuation_path.is_file():
        return
    extra = continuation_path.read_text(encoding="utf-8", errors="ignore")
    if extra:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(extra)
    continuation_path.unlink()


def apply_surface_mesh_quality_gate(
    log_path,
    max_skewness_limit,
    skewed_face_fraction_limit,
):
    """Refuse a bad or unreadable surface mesh before prism/volume generation.

    Uses only the Fluent surface-skewness table. An unparseable table is always
    a failure: ``fail_if_quality_not_parsed`` applies to the post-volume gate,
    where missing ortho/AR used to mean a formatting change. Here, continuing
    without a table is the MPI crash this check exists to stop.
    """
    log_path = Path(log_path)
    if log_path.is_file():
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    else:
        text = ""
        print(f"Surface quality log file not found: {log_path}")

    surface_table = _parse_surface_skewness_table(text)
    if surface_table is None:
        raise RuntimeError(
            "Could not parse the surface-mesh skewness table from the "
            "transcript. The surface mesh quality gate cannot be applied."
        )

    max_skewness = surface_table["max_skewness"]
    skewed_face_fraction = surface_table["skewed_face_fraction"]
    skewed_faces_over_080 = surface_table["skewed_faces_over_080"]
    surface_face_count = surface_table["surface_face_count"]

    print(f"Parsed surface maximum skewness: {max_skewness}")
    print(
        f"Parsed surface averaged skewness: {surface_table['averaged_skewness']}"
    )
    print(f"Parsed skewed faces over 0.80: {skewed_faces_over_080}")
    print(f"Parsed surface face count: {surface_face_count}")
    print(f"Parsed skewed face fraction: {skewed_face_fraction}")

    if max_skewness_limit is not None and max_skewness > max_skewness_limit:
        raise RuntimeError(
            "Surface mesh quality failed: maximum skewness "
            f"{max_skewness} is above the threshold {max_skewness_limit}."
        )

    if (
        skewed_face_fraction_limit is not None
        and skewed_face_fraction > skewed_face_fraction_limit
    ):
        raise RuntimeError(
            "Surface mesh quality failed: skewed face fraction "
            f"{skewed_face_fraction} "
            f"({skewed_faces_over_080} / {surface_face_count}) "
            f"is above the threshold {skewed_face_fraction_limit}."
        )

    print("Surface mesh quality gate passed (skewness only).")
    return surface_table


def apply_mesh_quality_gate(
    log_path,
    min_orthogonal_quality_limit,
    max_aspect_ratio_limit,
    max_skewness_limit,
    skewed_face_fraction_limit,
    fail_if_not_parsed,
):
    """Apply mesh quality pass/fail criteria using the Fluent transcript."""
    metrics = parse_mesh_metrics_from_log(log_path)
    min_orthogonal_quality = metrics["min_orthogonal_quality"]
    max_aspect_ratio = metrics["max_aspect_ratio"]
    max_skewness = metrics["max_skewness"]
    skewed_face_fraction = metrics["skewed_face_fraction"]
    skewed_faces_over_080 = metrics["skewed_faces_over_080"]
    surface_face_count = metrics["surface_face_count"]

    print(f"Parsed minimum orthogonal quality: {min_orthogonal_quality}")
    print(f"Parsed maximum aspect ratio: {max_aspect_ratio}")
    print(f"Parsed maximum skewness: {max_skewness}")
    print(f"Parsed averaged skewness: {metrics['averaged_skewness']}")
    print(f"Parsed skewed faces over 0.80: {skewed_faces_over_080}")
    print(f"Parsed surface face count: {surface_face_count}")
    print(f"Parsed skewed face fraction: {skewed_face_fraction}")
    print(
        "Parsed cells below min ortho quality: "
        f"{metrics['cells_below_min_ortho_quality']}"
    )
    print(f"Parsed cell count: {metrics['cell_count']}")

    if min_orthogonal_quality is None:
        message = (
            "Could not parse minimum orthogonal quality from the transcript. "
            "The mesh quality gate could not be fully applied."
        )

        if fail_if_not_parsed:
            raise RuntimeError(message)

        print(f"Warning: {message}")
    else:
        if min_orthogonal_quality < min_orthogonal_quality_limit:
            raise RuntimeError(
                f"Mesh quality failed: minimum orthogonal quality "
                f"{min_orthogonal_quality} is below the threshold "
                f"{min_orthogonal_quality_limit}."
            )

    if max_aspect_ratio_limit is not None:
        if max_aspect_ratio is None:
            message = (
                "Could not parse maximum aspect ratio from the transcript. "
                "The aspect ratio gate could not be applied."
            )

            if fail_if_not_parsed:
                raise RuntimeError(message)

            print(f"Warning: {message}")
        else:
            if max_aspect_ratio > max_aspect_ratio_limit:
                raise RuntimeError(
                    f"Mesh quality failed: maximum aspect ratio "
                    f"{max_aspect_ratio} is above the threshold "
                    f"{max_aspect_ratio_limit}."
                )

    if max_skewness_limit is not None:
        if max_skewness is None:
            message = (
                "Could not parse maximum skewness from the transcript. "
                "The skewness gate could not be applied."
            )

            if fail_if_not_parsed:
                raise RuntimeError(message)

            print(f"Warning: {message}")
        else:
            if max_skewness > max_skewness_limit:
                raise RuntimeError(
                    f"Mesh quality failed: maximum skewness "
                    f"{max_skewness} is above the threshold "
                    f"{max_skewness_limit}."
                )

    if skewed_face_fraction_limit is not None:
        if skewed_face_fraction is None:
            message = (
                "Could not parse skewed face fraction from the transcript. "
                "The skewed-face-count gate could not be applied."
            )

            if fail_if_not_parsed:
                raise RuntimeError(message)

            print(f"Warning: {message}")
        else:
            if skewed_face_fraction > skewed_face_fraction_limit:
                raise RuntimeError(
                    f"Mesh quality failed: skewed face fraction "
                    f"{skewed_face_fraction} "
                    f"({skewed_faces_over_080} / {surface_face_count}) "
                    f"is above the threshold {skewed_face_fraction_limit}."
                )

    print("Mesh quality gate passed.")
    return metrics


# ==========================================================
# ##### [5] Launch Fluent Meshing and Run Workflow #####
# ==========================================================

if __name__ == "__main__":
    meshing = None
    workflow = None
    transcript_is_running = False
    continuation_log_path = None
    original_working_directory = os.getcwd()
    run_started = time.monotonic()
    run_status = "FAILED"
    run_error = ""
    mesh_metrics = {name: None for name in MESH_METRIC_NAMES}

    try:
        os.chdir(case_path)

        meshing = pyfluent.launch_fluent(
            product_version=product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=processor_count,
            ui_mode="gui",
            graphics_driver=graphics_driver,
        )

        workflow = meshing.workflow

        meshing.transcript.start(file_name=as_fluent_path(mesh_log_path))
        transcript_is_running = True

        print(f"Meshing log will be saved to: {mesh_log_path}")
        print(f"Mesh file will be saved to: {mesh_file_path}")
        print(f"Surface mesh checkpoint will be saved to: {surface_mesh_checkpoint_path}")

        print_meshing_input_summary()

        # ======================================================
        # ##### [6] Start Workflow and Load Geometry #####
        # ======================================================

        workflow.InitializeWorkflow(WorkflowType=r"Watertight Geometry")

        workflow.TaskObject["Import Geometry"].Arguments.set_state({
            r"FileName": as_fluent_path(geo_full_path),
            r"ImportCadPreferences": {
                r"MaxFacetLength": 0,
            },
            r"LengthUnit": r"mm",
        })

        workflow.TaskObject["Import Geometry"].Execute()

        print("Active membrane wall labels:", active_membrane_wall_labels)
        print("Buffer wall labels:", buffer_wall_labels)
        print("Wall spacer face labels:", wall_spacer_labels)
        print("Boundary layer labels:", boundary_layer_labels)
        print("Periodic face labels:", periodic_labels)
        print("Periodic reference label:", periodic_reference_label)

        local_sizing_labels = [
            *periodic_labels,
            *wall_spacer_labels,
        ]

        # Validate local sizing labels after geometry import.
        validate_requested_labels(
            workflow.TaskObject["Add Local Sizing"],
            r"CompleteFaceLabelList",
            local_sizing_labels,
            "local sizing face labels",
        )

        # ======================================================
        # ##### [7] Local Sizing #####
        # ======================================================

        workflow.TaskObject["Add Local Sizing"].Arguments.set_state({
            r"AddChild": r"yes",
            r"BOICellsPerGap": m_cpg,
            r"BOIControlName": r"proximity_1",
            r"BOICurvatureNormalAngle": boi_curvature_normal_angle,
            r"BOIExecution": r"Proximity",
            r"BOIFaceLabelList": local_sizing_labels,
            r"BOIGrowthRate": boi_growth_rate,
            r"BOIMaxSize": m_max,
            r"BOIMinSize": m_min,
            r"BOIZoneorLabel": r"label",
        })

        workflow.TaskObject["Add Local Sizing"].AddChildAndUpdate(
            DeferUpdate=False
        )

        # ======================================================
        # ##### [8] Setup Periodic Boundaries (default: before surface mesh) #####
        # ======================================================

        if not periodic_after_surface_mesh:
            ensure_periodic_boundary_task(workflow)

            workflow.TaskObject["Set Up Periodic Boundaries"].Arguments.set_state({
                r"LabelList": [periodic_reference_label],
                r"Method": r"Manual - pick reference side",
                r"TransShift": {
                    r"ShiftX": periodic_shift_x,
                    r"ShiftY": periodic_shift_y,
                    r"ShiftZ": periodic_shift_z,
                },
                r"Type": r"Translational",
            })

            workflow.TaskObject["Set Up Periodic Boundaries"].Execute()

        # ======================================================
        # ##### [9] Generate Surface Mesh #####
        # ======================================================

        workflow.TaskObject["Generate the Surface Mesh"].Arguments.set_state({
            r"CFDSurfaceMeshControls": {
                r"CellsPerGap": m_cpg,
                r"MaxSize": m_max,
                r"MinSize": m_min,
                r"ScopeProximityTo": r"faces",
            },
        })

        workflow.TaskObject["Generate the Surface Mesh"].Execute()

        if save_surface_mesh_checkpoint:
            write_mesh_file(
                meshing_session=meshing,
                output_path=surface_mesh_checkpoint_path,
                description="Surface mesh checkpoint",
            )

        # Flush, refuse a bad surface, then resume onto a sidecar file.
        # transcript.start(file_name=) deletes its target, so the original
        # log cannot be reopened for the volume-mesh portion.
        meshing.transcript.stop()
        transcript_is_running = False
        apply_surface_mesh_quality_gate(
            log_path=mesh_log_path,
            max_skewness_limit=max_skewness_threshold,
            skewed_face_fraction_limit=skewed_face_fraction_threshold,
        )
        continuation_log_path = surface_mesh_continuation_log_path(mesh_log_path)
        meshing.transcript.start(
            file_name=as_fluent_path(continuation_log_path)
        )
        transcript_is_running = True

        # ======================================================
        # ##### [9b] Setup Periodic Boundaries (optional: after surface mesh) #####
        # ======================================================

        if periodic_after_surface_mesh:
            ensure_periodic_boundary_task_after(
                workflow, "Generate the Surface Mesh"
            )

            # Probe showed dangerous defaults after insert (Rotational, null
            # LabelList, TransShift z=1). Override Type/TransShift/LabelList
            # explicitly; RemeshBoundariesOption default is "auto".
            after_surface_periodic_labels = [periodic_reference_label] + [
                label
                for label in periodic_labels
                if label != periodic_reference_label
            ]

            workflow.TaskObject["Set Up Periodic Boundaries"].Arguments.set_state({
                r"LabelList": after_surface_periodic_labels,
                r"Method": r"Automatic - pick both sides",
                r"RemeshBoundariesOption": r"auto",
                r"TransShift": {
                    r"ShiftX": periodic_shift_x,
                    r"ShiftY": periodic_shift_y,
                    r"ShiftZ": periodic_shift_z,
                },
                r"Type": r"Translational",
            })

            workflow.TaskObject["Set Up Periodic Boundaries"].Execute()

        # ======================================================
        # ##### [10] Describe Geometry, Boundaries, and Regions #####
        # ======================================================

        workflow.TaskObject["Describe Geometry"].UpdateChildTasks(
            Arguments={
                r"v1": True,
            },
            SetupTypeChanged=False,
        )

        workflow.TaskObject["Describe Geometry"].Arguments.set_state({
            r"NonConformal": r"No",
            r"SetupType": r"The geometry consists of only fluid regions with no voids",
        })

        workflow.TaskObject["Describe Geometry"].UpdateChildTasks(
            Arguments={
                r"v1": True,
            },
            SetupTypeChanged=True,
        )

        workflow.TaskObject["Describe Geometry"].Execute()
        workflow.TaskObject["Update Boundaries"].Execute()
        workflow.TaskObject["Update Regions"].Execute()

        # Validate boundary layer labels after boundary and region updates.
        validate_requested_labels(
            workflow.TaskObject["Add Boundary Layers"],
            r"CompleteFaceLabelList",
            boundary_layer_labels,
            "boundary layer face labels",
        )

        # ======================================================
        # ##### [11] Add Boundary Layers #####
        # ======================================================

        workflow.TaskObject["Add Boundary Layers"].Arguments.set_state({
            r"BLControlName": r"smooth_transition_1",
            r"BlLabelList": boundary_layer_labels,
            r"FaceScope": {
                r"GrowOn": r"selected-labels",
            },
            r"FirstHeight": bl_height,
            r"NumberOfLayers": bl_layers,
            r"OffsetMethodType": bl_offset_method,
            r"Rate": bl_growth_rate,
        })

        workflow.TaskObject["Add Boundary Layers"].AddChildAndUpdate(
            DeferUpdate=False
        )

        # ======================================================
        # ##### [12] Generate Volume Mesh #####
        # ======================================================

        workflow.TaskObject["Generate the Volume Mesh"].Arguments.set_state({
            r"VolumeFill": r"poly-hexcore",
            r"VolumeFillControls": {
                r"HexMaxCellLength": vol_hex_max,
                r"PeelLayers": peel_layers,
            },
        })

        workflow.TaskObject["Generate the Volume Mesh"].Execute()

        # ======================================================
        # ##### [13] Quality Check and Save Final Mesh #####
        # ======================================================
    
        meshing.execute_tui(r"/mesh/check")
        meshing.execute_tui(r"/mesh/check-quality")

        print("\n" + "=" * 72)
        print("MESHING OUTPUT SUMMARY MARKER")
        print("=" * 72)
        print("Surface mesh face count and skewness are printed above by Fluent Meshing.")
        print("Volume mesh cell count, minimum orthogonal quality, and aspect ratio are printed above by Fluent Meshing.")
        print("Input controls repeated for traceability:")
        print(f"  Max size [mm] = {m_max}")
        print(f"  Min size [mm] = {m_min}")
        print(f"  Cells per gap = {m_cpg}")
        print(f"  Boundary layer labels = {boundary_layer_labels}")
        print(f"  Boundary layer offset method = {bl_offset_method}")
        print(f"  Boundary layer first height [mm] = {bl_height}")
        print(f"  Boundary layer number of layers = {bl_layers}")
        print(f"  Boundary layer growth rate = {bl_growth_rate}")
        print("=" * 72 + "\n")

        # Stop the transcript to flush mesh quality output before parsing the log file.
        meshing.transcript.stop()
        transcript_is_running = False
        if continuation_log_path is not None:
            append_transcript_continuation(mesh_log_path, continuation_log_path)
            continuation_log_path = None

        mesh_metrics = apply_mesh_quality_gate(
            log_path=mesh_log_path,
            min_orthogonal_quality_limit=min_orthogonal_quality_threshold,
            max_aspect_ratio_limit=max_aspect_ratio_threshold,
            max_skewness_limit=max_skewness_threshold,
            skewed_face_fraction_limit=skewed_face_fraction_threshold,
            fail_if_not_parsed=fail_if_quality_not_parsed,
        )

        print(
            f"Writing final mesh to: {mesh_file_path} "
            f"(transcript is already closed after quality parsing)."
        )

        write_mesh_file(
            meshing_session=meshing,
            output_path=mesh_file_path,
            description="Final volume mesh",
        )
        mesh_manifest_path = write_worker_mesh_manifest(
            cfg,
            case_path,
            mesh_file_path,
            mesh_metrics,
        )
        print(f"Mesh manifest written: {mesh_manifest_path}")
        run_status = "SUCCESS"

    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
        raise

    finally:
        if meshing is not None and transcript_is_running:
            try:
                # Flush and close the active transcript file.
                meshing.transcript.stop()
                transcript_is_running = False
            except Exception:
                pass

        if continuation_log_path is not None:
            try:
                append_transcript_continuation(
                    mesh_log_path, continuation_log_path
                )
            except Exception:
                pass
            continuation_log_path = None

        try:
            if not any(value is not None for value in mesh_metrics.values()):
                mesh_metrics = parse_mesh_metrics_from_log(mesh_log_path)
            mesh_parameters = mesh_parameters_from_mapping(globals())
            record = build_mesh_ledger_record(
                geo_name=cfg.geo_id,
                mesh_case_name=cfg.mesh_id,
                mesh_parameters=mesh_parameters,
                status=run_status,
                exit_code=0 if run_status == "SUCCESS" else 1,
                wall_time_seconds=time.monotonic() - run_started,
                metrics=mesh_metrics,
                mesh_log_path=mesh_log_path,
                mesh_file_path=mesh_file_path,
                error_summary=run_error,
            )
            write_mesh_run_record(mesh_run_record_path, record)
            print(f"Mesh run record written: {mesh_run_record_path}")
        except Exception as record_error:
            print(f"Warning: could not write mesh run record: {record_error}")

        if meshing is not None:
            try:
                # wait=True blocks until Fluent host/cortex PIDs are gone so the
                # Discovery CAD plugin session cannot contend with the next
                # batch case. Default wait=False returns while shutdown is
                # still in flight.
                meshing.exit(wait=True)
                print("Fluent meshing session exited (waited for process exit).")
            except Exception as exit_error:
                print(f"Warning: meshing.exit(wait=True) failed: {exit_error}")
                try:
                    meshing.force_exit()
                    print("Forced Fluent meshing session exit after wait failure.")
                except Exception as force_error:
                    print(f"Warning: meshing.force_exit() failed: {force_error}")

        try:
            os.chdir(original_working_directory)
        except Exception:
            pass