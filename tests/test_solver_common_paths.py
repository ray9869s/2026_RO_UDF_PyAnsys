"""Golden and parity tests for _solver_common path primitives (phase 1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_solver_common

SIN_MESH = "mesh_max100_min006_cpg3_bl3"
PROJECT_ROOT = REPO_ROOT / "My_CFD_Project"

# Active 24-case campaign naming matrix (batch_config.py / test_case_naming.py).
CAMPAIGN_CASES = [
    (0.3, 4.0e6, "u0p3_p4M", None),
    (0.3, 6.0e6, "u0p3_p6M", None),
    (0.3, 8.0e6, "u0p3_p8M", None),
    (0.1, 4.0e6, "u0p1_p4M", SIN_MESH),
    (0.1, 6.0e6, "u0p1_p6M", SIN_MESH),
    (0.1, 8.0e6, "u0p1_p8M", SIN_MESH),
    (0.2, 4.0e6, "u0p2_p4M", SIN_MESH),
    (0.2, 6.0e6, "u0p2_p6M", SIN_MESH),
    (0.2, 8.0e6, "u0p2_p8M", SIN_MESH),
    (0.3, 4.0e6, "u0p3_p4M", SIN_MESH),
    (0.3, 6.0e6, "u0p3_p6M", SIN_MESH),
    (0.3, 8.0e6, "u0p3_p8M", SIN_MESH),
]


def legacy_as_fluent_path(path) -> str:
    """Inline copy of solver_code_260616.as_fluent_path before extraction."""
    return os.path.abspath(path).replace("\\", "/")


def legacy_normalize_path(path) -> str:
    """Inline copy of solver_code_260616.normalize_path before extraction."""
    return os.path.normcase(os.path.abspath(path))


def legacy_batch_final_paths(project_root, geo_name: str, case_name: str) -> tuple[str, str, str]:
    """Inline copy of batch_solver_sweep.py lines 166-173 before extraction."""
    target_case_folder = os.path.join(project_root, "03_Results", geo_name, case_name)
    expected_final_case = os.path.join(
        target_case_folder,
        f"{geo_name}_{case_name}_final.cas.h5",
    )
    expected_final_data = expected_final_case.replace(".cas.h5", ".dat.h5")
    return target_case_folder, expected_final_case, expected_final_data


def legacy_07_final_pair(results_root, geo_name: str, case_name: str) -> tuple[str, str, str]:
    """Inline copy of 07_batch_solver_rerun.final_pair_for_case (phase 5 target).

    Not wired in phase 1; documented equivalence when results_root is
    project_root/03_Results.
    """
    case_dir = Path(results_root) / geo_name / case_name
    final_case_file = case_dir / f"{geo_name}_{case_name}_final.cas.h5"
    final_data_file = case_dir / f"{geo_name}_{case_name}_final.dat.h5"
    return str(case_dir), str(final_case_file), str(final_data_file)


def mesh_qualified_case_name(base_name: str, mesh_name: str | None) -> str:
    if mesh_name:
        return f"{base_name}__{mesh_name}"
    return base_name


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


class TestPathToFluentStrParity:
    @pytest.mark.parametrize(
        "path_input",
        [
            PROJECT_ROOT / "03_Results" / "Sin_ST" / "u0p1_p4M",
            PROJECT_ROOT / "03_Results" / "Sin_ST" / f"u0p1_p4M__{SIN_MESH}",
            str(PROJECT_ROOT / "03_Results" / "Sin_ST" / "u0p2_p6M"),
            str(PROJECT_ROOT).replace("/", "\\") + "\\03_Results\\Sin_ST\\u0p3_p8M",
            "C:/Users/runner/case/final.cas.h5",
            "C:\\Users\\runner\\case\\final.cas.h5",
            "D:/data/u0p1_p4M__mesh_max100_min006_cpg3_bl3/setup.cas.h5",
        ],
    )
    def test_matches_legacy_as_fluent_path(self, common, path_input):
        assert common.path_to_fluent_str(path_input) == legacy_as_fluent_path(path_input)

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_campaign_final_paths(self, common, velocity, pressure, base_name, mesh_name):
        case_name = mesh_qualified_case_name(base_name, mesh_name)
        case_dir = PROJECT_ROOT / "03_Results" / "Sin_ST" / case_name
        final_case = case_dir / f"Sin_ST_{case_name}_final.cas.h5"
        assert common.path_to_fluent_str(final_case) == legacy_as_fluent_path(final_case)


class TestNormalizePathParity:
    @pytest.mark.parametrize(
        "path_input",
        [
            "/tmp/udf.c",
            Path("/tmp/udf.c"),
            "relative/subdir/file.cas.h5",
            PROJECT_ROOT / "02_UDFs" / "260612_RO_UDF.c",
        ],
    )
    def test_matches_legacy_normalize_path(self, common, path_input):
        assert common.normalize_path(path_input) == legacy_normalize_path(path_input)


class TestFinalCaseDataPathsParity:
    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_matches_batch_inline_logic(self, common, velocity, pressure, base_name, mesh_name):
        geo_name = "Sin_ST"
        case_name = mesh_qualified_case_name(base_name, mesh_name)
        project_root = str(PROJECT_ROOT)

        expected = legacy_batch_final_paths(project_root, geo_name, case_name)
        actual = common.final_case_data_paths(project_root, geo_name, case_name)
        assert actual == expected

    @pytest.mark.parametrize("velocity,pressure,base_name,mesh_name", CAMPAIGN_CASES)
    def test_matches_07_layout_when_results_root_is_under_project(
        self, common, velocity, pressure, base_name, mesh_name
    ):
        """Phase 5 note: same filenames as 07.final_pair_for_case, different root arg."""
        geo_name = "Sin_ST"
        case_name = mesh_qualified_case_name(base_name, mesh_name)
        project_root = str(PROJECT_ROOT)
        results_root = os.path.join(project_root, "03_Results")

        batch_paths = common.final_case_data_paths(project_root, geo_name, case_name)
        seven_paths = legacy_07_final_pair(results_root, geo_name, case_name)
        assert batch_paths == seven_paths


class TestRejectWindowsDrivePaths:
    def test_find_windows_drive_paths(self, common):
        assert common.find_windows_drive_paths(["C:/foo", "/home/foo", "C:\\bar"]) == [
            "C:/foo",
            "C:\\bar",
        ]

    def test_reject_raises_on_posix_host(self, common):
        with pytest.raises(ValueError, match="Unsafe path"):
            common.reject_windows_drive_paths_on_non_windows(
                [Path("C:/temp/case")],
                host_os_name="posix",
            )

    def test_reject_noop_on_nt_host(self, common):
        common.reject_windows_drive_paths_on_non_windows(
            [Path("C:/temp/case")],
            host_os_name="nt",
        )
