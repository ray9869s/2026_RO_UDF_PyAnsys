"""Campaign CP contour inputs, piecewise k_N, view bounds, CSV agreement."""

from __future__ import annotations

import csv

import pytest

from ro.contour_campaign import (
    CellSpan,
    contour_view_bounds_xy,
    piecewise_k_expression,
    read_campaign_cp_contour_inputs,
    require_figure_matches_csv,
    window_mask_expression,
)
from ro.domain_layout import layout_from_mesh_manifest
from ro.manifest import write_mesh_manifest
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload, write_test_run


def _write_mesh(monkeypatch, tmp_path, **updates):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload.update(updates)
    write_mesh_manifest(directory, payload)
    return directory, payload


def _wide_csv(path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _campaign_row(layout_dir, *, drop_k_cell=None, window_avg=None):
    record = layout_from_mesh_manifest(layout_dir)
    layout = record.layout
    boundaries = layout.boundary_positions(0.0)
    row = {}
    for index, x_m in enumerate(boundaries):
        row[f"pp_unit_cell_boundary_{index}_x_m"] = f"{x_m:.12g}"
    eval_cells = record.evaluation_window.evaluation_cell_numbers(layout)
    weighted = 0.0
    area_sum = 0.0
    for cell in layout.active_cell_numbers():
        if drop_k_cell is not None and cell == drop_k_cell:
            continue
        k_n = 0.99 + 0.001 * cell
        area = 0.002 + 0.0001 * cell
        cp = 1.10 + 0.01 * cell
        row[f"pp_cp_canon_rescale_k_cell_{cell}"] = f"{k_n:.12g}"
        row[f"pp_membrane_area_cell_{cell}_m2"] = f"{area:.12g}"
        row[f"pp_cp_canon_cell_{cell}"] = f"{cp:.12g}"
        if cell in eval_cells:
            weighted += cp * area
            area_sum += area
    row["cp_canon_window_avg"] = (
        f"{window_avg:.12g}" if window_avg is not None else f"{weighted / area_sum:.12g}"
    )
    return row, eval_cells


def test_contour_view_bounds_use_measured_x_not_ten_cell_default():
    bounds = contour_view_bounds_xy(
        {
            "domain_extent_x_m": 0.03465,
            "domain_extent_y_m": 0.003469131,
        }
    )
    assert bounds.xmin == 0.0
    assert bounds.xmax == pytest.approx(0.03465)
    assert bounds.ymax == pytest.approx(0.003469131 / 2.0)
    assert bounds.ymin == pytest.approx(-0.003469131 / 2.0)
    assert "0.010395" not in bounds.as_cli()
    assert bounds.x_source == "domain_extent_x_m"
    assert bounds.y_source == "domain_extent_y_m"


def test_contour_view_bounds_fall_back_to_periodic_shift_y():
    bounds = contour_view_bounds_xy(
        {
            "domain_extent_x_m": 0.031185,
            "periodic_shift_y_m": 0.003465,
        }
    )
    assert bounds.xmax == pytest.approx(0.031185)
    assert bounds.y_source == "periodic_shift_y_m"


def test_contour_view_bounds_refuse_missing_x_extent():
    with pytest.raises(ValueError, match="domain_extent_x_m"):
        contour_view_bounds_xy({"periodic_shift_y_m": 0.003465})


def test_piecewise_k_expression_is_per_cell_not_a_window_scalar():
    spans = (
        CellSpan(2, 0.991, 0.003465, 0.00693, None, None),
        CellSpan(3, 0.993, 0.00693, 0.010395, None, None),
    )
    expr = piecewise_k_expression(spans, "X")
    assert "0.991" in expr
    assert "0.993" in expr
    assert "IfThenElse" in expr
    assert "GE(X,0.003465)" in expr


def test_window_mask_covers_evaluation_x_span():
    expr = window_mask_expression(0.010395, 0.024255, "X")
    assert "GE(X,0.010395)" in expr
    assert "LE(X,0.024255)" in expr


def test_require_figure_matches_csv_passes_within_tol():
    require_figure_matches_csv(1.123, 1.123)


def test_require_figure_matches_csv_fails_loudly():
    with pytest.raises(ValueError, match="Refusing to render"):
        require_figure_matches_csv(1.20, 1.10)


def test_read_campaign_cp_uses_per_cell_k_and_eval_window(
    monkeypatch, tmp_path
):
    mesh_directory, _payload = _write_mesh(monkeypatch, tmp_path)
    run_directory = write_test_run()
    row, eval_cells = _campaign_row(mesh_directory)
    _wide_csv(run_directory / "post" / "reports" / "summary_metrics_wide.csv", row)

    inputs = read_campaign_cp_contour_inputs(run_directory)
    assert list(inputs.evaluation_cells) == list(eval_cells)
    assert {span.cell_number for span in inputs.active_spans} == set(
        layout_from_mesh_manifest(mesh_directory).layout.active_cell_numbers()
    )
    k_by_cell = {span.cell_number: span.k_n for span in inputs.active_spans}
    assert k_by_cell[eval_cells[0]] != k_by_cell[eval_cells[-1]]


def test_read_campaign_cp_refuses_partial_active_k(monkeypatch, tmp_path):
    mesh_directory, _payload = _write_mesh(monkeypatch, tmp_path)
    run_directory = write_test_run()
    row, _eval_cells = _campaign_row(mesh_directory, drop_k_cell=2)
    _wide_csv(run_directory / "post" / "reports" / "summary_metrics_wide.csv", row)
    with pytest.raises(ValueError, match="missing pp_cp_canon_rescale_k_cell"):
        read_campaign_cp_contour_inputs(run_directory)


def test_read_campaign_cp_refuses_csv_internal_window_mismatch(
    monkeypatch, tmp_path
):
    mesh_directory, _payload = _write_mesh(monkeypatch, tmp_path)
    run_directory = write_test_run()
    row, _eval_cells = _campaign_row(mesh_directory, window_avg=9.99)
    _wide_csv(run_directory / "post" / "reports" / "summary_metrics_wide.csv", row)
    with pytest.raises(ValueError, match="cp_canon_window_avg"):
        read_campaign_cp_contour_inputs(run_directory)
