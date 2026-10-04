"""Separate spacer boundary-layer counts stay off the default mesh path."""

from __future__ import annotations

import sys
import types

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)
from ro.mesh_common import (
    MESH_LEDGER_FIELDNAMES,
    MESH_PARAMETER_NAMES,
    parse_meshing_input_summary,
)
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload
from ro.manifest import ManifestError, write_mesh_manifest


def load_meshing_code():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "meshing_code_boundary_layers_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


LABELS = [
    "wall_top_mem",
    "wall_bottom_mem",
    "wall_top_buffer",
    "wall_bottom_buffer",
    "wall_spacer",
]
MEMBRANE_AND_BUFFER = LABELS[:4]
SPACER = ["wall_spacer"]

SINGLE_ARGUMENTS = {
    "BLControlName": "smooth_transition_1",
    "BlLabelList": LABELS,
    "FaceScope": {"GrowOn": "selected-labels"},
    "FirstHeight": 0.002,
    "NumberOfLayers": 4,
    "OffsetMethodType": "smooth-transition",
    "Rate": 1.2,
}


class _Arguments:
    def __init__(self, state=None):
        self.state = {} if state is None else dict(state)

    def set_state(self, state):
        self.state = dict(state)

    def get_state(self):
        return dict(self.state)


class _TaskList:
    def __init__(self):
        self.state = []
        self.reads = 0

    def get_state(self):
        self.reads += 1
        return list(self.state)

    def append(self, name):
        self.state.append(name)


class _Task:
    def __init__(self, name, workflow):
        self.name = name
        self.workflow = workflow
        self.Arguments = _Arguments()
        self.TaskList = _TaskList()
        self.updates = []

    def AddChildAndUpdate(self, DeferUpdate=False):
        self.updates.append(
            {"DeferUpdate": DeferUpdate, "state": dict(self.Arguments.state)}
        )
        child_name = self.Arguments.state["BLControlName"]
        child = self.workflow.tasks.get(child_name)
        if child is None:
            child = _Task(child_name, self.workflow)
            self.workflow.tasks[child_name] = child
            self.TaskList.append(child_name)
        child.Arguments.set_state(self.Arguments.state)
        if self.workflow.corrupt_membrane_on_spacer and child_name == (
            "smooth_transition_spacer"
        ):
            membrane = self.workflow.tasks["smooth_transition_mem"]
            membrane.Arguments.state["NumberOfLayers"] = 1


class _Workflow:
    def __init__(self, *, corrupt_membrane_on_spacer=False):
        self.tasks = {}
        self.corrupt_membrane_on_spacer = corrupt_membrane_on_spacer
        self.tasks["Add Boundary Layers"] = _Task(
            "Add Boundary Layers", self
        )

    @property
    def TaskObject(self):
        return self.tasks


def _metrics():
    return {
        "min_orthogonal_quality": 0.12,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.78,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 123456,
    }


def test_single_control_payload_matches_the_previous_task_arguments():
    meshing = load_meshing_code()
    payload = meshing.single_boundary_layer_arguments(
        LABELS,
        0.002,
        4,
        "smooth-transition",
        1.2,
    )
    assert payload == SINGLE_ARGUMENTS
    assert "AddChild" not in payload


def test_default_path_calls_one_control_without_reading_task_list():
    meshing = load_meshing_code()
    workflow = _Workflow()
    meshing.configure_boundary_layers(
        workflow,
        boundary_layer_labels=LABELS,
        membrane_and_buffer_labels=MEMBRANE_AND_BUFFER,
        wall_spacer_labels=SPACER,
        bl_height=0.002,
        bl_layers=4,
        spacer_bl_layers=None,
        bl_offset_method="smooth-transition",
        bl_growth_rate=1.2,
        include_spacer_in_boundary_layers=True,
    )
    task = workflow.TaskObject["Add Boundary Layers"]
    assert task.updates == [
        {"DeferUpdate": False, "state": SINGLE_ARGUMENTS}
    ]
    assert task.TaskList.reads == 0

    workflow_equal = _Workflow()
    meshing.configure_boundary_layers(
        workflow_equal,
        boundary_layer_labels=LABELS,
        membrane_and_buffer_labels=MEMBRANE_AND_BUFFER,
        wall_spacer_labels=SPACER,
        bl_height=0.002,
        bl_layers=4,
        spacer_bl_layers=4,
        bl_offset_method="smooth-transition",
        bl_growth_rate=1.2,
        include_spacer_in_boundary_layers=True,
    )
    assert workflow_equal.TaskObject["Add Boundary Layers"].updates == [
        {"DeferUpdate": False, "state": SINGLE_ARGUMENTS}
    ]


def test_split_path_payloads_and_membrane_layer_count(capsys):
    meshing = load_meshing_code()
    membrane, spacer = meshing.split_boundary_layer_arguments(
        MEMBRANE_AND_BUFFER,
        SPACER,
        0.002,
        8,
        4,
        "smooth-transition",
        1.2,
    )
    assert membrane["AddChild"] == "yes"
    assert spacer["AddChild"] == "yes"
    assert membrane["BLControlName"] == "smooth_transition_mem"
    assert spacer["BLControlName"] == "smooth_transition_spacer"
    assert membrane["BlLabelList"] == MEMBRANE_AND_BUFFER
    assert spacer["BlLabelList"] == SPACER
    assert membrane["NumberOfLayers"] == 8
    assert spacer["NumberOfLayers"] == 4
    assert membrane["FirstHeight"] == spacer["FirstHeight"] == 0.002
    assert membrane["OffsetMethodType"] == spacer["OffsetMethodType"]
    assert membrane["Rate"] == spacer["Rate"] == 1.2

    workflow = _Workflow()
    meshing.configure_boundary_layers(
        workflow,
        boundary_layer_labels=LABELS,
        membrane_and_buffer_labels=MEMBRANE_AND_BUFFER,
        wall_spacer_labels=SPACER,
        bl_height=0.002,
        bl_layers=8,
        spacer_bl_layers=4,
        bl_offset_method="smooth-transition",
        bl_growth_rate=1.2,
        include_spacer_in_boundary_layers=True,
    )
    task = workflow.TaskObject["Add Boundary Layers"]
    assert [item["state"] for item in task.updates] == [membrane, spacer]
    assert all(item["DeferUpdate"] is False for item in task.updates)
    assert (
        workflow.TaskObject["smooth_transition_mem"].Arguments.get_state()[
            "NumberOfLayers"
        ]
        == 8
    )
    printed = capsys.readouterr().out
    assert "Arguments.get_state() after smooth_transition_mem" in printed
    assert "TaskList after smooth_transition_mem" in printed
    assert "Arguments.get_state() after smooth_transition_spacer" in printed
    assert "TaskList after smooth_transition_spacer" in printed

    with pytest.raises(RuntimeError, match="NumberOfLayers"):
        meshing.configure_boundary_layers(
            _Workflow(corrupt_membrane_on_spacer=True),
            boundary_layer_labels=LABELS,
            membrane_and_buffer_labels=MEMBRANE_AND_BUFFER,
            wall_spacer_labels=SPACER,
            bl_height=0.002,
            bl_layers=8,
            spacer_bl_layers=4,
            bl_offset_method="smooth-transition",
            bl_growth_rate=1.2,
            include_spacer_in_boundary_layers=True,
        )


def test_summary_prints_spacer_count_only_on_the_split_path():
    meshing = load_meshing_code()
    assert meshing.boundary_layer_count_summary_lines(4, None) == [
        "Boundary layer number of layers [-]: 4"
    ]
    assert meshing.boundary_layer_count_summary_lines(4, 4) == [
        "Boundary layer number of layers [-]: 4"
    ]
    assert meshing.boundary_layer_count_summary_lines(8, 4) == [
        "Boundary layer number of layers [-]: 8",
        "Spacer boundary layer number of layers [-]: 4",
    ]
    parsed = parse_meshing_input_summary(
        "Boundary layer number of layers [-]: 8\n"
        "Spacer boundary layer number of layers [-]: 4\n"
    )
    assert parsed["bl_layers"] == 8
    assert parsed["spacer_bl_layers"] == 4
    absent = parse_meshing_input_summary(
        "Boundary layer number of layers [-]: 4\n"
    )
    assert absent["spacer_bl_layers"] is None


def test_ledger_header_does_not_gain_a_spacer_column():
    assert "spacer_bl_layers" not in MESH_PARAMETER_NAMES
    assert "spacer_bl_layers" not in MESH_LEDGER_FIELDNAMES
    assert "spacer_bl" not in MESH_LEDGER_FIELDNAMES


def test_manifest_records_spacer_bl_only_for_a_split(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.m_min = 0.006
    cfg.mesh_id = MESH_ID
    default_payload = build_mesh_manifest_payload(
        cfg, _metrics(), "a" * 64, created_utc="2026-10-04T00:00:00Z"
    )
    assert "spacer_bl" not in default_payload
    assert default_payload["bl"] == 4
    assert default_payload["mesh_id"] == MESH_ID

    cfg.spacer_bl_layers = 4
    equal_payload = build_mesh_manifest_payload(
        cfg, _metrics(), "a" * 64, created_utc="2026-10-04T00:00:00Z"
    )
    assert "spacer_bl" not in equal_payload

    cfg.bl_layers = 8
    cfg.spacer_bl_layers = 4
    cfg.mesh_id = "max085_min006_cpg5_bl8s4_peel2"
    split_payload = build_mesh_manifest_payload(
        cfg, _metrics(), "a" * 64, created_utc="2026-10-04T00:00:00Z"
    )
    assert split_payload["bl"] == 8
    assert split_payload["spacer_bl"] == 4

    directory = mesh_dir(FAMILY, GEO_ID, cfg.mesh_id)
    directory.mkdir(parents=True)
    stored = mesh_payload()
    stored["mesh_id"] = cfg.mesh_id
    stored["bl"] = 8
    stored["spacer_bl"] = 4
    write_mesh_manifest(directory, stored)

    missing = dict(stored)
    del missing["spacer_bl"]
    with pytest.raises(ManifestError, match="spacer_bl is missing"):
        write_mesh_manifest(directory, missing)

    on_default_id = mesh_payload()
    on_default_id["spacer_bl"] = 2
    default_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    default_directory.mkdir(parents=True)
    with pytest.raises(ManifestError, match="mesh_id tokens"):
        write_mesh_manifest(default_directory, on_default_id)
