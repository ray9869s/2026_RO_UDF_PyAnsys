"""Tests for meshing metrics, run records, and retroactive ledgers."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from _mesh_common import (
    MESH_LEDGER_FIELDNAMES,
    MESH_PARAMETER_NAMES,
    build_mesh_ledger_record,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_text,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
)
from helpers import SCRIPTS_DIR, load_module


SYNTHETIC_MESH_LOG = """
========================================================================
MESHING INPUT SUMMARY
========================================================================
Geometry name: Sin_ST
Case name: mesh_max085_min005_cpg5_bl4
Maximum size, m_max [mm]: 0.085
Minimum size, m_min [mm]: 0.005
Cells per gap, m_cpg [-]: 5
Active membrane wall labels: ['wall_top_mem', 'wall_bottom_mem']
Buffer wall labels: ['wall_top_buffer', 'wall_bottom_buffer']
Wall spacer labels for local sizing: ['wall_spacer']
Periodic labels: ['periodic_l', 'periodic_r']
Periodic reference label: periodic_r
Periodic translation [mm]: dx=0.0, dy=3.465, dz=0.0
BOI curvature normal angle [deg]: 18
BOI growth rate [-]: 1.2
Boundary layer labels: ['wall_top_mem', 'wall_bottom_mem', 'wall_spacer']
Boundary layer offset method: smooth-transition
Boundary layer first height factor [-]: 0.4
Boundary layer first height [mm]: 0.002
Boundary layer number of layers [-]: 4
Boundary layer growth rate [-]: 1.2
Volume hex max factor [-]: 0.7
Volume hex max cell length [mm]: 0.0595
Peel layers [-]: 2
Minimum orthogonal quality threshold [-]: 0.05
Maximum aspect ratio threshold [-]: 100.0
Maximum Skewness = 9.2e-01
Minimum Orthogonal Quality = 1.1e-01
Maximum Aspect Ratio = 4.2e+01
Total Number of Cells = 123,456
"""


def legacy_quality_parser(text):
    float_pattern = r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"

    def last(pattern):
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        return float(matches[-1]) if matches else None

    minimum = last(
        rf"Minimum\s+Orthogonal\s+Quality\s*=\s*{float_pattern}"
    )
    if minimum is None:
        minimum = last(
            rf"minimum\s+Orthogonal\s+Quality\s+of:\s*{float_pattern}"
        )
    maximum = last(
        rf"Maximum\s+Aspect\s+Ratio\s*=\s*{float_pattern}"
    )
    return minimum, maximum


def test_extended_parser_preserves_existing_quality_results():
    metrics = parse_mesh_metrics_text(SYNTHETIC_MESH_LOG)
    assert (
        metrics["min_orthogonal_quality"],
        metrics["max_aspect_ratio"],
    ) == legacy_quality_parser(SYNTHETIC_MESH_LOG)
    assert metrics == {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.92,
        "cell_count": 123456,
    }


def test_input_summary_recovers_logged_mesh_parameters():
    parameters = parse_meshing_input_summary(SYNTHETIC_MESH_LOG)
    assert parameters["m_max"] == 0.085
    assert parameters["m_min"] == 0.005
    assert parameters["m_cpg"] == 5
    assert parameters["bl_layers"] == 4
    assert parameters["periodic_shift_y"] == 3.465
    assert parameters["wall_spacer_labels"] == ["wall_spacer"]


def test_ledger_contains_every_mesh_parameter_and_metric(tmp_path):
    parameters = mesh_parameters_from_mapping(
        parse_meshing_input_summary(SYNTHETIC_MESH_LOG)
    )
    metrics = parse_mesh_metrics_text(SYNTHETIC_MESH_LOG)
    record = build_mesh_ledger_record(
        geo_name="Sin_ST",
        mesh_case_name="mesh_max085_min005_cpg5_bl4",
        mesh_parameters=parameters,
        status="SUCCESS",
        exit_code=0,
        wall_time_seconds=12.5,
        metrics=metrics,
        mesh_log_path=tmp_path / "mesh_log.txt",
        mesh_file_path=tmp_path / "mesh.msh.h5",
    )

    assert set(MESH_PARAMETER_NAMES) <= set(record)
    assert record["status"] == "SUCCESS"
    assert record["exit_code"] == 0
    assert record["wall_time_seconds"] == 12.5
    assert record["quality_gate_passed"] is True

    ledger_path = tmp_path / "mesh_ledger.csv"
    upsert_mesh_ledger_csv(ledger_path, [record])
    with ledger_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == MESH_LEDGER_FIELDNAMES
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["cell_count"] == "123456"


def test_ledger_upserts_one_row_per_case(tmp_path):
    parameters = mesh_parameters_from_mapping({})
    metrics = {
        "min_orthogonal_quality": None,
        "max_aspect_ratio": None,
        "max_skewness": None,
        "cell_count": None,
    }
    base = dict(
        geo_name="Sin_ST",
        mesh_case_name="mesh_case",
        mesh_parameters=parameters,
        exit_code=1,
        wall_time_seconds=1.0,
        metrics=metrics,
        mesh_log_path=tmp_path / "log.txt",
        mesh_file_path=tmp_path / "mesh.msh.h5",
    )
    path = tmp_path / "ledger.csv"
    upsert_mesh_ledger_csv(
        path,
        [build_mesh_ledger_record(status="FAILED", **base)],
    )
    base["exit_code"] = 0
    upsert_mesh_ledger_csv(
        path,
        [build_mesh_ledger_record(status="SUCCESS", **base)],
    )

    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["status"] == "SUCCESS"


def test_retroactive_builder_uses_same_schema(tmp_path):
    case_dir = (
        tmp_path
        / "03_Results"
        / "Sin_ST"
        / "mesh_max085_min005_cpg5_bl4"
    )
    case_dir.mkdir(parents=True)
    log_path = case_dir / "mesh_log_mesh_max085_min005_cpg5_bl4.txt"
    log_path.write_text(SYNTHETIC_MESH_LOG, encoding="utf-8")
    (
        case_dir
        / "Sin_ST_mesh_max085_min005_cpg5_bl4.msh.h5"
    ).write_bytes(b"mesh")

    retro = load_module(
        "rebuild_mesh_ledger_under_test",
        SCRIPTS_DIR / "rebuild_mesh_ledger_from_logs.py",
    )
    records = retro.rebuild_mesh_ledger_records(tmp_path / "03_Results")

    assert len(records) == 1
    assert tuple(records[0]) == MESH_LEDGER_FIELDNAMES
    assert records[0]["status"] == "SUCCESS"
    assert records[0]["cell_count"] == 123456


def test_batch_meshing_continue_on_failure_defaults_true():
    batch = load_module(
        "batch_meshing_under_test",
        SCRIPTS_DIR / "batch_meshing.py",
    )
    assert batch._continue_on_failure(SimpleNamespace()) is True
    assert (
        batch._continue_on_failure(
            SimpleNamespace(continue_on_failure=False)
        )
        is False
    )
