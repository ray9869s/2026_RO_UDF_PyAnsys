"""Tests for 03 contour export exit-code resolver (F-03 #1)."""

from __future__ import annotations

from helpers import POST_DIR, load_module


def load_contour_export():
    return load_module(
        "contour_export_under_test",
        POST_DIR / "03_pyensight_contour_export.py",
    )


def make_record(mod, status: str):
    return mod.ExportRecord(
        geo_name="Sin_ST",
        case_name="u0p1_p4M",
        field_key="cp_inlet",
        field_name="CP inlet",
        target_surface_or_plane="membrane",
        output_file="/tmp/cp_inlet.png",
        status=status,
    )


def test_all_success_exits_zero():
    mod = load_contour_export()
    records = [
        make_record(mod, mod.STATUS_SUCCESS),
        make_record(mod, mod.STATUS_SUCCESS),
    ]
    assert mod.resolve_contour_export_exit_code(records) == 0


def test_success_and_warn_exits_one():
    mod = load_contour_export()
    records = [
        make_record(mod, mod.STATUS_SUCCESS),
        make_record(mod, mod.STATUS_WARN),
    ]
    assert mod.resolve_contour_export_exit_code(records) == 1


def test_any_failed_exits_two_even_with_warn():
    mod = load_contour_export()
    records = [
        make_record(mod, mod.STATUS_WARN),
        make_record(mod, mod.STATUS_FAILED),
    ]
    assert mod.resolve_contour_export_exit_code(records) == 2


def test_skipped_existing_only_exits_zero():
    mod = load_contour_export()
    records = [make_record(mod, mod.STATUS_SKIPPED_EXISTING)]
    assert mod.resolve_contour_export_exit_code(records) == 0
