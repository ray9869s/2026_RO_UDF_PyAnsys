"""Restart must read copies in the target leaf, never the source folder."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_solver_code


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


def test_staged_names_use_the_new_run_id_not_final(tmp_path):
    solver_code = load_solver_code("cwd_staged_names")
    target = tmp_path / "u0p3_p6M_restart"
    case_path = solver_code.restart_staged_case_path(
        str(target), "D0817_a30", "u0p3_p6M_restart"
    )
    data_path = solver_code.restart_staged_data_path(
        str(target), "D0817_a30", "u0p3_p6M_restart"
    )
    assert case_path.endswith("D0817_a30_u0p3_p6M_restart_restart_from.cas.h5")
    assert data_path.endswith("D0817_a30_u0p3_p6M_restart_restart_from.dat.h5")
    assert "_final." not in case_path
    assert "_final." not in data_path


def test_copy_moves_only_the_two_finals(tmp_path):
    solver_code = load_solver_code("cwd_copy_two_files")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    source_case = source / "D0817_a30_u0p2_p6M_final.cas.h5"
    source_data = source / "D0817_a30_u0p2_p6M_final.dat.h5"
    source_case.write_text("cas", encoding="utf-8")
    source_data.write_text("dat", encoding="utf-8")
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "lmh_udm_avg.out").write_text("out", encoding="utf-8")
    (source / "fluent-0001.trn").write_text("trn", encoding="utf-8")
    post = source / "post"
    post.mkdir()
    (post / "figure.png").write_text("png", encoding="utf-8")

    staged_case = solver_code.restart_staged_case_path(
        str(target), "D0817_a30", "u0p3_p6M_restart"
    )
    staged_data = solver_code.restart_staged_data_path(
        str(target), "D0817_a30", "u0p3_p6M_restart"
    )
    solver_code.copy_restart_source_into_case_folder(
        str(source_case),
        str(source_data),
        staged_case,
        staged_data,
    )
    solver_code.require_restart_read_not_source_folder(
        staged_case,
        staged_data,
        str(target),
        str(source),
        source_case_file=str(source_case),
        source_data_file=str(source_data),
    )

    copied = sorted(path.name for path in target.iterdir())
    assert copied == [
        "D0817_a30_u0p3_p6M_restart_restart_from.cas.h5",
        "D0817_a30_u0p3_p6M_restart_restart_from.dat.h5",
    ]
    assert (target / copied[0]).read_text(encoding="utf-8") == "cas"
    assert (target / copied[1]).read_text(encoding="utf-8") == "dat"


def test_opening_the_source_files_is_refused(tmp_path):
    solver_code = load_solver_code("cwd_refuse_source_open")
    source = tmp_path / "u0p2_p6M"
    target = tmp_path / "u0p3_p6M_restart"
    source.mkdir()
    target.mkdir()
    source_case = source / "D0817_a30_u0p2_p6M_final.cas.h5"
    source_data = source / "D0817_a30_u0p2_p6M_final.dat.h5"
    source_case.write_text("cas", encoding="utf-8")
    source_data.write_text("dat", encoding="utf-8")
    with pytest.raises(RuntimeError, match="restart source folder"):
        solver_code.require_restart_read_not_source_folder(
            str(source_case),
            str(source_data),
            str(target),
            str(source),
            source_case_file=str(source_case),
            source_data_file=str(source_data),
        )


def test_remove_staged_copies_leaves_other_files(tmp_path):
    solver_code = load_solver_code("cwd_remove_staged")
    target = tmp_path / "u0p3_p6M_restart"
    target.mkdir()
    staged_case = target / "D0817_a30_u0p3_p6M_restart_restart_from.cas.h5"
    staged_data = target / "D0817_a30_u0p3_p6M_restart_restart_from.dat.h5"
    kept = target / "D0817_a30_u0p3_p6M_restart_final.cas.h5"
    staged_case.write_text("cas", encoding="utf-8")
    staged_data.write_text("dat", encoding="utf-8")
    kept.write_text("final", encoding="utf-8")
    solver_code.remove_staged_restart_copies(str(staged_case), str(staged_data))
    assert not staged_case.exists()
    assert not staged_data.exists()
    assert kept.read_text(encoding="utf-8") == "final"


def test_copy_precedes_launch_and_source_is_not_read():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    copy_at = source.index(
        "copy_restart_source_into_case_folder(\n            restart_from_case_file,"
    )
    first_launch = source.index("pyfluent.launch_fluent(")
    read_staged = source.index(
        "file_name=as_fluent_path(staged_restart_case_file)"
    )
    assert copy_at < first_launch < read_staged
    assert "file_name=as_fluent_path(restart_from_case_file)" not in source
    assert "file_name=as_fluent_path(restart_from_data_file)" not in source
    assert "/file/set-working-directory" not in source
    assert 'string_eval("(pwd)")' not in source
