"""Tests for the 35-case campaign geo_id whitelist."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import REPO_ROOT
from ro.campaign_geo_ids import (
    CAMPAIGN_GEO_IDS,
    assert_no_legacy_ml_geo_paths,
    campaign_geo_id_shape_re,
    family_for_geo_id,
    validate_campaign_geo_id,
)
from ro.paths import geometry_dir


def test_whitelist_has_35_entries():
    assert len(CAMPAIGN_GEO_IDS) == 35


@pytest.mark.parametrize(
    "geo_id",
    sorted(CAMPAIGN_GEO_IDS),
)
def test_whitelisted_ids_match_shape_regex(geo_id: str):
    assert campaign_geo_id_shape_re().fullmatch(geo_id) is not None


@pytest.mark.parametrize(
    ("geo_id", "family"),
    [
        ("D2450_a45", "diamond"),
        ("M_c160", "ml"),
        ("M_c267", "ml"),
        ("M_c400", "ml"),
        ("P_p80_h20", "pillar"),
        ("S3465_A400", "sin"),
        ("S_A000", "sin"),
        ("REF_empty", "empty"),
    ],
)
def test_family_for_geo_id(geo_id: str, family: str):
    assert family_for_geo_id(geo_id) == family


def test_optional_numeric_token_ids_are_whitelisted():
    for geo_id in ("M_c160", "S_A000"):
        assert geo_id in CAMPAIGN_GEO_IDS
        validate_campaign_geo_id(geo_id)


def test_unknown_geo_id_raises():
    with pytest.raises(ValueError, match="not a campaign case"):
        validate_campaign_geo_id("D9999_a45")


def test_paths_reject_non_campaign_geo_id(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="not a campaign case"):
        geometry_dir("diamond", "D9999_a45")


def test_no_legacy_ml_geo_ids_remain_in_codebase():
    """Legacy M_r050/M_r100/M_r200 tokens must not appear outside guard code."""
    legacy_tokens = ("M_r050", "M_r100", "M_r200")
    allowed_suffixes = (
        "src/ro/campaign_geo_ids.py",
        "tests/test_campaign_geo_ids.py",
    )
    hits: list[str] = []
    for root_name in ("src", "tests", "scripts", "configs"):
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in allowed_suffixes:
                continue
            text = path.read_text(encoding="utf-8")
            for token in legacy_tokens:
                if token in text:
                    hits.append(f"{rel}: {token}")
    assert not hits, "Legacy ML geo_id tokens found:\n" + "\n".join(hits)


def test_data_root_has_no_legacy_ml_paths(monkeypatch, tmp_path):
    root = tmp_path / "data"
    (root / "meshes" / "ml" / "M_r050").mkdir(parents=True)
    with pytest.raises(ValueError, match="Legacy ML geo_id paths"):
        assert_no_legacy_ml_geo_paths(root)


def test_data_root_legacy_ml_check_passes_when_clean(tmp_path):
    root = tmp_path / "data"
    (root / "meshes" / "ml" / "M_c160").mkdir(parents=True)
    assert_no_legacy_ml_geo_paths(root)


def test_production_data_root_has_no_legacy_ml_paths():
    data_root = os.environ.get("RO_DATA_ROOT")
    if not data_root:
        pytest.skip("RO_DATA_ROOT not set")
    assert_no_legacy_ml_geo_paths(Path(data_root))
