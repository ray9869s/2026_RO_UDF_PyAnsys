"""Per-geometry evaluation window and the two window LMH bases."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ro.evaluation_window_table import (
    LEGACY_WINDOW_TABLE_VERSION,
    apply_selection_to_config,
    legacy_3_cell_selection,
    select_evaluation_window,
    selection_from_table,
    window_lmh,
)
from ro.lmh_metrics import MS_TO_LMH

MESH_ID = "max085_min006_cpg5_bl4_peel2"
DX = 0.003465


def _table(path, records):
    path.write_text(json.dumps(records), encoding="utf-8")


def _record(**overrides):
    n_lead = overrides.pop("n_lead_excluded", 2)
    n_active = overrides.pop("n_active", 7)
    payload = {
        "n_lead_excluded": n_lead,
        "excluded_length_m": n_lead * DX,
        "window_length_m": (n_active - n_lead) * DX,
        "mesh_id": MESH_ID,
        "date": "2026-10-09",
        "source_data_root": "C:/ro_data",
        "short_window": False,
    }
    payload.update(overrides)
    return payload


def test_missing_geo_id_is_an_error(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(path, {"D2450_a30": _record()})
    with pytest.raises(KeyError, match="D2450_a45"):
        selection_from_table(
            path,
            "D2450_a45",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
        )


def test_missing_table_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        select_evaluation_window(
            geo_id="D2450_a45",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
            legacy_3_cell_window=False,
            table_path=tmp_path / "absent.json",
        )


def test_legacy_flag_does_not_read_the_table(tmp_path):
    selection = select_evaluation_window(
        geo_id="D2450_a45",
        mesh_id=MESH_ID,
        n_active=7,
        cell_length_x_m=DX,
        legacy_3_cell_window=True,
        table_path=tmp_path / "absent.json",
    )
    assert selection.n_lead_excluded == 3
    assert selection.window_table_version == LEGACY_WINDOW_TABLE_VERSION
    assert selection.window_source == "legacy-3-cell"
    assert selection.excluded_length_m == pytest.approx(3 * DX)
    assert selection.window_length_m == pytest.approx(4 * DX)
    cfg = SimpleNamespace(
        n_lead_excluded=None,
        n_trail_excluded=None,
        n_inlet_spacer_cells_excluded=None,
    )
    apply_selection_to_config(cfg, selection)
    assert cfg.n_lead_excluded == 3
    assert cfg.n_trail_excluded == 0
    assert cfg.n_inlet_spacer_cells_excluded == 3


def test_length_and_mesh_mismatches_raise(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(path, {"D2450_a45": _record(excluded_length_m=0.001)})
    with pytest.raises(ValueError, match="excluded_length_m"):
        selection_from_table(
            path,
            "D2450_a45",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
        )
    _table(
        path,
        {"D2450_a45": _record(mesh_id="max060_min006_cpg5_bl4_peel2")},
    )
    with pytest.raises(ValueError, match="mesh_id"):
        selection_from_table(
            path,
            "D2450_a45",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
        )


def test_table_row_sets_the_window_and_version(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(path, {"D2450_a45": _record(n_lead_excluded=2)})
    selection = selection_from_table(
        path,
        "D2450_a45",
        mesh_id=MESH_ID,
        n_active=7,
        cell_length_x_m=DX,
    )
    assert selection.n_lead_excluded == 2
    assert selection.window_table_version == "2026-10-09"
    assert selection.window_source == "geo_id"
    assert selection.manifest_fields()["window_length_m"] == pytest.approx(5 * DX)


def test_window_lmh_uses_both_area_bases():
    fluxes = [1e-6, 2e-6, 3e-6]
    areas = [0.01, 0.01, 0.02]
    flux_area = 1e-6 * 0.01 + 2e-6 * 0.01 + 3e-6 * 0.02
    exposed, module = window_lmh(fluxes, areas, 0.003, 0.002)
    assert exposed == pytest.approx(flux_area / 0.04 * MS_TO_LMH)
    assert module == pytest.approx(flux_area / (2 * 0.003 * 0.002) * MS_TO_LMH)
    assert exposed == pytest.approx(8.1)
    assert module == pytest.approx(27000.0)


def test_window_lmh_rejects_an_empty_window_and_a_nonpositive_area():
    with pytest.raises(ValueError, match="window cell"):
        window_lmh([], [], 0.003, 0.002)
    with pytest.raises(ValueError, match="A_mem"):
        window_lmh([1e-6], [0.0], 0.003, 0.002)


def test_legacy_window_rejects_a_short_active_span():
    with pytest.raises(ValueError, match="n_active > 3"):
        legacy_3_cell_selection(3, DX)


def _family_default():
    return {
        "n_lead_excluded": 3,
        "excluded_length_m": 3 * DX,
        "basis": "all 9 campaign pillars develop within 3.5 mm",
        "date": "2026-10-09",
    }


def test_registered_mfbo_pillar_uses_the_family_default(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(
        path,
        {
            "P_p100_h30": _record(n_lead_excluded=2),
            "family_defaults": {"pillar": _family_default()},
        },
    )
    selection = selection_from_table(
        path,
        "MFP_d0800_h0000_f0400",
        mesh_id="not-the-campaign-mesh",
        n_active=7,
        cell_length_x_m=DX,
    )
    assert selection.window_source == "family_default"
    assert selection.n_lead_excluded == 3
    assert selection.excluded_length_m == pytest.approx(3 * DX)
    assert selection.window_length_m == pytest.approx(4 * DX)
    assert selection.manifest_fields()["window_source"] == "family_default"
    campaign = selection_from_table(
        path,
        "P_p100_h30",
        mesh_id=MESH_ID,
        n_active=7,
        cell_length_x_m=DX,
    )
    assert campaign.window_source == "geo_id"
    assert campaign.n_lead_excluded == 2


def test_campaign_pillar_missing_from_the_table_does_not_use_the_default(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(path, {"family_defaults": {"pillar": _family_default()}})
    with pytest.raises(KeyError, match="P_p100_h30"):
        selection_from_table(
            path,
            "P_p100_h30",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
        )


def test_family_default_refuses_diamond_and_a_mismatched_pitch(tmp_path):
    path = tmp_path / "evaluation_window_table.json"
    _table(path, {"family_defaults": {"diamond": _family_default()}})
    with pytest.raises(ValueError, match="diamond"):
        selection_from_table(
            path,
            "D2450_a45",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=DX,
        )
    _table(path, {"family_defaults": {"pillar": _family_default()}})
    with pytest.raises(ValueError, match="excluded_length_m"):
        selection_from_table(
            path,
            "MFP_d0800_h0000_f0400",
            mesh_id=MESH_ID,
            n_active=7,
            cell_length_x_m=0.001,
        )
