"""Tests for canonical project and external data-tree paths."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from helpers import REPO_ROOT
from ro import paths


FAMILY = "diamond"
GEO_ID = "D2450_a45"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
RUN_ID = "u0p2_p6M"


def test_project_root_environment_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", "relative-project")
    assert paths.project_root() == Path("relative-project")


def test_project_root_is_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.project_root() == REPO_ROOT


def test_project_root_empty_override_uses_marker_search(monkeypatch, tmp_path):
    monkeypatch.setenv("PYFLUENT_PROJECT_ROOT", "")
    monkeypatch.chdir(tmp_path)
    assert paths.project_root() == REPO_ROOT


def test_paths_module_loads_directly_by_file_path(monkeypatch, tmp_path):
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    module_path = REPO_ROOT / "src" / "ro" / "paths.py"
    spec = importlib.util.spec_from_file_location("ro_paths_by_file", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.project_root() == REPO_ROOT


def test_data_root_unset_raises(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    with pytest.raises(ValueError, match="RO_DATA_ROOT"):
        paths.data_root()


def test_data_root_relative_raises(monkeypatch):
    monkeypatch.setenv("RO_DATA_ROOT", "relative-data")
    with pytest.raises(ValueError, match="absolute"):
        paths.data_root()


@pytest.mark.parametrize(
    ("family", "geo_id", "mesh_id", "message"),
    [
        (FAMILY, GEO_ID, "max085_min0006_cpg5_bl4_peel2", "mesh_id"),
        (FAMILY, GEO_ID, "max085_min006_cpg5_bl4", "mesh_id"),
        (FAMILY, "D2450_a45_7c", MESH_ID, "geo_id"),
        ("unknown", GEO_ID, MESH_ID, "family"),
    ],
)
def test_invalid_ids_raise(monkeypatch, tmp_path, family, geo_id, mesh_id, message):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match=message):
        paths.mesh_dir(family, geo_id, mesh_id)


@pytest.mark.parametrize(
    "run_id",
    [
        "u0p2_p6M",
        "u0p2_p6M_plug",
        "u0p20_p6M",
        "u0p2_p6M_abc123",
    ],
)
def test_run_dir_accepts_optional_letter_led_suffix(monkeypatch, tmp_path, run_id):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    assert paths.RUN_ID_RE.fullmatch(run_id)
    run = paths.run_dir(FAMILY, GEO_ID, MESH_ID, run_id)
    assert run == tmp_path / "runs" / FAMILY / GEO_ID / MESH_ID / run_id


@pytest.mark.parametrize(
    "run_id",
    [
        "u0p2_p6M_20",
        "u0p2_p6M_0",
        "u0p2_p6M_",
        "u0p2_p6M_Plug",
        "u0p2_p6M__plug",
    ],
)
def test_run_dir_rejects_digit_or_empty_suffix(monkeypatch, tmp_path, run_id):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    assert paths.RUN_ID_RE.fullmatch(run_id) is None
    with pytest.raises(ValueError, match="run_id"):
        paths.run_dir(FAMILY, GEO_ID, MESH_ID, run_id)


def test_optional_suffix_cannot_shift_operating_point_tokens():
    """A suffix cannot rewrite u or p; extra digits belong in the base token.

    u0p20_p6M is a legitimate bare id (0.20 m/s-shaped token). u0p2_p6M_20
    is a digits-only suffix and is rejected, so it cannot masquerade as a
    different velocity.
    """
    operating_point = re.compile(r"^u\d+p\d+_p\d+M$")
    assert paths.RUN_ID_RE.fullmatch("u0p20_p6M")
    assert operating_point.fullmatch("u0p20_p6M")
    assert paths.RUN_ID_RE.fullmatch("u0p2_p6M_20") is None
    for run_id in ("u0p2_p6M", "u0p2_p6M_plug"):
        prefix = re.fullmatch(
            r"(u\d+p\d+_p\d+M)(?:_[a-z][a-z0-9]*)?",
            run_id,
        )
        assert prefix is not None
        assert operating_point.fullmatch(prefix.group(1))


def test_builders_form_canonical_hierarchy(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)

    geometry = paths.geometry_dir(FAMILY, GEO_ID)
    mesh = paths.mesh_dir(FAMILY, GEO_ID, MESH_ID)
    run = paths.run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)

    assert geometry == tmp_path / "geometries" / FAMILY / GEO_ID
    assert mesh == tmp_path / "meshes" / FAMILY / GEO_ID / MESH_ID
    assert run == tmp_path / "runs" / FAMILY / GEO_ID / MESH_ID / RUN_ID
    assert paths.templates_dir() == REPO_ROOT / "templates"
    assert paths.udfs_dir() == REPO_ROOT / "udfs"


def test_require_existing_run_raises_without_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="No run manifest"):
        paths.require_existing_run(FAMILY, GEO_ID, MESH_ID, RUN_ID)


def test_resolve_selected_run_directory_needs_ids_or_case_path():
    with pytest.raises(ValueError, match="filename labels only"):
        paths.resolve_selected_run_directory()


def test_resolve_selected_run_directory_uses_case_path(tmp_path):
    case_path = tmp_path / "explicit-run"
    assert paths.resolve_selected_run_directory(case_path=case_path) == case_path


def test_no_results_root_api():
    assert not hasattr(paths, "results_root")
