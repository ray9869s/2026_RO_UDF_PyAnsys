"""Parity tests for 07_batch_solver_rerun path helpers vs _solver_common (phase 5)."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module, load_solver_common

PROJECT_ROOT = REPO_ROOT / "My_CFD_Project"
RESULTS_ROOT = PROJECT_ROOT / "03_Results"
SCRIPT_DIR = PROJECT_ROOT / "01_Scripts"
SIN_MESH = "mesh_max100_min006_cpg3_bl3"
SIN_MESH_CPG5 = "mesh_max100_min006_cpg5_bl4"

FIXTURE_CASES = [
    ("Sin_ST", "u0p2_p4M__mesh_max100_min006_cpg5_bl4"),
    ("Sin_ST", "u0p2_p6M__mesh_max100_min006_cpg5_bl4"),
    ("Sin_SL", "u0p3_p8M__mesh_max100_min006_cpg5_bl4"),
    ("Sin_ST", "u0p2_p4M"),
    ("Sin_ST", f"u0p1_p4M__{SIN_MESH}"),
    ("Sin_ST", f"u0p3_p8M__{SIN_MESH}"),
]

CAMPAIGN_CASES = [
    ("Sin_ST", "u0p3_p4M"),
    ("Sin_ST", "u0p3_p6M"),
    ("Sin_ST", "u0p3_p8M"),
    ("Sin_ST", f"u0p1_p4M__{SIN_MESH}"),
    ("Sin_ST", f"u0p1_p6M__{SIN_MESH}"),
    ("Sin_ST", f"u0p1_p8M__{SIN_MESH}"),
    ("Sin_ST", f"u0p2_p4M__{SIN_MESH}"),
    ("Sin_ST", f"u0p2_p6M__{SIN_MESH}"),
    ("Sin_ST", f"u0p2_p8M__{SIN_MESH}"),
    ("Sin_ST", f"u0p3_p4M__{SIN_MESH}"),
    ("Sin_ST", f"u0p3_p6M__{SIN_MESH}"),
    ("Sin_ST", f"u0p3_p8M__{SIN_MESH}"),
]


def legacy_fluent_path(path) -> str:
    """Inline copy of 07_batch_solver_rerun.fluent_path before phase 5."""
    return str(Path(path).resolve()).replace("\\", "/")


def legacy_final_pair_for_case(results_root, geo_name: str, case_name: str) -> tuple[str, str, str]:
    """Inline copy of 07 final_pair_for_case string forms (str(case_dir) etc.)."""
    case_dir = Path(results_root) / geo_name / case_name
    final_case_file = case_dir / f"{geo_name}_{case_name}_final.cas.h5"
    final_data_file = case_dir / f"{geo_name}_{case_name}_final.dat.h5"
    return str(case_dir), str(final_case_file), str(final_data_file)


def legacy_attempt_pair_for_case(
    case_dir,
    geo_name: str,
    case_name: str,
    timestamp: str,
) -> tuple[str, str, str]:
    """Inline copy of 07 attempt_pair_for_case before phase 5."""
    attempt_dir = (
        Path(case_dir) / "post" / "solver_rerun" / "attempts" / timestamp
    ).resolve()
    attempt_case_file = attempt_dir / f"{geo_name}_{case_name}_rerun_attempt.cas.h5"
    attempt_data_file = attempt_dir / f"{geo_name}_{case_name}_rerun_attempt.dat.h5"
    return str(attempt_dir), str(attempt_case_file), str(attempt_data_file)


def build_fluent_path_golden_inputs() -> list[tuple[str, Path | str]]:
    inputs: list[tuple[str, Path | str]] = []
    inputs.append(("DEFAULT_RESULTS_ROOT", RESULTS_ROOT))
    inputs.append(
        (
            "DEFAULT_REPORT_SCRIPT",
            SCRIPT_DIR / "post_processing" / "01_pyfluent_report_extract.py",
        )
    )
    inputs.append(("PROJECT_ROOT/03_Results", RESULTS_ROOT))

    ts = "20260721_165800"
    for geo, case in FIXTURE_CASES:
        case_dir = RESULTS_ROOT / geo / case
        final_case = case_dir / f"{geo}_{case}_final.cas.h5"
        log_path = (
            RESULTS_ROOT / "_inventory" / "solver_rerun_logs" / f"{geo}__{case}__solver_rerun.log"
        )
        transcript = log_path.with_name(log_path.stem + "__fluent.trn")
        attempt_dir = (case_dir / "post" / "solver_rerun" / "attempts" / ts).resolve()
        attempt_case = attempt_dir / f"{geo}_{case}_rerun_attempt.cas.h5"
        inputs.extend(
            [
                (f"case_dir {geo}/{case}", case_dir),
                (f"case_dir_resolved {geo}/{case}", case_dir.resolve()),
                (f"final_case {geo}/{case}", final_case),
                (f"final_case_resolved {geo}/{case}", final_case.resolve()),
                (f"attempt_case {geo}/{case}", attempt_case),
                (f"transcript {geo}/{case}", transcript),
                (f"log_path {geo}/{case}", log_path),
            ]
        )

    inputs.append(("relative .", Path(".")))
    inputs.append(("relative final.cas.h5", Path("Sin_ST_u0p2_p4M_final.cas.h5")))
    return inputs


FLUENT_PATH_GOLDEN_INPUTS = build_fluent_path_golden_inputs()


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


@pytest.fixture(scope="module")
def rerun07():
    return load_module("batch_solver_rerun_path_parity", SCRIPTS_DIR / "07_batch_solver_rerun.py")


class TestFinalPairForCaseParity:
    @pytest.mark.parametrize("geo_name,case_name", CAMPAIGN_CASES)
    def test_matches_under_root_default_results_root(self, common, geo_name, case_name):
        legacy = legacy_final_pair_for_case(RESULTS_ROOT, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(str(RESULTS_ROOT), geo_name, case_name)
        assert shared == legacy

    @pytest.mark.parametrize("geo_name,case_name", FIXTURE_CASES)
    def test_matches_under_root_fixture_cases(self, common, geo_name, case_name):
        legacy = legacy_final_pair_for_case(RESULTS_ROOT, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(str(RESULTS_ROOT), geo_name, case_name)
        assert shared == legacy

    def test_custom_results_root(self, common):
        custom_root = "/data/alternate_results"
        geo_name = "Sin_ST"
        case_name = "u0p2_p4M__mesh_max100_min006_cpg5_bl4"
        legacy = legacy_final_pair_for_case(custom_root, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(custom_root, geo_name, case_name)
        assert shared == legacy

    @pytest.mark.parametrize("geo_name,case_name", FIXTURE_CASES[:2])
    def test_rerun07_wrapper_matches_legacy_strings(self, rerun07, geo_name, case_name):
        case_dir, final_case, final_data = rerun07.final_pair_for_case(
            RESULTS_ROOT,
            geo_name,
            case_name,
        )
        legacy = legacy_final_pair_for_case(RESULTS_ROOT, geo_name, case_name)
        assert (str(case_dir), str(final_case), str(final_data)) == legacy


class TestFluentPathParity:
    @pytest.mark.parametrize(
        "label,path_input",
        FLUENT_PATH_GOLDEN_INPUTS,
        ids=[label for label, _ in FLUENT_PATH_GOLDEN_INPUTS],
    )
    def test_resolved_matches_legacy_fluent_path(self, common, label, path_input):
        assert common.path_to_fluent_str_resolved(path_input) == legacy_fluent_path(path_input)

    @pytest.mark.parametrize(
        "label,path_input",
        FLUENT_PATH_GOLDEN_INPUTS,
        ids=[label for label, _ in FLUENT_PATH_GOLDEN_INPUTS],
    )
    def test_resolved_matches_abspath_on_golden_inputs(self, common, label, path_input):
        assert common.path_to_fluent_str_resolved(path_input) == common.path_to_fluent_str(path_input)

    def test_symlink_matches_resolved_not_abspath(self, common):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            real_dir = base / "real_case"
            real_dir.mkdir()
            link_dir = base / "linked_case"
            link_dir.symlink_to(real_dir)
            case_file = link_dir / "case.cas.h5"
            case_file.touch()

            resolved = common.path_to_fluent_str_resolved(case_file)
            abspath = common.path_to_fluent_str(case_file)
            assert resolved == legacy_fluent_path(case_file)
            assert resolved != abspath
            assert str(real_dir) in resolved
            assert str(link_dir) in abspath

    def test_rerun07_uses_resolved_helper(self, rerun07, common):
        sample = RESULTS_ROOT / "Sin_ST" / "u0p2_p4M"
        assert rerun07.fluent_path(sample) == common.path_to_fluent_str_resolved(sample)


class TestRejectWindowsDriveWrapper:
    CLI_PATHS = [
        RESULTS_ROOT,
        RESULTS_ROOT / "_inventory" / "active_solver_rerun_candidates.csv",
        RESULTS_ROOT / "_inventory" / "solver_rerun",
        RESULTS_ROOT / "_inventory" / "solver_rerun_logs",
        SCRIPT_DIR / "post_processing" / "01_pyfluent_report_extract.py",
    ]

    @pytest.mark.parametrize(
        "unsafe_path",
        ["C:/temp/case", "C:\\temp\\case"],
    )
    def test_wrapper_detects_same_unsafe_set_as_shared(self, rerun07, common, unsafe_path):
        paths = [Path(unsafe_path)] + list(self.CLI_PATHS)
        assert common.find_windows_drive_paths(paths) == [unsafe_path]

    def test_wrapper_uses_parser_error_with_exact_message(self, rerun07, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        parser = argparse.ArgumentParser()
        with pytest.raises(SystemExit) as exc_info:
            rerun07.reject_windows_drive_paths_on_non_windows(
                [Path("C:/temp/case")],
                parser,
            )
        assert exc_info.value.code == 2

        # Capture message via a fresh parser that records error text.
        recorded: list[str] = []

        class RecordingParser(argparse.ArgumentParser):
            def error(self, message):  # type: ignore[override]
                recorded.append(message)
                raise SystemExit(2)

        with pytest.raises(SystemExit):
            rerun07.reject_windows_drive_paths_on_non_windows(
                [Path("C:/temp/case")],
                RecordingParser(),
            )
        assert len(recorded) == 1
        assert "Unsafe path(s): ['C:/temp/case']" in recorded[0]
        assert "Windows drive paths are not accepted on this non-Windows host" in recorded[0]


class TestAttemptPairForCaseRegression:
    @pytest.mark.parametrize("geo_name,case_name", FIXTURE_CASES)
    def test_matches_legacy_snapshot(self, rerun07, geo_name, case_name):
        timestamp = "20260721_165800"
        case_dir = RESULTS_ROOT / geo_name / case_name
        attempt_dir, attempt_case, attempt_data = rerun07.attempt_pair_for_case(
            case_dir,
            geo_name,
            case_name,
            timestamp,
        )
        legacy = legacy_attempt_pair_for_case(case_dir, geo_name, case_name, timestamp)
        assert (str(attempt_dir), str(attempt_case), str(attempt_data)) == legacy

    def test_attempt_dir_is_resolved(self, rerun07):
        timestamp = "20260721_165800"
        case_dir = RESULTS_ROOT / "Sin_ST" / "u0p2_p4M"
        attempt_dir, _, _ = rerun07.attempt_pair_for_case(
            case_dir,
            "Sin_ST",
            "u0p2_p4M",
            timestamp,
        )
        assert attempt_dir.is_absolute()


class TestBuildPlanRowsCaseDirUnresolved:
    def test_plan_emits_unresolved_case_dir_string(self, rerun07, monkeypatch):
        import argparse

        args = argparse.Namespace(results_root=RESULTS_ROOT)
        candidates = [
            {
                "selected_index": "1",
                "source_row_number": "2",
                "geo_name": "Sin_ST",
                "case_name": "u0p2_p4M",
                "convergence_status_before": "MAX_ITER_REACHED",
                "case_status_before": "NEEDS_SOLVER_RERUN",
            }
        ]
        plan_rows = rerun07.build_plan_rows(candidates, args)
        expected_case_dir = str(RESULTS_ROOT / "Sin_ST" / "u0p2_p4M")
        assert plan_rows[0]["case_dir"] == expected_case_dir
        assert plan_rows[0]["case_dir"] == str(
            rerun07.final_pair_for_case(RESULTS_ROOT, "Sin_ST", "u0p2_p4M")[0]
        )
