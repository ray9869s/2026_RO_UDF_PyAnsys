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
