"""Path defaults for inventory/post scripts must not be cwd-relative."""

from __future__ import annotations

import json

import pytest

from helpers import (
    POST_DIR,
    SCRIPTS_DIR,
    load_batch_postprocess,
    load_case_inventory,
    load_module,
)
from ro.paths import run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, write_test_run


CWD_BUG_SCRIPTS = (
    "case_inventory.py",
    "batch_postprocess_all_cases.py",
    "residual_measurement_report.py",
)


@pytest.fixture
def inventory():
    return load_case_inventory()


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


@pytest.fixture
def residual_report():
    return load_module(
        "residual_measurement_report_under_test",
        POST_DIR / "residual_measurement_report.py",
    )


@pytest.mark.parametrize("name", CWD_BUG_SCRIPTS)
def test_scripts_do_not_use_cwd_relative_results_defaults(name):
    text = (POST_DIR / name).read_text(encoding="utf-8")
    assert 'Path("My_CFD_Project")' not in text
    assert "DEFAULT_RESULTS_ROOT" not in text


def test_inventory_and_residual_load_batch_config_from_configs_dir():
    expected = 'project_root() / "configs" / "batch_config.py"'
    inventory = (POST_DIR / "case_inventory.py").read_text(encoding="utf-8")
    residual = (POST_DIR / "residual_measurement_report.py").read_text(
        encoding="utf-8"
    )
    assert expected in inventory
    assert 'parents[1] / "batch_config.py"' not in inventory
    assert expected in residual
    assert 'SCRIPT_DIR.parent / "batch_config.py"' not in residual


def test_inventory_parse_args_does_not_require_data_root(inventory, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = inventory.parse_args([])
    assert args.results_root is None
    assert args.output_dir is None


def test_residual_parse_args_does_not_require_data_root(residual_report, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = residual_report.parse_args([])
    assert args.results_root is None
    assert args.output_dir is None


def test_batch_post_parse_args_does_not_require_data_root(batch_post, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = batch_post.parse_args([])
    assert args.results_root is None
    assert args.inventory_csv is None


def test_inventory_missing_runs_root_is_not_silent_success(
    inventory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rc = inventory.main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Total cases: 0" not in captured.out
    assert "ERROR:" in captured.err


def test_residual_missing_runs_root_is_not_silent_success(
    residual_report, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rc = residual_report.main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Cases found  : 0" not in captured.out
    assert "ERROR:" in captured.err


def test_inventory_dry_run_finds_cases_from_manifests(
    inventory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_test_run(run_id="u0p2_p6M", stop_reason="max_iter_reached", u_target_ms=0.2)
    write_test_run(run_id="u0p3_p6M", stop_reason="max_iter_reached", u_target_ms=0.3)
    monkeypatch.chdir(elsewhere)

    rc = inventory.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total cases: 2" in captured.out
    assert "Max-iter reached: 2" in captured.out
    assert "Dry run: would write case_inventory.csv" in captured.out
    assert not (tmp_path / "inventory" / "case_inventory.csv").exists()


def test_inventory_refuses_run_dir_without_manifest(
    inventory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID).mkdir(parents=True)

    rc = inventory.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Total cases: 0" not in captured.out
    assert "ERROR:" in captured.err
    assert "manifest" in captured.err.lower()


def test_inventory_refuses_manifest_ids_that_disagree_with_path(
    inventory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = write_test_run()
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["geo_id"] = "D1225_a45"
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    rc = inventory.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "ERROR:" in captured.err
    assert "ids do not match" in captured.err


def test_inventory_two_level_leftover_is_not_a_case(
    inventory, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    leftover = tmp_path / "runs" / "Sin_ST" / "u0p1_p4M"
    leftover.mkdir(parents=True)
    (leftover / "solver_log.txt").write_text("iteration 10\n", encoding="utf-8")

    rc = inventory.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total cases: 0" in captured.out


def test_inventory_log_walk_stays_inside_one_run_dir(
    inventory, monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    run_a = write_test_run(run_id="u0p2_p6M", stop_reason="max_iter_reached")
    run_b = write_test_run(run_id="u0p3_p6M", stop_reason="max_iter_reached")
    (run_a / "solver_log_a.txt").write_text("iteration 10\n", encoding="utf-8")
    (run_b / "solver_log_b.txt").write_text("iteration 20\n", encoding="utf-8")

    logs_a, _, _ = inventory.find_log_files(run_a)
    logs_b, _, _ = inventory.find_log_files(run_b)
    names_a = {path.name for path in logs_a}
    names_b = {path.name for path in logs_b}
    assert names_a == {"solver_log_a.txt"}
    assert names_b == {"solver_log_b.txt"}
    assert all(run_a in path.parents or path.parent == run_a for path in logs_a)
    assert all(run_b in path.parents or path.parent == run_b for path in logs_b)


def test_batch_post_resolves_inventory_under_data_root(
    batch_post, monkeypatch, tmp_path
):
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(elsewhere)

    args = batch_post.parse_args([])
    batch_post.resolve_path_defaults(args)
    assert args.results_root == (tmp_path / "runs").resolve()
    assert args.inventory_csv == (
        tmp_path / "inventory" / "case_inventory_compact.csv"
    ).resolve()


@pytest.fixture
def rerun07():
    return load_module(
        "batch_solver_rerun_inventory_defaults",
        SCRIPTS_DIR / "batch_solver_rerun.py",
    )


def test_07_script_has_no_legacy_results_root_default():
    text = (SCRIPTS_DIR / "batch_solver_rerun.py").read_text(encoding="utf-8")
    assert "DEFAULT_RESULTS_ROOT" not in text
    assert "PROJECT_ROOT" not in text
    assert ' / "03_Results"' not in text
    assert "_inventory" not in text


def test_07_parse_args_does_not_require_data_root(rerun07, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = rerun07.parse_args([])
    assert args.results_root is None
    assert args.candidates_csv is None
    assert args.output_dir is None
    assert args.logs_dir is None


def test_07_inventory_defaults_use_data_root(rerun07, monkeypatch, tmp_path):
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(elsewhere)

    args = rerun07.parse_args([])
    rerun07.resolve_path_defaults(args)
    inventory = (tmp_path / "inventory").resolve()
    assert args.results_root == inventory
    assert args.candidates_csv == inventory / "active_solver_rerun_candidates.csv"
    assert args.output_dir == inventory / "solver_rerun"
    assert args.logs_dir == inventory / "solver_rerun_logs"


def test_07_results_root_override_wins(rerun07, monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path / "data"))
    custom = tmp_path / "custom-inventory"
    args = rerun07.parse_args(["--results-root", str(custom)])
    rerun07.resolve_path_defaults(args)
    custom = custom.resolve()
    assert args.results_root == custom
    assert args.candidates_csv == custom / "active_solver_rerun_candidates.csv"
    assert args.output_dir == custom / "solver_rerun"
    assert args.logs_dir == custom / "solver_rerun_logs"


def test_batch_post_default_cff_template_uses_templates_dir(
    batch_post, monkeypatch, tmp_path
):
    template = tmp_path / "cff_wall_shear_rate.scm"
    template.write_text("# test template\n", encoding="utf-8")
    monkeypatch.setattr(batch_post, "templates_dir", lambda: tmp_path)
    args = batch_post.parse_args([])
    chosen, source = batch_post.resolve_cff_file(
        args,
        {"contours_dir": tmp_path / "missing-contours"},
    )
    assert chosen == template
    assert source == batch_post.CFF_SOURCE_TEMPLATE
