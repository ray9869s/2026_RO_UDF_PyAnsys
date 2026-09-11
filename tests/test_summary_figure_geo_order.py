"""Live post config and figures use campaign geo_ids, not archive names."""

from __future__ import annotations

import pandas as pd
import pytest

from helpers import CONFIGS_DIR, SCRIPTS_DIR, load_module
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, CAMPAIGN_GEO_IDS

ARCHIVE_GEO_NAMES = (
    "Sin_ST",
    "Sin_SL",
    "Diamond_Spacer",
    "Hole_Pillar",
    "Multi_Layer_equal",
    "Multi_Layer_diff",
)

LIVE_FILES = (
    CONFIGS_DIR / "batch_post_config.py",
    SCRIPTS_DIR / "make_summary_figures.py",
)


def _load_batch_post_config():
    return load_module("batch_post_config_geo_order", CONFIGS_DIR / "batch_post_config.py")


def _load_summary_figures():
    return load_module(
        "make_summary_figures_geo_order",
        SCRIPTS_DIR / "make_summary_figures.py",
    )


@pytest.mark.parametrize("path", LIVE_FILES)
def test_live_post_files_have_no_archive_geo_names(path):
    text = path.read_text(encoding="utf-8")
    for name in ARCHIVE_GEO_NAMES:
        assert name not in text
    assert '"Empty"' not in text
    assert "'Empty'" not in text


def test_batch_post_config_geometries_are_campaign_geo_ids():
    cfg = _load_batch_post_config()
    assert cfg.geometries == list(CAMPAIGN_GEO_ID_ORDER)
    assert set(cfg.geometries) == set(CAMPAIGN_GEO_IDS)
    assert cfg.post_cases == []


def test_geo_order_is_campaign_geo_ids():
    figures = _load_summary_figures()
    assert figures.GEO_ORDER == list(CAMPAIGN_GEO_ID_ORDER)
    assert set(figures.GEO_ORDER) == set(CAMPAIGN_GEO_IDS)
    assert figures.GEO_ORDER[0] == "REF_empty"


def test_ordered_geos_follows_campaign_then_appends_extras():
    figures = _load_summary_figures()
    df = pd.DataFrame(
        {"geo_name": ["S_a144_l3465", "not_in_campaign", "REF_empty"]}
    )
    with pytest.warns(UserWarning, match="not_in_campaign"):
        ordered = figures.ordered_geos(df)
    assert ordered == ["REF_empty", "S_a144_l3465", "not_in_campaign"]
