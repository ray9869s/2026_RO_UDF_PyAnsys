"""Path defaults for inventory/post scripts must not be cwd-relative."""

from __future__ import annotations

import pytest

from helpers import (
    POST_DIR,
    SCRIPTS_DIR,
    load_batch_postprocess,
    load_case_inventory,
    load_module,
)


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


def test_inventory_dry_run_finds_cases_from_data_root(
    inventory, monkeypatch, tmp_path, capsys
):
    data = tmp_path / "data"
    elsewhere = tmp_path / "elsewhere"
    case_dir = data / "runs" / "Sin_ST" / "u0p1_p4M"
    case_dir.mkdir(parents=True)
    elsewhere.mkdir()
    monkeypatch.setenv("RO_DATA_ROOT", str(data))
    monkeypatch.chdir(elsewhere)

    rc = inventory.main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total cases: 1" in captured.out
    assert "Dry run: would write case_inventory.csv" in captured.out
    assert not (data / "inventory" / "case_inventory.csv").exists()


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
