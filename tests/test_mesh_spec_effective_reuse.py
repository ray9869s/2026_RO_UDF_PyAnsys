"""Mesh reuse compares the effective spec settings, not the whole-file hash."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module, load_solver_code
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.mesh_spec import (
    changes_for_settings,
    default_spec_path,
    effective_mesh_settings,
    effective_settings_sha256,
    family_settings,
    geometry_membrane_face_size,
    legacy_manifest_mismatches,
    load_mesh_spec,
    provenance_fields,
    spec_file_sha256,
)

driver = load_module("mesh_study_case_effective", SCRIPTS_DIR / "mfbo" / "mesh_study_case.py")

# configs/mesh_spec_v3.json at 7f4ad9b, the spec the 27 existing v3 meshes
# were made with (byte for byte, so its hash is the recorded one).
SPEC_7F4AD9B = (
    "{\n"
    '  "defaults": {\n'
    '    "m_max": 0.035,\n'
    '    "m_min": 0.006,\n'
    '    "cpg": 5,\n'
    '    "membrane_bl_y1_um": 2.0,\n'
    '    "membrane_bl_offset_method": "last-ratio",\n'
    '    "bulk_max_size_mm": 0.034,\n'
    '    "peel": 2\n'
    "  },\n"
    '  "families": {\n'
    '    "diamond": {},\n'
    '    "empty": {},\n'
    '    "ml": {},\n'
    '    "pillar": {},\n'
    '    "sin": {}\n'
    "  }\n"
    "}\n"
)
_HEAD = "a" * 40
GEO = "REF_empty"
FAMILY = "empty"


def _old_spec(tmp_path: Path) -> Path:
    path = tmp_path / "mesh_spec_v3_7f4ad9b.json"
    path.write_bytes(SPEC_7F4AD9B.encode("utf-8"))
    return path


def test_spec_changes_since_7f4ad9b_leave_every_campaign_geometry_unchanged(tmp_path):
    old_path = _old_spec(tmp_path)
    old, new = load_mesh_spec(old_path), load_mesh_spec(default_spec_path())
    assert spec_file_sha256(old_path) != spec_file_sha256(default_spec_path())
    for geo_id in CAMPAIGN_GEO_ID_ORDER:
        family = family_for_geo_id(geo_id)
        assert changes_for_settings(family_settings(old, family)) == changes_for_settings(
            family_settings(new, family)
        ), geo_id
        assert geometry_membrane_face_size(new, geo_id) is None
        assert effective_mesh_settings(old, family, geo_id) == effective_mesh_settings(
            new, family, geo_id
        ), geo_id


def _overrides(spec_path: Path) -> dict:
    changes = driver.collect_overrides(
        m_max=0.035, m_min=0.006, m_cpg=5, bl_layers=4, bl_height_factor=None,
        spacer_bl_layers=None, local_proximity=None, local_face_size=None,
        membrane_bl_y1_um=2.0, membrane_bl_layers=None, bulk_max_size_mm=0.034,
        peel_layers=2, max_aspect_ratio=None,
    )
    _case, template, _retries = driver.load_mesh_template(GEO)
    overrides = driver.apply_study_overrides(template, changes)
    overrides.update(provenance_fields(spec_path, git_head=_HEAD, family=FAMILY, geo_id=GEO))
    return overrides


def _plant(tmp_path: Path, overrides: dict, **fields) -> tuple[Path, Path, str]:
    geometry = tmp_path / f"{GEO}.pmdb"
    geometry.write_bytes(b"pmdb")
    digest = driver.sha256_file(geometry)
    leaf = tmp_path / "mesh"
    leaf.mkdir()
    manifest = {
        "max_size_mm": overrides["m_max"],
        "min_size_mm": overrides["m_min"],
        "cpg": overrides["m_cpg"],
        "bl": 4,
        "peel": overrides["peel_layers"],
        "bulk_max_size_mm": overrides["bulk_max_size_mm"],
        "membrane_bl_y1": overrides["membrane_bl_y1"],
        "spacer_boundary_layers": "skipped",
        "geometry_sha256": digest,
        "git_head": _HEAD,
    }
    manifest.update(fields)
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (leaf / "mesh_run_record.json").write_text(json.dumps({"status": "SUCCESS"}), encoding="utf-8")
    return leaf, geometry, digest


def _effective(spec_path=None):
    spec = load_mesh_spec(spec_path or default_spec_path())
    return effective_mesh_settings(spec, FAMILY, GEO)


def test_old_manifest_with_unchanged_effective_spec_is_reused(tmp_path, capsys):
    overrides = _overrides(default_spec_path())
    old_hash = spec_file_sha256(_old_spec(tmp_path))
    leaf, geometry, digest = _plant(tmp_path, overrides, mesh_spec_sha256=old_hash)
    manifest = driver.require_reusable_mesh(
        leaf, overrides, geometry, digest, spec_effective=_effective()
    )
    out = capsys.readouterr().out
    assert "accepted on effective parameters" in out
    assert old_hash in out
    # The solve records the mesh's hash, so the solver's provenance check passes.
    provenance = driver.run_spec_provenance(
        {k: overrides[k] for k in ("mesh_spec_sha256", "mesh_spec_effective_sha256", "git_head")},
        manifest,
    )
    assert provenance["mesh_spec_sha256"] == old_hash
    solver = load_solver_code()
    base = {}
    solver._stamp_campaign_provenance(base, SimpleNamespace(**provenance), manifest)
    assert base["mesh_spec_sha256"] == old_hash


def test_old_manifest_is_refused_when_an_effective_default_changed(tmp_path):
    overrides = _overrides(default_spec_path())
    old_hash = spec_file_sha256(_old_spec(tmp_path))
    leaf, geometry, digest = _plant(tmp_path, overrides, mesh_spec_sha256=old_hash)
    changed = json.loads(default_spec_path().read_text(encoding="utf-8"))
    changed["defaults"]["bulk_max_size_mm"] = 0.030
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match=r"effective parameters differ: bulk_max_size_mm"):
        driver.require_reusable_mesh(
            leaf, overrides, geometry, digest, spec_effective=_effective(changed_path)
        )
    # A per-geometry face size for this geometry also changes its mesh.
    changed = json.loads(default_spec_path().read_text(encoding="utf-8"))
    changed["geometries"] = {GEO: {"membrane_face_size_mm": 0.02}}
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="local_face_size"):
        driver.require_reusable_mesh(
            leaf, overrides, geometry, digest, spec_effective=_effective(changed_path)
        )
    # Without the resolved settings the whole-file hash still decides.
    with pytest.raises(ValueError, match="mesh_spec_sha256: recorded"):
        driver.require_reusable_mesh(leaf, overrides, geometry, digest)


def test_recorded_effective_fingerprint_decides_reuse(tmp_path):
    overrides = _overrides(default_spec_path())
    leaf, geometry, digest = _plant(
        tmp_path,
        overrides,
        mesh_spec_sha256="b" * 64,
        mesh_spec_effective_sha256=overrides["mesh_spec_effective_sha256"],
    )
    driver.require_reusable_mesh(leaf, overrides, geometry, digest, spec_effective=_effective())
    (leaf / "manifest.json").write_text(
        json.dumps({
            **json.loads((leaf / "manifest.json").read_text(encoding="utf-8")),
            "mesh_spec_effective_sha256": "c" * 64,
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mesh_spec_effective_sha256: recorded"):
        driver.require_reusable_mesh(
            leaf, overrides, geometry, digest, spec_effective=_effective()
        )


def test_effective_fingerprint_ignores_other_geometries_and_is_stamped(tmp_path):
    spec = json.loads(default_spec_path().read_text(encoding="utf-8"))
    spec["geometries"] = {"D2450_a45": {"membrane_face_size_mm": 0.02}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    here = provenance_fields(path, git_head=_HEAD, family=FAMILY, geo_id=GEO)
    current = provenance_fields(default_spec_path(), git_head=_HEAD, family=FAMILY, geo_id=GEO)
    assert here["mesh_spec_sha256"] != current["mesh_spec_sha256"]
    assert here["mesh_spec_effective_sha256"] == current["mesh_spec_effective_sha256"]
    assert current["mesh_spec_effective_sha256"] == effective_settings_sha256(_effective())
    other = provenance_fields(path, git_head=_HEAD, family="diamond", geo_id="D2450_a45")
    assert other["mesh_spec_effective_sha256"] != current["mesh_spec_effective_sha256"]
    assert legacy_manifest_mismatches({}, _effective())  # nothing recorded -> refuse

    from ro.manifest import ManifestError, _optional_provenance

    _optional_provenance({"mesh_spec_effective_sha256": "d" * 64}, "Mesh")
    with pytest.raises(ManifestError, match="mesh_spec_effective_sha256"):
        _optional_provenance({"mesh_spec_effective_sha256": None}, "Run")


def test_solver_accepts_a_whole_file_difference_only_with_the_same_fingerprint():
    solver = load_solver_code()
    effective = "e" * 64
    base = {}
    solver._stamp_campaign_provenance(
        base,
        SimpleNamespace(mesh_spec_sha256="a" * 64, mesh_spec_effective_sha256=effective, git_head=_HEAD),
        {"mesh_spec_sha256": "b" * 64, "mesh_spec_effective_sha256": effective},
    )
    assert base["mesh_spec_sha256"] == "b" * 64
    assert base["mesh_spec_effective_sha256"] == effective
    with pytest.raises(ValueError, match="mesh_spec_sha256"):
        solver._stamp_campaign_provenance(
            {},
            SimpleNamespace(mesh_spec_sha256="a" * 64, mesh_spec_effective_sha256="f" * 64, git_head=_HEAD),
            {"mesh_spec_sha256": "b" * 64, "mesh_spec_effective_sha256": effective},
        )
    with pytest.raises(ValueError, match="mesh_spec_sha256"):
        solver._stamp_campaign_provenance(
            {},
            SimpleNamespace(mesh_spec_sha256="a" * 64, git_head=_HEAD),
            {"mesh_spec_sha256": "b" * 64},
        )


def test_new_mesh_manifest_records_the_fingerprint():
    from helpers import load_run_config, populate_valid_meshing_config
    from ro.mesh_manifest_payload import build_mesh_manifest_payload

    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.m_min = 0.006
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2"
    cfg.mesh_spec_sha256 = "1" * 64
    cfg.mesh_spec_effective_sha256 = "2" * 64
    cfg.git_head = _HEAD
    payload = build_mesh_manifest_payload(
        cfg,
        {"min_orthogonal_quality": 0.1, "max_aspect_ratio": 40.0, "max_skewness": 0.5,
         "skewed_face_fraction": 0.0, "cell_count": 100},
        "c" * 64,
        created_utc="2026-11-19T00:00:00Z",
    )
    assert payload["mesh_spec_effective_sha256"] == "2" * 64
