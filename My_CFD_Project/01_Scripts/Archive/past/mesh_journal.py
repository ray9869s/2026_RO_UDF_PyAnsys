import ansys.fluent.core as pyfluent
meshing = pyfluent.launch_fluent(product_version="25.1.0", mode="meshing", dimension=3, precision="double", processor_count=20, ui_mode="gui", graphics_driver="dx11")
workflow = meshing.workflow

meshing.transcript.start(file_name=r'C:/PyFluent/My_CFD_Project/mesh_log_260323.txt')

workflow.InitializeWorkflow(WorkflowType=r'Watertight Geometry')
workflow.TaskObject['Import Geometry'].Arguments.set_state({r'FileName': r'C:/PyFluent/My_CFD_Project/00_Geometries/UW90.dsco',r'ImportCadPreferences': {r'MaxFacetLength': 0,},r'LengthUnit': r'mm',})
workflow.TaskObject['Import Geometry'].Execute()
workflow.TaskObject['Add Local Sizing'].Arguments.set_state({r'AddChild': r'yes',r'BOICellsPerGap': 3,r'BOIControlName': r'proximity_1',r'BOICurvatureNormalAngle': 18,r'BOIExecution': r'Proximity',r'BOIFaceLabelList': [r'periodic_l', r'periodic_r', r'wall_spacer'],r'BOIGrowthRate': 1.2,r'BOIMaxSize': 0.15,r'BOIMinSize': 0.01,r'BOIZoneorLabel': r'label',})
workflow.TaskObject['Add Local Sizing'].AddChildAndUpdate(DeferUpdate=False)
workflow.TaskObject['Add Local Sizing'].InsertNextTask(CommandName=r'SetUpPeriodicBoundaries')
workflow.TaskObject['Set Up Periodic Boundaries'].Arguments.set_state({r'AutoMultiplePeriodic': r'no',r'LCSOrigin': {r'OriginX': 0,r'OriginY': 0,r'OriginZ': 0,},r'LCSVector': {r'VectorX': 0,r'VectorY': 0,r'VectorZ': 0,},r'LabelList': [r'periodic_r'],r'ListAllLabelToggle': False,r'MeshObject': r'',r'Method': r'Manual - pick reference side',r'MultipleOption': r'Paired',r'PeriodicityAngle': 45,r'RemeshBoundariesOption': r'no',r'SelectionType': r'label',r'TransShift': {r'ShiftX': 0,r'ShiftY': 3.465,r'ShiftZ': 0,},r'Type': r'Translational',})
workflow.TaskObject['Set Up Periodic Boundaries'].Execute()
workflow.TaskObject['Generate the Surface Mesh'].Arguments.set_state({r'CFDSurfaceMeshControls': {r'CellsPerGap': 3,r'MaxSize': 0.15,r'MinSize': 0.01,r'ScopeProximityTo': r'faces',},})
workflow.TaskObject['Generate the Surface Mesh'].Execute()
workflow.TaskObject['Describe Geometry'].UpdateChildTasks(Arguments={r'v1': True,}, SetupTypeChanged=False)
workflow.TaskObject['Describe Geometry'].Arguments.set_state({r'NonConformal': r'No',r'SetupType': r'The geometry consists of only fluid regions with no voids',})
workflow.TaskObject['Describe Geometry'].UpdateChildTasks(Arguments={r'v1': True,}, SetupTypeChanged=True)
workflow.TaskObject['Describe Geometry'].Execute()
workflow.TaskObject['Update Boundaries'].Execute()
workflow.TaskObject['Update Regions'].Execute()
workflow.TaskObject['Add Boundary Layers'].Arguments.set_state({r'BLControlName': r'uniform_1',r'BlLabelList': [r'wall'],r'FaceScope': {r'GrowOn': r'selected-labels',},r'FirstHeight': 0.004,r'LocalPrismPreferences': {r'Continuous': r'Continuous',},r'NumberOfLayers': 4,r'OffsetMethodType': r'uniform',})
workflow.TaskObject['Add Boundary Layers'].AddChildAndUpdate(DeferUpdate=False)
workflow.TaskObject['Generate the Volume Mesh'].Arguments.set_state({r'VolumeFill': r'poly-hexcore',r'VolumeFillControls': {r'HexMaxCellLength': 0.08,r'PeelLayers': 2,},})
workflow.TaskObject['Generate the Volume Mesh'].Execute()

meshing.transcript.stop()