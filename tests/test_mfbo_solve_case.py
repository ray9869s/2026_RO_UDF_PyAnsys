"""Guards for the MFBO single-case solve driver. Does not launch Fluent."""

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
        "solve_mfbo_case_under_test",
        SCRIPTS_DIR / "mfbo" / "solve_mfbo_case.py",
    )


def _registry(geo_id):
    return pillar_registry_entry(geo_id, 0.001, 0.0003, 0.0004)


def _write_meta(root, geo_id, registry):
    path = root / "geometries" / "pillar" / geo_id / f"{geo_id}_meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "inputs": {"d_p_mm": 1.0, "d_h_mm": 0.3, "d_f_mm": 0.4},
                "registry": registry,
            }
        ),
        encoding="utf-8",
    )


def _template():
    return {
        "family": "pillar",
        "geo_id": "P_p100_h30",
        "geo_name": "P_p100_h30",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "run_id": "u0p2_p6M",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
        "use_inlet_velocity_profile": True,
        "max_iterations": 2000,
    }


def _place_mesh(driver, root, geo_id):
    leaf = driver.mesh_leaf(root, geo_id, driver.DEFAULT_MESH_ID)
    leaf.mkdir(parents=True)
    (leaf / "manifest.json").write_text(
        json.dumps({"inlet_profile_G": 1.01}),
        encoding="utf-8",
    )
    (leaf / f"{geo_id}_{driver.DEFAULT_MESH_ID}.msh.h5").write_bytes(b"mesh")
    return leaf


def test_data_root_refuses_production_tree(tmp_path):
    driver = load_driver()
    for value in (
        "C:/ro_data",
        "C:/ro_data/runs",
        r"C:\ro_data\meshes",
        "c:/RO_DATA/runs/pillar",
        "/mnt/c/ro_data",
        "/mnt/c/ro_data/runs",
    ):
        with pytest.raises(ValueError, match="production data root"):
            driver.resolve_data_root(value)
    assert driver.resolve_data_root(str(tmp_path)) == tmp_path.resolve()
    assert driver.resolve_data_root("C:/ro_data_mfbo/case") == Path(
        "C:/ro_data_mfbo/case"
    )


def test_geo_id_mesh_leaf_and_existing_run(tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    with pytest.raises(ValueError, match="MFBO_PILLAR_GEO_ID_RE"):
        driver.require_mfbo_geo_id("P_p100_h30")
    with pytest.raises(ValueError, match="MFBO_PILLAR_GEO_ID_RE"):
        driver.require_mfbo_geo_id("P_p100_h3O")
    assert driver.require_mfbo_geo_id(geo_id) == geo_id

    leaf = driver.mesh_leaf(tmp_path, geo_id, driver.DEFAULT_MESH_ID)
    with pytest.raises(FileNotFoundError, match="Mesh leaf"):
        driver.require_mesh_leaf(leaf, geo_id, driver.DEFAULT_MESH_ID)
    leaf.mkdir(parents=True)
    (leaf / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"\.msh\.h5"):
        driver.require_mesh_leaf(leaf, geo_id, driver.DEFAULT_MESH_ID)
    (leaf / f"{geo_id}_{driver.DEFAULT_MESH_ID}.msh.h5").write_bytes(b"mesh")
    driver.require_mesh_leaf(leaf, geo_id, driver.DEFAULT_MESH_ID)

    run = driver.run_leaf(tmp_path, geo_id, driver.DEFAULT_MESH_ID, driver.DEFAULT_CASE)
    driver.refuse_existing_run(run)
    run.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="run leaf already exists"):
        driver.refuse_existing_run(run)


def test_override_delta_allows_only_geometry_identity_keys():
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
    assert overrides["inlet_velocity_value"] == 0.2
    assert overrides["case_name"] == "u0p2_p6M"
    assert "geometry_suffix" not in overrides

    rows = driver.assert_override_delta(template, overrides)
    differed = {row["key"] for row in rows if row["status"] == "differs"}
    assert differed <= driver.ALLOWED_OVERRIDE_DELTA
    assert "inlet_velocity_value" not in differed

    tampered = dict(overrides)
    tampered["max_iterations"] = 100
    with pytest.raises(ValueError, match="max_iterations"):
        driver.assert_override_delta(template, tampered)


def test_production_template_delta_is_only_geometry_identity_keys():
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.0, 0.4)
    registry = pillar_registry_entry(geo_id, 0.0008, 0.0, 0.0004)
    case, template, _retries, _settle = driver.load_template(
        driver.DEFAULT_MESH_ID,
        driver.DEFAULT_CASE,
    )
    assert case["geo_id"] == "P_p100_h30"
    assert case["run_id"] == "u0p2_p6M"
    assert template["geo_name"] == "P_p100_h30"
    assert template["case_name"] == "u0p2_p6M"
    assert "base_case_name" not in template
    overrides = driver.apply_mfbo_overrides(template, geo_id, registry)
    driver.assert_override_delta(template, overrides)
    assert overrides["wall_spacer_labels"] == list(registry["spacer_wall_zones"])
    with pytest.raises(RuntimeError, match="exactly one"):
        driver.load_template(driver.DEFAULT_MESH_ID, "u9p9_p9M")


def test_resolve_registry_restores_parent_env(monkeypatch, tmp_path):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    _write_meta(tmp_path, geo_id, _registry(geo_id))
    monkeypatch.setenv("RO_DATA_ROOT", "C:/keep-me")
    resolved = driver.resolve_registry(tmp_path, geo_id)
    assert resolved["spacing_code"] == geo_id
    assert os.environ["RO_DATA_ROOT"] == "C:/keep-me"


def test_launch_puts_data_root_on_the_child_only(monkeypatch, tmp_path):
    driver = load_driver()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setenv("PYFLUENT_RUN_CONFIG", "leftover")
    captured = {}

    def fake_attempts(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=4), 1, [], []

    monkeypatch.setattr(driver.batch_solver_sweep, "run_solver_attempts", fake_attempts)
    monkeypatch.setattr(
        driver.batch_solver_sweep,
        "write_solver_retry_record",
        lambda *args, **kwargs: None,
    )
    result = driver.launch_solver(
        {"case_name": "u0p2_p6M"},
        tmp_path,
        tmp_path / "run",
        geo_id="MFP_d1000_h0300_f0400",
        mesh_id=driver.DEFAULT_MESH_ID,
        run_id=driver.DEFAULT_CASE,
        max_retries=0,
        settle_s=0.0,
    )
    assert result.returncode == 4
    assert captured["geo_id"] == "MFP_d1000_h0300_f0400"
    assert captured["env"]["RO_DATA_ROOT"] == str(tmp_path)
    assert "PYFLUENT_RUN_CONFIG" not in captured["env"]
    assert "RO_DATA_ROOT" not in os.environ


def test_main_returns_worker_code_and_prints_results(monkeypatch, tmp_path, capsys):
    driver = load_driver()
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    registry = _registry(geo_id)
    _write_meta(tmp_path, geo_id, registry)
    _place_mesh(driver, tmp_path, geo_id)
    template = _template()
    case = {
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    }
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setattr(
        driver,
        "load_template",
        lambda mesh_id, run_id: (case, template, 0, 0.0),
    )

    def fake_solver(overrides, data_root, run_directory, **kwargs):
        assert overrides["geo_id"] == geo_id
        assert os.environ.get("RO_DATA_ROOT") is None
        run_directory.mkdir(parents=True)
        (run_directory / "manifest.json").write_text(
            json.dumps({"u_mean_ms": 0.198, "stop_reason": "qoi_met"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    extracted = {}

    def fake_extract(data_root, run_directory, case_dict, *, geo_id, mesh_id, run_id):
        extracted["geo_id"] = geo_id
        extracted["mesh_id"] = mesh_id
        extracted["run_id"] = run_id
        summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
        summary.parent.mkdir(parents=True)
        header = ",".join(driver.SUMMARY_PRINT_FIELDS)
        values = ",".join(["1.5", "1000", "1.1", "1.4", "1.2", "1.3", "1.4", "1.5"])
        summary.write_text(header + "\n" + values + "\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver, "launch_solver", fake_solver)
    monkeypatch.setattr(driver.mfbo_common, "launch_extract", fake_extract)
    monkeypatch.setattr(
        driver.batch_report_extract,
        "validate_summary_wide_csv",
        lambda path: (True, "OK"),
    )
    code = driver.main(["--data-root", str(tmp_path), "--geo-id", geo_id])
    assert code == 0
    assert extracted == {
        "geo_id": geo_id,
        "mesh_id": driver.DEFAULT_MESH_ID,
        "run_id": driver.DEFAULT_CASE,
    }
    printed = capsys.readouterr().out
    assert "lmh_mass_balance: 1.5" in printed
    assert "pp_cp_canon_max_cell_8: 1.5" in printed
    assert "inlet_profile_G: 1.01" in printed
    assert "stop_reason: qoi_met" in printed
    assert "RO_DATA_ROOT" not in os.environ

    with pytest.raises(FileExistsError, match="run leaf already exists"):
        driver.main(["--data-root", str(tmp_path), "--geo-id", geo_id])
