"""Pure worker tests for mesh and run manifest production."""

from __future__ import annotations

import hashlib
import sys
import types

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    load_solver_code,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)
from ro.manifest import read_mesh_manifest, read_run_manifest


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
            "meshing_code_manifests_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _mesh_metrics():
    return {
        "min_orthogonal_quality": 0.12,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.78,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 123456,
    }


def test_workers_write_linked_manifests(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    created_utc = "2026-08-21T08:00:00Z"

    meshing = load_meshing_code()
    mesh_cfg = load_run_config()
    populate_valid_meshing_config(mesh_cfg)
    mesh_paths = meshing.resolve_meshing_paths(mesh_cfg)
    mesh_paths["mesh_directory"].mkdir(parents=True)
    mesh_bytes = b"test mesh bytes"
    mesh_paths["mesh_file"].write_bytes(mesh_bytes)

    mesh_manifest_path = meshing.write_worker_mesh_manifest(
        mesh_cfg,
        mesh_paths["mesh_directory"],
        mesh_paths["mesh_file"],
        _mesh_metrics(),
        created_utc=created_utc,
    )
    mesh_manifest = read_mesh_manifest(mesh_paths["mesh_directory"])

    assert mesh_manifest_path == mesh_paths["mesh_directory"] / "manifest.json"
    assert mesh_manifest["mesh_sha256"] == hashlib.sha256(mesh_bytes).hexdigest()
    assert mesh_manifest["inlet_profile_G"] is None
    assert mesh_manifest["bridge_radius_m"] == mesh_cfg.bridge_radius_m
    assert mesh_manifest["n_lead_excluded"] == mesh_cfg.n_lead_excluded

    solver = load_solver_code()
    run_cfg = load_run_config()
    populate_valid_solver_config(run_cfg)
    run_cfg.family = mesh_cfg.family
    run_cfg.geo_id = mesh_cfg.geo_id
    run_cfg.mesh_id = mesh_cfg.mesh_id
    run_cfg.run_id = "u0p2_p6M"
    run_cfg.mesh_case_name = mesh_cfg.case_name
    run_cfg.inlet_velocity_value = 0.2
    run_paths = solver.resolve_solver_paths(run_cfg)
    run_paths["run_directory"].mkdir(parents=True)

    run_manifest_path = solver.write_worker_run_manifest(
        run_cfg,
        run_paths["mesh_directory"],
        run_paths["run_directory"],
        created_utc=created_utc,
    )
    run_manifest = read_run_manifest(run_paths["run_directory"])

    assert run_manifest_path == run_paths["run_directory"] / "manifest.json"
    assert run_manifest["mesh_sha256"] == mesh_manifest["mesh_sha256"]
    assert run_manifest["inlet_bc_type"] == "plug"
    assert run_manifest["u_target_ms"] == 0.2
    assert run_manifest["u_mean_ms"] == 0.2
    assert run_manifest["stop_reason"] == "RUNNING"

    solver.finalize_worker_run_manifest(
        run_paths["run_directory"],
        "qoi_converged",
    )
    assert (
        read_run_manifest(run_paths["run_directory"])["stop_reason"]
        == "qoi_converged"
    )


def test_parabolic_run_manifest_leaves_mean_null():
    solver = load_solver_code()
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.use_inlet_velocity_profile = True
    mesh_manifest = {"mesh_sha256": "a" * 64}

    payload = solver.build_run_manifest_payload(
        cfg,
        mesh_manifest,
        created_utc="2026-08-21T08:00:00Z",
    )

    assert payload["inlet_bc_type"] == "parabolic"
    assert payload["u_target_ms"] == cfg.inlet_velocity_value
    assert payload["u_mean_ms"] is None


def test_mesh_manifest_fields_have_no_worker_defaults():
    meshing = load_meshing_code()
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    del cfg.bridge_radius_m

    with pytest.raises(AttributeError, match="bridge_radius_m"):
        meshing.build_mesh_manifest_payload(
            cfg,
            _mesh_metrics(),
            "a" * 64,
            created_utc="2026-08-21T08:00:00Z",
        )


def test_run_manifest_write_precedes_case_loading_and_udf_handling():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    runtime = source[source.index("    try:\n        os.chdir(case_path)") :]
    mesh_branch, restart_and_after = runtime.split(
        "        else:\n"
        "            # Direct solver-mode launch failed on the Windows server",
        maxsplit=1,
    )
    restart_branch, after_input_load = restart_and_after.split(
        "        # Update solver-side thread names",
        maxsplit=1,
    )
    manifest_call = "run_manifest_path = write_worker_run_manifest("

    assert mesh_branch.count(manifest_call) == 1
    assert mesh_branch.index(manifest_call) < mesh_branch.index(
        "setup = solver.settings.setup"
    )
    assert mesh_branch.index(manifest_call) < mesh_branch.index(
        "solver.settings.file.read_case("
    )
    assert mesh_branch.index(manifest_call) < mesh_branch.index(
        "solver.settings.file.replace_mesh("
    )

    assert restart_branch.count(manifest_call) == 1
    assert restart_branch.index(manifest_call) < restart_branch.index(
        "setup = solver.settings.setup"
    )
    assert restart_branch.index(manifest_call) < restart_branch.index(
        "solver.settings.file.read_case("
    )
    assert restart_branch.index(manifest_call) < restart_branch.index(
        "solver.settings.file.read_data("
    )

    assert manifest_call not in after_input_load
    assert "udf_case_path = copy_and_patch_udf_to_case_folder(" in after_input_load
    assert 'solver.tui.define.user_defined.compiled_functions(\n            "compile"' in (
        after_input_load
    )
