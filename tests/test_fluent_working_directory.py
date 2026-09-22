"""Fluent file cwd must be the new case folder, never the restart source."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import load_solver_code
from ro.solver_common import path_to_fluent_str


class _FakeFluent:
    def __init__(self, pwd):
        self.pwd = pwd
        self.tui_commands = []
        self.scheme_eval = SimpleNamespace(string_eval=self._string_eval)

    def _string_eval(self, command):
        if command == "(pwd)":
            return self.pwd
        raise RuntimeError(f"unexpected scheme command: {command}")

    def execute_tui(self, command):
        self.tui_commands.append(command)
        prefix = '/file/set-working-directory "'
        if command.startswith(prefix) and command.endswith('"'):
            self.pwd = command[len(prefix) : -1]


def test_source_folder_cwd_is_refused(tmp_path):
    solver_code = load_solver_code("cwd_source_refused")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    error = solver_code.fluent_working_directory_error(
        str(source),
        str(target),
        restart_source_dir=str(source),
    )
    assert error is not None
    assert "restart source folder" in error


def test_wrong_cwd_is_refused_even_if_not_source(tmp_path):
    solver_code = load_solver_code("cwd_wrong_folder")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    other = tmp_path / "other"
    source.mkdir()
    target.mkdir()
    other.mkdir()
    error = solver_code.fluent_working_directory_error(
        str(other),
        str(target),
        restart_source_dir=str(source),
    )
    assert error is not None
    assert "not the target case_path" in error


def test_target_cwd_is_accepted(tmp_path):
    solver_code = load_solver_code("cwd_target_ok")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    assert (
        solver_code.fluent_working_directory_error(
            str(target),
            str(target),
            restart_source_dir=str(source),
        )
        is None
    )


def test_relative_cwd_is_not_treated_as_the_python_directory():
    solver_code = load_solver_code("cwd_relative")
    error = solver_code.fluent_working_directory_error(
        ".",
        "/tmp/target",
        restart_source_dir="/tmp/source",
    )
    assert error is not None
    assert "not an absolute path" in error


def test_restore_sets_fluent_cwd_then_confirms(tmp_path):
    solver_code = load_solver_code("cwd_restore")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    restart_case = source / "D0817_a30_u0p2_p6M_final.cas.h5"
    restart_case.write_text("cas", encoding="utf-8")
    fluent = _FakeFluent(str(source))

    confirmed = solver_code.restore_fluent_working_directory_for_restart(
        fluent,
        str(target),
        restart_case,
    )

    assert confirmed == path_to_fluent_str(target)
    assert fluent.pwd == path_to_fluent_str(target)
    assert fluent.tui_commands == [
        f'/file/set-working-directory "{path_to_fluent_str(target)}"'
    ]


def test_require_raises_when_pwd_stays_on_source(tmp_path):
    solver_code = load_solver_code("cwd_require_source")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    fluent = _FakeFluent(str(source))
    with pytest.raises(RuntimeError, match="restart source folder"):
        solver_code.require_fluent_working_directory(
            fluent,
            str(target),
            restart_source_dir=str(source),
        )
