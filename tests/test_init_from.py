"""--init-from: source-leaf validation, staging, counter check, config refusal."""

from __future__ import annotations

import hashlib
import json

import pytest

from helpers import load_run_config, populate_valid_solver_config
from ro.init_from import (
    InitFromError,
    manifest_record,
    require_counter_restarted,
    require_init_from_record,
    resolve_init_from_leaf,
    stage_init_data,
    staged_init_data_path,
)

MESH = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
GEO = "D2450_a30"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plant_source(root, *, run="u0p1_p6M", gate="PASS", stop="max_iter_reached", hashes=True,
                 mesh_sha="m" * 64):
    leaf = root / "runs" / "diamond" / GEO / MESH / run
    leaf.mkdir(parents=True)
    case = leaf / f"{GEO}_{run}_final.cas.h5"
    data = leaf / f"{GEO}_{run}_final.dat.h5"
    case.write_bytes(b"case-" + run.encode())
    data.write_bytes(b"data-" + run.encode())
    manifest = {"run_id": run, "convergence_quality": gate, "stop_reason": stop,
                "iterations_run": 2000, "mesh_sha256": mesh_sha}
    if hashes:
        manifest.update(final_case_sha256=_sha(case), final_data_sha256=_sha(data))
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return leaf


def _resolve(root, source, **kwargs):
    params = dict(data_root=root, family="diamond", geo_id=GEO, mesh_id=MESH,
                  case_id="u0p2_p6M", mesh_sha256="m" * 64)
    params.update(kwargs)
    return resolve_init_from_leaf(source, **params)


def test_valid_source_gives_a_record(tmp_path):
    leaf = plant_source(tmp_path)
    record = _resolve(tmp_path, leaf)
    assert record["leaf"] == str(leaf.resolve()) and record["run_id"] == "u0p1_p6M"
    assert record["final_data_sha256"] == _sha(leaf / f"{GEO}_u0p1_p6M_final.dat.h5")
    assert record["iterations_run"] == 2000 and record["convergence_quality"] == "PASS"
    assert require_init_from_record(record) is record
    assert set(manifest_record(record)) == set(record)


@pytest.mark.parametrize(
    "kwargs, change, match",
    [
        ({"gate": "FAIL"}, None, "not PASS"),
        ({"stop": "RUNNING"}, None, "not finished"),
        ({"hashes": False}, None, "no final hashes"),
        ({}, "tamper", "does not match its manifest hashes"),
        ({}, "broken", "BROKEN"),
        ({}, "missing", "missing or empty"),
        ({"mesh_sha": "x" * 64}, None, "different mesh file"),
    ],
)
def test_source_refusals(tmp_path, kwargs, change, match):
    leaf = plant_source(tmp_path, **kwargs)
    if change == "tamper":
        (leaf / f"{GEO}_u0p1_p6M_final.dat.h5").write_bytes(b"changed")
    elif change == "broken":
        (leaf / "BROKEN").write_text("x", encoding="utf-8")
    elif change == "missing":
        (leaf / f"{GEO}_u0p1_p6M_final.cas.h5").unlink()
    with pytest.raises(InitFromError, match=match):
        _resolve(tmp_path, leaf)


def test_source_must_be_same_mesh_and_another_run(tmp_path):
    leaf = plant_source(tmp_path)
    with pytest.raises(InitFromError, match="same geometry and mesh"):
        _resolve(tmp_path, leaf, mesh_id=MESH + "_ar250")
    with pytest.raises(InitFromError, match="same geometry and mesh"):
        _resolve(tmp_path / "other_root", leaf)
    same = plant_source(tmp_path, run="u0p2_p6M")
    with pytest.raises(InitFromError, match="names the run being solved"):
        _resolve(tmp_path, same)


def test_stage_copies_and_verifies(tmp_path):
    record = _resolve(tmp_path, plant_source(tmp_path))
    target = tmp_path / "runs" / "diamond" / GEO / MESH / "u0p2_p6M"
    staged = stage_init_data(record, target, GEO, "u0p2_p6M")
    assert staged == staged_init_data_path(target, GEO, "u0p2_p6M")
    assert staged.read_bytes() == b"data-u0p1_p6M"
    bad = dict(record, final_data_sha256="0" * 64)
    with pytest.raises(InitFromError, match="staged data hash"):
        stage_init_data(bad, target, GEO, "u0p2_p6M")
    assert not staged.exists()


def test_counter_must_restart():
    assert require_counter_restarted((1, 2000)) == 1
    with pytest.raises(InitFromError, match="did not restart"):
        require_counter_restarted((2001, 4000))
    with pytest.raises(InitFromError, match="no residual rows"):
        require_counter_restarted(None)


def test_record_shape_and_config_refusals(tmp_path):
    with pytest.raises(InitFromError, match="missing"):
        require_init_from_record({"leaf": "x"})
    record = _resolve(tmp_path, plant_source(tmp_path))
    with pytest.raises(InitFromError, match="PASS"):
        require_init_from_record(dict(record, convergence_quality="FAIL"))
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    assert cfg.init_from is None
    cfg.init_from = record
    cfg.validate_for_solver()
    cfg.checkpoint_resume = True
    with pytest.raises(ValueError, match="init_from is a fresh solve"):
        cfg.validate_for_solver()
    cfg.checkpoint_resume = False
    cfg.init_from = {"leaf": "x"}
    with pytest.raises(ValueError, match="missing"):
        cfg.validate_for_solver()


def test_run_manifest_records_init_from(monkeypatch, tmp_path):
    from test_run_wall_time import _written_run

    solver, run_cfg, mesh_directory, run_directory = _written_run(monkeypatch, tmp_path)
    from ro.manifest import read_run_manifest

    assert "init_from" not in read_run_manifest(run_directory)
    record = _resolve(tmp_path / "src", plant_source(tmp_path / "src"))
    run_cfg.init_from = record
    solver.write_worker_run_manifest(run_cfg, mesh_directory, run_directory)
    solver.finalize_worker_run_manifest(run_directory, "qoi_converged")
    recorded = read_run_manifest(run_directory)["init_from"]
    assert recorded == manifest_record(record)
    assert recorded["final_data_sha256"] == record["final_data_sha256"]
