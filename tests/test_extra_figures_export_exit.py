"""Tests for 08 extra-figures planned-export exit bookkeeping (F-03 partial)."""

from __future__ import annotations

from pathlib import Path

from helpers import POST_DIR, load_module


def load_extra_figures():
    return load_module(
        "extra_figures_under_test",
        POST_DIR / "pyensight_extra_figures.py",
    )


def test_count_planned_exported_ignores_unplanned_copies():
    mod = load_extra_figures()
    planned = [Path("/tmp/a.png"), Path("/tmp/b.png")]
    exported = [Path("/tmp/a.png"), Path("/tmp/generic.png")]
    assert mod.count_planned_exported(planned, exported) == (1, 2)


def test_resolve_exit_partial_failure_when_planned_export_missing():
    mod = load_extra_figures()
    planned = [Path("/tmp/a.png"), Path("/tmp/b.png")]
    exported = [Path("/tmp/a.png")]
    assert mod.resolve_extra_figures_exit_code(exported, planned) == 1


def test_resolve_exit_success_when_all_planned_exported():
    mod = load_extra_figures()
    planned = [Path("/tmp/a.png"), Path("/tmp/b.png")]
    exported = [Path("/tmp/a.png"), Path("/tmp/b.png"), Path("/tmp/extra.png")]
    assert mod.resolve_extra_figures_exit_code(exported, planned) == 0


def test_empty_planned_exports_does_not_trigger_partial_failure():
    mod = load_extra_figures()
    exported = [Path("/tmp/a.png")]
    assert mod.resolve_extra_figures_exit_code(exported, []) == 0


def test_total_failure_when_nothing_exported():
    mod = load_extra_figures()
    planned = [Path("/tmp/a.png")]
    assert mod.resolve_extra_figures_exit_code([], planned) == 1


def test_strict_qcriterion_failure_still_exits_one():
    mod = load_extra_figures()
    planned = [Path("/tmp/a.png")]
    exported = [Path("/tmp/a.png")]
    assert mod.resolve_extra_figures_exit_code(
        exported,
        planned,
        q_iso_strict_failure=True,
        strict_qcriterion_required=True,
    ) == 1
