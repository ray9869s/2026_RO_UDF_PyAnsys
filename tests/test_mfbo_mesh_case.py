"""Guards for the MFBO single-case meshing driver. Does not launch Fluent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.geometry_registry import format_mfbo_pillar_geo_id, pillar_registry_entry


def load_driver():
    return load_module(
        "mesh_mfbo_case_under_test",
        SCRIPTS_DIR / "mfbo" / "mesh_mfbo_case.py",
    )


def _registry(geo_id):
    return pillar_registry_entry(geo_id, 0.001, 0.0003, 0.0004)


def _write_meta(root, geo_id, registry, *, d_p_mm=1.0, d_h_mm=0.3, d_f_mm=0.4):
    path = root / "geometries" / "pillar" / geo_id / f"{geo_id}_meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "inputs": {
                    "d_p_mm": d_p_mm,
                    "d_h_mm": d_h_mm,
                    "d_f_mm": d_f_mm,
                },
                "registry": registry,
                "pmdb_sha256": "abc",
            }
        ),
        encoding="utf-8",
    )
    return path


def _template():
    return {
        "family": "pillar",
        "geo_id": "P_p100_h30",
        "geo_name": "P_p100_h30",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "spacing_code": "P_p100_h30",
        "filament_d_m": 4.0e-4,
        "bridge_radius_m": 1.1e-4,
        "wall_spacer_labels": ["wall_spacer"],
        "m_max": 0.085,
    }


def test_data_root_refuses_production_tree(tmp_path):
    driver = load_driver()
    for value in (
        "C:/ro_data",
        "C:/ro_data/meshes",
        r"C:\ro_data\geometries",
        "c:/RO_DATA/meshes/pillar",
        "/mnt/c/ro_data",
        "/mnt/c/ro_data/meshes",
    ):
        with pytest.raises(ValueError, match="production data root"):
            driver.resolve_data_root(value)
    assert driver.resolve_data_root(str(tmp_path)) == tmp_path.resolve()
    assert driver.resolve_data_root("C:/ro_data_mfbo/case") == Path("C:/ro_data_mfbo/case")


def test_geo_id_and_geometry_files_are_required(tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    with pytest.raises(ValueError, match="MFBO_PILLAR_GEO_ID_RE"):
        driver.require_mfbo_geo_id("P_p100_h30")
    with pytest.raises(ValueError, match="MFBO_PILLAR_GEO_ID_RE"):
        driver.require_mfbo_geo_id("MFP_d100_h0300_f0400")
    assert driver.require_mfbo_geo_id(geo_id) == geo_id

    with pytest.raises(FileNotFoundError, match=r"\.pmdb"):
        driver.require_geometry(tmp_path, geo_id)
    pmdb, meta = driver.geometry_paths(tmp_path, geo_id)
    pmdb.parent.mkdir(parents=True)
    pmdb.write_bytes(b"pmdb")
    with pytest.raises(FileNotFoundError, match="_meta.json"):
        driver.require_geometry(tmp_path, geo_id)
    meta.write_text("{}", encoding="utf-8")
    assert driver.require_geometry(tmp_path, geo_id) == (pmdb, meta)


def test_existing_mesh_leaf_is_refused(tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.0, 0.4)
    leaf = driver.mesh_leaf(tmp_path, geo_id, driver.DEFAULT_MESH_ID)
    driver.refuse_existing_mesh(leaf)
    leaf.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="already exists"):
        driver.refuse_existing_mesh(leaf)


def test_override_delta_allows_only_mfp_keys():
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    registry = _registry(geo_id)
    template = _template()
    overrides = driver.apply_mfbo_overrides(template, geo_id, registry)

    assert template["geo_id"] == "P_p100_h30"
    assert overrides["geo_id"] == geo_id
    assert overrides["geo_name"] == geo_id
    assert overrides["spacing_code"] == registry["spacing_code"]
    assert overrides["filament_d_m"] == registry["filament_d_m"]
    assert overrides["bridge_radius_m"] == registry["bridge_radius_m"]
    assert overrides["wall_spacer_labels"] == list(registry["spacer_wall_zones"])
    assert overrides["geometry_suffix"] == ".pmdb"
    assert overrides["m_max"] == template["m_max"]
    assert overrides["mesh_id"] == template["mesh_id"]

    rows = driver.assert_override_delta(template, overrides)
    differed = {row["key"] for row in rows if row["status"] == "differs"}
    assert differed <= driver.ALLOWED_OVERRIDE_DELTA
    assert "m_max" not in differed

    tampered = dict(overrides)
    tampered["m_max"] = 0.060
    with pytest.raises(ValueError, match="m_max"):
        driver.assert_override_delta(template, tampered)


def test_production_template_delta_is_only_mfp_keys():
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.5)
    registry = pillar_registry_entry(geo_id, 0.0008, 0.00015, 0.0005)
    _case, template, _retries = driver.load_template(driver.DEFAULT_MESH_ID)
    assert template["geo_id"] == "P_p100_h30"
    assert template["mesh_id"] == driver.DEFAULT_MESH_ID
    overrides = driver.apply_mfbo_overrides(template, geo_id, registry)
    driver.assert_override_delta(template, overrides)
    with pytest.raises(RuntimeError, match="exactly one"):
        driver.load_template("max060_min006_cpg5_bl4_peel2")


def test_resolve_registry_uses_data_root_then_restores(monkeypatch, tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    registry = _registry(geo_id)
    _write_meta(tmp_path, geo_id, registry)
    monkeypatch.setenv("RO_DATA_ROOT", "C:/keep-me")
    resolved = driver.resolve_registry(tmp_path, geo_id)
    assert resolved["spacing_code"] == geo_id
    assert resolved["spacer_wall_zones"] == registry["spacer_wall_zones"]
    assert os.environ["RO_DATA_ROOT"] == "C:/keep-me"

    monkeypatch.delenv("RO_DATA_ROOT")
    driver.resolve_registry(tmp_path, geo_id)
    assert "RO_DATA_ROOT" not in os.environ


def test_main_returns_worker_code_without_leaving_data_root(monkeypatch, tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    registry = _registry(geo_id)
    _write_meta(tmp_path, geo_id, registry)
    pmdb, _meta = driver.geometry_paths(tmp_path, geo_id)
    pmdb.write_bytes(b"pmdb")
    template = _template()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setattr(
        driver,
        "load_template",
        lambda mesh_id: ({}, template, 0),
    )
    captured = {}

    def fake_launch(overrides, data_root, mesh_directory, mesh_id, max_retries):
        captured["env_during"] = os.environ.get("RO_DATA_ROOT")
        captured["overrides"] = overrides
        captured["data_root"] = data_root
        manifest = Path(mesh_directory) / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "cell_count": 10,
                    "skewness_max": 0.2,
                    "ortho_min": 0.3,
                    "AR_max": 4.0,
                    "porosity_eps": 0.9,
                    "membrane_blocked_area_frac_geometric": 0.1,
                    "spacer_wall_zones": overrides["wall_spacer_labels"],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver, "launch_worker", fake_launch)
    code = driver.main(
        ["--data-root", str(tmp_path), "--geo-id", geo_id]
    )
    assert code == 0
    assert captured["env_during"] is None
    assert "RO_DATA_ROOT" not in os.environ
    assert captured["overrides"]["geometry_suffix"] == ".pmdb"
    assert captured["overrides"]["geo_id"] == geo_id

    leaf = driver.mesh_leaf(tmp_path, geo_id, driver.DEFAULT_MESH_ID)
    with pytest.raises(FileExistsError, match="already exists"):
        driver.main(["--data-root", str(tmp_path), "--geo-id", geo_id])
    assert leaf.exists()


def test_launch_puts_data_root_on_the_child_only(monkeypatch, tmp_path):
    driver = load_driver()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    captured = {}

    def fake_attempts(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=7), 1, []

    monkeypatch.setattr(driver.batch_meshing, "run_meshing_attempts", fake_attempts)
    result = driver.launch_worker(
        {"geo_id": "MFP_d1000_h0300_f0400"},
        tmp_path,
        tmp_path / "leaf",
        driver.DEFAULT_MESH_ID,
        0,
    )
    assert result.returncode == 7
    assert captured["env"]["RO_DATA_ROOT"] == str(tmp_path)
    assert "PYFLUENT_RUN_CONFIG" not in captured["env"]
    assert "RO_DATA_ROOT" not in os.environ
