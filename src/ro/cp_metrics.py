"""Pure-Python CP modulus definitions and averaging-order helpers.

Post-processing uses these after Fluent reduction returns area sums and
optional facet maxima. UDM-9 is already average-of-ratios at the UDF level;
canonical rescaling applies a per-cell scalar factor to those stored values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

# Bound on face-dependence of the scalar rescale factor k_N.
# Set to 1e-3 (not 1e-4): the old 1e-4 came from dk/dc_p ~ 2e-6, which
# assumes |c_0 - c_b| ~ 0.7 mol/m3. Measured window |c_0 - c_b| at p=6 MPa
# is 7.1 (u=0.3), 12.7 (u=0.2), ~29 (u=0.1), so dk/dc_p is ~2e-5–8e-5
# across the matrix — the 2e-6 premise never held. delta bounds relative
# error on M at about delta itself; Diamond-family CP discriminability is
# ~0.6%, so 1e-3 leaves a factor-of-6 margin. Observed delta at u=0.1,
# p=6 MPa is 2.32e-4; low-u / high-p cases sit in the few-times-1e-4
# range. Per-face canonical CP would remove the approximation but is
# blocked by Fluent 25.1's F_UDMI consumption defect.
CP_SCALAR_RESCALE_GUARD_THRESHOLD = 1.0e-3

# Area-backed facet-minimum hygiene for the unpaired cp_min/cp_max bound.
# surface-facetmin can return a zero-area iso-clip cut facet (UDM cell value
# from a non-membrane neighbour → exactly 0). Reject when the area fraction
# below facet_min*(1+rel) is < REJECT; substitute the lowest threshold whose
# iso_clip area fraction reaches TARGET (area-weighted low quantile).
FACET_MIN_AREA_CHECK_REL = 1.0e-3
FACET_MIN_REJECT_AREA_FRAC = 1.0e-9
FACET_MIN_TARGET_AREA_FRAC = 1.0e-4

# Shared iso_clip area-fraction bisection limits. Fluent surface-area reports
# have ~8 significant figures; chasing tighter than AREA_FRAC_TOL is noise.
BISECT_MAX_ITER = 40
BISECT_INTERVAL_REL_TOL = 1.0e-9
BISECT_AREA_FRAC_TOL = 1.0e-7


@dataclass(frozen=True)
class AreaFractionBisectResult:
    """Outcome of ``bisect_area_fraction_threshold``."""

    value: float
    iterations: int
    stop_reason: str  # "area_tol" | "interval_tol" | "max_iter"
    area_frac: float


def facet_min_check_threshold(
    facet_min: float,
    *,
    rel: float = FACET_MIN_AREA_CHECK_REL,
) -> float:
    """Upper bound for the area-support check: facet_min * (1 + rel)."""
    value = float(facet_min)
    r = float(rel)
    if r < 0.0:
        raise ValueError(f"rel must be non-negative, got {rel!r}.")
    return value * (1.0 + r)


def bisect_area_fraction_threshold(
    area_frac_below_fn,
    *,
    target_frac: float,
    lo: float,
    hi: float,
    max_iter: int = BISECT_MAX_ITER,
    interval_rel_tol: float = BISECT_INTERVAL_REL_TOL,
    area_frac_tol: float = BISECT_AREA_FRAC_TOL,
) -> AreaFractionBisectResult:
    """Lowest threshold ``T`` with ``area_frac_below_fn(T) >= target_frac``.

    Stops on the first of:
      - ``area_tol``: ``|frac - target| <= area_frac_tol`` with ``frac`` not
        materially below target (Fluent report noise floor ~1e-7);
      - ``interval_tol``: ``(hi - lo) / max(|hi|, |lo|) < interval_rel_tol``;
      - ``max_iter``: iteration budget exhausted (returns current ``hi``).
    """
    target = float(target_frac)
    lo_v = float(lo)
    hi_v = float(hi)
    if not math.isfinite(target) or target < 0.0 or target > 1.0:
        raise ValueError(f"target_frac must be in [0, 1], got {target_frac!r}.")
    if not math.isfinite(lo_v) or not math.isfinite(hi_v):
        raise ValueError(f"lo/hi must be finite, got lo={lo!r}, hi={hi!r}.")
    if hi_v < lo_v:
        lo_v, hi_v = hi_v, lo_v
    tol = float(area_frac_tol)
    if tol < 0.0:
        raise ValueError(f"area_frac_tol must be non-negative, got {area_frac_tol!r}.")

    def _frac(threshold: float) -> float:
        value = float(area_frac_below_fn(threshold))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                f"area_frac_below_fn must return a non-negative finite float, "
                f"got {value!r} at threshold={threshold!r}."
            )
        return value

    frac_hi = _frac(hi_v)
    if frac_hi < target - tol:
        return AreaFractionBisectResult(
            value=hi_v,
            iterations=0,
            stop_reason="max_iter",
            area_frac=frac_hi,
        )
    frac_lo = _frac(lo_v)
    if frac_lo >= target - tol:
        return AreaFractionBisectResult(
            value=lo_v,
            iterations=0,
            stop_reason="area_tol" if abs(frac_lo - target) <= tol else "interval_tol",
            area_frac=frac_lo,
        )

    best_hi = hi_v
    best_frac = frac_hi
    for iteration in range(1, int(max_iter) + 1):
        mid = 0.5 * (lo_v + hi_v)
        frac_mid = _frac(mid)
        # Area tolerance: measured fraction is within Fluent noise of target.
        if abs(frac_mid - target) <= tol and frac_mid >= target - tol:
            return AreaFractionBisectResult(
                value=mid,
                iterations=iteration,
                stop_reason="area_tol",
                area_frac=frac_mid,
            )
        if frac_mid < target:
            lo_v = mid
        else:
            hi_v = mid
            best_hi = mid
            best_frac = frac_mid
        scale = max(abs(hi_v), abs(lo_v), 1.0e-30)
        if (hi_v - lo_v) / scale < float(interval_rel_tol):
            return AreaFractionBisectResult(
                value=best_hi,
                iterations=iteration,
                stop_reason="interval_tol",
                area_frac=best_frac,
            )

    return AreaFractionBisectResult(
        value=best_hi,
        iterations=int(max_iter),
        stop_reason="max_iter",
        area_frac=best_frac,
    )


def resolve_area_backed_minimum(
    facet_min: float,
    *,
    area_frac_at_check: float,
    area_frac_below_fn,
    search_upper: float,
    reject_frac: float = FACET_MIN_REJECT_AREA_FRAC,
    target_frac: float = FACET_MIN_TARGET_AREA_FRAC,
    max_iter: int = BISECT_MAX_ITER,
) -> tuple[float, bool]:
    """Return ``(value_used, rejected)`` for an area-backed field minimum.

    ``area_frac_at_check`` is the area fraction with field below
    ``facet_min_check_threshold(facet_min)``. ``area_frac_below_fn(T)`` returns
    the area fraction with field below threshold ``T``. When the facet minimum
    has insufficient area support, bisect between ``facet_min`` and
    ``search_upper`` for the lowest ``T`` whose area fraction is at least
    ``target_frac``.
    """
    raw = float(facet_min)
    frac_check = float(area_frac_at_check)
    if not math.isfinite(frac_check) or frac_check < 0.0:
        raise ValueError(
            f"area_frac_at_check must be a non-negative finite float, "
            f"got {area_frac_at_check!r}."
        )
    if frac_check >= float(reject_frac):
        return raw, False

    hi = float(search_upper)
    if not math.isfinite(hi):
        raise ValueError(f"search_upper must be finite, got {search_upper!r}.")
    if hi < raw:
        hi = raw

    def _frac(threshold: float) -> float:
        value = float(area_frac_below_fn(threshold))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                f"area_frac_below_fn must return a non-negative finite float, "
                f"got {value!r} at threshold={threshold!r}."
            )
        return value

    # Expand the upper bracket if the initial search_upper undershoots.
    expand_guard = 0
    while _frac(hi) < float(target_frac) and expand_guard < 8:
        span = max(hi - raw, abs(hi), 1.0e-30)
        hi = hi + span
        expand_guard += 1
    if _frac(hi) < float(target_frac):
        return hi, True

    result = bisect_area_fraction_threshold(
        _frac,
        target_frac=float(target_frac),
        lo=raw,
        hi=hi,
        max_iter=int(max_iter),
    )
    return result.value, True


def film_theory_cp_perm_mol_m3(
    cm_mol_per_m3: float,
    jw_m_per_s: float,
    b_perm_m_per_s: float,
) -> float:
    """Face or segment c_p = B*cm/(Jw+B) [mol/m3]."""
    cm = float(cm_mol_per_m3)
    jw = float(jw_m_per_s)
    b = float(b_perm_m_per_s)
    if b <= 0.0:
        raise ValueError(f"b_perm must be positive, got {b_perm_m_per_s!r}.")
    denom = jw + b
    if denom <= 0.0:
        raise ValueError(
            f"Jw + B must be positive for cp_perm: Jw={jw!r}, B={b!r}."
        )
    return b * cm / denom


def cp_perm_expression(
    b_perm: float,
    cm_field: str = "udm-7",
    jw_field: str = "udm-6",
) -> str:
    """Fluent expression for film-theory c_p = B*cm/(Jw+B) [mol/m3]."""
    b = float(b_perm)
    return f"({b!r} * ({cm_field}) / (({jw_field}) + {b!r}))"


def cp_l2_bae_approx_expression(
    c0_mol_per_m3: float,
    b_perm: float,
    cm_field: str = "udm-7",
    jw_field: str = "udm-6",
) -> str:
    """Ratio-of-averages Bae 2023 approx form — wrong averaging order."""
    cp_perm = cp_perm_expression(b_perm, cm_field, jw_field)
    c0 = float(c0_mol_per_m3)
    return f"((({cm_field}) - ({cp_perm})) / ({c0!r} - ({cp_perm})))"


def scalar_rescale_guard_delta(
    c0_mol_per_m3: float,
    c_b_cell_mol_per_m3: float,
    cp_perm_min_mol_per_m3: float,
    cp_perm_max_mol_per_m3: float,
) -> float:
    """Bound on face-spread error when replacing c_0 with c_b in the denominator.

    delta = |c_0 - c_b| * spread(c_p) / (c_b - c_p_min)^2
    """
    c0 = float(c0_mol_per_m3)
    cb = float(c_b_cell_mol_per_m3)
    cp_min = float(cp_perm_min_mol_per_m3)
    cp_max = float(cp_perm_max_mol_per_m3)
    denom = cb - cp_min
    if denom <= 0.0:
        raise ValueError(
            f"c_b - c_p_min must be positive for scalar-rescale guard: "
            f"c_b={cb!r}, c_p_min={cp_min!r}."
        )
    spread = cp_max - cp_min
    return abs(c0 - cb) * spread / (denom * denom)


def canonical_rescale_factor(
    c0_mol_per_m3: float,
    c_b_cell_mol_per_m3: float,
    cp_perm_avg_mol_per_m3: float,
    *,
    cp_perm_min_mol_per_m3: Optional[float] = None,
    cp_perm_max_mol_per_m3: Optional[float] = None,
) -> tuple[float, float]:
    """Return (k_N, delta) for CP_canon = CP_udm9 * k_N.

    Raises if c_b - c_p_avg <= 0 or if delta exceeds the guard threshold.
    """
    c0 = float(c0_mol_per_m3)
    cb = float(c_b_cell_mol_per_m3)
    cp_avg = float(cp_perm_avg_mol_per_m3)
    denom = cb - cp_avg
    if denom <= 0.0:
        raise ValueError(
            f"c_b - c_p_avg must be positive: c_b={cb!r}, c_p_avg={cp_avg!r}."
        )
    k = (c0 - cp_avg) / denom
    if cp_perm_min_mol_per_m3 is None or cp_perm_max_mol_per_m3 is None:
        return k, 0.0
    delta = scalar_rescale_guard_delta(
        c0,
        cb,
        cp_perm_min_mol_per_m3,
        cp_perm_max_mol_per_m3,
    )
    if delta > CP_SCALAR_RESCALE_GUARD_THRESHOLD:
        raise ValueError(
            f"CP scalar-rescale guard failed: delta={delta:.6g} exceeds "
            f"threshold {CP_SCALAR_RESCALE_GUARD_THRESHOLD:.6g}."
        )
    return k, delta


def cp_l1_gu2017(cm_avg_mol_per_m3: float, c_b_cell_mol_per_m3: float) -> float:
    """Gu 2017 M = c_m / c_b. Denominator is face-independent so order is exact."""
    cb = float(c_b_cell_mol_per_m3)
    if cb <= 0.0:
        raise ValueError(f"c_b must be positive, got {cb!r}.")
    return float(cm_avg_mol_per_m3) / cb


def window_area_weighted_average(
    values_by_cell: Mapping[int, float],
    areas_by_cell: Mapping[int, float],
    cell_numbers: Sequence[int],
) -> float:
    """Area-weighted mean of per-cell scalars over the listed cells."""
    total_area = 0.0
    weighted = 0.0
    for cell_number in cell_numbers:
        area = areas_by_cell.get(cell_number)
        value = values_by_cell.get(cell_number)
        if area is None or value is None:
            raise ValueError(
                f"Missing area or value for evaluation cell {cell_number}."
            )
        if area <= 0.0:
            raise ValueError(
                f"Non-positive membrane area for evaluation cell {cell_number}: "
                f"{area!r}."
            )
        total_area += area
        weighted += value * area
    if total_area <= 0.0:
        raise ValueError("Evaluation-window membrane area sum is not positive.")
    return weighted / total_area


def window_pointwise_max(
    max_values_by_cell: Mapping[int, float],
    cell_numbers: Sequence[int],
) -> float:
    """Maximum of per-cell facet maxima across evaluation cells."""
    values = []
    for cell_number in cell_numbers:
        value = max_values_by_cell.get(cell_number)
        if value is None:
            raise ValueError(
                f"Missing facet max for evaluation cell {cell_number}."
            )
        values.append(float(value))
    return max(values)


def ratio_of_averages_cp_bae_approx(
    cm_area_sum: float,
    cp_perm_area_sum: float,
    area_m2: float,
    c0_mol_per_m3: float,
) -> float:
    """Wrong order: average(cm) and average(cp_perm) then divide."""
    if area_m2 <= 0.0:
        raise ValueError("area_m2 must be positive.")
    cm_avg = cm_area_sum / area_m2
    cp_perm_avg = cp_perm_area_sum / area_m2
    denom = c0_mol_per_m3 - cp_perm_avg
    if denom <= 0.0:
        raise ValueError(
            f"Bae approx denominator must be positive: c0={c0_mol_per_m3!r}, "
            f"cp_perm_avg={cp_perm_avg!r}."
        )
    return (cm_avg - cp_perm_avg) / denom


def average_of_ratios_cp_bae_approx(
    cp_udm9_area_sum: float,
    area_m2: float,
) -> float:
    """Correct order: UDM-9 is per-face CP already area-weighted into cells."""
    if area_m2 <= 0.0:
        raise ValueError("area_m2 must be positive.")
    return cp_udm9_area_sum / area_m2


def midplane_window_bulk_aggregate(
    c_b_by_cell: Mapping[int, float],
    midplane_areas_by_cell: Mapping[int, float],
    cell_numbers: Sequence[int],
) -> float:
    """Area-weighted c_b_window over evaluation cells on the mid-plane."""
    return window_area_weighted_average(
        c_b_by_cell,
        midplane_areas_by_cell,
        cell_numbers,
    )


def build_window_cp_metric_keys(definition: str, scope: str) -> tuple[str, str]:
    """Return (avg_key, max_key) for a definition label and scope."""
    return (
        f"cp_{definition}_{scope}_avg",
        f"cp_{definition}_{scope}_max",
    )
