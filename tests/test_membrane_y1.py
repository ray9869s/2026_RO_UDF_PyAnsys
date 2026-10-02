"""Membrane y1 resolution. Existing summary columns stay unchanged."""

import math

import pytest

from ro.cp_concentration_stats import (
    area_weighted_quantile,
    concentration_cp_report,
    concentration_cp_summary_rows,
)
from ro.fluent_report_helpers import summary_rows_to_wide_record
from ro.membrane_y1 import (
    METRES_TO_UM,
    Y1_WINDOW_COLUMNS,
    y1_window_resolution,
    y1_window_summary_rows,
)


def _face(y1_m, area, **extra):
    face = {"y1": y1_m, "area": area}
    face.update(extra)
    return face


def _existing_rows():
    return [
        {"metric": "geo_name", "value": "D2450_a45", "unit": "-"},
        {"metric": "case_name", "value": "u0p2_p6M", "unit": "-"},
        {"metric": "lmh_mass_balance", "value": 24.5, "unit": "LMH"},
        {"metric": "cp_canon_window_avg", "value": 1.08, "unit": "-"},
        {"metric": "pp_cp_canon_max_cell_5", "value": 1.4, "unit": "-"},
        {"metric": "c_b_window_mol_m3", "value": 600.0, "unit": "mol/m3"},
        {"metric": "pp_c_b_midplane_cell_5_mol_m3", "value": 610.0, "unit": "mol/m3"},
    ]


def test_area_weights_quantiles_and_micrometre_conversion():
    faces = [
        _face(1.0e-6, 3.0),
        _face(5.0e-6, 1.0),
    ]
    metrics = y1_window_resolution(faces)
    values = [1.0e-6, 5.0e-6]
    areas = [3.0, 1.0]
    mean_um = (3.0e-6 + 5.0e-6) / 4.0 * METRES_TO_UM
    assert metrics["y1_window_areamean_um"] == pytest.approx(mean_um)
    assert metrics["y1_window_areamean_um"] == pytest.approx(2.0)
    assert metrics["y1_window_areamean_um"] != pytest.approx(3.0)
    for column, probability in (
        ("y1_window_q10_um", 0.10),
        ("y1_window_median_um", 0.50),
        ("y1_window_q90_um", 0.90),
    ):
        expected = area_weighted_quantile(values, areas, probability) * METRES_TO_UM
        assert metrics[column] == pytest.approx(expected)
    assert metrics["y1_window_q10_um"] == pytest.approx(1.0)
    assert metrics["y1_window_median_um"] == pytest.approx(1.0)
    assert metrics["y1_window_q90_um"] == pytest.approx(5.0)


def test_single_face_landscape_scale_and_zero_area_ignored():
    faces = [
        _face(5.736e-6, 2.0),
        _face(9.0e-6, 0.0),
    ]
    metrics = y1_window_resolution(faces)
    assert metrics["y1_window_areamean_um"] == pytest.approx(5.736)
    assert metrics["y1_window_q10_um"] == pytest.approx(5.736)
    assert metrics["y1_window_median_um"] == pytest.approx(5.736)
    assert metrics["y1_window_q90_um"] == pytest.approx(5.736)


def test_window_quantile_is_not_the_mean_of_cell_quantiles():
    window = [_face(1.0e-6, 1.0), _face(3.0e-6, 1.0)]
    metrics = y1_window_resolution(window)
    cell_medians_um = [
        area_weighted_quantile([1.0e-6], [1.0], 0.50) * METRES_TO_UM,
        area_weighted_quantile([3.0e-6], [1.0], 0.50) * METRES_TO_UM,
    ]
    assert metrics["y1_window_median_um"] == pytest.approx(1.0)
    assert metrics["y1_window_median_um"] != pytest.approx(
        sum(cell_medians_um) / len(cell_medians_um)
    )


@pytest.mark.parametrize(
    "y1",
    [0.0, -1.0e-6, -0.0, math.nan, math.inf, -math.inf],
)
def test_nonpositive_or_nonfinite_y1_raises(y1):
    with pytest.raises(ValueError, match="UDM layout mismatch"):
        y1_window_resolution([_face(1.0e-6, 1.0), _face(y1, 0.4)])


def test_bad_y1_message_reports_count_and_total_area():
    faces = [
        _face(2.0e-6, 4.0),
        _face(0.0, 0.25),
        _face(-1.0e-6, 0.5),
        _face(math.nan, 0.125),
        _face(math.inf, 0.125),
    ]
    with pytest.raises(ValueError, match="UDM layout mismatch") as exc:
        y1_window_resolution(faces)
    message = str(exc.value)
    assert "4 membrane window faces" in message
    assert "total area 1.0 m2" in message


def test_missing_y1_empty_window_and_nonfinite_area_raise():
    with pytest.raises(ValueError, match="udm-12 was not read"):
        y1_window_resolution([{"area": 1.0, "cm": 2.0}])
    with pytest.raises(ValueError, match="at least one membrane face"):
        y1_window_resolution([])
    with pytest.raises(ValueError, match="face area must be finite"):
        y1_window_resolution([_face(1.0e-6, math.nan)])
    with pytest.raises(ValueError, match="no positive face area"):
        y1_window_resolution([_face(1.0e-6, 0.0), _face(2.0e-6, -1.0)])


def test_existing_summary_columns_unchanged():
    faces = [
        _face(2.0e-6, 1.0, cm=2.0, jw=1.0e-5),
        _face(4.0e-6, 1.0, cm=4.0, jw=1.0e-5),
    ]
    report = concentration_cp_report(
        faces,
        {5: faces},
        c_b_window=600.0,
        c_b_by_cell={5: 610.0},
    )
    existing = _existing_rows() + concentration_cp_summary_rows(report)
    before = summary_rows_to_wide_record(existing)
    metrics = y1_window_resolution(faces)
    rows = y1_window_summary_rows(metrics)
    after = summary_rows_to_wide_record(existing + rows)
    for column, value in before.items():
        assert after[column] == value
    assert [row["metric"] for row in rows] == list(Y1_WINDOW_COLUMNS)
    for name in Y1_WINDOW_COLUMNS:
        assert name not in before
        assert after[name] == metrics[name]
        assert name not in {row["metric"] for row in concentration_cp_summary_rows(report)}
