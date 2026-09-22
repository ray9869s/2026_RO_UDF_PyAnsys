"""Compiled U_TARGET from the last probe block must match inlet_velocity_value."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_solver_code

MARKER = "=== RO_UDF probe_inlet_profile ==="


def _probe_block(u_target):
    return (
        f"{MARKER}\n"
        f"  U_TARGET                     = {u_target} m/s\n"
        "  G                            = 1.00371743\n"
    )


def _write_trn(case_dir, name, body):
    path = case_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def test_last_probe_block_wins_over_source_library_print(tmp_path):
    solver_code = load_solver_code("u_target_last_block")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    _write_trn(
        case_dir,
        "fluent-0001.trn",
        _probe_block("0.2") + _probe_block("0.3"),
    )
    solver_log = case_dir / "solver_log_u0p3_p6M_restart.txt"
    solver_log.write_text(_probe_block("9.9"), encoding="utf-8")

    assert solver_code.count_inlet_probe_markers(case_dir, solver_log) == 2
    assert solver_code.collect_last_probe_u_target_token(case_dir, solver_log) == "0.3"
    assert (
        solver_code.wait_for_agreed_probe_u_target(
            case_dir=case_dir,
            solver_log_path=solver_log,
            expected=0.3,
            min_probe_blocks=2,
            timeout_s=0.01,
            poll_interval_s=0.001,
        )
        == 0.3
    )


def test_stale_source_u_target_raises_before_iterate(tmp_path):
    solver_code = load_solver_code("u_target_stale")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    _write_trn(case_dir, "fluent-0001.trn", _probe_block("0.2") + _probe_block("0.2"))
    solver_log = case_dir / "solver_log_u0p3_p6M_restart.txt"
    solver_log.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Compiled U_TARGET"):
        solver_code.wait_for_agreed_probe_u_target(
            case_dir=case_dir,
            solver_log_path=solver_log,
            expected=0.3,
            min_probe_blocks=2,
            timeout_s=0.01,
            poll_interval_s=0.001,
        )


def test_old_probe_block_is_not_accepted_as_the_compiled_constant(tmp_path):
    solver_code = load_solver_code("u_target_need_new_block")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    _write_trn(case_dir, "fluent-0001.trn", _probe_block("0.2"))
    solver_log = case_dir / "solver_log_u0p3_p6M_restart.txt"
    solver_log.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Missing compiled U_TARGET"):
        solver_code.wait_for_agreed_probe_u_target(
            case_dir=case_dir,
            solver_log_path=solver_log,
            expected=0.3,
            min_probe_blocks=2,
            timeout_s=0.05,
            poll_interval_s=0.01,
        )


def test_solver_log_u_target_is_ignored(tmp_path):
    solver_code = load_solver_code("u_target_ignore_solver_log")
    case_dir = tmp_path / "run"
    case_dir.mkdir()
    solver_log = case_dir / "solver_log_u0p3_p6M_restart.txt"
    solver_log.write_text(_probe_block("0.3"), encoding="utf-8")

    assert solver_code.collect_last_probe_u_target_token(case_dir, solver_log) is None
    with pytest.raises(RuntimeError, match="fluent-\\*\\.trn"):
        solver_code.wait_for_agreed_probe_u_target(
            case_dir=case_dir,
            solver_log_path=solver_log,
            expected=0.3,
            timeout_s=0.05,
            poll_interval_s=0.01,
        )


def test_u_target_guard_is_after_compile_and_before_iterate():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    compile_at = source.index(
        'solver.tui.define.user_defined.compiled_functions(\n            "compile"'
    )
    load_at = source.index(
        'solver.tui.define.user_defined.compiled_functions(\n            "load"'
    )
    guard_at = source.index(
        "wait_for_agreed_probe_u_target(\n            case_dir=case_path"
    )
    iterate_at = source.index("solution.run_calculation.iterate")
    assert compile_at < load_at < guard_at < iterate_at
