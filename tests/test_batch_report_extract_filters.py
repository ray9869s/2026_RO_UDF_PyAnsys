"""Extract case-set filters match the solver sweep convention."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import CONFIGS_DIR, load_batch_report_extract, load_module
from ro.campaign_matrix import PRODUCTION_MESH_ID_D0817_A60, PRODUCTION_MESH_ID_DEFAULT


def _load_batch_config():
    return load_module(
        "batch_config_extract_filters",
        CONFIGS_DIR / "batch_config.py",
    )


@pytest.fixture
def batch_extract():
    return load_batch_report_extract()


@pytest.fixture
def solver_batch_cfg():
    return _load_batch_config()


def test_extract_cli_defaults_keep_manifest_walk(batch_extract):
    args = batch_extract.parse_batch_report_extract_cli([])
    assert args.case_set is None
    assert args.geo_ids is None
    assert args.outlet_gauge_pressures is None
    assert args.dry_run is False
    assert args.report_skip is False


def test_production_p6m_selects_93_and_ignores_post_cases(
    batch_extract, solver_batch_cfg
):
    post_cfg = SimpleNamespace(
        post_cases=[
            {
                "family": "diamond",
                "geo_id": "D2450_a45",
                "mesh_id": "max085_min006_cpg5_bl4_peel2",
                "run_id": "u0p2_p8M",
                "inlet_velocity_value": 0.2,
                "outlet_gauge_pressure": 8.0e6,
            }
        ]
    )
    args = batch_extract.parse_batch_report_extract_cli(
        [
            "--case-set",
            "production",
            "--outlet-gauge-pressure",
            "6.0e6",
            "--dry-run",
        ]
    )
    assert args.dry_run is True
    cases, source = batch_extract.select_extract_cases(
        post_cfg, args, solver_batch_cfg=solver_batch_cfg
    )
    assert source == "production solver matrix"
    assert len(cases) == 93
    assert {case["outlet_gauge_pressure"] for case in cases} == {6.0e6}
    assert {case["run_id"] for case in cases} == {
        "u0p1_p6M",
        "u0p2_p6M",
        "u0p3_p6M",
    }
    assert len({case["geo_id"] for case in cases}) == 31
    for case in cases:
        if case["geo_id"] == "D0817_a60":
            assert case["mesh_id"] == PRODUCTION_MESH_ID_D0817_A60
        else:
            assert case["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT


def test_omitted_case_set_uses_post_cases(batch_extract):
    post_cfg = SimpleNamespace(
        post_cases=[
            {
                "family": "diamond",
                "geo_id": "D2450_a45",
                "mesh_id": "max085_min006_cpg5_bl4_peel2",
                "run_id": "u0p2_p6M",
                "inlet_velocity_value": 0.2,
                "outlet_gauge_pressure": 6.0e6,
            }
        ]
    )
    args = batch_extract.parse_batch_report_extract_cli([])
    cases, source = batch_extract.select_extract_cases(post_cfg, args)
    assert source == "explicit post_cases"
    assert [case["run_id"] for case in cases] == ["u0p2_p6M"]


def test_extract_unknown_pressure_is_error_not_empty(
    batch_extract, solver_batch_cfg
):
    args = batch_extract.parse_batch_report_extract_cli(
        ["--case-set", "production", "--outlet-gauge-pressure", "5.0e6"]
    )
    with pytest.raises(ValueError, match="not in the selected case-set"):
        batch_extract.select_extract_cases(
            SimpleNamespace(post_cases=[]),
            args,
            solver_batch_cfg=solver_batch_cfg,
        )


def test_extract_unknown_geo_id_is_error_not_empty(
    batch_extract, solver_batch_cfg
):
    args = batch_extract.parse_batch_report_extract_cli(
        ["--case-set", "production", "--geo-id", "not_a_geo"]
    )
    with pytest.raises(ValueError, match="not in the selected case-set"):
        batch_extract.select_extract_cases(
            SimpleNamespace(post_cases=[]),
            args,
            solver_batch_cfg=solver_batch_cfg,
        )


def test_missing_finals_are_skip_only_on_matrix_selection(batch_extract):
    assert batch_extract.missing_finals_status(matrix_selected=False) == (
        "MISSING_CASE_DATA"
    )
    assert batch_extract.missing_finals_status(matrix_selected=True) == (
        batch_extract.EXTRACT_SKIPPED_MISSING_FINALS_STATUS
    )
    assert (
        batch_extract.EXTRACT_SKIPPED_MISSING_FINALS_STATUS
        not in batch_extract.EXTRACT_BATCH_FAILED_STATUSES
    )
    assert "MISSING_CASE_DATA" in batch_extract.EXTRACT_BATCH_FAILED_STATUSES
