"""MFBO pillar geo_ids (MFP_*) and unchanged campaign geometry entries."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.campaign_geometry import (
    _PILLAR_BLOCKED_GEOMETRIC,
    _PILLAR_HAS_HOLE,
    _PILLAR_UNIT_CELL_M,
    _pillar_dp_token,
    geometry_parameters_for_geo_id,
)
from ro.geometry_registry import (
    MFBO_PILLAR_GEO_ID_RE,
    PILLAR_REGISTRY_FIELDS,
    family_for_known_geo_id,
    format_mfbo_pillar_geo_id,
    pillar_registry_entry,
    require_mfp_geometry_sha256,
    require_pillar_cad_geo_id,
    resolve_geometry_parameters,
    validate_known_geo_id,
)
from ro.manifest_validation import validate_spacer_wall_zones
from ro.paths import GEO_ID_RE, _GEO_ID_FORBIDDEN, geometry_dir
from ro.solver_common import sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "campaign_geometry_parameters.json"

_PLAIN_ZONES = [
    "wall_spacer_filament",
    "wall_spacer_pillar",
    "wall_spacer_buffer",
]
_BORE_ZONES = [
    "wall_spacer_filament",
    "wall_spacer_pillar",
    "wall_spacer_hole",
    "wall_spacer_buffer",
]


def _write_meta(root, geo_id, *, d_p_mm, d_h_mm, d_f_mm, registry, pmdb_sha256):
    path = root / "geometries" / "pillar" / geo_id / f"{geo_id}_meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "geo_id": geo_id,
                "inputs": {
                    "d_p_mm": d_p_mm,
                    "d_h_mm": d_h_mm,
                    "d_f_mm": d_f_mm,
                    "geo_id": geo_id,
                },
                "registry": registry,
                "pmdb_sha256": pmdb_sha256,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_mfbo_pillar_geo_id_satisfies_path_constraints():
    geo_id = format_mfbo_pillar_geo_id(0.6, 0.15, 0.4)
    assert geo_id == "MFP_d0600_h0150_f0400"
    assert MFBO_PILLAR_GEO_ID_RE.pattern == r"^MFP_d(\d{4})_h(\d{4})_f(\d{4})$"
    assert MFBO_PILLAR_GEO_ID_RE.fullmatch(geo_id)
    assert GEO_ID_RE.fullmatch(geo_id)
    assert _GEO_ID_FORBIDDEN.search(geo_id) is None
    assert len(geo_id) <= 64
    assert format_mfbo_pillar_geo_id(1.0, 0.0, 0.4) == "MFP_d1000_h0000_f0400"


def test_campaign_geometry_parameters_match_prechange_snapshot():
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert list(snapshot) == list(CAMPAIGN_GEO_ID_ORDER)
    assert len(snapshot) == 31
    for geo_id, expected in snapshot.items():
        assert geometry_parameters_for_geo_id(geo_id) == expected


def test_campaign_pillar_blocked_fraction_is_near_the_table():
    cell_area = _PILLAR_UNIT_CELL_M * _PILLAR_UNIT_CELL_M / 2.0
    for geo_id in _PILLAR_HAS_HOLE:
        d_p_mm = int(geo_id.split("_")[1][1:]) / 100.0
        d_p_m = d_p_mm * 1.0e-3
        computed = math.pi * (d_p_m / 2.0) ** 2 / cell_area
        table = _PILLAR_BLOCKED_GEOMETRIC[_pillar_dp_token(geo_id)]
        assert abs(computed - table) < 5e-4
        stored = geometry_parameters_for_geo_id(geo_id)[
            "membrane_blocked_area_frac_geometric"
        ]
        assert stored == table


def test_mfp_parameters_require_meta_under_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    d_p_m, d_h_m, d_f_m = 0.001, 0.0003, 0.0004
    registry = pillar_registry_entry(geo_id, d_p_m, d_h_m, d_f_m)
    assert tuple(registry) == PILLAR_REGISTRY_FIELDS
    with pytest.raises(FileNotFoundError, match="meta not found"):
        resolve_geometry_parameters(geo_id)
    _write_meta(
        tmp_path,
        geo_id,
        d_p_mm=1.0,
        d_h_mm=0.3,
        d_f_mm=0.4,
        registry=registry,
        pmdb_sha256="abc",
    )
    assert resolve_geometry_parameters(geo_id) == registry
    assert geometry_parameters_for_geo_id(geo_id) == registry
    assert family_for_known_geo_id(geo_id) == "pillar"
    validate_known_geo_id(geo_id)
    assert geometry_dir("pillar", geo_id) == (
        tmp_path / "geometries" / "pillar" / geo_id
    )
    with pytest.raises(ValueError, match="not in the campaign whitelist"):
        family_for_geo_id(geo_id)


def test_mfp_rejected_on_name_meta_mismatch_and_bad_format(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.0, 0.4)
    registry = pillar_registry_entry(geo_id, 0.001, 0.0, 0.0004)
    _write_meta(
        tmp_path,
        geo_id,
        d_p_mm=0.8,
        d_h_mm=0.0,
        d_f_mm=0.4,
        registry=registry,
        pmdb_sha256="abc",
    )
    with pytest.raises(ValueError, match="encodes d_p_mm"):
        resolve_geometry_parameters(geo_id)
    with pytest.raises(ValueError, match="not a campaign case"):
        validate_known_geo_id("MFP_d100_h0000_f0400")
    with pytest.raises(ValueError, match="Unknown campaign geo_id"):
        resolve_geometry_parameters("MFP_d100_h0000_f0400")
    with pytest.raises(ValueError, match="not a campaign case"):
        validate_known_geo_id("P_p100_h3O")
    with pytest.raises(ValueError, match="Unknown campaign geo_id"):
        geometry_parameters_for_geo_id("P_p100_h3O")


def test_mfp_sha256_mismatch_raises_and_campaign_dsco_is_unchecked(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4)
    registry = pillar_registry_entry(geo_id, 0.0008, 0.00015, 0.0004)
    geometry = tmp_path / "body.pmdb"
    geometry.write_bytes(b"pmdb-bytes")
    digest = sha256_file(geometry)
    _write_meta(
        tmp_path,
        geo_id,
        d_p_mm=0.8,
        d_h_mm=0.15,
        d_f_mm=0.4,
        registry=registry,
        pmdb_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="sha256 mismatch"):
        require_mfp_geometry_sha256(geo_id, geometry)
    _write_meta(
        tmp_path,
        geo_id,
        d_p_mm=0.8,
        d_h_mm=0.15,
        d_f_mm=0.4,
        registry=registry,
        pmdb_sha256=digest,
    )
    require_mfp_geometry_sha256(geo_id, geometry)
    require_mfp_geometry_sha256("P_p100_h30", tmp_path / "missing.dsco")


def test_mfp_bore_check_uses_registry_both_ways(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    solid = format_mfbo_pillar_geo_id(1.0, 0.0, 0.4)
    bored = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    _write_meta(
        tmp_path,
        solid,
        d_p_mm=1.0,
        d_h_mm=0.0,
        d_f_mm=0.4,
        registry=pillar_registry_entry(solid, 0.001, 0.0, 0.0004),
        pmdb_sha256="abc",
    )
    _write_meta(
        tmp_path,
        bored,
        d_p_mm=1.0,
        d_h_mm=0.3,
        d_f_mm=0.4,
        registry=pillar_registry_entry(bored, 0.001, 0.0003, 0.0004),
        pmdb_sha256="abc",
    )
    validate_spacer_wall_zones(_PLAIN_ZONES, _PLAIN_ZONES, geo_id=solid)
    validate_spacer_wall_zones(_BORE_ZONES, _BORE_ZONES, geo_id=bored)
    with pytest.raises(Exception, match="declares wall_spacer_hole"):
        validate_spacer_wall_zones(_BORE_ZONES, _BORE_ZONES, geo_id=solid)
    with pytest.raises(Exception, match="lacks wall_spacer_hole"):
        validate_spacer_wall_zones(_PLAIN_ZONES, _PLAIN_ZONES, geo_id=bored)


def test_pillar_cad_refuses_geo_id_that_is_not_mfp_or_campaign(tmp_path):
    from ro.pillar_cad import _meta_payload, generate_pillar_cad
    import ro.pillar_cad as pillar_cad

    require_pillar_cad_geo_id("P_p100_h30", 1.0, 0.3, 0.4)
    geo_id = format_mfbo_pillar_geo_id(1.0, 0.3, 0.4)
    require_pillar_cad_geo_id(geo_id, 1.0, 0.3, 0.4)
    with pytest.raises(ValueError, match="campaign pillar"):
        require_pillar_cad_geo_id(geo_id, 0.8, 0.3, 0.4)
    with pytest.raises(ValueError, match="campaign pillar"):
        generate_pillar_cad(
            d_p_mm=1.0,
            d_h_mm=0.0,
            d_f_mm=0.4,
            geo_id="not_a_pillar",
            out_dir=tmp_path,
        )

    pillar_cad.pyansys_geometry = SimpleNamespace(__version__="0.15.5")
    registry = pillar_registry_entry(geo_id, 0.001, 0.0003, 0.0004)
    layout = {
        "a_m": 0.003465,
        "h_m": 0.00077,
        "filament_d_m": 0.0004,
        "periodic_shift_y_mm": 3.465,
        "n_active": 7,
        "n_buffer_in": 1,
        "n_buffer_out": 1,
        "x_active_0": 0.0,
        "x_active_1": 1.0,
        "x_outlet": 2.0,
        "y_min": 0.0,
        "y_max": 1.0,
        "z_min": 0.0,
        "z_max": 1.0,
    }
    meta = _meta_payload(
        geo_id=geo_id,
        d_p_mm=1.0,
        d_h_mm=0.3,
        d_f_mm=0.4,
        layout=layout,
        d_p_m=0.001,
        d_h_m=0.0003,
        nodes=[(0, 0, 0)],
        line_count=4,
        counts={"wall_membrane_top": 1},
        backend_version="test",
        registry=registry,
        pmdb_sha256="deadbeef",
    )
    assert meta["registry"] == registry
    assert meta["pmdb_sha256"] == "deadbeef"
    assert meta["face_counts"] == {"wall_membrane_top": 1}
