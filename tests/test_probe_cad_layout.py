"""Pure-Python checks for scripts/_probe_cad_layout.py."""

from __future__ import annotations

import sys
import types

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module


def _load_probe(monkeypatch):
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    return load_module(
        "probe_cad_layout_under_test",
        SCRIPTS_DIR / "_probe_cad_layout.py",
    )


def test_parse_geo_pitch_accepts_token(monkeypatch):
    probe = _load_probe(monkeypatch)
    assert probe.parse_geo_pitch("D2450_a45:3.465") == ("D2450_a45", 3.465)


@pytest.mark.parametrize(
    "token",
    ["D2450_a45", "D2450_a45:", ":3.465", "D2450_a45:0", "D2450_a45:-1", "D2450_a45:abc"],
)
def test_parse_geo_pitch_rejects_bad_tokens(monkeypatch, token):
    probe = _load_probe(monkeypatch)
    with pytest.raises(ValueError):
        probe.parse_geo_pitch(token)


def test_classify_labels_detects_split_buffers(monkeypatch):
    probe = _load_probe(monkeypatch)
    labels = [
        "wall_top_mem",
        "wall_bottom_mem",
        "wall_top_buffer_in",
        "wall_top_buffer_out",
        "wall_bottom_buffer_in",
        "wall_bottom_buffer_out",
        "wall_spacer",
        "periodic_l",
        "periodic_r",
    ]
    flags = probe.classify_labels(labels)
    assert flags["buffers_split"] is True
    assert flags["buffers_unsplit"] is False
    assert flags["has_wall_spacer"] is True
    assert flags["has_periodic_l"] is True
    assert flags["has_periodic_r"] is True
    assert flags["has_wall_top_mem"] is True


def test_classify_labels_detects_unsplit_buffers(monkeypatch):
    probe = _load_probe(monkeypatch)
    flags = probe.classify_labels(
        ["wall_top_mem", "wall_top_buffer", "periodic_l", "periodic_r"]
    )
    assert flags["buffers_split"] is False
    assert flags["buffers_unsplit"] is True


def test_bbox_from_raw_accepts_xmin_xmax_layout(monkeypatch):
    probe = _load_probe(monkeypatch)
    box = probe.bbox_from_raw([0.0, 24.255, -1.0, 1.0, 0.0, 0.77])
    assert box["dx"] == pytest.approx(24.255)
    assert box["cx"] == pytest.approx(12.1275)


def test_bbox_from_raw_accepts_xyz_min_then_max(monkeypatch):
    probe = _load_probe(monkeypatch)
    box = probe.bbox_from_raw([0.0, -1.0, 0.0, 24.255, 1.0, 0.77])
    assert box is not None
    assert box["dx"] == pytest.approx(24.255)


def test_bbox_from_raw_parses_scheme_string(monkeypatch):
    probe = _load_probe(monkeypatch)
    box = probe.bbox_from_raw("(0.0 24.255 -0.5 0.5 0.0 0.77)")
    assert box["dx"] == pytest.approx(24.255)


def test_n_active_from_dx(monkeypatch):
    probe = _load_probe(monkeypatch)
    assert probe.n_active_from_dx(24.255, 3.465) == pytest.approx(7.0)
    assert probe.n_active_from_dx(None, 3.465) is None


def test_calibration_report_matches_known_d2450(monkeypatch):
    probe = _load_probe(monkeypatch)
    membrane = probe.bbox_from_raw([3.465, 27.72, 0.0, 3.465, 0.0, 0.77])
    periodic_l = probe.bbox_from_raw([0.0, 34.65, 0.0, 0.0, 0.0, 0.77])
    periodic_r = probe.bbox_from_raw([0.0, 34.65, 3.465, 3.465, 0.0, 0.77])
    extents = {
        "wall_top_mem": membrane,
        "periodic_l": periodic_l,
        "periodic_r": periodic_r,
    }
    flags = probe.classify_labels(
        [
            "wall_top_mem",
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
            "periodic_l",
            "periodic_r",
        ]
    )
    report = probe.calibration_report(extents, extents, flags)
    assert report["import_matches_known"] is True
    assert report["surface_mesh_matches_known"] is True
    assert report["import_agrees_with_surface_mesh"] is True
    assert report["import_only_sufficient"] is True


def test_calibration_report_rejects_wrong_span(monkeypatch):
    probe = _load_probe(monkeypatch)
    membrane = probe.bbox_from_raw([0.0, 10.0, 0.0, 1.0, 0.0, 0.77])
    periodic_l = probe.bbox_from_raw([0.0, 10.0, 0.0, 0.0, 0.0, 0.77])
    periodic_r = probe.bbox_from_raw([0.0, 10.0, 1.0, 1.0, 0.0, 0.77])
    extents = {
        "wall_top_mem": membrane,
        "periodic_l": periodic_l,
        "periodic_r": periodic_r,
    }
    flags = {"buffers_split": True}
    report = probe.calibration_report(extents, extents, flags)
    assert report["import_matches_known"] is False
    assert report["surface_mesh_matches_known"] is False


def test_parse_args_calibrate(monkeypatch):
    probe = _load_probe(monkeypatch)
    args = probe.parse_args(["--calibrate", "D2450_a45:3.465"])
    assert args.calibrate is True
    assert args.geos == ["D2450_a45:3.465"]
    assert args.family == "diamond"


def test_script_does_not_touch_mesh_or_run_trees():
    source = (SCRIPTS_DIR / "_probe_cad_layout.py").read_text(encoding="utf-8")
    for forbidden in ("mesh_dir(", "meshes_root(", "run_dir(", "runs_root("):
        assert forbidden not in source
    assert "TemporaryDirectory" in source
    assert REPO_ROOT.joinpath("scripts", "_probe_cad_layout.py").is_file()
