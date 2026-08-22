"""Tests for post-processing default field alignment (F-03 partial)."""

from __future__ import annotations

from helpers import POST_DIR, load_module

INVENTORY_BASIC_FIELDS = ["cp_inlet", "water_flux", "lmh", "salt_flux"]
BATCH_POST_DEFAULT_FIELDS = "cp_inlet,water_flux,lmh,salt_flux"


def load_contour_export():
    return load_module("contour_export_under_test", POST_DIR / "pyensight_contour_export.py")


def load_batch_postprocess():
    return load_module("batch_postprocess_under_test", POST_DIR / "batch_postprocess_all_cases.py")


def test_contour_default_fields_match_inventory_basic_set():
    contour = load_contour_export()
    assert contour.DEFAULT_FIELDS == INVENTORY_BASIC_FIELDS


def test_batch_post_default_fields_match_contour_defaults():
    batch_post = load_batch_postprocess()
    args = batch_post.parse_args([])
    assert args.fields == BATCH_POST_DEFAULT_FIELDS
    assert [field.strip() for field in args.fields.split(",")] == INVENTORY_BASIC_FIELDS
