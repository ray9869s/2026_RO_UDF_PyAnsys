"""Guards for scripts/mfbo/reextract_runs.py. Does not launch Fluent."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module

reextract_runs = load_module(
    "reextract_runs_under_test",
    SCRIPTS_DIR / "mfbo" / "reextract_runs.py",
)
import mfbo.mesh_study_case as mesh_study  # noqa: E402


def _leaf(root):
    return (
        root
        / "runs"
        / "pillar"
        / "P_p100_h30"
        / "max085_min006_cpg5_bl4_peel2"
        / "u0p2_p6M_rke"
    )


def test_refuses_production_roots():
    samples = [
        "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
        "c:/RO_DATA/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
        "/mnt/c/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
    ]
    for sample in samples:
        with pytest.raises(ValueError, match="production"):
            reextract_runs.parse_run_leaf(sample)


def test_archive_renames_and_keeps_previous_reports(tmp_path):
    leaf = _leaf(tmp_path)
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text("lmh_mass_balance\n24.5\n")
    moment = datetime(2026, 10, 1, 11, 2, 3, tzinfo=timezone.utc)
    dest = reextract_runs.archive_reports(leaf, now=moment)
    assert dest == leaf / "post" / "reports_prev_20261001T110203Z"
    assert dest.is_dir()
    assert (dest / "summary_metrics_wide.csv").read_text().startswith("lmh")
    assert not reports.exists()


def test_reextract_uses_leaf_data_root_and_does_not_keep_parent_env(
    monkeypatch, tmp_path
):
    leaf = _leaf(tmp_path)
    (leaf / "post" / "reports").mkdir(parents=True)
    (leaf / "post" / "reports" / "summary_metrics_wide.csv").write_text("x\n")
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    seen = {}

    def fake_launch(data_root, run_directory, case, *, geo_id, mesh_id, run_id):
        seen["data_root"] = data_root
        seen["run_directory"] = run_directory
        seen["case"] = case
        seen["geo_id"] = geo_id
        seen["mesh_id"] = mesh_id
        seen["run_id"] = run_id
        seen["parent_env"] = os.environ.get("RO_DATA_ROOT")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reextract_runs.mfbo_common, "launch_extract", fake_launch)
    monkeypatch.setattr(
        reextract_runs,
        "operating_point_case",
        lambda data_root, run_leaf: {
            "inlet_velocity_value": 0.2,
            "outlet_gauge_pressure": 6.0e6,
        },
    )
    assert reextract_runs.main([str(leaf)]) == 0
    assert seen["data_root"] == tmp_path.resolve()
    assert seen["run_directory"] == leaf.resolve()
    assert seen["geo_id"] == "P_p100_h30"
    assert seen["mesh_id"] == "max085_min006_cpg5_bl4_peel2"
    assert seen["run_id"] == "u0p2_p6M_rke"
    assert seen["case"]["inlet_velocity_value"] == 0.2
    assert seen["parent_env"] is None
    assert os.environ.get("RO_DATA_ROOT") is None
    assert not (leaf / "post" / "reports").exists()
    archives = list((leaf / "post").glob("reports_prev_*"))
    assert len(archives) == 1
    assert (archives[0] / "summary_metrics_wide.csv").is_file()


def _production_identity(tmp_path):
    source_run = tmp_path / "src_run"
    source_mesh = tmp_path / "src_mesh"
    source_run.mkdir()
    source_mesh.mkdir()
    (source_run / "P_p100_h30_u0p2_p6M_final.cas.h5").write_bytes(b"case")
    (source_run / "P_p100_h30_u0p2_p6M_final.dat.h5").write_bytes(b"data")
    (source_mesh / "mesh.msh").write_bytes(b"mesh")
    return {
        "run_leaf": source_run,
        "mesh_leaf": source_mesh,
        "family": "pillar",
        "geo_id": "P_p100_h30",
        "mesh_id": "mesh",
        "run_id": "u0p2_p6M",
    }


def _plant_geometry(root, payload=b"dsco-v1"):
    path = (
        root
        / "geometries"
        / "pillar"
        / "P_p100_h30"
        / "P_p100_h30.dsco"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return path


def test_copy_from_production_reextracts_the_copy(monkeypatch, tmp_path):
    import mfbo.diagnose_cp_max_hotspots as hotspots

    identity = _production_identity(tmp_path)
    prod = tmp_path / "prod"
    source = _plant_geometry(prod)
    monkeypatch.setattr(mesh_study, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.setattr(hotspots, "parse_production_run_leaf", lambda value: identity)
    seen = []

    def fake_reextract(leaf):
        seen.append(leaf)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reextract_runs, "reextract_leaf", fake_reextract)
    data = tmp_path / "root"
    code = reextract_runs.main(
        [
            "--copy-from-production",
            "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
            "--data-root",
            str(data),
        ]
    )
    dest = data / "runs" / "pillar" / "P_p100_h30" / "mesh" / "u0p2_p6M"
    assert code == 0
    assert seen == [dest]
    assert "ro_data" not in str(seen[0]).casefold()
    mesh_copy = data / "meshes" / "pillar" / "P_p100_h30" / "mesh" / "mesh.msh"
    assert mesh_copy.read_bytes() == b"mesh"
    assert (dest / "P_p100_h30_u0p2_p6M_final.cas.h5").read_bytes() == b"case"
    geometry = data / "geometries" / "pillar" / "P_p100_h30" / "P_p100_h30.dsco"
    assert geometry.read_bytes() == b"dsco-v1"
    assert source.read_bytes() == b"dsco-v1"


def test_copy_reuses_identical_tree_and_refuses_a_different_one(monkeypatch, tmp_path):
    import mfbo.diagnose_cp_max_hotspots as hotspots

    identity = _production_identity(tmp_path)
    prod = tmp_path / "prod"
    _plant_geometry(prod)
    monkeypatch.setattr(mesh_study, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.setattr(hotspots, "parse_production_run_leaf", lambda value: identity)
    data = tmp_path / "root"
    prod_leaf = "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M"
    first = reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    mesh_copy = data / "meshes" / "pillar" / "P_p100_h30" / "mesh" / "mesh.msh"
    mesh_copy.chmod(0o444)
    second = reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    assert second == first
    mesh_copy.chmod(0o644)
    mesh_copy.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="not identical"):
        reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    assert mesh_copy.read_bytes() == b"changed"


def test_copy_from_production_geometry_reuse_and_mismatch(monkeypatch, tmp_path):
    import mfbo.diagnose_cp_max_hotspots as hotspots

    identity = _production_identity(tmp_path)
    prod = tmp_path / "prod"
    source = _plant_geometry(prod, b"dsco-v1")
    monkeypatch.setattr(mesh_study, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.setattr(hotspots, "parse_production_run_leaf", lambda value: identity)
    data = tmp_path / "root"
    prod_leaf = "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M"
    reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    geometry = data / "geometries" / "pillar" / "P_p100_h30" / "P_p100_h30.dsco"
    geometry.chmod(0o444)
    again = reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    assert again.name == "u0p2_p6M"
    assert geometry.read_bytes() == b"dsco-v1"
    assert source.read_bytes() == b"dsco-v1"
    geometry.chmod(0o644)
    geometry.write_bytes(b"other-dsco")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        reextract_runs.copy_production_for_reextract(prod_leaf, str(data))
    assert geometry.read_bytes() == b"other-dsco"
    assert source.read_bytes() == b"dsco-v1"


def test_copy_from_production_does_not_launch_on_production_root(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(
        reextract_runs.mfbo_common,
        "launch_extract",
        lambda *args, **kwargs: launched.append(args) or SimpleNamespace(returncode=0),
    )
    with pytest.raises(ValueError, match="production"):
        reextract_runs.main(
            [
                "--copy-from-production",
                "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
                "--data-root",
                "C:/ro_data",
            ]
        )
    assert launched == []


def test_copy_from_production_can_take_the_converted_pmdb(monkeypatch, tmp_path):
    import json

    import mfbo.diagnose_cp_max_hotspots as hotspots

    identity = _production_identity(tmp_path)
    prod = tmp_path / "prod"
    _plant_geometry(prod, b"dsco-v1")
    monkeypatch.setattr(mesh_study, "PRODUCTION_DATA_ROOT", prod)
    monkeypatch.setattr(hotspots, "parse_production_run_leaf", lambda value: identity)
    pmdb_root = tmp_path / "geometries_pmdb"
    source = pmdb_root / "pillar" / "P_p100_h30" / "P_p100_h30.pmdb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pmdb-v1")
    meta = {
        "geo_id": "P_p100_h30",
        "status": "converted",
        "pmdb_sha256": mesh_study.sha256_file(source),
    }
    (source.parent / "P_p100_h30_meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    data = tmp_path / "root"
    reextract_runs.copy_production_for_reextract(
        "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
        str(data),
        geometry_suffix=".pmdb",
        geometry_root=str(pmdb_root),
    )
    copied = data / "geometries" / "pillar" / "P_p100_h30" / "P_p100_h30.pmdb"
    assert copied.read_bytes() == b"pmdb-v1"
    assert source.read_bytes() == b"pmdb-v1"
    assert not (
        data / "geometries" / "pillar" / "P_p100_h30" / "P_p100_h30.dsco"
    ).exists()
