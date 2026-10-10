"""geometry_suffix selects the meshing CAD filename. Default stays .dsco."""

from __future__ import annotations

import sys
import types

import pytest

from helpers import (
    SCRIPTS_DIR,
    apply_json_overrides,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)
from ro.paths import geometry_dir


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
            "meshing_code_geometry_suffix_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_default_geometry_file_matches_dsco_expression(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    meshing = load_meshing_code()
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)

    resolved = meshing.resolve_meshing_paths(cfg)

    assert resolved["geometry_file"] == (
        geometry_dir(cfg.family, cfg.geo_id) / f"{cfg.geo_id}.dsco"
    )


def test_pmdb_override_uses_batch_override_mechanism(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    meshing = load_meshing_code()
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    apply_json_overrides(cfg, {"geometry_suffix": ".pmdb"})

    resolved = meshing.resolve_meshing_paths(cfg)

    assert str(resolved["geometry_file"]).endswith(".pmdb")
    assert resolved["geometry_file"] == (
        geometry_dir(cfg.family, cfg.geo_id) / f"{cfg.geo_id}.pmdb"
    )


def test_geometry_root_points_at_pmdb_and_leaves_the_mesh_tree(monkeypatch, tmp_path):
    campaign = tmp_path / "campaign"
    monkeypatch.setenv("RO_DATA_ROOT", str(campaign))
    meshing = load_meshing_code()
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    pmdb_root = tmp_path / "geometries_pmdb"
    apply_json_overrides(
        cfg,
        {
            "geometry_suffix": ".pmdb",
            "geometry_root": str(pmdb_root),
        },
    )

    cfg.validate_for_meshing()
    resolved = meshing.resolve_meshing_paths(cfg)

    assert resolved["geometry_file"] == (
        pmdb_root / "diamond" / "D2450_a45" / "D2450_a45.pmdb"
    )
    assert resolved["mesh_directory"] == (
        campaign
        / "meshes"
        / "diamond"
        / "D2450_a45"
        / "max085_min005_cpg5_bl4_peel2"
    )
    assert cfg.geometry_suffix == ".pmdb"


def test_default_geometry_root_stays_empty():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    assert cfg.geometry_root == ""
    cfg.validate_for_meshing()


def test_relative_geometry_root_is_rejected():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    apply_json_overrides(cfg, {"geometry_root": "geometries_pmdb"})
    with pytest.raises(ValueError, match="absolute"):
        cfg.validate_for_meshing()


def test_windows_geometry_root_is_accepted():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    apply_json_overrides(
        cfg,
        {"geometry_root": "C:/ro_data_mfbo/geometries_pmdb"},
    )
    cfg.validate_for_meshing()


def test_invalid_geometry_suffix_rejected_by_validate_for_meshing():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    apply_json_overrides(cfg, {"geometry_suffix": ".step"})

    with pytest.raises(ValueError, match="geometry_suffix"):
        cfg.validate_for_meshing()
