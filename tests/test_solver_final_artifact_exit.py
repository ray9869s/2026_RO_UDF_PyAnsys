"""Tests for solver final-artifact exit-code resolver (F-03 #6)."""

from __future__ import annotations

import sys
import types

from helpers import SCRIPTS_DIR, load_batch_solver_sweep, load_module


def load_solver_code():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "solver_code_under_test",
            SCRIPTS_DIR / "solver_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def make_fs(*, case_exists=True, data_exists=True, case_size=100, data_size=200):
    paths = {
        "/tmp/final.cas.h5": case_exists,
        "/tmp/final.dat.h5": data_exists,
    }
    sizes = {
        "/tmp/final.cas.h5": case_size,
        "/tmp/final.dat.h5": data_size,
    }

    def is_file(path):
        return paths.get(path, False)

    def get_size(path):
        return sizes[path]

    return is_file, get_size


class TestResolveSolverFinalArtifactExitCode:
    def test_both_present_and_non_empty_exits_zero(self):
        mod = load_solver_code()
        is_file, get_size = make_fs()
        assert (
            mod.resolve_solver_final_artifact_exit_code(
                "/tmp/final.cas.h5",
                "/tmp/final.dat.h5",
                is_file=is_file,
                get_size=get_size,
            )
            == mod.SOLVER_EXIT_SUCCESS
        )

    def test_missing_case_exits_two(self):
        mod = load_solver_code()
        is_file, get_size = make_fs(case_exists=False)
        assert (
            mod.resolve_solver_final_artifact_exit_code(
                "/tmp/final.cas.h5",
                "/tmp/final.dat.h5",
                is_file=is_file,
                get_size=get_size,
            )
            == mod.SOLVER_EXIT_ARTIFACT_FAILURE
        )

    def test_missing_data_exits_two(self):
        mod = load_solver_code()
        is_file, get_size = make_fs(data_exists=False)
        assert (
            mod.resolve_solver_final_artifact_exit_code(
                "/tmp/final.cas.h5",
                "/tmp/final.dat.h5",
                is_file=is_file,
                get_size=get_size,
            )
            == mod.SOLVER_EXIT_ARTIFACT_FAILURE
        )

    def test_zero_byte_case_exits_two(self):
        mod = load_solver_code()
        is_file, get_size = make_fs(case_size=0)
        assert (
            mod.resolve_solver_final_artifact_exit_code(
                "/tmp/final.cas.h5",
                "/tmp/final.dat.h5",
                is_file=is_file,
                get_size=get_size,
            )
            == mod.SOLVER_EXIT_ARTIFACT_FAILURE
        )

    def test_zero_byte_data_exits_two(self):
        mod = load_solver_code()
        is_file, get_size = make_fs(data_size=0)
        assert (
            mod.resolve_solver_final_artifact_exit_code(
                "/tmp/final.cas.h5",
                "/tmp/final.dat.h5",
                is_file=is_file,
                get_size=get_size,
            )
            == mod.SOLVER_EXIT_ARTIFACT_FAILURE
        )

    def test_collect_failures_reports_missing_and_empty(self):
        mod = load_solver_code()
        is_file, get_size = make_fs(case_exists=False, data_size=0)
        failures = mod.collect_solver_final_artifact_failures(
            "/tmp/final.cas.h5",
            "/tmp/final.dat.h5",
            is_file=is_file,
            get_size=get_size,
        )
        assert len(failures) == 2
        assert "not found" in failures[0]
        assert "empty" in failures[1]


class TestBatchSolverWorkerSucceeded:
    def test_zero_is_success(self):
        batch = load_batch_solver_sweep()
        assert batch.solver_worker_succeeded(0) is True

    def test_one_and_two_are_not_success(self):
        batch = load_batch_solver_sweep()
        assert batch.solver_worker_succeeded(1) is False
        assert batch.solver_worker_succeeded(2) is False

    def test_describe_artifact_failure(self):
        batch = load_batch_solver_sweep()
        assert batch.describe_solver_worker_failure(2) == "final case/data missing or empty"
