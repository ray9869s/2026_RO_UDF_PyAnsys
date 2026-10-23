"""Seminar membrane maps from synthetic face arrays. No Fluent."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from helpers import SCRIPTS_DIR, load_module

PLOT = load_module(
    "plot_membrane_cm_under_test",
    SCRIPTS_DIR / "seminar" / "plot_membrane_cm.py",
)
EXPORT = load_module(
    "export_membrane_cm_under_test",
    SCRIPTS_DIR / "seminar" / "export_membrane_cm.py",
)

MESH = "max085_min006_cpg5_bl4_peel2"


def _png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_two_cell_crop_uses_each_geometry_window():
    diamond = PLOT.two_cell_crop("D2450_a45")
    pillar = PLOT.two_cell_crop("P_p80_h15")
    empty = PLOT.two_cell_crop("REF_empty")
    ml = PLOT.two_cell_crop("M_c267")
    sine = PLOT.two_cell_crop("S_a144_l3465")

    assert diamond["cells"] == (6, 7)
    assert empty["cells"] == (6, 7)
    assert sine["cells"] == (6, 7)
    assert pillar["cells"] == (5, 6)
    assert ml["cells"] == (5, 6)

    pitch = 0.003465
    assert diamond["cell_length_x_m"] == pytest.approx(pitch)
    assert pillar["cell_length_x_m"] == pytest.approx(pitch)
    assert diamond["x_max_m"] - diamond["x_min_m"] == pytest.approx(2 * pitch)
    assert pillar["x_max_m"] - pillar["x_min_m"] == pytest.approx(2 * pitch)
    assert diamond["x_min_m"] == pytest.approx(5 * pitch)
    assert pillar["x_min_m"] == pytest.approx(4 * pitch)
    assert diamond["y_max_m"] - diamond["y_min_m"] == pytest.approx(pitch)
    assert diamond["y_min_m"] == pytest.approx(-pitch / 2)
    assert diamond["x_min_m"] < diamond["x_max_m"]


def test_maps_share_colour_range_and_panel_size(tmp_path):
    c_b = 600.0
    in_window = []
    for index, geo_id in enumerate(PLOT.SEMINAR_GEO_IDS):
        crop = PLOT.two_cell_crop(geo_id)
        xs = np.linspace(crop["x_min_m"], crop["x_max_m"], 5)
        ys = np.linspace(crop["y_min_m"], crop["y_max_m"], 4)
        xx, yy = np.meshgrid(xs, ys)
        local = 1.1 + 0.05 * index + 0.01 * xx.ravel()
        cm = local * c_b
        in_window.append(local)
        x = np.concatenate([xx.ravel(), [crop["x_min_m"] - 0.01]])
        y = np.concatenate([yy.ravel(), [0.0]])
        udm = np.concatenate([cm, [50.0 * c_b]])
        area = np.ones_like(x)
        np.savez_compressed(
            tmp_path / f"{geo_id}_u0p2_p6M_membrane_cm.npz",
            geo_id=np.asarray(geo_id),
            c_b_window_mol_m3=np.asarray(c_b),
            cell_length_x_m=np.asarray(crop["cell_length_x_m"]),
            periodic_shift_y_m=np.asarray(crop["periodic_shift_y_m"]),
            crop_cell_first=np.asarray(crop["cells"][0]),
            crop_cell_second=np.asarray(crop["cells"][1]),
            crop_x_min_m=np.asarray(crop["x_min_m"]),
            crop_x_max_m=np.asarray(crop["x_max_m"]),
            crop_y_min_m=np.asarray(crop["y_min_m"]),
            crop_y_max_m=np.asarray(crop["y_max_m"]),
            bottom_x=x,
            bottom_y=y,
            bottom_udm_cm=udm,
            bottom_area=area,
        )
    expected_low, expected_high = PLOT.shared_color_limits(np.concatenate(in_window))
    assert expected_high < 10.0

    written, limits = PLOT.plot_membrane_maps(tmp_path)
    assert limits == pytest.approx((expected_low, expected_high))
    panels = [
        tmp_path / f"{geo_id}_cm_over_cb.png" for geo_id in PLOT.SEMINAR_GEO_IDS
    ]
    colourbar = tmp_path / "cm_over_cb_colorbar.png"
    assert written[:-1] == panels
    assert written[-1] == colourbar
    for path in panels:
        assert _png_size(path) == (
            round(PLOT.PANEL_SIZE_IN[0] * PLOT.DPI),
            round(PLOT.PANEL_SIZE_IN[1] * PLOT.DPI),
        )
    assert _png_size(colourbar) == (
        round(PLOT.COLORBAR_SIZE_IN[0] * PLOT.DPI),
        round(PLOT.COLORBAR_SIZE_IN[1] * PLOT.DPI),
    )

    crop = PLOT.two_cell_crop("D2450_a45")
    with np.load(tmp_path / "D2450_a45_u0p2_p6M_membrane_cm.npz") as payload:
        x, y, values, used = PLOT.cropped_local_cp(payload)
    fig, ax = PLOT.draw_panel(x, y, values, used, vmin=limits[0], vmax=limits[1])
    assert len(fig.axes) == 1
    assert ax.get_xlim() == pytest.approx((crop["x_min_m"], crop["x_max_m"]))
    assert ax.get_xlim()[0] < ax.get_xlim()[1]
    PLOT.plt.close(fig)


def test_residual_history_is_log_y(tmp_path):
    csv_path = tmp_path / "residuals.csv"
    csv_path.write_text(
        "iteration,continuity,x-velocity,y-velocity,z-velocity,nacl\n"
        "1,1.0e-1,2.0e-1,3.0e-1,4.0e-1,5.0e-1\n"
        "2,1.0e-3,2.0e-3,3.0e-3,4.0e-3,5.0e-3\n",
        encoding="utf-8",
    )
    rows = PLOT.read_residual_history(csv_path)
    assert [row["iter"] for row in rows] == [1, 2]
    assert rows[1]["nacl"] == pytest.approx(5.0e-3)
    fig, ax = PLOT.draw_residuals(rows)
    assert ax.get_yscale() == "log"
    assert [line.get_label() for line in ax.get_lines()] == list(PLOT.RESIDUAL_COLUMNS)
    assert ax.get_xlabel() == "iteration"
    destination = tmp_path / "residuals_P_p80_h15_u0p2_bl12s4.png"
    PLOT.plt.close(fig)
    written = PLOT.plot_residual_history(csv_path, destination)
    assert written == destination
    assert _png_size(destination) == (
        round(PLOT.RESIDUAL_SIZE_IN[0] * PLOT.DPI),
        round(PLOT.RESIDUAL_SIZE_IN[1] * PLOT.DPI),
    )


def _plant_leaf(root):
    leaf = root / "runs" / "empty" / "REF_empty" / MESH / "u0p2_p6M"
    leaf.mkdir(parents=True)
    (leaf / "REF_empty_u0p2_p6M_final.cas.h5").write_bytes(b"case")
    (leaf / "REF_empty_u0p2_p6M_final.dat.h5").write_bytes(b"data")
    (leaf / "libudf").mkdir()
    (leaf / "libudf" / "udf_names.c").write_text("ok", encoding="utf-8")
    (leaf / "260822_RO_UDF.c").write_text("source", encoding="utf-8")
    (leaf / "manifest.json").write_text(
        json.dumps(
            {
                "family": "empty",
                "geo_id": "REF_empty",
                "mesh_id": MESH,
                "run_id": "u0p2_p6M",
                "udf_version": "260822_RO_UDF.c",
            }
        ),
        encoding="utf-8",
    )
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text(
        "c_b_window_mol_m3\n597.8\n",
        encoding="utf-8",
    )
    return leaf


def test_scratch_copy_does_not_touch_the_leaf(tmp_path):
    leaf = _plant_leaf(tmp_path)
    before = {
        path: path.read_bytes()
        for path in leaf.rglob("*")
        if path.is_file()
    }
    scratch = tmp_path / "work" / "REF_empty"
    identity, case_file = EXPORT.copy_leaf_to_scratch(leaf, scratch)
    assert identity["geo_id"] == "REF_empty"
    assert case_file.name == "REF_empty_u0p2_p6M_final.cas.h5"
    assert (scratch / "libudf" / "udf_names.c").read_text(encoding="utf-8") == "ok"
    assert (scratch / "260822_RO_UDF.c").read_text(encoding="utf-8") == "source"
    after = {
        path: path.read_bytes()
        for path in leaf.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert EXPORT.read_c_b_window(leaf) == pytest.approx(597.8)
    with pytest.raises(ValueError, match="overlaps"):
        EXPORT.copy_leaf_to_scratch(leaf, leaf / "nested")


def test_free_ram_gate_refuses_below_16_gb(capsys):
    assert EXPORT.require_free_ram(16 * 1024**3) == 16 * 1024**3
    assert "free RAM 16.00 GiB" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="below 16 GB"):
        EXPORT.require_free_ram(16 * 1024**3 - 1)
    assert EXPORT.LEAF_PATHS[0].endswith(
        "empty/REF_empty/max085_min006_cpg5_bl4_peel2/u0p2_p6M"
    )
    assert [path.split("/")[-3] for path in EXPORT.LEAF_PATHS] == list(
        PLOT.SEMINAR_GEO_IDS
    )
    assert EXPORT.PROCESSOR_COUNT == 4
