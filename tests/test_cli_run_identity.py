"""CLI selectors are the four ids; missing runs fail loudly."""

from __future__ import annotations

import pytest

from helpers import load_batch_postprocess, load_case_inventory, load_module
from helpers import POST_DIR, SCRIPTS_DIR
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, write_test_run


@pytest.fixture
def inventory():
    return load_case_inventory()


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


@pytest.fixture
def residual_report():
    return load_module(
        "residual_cli_under_test",
        POST_DIR / "residual_measurement_report.py",
    )


@pytest.fixture
def rerun07():
    return load_module(
        "batch_solver_rerun_cli_under_test",
        SCRIPTS_DIR / "batch_solver_rerun.py",
    )


def test_inventory_cli_exposes_id_selectors_not_geo_case(inventory, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = inventory.parse_args([])
    assert args.family is None
    assert args.geo_id is None
    assert args.mesh_id is None
    assert args.run_id is None
    assert not hasattr(args, "geo_name")
    assert not hasattr(args, "case_name")


def test_post_cli_exposes_id_selectors_not_geo_case(batch_post, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = batch_post.parse_args([])
    assert args.family is None
    assert args.geo_id is None
    assert not hasattr(args, "geo_name")


def test_residual_cli_exposes_id_selectors_not_geo_case(residual_report, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = residual_report.parse_args([])
    assert args.run_id is None
    assert not hasattr(args, "case_name")


def test_07_cli_appends_id_selectors(rerun07, monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    args = rerun07.parse_args(
        ["--family", "diamond", "--geo-id", "D2450_a45", "--run-id", "u0p2_p6M"]
    )
    assert args.family == ["diamond"]
    assert args.geo_id == ["D2450_a45"]
    assert args.mesh_id == []
    assert args.run_id == ["u0p2_p6M"]
    assert not hasattr(args, "geo_name")


def test_inventory_complete_identity_uses_run_dir(inventory, monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = write_test_run(stop_reason="max_iter_reached")
    discovered = inventory.discover_cases(
        tmp_path / "runs",
        FAMILY,
        GEO_ID,
        MESH_ID,
        RUN_ID,
        include_hidden=False,
        verbose=False,
    )
    assert len(discovered) == 1
    assert discovered[0]["_case_dir_path"] == directory


def test_inventory_complete_identity_missing_run_is_loud(
    inventory, monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="No run manifest"):
        inventory.discover_cases(
            tmp_path / "runs",
            FAMILY,
            GEO_ID,
            MESH_ID,
            RUN_ID,
            include_hidden=False,
            verbose=False,
        )


def test_inventory_partial_filter_with_no_match_is_loud(
    inventory, monkeypatch, tmp_path
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    write_test_run(stop_reason="max_iter_reached")
    with pytest.raises(FileNotFoundError, match="No run matched"):
        inventory.discover_cases(
            tmp_path / "runs",
            None,
            None,
            None,
            "u0p3_p6M",
            include_hidden=False,
            verbose=False,
        )
