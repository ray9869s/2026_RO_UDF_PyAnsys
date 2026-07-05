# ==========================================================
# ##### [0] Import Required Packages #####
# ==========================================================
import ansys.fluent.core as pyfluent
import os
import re
import json
import importlib.util
from pathlib import Path

# ==========================================================
# ##### [1] Load Run Configuration #####
# ==========================================================

# The script body below runs only when this file is executed directly.
# Importing this module must not launch Fluent or write any files.
if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    _default_config = SCRIPT_DIR / "run_config.py"
    _env = os.environ.get("PYFLUENT_RUN_CONFIG")
    CONFIG_PATH = Path(_env or str(_default_config)).resolve()

    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Run config file not found: {CONFIG_PATH}. "
            "Set PYFLUENT_RUN_CONFIG or place run_config.py next to this meshing script."
        )

    config_spec = importlib.util.spec_from_file_location("active_run_config", CONFIG_PATH)
    cfg = importlib.util.module_from_spec(config_spec)
    config_spec.loader.exec_module(cfg)

    print(f"Loaded run config: {CONFIG_PATH}")

    # Per-case overrides from the batch drivers (JSON dict). Applied to the
    # config module before validation and parameter binding below.
    _overrides_env = os.environ.get("PYFLUENT_RUN_OVERRIDES")
    if _overrides_env:
        try:
            _overrides = json.loads(_overrides_env)
        except json.JSONDecodeError as e:
            raise ValueError(f"PYFLUENT_RUN_OVERRIDES is not valid JSON: {e}")
        if not isinstance(_overrides, dict):
            raise ValueError("PYFLUENT_RUN_OVERRIDES must be a JSON object.")
        for _key, _value in _overrides.items():
            setattr(cfg, _key, _value)
        print(f"Applied config overrides: {sorted(_overrides)}")

    if not _env:
        cfg.validate_for_meshing()

    # [Common project/case settings]
    project_root = cfg.project_root
    geo_name = cfg.geo_name
    case_name = cfg.case_name

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

    # Boundary layers are applied to active membrane walls, buffer walls, and spacer walls.
    boundary_layer_labels = (
        active_membrane_wall_labels
        + buffer_wall_labels
        + wall_spacer_labels
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
    fail_if_quality_not_parsed = cfg.fail_if_quality_not_parsed

    # [Checkpoint options]
    save_surface_mesh_checkpoint = cfg.save_surface_mesh_checkpoint

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

    geo_full_path = os.path.join(project_root, "00_Geometries", f"{geo_name}.dsco")
    case_path = os.path.join(project_root, "03_Results", geo_name, case_name)

    mesh_log_path = os.path.join(case_path, f"mesh_log_{case_name}.txt")
    mesh_file_path = os.path.join(case_path, f"{geo_name}_{case_name}.msh.h5")
    surface_mesh_checkpoint_path = os.path.join(
        case_path,
        f"{geo_name}_{case_name}_surface_checkpoint.msh.h5",
    )

    if not os.path.isfile(geo_full_path):
        raise FileNotFoundError(f"Geometry file not found: {geo_full_path}")

    if not os.path.exists(case_path):
        os.makedirs(case_path)


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
    periodic_task_name = "Set Up Periodic Boundaries"

    if workflow_task_exists(workflow_object, periodic_task_name):
        print(f"Workflow task already exists: {periodic_task_name}")
        return

    workflow_object.TaskObject["Add Local Sizing"].InsertNextTask(
        CommandName=r"SetUpPeriodicBoundaries"
    )

    if not workflow_task_exists(workflow_object, periodic_task_name):
        raise RuntimeError(f"Failed to insert workflow task: {periodic_task_name}")

    print(f"Inserted workflow task: {periodic_task_name}")


def parse_last_float(pattern, text):
    """Parse the last floating-point value matching a regular expression."""
    matches = re.findall(pattern, text, flags=re.IGNORECASE)

    if not matches:
        return None

    return float(matches[-1])


def parse_mesh_quality_from_log(log_path):
    """Parse mesh quality values from the Fluent transcript."""
    if not os.path.isfile(log_path):
        print(f"Quality log file not found: {log_path}")
        return None, None

    with open(log_path, "r", encoding="utf-8", errors="ignore") as file:
        text = file.read()

    min_orthogonal_quality = parse_last_float(
        r"Minimum\s+Orthogonal\s+Quality\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
        text,
    )

    if min_orthogonal_quality is None:
        min_orthogonal_quality = parse_last_float(
            r"minimum\s+Orthogonal\s+Quality\s+of:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
            text,
        )

    max_aspect_ratio = parse_last_float(
        r"Maximum\s+Aspect\s+Ratio\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
        text,
    )

    return min_orthogonal_quality, max_aspect_ratio


def apply_mesh_quality_gate(
    log_path,
    min_orthogonal_quality_limit,
    max_aspect_ratio_limit,
    fail_if_not_parsed,
):
    """Apply mesh quality pass/fail criteria using the Fluent transcript."""
    min_orthogonal_quality, max_aspect_ratio = parse_mesh_quality_from_log(log_path)

    print(f"Parsed minimum orthogonal quality: {min_orthogonal_quality}")
    print(f"Parsed maximum aspect ratio: {max_aspect_ratio}")

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

    print("Mesh quality gate passed.")


# ==========================================================
# ##### [5] Launch Fluent Meshing and Run Workflow #####
# ==========================================================

if __name__ == "__main__":
    meshing = None
    workflow = None
    transcript_is_running = False
    original_working_directory = os.getcwd()

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
        # ##### [8] Setup Periodic Boundaries #####
        # ======================================================

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

        apply_mesh_quality_gate(
            log_path=mesh_log_path,
            min_orthogonal_quality_limit=min_orthogonal_quality_threshold,
            max_aspect_ratio_limit=max_aspect_ratio_threshold,
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

    finally:
        if meshing is not None and transcript_is_running:
            try:
                # Flush and close the active transcript file.
                meshing.transcript.stop()
                transcript_is_running = False
            except Exception:
                pass

        if meshing is not None:
            try:
                meshing.exit()
            except Exception:
                pass

        try:
            os.chdir(original_working_directory)
        except Exception:
            pass