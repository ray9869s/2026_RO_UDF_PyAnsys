"""Tests for canonical mesh-case provenance and legacy opt-out."""

from __future__ import annotations

import pytest

from ro.mesh_common import (
    assert_mesh_case_name_matches,
    make_canonical_mesh_case_name,
    mesh_case_name_provenance,
)
from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    populate_valid_meshing_config,
)


@pytest.mark.parametrize(
    ("m_max", "m_min", "m_cpg", "bl_layers", "expected"),
    [
        (
            0.085,
            0.005,
            5,
            4,
            "mesh_max085_min005_cpg5_bl4",
        ),
        (
            0.100,
            0.006,
            3,
            3,
            "mesh_max100_min006_cpg3_bl3",
        ),
    ],
)
def test_canonical_mesh_case_name(
    m_max,
    m_min,
    m_cpg,
    bl_layers,
    expected,
):
    assert make_canonical_mesh_case_name(
        m_max,
        m_min,
        m_cpg,
        bl_layers,
    ) == expected


def test_mismatched_encoded_minimum_size_raises():
    with pytest.raises(AssertionError, match="does not match"):
        assert_mesh_case_name_matches(
            "mesh_max085_min006_cpg5_bl4",
            0.085,
            0.005,
            5,
            4,
        )


def test_legacy_opt_out_returns_canonical_name_without_raising():
    canonical = assert_mesh_case_name_matches(
        "legacy_hand_typed_mesh",
        0.085,
        0.005,
        5,
        4,
        allow_legacy=True,
    )
    assert canonical == "mesh_max085_min005_cpg5_bl4"


def test_non_integral_name_tokens_are_rejected():
    with pytest.raises(ValueError, match="cannot be represented"):
        make_canonical_mesh_case_name(0.0855, 0.005, 5, 4)


def test_run_config_enforces_names_by_default():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    assert cfg.allow_legacy_mesh_case_name_mismatch is False
    cfg.validate_for_meshing()

    cfg.case_name = "mesh_max085_min006_cpg5_bl4"
    with pytest.raises(AssertionError, match="canonical"):
        cfg.validate_for_meshing()

    cfg.allow_legacy_mesh_case_name_mismatch = True
    cfg.validate_for_meshing()


def test_legacy_opt_out_flag_must_be_bool():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.allow_legacy_mesh_case_name_mismatch = "yes"
    with pytest.raises(TypeError, match="must be bool"):
        cfg.validate_for_meshing()


def test_current_mesh_batch_names_match_parameters():
    from helpers import load_solver_common

    merge_batch_case_overrides = load_solver_common().merge_batch_case_overrides
    batch = load_module(
        "mesh_batch_name_validation",
        SCRIPTS_DIR / "batch_config_before_sin_3mesh_20260716_231030.py",
    )
    for case in batch.mesh_batch_cases:
        merged = merge_batch_case_overrides(batch.common_mesh_settings, case)
        assert_mesh_case_name_matches(
            merged["mesh_case_name"],
            merged["m_max"],
            merged["m_min"],
            merged["m_cpg"],
            merged["bl_layers"],
        )


def test_ledger_provenance_marks_legacy_mismatch():
    parameters = {
        "m_max": 0.085,
        "m_min": 0.005,
        "m_cpg": 5,
        "bl_layers": 4,
        "allow_legacy_mesh_case_name_mismatch": True,
    }
    assert mesh_case_name_provenance(
        "legacy_mesh",
        parameters,
    ) == ("mesh_max085_min005_cpg5_bl4", "LEGACY_OPT_OUT")
