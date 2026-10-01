"""Concentration-statistics CP. Existing summary columns stay unchanged."""

import pytest

from helpers import REPO_ROOT
from ro.cp_concentration_stats import (
    B_PERM_M_PER_S,
    QUANTILE_DEFINITION,
    area_weighted_quantile,
    concentration_cp_report,
    concentration_cp_summary_rows,
    cp_face_mol_per_m3,
    distribution_cp_metrics,
    production_b_perm_m_per_s,
    reference_permeate,
)
from ro.fluent_report_helpers import summary_rows_to_wide_record
from ro.udf_constants import parse_udf_membrane_constants


def _face(cm, jw, area):
    return {"cm": cm, "jw": jw, "area": area}


def _existing_rows():
    return [
        {"metric": "geo_name", "value": "P_p100_h30", "unit": "-"},
        {"metric": "case_name", "value": "u0p2_p6M", "unit": "-"},
        {"metric": "lmh_mass_balance", "value": 24.5, "unit": "LMH"},
        {"metric": "cp_canon_window_avg", "value": 1.08, "unit": "-"},
        {"metric": "pp_cp_canon_max_cell_5", "value": 1.4, "unit": "-"},
        {"metric": "c_b_window_mol_m3", "value": 600.0, "unit": "mol/m3"},
        {"metric": "pp_c_b_midplane_cell_5_mol_m3", "value": 610.0, "unit": "mol/m3"},
    ]


def test_b_perm_matches_production_udf():
    source = (REPO_ROOT / "udfs" / "260822_RO_UDF.c").read_text(encoding="utf-8")
    parsed = parse_udf_membrane_constants(source)["b_perm"]
    assert parsed == pytest.approx(2.50e-8)
    assert production_b_perm_m_per_s() == pytest.approx(parsed)
    assert B_PERM_M_PER_S == pytest.approx(parsed)


def test_area_weighted_quantile_ties_single_face_and_edges():
    assert "cumulative area fraction >= p" in QUANTILE_DEFINITION
    assert area_weighted_quantile([1.0, 2.0, 1.0], [0.3, 0.3, 0.4], 0.5) == 1.0
    assert area_weighted_quantile([5.0], [2.0], 0.0) == 5.0
    assert area_weighted_quantile([5.0], [2.0], 1.0) == 5.0
    assert area_weighted_quantile([5.0], [2.0], 0.5) == 5.0
    assert area_weighted_quantile([1.0, 3.0], [1.0, 1.0], 0.0) == 1.0
    assert area_weighted_quantile([1.0, 3.0], [1.0, 1.0], 1.0) == 3.0
    with pytest.raises(ValueError):
        area_weighted_quantile([1.0], [1.0], -0.01)
    with pytest.raises(ValueError):
        area_weighted_quantile([1.0], [1.0], 1.01)
    with pytest.raises(TypeError):
        area_weighted_quantile([1.0], [1.0], True)


def test_window_quantile_is_not_the_mean_of_cell_quantiles():
    cell_a = [_face(1.0, 1.0e-5, 1.0)]
    cell_b = [_face(3.0, 1.0e-5, 1.0)]
    window = cell_a + cell_b
    q_window = area_weighted_quantile(
        [face["cm"] for face in window],
        [face["area"] for face in window],
        0.5,
    )
    q_cells = [
        area_weighted_quantile([1.0], [1.0], 0.5),
        area_weighted_quantile([3.0], [1.0], 0.5),
    ]
    assert q_window == 1.0
    assert q_window != sum(q_cells) / len(q_cells)


def test_cp_face_and_reference_formulas():
    assert cp_face_mol_per_m3(2.0, 1.0, b_perm=1.0) == pytest.approx(1.0)
    faces = [
        _face(2.0, 2.0, 1.0),
        _face(4.0, 0.0, 1.0),
    ]
    refs = reference_permeate(faces, b_perm=1.0)
    assert refs["cp_ref_area"] == pytest.approx(7.0 / 3.0)
    assert refs["cp_ref_flux"] == pytest.approx(2.0 / 3.0)
    assert refs["cm_area_mean"] == pytest.approx(3.0)
    assert refs["n_faces_jw_nonpositive"] == 1
    assert refs["area_jw_nonpositive"] == pytest.approx(1.0)
    metrics = distribution_cp_metrics(faces, c_b=5.0, b_perm=1.0)
    assert metrics["cpc_avg_area"] == pytest.approx(0.25)
    assert metrics["cm_q99"] == pytest.approx(4.0)
    assert metrics["cp_q99_area"] == pytest.approx(5.0 / 8.0)


def test_flux_weight_and_denominator_guards():
    with pytest.raises(ValueError, match="sum\\(Jw\\*A\\)"):
        reference_permeate([_face(2.0, 0.0, 1.0), _face(3.0, -0.5, 2.0)], b_perm=1.0)
    with pytest.raises(ValueError, match="c_b - cp_ref_area"):
        distribution_cp_metrics([_face(2.0, 1.0, 1.0)], c_b=1.0, b_perm=1.0)
    with pytest.raises(ValueError, match="c_b - cp_ref_flux"):
        distribution_cp_metrics(
            [_face(2.0, 0.1, 10.0), _face(100.0, 10.0, 1.0)],
            c_b=3.0,
            b_perm=1.0,
        )


def test_existing_summary_columns_unchanged():
    faces = [_face(2.0, 1.0e-5, 1.0), _face(4.0, 1.0e-5, 1.0)]
    report = concentration_cp_report(
        faces,
        {5: faces},
        c_b_window=600.0,
        c_b_by_cell={5: 610.0},
    )
    existing = _existing_rows()
    before = summary_rows_to_wide_record(existing)
    after = summary_rows_to_wide_record(existing + concentration_cp_summary_rows(report))
    for column, value in before.items():
        assert after[column] == value
    assert after["cpc_window_avg_area"] != before["cp_canon_window_avg"]
    assert after["cpc_cell_5_avg_area"] is not None
    assert "cp_canon_window_avg" not in {
        row["metric"] for row in concentration_cp_summary_rows(report)
    }
