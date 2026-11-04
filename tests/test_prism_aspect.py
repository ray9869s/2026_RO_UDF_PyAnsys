"""Prism/bulk aspect-ratio split. No Fluent."""

from __future__ import annotations

import pytest

from helpers import load_run_config, populate_valid_meshing_config
from ro.manifest import ManifestError, read_mesh_manifest, write_mesh_manifest
from ro.mesh_common import evaluate_quality_gate
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.paths import mesh_dir
from ro.prism_aspect import (
    aspect_ratio_exceeds,
    aspect_ratio_failure_message,
    classify_cell_zone_shape,
    diagnose_aspect_split,
    format_aspect_split,
    parse_aspect_split_text,
    parse_distribution_count,
    parse_quality_limits,
)
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload


class _Utilities:
    def __init__(self, shapes):
        self._shapes = shapes

    def get_cell_zones(self, filter):
        assert filter == "*"
        return list(self._shapes)

    def get_cell_zone_shape(self, cell_zone_id):
        return self._shapes[cell_zone_id][0]

    def get_cell_zone_count(self, cell_zone_id_list):
        zone_id = cell_zone_id_list[0]
        return self._shapes[zone_id][1]


class _Scheme:
    def __init__(self, returns):
        self.returns = returns
        self.expressions = []

    def eval(self, expression):
        self.expressions.append(expression)
        for key, value in self.returns.items():
            if key in expression:
                return value
        raise AssertionError(expression)


class _Session:
    def __init__(self, shapes, returns):
        self.meshing_utilities = _Utilities(shapes)
        self.scheme = _Scheme(returns)


def _split_session():
    shapes = {1: ("wedge", 12), 2: ("polyhedron", 100)}
    returns = {
        "mesh-distribution": 4,
        '(1) "FLUENT Aspect Ratio"': (12, 1.8, 212.0, 40.0),
        '(1) "Orthogonal Quality"': (12, 0.08, 0.99, 0.4),
        '(2) "FLUENT Aspect Ratio"': (100, 1.2, 18.0, 3.0),
        '(2) "Orthogonal Quality"': (100, 0.21, 1.0, 0.7),
    }
    return _Session(shapes, returns)


def test_shape_names_map_to_prism_or_bulk():
    assert classify_cell_zone_shape("'wedge") == "prism"
    assert classify_cell_zone_shape("polyhedron") == "bulk"
    assert classify_cell_zone_shape("mixed") == "mixed"
    assert classify_cell_zone_shape("hexcore") == "bulk"


def test_quality_limits_accept_the_scheme_list_and_a_printed_tuple():
    parsed = parse_quality_limits((12, 0.08, 212.0, 40.0))
    assert parsed == {
        "count": 12,
        "minimum": 0.08,
        "maximum": 212.0,
        "average": 40.0,
    }
    assert parse_quality_limits("(10 1.5 18 4)")["maximum"] == pytest.approx(18.0)
    assert parse_quality_limits("not a list") is None
    assert parse_distribution_count(4) == 4
    assert parse_distribution_count([1, 3]) == 4
    assert parse_distribution_count("4") is None


def test_global_ceiling_stays_until_the_zones_split():
    assert aspect_ratio_exceeds(
        212.0, bulk_limit=150.0, prism_limit=250.0, aspect_split=None
    )
    mixed = {"status": "mixed", "prism_ar_max": 212.0, "bulk_ar_max": 18.0}
    assert aspect_ratio_exceeds(
        212.0, bulk_limit=150.0, prism_limit=250.0, aspect_split=mixed
    )
    assert aspect_ratio_failure_message(
        212.0, bulk_limit=150.0, prism_limit=None, aspect_split=None
    ) == (
        "Mesh quality failed: maximum aspect ratio "
        "212.0 is above the threshold 150.0."
    )


def test_diagnose_reports_prism_and_bulk_limits():
    session = _split_session()
    split = diagnose_aspect_split(
        session, bulk_ar_limit=150.0, prism_ar_limit=250.0
    )
    assert split["status"] == "split"
    assert split["prism_zone_count"] == 12
    assert split["prism_ar_max"] == pytest.approx(212.0)
    assert split["prism_ortho_min"] == pytest.approx(0.08)
    assert split["bulk_zone_count"] == 100
    assert split["bulk_ar_max"] == pytest.approx(18.0)
    assert split["bulk_ortho_min"] == pytest.approx(0.21)
    assert split["cells_above_bulk_ar_limit"] == 4
    assert not aspect_ratio_exceeds(
        212.0, bulk_limit=150.0, prism_limit=250.0, aspect_split=split
    )
    text = format_aspect_split(split)
    assert parse_aspect_split_text(text)["prism_ar_max"] == pytest.approx(212.0)
    assert "tgapi-util-get-cell-quality-limits" in session.scheme.expressions[0]
    assert "FLUENT Aspect Ratio" in session.scheme.expressions[0]


def test_mixed_zone_does_not_waive_the_global_ceiling():
    session = _Session(
        {7: ("mixed", 50)},
        {'(7) "FLUENT Aspect Ratio"': (50, 1.0, 212.0, 10.0),
         '(7) "Orthogonal Quality"': (50, 0.08, 1.0, 0.4)},
    )
    split = diagnose_aspect_split(
        session, bulk_ar_limit=150.0, prism_ar_limit=250.0
    )
    assert split["status"] == "mixed"
    assert aspect_ratio_exceeds(
        212.0, bulk_limit=150.0, prism_limit=250.0, aspect_split=split
    )


def test_ledger_gate_uses_the_split_when_the_flag_is_set():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_prism_aspect_ratio_threshold": 250.0,
        "max_skewness_threshold": None,
        "skewed_face_fraction_threshold": None,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.08,
        "max_aspect_ratio": 212.0,
        "max_skewness": None,
        "skewed_face_fraction": None,
        "aspect_split": {
            "status": "split",
            "prism_ar_max": 212.0,
            "bulk_ar_max": 18.0,
        },
    }
    assert evaluate_quality_gate(parameters, metrics) is True
    metrics["aspect_split"]["bulk_ar_max"] = 180.0
    assert evaluate_quality_gate(parameters, metrics) is False
    metrics["min_orthogonal_quality"] = 0.04
    metrics["aspect_split"]["bulk_ar_max"] = 18.0
    assert evaluate_quality_gate(parameters, metrics) is False


def test_manifest_records_the_prism_ceiling(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.max_prism_aspect_ratio_threshold = 250.0
    metrics = {
        "min_orthogonal_quality": 0.08,
        "max_aspect_ratio": 212.0,
        "max_skewness": 0.6,
        "skewed_face_fraction": 1.0e-6,
        "cell_count": 100,
        "aspect_split": {
            "status": "split",
            "prism_ar_max": 212.0,
            "bulk_ar_max": 18.0,
            "prism_ortho_min": 0.08,
            "bulk_ortho_min": 0.2,
        },
    }
    payload = build_mesh_manifest_payload(
        cfg, metrics, "c" * 64, created_utc="2026-11-04T00:00:00Z"
    )
    assert payload["max_prism_aspect_ratio_threshold"] == pytest.approx(250.0)
    assert payload["aspect_split"]["prism_ar_max"] == pytest.approx(212.0)

    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    stored = mesh_payload()
    stored["max_prism_aspect_ratio_threshold"] = 250.0
    stored["aspect_split"] = payload["aspect_split"]
    write_mesh_manifest(directory, stored)
    assert read_mesh_manifest(directory)["max_prism_aspect_ratio_threshold"] == 250.0

    rejected = dict(stored)
    rejected["aspect_split"] = {"status": "split", "prism_ar_max": "high"}
    with pytest.raises(ManifestError, match="prism_ar_max"):
        write_mesh_manifest(directory, rejected)
