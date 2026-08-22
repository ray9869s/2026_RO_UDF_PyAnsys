"""Tests for F-03 #8 Phase 3: log-only CFF .scm vs config-mu guard."""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import pytest

from helpers import POST_DIR, REPO_ROOT, load_module

REAL_TEMPLATE_SHAPE = (
    "(custom-field-function/define\n"
    " '(((name cff_wall_shear_rate)"
    ' (display "wall-shear / (0.000893)")'
    ' (syntax-tree ("/" "wall-shear" 0.000893))'
    ' (code (field-/ (field-load "wall-shear") 0.000893)))\n'
    "   ))\n"
)

FALLBACK_ONLY_SHAPE = (
    "(custom-field-function/define\n"
    " '(((name cff_wall_shear_rate)"
    ' (syntax-tree ("/" "wall-shear" 0.000893)))\n'
    "   ))\n"
)

CONFLICTING_DIVISORS = (
    "(custom-field-function/define\n"
    " '(((name cff_wall_shear_rate)"
    ' (syntax-tree ("/" "wall-shear" 0.000893))'
    ' (code (field-/ (field-load "wall-shear") 0.001)))\n'
    "   ))\n"
)

MALFORMED_SCM = "this is not a fluent cff scm\n"


def load_guard():
    # Load the implementation module, not the 01_Scripts shim. monkeypatch.setattr
    # of parse_scm_wall_shear_divisor must hit the globals emit_scm_mu_guard_message
    # actually looks up.
    return load_module(
        "shear_cff_mu_guard_under_test",
        REPO_ROOT / "src" / "ro" / "shear_cff_mu_guard.py",
    )


def load_shear_export():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
        "numpy": types.ModuleType("numpy"),
        "matplotlib": types.ModuleType("matplotlib"),
        "matplotlib.pyplot": types.ModuleType("matplotlib.pyplot"),
        "matplotlib.tri": types.ModuleType("matplotlib.tri"),
    }
    stubs["matplotlib"].use = lambda *args, **kwargs: None

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "shear_export_mu_guard_under_test",
            POST_DIR / "pyfluent_shear_contour_export.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_real_template_shape(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "cff_wall_shear_rate.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")
    assert guard.parse_scm_wall_shear_divisor(scm) == 0.000893


def test_parse_fallback_only_shape(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "fallback_only.scm"
    scm.write_text(FALLBACK_ONLY_SHAPE, encoding="utf-8")
    assert guard.parse_scm_wall_shear_divisor(scm) == 0.000893


def test_parse_malformed_returns_none_no_raise(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "bad.scm"
    scm.write_text(MALFORMED_SCM, encoding="utf-8")
    assert guard.parse_scm_wall_shear_divisor(scm) is None
    assert guard.parse_scm_wall_shear_divisor(tmp_path / "missing.scm") is None


def test_parse_conflicting_divisors_returns_none(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "conflict.scm"
    scm.write_text(CONFLICTING_DIVISORS, encoding="utf-8")
    assert guard.parse_scm_wall_shear_divisor(scm) is None


def test_parse_accepts_path_and_str(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "cff.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")
    assert guard.parse_scm_wall_shear_divisor(scm) == 0.000893
    assert guard.parse_scm_wall_shear_divisor(str(scm)) == 0.000893


# ---------------------------------------------------------------------------
# Guard emit table
# ---------------------------------------------------------------------------


def test_guard_within_tol_silent(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "cff.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")
    lines: list[str] = []
    guard.emit_scm_mu_guard_message(scm, 8.93e-4, case_label="g/c", log=lines.append)
    assert lines == []


def test_guard_mismatch_one_warning(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "cff.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")
    lines: list[str] = []
    guard.emit_scm_mu_guard_message(scm, 1e-3, case_label="Sin_ST/u0p1", log=lines.append)
    assert len(lines) == 1
    msg = lines[0]
    assert msg.startswith("WARNING:")
    assert "0.000893" in msg
    assert "0.001" in msg
    assert str(scm) in msg
    assert "Sin_ST/u0p1" in msg


def test_guard_parse_fail_info_only(tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "bad.scm"
    scm.write_text(MALFORMED_SCM, encoding="utf-8")
    lines: list[str] = []
    guard.emit_scm_mu_guard_message(scm, 8.93e-4, log=lines.append)
    assert len(lines) == 1
    assert lines[0].startswith("INFO:")
    assert "could not parse" in lines[0]
    assert not any(line.startswith("WARNING:") for line in lines)


def test_guard_none_and_empty_cff_file_noop():
    guard = load_guard()
    lines: list[str] = []
    guard.emit_scm_mu_guard_message(None, 8.93e-4, log=lines.append)
    guard.emit_scm_mu_guard_message("", 8.93e-4, log=lines.append)
    assert lines == []


def test_guard_swallows_unexpected_errors(monkeypatch, tmp_path: Path):
    guard = load_guard()
    scm = tmp_path / "cff.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "parse_scm_wall_shear_divisor", boom)
    lines: list[str] = []
    guard.emit_scm_mu_guard_message(scm, 8.93e-4, log=lines.append)
    assert len(lines) == 1
    assert lines[0].startswith("INFO: CFF mu guard skipped:")
    assert "boom" in lines[0]


# ---------------------------------------------------------------------------
# Parity: prepare_native_cff return dict / status key set unchanged
# ---------------------------------------------------------------------------


def test_prepare_native_cff_parity_guard_is_side_effect_only(tmp_path: Path, monkeypatch):
    mod = load_shear_export()
    scm = tmp_path / "cff.scm"
    scm.write_text(REAL_TEMPLATE_SHAPE, encoding="utf-8")

    monkeypatch.setattr(
        mod,
        "_load_cff_file",
        lambda solver, cff_file: (True, "mock", ""),
    )

    result_with = mod.prepare_native_cff(
        solver=object(),
        cff_name="cff_wall_shear_rate",
        cff_file=scm,
        mu=8.93e-4,
        case_label="geo/case",
    )
    snapshot = copy.deepcopy(result_with)

    monkeypatch.setattr(mod, "emit_scm_mu_guard_message", lambda *a, **k: None)
    result_without = mod.prepare_native_cff(
        solver=object(),
        cff_name="cff_wall_shear_rate",
        cff_file=scm,
        mu=8.93e-4,
        case_label="geo/case",
    )

    assert json.dumps(result_with, sort_keys=True) == json.dumps(
        result_without, sort_keys=True
    )
    assert result_with == snapshot  # input/result dict unmodified by later call
    assert set(result_with) == set(result_without)

    # Status payload key set must not gain a guard-related key from this path.
    status_keys = set(
        mod.build_status_payload(
            geo_name="g",
            case_name="c",
            status=mod.STATUS_OK,
            selected_surfaces=["wall_top_mem"],
            mu_used=8.93e-4,
            output_files=[],
            message="",
            selected_variable=None,
            selected_variable_for_field_data=None,
            selected_cff_cell_function=result_with["selected_cff_cell_function"],
            selected_wall_shear_source="",
            derived_variable_mode="",
            native_attempted=True,
            native_status="SUCCESS",
            native_error="",
            native_variable_used=result_with["native_variable_used"] or None,
            cff_name="cff_wall_shear_rate",
            cff_file=scm,
            cff_file_load_attempted=result_with["cff_file_load_attempted"],
            cff_file_load_status=result_with["cff_file_load_status"],
            cff_file_load_error=result_with["cff_file_load_error"],
            cff_direct_create_attempted=result_with["cff_direct_create_attempted"],
            cff_direct_create_status=result_with["cff_direct_create_status"],
            cff_direct_create_errors=list(result_with["cff_direct_create_errors"]),
            cff_expression_attempts=list(result_with["cff_expression_attempts"]),
            fallback_attempted=False,
            fallback_status="SKIPPED",
            fallback_error="",
            shear_related_field_candidates=[],
            shear_related_cff_candidates=[],
            inferred_cff_cell_function_candidates=[],
            cff_candidate_source="",
            background="white",
            view_margin=1.2,
            image_width=1600,
            image_height=1200,
        ).keys()
    )
    assert "scm_mu" not in status_keys
    assert "mu_guard" not in status_keys
    assert "scm_mu_mismatch" not in status_keys
