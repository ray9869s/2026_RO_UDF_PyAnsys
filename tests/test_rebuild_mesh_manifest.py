"""Tests for mesh manifest rebuild helper."""

from __future__ import annotations

import pytest

from ro.campaign_geometry import geometry_parameters_for_geo_id
from ro.manifest import ManifestError

from helpers import SCRIPTS_DIR, load_module


def _load_rebuild_script():
    return load_module(
        "rebuild_mesh_manifest_under_test",
        SCRIPTS_DIR / "rebuild_mesh_manifest.py",
    )


def test_sinusoidal_geometry_entry_persists_wavelength_and_amplitude():
    geometry = geometry_parameters_for_geo_id("S_a144_l1733")
    assert geometry["wavelength_m"] == pytest.approx(3.465e-3 / 2.0)
    assert geometry["amplitude_m"] == pytest.approx(3.465e-3 / 24.0)
    assert geometry["curvature_margin"] == pytest.approx(1.3165, rel=1e-3)


def test_merge_geometry_into_run_manifest_leaves_non_sin_wavelength_null():
    from ro.campaign_geometry import merge_geometry_into_run_manifest

    merged = merge_geometry_into_run_manifest(
        {"family": "diamond", "geo_id": "D2450_a45"},
        "D2450_a45",
        mesh_id="max085_min006_cpg5_bl4_peel2",
    )
    assert "wavelength_m" not in merged
    assert "amplitude_m" not in merged


def test_enforce_diff_allowlist_accepts_listed_fields():
    rebuild = _load_rebuild_script()
    rebuild.enforce_diff_allowlist(
        {"wavelength_m": (None, 0.001)},
        ["wavelength_m"],
    )


def test_enforce_diff_allowlist_rejects_unlisted_fields():
    rebuild = _load_rebuild_script()
    with pytest.raises(ManifestError, match="outside --allow-field allowlist"):
        rebuild.enforce_diff_allowlist(
            {
                "wavelength_m": (None, 0.001),
                "bridge_radius_m": (1.0e-4, 1.1e-4),
            },
            ["wavelength_m"],
        )


def test_enforce_diff_allowlist_requires_names_when_diff_nonempty():
    rebuild = _load_rebuild_script()
    with pytest.raises(ManifestError, match="no --allow-field names"):
        rebuild.enforce_diff_allowlist(
            {"wavelength_m": (None, 0.001)},
            [],
        )
