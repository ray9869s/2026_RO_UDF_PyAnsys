"""Parity tests for _solver_common.resolve_input_mode (phase 3)."""

from __future__ import annotations

from typing import Any

import pytest

from helpers import load_solver_common

RESTART_CASE = "/data/cases/u0p2_p6M_final.cas.h5"
RESTART_DATA = "/data/cases/u0p2_p6M_final.dat.h5"


def legacy_resolve_input_mode(case_settings: dict[str, Any]) -> tuple[str, Any, Any]:
    """Inline copy of batch_solver_sweep.resolve_input_mode before extraction."""
    restart_case = case_settings.get("restart_from_case_file")
    restart_data = case_settings.get("restart_from_data_file")

    has_restart_case = restart_case is not None
    has_restart_data = restart_data is not None

    if has_restart_case != has_restart_data:
        raise ValueError(
            "restart_from_case_file and restart_from_data_file must be provided together."
        )

    if has_restart_case:
        if not str(restart_case).strip() or not str(restart_data).strip():
            raise ValueError(
                "restart_from_case_file and restart_from_data_file must be non-empty paths."
            )
        return "restart_continuation", restart_case, restart_data

    return "mesh_initialization", None, None


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


class TestResolveInputModeParity:
    def test_mesh_initialization_when_restart_keys_absent(self, common):
        settings = {"geo_name": "Sin_ST", "case_name": "u0p1_p4M"}
        assert common.resolve_input_mode(settings) == legacy_resolve_input_mode(settings)
        assert common.resolve_input_mode(settings) == ("mesh_initialization", None, None)

    @pytest.mark.parametrize(
        "settings",
        [
            {
                "restart_from_case_file": RESTART_CASE,
                "restart_from_data_file": RESTART_DATA,
            },
            {
                "restart_from_case_file": RESTART_CASE,
                "restart_from_data_file": RESTART_DATA,
                "geo_name": "Sin_ST",
            },
        ],
    )
    def test_restart_continuation_with_paired_paths(self, common, settings):
        assert common.resolve_input_mode(settings) == legacy_resolve_input_mode(settings)
        mode, case_path, data_path = common.resolve_input_mode(settings)
        assert mode == "restart_continuation"
        assert case_path == RESTART_CASE
        assert data_path == RESTART_DATA

    @pytest.mark.parametrize(
        ("settings", "missing_key"),
        [
            ({"restart_from_case_file": RESTART_CASE}, "restart_from_data_file"),
            ({"restart_from_data_file": RESTART_DATA}, "restart_from_case_file"),
        ],
    )
    def test_mismatched_pair_raises(self, common, settings, missing_key):
        with pytest.raises(ValueError, match="must be provided together"):
            legacy_resolve_input_mode(settings)
        with pytest.raises(ValueError, match="must be provided together"):
            common.resolve_input_mode(settings)

    @pytest.mark.parametrize(
        "settings",
        [
            {"restart_from_case_file": "", "restart_from_data_file": RESTART_DATA},
            {"restart_from_case_file": RESTART_CASE, "restart_from_data_file": ""},
            {"restart_from_case_file": "   ", "restart_from_data_file": RESTART_DATA},
            {"restart_from_case_file": RESTART_CASE, "restart_from_data_file": "   "},
        ],
    )
    def test_empty_restart_path_raises(self, common, settings):
        with pytest.raises(ValueError, match="must be non-empty paths"):
            legacy_resolve_input_mode(settings)
        with pytest.raises(ValueError, match="must be non-empty paths"):
            common.resolve_input_mode(settings)
