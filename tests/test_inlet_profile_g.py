"""Parse-and-assert RO_UDF_INLET_PROFILE_G from fluent-*.trn."""

from __future__ import annotations

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)
from ro.manifest import read_mesh_manifest, read_run_manifest, write_mesh_manifest
from ro.paths import mesh_dir, run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload


G_TOKEN = "1.00360939613"
G_VALUE = float(G_TOKEN)


def _write_trn(case_dir, name, body):
    path = case_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def test_agreed_tokens_return_the_shared_value():
    solver = load_solver_code("inlet_g_agree")
    assert solver.agreed_inlet_profile_g([G_TOKEN, G_TOKEN]) == G_VALUE


def test_disagreeing_tokens_raise():
    solver = load_solver_code("inlet_g_disagree")
    with pytest.raises(RuntimeError, match="Disagreeing RO_UDF_INLET_PROFILE_G"):
        solver.agreed_inlet_profile_g([G_TOKEN, "1.1"])


def test_missing_tokens_raise():
    solver = load_solver_code("inlet_g_missing")
    with pytest.raises(RuntimeError, match="Missing required transcript marker"):
        solver.agreed_inlet_profile_g([])


def test_parse_collects_all_trn_hits_and_ignores_solver_log(tmp_path):
    solver = load_solver_code("inlet_g_trn_only")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    _write_trn(
        case_dir,
        "fluent-0001.trn",
        f"RO_UDF_INLET_PROFILE_G={G_TOKEN}\n"
        "=== RO_UDF probe_inlet_profile ===\n"
        f"RO_UDF_INLET_PROFILE_G={G_TOKEN}\n",
    )
    solver_log = case_dir / "solver_log_u0p1_p6M.txt"
    solver_log.write_text("RO_UDF_INLET_PROFILE_G=9.9\n", encoding="utf-8")

    tokens = solver.collect_inlet_profile_g_tokens(case_dir, solver_log)
    assert tokens == [G_TOKEN, G_TOKEN]
    assert solver.wait_for_agreed_inlet_profile_g(
        case_dir=case_dir,
        solver_log_path=solver_log,
        timeout_s=0.01,
        poll_interval_s=0.001,
    ) == G_VALUE


def test_g_only_in_solver_log_is_missing(tmp_path):
    solver = load_solver_code("inlet_g_solver_log_ignored")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    solver_log = case_dir / "solver_log_u0p1_p6M.txt"
    solver_log.write_text(f"RO_UDF_INLET_PROFILE_G={G_TOKEN}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="fluent-\\*\\.trn"):
        solver.wait_for_agreed_inlet_profile_g(
            case_dir=case_dir,
            solver_log_path=solver_log,
            timeout_s=0.05,
            poll_interval_s=0.01,
        )


def test_lazy_fill_writes_once_then_compares(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    solver = load_solver_code("inlet_g_fill")
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["inlet_profile_G"] = None
    write_mesh_manifest(directory, payload)

    filled = solver.apply_parsed_inlet_profile_g(directory, G_VALUE)
    assert filled == G_VALUE
    stored = read_mesh_manifest(directory)["inlet_profile_G"]
    assert stored == G_VALUE

    close = 1.003609
    matched = solver.apply_parsed_inlet_profile_g(directory, close)
    assert matched == G_VALUE
    assert read_mesh_manifest(directory)["inlet_profile_G"] == G_VALUE

    with pytest.raises(RuntimeError, match="Aborting before iterate"):
        solver.apply_parsed_inlet_profile_g(directory, 1.1)
    assert read_mesh_manifest(directory)["inlet_profile_G"] == G_VALUE


def test_parabolic_finalize_fills_u_mean(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    solver = load_solver_code("inlet_g_finalize")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.use_inlet_velocity_profile = True
    cfg.inlet_velocity_value = 0.1
    cfg.outlet_gauge_pressure = 6.0e6
    cfg.family = FAMILY
    cfg.geo_id = GEO_ID
    cfg.mesh_id = MESH_ID
    cfg.run_id = "u0p1_p6M"

    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    mesh_directory.mkdir(parents=True)
    mesh = mesh_payload()
    mesh["inlet_profile_G"] = None
    write_mesh_manifest(mesh_directory, mesh)

    run_directory = run_dir(cfg.family, cfg.geo_id, cfg.mesh_id, cfg.run_id)
    run_directory.mkdir(parents=True)
    solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)
    assert read_run_manifest(run_directory)["u_mean_ms"] is None

    solver.finalize_worker_run_manifest(
        run_directory,
        "max_iter_reached",
        inlet_profile_g=G_VALUE,
    )
    finalized = read_run_manifest(run_directory)
    assert finalized["u_mean_ms"] == pytest.approx(0.1 / G_VALUE)
    assert finalized["stop_reason"] == "max_iter_reached"

    solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)
    assert read_run_manifest(run_directory)["u_mean_ms"] == pytest.approx(0.1 / G_VALUE)


def test_parabolic_finalize_without_g_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    solver = load_solver_code("inlet_g_finalize_missing")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.use_inlet_velocity_profile = True
    cfg.family = FAMILY
    cfg.geo_id = GEO_ID
    cfg.mesh_id = MESH_ID
    cfg.run_id = "u0p1_p6M"
    cfg.outlet_gauge_pressure = 6.0e6

    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    mesh_directory.mkdir(parents=True)
    mesh = mesh_payload()
    write_mesh_manifest(mesh_directory, mesh)

    run_directory = run_dir(cfg.family, cfg.geo_id, cfg.mesh_id, cfg.run_id)
    run_directory.mkdir(parents=True)
    solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)

    with pytest.raises(RuntimeError, match="without inlet_profile_G"):
        solver.finalize_worker_run_manifest(run_directory, "max_iter_reached")


def test_g_assert_is_before_iterate():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    apply_at = source.index("apply_parsed_inlet_profile_g(")
    iterate_at = source.index("solution.run_calculation.iterate")
    assert apply_at < iterate_at
    assert "solver_log_*.txt is not" in source
