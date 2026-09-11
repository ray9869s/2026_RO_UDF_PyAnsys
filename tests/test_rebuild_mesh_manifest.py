"""Tests for mesh manifest rebuild helper."""

from __future__ import annotations

import hashlib
import sys

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


def test_payload_diff_ignores_one_ulp_float_drift():
    rebuild = _load_rebuild_script()
    existing = {"periodic_shift_y_m": 3.465 * 1.0e-3}
    rebuilt = {"periodic_shift_y_m": 3.465e-3}
    assert rebuild._payload_diff(existing, rebuilt) == {}


def test_payload_diff_compares_numeric_lists_with_tolerance():
    rebuild = _load_rebuild_script()
    existing = {"layer_axis_z_m": [3.465e-3 / 9, 0.0, -(3.465e-3 / 9)]}
    rebuilt = {
        "layer_axis_z_m": [
            (3.465 * 1.0e-3) / 9.0,
            0.0,
            -((3.465 * 1.0e-3) / 9.0),
        ],
    }
    assert rebuild._payload_diff(existing, rebuilt) == {}


def test_payload_diff_none_vs_number_is_a_diff():
    rebuild = _load_rebuild_script()
    diff = rebuild._payload_diff({}, {"wavelength_m": 0.001})
    assert diff == {"wavelength_m": ("<missing>", 0.001)}


_FROM_LOG_COMMON = {
    "n_buffer_in": 1,
    "n_buffer_out": 2,
    "buffer_length_in_m": 0.003465,
    "buffer_length_out_m": 0.00693,
    "n_lead_excluded": 3,
    "n_trail_excluded": 0,
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "peel_layers": 2,
    "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    "buffer_wall_labels": [
        "wall_top_buffer_in",
        "wall_top_buffer_out",
        "wall_bottom_buffer_in",
        "wall_bottom_buffer_out",
    ],
}

# D2450_a45 current layout total_length_m = 0.03465 m = 34.65 mm.
_FROM_LOG_MATCHING_EXTENTS = """
---------------- 123,456 cells were created in :  0.22 minutes
Domain extents.
  x-coordinate: min = 0.000000e+00, max = 3.465000e+01.
  y-coordinate: min = 0.000000e+00, max = 3.465000e+00.
  z-coordinate: min = 0.000000e+00, max = 7.700000e-01.
"""


def _from_log_text(*, extents=True):
    from tests.test_mesh_ledger import REAL_SURFACE_SKEWNESS_TABLE, SYNTHETIC_MESH_LOG

    text = SYNTHETIC_MESH_LOG + REAL_SURFACE_SKEWNESS_TABLE
    if extents:
        return text + _FROM_LOG_MATCHING_EXTENTS
    return text


def _write_from_log_leaf(
    monkeypatch,
    tmp_path,
    *,
    write_log=True,
    extents=True,
    write_manifest=False,
    mesh_bytes=b"from-log-mesh",
):
    from ro.paths import mesh_dir
    from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload
    from ro.manifest import write_mesh_manifest

    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    mesh_file = directory / f"{GEO_ID}_{MESH_ID}.msh.h5"
    mesh_file.write_bytes(mesh_bytes)
    if write_log:
        (directory / f"mesh_log_{MESH_ID}.txt").write_text(
            _from_log_text(extents=extents),
            encoding="utf-8",
        )
    if write_manifest:
        payload = mesh_payload()
        payload["mesh_sha256"] = hashlib.sha256(mesh_bytes).hexdigest()
        write_mesh_manifest(directory, payload)
    return directory


def test_from_log_dry_run_does_not_write(monkeypatch, tmp_path):
    rebuild = _load_rebuild_script()
    directory = _write_from_log_leaf(monkeypatch, tmp_path)
    result = rebuild.rebuild_mesh_manifest_from_log(
        directory,
        apply=False,
        common_mesh_settings=_FROM_LOG_COMMON,
    )
    assert result["status"] == "would-write"
    assert not (directory / "manifest.json").exists()
    payload = result["payload"]
    assert payload["geo_id"] == "D2450_a45"
    assert payload["n_active_cells"] == 7
    assert payload["mesh_sha256"] == hashlib.sha256(b"from-log-mesh").hexdigest()
    assert payload["created_utc"] == rebuild.FROM_LOG_UNRECOVERABLE_MARKER
    assert payload["generator_version"] == rebuild.FROM_LOG_UNRECOVERABLE_MARKER
    assert payload["inlet_profile_G"] is None
    assert result["unrecoverable"]["inlet_profile_G"] is None
    assert "registry" in result["sources"]
    assert "log_metrics" in result["sources"]
    assert "mesh_file" in result["sources"]
    assert "run_config" in result["sources"]


def test_from_log_apply_writes_valid_manifest(monkeypatch, tmp_path):
    from ro.manifest import read_mesh_manifest

    rebuild = _load_rebuild_script()
    directory = _write_from_log_leaf(monkeypatch, tmp_path)
    result = rebuild.rebuild_mesh_manifest_from_log(
        directory,
        apply=True,
        common_mesh_settings=_FROM_LOG_COMMON,
    )
    assert result["status"] == "wrote"
    payload = read_mesh_manifest(directory)
    assert payload["mesh_sha256"] == hashlib.sha256(b"from-log-mesh").hexdigest()
    assert payload["n_active_cells"] == 7
    assert payload["created_utc"] == "unrecoverable-from-log"
    assert payload["generator_version"] == "unrecoverable-from-log"
    assert payload["inlet_profile_G"] is None
    assert payload["domain_extent_x_m"] == pytest.approx(0.03465)


def test_from_log_refuses_missing_log(monkeypatch, tmp_path):
    rebuild = _load_rebuild_script()
    directory = _write_from_log_leaf(monkeypatch, tmp_path, write_log=False)
    with pytest.raises(FileNotFoundError, match="mesh log required"):
        rebuild.rebuild_mesh_manifest_from_log(
            directory,
            apply=True,
            common_mesh_settings=_FROM_LOG_COMMON,
        )
    assert not (directory / "manifest.json").exists()


def test_from_log_refuses_existing_manifest(monkeypatch, tmp_path):
    rebuild = _load_rebuild_script()
    directory = _write_from_log_leaf(monkeypatch, tmp_path, write_manifest=True)
    with pytest.raises(ManifestError, match="already exists"):
        rebuild.rebuild_mesh_manifest_from_log(
            directory,
            apply=True,
            common_mesh_settings=_FROM_LOG_COMMON,
        )


def test_from_log_refuses_when_x_extent_gate_fails(monkeypatch, tmp_path):
    rebuild = _load_rebuild_script()
    directory = _write_from_log_leaf(monkeypatch, tmp_path, extents=False)
    with pytest.raises(ManifestError, match="x-extent gate"):
        rebuild.rebuild_mesh_manifest_from_log(
            directory,
            apply=True,
            common_mesh_settings=_FROM_LOG_COMMON,
        )
    assert not (directory / "manifest.json").exists()


def test_from_log_rejects_allow_field():
    rebuild = _load_rebuild_script()
    with pytest.raises(SystemExit, match="--allow-field"):
        rebuild.main(["--from-log", "--geo-id", "D2450_a45", "--allow-field", "n_active_cells"])


def test_from_log_is_callable_when_ansys_is_absent(monkeypatch, tmp_path):
    """--from-log must not import the meshing worker (or any ansys package)."""
    monkeypatch.setitem(sys.modules, "ansys", None)
    monkeypatch.setitem(sys.modules, "ansys.fluent", None)
    monkeypatch.setitem(sys.modules, "ansys.fluent.core", None)
    rebuild = load_module(
        "rebuild_mesh_manifest_ansys_absent",
        SCRIPTS_DIR / "rebuild_mesh_manifest.py",
    )
    directory = _write_from_log_leaf(monkeypatch, tmp_path)
    result = rebuild.rebuild_mesh_manifest_from_log(
        directory,
        apply=False,
        common_mesh_settings=_FROM_LOG_COMMON,
    )
    assert result["status"] == "would-write"
    assert result["payload"]["geo_id"] == "D2450_a45"
    assert result["payload"]["created_utc"] == rebuild.FROM_LOG_UNRECOVERABLE_MARKER

