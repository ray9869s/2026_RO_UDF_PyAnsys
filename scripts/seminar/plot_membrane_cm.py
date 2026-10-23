"""Seminar membrane maps from exported face arrays. Matplotlib only.

Each panel is ``c_m / c_b`` on ``wall_bottom_mem``, viewed from above, flow
left to right. The crop is the first two unit cells inside that geometry's
evaluation window. Inlet face centre is the origin. One colour range is the
1st and 99.5th percentiles of those five cropped sets. Panels have no colour
bar; a separate horizontal bar is written beside them.

Importing this module does not launch Fluent and does not read a case.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

from ro.campaign_geometry import geometry_parameters_for_geo_id  # noqa: E402
from ro.cp_concentration_stats import cell_x_bounds  # noqa: E402
from ro.domain_layout import (  # noqa: E402
    BUFFER_LENGTH_IN_M,
    BUFFER_LENGTH_OUT_M,
    DomainLayout,
    EvaluationWindow,
)
from ro.evaluation_window_table import select_evaluation_window  # noqa: E402

FIG_DIR = Path("C:/ro_data_mfbo/seminar_figs")
RESIDUAL_CSV = FIG_DIR / "residuals_P_p80_h15_u0p2_bl12s4.csv"
PRODUCTION_MESH_ID = "max085_min006_cpg5_bl4_peel2"
SEMINAR_GEO_IDS = (
    "REF_empty",
    "D2450_a45",
    "M_c267",
    "P_p80_h15",
    "S_a144_l3465",
)
PANEL_SIZE_IN = (2.18, 3.0)
COLORBAR_SIZE_IN = (11.67, 0.38)
RESIDUAL_SIZE_IN = (2.34, 1.85)
DPI = 300
CMAP = "viridis"
PERCENTILES = (1.0, 99.5)
Y_PAD_M = 1.0e-4
RESIDUAL_COLUMNS = (
    "continuity",
    "x-velocity",
    "y-velocity",
    "z-velocity",
    "nacl",
)
_RESIDUAL_KEY_ALIASES = {
    "iteration": "iter",
    "iterations": "iter",
}


def campaign_layout(geo_id):
    """Standard 1+n+2 campaign layout at this geometry's own pitch.

    Buffer lengths are the current-generation inlet 3.465 mm and outlet
    6.93 mm. ``periodic_shift_y_m`` is the spanwise unit-cell width.
    """
    geometry = geometry_parameters_for_geo_id(geo_id)
    layout = DomainLayout(
        n_buffer_in=1,
        n_active=int(geometry["n_active_cells"]),
        n_buffer_out=2,
        cell_length_x_m=float(geometry["cell_length_x_m"]),
        buffer_length_in_m=BUFFER_LENGTH_IN_M,
        buffer_length_out_m=BUFFER_LENGTH_OUT_M,
    )
    return layout, float(geometry["periodic_shift_y_m"])


def two_cell_crop(
    geo_id,
    *,
    layout=None,
    periodic_shift_y_m=None,
    mesh_id=PRODUCTION_MESH_ID,
    boundaries=None,
):
    """First two global cells inside the evaluation window, and their box.

    x increases downstream. y is centred on the inlet-face origin and spans
    one ``periodic_shift_y_m``.
    """
    if layout is None or periodic_shift_y_m is None:
        default_layout, default_shift = campaign_layout(geo_id)
        if layout is None:
            layout = default_layout
        if periodic_shift_y_m is None:
            periodic_shift_y_m = default_shift
    selection = select_evaluation_window(
        geo_id=geo_id,
        mesh_id=mesh_id,
        n_active=layout.n_active,
        cell_length_x_m=layout.cell_length_x_m,
        legacy_3_cell_window=False,
    )
    window = EvaluationWindow(
        n_lead_excluded=selection.n_lead_excluded,
        n_trail_excluded=0,
    )
    cells = window.evaluation_cell_numbers(layout)
    if len(cells) < 2:
        raise ValueError(
            f"{geo_id} evaluation window has {len(cells)} cells; need two."
        )
    first, second = int(cells[0]), int(cells[1])
    if second != first + 1:
        raise ValueError(
            f"{geo_id} first evaluation cells are not contiguous: "
            f"{first}, {second}."
        )
    if boundaries is None:
        boundaries = layout.boundary_positions(0.0)
    x_min, _x_hi = cell_x_bounds(boundaries, first)
    _x_lo, x_max = cell_x_bounds(boundaries, second)
    half = float(periodic_shift_y_m) / 2.0
    if half <= 0.0 or not (x_min < x_max):
        raise ValueError(
            f"{geo_id} crop is not a positive box: x=[{x_min}, {x_max}], "
            f"half-width={half}."
        )
    return {
        "cells": (first, second),
        "x_min_m": float(x_min),
        "x_max_m": float(x_max),
        "y_min_m": -half,
        "y_max_m": half,
        "cell_length_x_m": float(layout.cell_length_x_m),
        "periodic_shift_y_m": float(periodic_shift_y_m),
    }


def _scalar(payload, key):
    value = float(np.asarray(payload[key]).reshape(-1)[0])
    if not math.isfinite(value):
        raise ValueError(f"{key} is not finite: {value!r}.")
    return value


def crop_from_payload(payload):
    """Crop box stored on an export, else the geometry's own two-cell window."""
    if "crop_x_min_m" in payload:
        return {
            "cells": (
                int(np.asarray(payload["crop_cell_first"]).reshape(-1)[0]),
                int(np.asarray(payload["crop_cell_second"]).reshape(-1)[0]),
            ),
            "x_min_m": _scalar(payload, "crop_x_min_m"),
            "x_max_m": _scalar(payload, "crop_x_max_m"),
            "y_min_m": _scalar(payload, "crop_y_min_m"),
            "y_max_m": _scalar(payload, "crop_y_max_m"),
            "cell_length_x_m": _scalar(payload, "cell_length_x_m"),
            "periodic_shift_y_m": _scalar(payload, "periodic_shift_y_m"),
        }
    geo_id = str(np.asarray(payload["geo_id"]).reshape(-1)[0])
    return two_cell_crop(geo_id)


def cropped_local_cp(payload):
    """``c_m / c_b`` on bottom-wall faces inside the two-cell window."""
    x = np.asarray(payload["bottom_x"], dtype=float)
    y = np.asarray(payload["bottom_y"], dtype=float)
    cm = np.asarray(payload["bottom_udm_cm"], dtype=float)
    c_b = _scalar(payload, "c_b_window_mol_m3")
    if c_b <= 0.0:
        raise ValueError(f"c_b_window_mol_m3 must be positive, got {c_b!r}.")
    crop = crop_from_payload(payload)
    mask = (
        (x >= crop["x_min_m"])
        & (x <= crop["x_max_m"])
        & (y >= crop["y_min_m"] - Y_PAD_M)
        & (y <= crop["y_max_m"] + Y_PAD_M)
        & np.isfinite(cm)
    )
    if int(np.count_nonzero(mask)) < 3:
        geo_id = str(np.asarray(payload["geo_id"]).reshape(-1)[0])
        raise ValueError(
            f"{geo_id} has fewer than 3 bottom-wall faces in the two-cell window."
        )
    return x[mask], y[mask], cm[mask] / c_b, crop


def shared_color_limits(values):
    """1st and 99.5th percentiles of the pooled cropped face values."""
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if array.size < 2:
        raise ValueError("colour range needs at least two finite face values.")
    low, high = np.percentile(array, PERCENTILES)
    low = float(low)
    high = float(high)
    if not (low < high):
        raise ValueError(
            f"colour percentiles are not increasing: {low!r}, {high!r}."
        )
    return low, high


def draw_panel(x, y, values, crop, *, vmin, vmax):
    """One map. Flow is +x. No colour bar."""
    fig, ax = plt.subplots(figsize=PANEL_SIZE_IN, dpi=DPI)
    ax.tricontourf(
        x,
        y,
        values,
        levels=24,
        cmap=CMAP,
        vmin=vmin,
        vmax=vmax,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop["x_min_m"], crop["x_max_m"])
    ax.set_ylim(crop["y_min_m"], crop["y_max_m"])
    ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)
    return fig, ax


def draw_colorbar(vmin, vmax):
    """Horizontal bar for the shared ``c_m / c_b`` range."""
    fig = plt.figure(figsize=COLORBAR_SIZE_IN, dpi=DPI)
    ax = fig.add_axes((0.03, 0.45, 0.94, 0.40))
    mappable = ScalarMappable(norm=Normalize(vmin=vmin, vmax=vmax), cmap=CMAP)
    bar = fig.colorbar(mappable, cax=ax, orientation="horizontal")
    bar.set_label(r"$c_m / c_b$", fontsize=6)
    bar.ax.tick_params(labelsize=5, length=2, pad=1)
    return fig, ax


def _norm_residual_key(key):
    text = str(key).strip().lower().replace("_", "-")
    return _RESIDUAL_KEY_ALIASES.get(text, text)


def read_residual_history(path):
    """Rows of iteration and the five laminar residuals.

    A comma-separated header, or a whitespace Fluent-style header, is accepted.
    ``iteration`` is read as ``iter``.
    """
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"residual file is empty: {source}")
    if "," in lines[0]:
        raw_rows = list(csv.DictReader(io.StringIO(text)))
        rows = []
        for raw in raw_rows:
            rows.append(
                {
                    _norm_residual_key(key): value
                    for key, value in raw.items()
                    if key is not None and str(key).strip()
                }
            )
    else:
        names = [_norm_residual_key(token) for token in lines[0].split()]
        rows = []
        for line in lines[1:]:
            tokens = line.split()
            if len(tokens) < len(names):
                continue
            rows.append(dict(zip(names, tokens[: len(names)])))
    parsed = []
    for row in rows:
        missing = [name for name in ("iter", *RESIDUAL_COLUMNS) if name not in row]
        if missing:
            raise ValueError(
                f"residual file {source} is missing {missing} in {row!r}."
            )
        try:
            iteration = int(float(row["iter"]))
            values = {name: float(row[name]) for name in RESIDUAL_COLUMNS}
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"residual file {source} has a non-numeric row: {row!r}."
            ) from exc
        parsed.append({"iter": iteration, **values})
    if not parsed:
        raise ValueError(f"residual file {source} has no data rows.")
    parsed.sort(key=lambda item: item["iter"])
    return parsed


def draw_residuals(rows):
    """Log-y residual history. Iteration is on x."""
    fig, ax = plt.subplots(figsize=RESIDUAL_SIZE_IN, dpi=DPI)
    iterations = [row["iter"] for row in rows]
    for name in RESIDUAL_COLUMNS:
        series = [row[name] for row in rows]
        if not any(value > 0.0 for value in series):
            raise ValueError(f"residual {name} has no positive samples.")
        ax.plot(iterations, series, linewidth=0.8, label=name)
    ax.set_yscale("log")
    ax.set_xlabel("iteration", fontsize=6)
    ax.set_ylabel("residual", fontsize=6)
    ax.tick_params(labelsize=5, length=2, pad=1)
    ax.legend(fontsize=4, frameon=False, loc="upper right")
    fig.subplots_adjust(left=0.22, right=0.98, bottom=0.22, top=0.96)
    return fig, ax


def _save(fig, path):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=DPI)
    plt.close(fig)
    return destination


def npz_path(fig_dir, geo_id):
    return Path(fig_dir) / f"{geo_id}_u0p2_p6M_membrane_cm.npz"


def plot_membrane_maps(fig_dir, geo_ids=SEMINAR_GEO_IDS):
    """Write one panel PNG per geometry and one shared colour bar."""
    directory = Path(fig_dir)
    loaded = []
    pooled = []
    for geo_id in geo_ids:
        path = npz_path(directory, geo_id)
        if not path.is_file():
            raise FileNotFoundError(f"membrane export is missing: {path}")
        with np.load(path) as payload:
            x, y, values, crop = cropped_local_cp(payload)
        loaded.append((geo_id, x, y, values, crop))
        pooled.append(values)
    vmin, vmax = shared_color_limits(np.concatenate(pooled))
    written = []
    for geo_id, x, y, values, crop in loaded:
        fig, _ax = draw_panel(x, y, values, crop, vmin=vmin, vmax=vmax)
        written.append(
            _save(fig, directory / f"{geo_id}_cm_over_cb.png")
        )
    fig, _ax = draw_colorbar(vmin, vmax)
    written.append(_save(fig, directory / "cm_over_cb_colorbar.png"))
    return written, (vmin, vmax)


def plot_residual_history(csv_path, destination):
    rows = read_residual_history(csv_path)
    fig, _ax = draw_residuals(rows)
    return _save(fig, destination)


def png_pixel_size(path):
    """Width and height from the PNG IHDR."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG.")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Plot seminar membrane c_m/c_b panels, a shared colour bar, "
            "and the pillar residual history. Does not launch Fluent."
        )
    )
    parser.add_argument(
        "--fig-dir",
        default=str(FIG_DIR),
        help="Directory of the .npz exports and the PNG output.",
    )
    parser.add_argument(
        "--residual-csv",
        default=str(RESIDUAL_CSV),
        help="Residual history CSV. iteration, continuity, velocities, nacl.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    fig_dir = Path(args.fig_dir)
    written, (vmin, vmax) = plot_membrane_maps(fig_dir)
    residual = plot_residual_history(
        args.residual_csv,
        fig_dir / "residuals_P_p80_h15_u0p2_bl12s4.png",
    )
    written.append(residual)
    print(
        f"colour range c_m/c_b [{vmin:.6g}, {vmax:.6g}] "
        f"from percentiles {PERCENTILES[0]:g} and {PERCENTILES[1]:g}"
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
