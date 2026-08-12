"""Tests for meshing metrics, run records, and retroactive ledgers."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from _mesh_common import (
    MESH_LEDGER_FIELDNAMES,
    MESH_METRIC_NAMES,
    MESH_PARAMETER_NAMES,
    build_mesh_ledger_record,
    evaluate_quality_gate,
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
Maximum skewness threshold [-]: 0.85
Maximum Skewness = 5.2e-01
Minimum Orthogonal Quality = 1.1e-01
Maximum Aspect Ratio = 4.2e+01
Total Number of Cells = 123,456
"""

# Representative Fluent transcript fragments from Windows-server mesh logs.
REAL_EMPTY_MESH_CHECK = """
---------------- 290284 cells were created in :  0.22 minutes
Domain extents.
  x-coordinate: min = 0.000000e+00, max = 1.039500e+01.
  y-coordinate: min = 0.000000e+00, max = 3.465000e+00.
  z-coordinate: min = 0.000000e+00, max = 7.700000e-01.
Volume statistics.
  minimum volume: 5.203703e-06.
  maximum volume: 3.856288e-04.
    total volume: 2.773438e+01.
"""

REAL_PILLAR_MESH_CHECK = """
---------------- 583457 cells were created in :  0.37 minutes
Domain extents.
  x-coordinate: min = -8.673617e-15, max = 1.732500e+01.
  y-coordinate: min = -1.732500e+00, max = 1.732500e+00.
  z-coordinate: min = -3.850000e-01, max = 3.850000e-01.
Volume statistics.
  minimum volume: 6.683190e-07.
  maximum volume: 4.351642e-04.
    total volume: 4.421702e+01.
"""

REAL_SIN_ST_MESH_CHECK = """
---------------- 2106112 cells were created in :  1.80 minutes
Domain extents.
  x-coordinate: min = 0.000000e+00, max = 1.732500e+01.
  y-coordinate: min = -1.732500e+00, max = 1.732500e+00.
  z-coordinate: min = -3.853586e-01, max = 3.853551e-01.
Volume statistics.
  minimum volume: 4.309264e-10.
  maximum volume: 9.794811e-04.
    total volume: 3.398170e+01.
"""

REAL_SURFACE_SKEWNESS_TABLE = """
-------------------------- --------------------- -------------------- ---------------- ----------
                     name skewed-cells (> 0.80)    averaged-skewness maximum-skewness face count
-------------------------- --------------------- -------------------- ---------------- ----------
                    solid                     0          0.024561487       0.52229388     756890
-------------------------- --------------------- -------------------- ---------------- ----------
                     name skewed-cells (> 0.80)    averaged-skewness maximum-skewness face count
-------------------------- --------------------- -------------------- ---------------- ----------
             sin_st-solid                     0          0.024561487       0.52229388     756890
---------------- Surface Meshing of Sin_ST complete in  6.06 minutes, with a maximum skewness of  0.52.
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
        "max_skewness": 0.52,
        "cell_count": 123456,
        "domain_extent_x_m": None,
        "domain_extent_y_m": None,
        "domain_extent_z_m": None,
        "min_cell_volume_m3": None,
        "max_cell_volume_m3": None,
        "total_fluid_volume_m3": None,
        "bounding_box_volume_m3": None,
        "porosity": None,
    }
    assert tuple(metrics) == MESH_METRIC_NAMES


def test_real_surface_table_prefers_full_precision_skewness():
    metrics = parse_mesh_metrics_text(REAL_SURFACE_SKEWNESS_TABLE)
    assert metrics["max_skewness"] == pytest.approx(0.52229388)


def test_surface_summary_skewness_is_fallback_when_table_absent():
    text = (
        "---------------- Surface Meshing of Empty complete in  0.13 "
        "minutes, with a maximum skewness of  0.36.\n"
    )
    metrics = parse_mesh_metrics_text(text)
    assert metrics["max_skewness"] == pytest.approx(0.36)


@pytest.mark.parametrize(
    ("text", "expected_count"),
    (
        (REAL_SIN_ST_MESH_CHECK, 2106112),
        (REAL_EMPTY_MESH_CHECK, 290284),
        (REAL_PILLAR_MESH_CHECK, 583457),
    ),
)
def test_created_cells_line_parses_real_counts(text, expected_count):
    metrics = parse_mesh_metrics_text(text)
    assert metrics["cell_count"] == expected_count


def test_sin_st_mesh_check_extents_volume_and_porosity():
    metrics = parse_mesh_metrics_text(REAL_SIN_ST_MESH_CHECK)
    assert metrics["domain_extent_x_m"] == pytest.approx(0.017325)
    assert metrics["domain_extent_y_m"] == pytest.approx(0.003465)
    assert metrics["domain_extent_z_m"] == pytest.approx(0.0007707137)
    assert metrics["min_cell_volume_m3"] == pytest.approx(4.309264e-19)
    assert metrics["max_cell_volume_m3"] == pytest.approx(9.794811e-13)
    assert metrics["total_fluid_volume_m3"] == pytest.approx(3.398170e-8)
    assert metrics["bounding_box_volume_m3"] == pytest.approx(
        4.62668104639125e-8
    )
    assert metrics["porosity"] == pytest.approx(0.7344725011140605)


def test_empty_channel_porosity_clamps_to_one():
    metrics = parse_mesh_metrics_text(REAL_EMPTY_MESH_CHECK)
    assert metrics["domain_extent_x_m"] == pytest.approx(0.010395)
    assert metrics["domain_extent_y_m"] == pytest.approx(0.003465)
    assert metrics["domain_extent_z_m"] == pytest.approx(0.00077)
    assert metrics["min_cell_volume_m3"] == pytest.approx(5.203703e-15)
    assert metrics["max_cell_volume_m3"] == pytest.approx(3.856288e-13)
    assert metrics["total_fluid_volume_m3"] == pytest.approx(2.773438e-8)
    assert metrics["bounding_box_volume_m3"] == pytest.approx(
        2.773437975e-8
    )
    assert metrics["porosity"] == 1.0


def test_pillar_porosity_from_mesh_check():
    metrics = parse_mesh_metrics_text(REAL_PILLAR_MESH_CHECK)
    assert metrics["domain_extent_x_m"] == pytest.approx(
        0.017325 + 8.673617e-18
    )
    assert metrics["min_cell_volume_m3"] == pytest.approx(6.683190e-16)
    assert metrics["max_cell_volume_m3"] == pytest.approx(4.351642e-13)
    assert metrics["total_fluid_volume_m3"] == pytest.approx(4.421702e-8)
    assert metrics["porosity"] == pytest.approx(0.9565821279994553)


def test_distinct_surface_rows_take_worst_maximum_skewness():
    text = """
name skewed-cells (> 0.80) averaged-skewness maximum-skewness face count
solid 0 0.01 0.40 100
wall 1 0.02 0.61 200
"""
    metrics = parse_mesh_metrics_text(text)
    assert metrics["max_skewness"] == pytest.approx(0.61)


def test_partial_mesh_check_blocks_leave_derived_metrics_none():
    metrics = parse_mesh_metrics_text(
        "Domain extents.\n"
        "x-coordinate: min = 0.0, max = 1.0.\n"
    )
    assert metrics["domain_extent_x_m"] is None
    assert metrics["total_fluid_volume_m3"] is None
    assert metrics["bounding_box_volume_m3"] is None
    assert metrics["porosity"] is None


def test_input_summary_recovers_logged_mesh_parameters():
    parameters = parse_meshing_input_summary(SYNTHETIC_MESH_LOG)
    assert parameters["m_max"] == 0.085
    assert parameters["m_min"] == 0.005
    assert parameters["m_cpg"] == 5
    assert parameters["bl_layers"] == 4
    assert parameters["periodic_shift_y"] == 3.465
    assert parameters["wall_spacer_labels"] == ["wall_spacer"]


def test_quality_gate_fails_when_max_skewness_exceeds_threshold():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 100.0,
        "max_skewness_threshold": 0.85,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.90,
    }
    assert evaluate_quality_gate(parameters, metrics) is False


def test_quality_gate_passes_when_max_skewness_within_threshold():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 100.0,
        "max_skewness_threshold": 0.85,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 64.82,
        "max_skewness": 0.670634,
    }
    assert evaluate_quality_gate(parameters, metrics) is True


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
    assert record["max_skewness_threshold"] == pytest.approx(0.85)
    assert record["max_skewness"] == pytest.approx(0.52)

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
    metrics = {name: None for name in MESH_METRIC_NAMES}
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
    assert records[0]["porosity"] is None

    real_case_dir = (
        tmp_path
        / "03_Results"
        / "Sin_ST"
        / "mesh_max085_min005_cpg5_bl4_real"
    )
    real_case_dir.mkdir(parents=True)
    real_log = (
        real_case_dir
        / "mesh_log_mesh_max085_min005_cpg5_bl4_real.txt"
    )
    real_log.write_text(
        SYNTHETIC_MESH_LOG
        + REAL_SURFACE_SKEWNESS_TABLE
        + REAL_SIN_ST_MESH_CHECK,
        encoding="utf-8",
    )
    (
        real_case_dir
        / "Sin_ST_mesh_max085_min005_cpg5_bl4_real.msh.h5"
    ).write_bytes(b"mesh")
    records = retro.rebuild_mesh_ledger_records(tmp_path / "03_Results")
    real_record = next(
        row
        for row in records
        if row["mesh_case_name"] == "mesh_max085_min005_cpg5_bl4_real"
    )
    assert real_record["max_skewness"] == pytest.approx(0.52229388)
    assert real_record["cell_count"] == 2106112
    assert real_record["porosity"] == pytest.approx(0.7344725011140605)


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
