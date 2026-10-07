"""Solve and extraction wall time are new run-manifest fields."""

from __future__ import annotations

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_solver_code,
    load_run_config,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)
from test_post_aggregate_paths import load_report_extract
from test_worker_manifests import load_meshing_code, _matching_extent_metrics
from ro.manifest import read_run_manifest


def _written_run(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    created_utc = "2026-08-21T08:00:00Z"
    meshing = load_meshing_code()
    mesh_cfg = load_run_config()
    populate_valid_meshing_config(mesh_cfg)
    mesh_paths = meshing.resolve_meshing_paths(mesh_cfg)
    mesh_paths["mesh_directory"].mkdir(parents=True)
    mesh_paths["mesh_file"].write_bytes(b"test mesh bytes")
    meshing.write_worker_mesh_manifest(
        mesh_cfg,
        mesh_paths["mesh_directory"],
        mesh_paths["mesh_file"],
        _matching_extent_metrics(mesh_cfg),
        created_utc=created_utc,
    )
    solver = load_solver_code()
    run_cfg = load_run_config()
    populate_valid_solver_config(run_cfg)
    run_cfg.family = mesh_cfg.family
    run_cfg.geo_id = mesh_cfg.geo_id
    run_cfg.mesh_id = mesh_cfg.mesh_id
    run_cfg.run_id = "u0p2_p6M"
    run_cfg.mesh_case_name = mesh_cfg.case_name
    run_cfg.inlet_velocity_value = 0.2
    run_cfg.outlet_gauge_pressure = 6.0e6
    run_paths = solver.resolve_solver_paths(run_cfg)
    run_paths["run_directory"].mkdir(parents=True)
    solver.write_worker_run_manifest(
        run_cfg,
        run_paths["mesh_directory"],
        run_paths["run_directory"],
        created_utc=created_utc,
    )
    solver.finalize_worker_run_manifest(run_paths["run_directory"], "qoi_converged")
    return solver, run_paths["run_directory"]


def test_solver_stamp_adds_wall_time_and_processor_count(monkeypatch, tmp_path):
    solver, run_directory = _written_run(monkeypatch, tmp_path)
    before = read_run_manifest(run_directory)
    assert "solver_wall_time_s" not in before
    assert "processor_count" not in before
    assert "extraction_wall_time_s" not in before
    assert "solver_time_s" not in before

    solver.stamp_solver_timing_on_run_manifest(run_directory, 12.5, 50)
    after = read_run_manifest(run_directory)
    assert after["solver_wall_time_s"] == pytest.approx(12.5)
    assert after["processor_count"] == 50
    assert "extraction_wall_time_s" not in after
    assert "solver_time_s" not in after
    for key, value in before.items():
        assert after[key] == value

    with pytest.raises(TypeError):
        solver.stamp_solver_timing_on_run_manifest(run_directory, 1.0, True)
    with pytest.raises(ValueError):
        solver.stamp_solver_timing_on_run_manifest(run_directory, -1.0, 50)


def test_extraction_stamp_adds_only_extraction_wall_time(monkeypatch, tmp_path):
    solver, run_directory = _written_run(monkeypatch, tmp_path)
    solver.stamp_solver_timing_on_run_manifest(run_directory, 12.5, 50)
    before = read_run_manifest(run_directory)
    extract = load_report_extract()
    extract.stamp_extraction_wall_time_on_run_manifest(run_directory, 3.25)
    after = read_run_manifest(run_directory)
    assert after["extraction_wall_time_s"] == pytest.approx(3.25)
    assert after["solver_wall_time_s"] == pytest.approx(12.5)
    assert after["processor_count"] == 50
    assert "solver_time_s" not in after
    for key, value in before.items():
        assert after[key] == value

    with pytest.raises(TypeError):
        extract.stamp_extraction_wall_time_on_run_manifest(run_directory, True)


def test_solver_clock_starts_at_launch_and_stops_after_final_write():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    launch = "fluent_launched_at = time.monotonic()\n            meshing = pyfluent.launch_fluent("
    assert source.count(launch) == 2
    publish = source[
        source.index("def publish_stop_reason_and_finals") : source.index(
            "def resolve_solver_paths"
        )
    ]
    assert publish.index("solver.settings.file.write_case_data(") < publish.index(
        "return solver_stop_reason"
    )
    call = source.index("        publish_stop_reason_and_finals(")
    measured = source.index(
        "        solver_wall_time_s = time.monotonic() - fluent_launched_at",
        call,
    )
    stamped = source.index("        stamp_solver_timing_on_run_manifest(", call)
    assert call < measured < stamped
    stamp_fn = source[
        source.index("def stamp_solver_timing_on_run_manifest") : source.index(
            "def stamp_run_manifest_final_artifact_hashes"
        )
    ]
    assert "solver_time_s" not in stamp_fn
    assert "extraction_wall_time_s" not in stamp_fn


def test_extraction_clock_is_the_existing_timing_total():
    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":') :]
    assert main.index("_reset_extract_timing()") < main.index("pyfluent.launch_fluent(")
    finally_at = main.index("extraction_wall_time_s = _write_report_extract_timing(")
    stamp_at = main.index("stamp_extraction_wall_time_on_run_manifest(", finally_at)
    exit_at = main.index("solver.exit()", stamp_at)
    assert finally_at < stamp_at < exit_at
