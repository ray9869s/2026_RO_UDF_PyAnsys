"""UDF membrane-constant parser for figure reconstruction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import REPO_ROOT
from ro.udf_constants import (
    assert_post_config_matches_udf,
    load_udf_membrane_constants_from_case,
    parse_udf_membrane_constants,
)
from ro.udm_layout import find_case_udf_path

UDF_PATH = REPO_ROOT / "udfs" / "260822_RO_UDF.c"


def test_parse_production_udf_membrane_constants():
    constants = parse_udf_membrane_constants(UDF_PATH.read_text(encoding="utf-8"))
    assert constants["a_perm"] == pytest.approx(2.50e-12)
    assert constants["b_perm"] == pytest.approx(2.50e-8)
    assert constants["kappa"] == pytest.approx(4958.0)
    assert constants["p_perm"] == pytest.approx(101325.0)
    assert constants["mw_salt"] == pytest.approx(0.05844)
    assert constants["rho_ref"] == pytest.approx(998.20)
    assert constants["c_inlet_ref"] == pytest.approx(597.8268309)
    assert constants["ms_to_lmh"] == pytest.approx(3600000.0)


def test_parse_udf_membrane_constants_missing_key_fails():
    with pytest.raises(ValueError, match="missing"):
        parse_udf_membrane_constants("static real A_perm = 1.0;")


def test_load_from_case_local_udf(tmp_path):
    (tmp_path / "260822_RO_UDF.c").write_text(
        UDF_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert find_case_udf_path(tmp_path).name == "260822_RO_UDF.c"
    constants = load_udf_membrane_constants_from_case(tmp_path)
    assert constants["b_perm"] == pytest.approx(2.50e-8)


def test_post_config_assert_passes_on_matching_values():
    constants = parse_udf_membrane_constants(UDF_PATH.read_text(encoding="utf-8"))
    cfg = SimpleNamespace(
        rho=998.20,
        salt_permeability_m_per_s=2.50e-8,
        c_inlet_ref=597.8268309,
        salt_molecular_weight_kg_per_mol=0.05844,
    )
    assert_post_config_matches_udf(cfg, constants)


def test_post_config_assert_fails_on_drift():
    constants = parse_udf_membrane_constants(UDF_PATH.read_text(encoding="utf-8"))
    cfg = SimpleNamespace(
        rho=999.0,
        salt_permeability_m_per_s=2.50e-8,
        c_inlet_ref=597.8268309,
        salt_molecular_weight_kg_per_mol=0.05844,
    )
    with pytest.raises(ValueError, match="rho"):
        assert_post_config_matches_udf(cfg, constants)
