"""Guards for the MFBO parity-mesh driver. Does not launch Fluent."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module


def load_parity():
    return load_module(
        "run_parity_mesh_under_test",
        SCRIPTS_DIR / "mfbo" / "run_parity_mesh.py",
    )


def test_data_root_refuses_production_tree(tmp_path):
    parity = load_parity()
    for value in (
        "C:/ro_data",
        "C:/ro_data/meshes",
        r"C:\ro_data\geometries",
        "c:/RO_DATA/meshes/pillar",
        "/mnt/c/ro_data",
        "/mnt/c/ro_data/meshes",
    ):
        with pytest.raises(ValueError, match="production data root"):
            parity.resolve_data_root(value)
    resolved = parity.resolve_data_root(str(tmp_path))
    assert resolved == tmp_path.resolve()


def test_copy_refuses_overwrite_and_checks_sha256(tmp_path):
    parity = load_parity()
    source = tmp_path / "source.dsco"
    dest = tmp_path / "out" / "P_p100_h30.dsco"
    source.write_bytes(b"cad-bytes")

    digest = parity.copy_geometry_file(source, dest)

    assert source.is_file()
    assert dest.read_bytes() == b"cad-bytes"
    assert digest == parity.assert_sha256_equal(source, dest)
    with pytest.raises(FileExistsError, match="overwrite"):
        parity.copy_geometry_file(source, dest)

    other = tmp_path / "other.dsco"
    other.write_bytes(b"different")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        parity.assert_sha256_equal(source, other)


def test_settings_comparison_maps_manifest_fields_and_raises():
    parity = load_parity()
    manifest = {
        "family": "pillar",
        "geo_id": "P_p100_h30",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "max_size_mm": 0.085,
        "periodic_shift_y_m": 0.003465,
        "membrane_wall_base_names": ["wall_top_mem", "wall_bottom_mem"],
        "cell_count": 1048280,
    }
    overrides = {
        "family": "pillar",
        "geo_id": "P_p100_h30",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "m_max": 0.085,
        "periodic_shift_y": 3.465,
        "active_membrane_wall_labels": ["wall_top_mem", "wall_bottom_mem"],
    }

    rows = parity.compare_mesh_settings(manifest, overrides)
    by_key = {row["manifest_key"]: row for row in rows}
    assert by_key["max_size_mm"]["override_key"] == "m_max"
    assert by_key["periodic_shift_y_m"]["override_key"] == "periodic_shift_y"
    assert by_key["periodic_shift_y_m"]["status"] == "ok"
    assert by_key["membrane_wall_base_names"]["override_key"] == (
        "active_membrane_wall_labels"
    )
    assert "cell_count" not in by_key

    bad = dict(overrides)
    bad["m_max"] = 0.060
    with pytest.raises(ValueError, match="max_size_mm"):
        parity.compare_mesh_settings(manifest, bad)

    generated = dict(overrides)
    generated["geo_id"] = "P_p100_h30_gen"
    generated["geometry_suffix"] = ".pmdb"
    staged = parity.compare_mesh_settings(
        manifest,
        generated,
        allowed_manifest_keys={"geo_id"},
    )
    geo_row = next(row for row in staged if row["manifest_key"] == "geo_id")
    assert geo_row["status"] == "stage override"
    generated["m_max"] = 0.060
    with pytest.raises(ValueError, match="max_size_mm"):
        parity.compare_mesh_settings(
            manifest,
            generated,
            allowed_manifest_keys={"geo_id"},
        )
