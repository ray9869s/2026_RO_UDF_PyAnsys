# ==========================================================
# _probe_improve_task.py
#
# Read-only probe: launch Fluent Meshing, import one geometry,
# insert periodic + Improve Surface Mesh tasks, dump Improve
# Surface Mesh Arguments.get_state() (and best-effort allowed
# values / defaults), then exit WITHOUT generating a surface
# or volume mesh.
#
# Intended to run on the Windows Fluent server only.
# Do not run from WSL (no Ansys install).
# ==========================================================

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import ansys.fluent.core as pyfluent

from ro.paths import geometry_dir

SCRIPT_DIR = Path(__file__).resolve().parent
FAMILY = "diamond"
GEO_ID = "D2450_a45"

# Lines marked UNCERTAIN are not copied verbatim from meshing_code_260616.py
# or are inferred from other repo helpers (07_batch_solver_rerun get_attr).


def as_fluent_path(path):
    """Convert an absolute file path to a Fluent-friendly path string."""
    # Matches meshing_code_260616.as_fluent_path.
    return os.path.abspath(path).replace("\\", "/")


def load_run_config():
    """Load run_config.py the same way the meshing worker does (no overrides)."""
    config_path = SCRIPT_DIR / "run_config.py"
    # UNCERTAIN: worker also honors PYFLUENT_RUN_CONFIG; probe always uses
    # the sibling run_config.py so launch args stay local and obvious.
    if not config_path.is_file():
        raise FileNotFoundError(f"Run config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location("probe_run_config", config_path)
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


def ensure_periodic_boundary_task(workflow_object):
    """Insert the periodic boundary task only when it is not already present."""
    # Copied from meshing_code_260616.ensure_periodic_boundary_task.
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
    # UNCERTAIN: meshing-workflow Arguments children may not expose get_attr
    # the same way solver settings objects do.
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


def introspect_arguments(arguments, state, prefix=""):
    """Walk get_state keys and collect allowedValues / default when available."""
    # UNCERTAIN: entire introspection block — best-effort only.
    rows = []
    if not isinstance(state, dict):
        return rows

    for key, value in state.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        child = _argument_child(arguments, key)
        row = {
            "path": path,
            "state_value": _jsonable(value),
            "child_resolved": child is not None,
            "allowedValues": None,
            "default": None,
        }
        if child is not None:
            row["allowedValues"] = _jsonable(
                _read_attr(
                    child,
                    ("allowedValues", "allowed_values", "allowed-values"),
                )
            )
            row["default"] = _jsonable(
                _read_attr(child, ("default", "Default"))
            )
            # UNCERTAIN: nested preference dicts (e.g. SMImprovePreferences)
            # may need recursion through the child object, not Arguments root.
            if isinstance(value, dict):
                rows.extend(introspect_arguments(child, value, prefix=path))
        rows.append(row)

    return rows


def main():
    geo_full_path = geometry_dir(FAMILY, GEO_ID) / f"{GEO_ID}.dsco"
    if not geo_full_path.is_file():
        raise FileNotFoundError(
            f"Geometry file not found: {geo_full_path}. "
            "This probe expects the canonical external geometry layout."
        )

    cfg = load_run_config()
    product_version = cfg.product_version
    processor_count = cfg.processor_count
    graphics_driver = cfg.graphics_driver
    periodic_reference_label = cfg.periodic_reference_label
    periodic_shift_x = cfg.periodic_shift_x
    periodic_shift_y = cfg.periodic_shift_y
    periodic_shift_z = cfg.periodic_shift_z

    print(f"Geometry: {geo_full_path}")
    print(
        "launch_fluent args: "
        f"product_version={product_version!r}, mode='meshing', dimension=3, "
        f"precision='double', processor_count={processor_count!r}, "
        f"ui_mode='gui', graphics_driver={graphics_driver!r}"
    )

    meshing = None
    try:
        # Same keyword arguments as meshing_code_260616.py launch_fluent call.
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

        # Import Geometry — same Arguments.set_state + Execute as the worker.
        workflow.TaskObject["Import Geometry"].Arguments.set_state({
            r"FileName": as_fluent_path(str(geo_full_path)),
            r"ImportCadPreferences": {
                r"MaxFacetLength": 0,
            },
            r"LengthUnit": r"mm",
        })
        workflow.TaskObject["Import Geometry"].Execute()
        print("Import Geometry executed.")

        # UNCERTAIN: worker runs Add Local Sizing AddChildAndUpdate before
        # inserting periodic. Skipped here; InsertNextTask still targets
        # TaskObject['Add Local Sizing'] which exists after InitializeWorkflow.

        ensure_periodic_boundary_task(workflow)

        # Same periodic Arguments.set_state + Execute as the worker main block.
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
        print("Set Up Periodic Boundaries executed.")

        # UNCERTAIN vs Ahmed-body example: that example Executes Generate the
        # Surface Mesh BEFORE InsertNextTask. This probe deliberately does
        # NOT execute surface mesh (per probe contract) and only inserts.
        improve_task_name = "Improve Surface Mesh"
        if workflow_task_exists(workflow, improve_task_name):
            print(f"Workflow task already exists: {improve_task_name}")
        else:
            workflow.TaskObject["Generate the Surface Mesh"].InsertNextTask(
                CommandName=r"ImproveSurfaceMesh"
            )
            if not workflow_task_exists(workflow, improve_task_name):
                raise RuntimeError(
                    f"Failed to insert workflow task: {improve_task_name}"
                )
            print(f"Inserted workflow task: {improve_task_name}")

        improve_args = workflow.TaskObject[improve_task_name].Arguments
        state = improve_args.get_state()

        print("\n=== Improve Surface Mesh Arguments.get_state() ===")
        print(json.dumps(_jsonable(state), indent=2))

        print("\n=== Per-key allowedValues / default (best-effort) ===")
        rows = introspect_arguments(improve_args, state)
        print(json.dumps(rows, indent=2))

        print(
            "\nProbe finished WITHOUT executing "
            "Generate the Surface Mesh or Generate the Volume Mesh."
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
