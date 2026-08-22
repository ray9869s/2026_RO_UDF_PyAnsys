"""Pure path tests for meshing and solver workers."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    load_solver_code,
    populate_valid_common_config,
)


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
            "meshing_code_paths_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_meshing_worker_paths_use_canonical_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    meshing = load_meshing_code()
    cfg = SimpleNamespace(
        family="diamond",
        geo_id="D2450_a45",
        mesh_id="max085_min006_cpg5_bl4_peel2",
        geo_name="D2450_a45_7c_brg110",
        case_name="mesh_max085_min006_cpg5_bl4",
        project_root=str(tmp_path / "legacy-project-root"),
    )

    resolved = meshing.resolve_meshing_paths(cfg)
    cfg.project_root = str(tmp_path / "injected-project-root")

    mesh_directory = (
        tmp_path
        / "meshes"
        / "diamond"
        / "D2450_a45"
        / "max085_min006_cpg5_bl4_peel2"
    )
    assert resolved["geometry_file"] == (
        tmp_path / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.dsco"
    )
    assert resolved["mesh_directory"] == mesh_directory
    assert resolved["mesh_file"] == (
        mesh_directory
        / "D2450_a45_7c_brg110_mesh_max085_min006_cpg5_bl4.msh.h5"
    )
    assert meshing.resolve_meshing_paths(cfg) == resolved


def test_run_override_project_root_cannot_relocate_worker_paths(
    monkeypatch,
    tmp_path,
):
    data_root = tmp_path / "data"
    repository = tmp_path / "repository"
    monkeypatch.setenv("RO_DATA_ROOT", str(data_root))
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", str(repository))

    solver = load_solver_code()
    cfg = load_run_config()
    populate_valid_common_config(cfg)
    cfg.mesh_case_name = "mesh_max100_min006_cpg3_bl3"

    before = solver.resolve_solver_paths(cfg)
    cfg.apply_run_config_overrides(
        cfg,
        {"project_root": str(tmp_path / "injected-project-root")},
    )
    after = solver.resolve_solver_paths(cfg)

    assert after == before
    assert after["run_directory"] == (
        data_root
        / "runs"
        / cfg.family
        / cfg.geo_id
        / cfg.mesh_id
        / cfg.run_id
    )
    assert after["mesh_directory"] == (
        data_root / "meshes" / cfg.family / cfg.geo_id / cfg.mesh_id
    )
    assert after["template_case"] == (
        repository
        / "templates"
        / cfg.template_case_file_name
    )
    assert after["udf_master"] == (
        repository
        / "udfs"
        / cfg.udf_source_file_name
    )
