"""Guards for the campaign re-solve driver. Does not launch Fluent."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module


def load_driver():
    return load_module(
        "solve_campaign_case_under_test",
        SCRIPTS_DIR / "mfbo" / "solve_campaign_case.py",
    )


def _write_tree(root, files):
    root = Path(root)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


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


def test_base_case_and_run_id_mapping():
    driver = load_driver()
    with pytest.raises(ValueError, match="base run id"):
        driver.require_base_case("u0p3_p6M_sst")
    assert driver.require_base_case("u0p3_p6M") == "u0p3_p6M"
    assert driver.run_id_for_viscous_model("u0p3_p6M", "laminar") == "u0p3_p6M"
    assert driver.run_id_for_viscous_model("u0p3_p6M", "k-omega-sst") == "u0p3_p6M_sst"
    assert (
        driver.run_id_for_viscous_model("u0p3_p6M", "k-epsilon-realizable-ewt")
        == "u0p3_p6M_rke"
    )

    overrides = {"run_id": "u0p3_p6M", "case_name": "u0p3_p6M", "geo_id": "P_p100_h30"}
    laminar, run_id = driver.apply_viscous_overrides(
        overrides, "u0p3_p6M", "laminar", None
    )
    assert laminar is overrides
    assert run_id == "u0p3_p6M"
    assert "viscous_model" not in laminar

    with pytest.raises(ValueError, match="only allowed"):
        driver.apply_viscous_overrides(overrides, "u0p3_p6M", "laminar", 1.0e-4)
    with pytest.raises(ValueError, match="required"):
        driver.apply_viscous_overrides(overrides, "u0p3_p6M", "k-omega-sst", None)

    sst, sst_id = driver.apply_viscous_overrides(
        overrides, "u0p3_p6M", "k-omega-sst", 1.0e-4
    )
    assert sst is not overrides
    assert sst_id == "u0p3_p6M_sst"
    assert sst["run_id"] == "u0p3_p6M_sst"
    assert sst["case_name"] == "u0p3_p6M_sst"
    assert sst["viscous_model"] == "k-omega-sst"
    assert sst["turbulence_residual_target"] == 1.0e-4
    assert overrides["run_id"] == "u0p3_p6M"


def test_template_is_the_single_production_entry():
    driver = load_driver()
    entry, overrides, _retries, _settle = driver.load_template(
        "P_p100_h30",
        "max085_min006_cpg5_bl4_peel2",
        "u0p3_p6M",
    )
    assert entry["family"] == "pillar"
    assert entry["geo_id"] == "P_p100_h30"
    assert overrides["run_id"] == "u0p3_p6M"
    assert overrides["case_name"] == "u0p3_p6M"
    assert overrides["geo_name"] == "P_p100_h30"
    assert overrides["inlet_velocity_value"] == 0.3
    assert overrides["outlet_gauge_pressure"] == 6.0e6
    with pytest.raises(RuntimeError, match="found 0"):
        driver.load_template("P_p100_h30", "max085_min006_cpg5_bl4_peel2", "u9p9_p6M")


def test_copy_keeps_the_source_and_reuse_requires_identical_sha256(tmp_path):
    driver = load_driver()
    source = _write_tree(
        tmp_path / "source",
        {
            "manifest.json": '{"mesh": 1}',
            "nested/mesh.msh.h5": "mesh-bytes",
        },
    )
    dest = tmp_path / "dest"
    copied = driver.copy_or_reuse_mesh_leaf(source, dest)
    assert (source / "nested" / "mesh.msh.h5").read_text(encoding="utf-8") == (
        "mesh-bytes"
    )
    assert (dest / "nested" / "mesh.msh.h5").read_text(encoding="utf-8") == (
        "mesh-bytes"
    )
    assert set(copied) == {"manifest.json", "nested/mesh.msh.h5"}
    assert driver.sha256_map(dest) == driver.sha256_map(source)

    reused = driver.copy_or_reuse_mesh_leaf(source, dest)
    assert reused == copied

    (dest / "manifest.json").write_text('{"mesh": 2}', encoding="utf-8")
    before = (dest / "manifest.json").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="not identical"):
        driver.copy_or_reuse_mesh_leaf(source, dest)
    assert (dest / "manifest.json").read_text(encoding="utf-8") == before
    assert (source / "manifest.json").read_text(encoding="utf-8") == '{"mesh": 1}'


def test_existing_run_leaf_is_refused(tmp_path):
    driver = load_driver()
    leaf = driver.run_leaf(tmp_path, "pillar", "P_p100_h30", "mesh", "u0p3_p6M")
    leaf.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="run leaf already exists"):
        driver.refuse_existing_run(leaf)


def test_report_does_not_default_a_missing_viscous_model(tmp_path):
    driver = load_driver()
    run = tmp_path / "run"
    reports = run / "post" / "reports"
    reports.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "stop_reason": "qoi_converged",
                "continuity_final": 1.0e-8,
                "convergence_quality": "PASS",
                "convergence_quality_failures": [],
                "solver_settings": {"residual_target": 1.0e-7},
            }
        ),
        encoding="utf-8",
    )
    (reports / "summary_metrics_wide.csv").write_text(
        "lmh_mass_balance,pressure_drop_spacer_per_m,"
        "cp_canon_window_avg,cp_canon_window_max\n"
        "1.5,2.5,3.5,4.5\n",
        encoding="utf-8",
    )
    report = driver.report_from_run(run)
    assert report["stop_reason"] == "qoi_converged"
    assert report["continuity_final"] == 1.0e-8
    assert report["convergence_quality"] == "PASS"
    assert report["failures"] == []
    assert report["lmh_mass_balance"] == "1.5"
    assert report["viscous_model"] is None
    assert driver.report_from_run(tmp_path / "missing") is None


def test_solver_env_sets_data_root_on_the_child_only(monkeypatch, tmp_path):
    driver = load_driver()
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    env = driver.solver_child_env(tmp_path, {"run_id": "u0p3_p6M"})
    assert env["RO_DATA_ROOT"] == str(tmp_path)
    assert "PYFLUENT_RUN_CONFIG" not in env
    assert "PYFLUENT_SKIP_VALIDATION" not in env
    assert json.loads(env["PYFLUENT_RUN_OVERRIDES"])["run_id"] == "u0p3_p6M"
    assert "RO_DATA_ROOT" not in os.environ
