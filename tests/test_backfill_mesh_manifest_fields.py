"""Mesh-manifest backfill: registry geometry vs log-measured extents."""

from __future__ import annotations

import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.domain_layout import require_mesh_manifest_x_extent_matches_layout
from ro.manifest import read_mesh_manifest
from ro.paths import mesh_dir
from tests.test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload

backfill = load_module(
    "backfill_mesh_manifest_fields_under_test",
    SCRIPTS_DIR / "backfill_mesh_manifest_fields.py",
)

_MESH_CHECK_LOG = """
---------------- 2106112 cells were created in :  1.80 minutes
Domain extents.
  x-coordinate: min = 0.000000e+00, max = 1.732500e+01.
  y-coordinate: min = -1.732500e+00, max = 1.732500e+00.
  z-coordinate: min = -3.853586e-01, max = 3.853551e-01.
"""


def _write_leaf(directory, payload, *, log_text=None):
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    if log_text is not None:
        (directory / f"mesh_log_{payload['mesh_id']}.txt").write_text(
            log_text,
            encoding="utf-8",
        )


def test_extent_fill_comes_from_log_not_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    _write_leaf(directory, payload, log_text=_MESH_CHECK_LOG)

    result = backfill.backfill_mesh_leaf(directory, apply=False)

    assert result["registry"] == {}
    assert result["extent"]["status"] == backfill.EXTENT_STATUS_FROM_LOG
    additions = result["extent"]["additions"]
    assert set(additions) == set(backfill.DOMAIN_EXTENT_FIELDS)
    assert additions["domain_extent_x_m"] == pytest.approx(0.017325)
    assert additions["domain_extent_y_m"] == pytest.approx(0.003465)
    assert "domain_extent_x_m" not in result["registry"]
    n_total = payload["n_buffer_in"] + payload["n_active_cells"] + payload["n_buffer_out"]
    assert additions["domain_extent_x_m"] != payload["cell_length_x_m"] * n_total


def test_extent_apply_writes_parsed_values(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    _write_leaf(directory, mesh_payload(), log_text=_MESH_CHECK_LOG)

    backfill.backfill_mesh_leaf(directory, apply=True)

    stored = read_mesh_manifest(directory)
    assert stored["domain_extent_x_m"] == pytest.approx(0.017325)
    assert stored["domain_extent_y_m"] == pytest.approx(0.003465)
    assert stored["domain_extent_z_m"] == pytest.approx(0.0007707137)


def test_existing_null_extent_is_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    payload["domain_extent_x_m"] = None
    payload["domain_extent_y_m"] = None
    payload["domain_extent_z_m"] = None
    _write_leaf(directory, payload, log_text=_MESH_CHECK_LOG)

    result = backfill.backfill_mesh_leaf(directory, apply=True)

    assert result["extent"]["status"] == backfill.EXTENT_STATUS_PRESENT
    assert result["extent"]["additions"] == {}
    stored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert stored["domain_extent_x_m"] is None


def test_missing_log_refuses_leaf_when_extent_keys_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    _write_leaf(directory, mesh_payload(), log_text=None)

    with pytest.raises(FileNotFoundError, match="mesh log required"):
        backfill.backfill_mesh_leaf(directory, apply=False)


def test_missing_log_is_ok_when_extent_keys_already_present(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    payload["domain_extent_x_m"] = 0.017325
    payload["domain_extent_y_m"] = 0.003465
    payload["domain_extent_z_m"] = 0.00077
    _write_leaf(directory, payload, log_text=None)

    result = backfill.backfill_mesh_leaf(directory, apply=False)

    assert result["extent"]["status"] == backfill.EXTENT_STATUS_PRESENT
    assert result["extent"]["additions"] == {}
    assert result["extent"]["log_exists"] is False


def test_unparseable_log_is_no_measurement_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    _write_leaf(directory, payload, log_text="no mesh check block\n")
    original = (directory / "manifest.json").read_text(encoding="utf-8")

    result = backfill.backfill_mesh_leaf(directory, apply=True)

    assert result["extent"]["status"] == backfill.EXTENT_STATUS_NO_MEASUREMENT
    assert result["extent"]["additions"] == {}
    stored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert "domain_extent_x_m" not in stored
    assert (directory / "manifest.json").read_text(encoding="utf-8") == original


def test_registry_and_log_sources_stay_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    del payload["membrane_blocked_area_frac_geometric"]
    _write_leaf(directory, payload, log_text=_MESH_CHECK_LOG)

    result = backfill.backfill_mesh_leaf(directory, apply=False)

    assert "membrane_blocked_area_frac_geometric" in result["registry"]
    assert "domain_extent_x_m" not in result["registry"]
    assert "membrane_blocked_area_frac_geometric" not in result["extent"]["additions"]
    assert "domain_extent_x_m" in result["extent"]["additions"]


def test_cli_dry_run_labels_log_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    _write_leaf(directory, mesh_payload(), log_text=_MESH_CHECK_LOG)

    rc = backfill.main(["--geo-id", GEO_ID])
    captured = capsys.readouterr()

    assert rc == 0
    assert "from log (/mesh/check)" in captured.out
    assert "from registry" not in captured.out
    assert "usable /mesh/check" in captured.out
    stored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert "domain_extent_x_m" not in stored


def test_cli_dry_run_reports_no_measurement(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    _write_leaf(directory, mesh_payload(), log_text="no check\n")

    rc = backfill.main(["--family", FAMILY])
    captured = capsys.readouterr()

    assert rc == 0
    assert "NO-MEASUREMENT" in captured.out
    assert "1 NO-MEASUREMENT" in captured.out
    assert "0 usable /mesh/check" in captured.out


_LAYOUT_MATCH_LOG = """
---------------- 2106112 cells were created in :  1.80 minutes
Domain extents.
  x-coordinate: min = 0.000000e+00, max = 3.465000e+01.
  y-coordinate: min = -1.732500e+00, max = 1.732500e+00.
  z-coordinate: min = -3.850000e-01, max = 3.850000e-01.
"""


def _layout_total_x_m(payload):
    return (
        payload["buffer_length_in_m"]
        + payload["n_active_cells"] * payload["cell_length_x_m"]
        + payload["buffer_length_out_m"]
    )


def test_log_backfill_of_layout_total_passes_x_extent_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    payload = mesh_payload()
    layout_x = _layout_total_x_m(payload)
    naive = payload["cell_length_x_m"] * (
        payload["n_buffer_in"] + payload["n_active_cells"] + payload["n_buffer_out"]
    )
    assert layout_x == pytest.approx(0.03465)
    assert naive == pytest.approx(0.038115)
    _write_leaf(directory, payload, log_text=_LAYOUT_MATCH_LOG)

    backfill.backfill_mesh_leaf(directory, apply=True)

    stored = read_mesh_manifest(directory)
    assert stored["domain_extent_x_m"] == pytest.approx(layout_x)
    result = require_mesh_manifest_x_extent_matches_layout(directory)
    assert result.ok is True


def test_log_backfill_of_short_x_extent_fails_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    _write_leaf(directory, mesh_payload(), log_text=_MESH_CHECK_LOG)

    backfill.backfill_mesh_leaf(directory, apply=True)

    with pytest.raises(ValueError, match="does not match layout nominal"):
        require_mesh_manifest_x_extent_matches_layout(directory)
