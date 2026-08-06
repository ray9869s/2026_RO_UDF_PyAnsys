# ==========================================================
# _probe_periodic_after_surface.py
#
# Read-only probe: launch Fluent Meshing, import geometry,
# generate a surface mesh WITHOUT prior periodic setup, then
# insert Set Up Periodic Boundaries after the surface mesh and
# dump Arguments.get_state() plus best-effort Method allowed
# values / LabelList cardinality.
#
# Does NOT execute Describe Geometry, boundary layers, or volume
# mesh. Does NOT execute the periodic task after the dump.
#
# Windows Fluent server only. Do not run from WSL.
# ==========================================================

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import ansys.fluent.core as pyfluent

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
GEO_NAME = "D2450_a45_test"
GEO_FULL_PATH = PROJECT_ROOT / "00_Geometries" / f"{GEO_NAME}.dsco"

# Surface-mesh sizes for a cheap probe mesh. Evidence case used m_max=0.085.
# UNCERTAIN: these are not loaded from a production mesh_batch case.
PROBE_M_MAX = 0.085
PROBE_M_MIN = 0.006
PROBE_M_CPG = 3.0

# Lines marked UNCERTAIN are not copied verbatim from meshing_code_260616.py
# or are inferred from UG / other repo helpers.


def as_fluent_path(path):
    """Convert an absolute file path to a Fluent-friendly path string."""
    return os.path.abspath(path).replace("\\", "/")


def load_run_config():
    """Load sibling run_config.py for launch_fluent kwargs only."""
    config_path = SCRIPT_DIR / "run_config.py"
    # UNCERTAIN: worker also honors PYFLUENT_RUN_CONFIG; probe always uses
    # the sibling run_config.py.
    if not config_path.is_file():
        raise FileNotFoundError(f"Run config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location(
        "probe_periodic_after_surface_run_config", config_path
    )
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


def workflow_task_exists(workflow_object, task_name):
    """Check whether a workflow task exists (same idea as the worker)."""
    try:
        workflow_object.TaskObject[task_name]
        return True
    except Exception as e:
        print(
            f"Warning: task existence check raised an exception for "
            f"'{task_name}'. Error: {e}"
        )
        return False


def _jsonable(value):
    """Best-effort JSON conversion for Fluent argument introspection."""
    # UNCERTAIN: get_state() / get_attr payloads may contain non-JSON types.
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return repr(value)


def _read_attr(obj, attr_names):
    """Try getattr then get_attr for each candidate name; return first hit."""
    # Pattern adapted from 07_batch_solver_rerun._read_allowed_values_from_object.
    # UNCERTAIN on meshing-workflow Arguments children (Improve probe saw
    # child_resolved=false for all keys).
    for attr_name in attr_names:
        try:
            attr = getattr(obj, attr_name)
            raw = attr() if callable(attr) else attr
            if raw is not None:
                return raw
        except Exception:
            pass

        try:
            raw = obj.get_attr(attr_name)
            if raw is not None:
                return raw
        except Exception:
            pass

    return None


def _argument_child(arguments, key):
    """Resolve one Arguments child by attribute or indexing."""
    # UNCERTAIN: which access form the watertight Arguments object supports.
    try:
        return getattr(arguments, key)
    except Exception:
        pass
    try:
        return arguments[key]
    except Exception:
        pass
    return None


def probe_method_and_labellist(arguments, state):
    """Collect Method allowed values and LabelList cardinality hints."""
    label_list = state.get("LabelList") if isinstance(state, dict) else None
    method_child = _argument_child(arguments, "Method")
    label_child = _argument_child(arguments, "LabelList")

    method_allowed = None
    if method_child is not None:
        method_allowed = _read_attr(
            method_child,
            ("allowedValues", "allowed_values", "allowed-values"),
        )

    # UNCERTAIN: some PyFluent builds expose allowed values only via get_attr
    # on the Arguments root with a dotted/relative path.
    if method_allowed is None:
        method_allowed = _read_attr(
            arguments,
            ("allowedValues", "allowed_values", "allowed-values"),
        )

    label_allowed = None
    if label_child is not None:
        label_allowed = _read_attr(
            label_child,
            ("allowedValues", "allowed_values", "allowed-values"),
        )

    return {
        "Method_state": _jsonable(state.get("Method") if isinstance(state, dict) else None),
        "Method_allowedValues": _jsonable(method_allowed),
        "Method_child_resolved": method_child is not None,
        "LabelList_state": _jsonable(label_list),
        "LabelList_len": len(label_list) if isinstance(label_list, (list, tuple)) else None,
        "LabelList_allowedValues": _jsonable(label_allowed),
        "LabelList_child_resolved": label_child is not None,
        "SelectionType_state": _jsonable(
            state.get("SelectionType") if isinstance(state, dict) else None
        ),
        "RemeshBoundariesOption_state": _jsonable(
            state.get("RemeshBoundariesOption") if isinstance(state, dict) else None
        ),
        "TransShift_state": _jsonable(
            state.get("TransShift") if isinstance(state, dict) else None
        ),
        # Doc expectation (not live-confirmed here): Automatic needs 2 labels;
        # Manual needs 1. Report both for the operator to compare against state.
        "doc_expected_LabelList_cardinality": {
            "Automatic - pick both sides": 2,
            "Manual - pick reference side": 1,
        },
    }


def main():
    if not GEO_FULL_PATH.is_file():
        raise FileNotFoundError(
            f"Geometry file not found: {GEO_FULL_PATH}. "
            "This probe expects the same 00_Geometries layout as the worker."
        )

    cfg = load_run_config()
    product_version = cfg.product_version
    processor_count = cfg.processor_count
    graphics_driver = cfg.graphics_driver

    print(f"Geometry: {GEO_FULL_PATH}")
    print(
        "launch_fluent args: "
        f"product_version={product_version!r}, mode='meshing', dimension=3, "
        f"precision='double', processor_count={processor_count!r}, "
        f"ui_mode='gui', graphics_driver={graphics_driver!r}"
    )
    print(
        "Probe surface mesh sizes: "
        f"m_max={PROBE_M_MAX}, m_min={PROBE_M_MIN}, m_cpg={PROBE_M_CPG}"
    )

    meshing = None
    try:
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

        # UNCERTAIN: worker also os.chdir(case_path) and starts a transcript;
        # omitted here because this probe does not write mesh outputs.

        workflow.InitializeWorkflow(WorkflowType=r"Watertight Geometry")

        workflow.TaskObject["Import Geometry"].Arguments.set_state({
            r"FileName": as_fluent_path(str(GEO_FULL_PATH)),
            r"ImportCadPreferences": {
                r"MaxFacetLength": 0,
            },
            r"LengthUnit": r"mm",
        })
        workflow.TaskObject["Import Geometry"].Execute()
        print("Import Geometry executed.")

        # Intentionally NO Set Up Periodic Boundaries before surface mesh.
        # UNCERTAIN: worker also runs Add Local Sizing before surface mesh;
        # skipped here so Method/LabelList defaults after insert are not
        # confounded by local sizing — surface mesh will differ from production.

        workflow.TaskObject["Generate the Surface Mesh"].Arguments.set_state({
            r"CFDSurfaceMeshControls": {
                r"CellsPerGap": PROBE_M_CPG,
                r"MaxSize": PROBE_M_MAX,
                r"MinSize": PROBE_M_MIN,
                r"ScopeProximityTo": r"faces",
            },
        })
        workflow.TaskObject["Generate the Surface Mesh"].Execute()
        print("Generate the Surface Mesh executed (no prior periodic setup).")

        periodic_task_name = "Set Up Periodic Boundaries"
        if workflow_task_exists(workflow, periodic_task_name):
            print(f"Workflow task already exists: {periodic_task_name}")
        else:
            # Insert immediately after the surface mesh task.
            workflow.TaskObject["Generate the Surface Mesh"].InsertNextTask(
                CommandName=r"SetUpPeriodicBoundaries"
            )
            if not workflow_task_exists(workflow, periodic_task_name):
                raise RuntimeError(
                    f"Failed to insert workflow task: {periodic_task_name}"
                )
            print(
                f"Inserted workflow task: {periodic_task_name} "
                "(after Generate the Surface Mesh)"
            )

        periodic_args = workflow.TaskObject[periodic_task_name].Arguments
        state = periodic_args.get_state()

        print("\n=== Set Up Periodic Boundaries Arguments.get_state() ===")
        print(json.dumps(_jsonable(state), indent=2))

        print("\n=== Method / LabelList probe ===")
        print(json.dumps(probe_method_and_labellist(periodic_args, state), indent=2))

        print(
            "\nProbe finished WITHOUT executing Set Up Periodic Boundaries, "
            "Describe Geometry, or Generate the Volume Mesh."
        )
        return 0
    finally:
        if meshing is not None:
            try:
                meshing.exit()
            except Exception as exit_error:
                print(f"Warning: meshing.exit() failed: {exit_error}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"PROBE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
