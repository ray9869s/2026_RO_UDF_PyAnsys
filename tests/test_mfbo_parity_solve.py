"""Guards and comparison for the MFBO parity-solve scripts. No Fluent."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from helpers import SCRIPTS_DIR, load_module


def load_solve():
    return load_module(
        "run_parity_solve_under_test",
        SCRIPTS_DIR / "mfbo" / "run_parity_solve.py",
    )


def load_compare():
    return load_module(
        "compare_parity_solves_under_test",
        SCRIPTS_DIR / "mfbo" / "compare_parity_solves.py",
    )


def test_data_root_refuses_production_tree(tmp_path):
    solve = load_solve()
    compare = load_compare()
    for guard in (solve.resolve_data_root, compare.resolve_data_root):
        for value in (
            "C:/ro_data",
            "C:/ro_data/runs",
            r"C:\ro_data\meshes",
            "c:/RO_DATA/runs/pillar",
            "/mnt/c/ro_data",
            "/mnt/c/ro_data/runs",
        ):
            with pytest.raises(ValueError, match="production data root"):
                guard(value)
        assert guard(str(tmp_path)) == tmp_path.resolve()


def test_mesh_leaf_required_and_existing_run_refused(tmp_path):
    solve = load_solve()
    mesh = solve.mesh_leaf(tmp_path)
    run = solve.run_leaf(tmp_path)
    with pytest.raises(FileNotFoundError, match="Mesh leaf"):
        solve.require_mesh_leaf(mesh)

    mesh.mkdir(parents=True)
    (mesh / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"\.msh\.h5"):
        solve.require_mesh_leaf(mesh)

    (mesh / f"{solve.SOURCE_GEO_ID}_{solve.SOURCE_MESH_ID}.msh.h5").write_bytes(b"mesh")
    solve.require_mesh_leaf(mesh)

    run.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="run leaf already exists"):
        solve.refuse_existing_run(run)


def test_operating_gates_raise_and_other_diffs_do_not():
    solve = load_solve()
    manifest = {
        "u_target_ms": 0.2,
        "p_gauge_pa": 6.0e6,
        "inlet_bc_type": "parabolic",
        "udf_version": "260822_RO_UDF.c",
        "solver_settings": {
            "max_iterations": 2000,
            "residual_target": 1.0e-7,
            "operating_pressure": 101325.0,
        },
    }
    overrides = {
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
        "use_inlet_velocity_profile": True,
        "max_iterations": 100,
        "residual_target": 1.0e-7,
        "operating_pressure": 101325.0,
    }
    rows = solve.compare_operating_settings(
        manifest,
        overrides,
        udf_file_name="260929_RO_UDF.c",
    )
    by_field = {row["field"]: row for row in rows}
    assert by_field["u_target_ms"]["status"] == "ok"
    assert by_field["max_iterations"]["status"] == "diff"
    assert by_field["max_iterations"]["gate"] is False
    assert by_field["udf_version"]["status"] == "diff"
    assert by_field["udf_version"]["override_value"] == "260929_RO_UDF.c"

    overrides["inlet_velocity_value"] = 0.3
    with pytest.raises(ValueError, match="u_target_ms"):
        solve.compare_operating_settings(
            manifest, overrides, udf_file_name="260929_RO_UDF.c"
        )
    overrides["inlet_velocity_value"] = 0.2
    overrides["outlet_gauge_pressure"] = 4.0e6
    with pytest.raises(ValueError, match="p_gauge_pa"):
        solve.compare_operating_settings(
            manifest, overrides, udf_file_name="260929_RO_UDF.c"
        )
    overrides["outlet_gauge_pressure"] = 6.0e6
    overrides["use_inlet_velocity_profile"] = False
    with pytest.raises(ValueError, match="inlet_bc_type"):
        solve.compare_operating_settings(
            manifest, overrides, udf_file_name="260929_RO_UDF.c"
        )


def test_source_case_is_the_single_production_entry():
    solve = load_solve()
    case, overrides, _retries, _settle = solve.load_source_case()
    assert case["geo_id"] == solve.SOURCE_GEO_ID
    assert case["mesh_id"] == solve.SOURCE_MESH_ID
    assert case["run_id"] == solve.SOURCE_RUN_ID
    assert case["family"] == solve.SOURCE_FAMILY
    assert overrides["inlet_velocity_value"] == 0.2
    assert overrides["outlet_gauge_pressure"] == 6.0e6
    assert overrides["use_inlet_velocity_profile"] is True
    assert overrides["geo_name"] == solve.SOURCE_GEO_ID
    assert overrides["case_name"] == solve.SOURCE_RUN_ID
    assert "base_case_name" not in overrides


def test_child_env_does_not_stick_on_the_parent(monkeypatch, tmp_path):
    solve = load_solve()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setenv("PYFLUENT_RUN_CONFIG", "leftover")
    monkeypatch.setenv("PYFLUENT_SKIP_VALIDATION", "1")

    def _seen():
        assert os.environ["RO_DATA_ROOT"] == str(tmp_path)
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        solve.call_with_data_root(tmp_path, _seen)
    assert "RO_DATA_ROOT" not in os.environ

    overrides = {"case_name": "u0p2_p6M", "inlet_velocity_value": 0.2}
    env = solve.solver_child_env(tmp_path, overrides)
    assert env["RO_DATA_ROOT"] == str(tmp_path)
    assert "PYFLUENT_RUN_CONFIG" not in env
    assert "PYFLUENT_SKIP_VALIDATION" not in env
    assert json.loads(env["PYFLUENT_RUN_OVERRIDES"])["case_name"] == "u0p2_p6M"
    assert "RO_DATA_ROOT" not in os.environ

    extract_env = solve.extract_child_env(tmp_path, {"geo_name": "P_p100_h30"}, tmp_path / "post_config.py")
    assert extract_env["RO_DATA_ROOT"] == str(tmp_path)
    assert extract_env["PYFLUENT_POST_CONFIG"].endswith("post_config.py")
    assert json.loads(extract_env["PYFLUENT_POST_OVERRIDES"])["geo_name"] == "P_p100_h30"
    assert "RO_DATA_ROOT" not in os.environ


def test_solver_launch_uses_batch_runner_without_parent_data_root(monkeypatch, tmp_path):
    solve = load_solve()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    seen = {}

    def runner(cmd, env, cwd, log_path):
        seen["cmd"] = cmd
        seen["env"] = env
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(solve.batch_solver_sweep, "run_solver_attempts", _attempts(runner))
    run = solve.run_leaf(tmp_path)
    result = solve.launch_solver(
        {"case_name": "u0p2_p6M"},
        tmp_path,
        run,
        max_retries=0,
        settle_s=0.0,
    )
    assert result.returncode == 0
    assert seen["cmd"][0] == solve.sys.executable
    assert seen["cmd"][1].endswith("solver_code_260616.py")
    assert seen["cwd"] == str(solve.batch_solver_sweep.SCRIPT_DIR)
    assert seen["env"]["RO_DATA_ROOT"] == str(tmp_path)
    assert "RO_DATA_ROOT" not in os.environ
    assert (run / "solver_retry_record.json").is_file()


def _attempts(runner):
    def _run(**kwargs):
        result = runner(
            kwargs["cmd"],
            env=kwargs["env"],
            cwd=kwargs["cwd"],
            log_path=kwargs["run_directory"] / "attempt.log",
        )
        return result, 1, [], []

    return _run


def _write_side(root, *, lmh, lmh_signed, udf, stop, g_value, u_mean, continuity=None):
    compare = load_compare()
    mesh = compare.mesh_manifest_path(root)
    run = compare.run_manifest_path(root)
    wide = compare.summary_wide_path(root)
    mesh.parent.mkdir(parents=True)
    run.parent.mkdir(parents=True)
    wide.parent.mkdir(parents=True)
    mesh_payload = {"inlet_profile_G": g_value}
    run_payload = {
        "u_mean_ms": u_mean,
        "stop_reason": stop,
        "udf_version": udf,
    }
    if continuity is not None:
        run_payload["continuity_final"] = continuity
    mesh.write_text(json.dumps(mesh_payload), encoding="utf-8")
    run.write_text(json.dumps(run_payload), encoding="utf-8")
    wide.write_text(
        "lmh_mass_balance,lmh_mass_balance_signed,"
        "pressure_drop_spacer_per_m,cp_canon_window_avg,cp_canon_window_max\n"
        f"{lmh},{lmh_signed},1000.0,1.10,1.40\n",
        encoding="utf-8",
    )


def test_comparison_reports_diffs_without_thresholds(tmp_path):
    compare = load_compare()
    root_a = tmp_path / "parity_code"
    root_b = tmp_path / "parity_geometry"
    _write_side(
        root_a,
        lmh=10.0,
        lmh_signed=-10.0,
        udf="260929_RO_UDF.c",
        stop="qoi_met",
        g_value=1.01,
        u_mean=0.198,
        continuity=1.0e-6,
    )
    _write_side(
        root_b,
        lmh=12.0,
        lmh_signed=-12.0,
        udf="260929_RO_UDF.c",
        stop="qoi_met",
        g_value=1.02,
        u_mean=0.196,
    )
    payload = compare.build_comparison(root_a, root_b)
    by_field = {row["field"]: row for row in payload["rows"]}
    assert by_field["lmh_mass_balance"]["value_a"] == 10.0
    assert by_field["lmh_mass_balance"]["value_b"] == 12.0
    assert by_field["lmh_mass_balance"]["abs_diff"] == 2.0
    assert by_field["lmh_mass_balance"]["rel_diff"] == pytest.approx(2.0 / 12.0)
    assert by_field["lmh_mass_balance_signed"]["abs_diff"] == 2.0
    assert by_field["pressure_drop_spacer_per_m"]["abs_diff"] == 0.0
    assert by_field["cp_canon_window_avg"]["value_a"] == 1.10
    assert by_field["cp_canon_window_max"]["value_b"] == 1.40
    assert by_field["inlet_profile_G"]["abs_diff"] == pytest.approx(0.01)
    assert by_field["u_mean_ms"]["value_a"] == 0.198
    assert by_field["stop_reason"]["abs_diff"] is None
    assert by_field["udf_version"]["value_a"] == "260929_RO_UDF.c"
    assert by_field["udf_version"]["value_b"] == "260929_RO_UDF.c"
    assert by_field["udf_version"]["rel_diff"] is None
    assert by_field["continuity_final"]["value_a"] == 1.0e-6
    assert by_field["continuity_final"]["value_b"] is None
    assert "final_iteration" not in by_field
    assert "iteration_count" not in by_field

    out = compare.comparison_output_path(root_a, root_b)
    compare.write_comparison(payload, out)
    assert out == tmp_path / "parity_solve_comparison.json"
    assert json.loads(out.read_text(encoding="utf-8"))["rel_diff_denominator"] == "b"
