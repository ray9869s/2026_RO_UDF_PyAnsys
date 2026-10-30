"""Guards for the campaign mesh-study driver. Does not launch Fluent."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module

driver = load_module(
    "mesh_study_case_under_test",
    SCRIPTS_DIR / "mfbo" / "mesh_study_case.py",
)
import mfbo.run_parity_mesh as parity_mesh  # noqa: E402


def _template():
    return {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "case_name": "max085_min006_cpg5_bl4_peel2",
        "m_max": 0.085,
        "m_min": 0.006,
        "m_cpg": 5,
        "bl_layers": 4,
        "peel_layers": 2,
        "bl_height_factor": 0.4,
    }


def test_refuses_production_data_root():
    samples = [
        "C:/ro_data",
        "c:/RO_DATA/meshes",
        "/mnt/c/ro_data",
        "relative/root",
    ]
    for sample in samples:
        with pytest.raises(ValueError, match="data root|production"):
            driver.resolve_data_root(sample)


def test_requires_an_override():
    with pytest.raises(ValueError, match="At least one mesh override"):
        driver.collect_overrides()


def test_mesh_id_uses_formatter_and_factor_token():
    template = _template()
    layers = driver.apply_study_overrides(template, {"bl_layers": 8})
    assert layers["mesh_id"] == "max085_min006_cpg5_bl8_peel2"
    assert layers["case_name"] == layers["mesh_id"]
    assert layers["m_max"] == template["m_max"]
    factor = driver.apply_study_overrides(
        template, {"bl_height_factor": 0.2}
    )
    assert factor["mesh_id"] == "max085_min006_cpg5_bl4_f020_peel2"
    assert driver.MESH_ID_RE.fullmatch(factor["mesh_id"])
    same_factor = driver.study_mesh_id(
        m_max=0.085,
        m_min=0.006,
        m_cpg=5,
        bl_layers=8,
        peel_layers=2,
        bl_height_factor=0.4,
        template_factor=0.4,
    )
    assert same_factor == "max085_min006_cpg5_bl8_peel2"
    with pytest.raises(ValueError, match="production mesh_id"):
        driver.apply_study_overrides(template, {"bl_layers": 4})
    split = driver.apply_study_overrides(template, {"spacer_bl_layers": 2})
    assert split["mesh_id"] == "max085_min006_cpg5_bl4s2_peel2"
    assert split["spacer_bl_layers"] == 2
    assert driver.MESH_ID_RE.fullmatch(split["mesh_id"])
    both = driver.apply_study_overrides(
        template,
        {"bl_layers": 8, "spacer_bl_layers": 4, "bl_height_factor": 0.2},
    )
    assert both["mesh_id"] == "max085_min006_cpg5_bl8s4_f020_peel2"
    with pytest.raises(ValueError, match="production mesh_id"):
        driver.apply_study_overrides(template, {"spacer_bl_layers": 4})
    bulk = driver.apply_study_overrides(
        template,
        {
            "m_max": 0.035,
            "membrane_bl_y1_um": 2.0,
            "bulk_max_size_mm": 0.034,
        },
    )
    assert bulk["mesh_id"] == "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
    assert bulk["bulk_max_size_mm"] == pytest.approx(0.034)
    assert bulk["membrane_bl_y1"]["layers"] == 5
    assert "membrane_bl_layers" not in bulk
    pinned = driver.apply_study_overrides(
        template,
        {
            "m_max": 0.035,
            "membrane_bl_y1_um": 2.0,
            "membrane_bl_layers": 8,
            "bulk_max_size_mm": 0.034,
        },
    )
    assert pinned["mesh_id"] == "max035_min006_cpg5_bl4_peel2_y1h020_mbl8_bms034"
    assert pinned["membrane_bl_y1"]["layers"] == 8
    same_count = driver.apply_study_overrides(
        template,
        {
            "m_max": 0.035,
            "membrane_bl_y1_um": 2.0,
            "membrane_bl_layers": 5,
        },
    )
    assert same_count["mesh_id"] == "max035_min006_cpg5_bl4_peel2_y1h020"
    with pytest.raises(ValueError, match="requires membrane_bl_y1_um"):
        driver.collect_overrides(membrane_bl_layers=8)


def test_override_table_marks_only_the_changed_knobs():
    template = _template()
    overrides = driver.apply_study_overrides(template, {"m_min": 0.003})
    rows = {row["key"]: row for row in driver.override_rows(template, overrides)}
    assert rows["m_min"]["status"] == "differs"
    assert rows["m_min"]["template"] == 0.006
    assert rows["m_min"]["override"] == 0.003
    assert rows["m_max"]["status"] == "same"
    assert rows["bl_layers"]["status"] == "same"
    assert rows["mesh_id"]["status"] == "differs"
    assert rows["mesh_id"]["override"] == "max085_min003_cpg5_bl4_peel2"


def test_solver_overrides_replace_only_mesh_id():
    template = {
        "geo_id": "D2450_a45",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "case_name": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    }
    updated = driver.solver_overrides_for_study(
        template, "max085_min006_cpg5_bl8_peel2"
    )
    assert updated["mesh_id"] == "max085_min006_cpg5_bl8_peel2"
    assert updated["case_name"] == "u0p2_p6M"
    assert updated["inlet_velocity_value"] == 0.2
    assert template["mesh_id"] == "max085_min006_cpg5_bl4_peel2"


def _plant_dsco(root, family, geo_id, payload):
    path = root / "geometries" / family / geo_id / f"{geo_id}.dsco"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_geometry_dsco_copy_reuse_and_mismatch(monkeypatch, tmp_path):
    prod = tmp_path / "prod"
    source = _plant_dsco(prod, "diamond", "D0817_a30", b"dsco-v1")
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", prod)
    study = tmp_path / "meshstudy"
    dest = driver.ensure_production_geometry_dsco(study, "diamond", "D0817_a30")
    assert dest == study / "geometries" / "diamond" / "D0817_a30" / "D0817_a30.dsco"
    assert dest.read_bytes() == b"dsco-v1"
    assert source.read_bytes() == b"dsco-v1"
    source_stat = source.stat()

    def refuse_copy(source_path, dest_path):
        raise AssertionError(
            f"reuse must not copy {source_path} -> {dest_path}"
        )

    monkeypatch.setattr(parity_mesh, "copy_geometry_file", refuse_copy)
    dest.chmod(0o444)
    again = driver.ensure_production_geometry_dsco(study, "diamond", "D0817_a30")
    assert again == dest
    assert dest.read_bytes() == b"dsco-v1"
    assert source.stat().st_ino == source_stat.st_ino
    assert source.read_bytes() == b"dsco-v1"

    dest.chmod(0o644)
    dest.write_bytes(b"hand-edit")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        driver.ensure_production_geometry_dsco(study, "diamond", "D0817_a30")
    assert dest.read_bytes() == b"hand-edit"
    assert source.read_bytes() == b"dsco-v1"


def test_geometry_dsco_missing_source_and_production_destination(monkeypatch, tmp_path):
    prod = tmp_path / "prod"
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", prod)
    with pytest.raises(FileNotFoundError, match=r"D0817_a30\.dsco"):
        driver.ensure_production_geometry_dsco(tmp_path / "study", "diamond", "D0817_a30")
    with pytest.raises(ValueError, match="C:/ro_data"):
        driver.ensure_production_geometry_dsco(
            Path("C:/ro_data"), "diamond", "D0817_a30"
        )


def test_meshing_child_env_sets_data_root_and_restores_parent(monkeypatch, tmp_path):
    captured = {}

    def fake_attempts(**kwargs):
        captured["env_root"] = kwargs["env"]["RO_DATA_ROOT"]
        captured["parent"] = os.environ.get("RO_DATA_ROOT")
        return SimpleNamespace(returncode=0), 1, []

    monkeypatch.setattr(driver.batch_meshing, "run_meshing_attempts", fake_attempts)
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    driver.launch_meshing({"mesh_id": "x"}, tmp_path, tmp_path, "x", 0)
    assert captured["env_root"] == str(tmp_path)
    assert captured["parent"] is None
    assert os.environ.get("RO_DATA_ROOT") is None


def test_first_height_comes_from_the_worker_log(tmp_path):
    mesh_id = "max085_min006_cpg5_bl4_f020_peel2"
    log = tmp_path / f"mesh_log_{mesh_id}.txt"
    log.write_text(
        "Boundary layer first height factor [-]: 0.2\n"
        "Boundary layer first height [mm]: 0.0012\n",
        encoding="utf-8",
    )
    assert driver.first_bl_height_mm(tmp_path, mesh_id) == pytest.approx(0.0012)


def test_main_meshes_solves_and_extracts_the_study_copy(monkeypatch, tmp_path):
    template = _template()
    monkeypatch.setattr(
        driver,
        "load_mesh_template",
        lambda geo_id: (dict(template), dict(template), 0),
    )
    solver_template = {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": template["mesh_id"],
        "case_name": "u0p2_p6M",
        "run_id": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    }
    monkeypatch.setattr(
        driver.campaign,
        "load_template",
        lambda geo_id, mesh_id, case: (
            dict(solver_template),
            dict(solver_template),
            0,
            0.0,
        ),
    )
    seen = {}

    def fake_mesh(overrides, data_root, mesh_directory, mesh_id, max_retries):
        seen["mesh_env_root"] = os.environ.get("RO_DATA_ROOT")
        seen["mesh_id"] = mesh_id
        seen["overrides"] = overrides
        seen["data_root"] = data_root
        geometry = (
            Path(data_root)
            / "geometries"
            / "diamond"
            / "D2450_a45"
            / "D2450_a45.dsco"
        )
        assert geometry.read_bytes() == b"diamond-dsco"
        assert source.read_bytes() == b"diamond-dsco"
        mesh_directory.mkdir(parents=True)
        (mesh_directory / "manifest.json").write_text(
            json.dumps(
                {
                    "cell_count": 10,
                    "skewness_max": 0.5,
                    "ortho_min": 0.2,
                    "AR_max": 30.0,
                }
            ),
            encoding="utf-8",
        )
        (mesh_directory / f"mesh_log_{mesh_id}.txt").write_text(
            "Boundary layer first height [mm]: 0.0012\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    def fake_solver(overrides, data_root, run_directory, **kwargs):
        seen["solver_mesh_id"] = overrides["mesh_id"]
        seen["solver_case_name"] = overrides["case_name"]
        seen["solver_kw_mesh_id"] = kwargs["mesh_id"]
        run_directory.mkdir(parents=True)
        (run_directory / "manifest.json").write_text(
            json.dumps(
                {"stop_reason": "residual_converged", "continuity_final": 1.0e-8}
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    def fake_extract(data_root, run_directory, case, *, geo_id, mesh_id, run_id):
        seen["extract_mesh_id"] = mesh_id
        seen["extract_root"] = data_root
        assert "ro_data" not in str(data_root).casefold()
        reports = run_directory / "post" / "reports"
        reports.mkdir(parents=True)
        (reports / "summary_metrics_wide.csv").write_text(
            "lmh_mass_balance,pressure_drop_spacer_per_m,"
            "cpc_window_avg_flux,cp_q99_window_flux,cp_q999_window_flux\n"
            "25.0,800.0,1.1,1.2,1.3\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver, "launch_meshing", fake_mesh)
    monkeypatch.setattr(driver.campaign, "launch_solver", fake_solver)
    monkeypatch.setattr(driver.mfbo_common, "launch_extract", fake_extract)
    monkeypatch.setattr(
        driver.batch_report_extract,
        "validate_summary_wide_csv",
        lambda path: (True, ""),
    )
    prod = tmp_path / "prod"
    source = _plant_dsco(prod, "diamond", "D2450_a45", b"diamond-dsco")
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    code = driver.main(
        [
            "--data-root",
            str(tmp_path),
            "--geo-id",
            "D2450_a45",
            "--bl-first-height-factor",
            "0.2",
        ]
    )
    assert code == 0
    assert seen["mesh_env_root"] is None
    assert seen["data_root"] == tmp_path.resolve()
    assert seen["mesh_id"] == "max085_min006_cpg5_bl4_f020_peel2"
    assert seen["overrides"]["bl_height_factor"] == pytest.approx(0.2)
    assert seen["solver_mesh_id"] == seen["mesh_id"]
    assert seen["solver_case_name"] == "u0p2_p6M"
    assert seen["solver_kw_mesh_id"] == seen["mesh_id"]
    assert seen["extract_mesh_id"] == seen["mesh_id"]
    assert "c:/ro_data" not in str(seen["extract_root"]).casefold()
    assert "geometry_suffix" not in seen["overrides"]
    assert "geometry_root" not in seen["overrides"]


def _matching_manifest(digest, **overrides):
    payload = {
        "max_size_mm": 0.085,
        "min_size_mm": 0.003,
        "cpg": 5,
        "bl": 4,
        "peel": 2,
        "geometry_sha256": digest,
        "cell_count": 10,
        "skewness_max": 0.5,
        "ortho_min": 0.2,
        "AR_max": 30.0,
    }
    payload.update(overrides)
    return payload


def _plant_successful_mesh(root, mesh_id, manifest):
    leaf = root / "meshes" / "diamond" / "D2450_a45" / mesh_id
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (leaf / "mesh_run_record.json").write_text(
        json.dumps({"status": "SUCCESS", "bl_height": 0.0012}),
        encoding="utf-8",
    )
    (leaf / f"mesh_log_{mesh_id}.txt").write_text(
        "Boundary layer first height [mm]: 0.0012\n",
        encoding="utf-8",
    )
    return leaf


def test_reusable_mesh_rejects_a_setting_mismatch_and_a_failed_record(tmp_path):
    geometry = tmp_path / "D2450_a45.dsco"
    geometry.write_bytes(b"diamond-dsco")
    digest = driver.sha256_file(geometry)
    leaf = tmp_path / "mesh"
    leaf.mkdir()
    manifest = _matching_manifest(digest, min_size_mm=0.006)
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (leaf / "mesh_run_record.json").write_text(
        json.dumps({"status": "SUCCESS"}),
        encoding="utf-8",
    )
    overrides = {
        "m_max": 0.085,
        "m_min": 0.006,
        "m_cpg": 5,
        "bl_layers": 4,
        "peel_layers": 2,
    }
    driver.require_reusable_mesh(leaf, overrides, geometry, digest)

    bulk_request = {**overrides, "bulk_max_size_mm": 0.034}
    with pytest.raises(ValueError, match="bulk_max_size_mm"):
        driver.require_reusable_mesh(leaf, bulk_request, geometry, digest)
    recorded_bulk = dict(manifest)
    recorded_bulk["bulk_max_size_mm"] = 0.034
    (leaf / "manifest.json").write_text(json.dumps(recorded_bulk), encoding="utf-8")
    driver.require_reusable_mesh(leaf, bulk_request, geometry, digest)
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    mismatched = dict(manifest)
    mismatched["max_size_mm"] = 0.060
    (leaf / "manifest.json").write_text(json.dumps(mismatched), encoding="utf-8")
    with pytest.raises(ValueError, match=r"m_max: recorded 0\.06, requested 0\.085"):
        driver.require_reusable_mesh(leaf, overrides, geometry, digest)

    split = dict(manifest)
    (leaf / "manifest.json").write_text(json.dumps(split), encoding="utf-8")
    with pytest.raises(ValueError, match="spacer_bl_layers: recorded 4, requested 2"):
        driver.require_reusable_mesh(
            leaf,
            {**overrides, "spacer_bl_layers": 2},
            geometry,
            digest,
        )

    wrong_hash = dict(manifest)
    wrong_hash["geometry_sha256"] = "0" * 64
    (leaf / "manifest.json").write_text(json.dumps(wrong_hash), encoding="utf-8")
    with pytest.raises(ValueError, match="geometry_sha256"):
        driver.require_reusable_mesh(leaf, overrides, geometry, digest)

    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (leaf / "mesh_run_record.json").write_text(
        json.dumps({"status": "FAILED"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not record success"):
        driver.require_reusable_mesh(leaf, overrides, geometry, digest)


def _patch_study_main(monkeypatch, tmp_path, seen):
    template = _template()
    monkeypatch.setattr(
        driver,
        "load_mesh_template",
        lambda geo_id: (dict(template), dict(template), 0),
    )
    solver_template = {
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": template["mesh_id"],
        "case_name": "u0p2_p6M",
        "run_id": "u0p2_p6M",
        "inlet_velocity_value": 0.2,
        "outlet_gauge_pressure": 6.0e6,
    }
    def load_template(geo_id, mesh_id, case):
        entry = dict(solver_template)
        entry["case_name"] = case
        entry["run_id"] = case
        return entry, dict(entry), 0, 0.0

    monkeypatch.setattr(driver.campaign, "load_template", load_template)

    def fake_mesh(overrides, data_root, mesh_directory, mesh_id, max_retries):
        seen["meshed"] = True
        seen["mesh_id"] = mesh_id
        mesh_directory.mkdir(parents=True, exist_ok=True)
        (mesh_directory / f"mesh_log_{mesh_id}.txt").write_text(
            "Boundary layer first height [mm]: 0.0012\n",
            encoding="utf-8",
        )
        (mesh_directory / "manifest.json").write_text(
            json.dumps(
                {
                    "cell_count": 10,
                    "skewness_max": 0.5,
                    "ortho_min": 0.2,
                    "AR_max": 30.0,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    def fake_solver(overrides, data_root, run_directory, **kwargs):
        seen["solved"] = True
        seen["solver_case_name"] = overrides["case_name"]
        seen["run_id"] = kwargs["run_id"]
        seen["solver_overrides"] = dict(overrides)
        run_directory.mkdir(parents=True, exist_ok=True)
        (run_directory / "manifest.json").write_text(
            json.dumps({"stop_reason": "residual_converged", "continuity_final": 1.0e-8}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    def fake_extract(data_root, run_directory, case, *, geo_id, mesh_id, run_id):
        seen["extracted"] = True
        reports = run_directory / "post" / "reports"
        reports.mkdir(parents=True)
        (reports / "summary_metrics_wide.csv").write_text(
            "lmh_mass_balance,pressure_drop_spacer_per_m,"
            "cpc_window_avg_flux,cp_q99_window_flux,cp_q999_window_flux\n"
            "25.0,800.0,1.1,1.2,1.3\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver, "launch_meshing", fake_mesh)
    monkeypatch.setattr(driver.campaign, "launch_solver", fake_solver)
    monkeypatch.setattr(driver.mfbo_common, "launch_extract", fake_extract)
    monkeypatch.setattr(
        driver.batch_report_extract,
        "validate_summary_wide_csv",
        lambda path: (True, ""),
    )
    prod = tmp_path / "prod"
    _plant_dsco(prod, "diamond", "D2450_a45", b"diamond-dsco")
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)


def test_existing_mesh_leaf_is_still_refused_without_reuse(monkeypatch, tmp_path):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    mesh_id = "max085_min003_cpg5_bl4_peel2"
    (tmp_path / "meshes" / "diamond" / "D2450_a45" / mesh_id).mkdir(parents=True)
    with pytest.raises(FileExistsError, match="mesh leaf"):
        driver.main(
            [
                "--data-root",
                str(tmp_path),
                "--geo-id",
                "D2450_a45",
                "--m-min",
                "0.003",
            ]
        )
    assert "meshed" not in seen
    assert "solved" not in seen


def test_reuse_mesh_solves_the_new_case_and_skips_meshing(monkeypatch, tmp_path):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    mesh_id = "max085_min003_cpg5_bl4_peel2"
    digest = driver.sha256_file(
        tmp_path / "prod" / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.dsco"
    )
    _plant_successful_mesh(tmp_path, mesh_id, _matching_manifest(digest))
    code = driver.main(
        [
            "--data-root",
            str(tmp_path),
            "--geo-id",
            "D2450_a45",
            "--case",
            "u0p3_p6M",
            "--m-min",
            "0.003",
            "--reuse-mesh",
        ]
    )
    assert code == 0
    assert "meshed" not in seen
    assert seen["solved"] is True
    assert seen["extracted"] is True
    assert seen["solver_case_name"] == "u0p3_p6M"
    assert seen["run_id"] == "u0p3_p6M"


def test_reuse_mesh_raises_the_setting_mismatch_and_still_refuses_the_run_leaf(
    monkeypatch, tmp_path
):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    mesh_id = "max085_min003_cpg5_bl4_peel2"
    digest = driver.sha256_file(
        tmp_path / "prod" / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.dsco"
    )
    _plant_successful_mesh(
        tmp_path,
        mesh_id,
        _matching_manifest(digest, max_size_mm=0.060),
    )
    with pytest.raises(ValueError, match="m_max"):
        driver.main(
            [
                "--data-root",
                str(tmp_path),
                "--geo-id",
                "D2450_a45",
                "--case",
                "u0p2_p6M",
                "--m-min",
                "0.003",
                "--reuse-mesh",
            ]
        )
    assert "solved" not in seen

    _plant_successful_mesh(tmp_path, mesh_id, _matching_manifest(digest))
    run = tmp_path / "runs" / "diamond" / "D2450_a45" / mesh_id / "u0p2_p6M"
    run.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="run leaf"):
        driver.main(
            [
                "--data-root",
                str(tmp_path),
                "--geo-id",
                "D2450_a45",
                "--case",
                "u0p2_p6M",
                "--m-min",
                "0.003",
                "--reuse-mesh",
            ]
        )
    assert "solved" not in seen


def _plant_converted_pmdb(root, family, geo_id, payload, *, status="passed", passed=True, digest=None):
    path = root / family / geo_id / f"{geo_id}.pmdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    file_digest = driver.sha256_file(path)
    meta = {
        "geo_id": geo_id,
        "status": "converted",
        "pmdb_sha256": file_digest if digest is None else digest,
    }
    (path.parent / f"{geo_id}_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    record = driver.parity_record_path(root, geo_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps({"geo_id": geo_id, "status": status, "passed": passed}),
        encoding="utf-8",
    )
    return path


def test_pmdb_copy_checks_meta_and_requires_a_passed_parity_record(tmp_path):
    root = tmp_path / "geometries_pmdb"
    source = _plant_converted_pmdb(root, "diamond", "D2450_a45", b"pmdb-v1")
    study = tmp_path / "study"
    dest = driver.ensure_study_geometry(
        study,
        "diamond",
        "D2450_a45",
        suffix=".pmdb",
        geometry_root=str(root),
        require_parity=True,
    )
    assert dest == study / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.pmdb"
    assert dest.read_bytes() == b"pmdb-v1"
    assert source.read_bytes() == b"pmdb-v1"
    assert driver.parity_record_path(
        "C:/ro_data_mfbo/geometries_pmdb", "D2450_a45"
    ) == Path("C:/ro_data_mfbo/geometries_pmdb_parity/D2450_a45/parity.json")

    failed = tmp_path / "failed_pmdb"
    _plant_converted_pmdb(
        failed, "diamond", "D0817_a30", b"pmdb-v1", status="failed", passed=False
    )
    with pytest.raises(RuntimeError, match="did not pass"):
        driver.ensure_study_geometry(
            study,
            "diamond",
            "D0817_a30",
            suffix=".pmdb",
            geometry_root=str(failed),
            require_parity=True,
        )
    assert not (
        study / "geometries" / "diamond" / "D0817_a30" / "D0817_a30.pmdb"
    ).exists()

    mismatch = tmp_path / "mismatch"
    _plant_converted_pmdb(
        mismatch, "diamond", "D0817_a60", b"pmdb-v1", digest="0" * 64
    )
    with pytest.raises(RuntimeError, match="sha256"):
        driver.ensure_study_geometry(
            study,
            "diamond",
            "D0817_a60",
            suffix=".pmdb",
            geometry_root=str(mismatch),
            require_parity=True,
        )
    without_volume = tmp_path / "without_volume"
    _plant_converted_pmdb(
        without_volume,
        "diamond",
        "D1225_a30",
        b"pmdb-unread",
        status="passed_without_volume",
        passed=False,
    )
    unread_dest = driver.ensure_study_geometry(
        study,
        "diamond",
        "D1225_a30",
        suffix=".pmdb",
        geometry_root=str(without_volume),
        require_parity=True,
    )
    assert unread_dest.read_bytes() == b"pmdb-unread"
    with pytest.raises(ValueError, match="C:/ro_data"):
        driver.resolve_geometry_root("C:/ro_data/geometries")
    with pytest.raises(ValueError, match="only valid"):
        driver.resolve_geometry_request(".dsco", str(root))


def test_pmdb_main_passes_the_geometry_override_to_meshing(monkeypatch, tmp_path):
    template = _template()
    monkeypatch.setattr(
        driver,
        "load_mesh_template",
        lambda geo_id: (dict(template), dict(template), 0),
    )
    seen = {}

    def fake_mesh(overrides, data_root, mesh_directory, mesh_id, max_retries):
        seen["overrides"] = overrides
        copied = (
            Path(data_root) / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.pmdb"
        )
        assert copied.read_bytes() == b"pmdb-body"
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(driver, "launch_meshing", fake_mesh)
    monkeypatch.setattr(driver, "PRODUCTION_DATA_ROOT", tmp_path / "empty-prod")
    pmdb_root = tmp_path / "geometries_pmdb"
    _plant_converted_pmdb(pmdb_root, "diamond", "D2450_a45", b"pmdb-body")
    code = driver.main(
        [
            "--data-root",
            str(tmp_path / "study"),
            "--geo-id",
            "D2450_a45",
            "--m-max",
            "0.04",
            "--geometry-suffix",
            ".pmdb",
            "--geometry-root",
            str(pmdb_root),
        ]
    )
    assert code == 7
    assert seen["overrides"]["geometry_suffix"] == ".pmdb"
    assert seen["overrides"]["geometry_root"] == pmdb_root.resolve().as_posix()
    assert not (tmp_path / "empty-prod").exists()


def _plant_checkpoint(run, iteration, *, every=None, max_iterations=None):
    from ro.solve_checkpoint import checkpoint_case_path, checkpoint_data_path

    run.mkdir(parents=True, exist_ok=True)
    case_path = checkpoint_case_path(run, "D2450_a45", "u0p2_p6M", iteration)
    case_path.write_bytes(b"cas")
    checkpoint_data_path(case_path).write_bytes(b"dat")
    payload = {}
    if every is not None:
        payload["checkpoint_every"] = every
        payload["checkpoint_iteration"] = iteration
    if max_iterations is not None:
        payload["solver_settings"] = {"max_iterations": max_iterations}
    if payload:
        (run / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return case_path


def test_checkpoint_every_is_passed_to_the_solver(monkeypatch, tmp_path):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    code = driver.main(
        [
            "--data-root",
            str(tmp_path),
            "--geo-id",
            "D2450_a45",
            "--m-min",
            "0.003",
            "--checkpoint-every",
            "200",
        ]
    )
    assert code == 0
    assert seen["solver_overrides"]["checkpoint_every"] == 200
    assert "checkpoint_resume" not in seen["solver_overrides"]


def test_resume_continues_the_latest_checkpoint_and_skips_meshing(
    monkeypatch, tmp_path
):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    mesh_id = "max085_min003_cpg5_bl4_peel2"
    digest = driver.sha256_file(
        tmp_path / "prod" / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.dsco"
    )
    _plant_successful_mesh(tmp_path, mesh_id, _matching_manifest(digest))
    run = tmp_path / "runs" / "diamond" / "D2450_a45" / mesh_id / "u0p2_p6M"
    _plant_checkpoint(run, 400)
    _plant_checkpoint(run, 600, every=200, max_iterations=2000)
    code = driver.main(
        [
            "--data-root",
            str(tmp_path),
            "--geo-id",
            "D2450_a45",
            "--case",
            "u0p2_p6M",
            "--m-min",
            "0.003",
            "--resume",
        ]
    )
    assert code == 0
    assert "meshed" not in seen
    assert seen["solved"] is True
    assert seen["solver_overrides"]["checkpoint_resume"] is True
    assert seen["solver_overrides"]["checkpoint_every"] == 200
    assert seen["solver_overrides"]["mesh_id"] == mesh_id
    assert (run / "D2450_a45_u0p2_p6M_checkpoint_000400.cas.h5").is_file()
    assert (run / "D2450_a45_u0p2_p6M_checkpoint_000600.cas.h5").is_file()


def test_resume_refuses_a_run_with_only_the_setup_case(monkeypatch, tmp_path):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    mesh_id = "max085_min003_cpg5_bl4_peel2"
    digest = driver.sha256_file(
        tmp_path / "prod" / "geometries" / "diamond" / "D2450_a45" / "D2450_a45.dsco"
    )
    _plant_successful_mesh(tmp_path, mesh_id, _matching_manifest(digest))
    run = tmp_path / "runs" / "diamond" / "D2450_a45" / mesh_id / "u0p2_p6M"
    run.mkdir(parents=True)
    (run / "D2450_a45_u0p2_p6M_setup.cas.h5").write_bytes(b"setup")
    with pytest.raises(FileNotFoundError, match="setup case is not a solution checkpoint"):
        driver.main(
            [
                "--data-root",
                str(tmp_path),
                "--geo-id",
                "D2450_a45",
                "--case",
                "u0p2_p6M",
                "--m-min",
                "0.003",
                "--resume",
            ]
        )
    assert "solved" not in seen


def test_resume_does_not_combine_with_mesh_only(monkeypatch, tmp_path):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    with pytest.raises(ValueError, match="does not combine with --mesh-only"):
        driver.main(
            [
                "--data-root",
                str(tmp_path),
                "--geo-id",
                "D2450_a45",
                "--m-min",
                "0.003",
                "--mesh-only",
                "--resume",
            ]
        )
    assert "meshed" not in seen


def _plant_run_files(run):
    from ro.solve_checkpoint import checkpoint_case_path, checkpoint_data_path

    run.mkdir(parents=True, exist_ok=True)
    for iteration in (200, 400):
        case = checkpoint_case_path(run, "D2450_a45", "u0p2_p6M", iteration)
        case.write_bytes(b"cas")
        checkpoint_data_path(case).write_bytes(b"dat")
    (run / "D2450_a45_u0p2_p6M_checkpoint_000100.dat.h5").write_bytes(b"orphan")
    (run / "D2450_a45_u0p2_p6M_setup.cas.h5").write_bytes(b"setup")
    (run / "D2450_a45_u0p2_p6M_final.cas.h5").write_bytes(b"fcas")
    (run / "D2450_a45_u0p2_p6M_final.dat.h5").write_bytes(b"fdat")
    (run / "solve.log").write_bytes(b"log")


def _plant_post_files(run):
    reports = run / "post" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "keep.txt").write_bytes(b"post")
    (reports / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5").write_bytes(b"nested")


def _set_gate(run, gate):
    path = run / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["convergence_quality"] = gate
    path.write_text(json.dumps(payload), encoding="utf-8")


def _checkpointed_run(
    monkeypatch,
    tmp_path,
    *,
    gate,
    checkpoint=True,
    keep=False,
    solver_code=0,
    extract_code=0,
    valid=True,
):
    seen = {}
    _patch_study_main(monkeypatch, tmp_path, seen)
    original_solver = driver.campaign.launch_solver
    original_extract = driver.mfbo_common.launch_extract

    def fake_solver(overrides, data_root, run_directory, **kwargs):
        result = original_solver(overrides, data_root, run_directory, **kwargs)
        _plant_run_files(run_directory)
        seen["run"] = run_directory
        if solver_code != 0:
            return SimpleNamespace(returncode=solver_code)
        return result

    def fake_extract(data_root, run_directory, case, *, geo_id, mesh_id, run_id):
        result = original_extract(
            data_root,
            run_directory,
            case,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=run_id,
        )
        _plant_post_files(run_directory)
        if gate is not None:
            _set_gate(run_directory, gate)
        if extract_code != 0:
            return SimpleNamespace(returncode=extract_code)
        return result

    monkeypatch.setattr(driver.campaign, "launch_solver", fake_solver)
    monkeypatch.setattr(driver.mfbo_common, "launch_extract", fake_extract)
    if not valid:
        monkeypatch.setattr(
            driver.batch_report_extract,
            "validate_summary_wide_csv",
            lambda path: (False, "missing column"),
        )
    argv = [
        "--data-root",
        str(tmp_path),
        "--geo-id",
        "D2450_a45",
        "--m-min",
        "0.003",
    ]
    if checkpoint:
        argv.extend(["--checkpoint-every", "200"])
    if keep:
        argv.append("--keep-checkpoints")
    code = driver.main(argv)
    return code, seen["run"]


def _assert_kept_solution(run):
    assert (run / "D2450_a45_u0p2_p6M_final.cas.h5").read_bytes() == b"fcas"
    assert (run / "D2450_a45_u0p2_p6M_final.dat.h5").read_bytes() == b"fdat"
    assert (run / "solve.log").read_bytes() == b"log"
    assert (run / "manifest.json").is_file()
    assert (run / "post" / "reports" / "keep.txt").read_bytes() == b"post"
    assert (run / "post" / "reports" / "summary_metrics_wide.csv").is_file()
    nested = run / "post" / "reports" / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5"
    assert nested.read_bytes() == b"nested"


def test_pass_extract_deletes_checkpoints_and_setup(monkeypatch, tmp_path, capsys):
    code, run = _checkpointed_run(monkeypatch, tmp_path, gate="PASS")
    assert code == 0
    assert not list(run.glob("*checkpoint*"))
    assert not (run / "D2450_a45_u0p2_p6M_setup.cas.h5").exists()
    _assert_kept_solution(run)
    out = capsys.readouterr().out
    assert "Leaf size after cleanup:" in out
    assert "Removed 6 checkpoint and setup files." in out


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"gate": "FAIL"}, "gate FAIL"),
        ({"gate": None}, "gate missing"),
        ({"gate": "PASS", "keep": True}, "--keep-checkpoints"),
        ({"gate": "PASS", "checkpoint": False}, None),
        ({"gate": "PASS", "solver_code": 1}, None),
        ({"gate": "PASS", "extract_code": 1}, None),
        ({"gate": "PASS", "valid": False}, None),
    ],
)
def test_checkpoints_stay_unless_the_extract_passed(
    monkeypatch, tmp_path, capsys, kwargs, reason
):
    code, run = _checkpointed_run(monkeypatch, tmp_path, **kwargs)
    if kwargs.get("solver_code") or kwargs.get("extract_code") or kwargs.get("valid") is False:
        assert code != 0
    else:
        assert code == 0
    assert (run / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5").is_file()
    assert (run / "D2450_a45_u0p2_p6M_checkpoint_000200.dat.h5").is_file()
    assert (run / "D2450_a45_u0p2_p6M_checkpoint_000100.dat.h5").is_file()
    assert (run / "D2450_a45_u0p2_p6M_setup.cas.h5").is_file()
    if kwargs.get("solver_code"):
        assert (run / "D2450_a45_u0p2_p6M_final.cas.h5").read_bytes() == b"fcas"
        assert (run / "solve.log").read_bytes() == b"log"
        assert not (run / "post" / "reports" / "summary_metrics_wide.csv").exists()
    else:
        _assert_kept_solution(run)
    out = capsys.readouterr().out
    if reason is None:
        assert "Leaf size after cleanup:" not in out
    else:
        assert f"Kept checkpoints and setup case ({reason})." in out
