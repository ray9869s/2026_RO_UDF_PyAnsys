# ==========================================================
# _probe_add_boundary_layers.py
#
# Read-only probe: launch Fluent Meshing, initialize the watertight
# workflow, and dump Add Boundary Layers Arguments.get_state() plus
# LocalPrismPreferences keys / allowed values.
#
# Does NOT import geometry, generate a surface mesh, or execute
# Add Boundary Layers / volume mesh. The schema of LocalPrismPreferences
# is what we need; it should be present after InitializeWorkflow.
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

from ro.paths import project_root

SCRIPT_DIR = Path(__file__).resolve().parent
TASK_NAME = "Add Boundary Layers"
PREFERENCE_KEYS = (
    "ShowLocalPrismPreferences",
    "IgnoreBoundaryLayers",
    "AdditionalIgnoredLayers",
    "Continuous",
    "ModifyAtInvalidNormals",
    "InvalidNormalMethod",
    "RemeshAtInvalidNormals",
    "SplitPrism",
)


def as_fluent_path(path):
    """Convert an absolute file path to a Fluent-friendly path string."""
    return os.path.abspath(path).replace("\\", "/")


def load_run_config():
    """Load configs/run_config.py for launch_fluent kwargs only."""
    config_path = project_root() / "configs" / "run_config.py"
    if not config_path.is_file():
        raise FileNotFoundError(f"Run config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location(
        "probe_add_boundary_layers_run_config", config_path
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
    try:
        return getattr(arguments, key)
    except Exception:
        pass
    try:
        return arguments[key]
    except Exception:
        pass
    return None


def probe_preference_key(parent, key):
    """Dump state and allowed values for one LocalPrismPreferences field."""
    child = _argument_child(parent, key)
    allowed = None
    if child is not None:
        allowed = _read_attr(
            child,
            ("allowedValues", "allowed_values", "allowed-values"),
        )
    return {
        "child_resolved": child is not None,
        "allowedValues": _jsonable(allowed),
    }


def main():
    cfg = load_run_config()
    product_version = cfg.product_version
    processor_count = cfg.processor_count
    graphics_driver = cfg.graphics_driver

    print(
        "launch_fluent args: "
        f"product_version={product_version!r}, mode='meshing', dimension=3, "
        f"precision='double', processor_count={processor_count!r}, "
        f"ui_mode='gui', graphics_driver={graphics_driver!r}"
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
        workflow.InitializeWorkflow(WorkflowType=r"Watertight Geometry")
        print("InitializeWorkflow(Watertight Geometry) executed.")

        if not workflow_task_exists(workflow, TASK_NAME):
            raise RuntimeError(f"Workflow task not found: {TASK_NAME}")

        arguments = workflow.TaskObject[TASK_NAME].Arguments
        state = arguments.get_state()

        print(f"\n=== {TASK_NAME} Arguments.get_state() ===")
        print(json.dumps(_jsonable(state), indent=2))

        prefs = None
        if isinstance(state, dict):
            prefs = state.get("LocalPrismPreferences")
        print("\n=== LocalPrismPreferences from get_state() ===")
        print(json.dumps(_jsonable(prefs), indent=2))

        prefs_child = _argument_child(arguments, "LocalPrismPreferences")
        print(
            "\n=== LocalPrismPreferences child resolved: "
            f"{prefs_child is not None} ==="
        )
        key_probe = {}
        parent = prefs_child if prefs_child is not None else arguments
        for key in PREFERENCE_KEYS:
            key_probe[key] = probe_preference_key(parent, key)
        print(json.dumps(key_probe, indent=2))

        print(
            "\nProbe finished WITHOUT importing geometry, generating a "
            "surface mesh, or executing Add Boundary Layers."
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
