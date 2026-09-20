"""Campaign-defensible contour helpers: per-cell k_N, CSV agreement, view bounds.

CP contours must use the same per-cell ``k_N`` as ``cp_canon_window_avg``.
View bounds come from the mesh manifest measured extent, not a 10-cell
literal.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ro.domain_layout import DomainLayout, layout_from_run_directory

# Same numerical guard as mid-plane c_b vs x-normal mixing-cup.
FIGURE_CSV_REL_TOL = 5.0e-3
FIGURE_CSV_ABS_TOL = 0.0


@dataclass(frozen=True)
class CellSpan:
    cell_number: int
    k_n: float
    x_min_m: float
    x_max_m: float
    area_m2: Optional[float]
    cp_canon: Optional[float]


@dataclass(frozen=True)
class CampaignCpContourInputs:
    """Per-cell k and x spans plus the CSV window average to check against."""

    evaluation_cells: tuple[int, ...]
    active_spans: tuple[CellSpan, ...]
    evaluation_x_min_m: float
    evaluation_x_max_m: float
    csv_cp_canon_window_avg: float
    csv_path: Path


@dataclass(frozen=True)
class ContourViewBounds:
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    x_source: str
    y_source: str

    def as_cli(self) -> str:
        return (
            f"{self.xmin:.12g},{self.xmax:.12g},"
            f"{self.ymin:.12g},{self.ymax:.12g}"
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.xmax, self.ymin, self.ymax)


def _csv_float(row: Mapping[str, str], key: str) -> float:
    raw = (row.get(key) or "").strip()
    if not raw or raw.lower() in ("none", "nan"):
        raise ValueError(f"CSV column {key!r} is missing or non-numeric: {raw!r}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"CSV column {key!r} is not numeric: {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"CSV column {key!r} is not finite: {value!r}")
    return value


def _read_wide_csv_row(csv_path: Path) -> dict[str, str]:
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"summary_metrics_wide.csv is required for a campaign CP contour: "
            f"{csv_path}"
        )
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{csv_path} has no data row.")
    return rows[0]


def _optional_csv_float(row: Mapping[str, str], key: str) -> Optional[float]:
    raw = (row.get(key) or "").strip()
    if not raw or raw.lower() in ("none", "nan"):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def _boundaries_from_csv_or_layout(
    row: Mapping[str, str],
    layout: DomainLayout,
) -> list[float]:
    n_planes = layout.n_total + 1
    values: list[float] = []
    for index in range(n_planes):
        key = f"pp_unit_cell_boundary_{index}_x_m"
        parsed = _optional_csv_float(row, key)
        if parsed is None:
            break
        values.append(parsed)
    if len(values) == n_planes:
        return values
    return layout.boundary_positions(0.0)


def read_campaign_cp_contour_inputs(case_path) -> CampaignCpContourInputs:
    """Load per-cell k_N, x spans, and cp_canon_window_avg from one extract CSV.

    Requires k for every active cell and refuses a partial evaluation-window
    key set. Reads layout from the run's mesh manifest, not from whichever
    keys happen to be present.
    """
    case_dir = Path(case_path)
    csv_path = case_dir / "post" / "reports" / "summary_metrics_wide.csv"
    row = _read_wide_csv_row(csv_path)
    layout_record, _run_payload = layout_from_run_directory(case_dir)
    layout = layout_record.layout
    evaluation_cells = tuple(
        layout_record.evaluation_window.evaluation_cell_numbers(layout)
    )
    if not evaluation_cells:
        raise ValueError("evaluation window is empty; cannot build a CP contour.")

    csv_avg = _csv_float(row, "cp_canon_window_avg")
    boundaries = _boundaries_from_csv_or_layout(row, layout)
    if len(boundaries) != layout.n_total + 1:
        raise ValueError(
            f"Need {layout.n_total + 1} unit-cell boundary x values, "
            f"got {len(boundaries)}."
        )

    active_cells = layout.active_cell_numbers()
    missing_k = [
        cell
        for cell in active_cells
        if _optional_csv_float(row, f"pp_cp_canon_rescale_k_cell_{cell}") is None
    ]
    if missing_k:
        raise ValueError(
            "CSV is missing pp_cp_canon_rescale_k_cell_N for active cells "
            f"{missing_k}; refusing a window-average k fallback."
        )
    extra_eval = [cell for cell in evaluation_cells if cell not in active_cells]
    if extra_eval:
        raise ValueError(
            f"evaluation cells {extra_eval} are not in the active span."
        )

    spans: list[CellSpan] = []
    for cell in active_cells:
        k_n = _csv_float(row, f"pp_cp_canon_rescale_k_cell_{cell}")
        if k_n <= 0.0:
            raise ValueError(
                f"pp_cp_canon_rescale_k_cell_{cell} must be positive, got {k_n!r}."
            )
        x_min_m = boundaries[cell - 1]
        x_max_m = boundaries[cell]
        if not (x_min_m < x_max_m):
            raise ValueError(
                f"cell {cell} x span is not increasing: "
                f"[{x_min_m!r}, {x_max_m!r}]."
            )
        spans.append(
            CellSpan(
                cell_number=cell,
                k_n=k_n,
                x_min_m=x_min_m,
                x_max_m=x_max_m,
                area_m2=_optional_csv_float(
                    row, f"pp_membrane_area_cell_{cell}_m2"
                ),
                cp_canon=_optional_csv_float(
                    row, f"pp_cp_canon_cell_{cell}"
                ),
            )
        )

    eval_set = set(evaluation_cells)
    eval_spans = [span for span in spans if span.cell_number in eval_set]
    if [span.cell_number for span in eval_spans] != list(evaluation_cells):
        raise ValueError(
            "evaluation-window cell set does not match CSV k spans: "
            f"expected {list(evaluation_cells)}, "
            f"got {[span.cell_number for span in eval_spans]}."
        )

    csv_from_cells = _window_avg_from_cell_columns(eval_spans)
    if csv_from_cells is not None:
        require_figure_matches_csv(
            csv_from_cells,
            csv_avg,
            what="CSV pp_cp_canon_cell_N window mean vs cp_canon_window_avg",
        )

    return CampaignCpContourInputs(
        evaluation_cells=evaluation_cells,
        active_spans=tuple(spans),
        evaluation_x_min_m=eval_spans[0].x_min_m,
        evaluation_x_max_m=eval_spans[-1].x_max_m,
        csv_cp_canon_window_avg=csv_avg,
        csv_path=csv_path,
    )


def _window_avg_from_cell_columns(eval_spans: Sequence[CellSpan]) -> Optional[float]:
    if any(span.area_m2 is None or span.cp_canon is None for span in eval_spans):
        return None
    total_area = 0.0
    weighted = 0.0
    for span in eval_spans:
        area = float(span.area_m2)
        if area <= 0.0:
            raise ValueError(
                f"pp_membrane_area_cell_{span.cell_number}_m2 must be positive."
            )
        total_area += area
        weighted += float(span.cp_canon) * area
    if total_area <= 0.0:
        raise ValueError("evaluation-window membrane area is not positive.")
    return weighted / total_area


def _quote_ensight_var(name: str) -> str:
    if any(ch in name for ch in (" ", "-", "[", "]")):
        return f"'{name}'"
    return name


def piecewise_k_expression(spans: Sequence[CellSpan], x_var: str) -> str:
    """EnSight calculator expression for k_N(x) on the active membrane span."""
    if not spans:
        raise ValueError("piecewise k_N needs at least one active cell span.")
    x_tok = _quote_ensight_var(x_var)
    expr = "0"
    ordered = list(spans)
    for index, span in enumerate(reversed(ordered)):
        is_last = index == 0
        right_op = "LE" if is_last else "LT"
        test = (
            f"AND(GE({x_tok},{span.x_min_m:.12g}),"
            f"{right_op}({x_tok},{span.x_max_m:.12g}))"
        )
        expr = f"IfThenElse({test},{span.k_n:.12g},{expr})"
    return expr


def window_mask_expression(
    x_min_m: float,
    x_max_m: float,
    x_var: str,
) -> str:
    x_tok = _quote_ensight_var(x_var)
    return (
        f"IfThenElse(AND(GE({x_tok},{x_min_m:.12g}),"
        f"LE({x_tok},{x_max_m:.12g})),1,0)"
    )


def cp_canon_field_expression(udm9_desc: str, k_expr: str) -> str:
    return f"{_quote_ensight_var(udm9_desc)} * ({k_expr})"


def require_figure_matches_csv(
    figure_mean: float,
    csv_mean: float,
    *,
    rel_tol: float = FIGURE_CSV_REL_TOL,
    abs_tol: float = FIGURE_CSV_ABS_TOL,
    what: str = "figure area-weighted mean vs cp_canon_window_avg",
) -> None:
    """Raise unless the figure mean agrees with the extract table."""
    if not math.isfinite(figure_mean) or not math.isfinite(csv_mean):
        raise ValueError(
            f"{what}: non-finite values figure={figure_mean!r} csv={csv_mean!r}."
        )
    if math.isclose(figure_mean, csv_mean, rel_tol=rel_tol, abs_tol=abs_tol):
        return
    denom = abs(csv_mean) if csv_mean != 0.0 else 1.0
    rel_err = abs(figure_mean - csv_mean) / denom
    raise ValueError(
        f"{what}: figure={figure_mean:.8g} csv={csv_mean:.8g} "
        f"rel_err={rel_err:.3e} (rel_tol={rel_tol}). "
        "Refusing to render a canonical CP figure that disagrees with the table."
    )


def contour_view_bounds_xy(mesh_payload: Mapping[str, object]) -> ContourViewBounds:
    """XY camera bounds from measured mesh extent.

    x is ``[0, domain_extent_x_m]``. y is origin-centred with width
    ``domain_extent_y_m`` when present, else ``periodic_shift_y_m``.
    There is no 10-cell numeric default.
    """
    x_max = mesh_payload.get("domain_extent_x_m")
    if x_max is None:
        raise ValueError(
            "mesh manifest domain_extent_x_m is required to frame a contour; "
            "refusing the 10-cell 0.010395 default."
        )
    if isinstance(x_max, bool) or not isinstance(x_max, (int, float)):
        raise TypeError(f"domain_extent_x_m must be numeric, got {x_max!r}.")
    x_max_f = float(x_max)
    if not math.isfinite(x_max_f) or x_max_f <= 0.0:
        raise ValueError(f"domain_extent_x_m must be positive, got {x_max_f!r}.")

    y_span = mesh_payload.get("domain_extent_y_m")
    y_source = "domain_extent_y_m"
    if y_span is None:
        y_span = mesh_payload.get("periodic_shift_y_m")
        y_source = "periodic_shift_y_m"
    if y_span is None:
        raise ValueError(
            "mesh manifest needs domain_extent_y_m or periodic_shift_y_m "
            "to frame the contour in y."
        )
    if isinstance(y_span, bool) or not isinstance(y_span, (int, float)):
        raise TypeError(f"{y_source} must be numeric, got {y_span!r}.")
    y_span_f = float(y_span)
    if not math.isfinite(y_span_f) or y_span_f <= 0.0:
        raise ValueError(f"{y_source} must be positive, got {y_span_f!r}.")
    half = 0.5 * y_span_f
    return ContourViewBounds(
        xmin=0.0,
        xmax=x_max_f,
        ymin=-half,
        ymax=half,
        x_source="domain_extent_x_m",
        y_source=y_source,
    )
