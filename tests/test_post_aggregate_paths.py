"""Aggregate CSV and remaining post-path locators share RO_DATA_ROOT/inventory."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from helpers import (
    CONFIGS_DIR,
    POST_DIR,
    REPO_ROOT,
    SCRIPTS_DIR,
    load_batch_report_extract,
    load_module,
    load_post_config,
    load_run_config,
)


def load_report_extract():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "report_extract_under_test",
            POST_DIR / "01_pyfluent_report_extract.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_summary_figures():
    stubs = {
        "matplotlib": types.ModuleType("matplotlib"),
        "matplotlib.pyplot": types.ModuleType("matplotlib.pyplot"),
        "matplotlib.ticker": types.ModuleType("matplotlib.ticker"),
        "numpy": types.ModuleType("numpy"),
        "pandas": types.ModuleType("pandas"),
    }
    stubs["matplotlib"].use = lambda *_args, **_kwargs: None
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "summary_figures_under_test",
            POST_DIR / "05_make_summary_figures.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_rebuild_ledger():
    return load_module(
        "rebuild_mesh_ledger_under_test",
        SCRIPTS_DIR / "rebuild_mesh_ledger_from_logs.py",
    )


@pytest.mark.parametrize(
    "path",
    [
        CONFIGS_DIR / "00_post_config.py",
        CONFIGS_DIR / "00_batch_post_config.py",
        CONFIGS_DIR / "run_config.py",
        SCRIPTS_DIR / "post_processing/05_make_summary_figures.py",
        SCRIPTS_DIR / "rebuild_mesh_ledger_from_logs.py",
        SCRIPTS_DIR / "post_processing/01_pyfluent_report_extract.py",
        SCRIPTS_DIR / "post_processing/01_batch_report_extract.py",
    ],
)
def test_6a3c_scripts_have_no_parents_n_project_root_locator(path):
    text = path.read_text(encoding="utf-8")
    assert "parents[1]" not in text
    assert "parents[2]" not in text
    assert ' / "03_Results"' not in text
    assert ' / "03_Results" /' not in text


def test_batch_post_config_does_not_declare_aggregate_csv_paths():
    text = (CONFIGS_DIR / "00_batch_post_config.py").read_text(encoding="utf-8")
    assert "merged_summary_csv" not in text
    assert "status_csv" not in text
    assert "project_root =" not in text


def test_post_config_project_root_uses_canonical_discovery(monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    cfg = load_post_config()
    assert cfg.project_root == str(REPO_ROOT)


def test_run_config_project_root_uses_canonical_discovery(monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    cfg = load_run_config()
    assert cfg.project_root == str(REPO_ROOT)


def test_report_extract_requires_a_case_locator():
    extract = load_report_extract()
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="case_path, results_dir, or final_case_file"):
        extract.resolve_report_case_paths(cfg)


def test_report_extract_uses_results_dir(tmp_path):
    extract = load_report_extract()
    results_dir = tmp_path / "runs"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        results_dir=results_dir,
    )
    paths = extract.resolve_report_case_paths(cfg)
    assert paths["case_path"] == results_dir / "Sin_ST" / "u0p1_p4M"


def test_report_extract_uses_final_case_file_parent(tmp_path):
    extract = load_report_extract()
    case_dir = tmp_path / "runs" / "Sin_ST" / "u0p1_p4M"
    final_case = case_dir / "Sin_ST_u0p1_p4M_final.cas.h5"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        final_case_file=final_case,
    )
    paths = extract.resolve_report_case_paths(cfg)
    assert paths["case_path"] == case_dir
    assert paths["final_case_file"] == final_case


def test_batch_report_aggregate_paths_use_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    batch = load_batch_report_extract()
    summary, status = batch.aggregate_output_paths()
    assert summary == tmp_path / "inventory" / "all_cases_post_summary.csv"
    assert status == tmp_path / "inventory" / "all_cases_post_status.csv"


def test_batch_report_results_dir_defaults_to_runs_root(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    batch = load_batch_report_extract()
    assert batch.resolve_batch_results_dir(SimpleNamespace()) == tmp_path / "runs"
    assert batch.resolve_batch_results_dir(
        SimpleNamespace(results_dir=tmp_path / "custom")
    ) == tmp_path / "custom"


def test_summary_figures_parse_args_does_not_require_data_root(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    figures = load_summary_figures()
    args = figures.parse_args([])
    assert args.summary_csv is None
    assert args.status_csv is None
    assert args.out_dir is None


def test_summary_figures_defaults_use_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    figures = load_summary_figures()
    args = figures.resolve_path_defaults(figures.parse_args([]))
    inventory = tmp_path / "inventory"
    assert args.summary_csv == inventory / "all_cases_post_summary.csv"
    assert args.status_csv == inventory / "all_cases_post_status.csv"
    assert args.out_dir == inventory / "post_summary_figures"


def test_rebuild_ledger_parse_args_does_not_require_data_root(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    retro = load_rebuild_ledger()
    args = retro.parse_args([])
    assert args.results_root is None
    assert args.output is None


def test_rebuild_ledger_missing_meshes_root_is_not_silent(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    retro = load_rebuild_ledger()
    assert retro.main([]) == 2


def test_rebuild_ledger_output_defaults_to_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    retro = load_rebuild_ledger()
    args = retro.resolve_path_defaults(retro.parse_args([]))
    assert args.results_root == meshes.resolve()
    assert args.output == (tmp_path / "inventory" / "mesh_ledger.csv").resolve()
