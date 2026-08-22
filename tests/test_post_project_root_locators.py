"""Post scripts must not locate the project root with parents[N]."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import POST_DIR, REPO_ROOT, load_module
from test_shear_export_exit import load_shear_export


LOCATOR_SCRIPTS = (
    "pyfluent_field_check.py",
    "pyensight_contour_export.py",
    "pyfluent_shear_contour_export.py",
    "pyensight_extra_figures.py",
)


@pytest.fixture
def field_check():
    return load_module("field_check_under_test", POST_DIR / "pyfluent_field_check.py")


@pytest.fixture
def contour_export():
    return load_module(
        "contour_export_under_test",
        POST_DIR / "pyensight_contour_export.py",
    )


@pytest.fixture
def extra_figures():
    return load_module(
        "extra_figures_under_test",
        POST_DIR / "pyensight_extra_figures.py",
    )


@pytest.mark.parametrize("name", LOCATOR_SCRIPTS)
def test_scripts_have_no_parents_n_project_root_locator(name):
    text = (POST_DIR / name).read_text(encoding="utf-8")
    assert "SCRIPT_DIR.parents" not in text
    assert "parents[1]" not in text
    assert 'project_root / "03_Results"' not in text
    assert 'PROJECT_ROOT / "03_Results"' not in text


def test_field_check_requires_results_dir(field_check):
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="results_dir"):
        field_check.get_case_paths(cfg)


def test_field_check_uses_config_results_dir(field_check, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    results_dir = tmp_path / "runs"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        results_dir=results_dir,
    )
    paths = field_check.get_case_paths(cfg)
    assert paths["results_dir"] == results_dir
    assert paths["case_path"] == results_dir / "Sin_ST" / "u0p1_p4M"
    assert paths["project_root"] == REPO_ROOT


def test_contour_export_requires_results_dir(contour_export):
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="results_dir"):
        contour_export.get_case_paths(cfg)


def test_contour_export_uses_config_results_dir(contour_export, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    results_dir = tmp_path / "runs"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        results_dir=results_dir,
    )
    paths = contour_export.get_case_paths(cfg)
    assert paths["case_path"] == results_dir / "Sin_ST" / "u0p1_p4M"
    assert paths["project_root"] == REPO_ROOT


def test_shear_export_requires_results_dir():
    mod = load_shear_export()
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="results_dir"):
        mod.get_case_paths(cfg)


def test_shear_export_uses_config_results_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    mod = load_shear_export()
    results_dir = tmp_path / "runs"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        results_dir=results_dir,
    )
    paths = mod.get_case_paths(cfg)
    assert paths["case_path"] == results_dir / "Sin_ST" / "u0p1_p4M"
    assert paths["project_root"] == REPO_ROOT


def test_extra_figures_requires_results_dir(extra_figures):
    cfg = {"geo_name": "Sin_ST", "case_name": "u0p1_p4M", "results_dir": None}
    with pytest.raises(ValueError, match="results_dir"):
        extra_figures.build_paths(cfg)


def test_extra_figures_uses_config_results_dir(extra_figures, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    results_dir = tmp_path / "runs"
    paths = extra_figures.build_paths(
        {"geo_name": "Sin_ST", "case_name": "u0p1_p4M", "results_dir": results_dir}
    )
    assert paths["case_path"] == results_dir / "Sin_ST" / "u0p1_p4M"
    assert paths["project_root"] == REPO_ROOT
