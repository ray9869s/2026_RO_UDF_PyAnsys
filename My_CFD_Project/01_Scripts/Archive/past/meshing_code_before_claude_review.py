import ansys.fluent.core as pyfluent
import os

# ==========================================================
# ##### [1] User Defined Parameters (change only here) #####
# ==========================================================
project_root = r"C:/PyFluent/My_CFD_Project"
geo_name = "Pillar"
case_name = "260427_Test"

# [Mesh size]
m_max = 0.15          # Maximum Size (mm)
m_min = 0.01          # Minimum Size (mm)
m_cpg = 3             # Cells Per Gap

# [Face labels]
wall_spacer_labels = ["wall_spacer_pillar", "wall_spacer_filament"]

if not wall_spacer_labels:
    raise ValueError("wall_spacer_labels must contain at least one face label.")

# [Periodic Condition]
periodic_shift_x = 0.0
periodic_shift_y = 3.465
periodic_shift_z = 0.0

# [Boundary Layer]
bl_height = m_min * 0.4     # First Layer Height (mm)
bl_layers = 4               # Number of Layers

# [Volume Mesh]
vol_hex_max = m_max * 0.7   # Hex Max Cell Length (usally less than m_max)
# ==========================================================

# [2] Path and File Name Setting
geo_full_path = os.path.join(project_root, "00_Geometries", f"{geo_name}.dsco")
case_path = os.path.join(project_root, "03_Results", geo_name, case_name) # changing here may be requuired

if not os.path.isfile(geo_full_path):
    raise FileNotFoundError(f"Geometry file not found: {geo_full_path}")

if not os.path.exists(case_path):
    os.makedirs(case_path)

os.chdir(case_path)

mesh_log_path = os.path.join(case_path, f"mesh_log_{case_name}.txt")
mesh_file_path = os.path.join(case_path, f"{geo_name}_{case_name}.msh.h5")


def get_available_labels(task, complete_label_key):
    """Read label candidates from the current workflow task when available."""
    try:
        state = task.Arguments.get_state()
    except Exception:
        return []

    labels = state.get(complete_label_key, [])
    return [label for label in labels if isinstance(label, str)]


def validate_requested_labels(task, complete_label_key, requested_labels, label_usage):
    """Fail early when a manually entered label does not exist in the geometry."""
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

# [3] Launch Fluent Meshing
meshing = pyfluent.launch_fluent(
    product_version="25.1.0", 
    mode="meshing", 
    dimension=3, 
    precision="double", 
    processor_count=20, 
    ui_mode="gui", 
    graphics_driver="dx11"
)
workflow = meshing.workflow

# Start Meshing Transcript
meshing.transcript.start(file_name=mesh_log_path)
print(f"Meshing log will be saved to: {mesh_log_path}")
print(f"Mesh file will be saved to: {mesh_file_path}")

try:
    # [4] Start Workflow and Load Geometry
    workflow.InitializeWorkflow(WorkflowType=r'Watertight Geometry')
    workflow.TaskObject['Import Geometry'].Arguments.set_state({
        r'FileName': geo_full_path, 
        r'ImportCadPreferences': {r'MaxFacetLength': 0,},
        r'LengthUnit': r'mm',
    })
    workflow.TaskObject['Import Geometry'].Execute()
    print("Wall spacer face labels:", wall_spacer_labels)

    validate_requested_labels(
        workflow.TaskObject['Add Local Sizing'],
        r'CompleteFaceLabelList',
        [r'periodic_l', r'periodic_r', *wall_spacer_labels],
        "local sizing face labels",
    )

    # [5] Local Sizing
    workflow.TaskObject['Add Local Sizing'].Arguments.set_state({
        r'AddChild': r'yes',
        r'BOICellsPerGap': m_cpg,
        r'BOIControlName': r'proximity_1',
        r'BOICurvatureNormalAngle': 18,
        r'BOIExecution': r'Proximity',
        r'BOIFaceLabelList': [r'periodic_l', r'periodic_r', *wall_spacer_labels],
        r'BOIGrowthRate': 1.2,
        r'BOIMaxSize': m_max,
        r'BOIMinSize': m_min,
        r'BOIZoneorLabel': r'label',
    })
    workflow.TaskObject['Add Local Sizing'].AddChildAndUpdate(DeferUpdate=False)

    # [6] Setup Periodic Condition
    workflow.TaskObject['Add Local Sizing'].InsertNextTask(CommandName=r'SetUpPeriodicBoundaries')
    workflow.TaskObject['Set Up Periodic Boundaries'].Arguments.set_state({
        r'LabelList': [r'periodic_r'],
        r'Method': r'Manual - pick reference side',
        r'TransShift': {
            r'ShiftX': periodic_shift_x,
            r'ShiftY': periodic_shift_y,
            r'ShiftZ': periodic_shift_z,
        },
        r'Type': r'Translational',
    })
    workflow.TaskObject['Set Up Periodic Boundaries'].Execute()

    # [7] Generate Surface Mesh
    workflow.TaskObject['Generate the Surface Mesh'].Arguments.set_state({
        r'CFDSurfaceMeshControls': {
            r'CellsPerGap': m_cpg,
            r'MaxSize': m_max,
            r'MinSize': m_min,
            r'ScopeProximityTo': r'faces',
        },
    })
    workflow.TaskObject['Generate the Surface Mesh'].Execute()

    # [8] Describe Geometry, Boundaries, and Regions
    # Describe Geometry itself is global. The wall_spacer* labels are already
    # preserved automatically once imported, so no prefix-specific setting is
    # required here.
    workflow.TaskObject['Describe Geometry'].UpdateChildTasks(Arguments={r'v1': True,}, SetupTypeChanged=False)
    workflow.TaskObject['Describe Geometry'].Arguments.set_state({
        r'NonConformal': r'No',
        r'SetupType': r'The geometry consists of only fluid regions with no voids',
    })
    workflow.TaskObject['Describe Geometry'].UpdateChildTasks(Arguments={r'v1': True,}, SetupTypeChanged=True)
    workflow.TaskObject['Describe Geometry'].Execute()
    workflow.TaskObject['Update Boundaries'].Execute()
    workflow.TaskObject['Update Regions'].Execute()

    # [9] Add Boundary Layers
    workflow.TaskObject['Add Boundary Layers'].Arguments.set_state({
        r'BLControlName': r'uniform_1',
        r'BlLabelList': [r'wall'],
        r'FaceScope': {r'GrowOn': r'selected-labels',},
        r'FirstHeight': bl_height,
        r'NumberOfLayers': bl_layers,
        r'OffsetMethodType': r'uniform',
    })
    workflow.TaskObject['Add Boundary Layers'].AddChildAndUpdate(DeferUpdate=False)

    # [10] Generate Volume Mesh
    workflow.TaskObject['Generate the Volume Mesh'].Arguments.set_state({
        r'VolumeFill': r'poly-hexcore',
        r'VolumeFillControls': {
            r'HexMaxCellLength': vol_hex_max,
            r'PeelLayers': 2,
        },
    })
    workflow.TaskObject['Generate the Volume Mesh'].Execute()

    # [11] Quality check and Save the Mesh File
    meshing.execute_tui(r'/mesh/check-quality')
    meshing.tui.file.write_mesh(f'"{mesh_file_path}"')
finally:
    try:
        meshing.transcript.stop()
    except Exception:
        pass

    try:
        meshing.exit()
    except Exception:
        pass