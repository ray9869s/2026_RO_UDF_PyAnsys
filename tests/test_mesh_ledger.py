"""Tests for meshing metrics, run records, and retroactive ledgers."""

from __future__ import annotations

import csv
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from ro.mesh_common import (
    MESH_LEDGER_FIELDNAMES,
    MESH_METRIC_NAMES,
    MESH_PARAMETER_NAMES,
    build_mesh_ledger_record,
    evaluate_quality_gate,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_from_log,
    parse_mesh_metrics_text,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
    write_mesh_run_record,
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
Skewed face fraction threshold [-]: 3e-5
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

REAL_D0817_A60_SURFACE_TABLE = """
                     name    skewed-cells (> 0.80)   averaged-skewness   maximum-skewness   face count
                    solid                       3         0.029528853         0.86782818        324454
"""

REAL_D0817_A60_VOLUME_TABLE = """
    name    id      cells (quality < 0.05)   minimum quality   cell count
    solid   11094                        0       0.066464689       1071672
"""

REAL_VOLUME_TABLE_WITH_OVERALL = """
                     name       id cells (quality < 0.05)  minimum quality cell count
                    fluid      120                      5      0.065995142    2179173
                  airfoil      115                      3       0.10070853    1876929
          Overall Summary     none                      8      0.065995142    4056102
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
        "averaged_skewness": None,
        "skewed_faces_over_080": None,
        "surface_face_count": None,
        "skewed_face_fraction": None,
        "cells_below_min_ortho_quality": None,
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
    assert metrics["averaged_skewness"] == pytest.approx(0.024561487)
    assert metrics["skewed_faces_over_080"] == 0
    assert metrics["surface_face_count"] == 756890
    assert metrics["skewed_face_fraction"] == 0.0


def test_surface_summary_skewness_is_fallback_when_table_absent():
    text = (
        "---------------- Surface Meshing of Empty complete in  0.13 "
        "minutes, with a maximum skewness of  0.36.\n"
    )
    metrics = parse_mesh_metrics_text(text)
    assert metrics["max_skewness"] == pytest.approx(0.36)
    assert metrics["averaged_skewness"] is None
    assert metrics["skewed_faces_over_080"] is None
    assert metrics["surface_face_count"] is None
    assert metrics["skewed_face_fraction"] is None


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
    assert metrics["skewed_faces_over_080"] == 1
    assert metrics["surface_face_count"] == 300
    assert metrics["skewed_face_fraction"] == pytest.approx(1.0 / 300.0)
    assert metrics["averaged_skewness"] == pytest.approx(
        (0.01 * 100 + 0.02 * 200) / 300
    )


def test_partial_mesh_check_blocks_leave_derived_metrics_none():
    metrics = parse_mesh_metrics_text(
        "Domain extents.\n"
        "x-coordinate: min = 0.0, max = 1.0.\n"
    )
    assert metrics["domain_extent_x_m"] is None
    assert metrics["total_fluid_volume_m3"] is None
    assert metrics["bounding_box_volume_m3"] is None
    assert metrics["porosity"] is None


def test_missing_or_unparseable_log_returns_none_extent_keys_not_zero(tmp_path):
    """R-09 needs None (no measurement) distinct from a measured 0.0."""
    missing = parse_mesh_metrics_from_log(tmp_path / "no_such_mesh_log.txt")
    empty = parse_mesh_metrics_text("")
    for metrics in (missing, empty):
        assert "domain_extent_x_m" in metrics
        assert metrics["domain_extent_x_m"] is None
        assert metrics["domain_extent_y_m"] is None
        assert metrics["domain_extent_z_m"] is None


def test_input_summary_recovers_logged_mesh_parameters():
    parameters = parse_meshing_input_summary(SYNTHETIC_MESH_LOG)
    assert parameters["m_max"] == 0.085
    assert parameters["m_min"] == 0.005
    assert parameters["m_cpg"] == 5
    assert parameters["bl_layers"] == 4
    assert parameters["periodic_shift_y"] == 3.465
    assert parameters["wall_spacer_labels"] == ["wall_spacer"]
    assert parameters["max_skewness_threshold"] == pytest.approx(0.85)
    assert parameters["skewed_face_fraction_threshold"] == pytest.approx(3.0e-5)


def test_quality_gate_fails_when_max_skewness_exceeds_threshold():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 42.0,
        "max_skewness": 0.90,
        "skewed_face_fraction": 0.0,
    }
    assert evaluate_quality_gate(parameters, metrics) is False


def test_quality_gate_passes_when_max_skewness_within_threshold():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 64.82,
        "max_skewness": 0.670634,
        "skewed_face_fraction": 0.0,
    }
    assert evaluate_quality_gate(parameters, metrics) is True


def test_d0817_a60_surface_and_volume_tables_parse():
    metrics = parse_mesh_metrics_text(
        REAL_D0817_A60_SURFACE_TABLE + REAL_D0817_A60_VOLUME_TABLE
    )
    assert metrics["max_skewness"] == pytest.approx(0.86782818)
    assert metrics["averaged_skewness"] == pytest.approx(0.029528853)
    assert metrics["skewed_faces_over_080"] == 3
    assert metrics["surface_face_count"] == 324454
    assert metrics["skewed_face_fraction"] == pytest.approx(3 / 324454)
    assert metrics["cells_below_min_ortho_quality"] == 0
    # The volume-table row is numerically similar to a surface row; it must
    # not be folded into the surface metrics (that would add id 11094 as a
    # skewed-face count and 1,071,672 as extra faces).
    assert metrics["skewed_faces_over_080"] != 11097


def test_volume_table_prefers_overall_summary_poor_cell_count():
    metrics = parse_mesh_metrics_text(REAL_VOLUME_TABLE_WITH_OVERALL)
    assert metrics["cells_below_min_ortho_quality"] == 8


def test_quality_gate_fails_when_skewed_face_fraction_exceeds_threshold():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.84,
        "skewed_face_fraction": 30000 / 324454,
    }
    assert evaluate_quality_gate(parameters, metrics) is False


def test_quality_gate_passes_measured_d0817_a60_fraction_with_max_under_limit():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.066464689,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.84,
        "skewed_face_fraction": 3 / 324454,
    }
    assert evaluate_quality_gate(parameters, metrics) is True


def test_quality_gate_requires_both_skewness_gates():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    high_max = {
        "min_orthogonal_quality": 0.0664,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.86782818,
        "skewed_face_fraction": 3 / 324454,
    }
    high_fraction = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.84,
        "skewed_face_fraction": 30000 / 324454,
    }
    assert evaluate_quality_gate(parameters, high_max) is False
    assert evaluate_quality_gate(parameters, high_fraction) is False


def test_quality_gate_fails_incomplete_cpg7_fraction_at_3e_minus_5():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    passing_worst = {
        "min_orthogonal_quality": 0.0664,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.84,
        "skewed_face_fraction": 9.25e-6,
        "averaged_skewness": 0.0336,
    }
    failed_cpg7 = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 83.3,
        "max_skewness": 0.84,
        "skewed_face_fraction": 8.22e-5,
        "averaged_skewness": 0.0251,
    }
    assert evaluate_quality_gate(parameters, passing_worst) is True
    assert evaluate_quality_gate(parameters, failed_cpg7) is False


def test_quality_gate_ignores_averaged_skewness():
    parameters = {
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 150.0,
        "max_skewness_threshold": 0.85,
        "skewed_face_fraction_threshold": 3.0e-5,
        "fail_if_quality_not_parsed": False,
    }
    metrics = {
        "min_orthogonal_quality": 0.11,
        "max_aspect_ratio": 64.82,
        "max_skewness": 0.670634,
        "skewed_face_fraction": 0.0,
        "averaged_skewness": 0.99,
    }
    assert evaluate_quality_gate(parameters, metrics) is True


def test_new_ledger_columns_sit_next_to_existing_quality_fields():
    names = list(MESH_LEDGER_FIELDNAMES)
    max_skew = names.index("max_skewness")
    assert names[max_skew:max_skew + 6] == [
        "max_skewness",
        "averaged_skewness",
        "skewed_faces_over_080",
        "surface_face_count",
        "skewed_face_fraction",
        "cells_below_min_ortho_quality",
    ]
    assert names[names.index("max_skewness_threshold") + 1] == (
        "skewed_face_fraction_threshold"
    )


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
    assert record["skewed_face_fraction_threshold"] == pytest.approx(3.0e-5)
    assert record["max_skewness"] == pytest.approx(0.52)
    assert record["averaged_skewness"] is None
    assert record["skewed_faces_over_080"] is None
    assert record["surface_face_count"] is None
    assert record["skewed_face_fraction"] is None
    assert record["cells_below_min_ortho_quality"] is None

    ledger_path = tmp_path / "mesh_ledger.csv"
    upsert_mesh_ledger_csv(ledger_path, [record])
    with ledger_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == MESH_LEDGER_FIELDNAMES
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["cell_count"] == "123456"
    assert rows[0]["averaged_skewness"] == ""
    assert rows[0]["skewed_faces_over_080"] == ""
    assert rows[0]["surface_face_count"] == ""
    assert rows[0]["skewed_face_fraction"] == ""
    assert rows[0]["cells_below_min_ortho_quality"] == ""


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


def test_ledger_upsert_read_write_encodings_preserve_geo_name(tmp_path):
    parameters = mesh_parameters_from_mapping({})
    metrics = {name: None for name in MESH_METRIC_NAMES}
    path = tmp_path / "ledger.csv"
    record_kw = dict(
        geo_name="Sin_ST",
        mesh_case_name="mesh_case",
        mesh_parameters=parameters,
        exit_code=0,
        wall_time_seconds=1.0,
        metrics=metrics,
        mesh_log_path=tmp_path / "log.txt",
        mesh_file_path=tmp_path / "mesh.msh.h5",
    )
    upsert_mesh_ledger_csv(
        path,
        [build_mesh_ledger_record(status="SUCCESS", **record_kw)],
    )
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    upsert_mesh_ledger_csv(
        path,
        [build_mesh_ledger_record(status="SUCCESS", **record_kw)],
    )
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["geo_name"] == "Sin_ST"
    assert rows[0]["mesh_case_name"] == "mesh_case"


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
    assert records[0]["averaged_skewness"] is None
    assert records[0]["skewed_faces_over_080"] is None
    assert records[0]["cells_below_min_ortho_quality"] is None

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
        + REAL_SIN_ST_MESH_CHECK
        + REAL_D0817_A60_VOLUME_TABLE,
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
    assert real_record["averaged_skewness"] == pytest.approx(0.024561487)
    assert real_record["skewed_faces_over_080"] == 0
    assert real_record["surface_face_count"] == 756890
    assert real_record["skewed_face_fraction"] == 0.0
    assert real_record["cells_below_min_ortho_quality"] == 0
    assert real_record["cell_count"] == 2106112
    assert real_record["porosity"] == pytest.approx(0.7344725011140605)


REAL_D0817_A45_FAILED_SURFACE_TABLE = """
                     name    skewed-cells (> 0.80)   averaged-skewness   maximum-skewness   face count
                    solid                      52         0.040000000         0.9556           200000
"""

SURFACE_SUMMARY_WITHOUT_TABLE = (
    "---------------- Surface Meshing of D0817_a45 complete in  1.20 "
    "minutes, with a maximum skewness of  0.96.\n"
)


def load_meshing_code():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "meshing_code_surface_gate_under_test",
            SCRIPTS_DIR / "meshing_code_260616.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


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


def test_surface_mesh_gate_passes_real_surface_table(tmp_path, capsys):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    log_path.write_text(REAL_SURFACE_SKEWNESS_TABLE, encoding="utf-8")
    table = meshing.apply_surface_mesh_quality_gate(
        log_path, 0.85, 3.0e-5
    )
    assert table["max_skewness"] == pytest.approx(0.52229388)
    assert table["skewed_faces_over_080"] == 0
    captured = capsys.readouterr().out
    assert "Surface mesh quality gate passed (skewness only)." in captured
    assert "Mesh quality gate passed." not in captured


def test_surface_mesh_gate_refuses_d0817_a45_max_skewness(tmp_path):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    log_path.write_text(REAL_D0817_A45_FAILED_SURFACE_TABLE, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Surface mesh quality failed: maximum skewness"):
        meshing.apply_surface_mesh_quality_gate(log_path, 0.85, 3.0e-5)


def test_surface_mesh_gate_refuses_d0817_a60_max_skewness(tmp_path):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    log_path.write_text(REAL_D0817_A60_SURFACE_TABLE, encoding="utf-8")
    with pytest.raises(RuntimeError, match="maximum skewness"):
        meshing.apply_surface_mesh_quality_gate(log_path, 0.85, 3.0e-5)


def test_surface_mesh_gate_refuses_unparseable_table(tmp_path):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    log_path.write_text("Generate the Surface Mesh complete.\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Could not parse the surface-mesh skewness table"):
        meshing.apply_surface_mesh_quality_gate(log_path, 0.85, 3.0e-5)


def test_surface_mesh_gate_refuses_summary_line_without_table(tmp_path):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    log_path.write_text(SURFACE_SUMMARY_WITHOUT_TABLE, encoding="utf-8")
    metrics = parse_mesh_metrics_text(SURFACE_SUMMARY_WITHOUT_TABLE)
    assert metrics["max_skewness"] == pytest.approx(0.96)
    with pytest.raises(RuntimeError, match="Could not parse the surface-mesh skewness table"):
        meshing.apply_surface_mesh_quality_gate(log_path, 0.85, 3.0e-5)


def test_surface_mesh_gate_refuses_missing_log(tmp_path):
    meshing = load_meshing_code()
    with pytest.raises(RuntimeError, match="Could not parse the surface-mesh skewness table"):
        meshing.apply_surface_mesh_quality_gate(
            tmp_path / "missing.txt", 0.85, 3.0e-5
        )


def test_append_transcript_continuation_preserves_flushed_log(tmp_path):
    meshing = load_meshing_code()
    log_path = tmp_path / "mesh_log.txt"
    continuation = meshing.surface_mesh_continuation_log_path(log_path)
    log_path.write_text("SURFACE TABLE\n", encoding="utf-8")
    continuation.write_text("VOLUME CHECK\n", encoding="utf-8")
    meshing.append_transcript_continuation(log_path, continuation)
    assert log_path.read_text(encoding="utf-8") == "SURFACE TABLE\nVOLUME CHECK\n"
    assert not continuation.is_file()


def test_batch_ledger_prefers_worker_surface_gate_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    batch = load_module(
        "batch_meshing_ledger_error_under_test",
        SCRIPTS_DIR / "batch_meshing.py",
    )
    mesh_directory = (
        tmp_path / "meshes" / "diamond" / "D0817_a45"
        / "max085_min006_cpg5_bl4_peel2"
    )
    mesh_directory.mkdir(parents=True)
    log_path = mesh_directory / "mesh_log_max085_min006_cpg5_bl4_peel2.txt"
    log_path.write_text(REAL_D0817_A45_FAILED_SURFACE_TABLE, encoding="utf-8")
    worker_message = (
        "RuntimeError: Surface mesh quality failed: maximum skewness "
        "0.9556 is above the threshold 0.85."
    )
    write_mesh_run_record(
        mesh_directory / "mesh_run_record.json",
        {"error_summary": worker_message},
    )
    ledger_path = tmp_path / "inventory" / "mesh_ledger.csv"
    record = batch._write_case_ledger(
        ledger_path=ledger_path,
        geo_name="D0817_a45",
        mesh_case_name="max085_min006_cpg5_bl4_peel2",
        mesh_parameters=mesh_parameters_from_mapping({}),
        status="FAILED",
        exit_code=1,
        wall_time_seconds=12.0,
        mesh_log_path=log_path,
        mesh_file_path=mesh_directory / "D0817_a45_max085_min006_cpg5_bl4_peel2.msh.h5",
        error_summary="meshing worker exited with code 1",
    )
    assert record["error_summary"] == worker_message
    assert record["status"] == "FAILED"
    assert record["max_skewness"] == pytest.approx(0.9556)

