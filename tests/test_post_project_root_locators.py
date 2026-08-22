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


def test_field_check_requires_run_selection(field_check):
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="filename labels only"):
        field_check.get_case_paths(cfg)


def test_field_check_uses_config_case_path(field_check, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    case_path = tmp_path / "runs" / "canonical"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        case_path=case_path,
    )
    paths = field_check.get_case_paths(cfg)
    assert paths["case_path"] == case_path
    assert paths["project_root"] == REPO_ROOT


def test_contour_export_requires_run_selection(contour_export):
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="filename labels only"):
        contour_export.get_case_paths(cfg)


def test_contour_export_uses_config_case_path(contour_export, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    case_path = tmp_path / "runs" / "canonical"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        case_path=case_path,
    )
    paths = contour_export.get_case_paths(cfg)
    assert paths["case_path"] == case_path
    assert paths["project_root"] == REPO_ROOT


def test_shear_export_requires_run_selection():
    mod = load_shear_export()
    cfg = SimpleNamespace(geo_name="Sin_ST", case_name="u0p1_p4M")
    with pytest.raises(ValueError, match="filename labels only"):
        mod.get_case_paths(cfg)


def test_shear_export_uses_config_case_path(tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    mod = load_shear_export()
    case_path = tmp_path / "runs" / "canonical"
    cfg = SimpleNamespace(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        case_path=case_path,
    )
    paths = mod.get_case_paths(cfg)
    assert paths["case_path"] == case_path
    assert paths["project_root"] == REPO_ROOT


def test_extra_figures_requires_run_selection(extra_figures):
    cfg = {"geo_name": "Sin_ST", "case_name": "u0p1_p4M"}
    with pytest.raises(ValueError, match="filename labels only"):
        extra_figures.build_paths(cfg)


def test_extra_figures_uses_config_case_path(extra_figures, tmp_path, monkeypatch):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    case_path = tmp_path / "runs" / "canonical"
    paths = extra_figures.build_paths(
        {"geo_name": "Sin_ST", "case_name": "u0p1_p4M", "case_path": case_path}
    )
    assert paths["case_path"] == case_path
    assert paths["project_root"] == REPO_ROOT
