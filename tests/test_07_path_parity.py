"""Parity tests for 07_batch_solver_rerun path helpers vs _solver_common (phase 5)."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path, PurePosixPath

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


def _can_create_symlinks() -> bool:
    """True when this host can create a directory symlink for the divergence test."""
    if not hasattr(os, "symlink"):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            target = base / "target"
            target.mkdir()
            link = base / "link"
            os.symlink(target, link, target_is_directory=True)
            return link.is_symlink()
    except (OSError, NotImplementedError, NotADirectoryError):
        return False


def legacy_fluent_path(path) -> str:
    """Inline copy of 07_batch_solver_rerun.fluent_path before phase 5."""
    return str(Path(path).resolve()).replace("\\", "/")


def legacy_final_pair_for_case(results_root, geo_name: str, case_name: str) -> tuple[str, str, str]:
    """Inline copy of 07 final_pair_for_case string forms (os.path.join semantics)."""
    case_dir = os.path.join(results_root, geo_name, case_name)
    final_case_file = os.path.join(case_dir, f"{geo_name}_{case_name}_final.cas.h5")
    final_data_file = final_case_file.replace(".cas.h5", ".dat.h5")
    return case_dir, final_case_file, final_data_file


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
        results_root = str(RESULTS_ROOT)
        legacy = legacy_final_pair_for_case(results_root, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(results_root, geo_name, case_name)
        assert shared == legacy

    @pytest.mark.parametrize("geo_name,case_name", FIXTURE_CASES)
    def test_matches_under_root_fixture_cases(self, common, geo_name, case_name):
        results_root = str(RESULTS_ROOT)
        legacy = legacy_final_pair_for_case(results_root, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(results_root, geo_name, case_name)
        assert shared == legacy

    def test_custom_results_root(self, common, tmp_path):
        custom_root = str(tmp_path / "alternate_results")
        geo_name = "Sin_ST"
        case_name = "u0p2_p4M__mesh_max100_min006_cpg5_bl4"
        legacy = legacy_final_pair_for_case(custom_root, geo_name, case_name)
        shared = common.final_case_data_paths_under_root(custom_root, geo_name, case_name)
        assert shared == legacy

    @pytest.mark.parametrize("geo_name,case_name", FIXTURE_CASES[:2])
    def test_rerun07_final_pair_uses_supplied_run_directory(
        self,
        rerun07,
        tmp_path,
        geo_name,
        case_name,
    ):
        run_directory = tmp_path / "runs" / "canonical-run"
        case_dir, final_case, final_data = rerun07.final_pair_for_run(
            run_directory,
            geo_name,
            case_name,
        )
        assert case_dir == run_directory
        assert final_case == run_directory / f"{geo_name}_{case_name}_final.cas.h5"
        assert final_data == run_directory / f"{geo_name}_{case_name}_final.dat.h5"


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

    @pytest.mark.skipif(
        not _can_create_symlinks(),
        reason="symlink divergence test requires os.symlink support on this host",
    )
    def test_symlink_matches_resolved_not_abspath(self, common):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            real_dir = base / "real_case"
            real_dir.mkdir()
            link_dir = base / "linked_case"
            os.symlink(real_dir, link_dir, target_is_directory=True)
            case_file = link_dir / "case.cas.h5"
            case_file.touch()

            resolved = common.path_to_fluent_str_resolved(case_file)
            abspath = common.path_to_fluent_str(case_file)
            assert resolved == legacy_fluent_path(case_file)
            assert resolved != abspath
            real_root = PurePosixPath(str(real_dir.resolve())).as_posix()
            link_root = PurePosixPath(str(link_dir)).as_posix()
            assert real_root in PurePosixPath(resolved).as_posix()
            assert link_root in PurePosixPath(abspath).as_posix()

    def test_rerun07_uses_resolved_helper(self, rerun07, common):
        sample = RESULTS_ROOT / "Sin_ST" / "u0p2_p4M"
        assert rerun07.fluent_path(sample) == common.path_to_fluent_str_resolved(sample)


class TestRejectWindowsDriveWrapper:
    # Posix-shaped paths: on any host, only explicit C: entries match the drive regex.
    POSIX_SHAPED_CLI_PATHS = [
        "/data/PyFluent/My_CFD_Project/03_Results",
        "/data/PyFluent/My_CFD_Project/03_Results/_inventory/active_solver_rerun_candidates.csv",
        "/data/PyFluent/My_CFD_Project/03_Results/_inventory/solver_rerun",
        "/data/PyFluent/My_CFD_Project/03_Results/_inventory/solver_rerun_logs",
        "/data/PyFluent/My_CFD_Project/01_Scripts/post_processing/01_pyfluent_report_extract.py",
    ]

    @pytest.mark.parametrize(
        "unsafe_path",
        ["C:/temp/case", "C:\\temp\\case"],
    )
    def test_find_windows_drive_paths_flags_only_explicit_drive_entries(
        self, common, unsafe_path
    ):
        paths = [unsafe_path] + self.POSIX_SHAPED_CLI_PATHS
        assert common.find_windows_drive_paths(paths) == [unsafe_path]

    @pytest.mark.parametrize(
        "unsafe_path",
        ["C:/temp/case", "C:\\temp\\case"],
    )
    def test_wrapper_detects_same_unsafe_set_as_shared(
        self, rerun07, common, unsafe_path, monkeypatch
    ):
        monkeypatch.setattr(os, "name", "posix")
        paths = [unsafe_path] + self.POSIX_SHAPED_CLI_PATHS
        expected = common.find_windows_drive_paths(paths)
        assert expected == [unsafe_path]

        recorded: list[str] = []

        class RecordingParser(argparse.ArgumentParser):
            def error(self, message):  # type: ignore[override]
                recorded.append(message)
                raise SystemExit(2)

        with pytest.raises(SystemExit):
            rerun07.reject_windows_drive_paths_on_non_windows(paths, RecordingParser())
        assert len(recorded) == 1
        assert f"Unsafe path(s): {expected}" in recorded[0]

    def test_wrapper_noop_on_windows_host(self, rerun07, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        rerun07.reject_windows_drive_paths_on_non_windows(
            ["C:/temp/case"],
            argparse.ArgumentParser(),
        )

    def test_shared_reject_respects_explicit_host_os_name(self, common):
        with pytest.raises(ValueError, match="Unsafe path"):
            common.reject_windows_drive_paths_on_non_windows(
                ["C:/temp/case"],
                host_os_name="posix",
            )
        common.reject_windows_drive_paths_on_non_windows(
            ["C:/temp/case"],
            host_os_name="nt",
        )

    def test_wrapper_uses_parser_error_with_exact_message(self, rerun07, common, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        unsafe_path = "C:/temp/case"
        parser = argparse.ArgumentParser()
        with pytest.raises(SystemExit) as exc_info:
            rerun07.reject_windows_drive_paths_on_non_windows(
                [unsafe_path],
                parser,
            )
        assert exc_info.value.code == 2

        recorded: list[str] = []

        class RecordingParser(argparse.ArgumentParser):
            def error(self, message):  # type: ignore[override]
                recorded.append(message)
                raise SystemExit(2)

        with pytest.raises(SystemExit):
            rerun07.reject_windows_drive_paths_on_non_windows(
                [unsafe_path],
                RecordingParser(),
            )
        assert len(recorded) == 1
        assert common.find_windows_drive_paths([unsafe_path]) == [unsafe_path]
        assert f"Unsafe path(s): {[unsafe_path]}" in recorded[0]
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


class TestBuildPlanRowsCanonicalRunDir:
    def test_plan_emits_manifest_resolved_run_dir(self, rerun07, tmp_path):
        args = argparse.Namespace(results_root=RESULTS_ROOT)
        canonical_run_dir = (
            tmp_path
            / "runs"
            / "diamond"
            / "D2450_a45"
            / "max085_min006_cpg5_bl4_peel2"
            / "u0p2_p4M"
        )
        candidates = [
            {
                "selected_index": "1",
                "source_row_number": "2",
                "geo_name": "Sin_ST",
                "case_name": "u0p2_p4M",
                "convergence_status_before": "MAX_ITER_REACHED",
                "case_status_before": "NEEDS_SOLVER_RERUN",
                "_run_directory": str(canonical_run_dir),
            }
        ]
        plan_rows = rerun07.build_plan_rows(candidates, args)
        assert plan_rows[0]["case_dir"] == str(canonical_run_dir)
        assert not plan_rows[0]["case_dir"].startswith(str(RESULTS_ROOT))
