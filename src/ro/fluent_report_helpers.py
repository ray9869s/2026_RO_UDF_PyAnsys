"""Shared helpers for Fluent report surfaces and unit-cell diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    average_of_ratios_cp_bae_approx,
    canonical_rescale_factor,
    cp_l1_gu2017,
    cp_perm_expression,
    midplane_window_bulk_aggregate,
    window_area_weighted_average,
    window_pointwise_max,
)
from ro.domain_layout import DomainLayout, EvaluationWindow


def list_named_object_names(named_object, object_label=""):
    """Return names from a PyFluent named object."""
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass

    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(state)
        if isinstance(state, list):
            return list(state)
    except Exception:
        pass

    try:
        names = named_object.list_1()
        if names is None:
            print(f"Warning: {object_label}.list_1() returned None.")
            return []
        return list(names)
    except Exception as exc:
        print(f"Could not list names for {object_label}. Error: {exc}")
        return []


def create_x_normal_plane(solver_obj, surface_name, x_value_m):
    """Create an x-normal iso-surface, replacing an existing surface of the same name."""
    settings_error = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list_named_object_names(
            iso_group,
            "results.surfaces.iso_surface",
        )
        if surface_name in existing:
            try:
                iso_group.delete(surface_name)
                print(f"Deleted existing iso-surface: {surface_name}")
            except Exception as exc:
                print(f"Could not delete iso-surface {surface_name}: {exc}")

        iso_group.create(surface_name)
        iso_group[surface_name].field = "x-coordinate"
        iso_group[surface_name].iso_values = [x_value_m]
        print(
            f"Created iso-surface '{surface_name}' at x = "
            f"{x_value_m:.6e} m (settings API)"
        )
        return
    except Exception as exc:
        settings_error = exc
        print(f"Settings API failed for iso-surface '{surface_name}': {exc}")

    try:
        solver_obj.tui.surface.iso_surface(
            "x-coordinate",
            surface_name,
            "()",
            "()",
            str(x_value_m),
            "0",
        )
        print(
            f"Created iso-surface '{surface_name}' at x = "
            f"{x_value_m:.6e} m (TUI fallback)"
        )
    except Exception as tui_error:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {settings_error}. TUI error: {tui_error}"
        ) from tui_error


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}.")


def unit_cell_boundary_positions(domain_x_min_m, domain_length_m, n_unit_cells):
    """Return all unit-cell boundary x positions, including both domain ends."""
    _require_positive_int("n_unit_cells", n_unit_cells)
    if isinstance(domain_length_m, bool) or not isinstance(
        domain_length_m,
        (int, float),
    ):
        raise TypeError(
            f"domain_length_m must be numeric, got {domain_length_m!r}."
        )
    if domain_length_m <= 0.0:
        raise ValueError(
            f"domain_length_m must be positive, got {domain_length_m!r}."
        )

    cell_length_m = float(domain_length_m) / n_unit_cells
    return [
        float(domain_x_min_m) + boundary_index * cell_length_m
        for boundary_index in range(n_unit_cells + 1)
    ]


def spacer_cell_numbers(n_unit_cells, n_buffer_cells_each_end):
    """Return one-based unit-cell numbers between the inlet and outlet buffers."""
    _require_positive_int("n_unit_cells", n_unit_cells)
    if isinstance(n_buffer_cells_each_end, bool) or not isinstance(
        n_buffer_cells_each_end,
        int,
    ):
        raise TypeError(
            "n_buffer_cells_each_end must be an integer, "
            f"got {n_buffer_cells_each_end!r}."
        )
    if n_buffer_cells_each_end < 0:
        raise ValueError(
            "n_buffer_cells_each_end must be non-negative, "
            f"got {n_buffer_cells_each_end!r}."
        )
    if 2 * n_buffer_cells_each_end >= n_unit_cells:
        raise ValueError(
            "Buffer cells must leave at least one spacer cell: "
            f"n_unit_cells={n_unit_cells}, "
            f"n_buffer_cells_each_end={n_buffer_cells_each_end}."
        )

    return list(
        range(
            n_buffer_cells_each_end + 1,
            n_unit_cells - n_buffer_cells_each_end + 1,
        )
    )


def validate_unit_cell_layout(
    domain_length_m,
    buffer_length_m,
    n_unit_cells,
    n_buffer_cells_each_end,
):
    """Validate that legacy buffer length and unit-cell counts describe one layout."""
    spacer_cells = spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    )
    expected_buffer_length_m = (
        float(domain_length_m)
        / n_unit_cells
        * n_buffer_cells_each_end
    )
    if not math.isclose(
        float(buffer_length_m),
        expected_buffer_length_m,
        rel_tol=1.0e-9,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            "buffer_length_m is inconsistent with the unit-cell layout: "
            f"buffer_length_m={buffer_length_m!r}, "
            f"expected={expected_buffer_length_m!r}, "
            f"n_unit_cells={n_unit_cells}, "
            f"n_buffer_cells_each_end={n_buffer_cells_each_end}."
        )
    return spacer_cells


@dataclass(frozen=True)
class ScoringLayoutGeometry:
    """Geometry derived from an asymmetric DomainLayout for report scoring."""

    layout: DomainLayout
    domain_x_min_m: float
    domain_length_m: float
    spacer_x_in_m: float
    spacer_x_out_m: float
    spacer_length_m: float
    unit_cell_boundary_x_m: list[float]
    spacer_cells: list[int]


_LAYOUT_REL_TOL = 1.0e-9
_LAYOUT_ABS_TOL = 1.0e-12


def scoring_geometry_from_layout(
    layout: DomainLayout,
    domain_x_min_m: float,
) -> ScoringLayoutGeometry:
    """Derive plane positions and active-cell indices from DomainLayout."""
    x0 = float(domain_x_min_m)
    spacer_x_in_m, spacer_x_out_m = layout.active_span(x0)
    return ScoringLayoutGeometry(
        layout=layout,
        domain_x_min_m=x0,
        domain_length_m=float(layout.total_length_m),
        spacer_x_in_m=float(spacer_x_in_m),
        spacer_x_out_m=float(spacer_x_out_m),
        spacer_length_m=float(layout.active_length_m),
        unit_cell_boundary_x_m=layout.boundary_positions(x0),
        spacer_cells=layout.active_cell_numbers(),
    )


def _config_has_layout_value(cfg: Any, name: str) -> bool:
    """True when cfg defines ``name`` with a non-None value."""
    if not hasattr(cfg, name):
        return False
    return getattr(cfg, name) is not None


def _require_config_attr(cfg: Any, name: str) -> Any:
    if not hasattr(cfg, name):
        raise AttributeError(f"Missing required layout config key: {name!r}.")
    value = getattr(cfg, name)
    if value is None:
        raise AttributeError(f"Missing required layout config key: {name!r}.")
    return value


def resolve_evaluation_window_from_config(cfg: Any) -> EvaluationWindow:
    """Build the scoring window from post-config keys.

    ``n_lead_excluded`` and ``n_trail_excluded`` are required (no silent
    lead=1 default). If ``n_inlet_spacer_cells_excluded`` is also set it
    must equal ``n_lead_excluded`` — neither name is preferred silently.
    """
    window = EvaluationWindow(
        int(_require_config_attr(cfg, "n_lead_excluded")),
        int(_require_config_attr(cfg, "n_trail_excluded")),
    )
    if _config_has_layout_value(cfg, "n_inlet_spacer_cells_excluded"):
        n_inlet = cfg.n_inlet_spacer_cells_excluded
        if isinstance(n_inlet, bool) or not isinstance(n_inlet, int):
            raise TypeError(
                "n_inlet_spacer_cells_excluded must be an integer."
            )
        if n_inlet != window.n_lead_excluded:
            raise ValueError(
                "n_inlet_spacer_cells_excluded contradicts n_lead_excluded: "
                f"n_inlet_spacer_cells_excluded={n_inlet!r}, "
                f"n_lead_excluded={window.n_lead_excluded!r}."
            )
    return window


def resolve_scoring_layout_from_config(cfg: Any) -> ScoringLayoutGeometry:
    """Build scoring geometry from asymmetric layout keys on a post config.

    Asymmetric keys ``n_buffer_in``, ``n_active``, ``n_buffer_out``,
    ``cell_length_x_m``, ``buffer_length_in_m``, and ``buffer_length_out_m``
    are required (no silent 5/1 defaults).

    Rule when legacy geometry keys are also present: they must agree with the
    asymmetric layout. Disagreement raises — neither side is preferred silently.
    ``buffer_length_m`` means the inlet-side buffer length
    (``buffer_length_in_m``). ``n_buffer_cells_each_end`` is only
    valid when ``n_buffer_in == n_buffer_out``; a non-None value on an
    asymmetric layout is a contradiction. A ``None`` value is treated as
    absent (used to clear the key via overrides).
    """
    layout = DomainLayout(
        int(_require_config_attr(cfg, "n_buffer_in")),
        int(_require_config_attr(cfg, "n_active")),
        int(_require_config_attr(cfg, "n_buffer_out")),
        float(_require_config_attr(cfg, "cell_length_x_m")),
        float(_require_config_attr(cfg, "buffer_length_in_m")),
        float(_require_config_attr(cfg, "buffer_length_out_m")),
    )
    domain_x_min_m = float(_require_config_attr(cfg, "domain_x_min_m"))
    geometry = scoring_geometry_from_layout(layout, domain_x_min_m)

    if _config_has_layout_value(cfg, "domain_length_m"):
        domain_length_m = float(cfg.domain_length_m)
        if not math.isclose(
            domain_length_m,
            geometry.domain_length_m,
            rel_tol=_LAYOUT_REL_TOL,
            abs_tol=_LAYOUT_ABS_TOL,
        ):
            raise ValueError(
                "domain_length_m contradicts asymmetric layout: "
                f"domain_length_m={domain_length_m!r}, "
                f"layout.total_length_m={geometry.domain_length_m!r} "
                f"(n_buffer_in={layout.n_buffer_in}, n_active={layout.n_active}, "
                f"n_buffer_out={layout.n_buffer_out}, "
                f"cell_length_x_m={layout.cell_length_x_m}, "
                f"buffer_length_in_m={layout.buffer_length_in_m}, "
                f"buffer_length_out_m={layout.buffer_length_out_m})."
            )

    if _config_has_layout_value(cfg, "n_unit_cells"):
        n_unit_cells = int(cfg.n_unit_cells)
        if n_unit_cells != layout.n_total:
            raise ValueError(
                "n_unit_cells contradicts asymmetric layout: "
                f"n_unit_cells={n_unit_cells!r}, "
                f"layout.n_total={layout.n_total!r}."
            )

    if _config_has_layout_value(cfg, "buffer_length_m"):
        buffer_length_m = float(cfg.buffer_length_m)
        expected_inlet_buffer_m = float(layout.buffer_length_in_m)
        if not math.isclose(
            buffer_length_m,
            expected_inlet_buffer_m,
            rel_tol=_LAYOUT_REL_TOL,
            abs_tol=_LAYOUT_ABS_TOL,
        ):
            raise ValueError(
                "buffer_length_m contradicts asymmetric layout inlet buffer: "
                f"buffer_length_m={buffer_length_m!r}, "
                f"expected={expected_inlet_buffer_m!r} "
                f"(buffer_length_in_m)."
            )

    if _config_has_layout_value(cfg, "n_buffer_cells_each_end"):
        n_each = int(cfg.n_buffer_cells_each_end)
        if layout.n_buffer_in != layout.n_buffer_out or n_each != layout.n_buffer_in:
            raise ValueError(
                "n_buffer_cells_each_end contradicts asymmetric layout: "
                f"n_buffer_cells_each_end={n_each!r}, "
                f"n_buffer_in={layout.n_buffer_in}, "
                f"n_buffer_out={layout.n_buffer_out}. "
                "Clear n_buffer_cells_each_end (set to None) for asymmetric "
                "layouts; do not invent a fake each-end count."
            )

    if geometry.spacer_length_m <= 0.0:
        raise ValueError(
            f"spacer_length_m must be > 0, got {geometry.spacer_length_m}."
        )
    return geometry


def unit_cell_plane_name(boundary_index):
    return f"pp_plane_unit_cell_boundary_{boundary_index}"


def unit_cell_pressure_report_name(boundary_index):
    return f"pp_p_unit_cell_boundary_{boundary_index}_avg"


def unit_cell_concentration_report_name(boundary_index):
    return f"pp_salt_mass_fraction_unit_cell_boundary_{boundary_index}_avg"


def unit_cell_mixing_cup_report_name(boundary_index):
    return (
        "pp_salt_mass_fraction_unit_cell_boundary_"
        f"{boundary_index}_massavg"
    )


def unit_cell_plane_area_report_name(boundary_index):
    return f"pp_area_unit_cell_boundary_{boundary_index}"


def unit_cell_areaavg_molar_concentration_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{boundary_index}_areaavg_mol_m3"
    )


def unit_cell_mixing_cup_molar_concentration_name(boundary_index):
    return (
        "pp_salt_molar_concentration_unit_cell_boundary_"
        f"{boundary_index}_massavg_mol_m3"
    )


def unit_cell_mixing_cup_report_spec(boundary_index, field_name="nacl"):
    """Return the Fluent mass-weighted plane concentration report spec."""
    return (
        unit_cell_mixing_cup_report_name(boundary_index),
        "surface-massavg",
        field_name,
    )


from ro.udm_layout import (  # noqa: E402
    FIELD_UDM_MEMBRANE_AREA_ACC as _DEFAULT_UDM_AREA_FIELD,
)


def udm_area_sum_report_spec(field_name=None):
    """Return the Fluent report spec for the unweighted membrane-area UDM sum."""
    if field_name is None:
        field_name = _DEFAULT_UDM_AREA_FIELD
    return ("pp_udm_area_sum", "volume-sum", field_name)


def validate_concentration_thresholds(lower_threshold, upper_threshold):
    """Return validated salt mass-fraction diagnostic thresholds."""
    for name, value in (
        ("lower_threshold", lower_threshold),
        ("upper_threshold", upper_threshold),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be numeric, got {value!r}.")
    if not 0.0 <= lower_threshold < upper_threshold <= 1.0:
        raise ValueError(
            "Concentration thresholds must satisfy "
            "0 <= lower < upper <= 1: "
            f"lower={lower_threshold!r}, upper={upper_threshold!r}."
        )
    return float(lower_threshold), float(upper_threshold)


def fluid_zone_reduction_locations(setup, fluid_zone_names):
    """Resolve fluid-zone names to settings objects accepted by reductions."""
    if not fluid_zone_names:
        raise ValueError("At least one fluid zone is required.")
    fluid_group = setup.cell_zone_conditions.fluid
    locations = []
    for zone_name in fluid_zone_names:
        try:
            locations.append(fluid_group[zone_name])
        except Exception as exc:
            raise ValueError(
                f"Could not resolve fluid zone {zone_name!r} "
                "to a settings object."
            ) from exc
    return locations


def wall_zone_reduction_locations(setup, wall_zone_names):
    """Resolve wall-zone names to settings objects accepted by reductions."""
    if not wall_zone_names:
        raise ValueError("At least one wall zone is required.")
    wall_group = setup.boundary_conditions.wall
    locations = []
    for zone_name in wall_zone_names:
        try:
            locations.append(wall_group[zone_name])
        except Exception as exc:
            raise ValueError(
                f"Could not resolve wall zone {zone_name!r} "
                "to a settings object."
            ) from exc
    return locations


def iso_surface_reduction_locations(setup, iso_surface_names):
    """Resolve iso-surface names to settings objects accepted by reductions."""
    if not iso_surface_names:
        raise ValueError("At least one iso-surface name is required.")
    iso_group = setup.results.surfaces.iso_surface
    locations = []
    for surface_name in iso_surface_names:
        try:
            locations.append(iso_group[surface_name])
        except Exception as exc:
            raise ValueError(
                f"Could not resolve iso-surface {surface_name!r} "
                "to a settings object."
            ) from exc
    return locations


def _reduction_max_if(reduction, expression, condition, locations):
    """Facet maximum of ``expression`` where ``condition`` holds."""
    for method_name in ("maximum_if", "max_if"):
        method = getattr(reduction, method_name, None)
        if method is None:
            continue
        try:
            return method(
                expression=expression,
                condition=condition,
                locations=list(locations),
            )
        except TypeError:
            try:
                return method(
                    expression=expression,
                    condition=condition,
                    locations=locations,
                )
            except Exception:
                continue
        except Exception:
            continue
    raise RuntimeError(
        "Fluent reduction API exposes no working maximum_if/max_if for "
        "facet-max CP metrics."
    )


def _reduction_min_if(reduction, expression, condition, locations):
    """Facet minimum of ``expression`` where ``condition`` holds."""
    for method_name in ("minimum_if", "min_if"):
        method = getattr(reduction, method_name, None)
        if method is None:
            continue
        try:
            return method(
                expression=expression,
                condition=condition,
                locations=list(locations),
            )
        except TypeError:
            try:
                return method(
                    expression=expression,
                    condition=condition,
                    locations=locations,
                )
            except Exception:
                continue
        except Exception:
            continue
    raise RuntimeError(
        "Fluent reduction API exposes no working minimum_if/min_if for "
        "facet-min CP guard metrics."
    )


def _membrane_x_segment_condition(x_min_m, x_max_m):
    return (
        f"AND(x >= {float(x_min_m)!r} [m], "
        f"x <= {float(x_max_m)!r} [m])"
    )


def _midplane_x_segment_condition(x_min_m, x_max_m):
    return _membrane_x_segment_condition(x_min_m, x_max_m)


def evaluation_window_midplane_bulk_concentrations(
    reduction,
    midplane_locations,
    unit_cell_boundary_x_m,
    evaluation_cell_numbers,
    salt_expression,
    *,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    salt_is_mass_fraction=True,
):
    """Mid-plane (z = h/2) area-average salt concentration per evaluation cell.

    Returns (c_b_by_cell, midplane_area_by_cell, c_b_window) in mol/m3.
    Whole-domain mid-plane averages include inlet-buffer regions and must not
    be substituted here.
    """
    if not midplane_locations:
        raise ValueError("At least one mid-plane location is required.")
    if not evaluation_cell_numbers:
        raise ValueError("At least one evaluation cell is required.")

    c_b_by_cell: dict[int, float] = {}
    midplane_area_by_cell: dict[int, float] = {}

    for cell_number in evaluation_cell_numbers:
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        condition = _midplane_x_segment_condition(x_min_m, x_max_m)
        area_m2 = reduction.sum_if(
            expression="1",
            condition=condition,
            locations=list(midplane_locations),
            weight="Area",
        )
        if area_m2 is None or area_m2 <= 0.0:
            raise ValueError(
                f"Mid-plane segment for evaluation cell {cell_number} has no "
                f"positive area (x=[{x_min_m!r}, {x_max_m!r} m])."
            )
        salt_sum = reduction.sum_if(
            expression=salt_expression,
            condition=condition,
            locations=list(midplane_locations),
            weight="Area",
        )
        if salt_sum is None:
            raise ValueError(
                f"Mid-plane salt average failed for evaluation cell "
                f"{cell_number}."
            )
        salt_avg = salt_sum / area_m2
        if salt_is_mass_fraction:
            c_b = mass_fraction_to_molar_concentration(
                salt_avg,
                density_kg_per_m3,
                molecular_weight_kg_per_mol,
            )
        else:
            c_b = float(salt_avg)
        if c_b is None or c_b <= 0.0:
            raise ValueError(
                f"Mid-plane bulk concentration is not positive for cell "
                f"{cell_number}: {c_b!r}."
            )
        c_b_by_cell[cell_number] = c_b
        midplane_area_by_cell[cell_number] = area_m2

    c_b_window = midplane_window_bulk_aggregate(
        c_b_by_cell,
        midplane_area_by_cell,
        evaluation_cell_numbers,
    )
    return c_b_by_cell, midplane_area_by_cell, c_b_window


def _compute_membrane_segment_reductions(
    reduction,
    wall_locations,
    condition,
    b_perm,
    c0_mol_per_m3,
    cm_field="udm-7",
    jw_field="udm-6",
    cp_field="udm-9",
):
    """Area sums and facet maxima for one membrane x-segment."""

    def area_sum(expression):
        return reduction.sum_if(
            expression=expression,
            condition=condition,
            locations=list(wall_locations),
            weight="Area",
        )

    cp_perm_expr = cp_perm_expression(b_perm, cm_field, jw_field)
    area_m2 = area_sum("1")
    if area_m2 is None or area_m2 <= 0.0:
        return None
    return {
        "area_m2": area_m2,
        "cm_area_sum": area_sum(cm_field),
        "jw_area_sum": area_sum(jw_field),
        "cp_udm9_area_sum": area_sum(cp_field),
        "cp_perm_area_sum": area_sum(cp_perm_expr),
        "cp_perm_min": _reduction_min_if(
            reduction,
            cp_perm_expr,
            condition,
            wall_locations,
        ),
        "cp_perm_max": _reduction_max_if(
            reduction,
            cp_perm_expr,
            condition,
            wall_locations,
        ),
        "cp_udm9_max": _reduction_max_if(
            reduction,
            cp_field,
            condition,
            wall_locations,
        ),
        "cm_max": _reduction_max_if(
            reduction,
            cm_field,
            condition,
            wall_locations,
        ),
        "cp_perm_expr": cp_perm_expr,
    }


def _segment_metrics_from_reductions(
    segment,
    cell_number,
    c_b_cell_mol_per_m3,
    c0_mol_per_m3,
):
    """Build per-cell CP metrics from pre-aggregated membrane reductions."""
    area_m2 = segment["area_m2"]
    cm_avg = segment["cm_area_sum"] / area_m2
    jw_avg = segment["jw_area_sum"] / area_m2
    cp_udm9_avg = average_of_ratios_cp_bae_approx(
        segment["cp_udm9_area_sum"],
        area_m2,
    )
    cp_perm_avg = segment["cp_perm_area_sum"] / area_m2

    cp_perm_min = segment["cp_perm_min"]
    cp_perm_max = segment["cp_perm_max"]
    if cp_perm_min is None or cp_perm_max is None:
        cp_perm_min = cp_perm_avg
        cp_perm_max = cp_perm_avg

    k_n, delta = canonical_rescale_factor(
        c0_mol_per_m3,
        c_b_cell_mol_per_m3,
        cp_perm_avg,
        cp_perm_min_mol_per_m3=cp_perm_min,
        cp_perm_max_mol_per_m3=cp_perm_max,
    )
    cp_canon = cp_udm9_avg * k_n
    cp_l1 = cp_l1_gu2017(cm_avg, c_b_cell_mol_per_m3)
    cp_l2 = cp_udm9_avg

    cp_canon_max = float(segment["cp_udm9_max"]) * k_n
    cp_l1_max = float(segment["cm_max"]) / c_b_cell_mol_per_m3
    cp_l2_max = float(segment["cp_udm9_max"])

    metrics = {
        f"pp_membrane_area_cell_{cell_number}_m2": area_m2,
        f"pp_cm_mol_m3_cell_{cell_number}": cm_avg,
        f"pp_jw_m_per_s_cell_{cell_number}": jw_avg,
        f"pp_cp_inlet_unit_cell_boundary_{cell_number}": cp_udm9_avg,
        f"pp_cp_perm_mol_m3_cell_{cell_number}": cp_perm_avg,
        f"pp_c_b_midplane_cell_{cell_number}_mol_m3": c_b_cell_mol_per_m3,
        f"pp_cp_canon_cell_{cell_number}": cp_canon,
        f"pp_cp_L1_cell_{cell_number}": cp_l1,
        f"pp_cp_L2_cell_{cell_number}": cp_l2,
        f"pp_cp_canon_rescale_k_cell_{cell_number}": k_n,
        f"pp_cp_canon_rescale_delta_cell_{cell_number}": delta,
        f"pp_cp_canon_max_cell_{cell_number}": cp_canon_max,
        f"pp_cp_L1_max_cell_{cell_number}": cp_l1_max,
        f"pp_cp_L2_max_cell_{cell_number}": cp_l2_max,
    }
    return metrics


def _cp_scope_aggregates(
    cell_numbers,
    membrane_area_by_cell,
    cp_avg_by_cell,
    cp_max_by_cell,
    definition_label,
    scope,
):
    """Return window or all-active aggregate keys for one CP definition."""
    avg_key = f"cp_{definition_label}_{scope}_avg"
    max_key = f"cp_{definition_label}_{scope}_max"
    return {
        avg_key: window_area_weighted_average(
            cp_avg_by_cell,
            membrane_area_by_cell,
            cell_numbers,
        ),
        max_key: window_pointwise_max(
            cp_max_by_cell,
            cell_numbers,
        ),
    }


def mass_fraction_to_molar_concentration(
    mass_fraction,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
):
    """Convert salt mass fraction to molar concentration [mol/m3]."""
    if mass_fraction is None:
        return None
    if density_kg_per_m3 <= 0.0 or molecular_weight_kg_per_mol <= 0.0:
        raise ValueError("Density and molecular weight must be positive.")
    return (
        float(mass_fraction)
        * float(density_kg_per_m3)
        / float(molecular_weight_kg_per_mol)
    )


def molar_concentration_to_mass_fraction(
    molar_concentration,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
):
    """Convert molar concentration [mol/m3] to salt mass fraction."""
    if molar_concentration is None:
        return None
    if density_kg_per_m3 <= 0.0 or molecular_weight_kg_per_mol <= 0.0:
        raise ValueError("Density and molecular weight must be positive.")
    return (
        float(molar_concentration)
        * float(molecular_weight_kg_per_mol)
        / float(density_kg_per_m3)
    )


def concentration_metric_unit(metric_name):
    """Return the expected explicit unit for concentration-like metrics."""
    if "cells_above" in metric_name or "cells_below" in metric_name:
        return "cells"
    if "mass_fraction" in metric_name:
        return "mass_fraction"
    if "mol_m3" in metric_name or metric_name in {
        "cm_avg",
        "cm_max",
        "cm_min",
    }:
        return "mol/m3"
    if "cp_" in metric_name:
        return "-"
    return None


def segmented_membrane_cp_metrics(
    reduction,
    wall_locations,
    unit_cell_boundary_x_m,
    spacer_cells,
    mixing_cup_mass_fraction_by_boundary,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    c_inlet_ref_mol_per_m3,
    salt_permeability_m_per_s,
    *,
    evaluation_cell_numbers=None,
    c_b_by_cell_mol_per_m3=None,
    midplane_area_by_cell_m2=None,
    wall_locations_by_name=None,
):
    """Compute x-segmented membrane CP metrics (all-active and optional window).

    When ``evaluation_cell_numbers`` and ``c_b_by_cell_mol_per_m3`` are
    supplied, also emits canonical/L1/L2 window aggregates and per-cell
    c_b-based metrics. UDM-9 values are average-of-ratios; canonical applies
    a per-cell scalar rescale.
    """
    if not wall_locations:
        raise ValueError("At least one membrane wall location is required.")
    b_perm = float(salt_permeability_m_per_s)
    c0 = float(c_inlet_ref_mol_per_m3)
    if b_perm <= 0.0 or c0 <= 0.0:
        raise ValueError("Salt permeability and inlet concentration must be positive.")

    metrics: dict[str, Any] = {}
    segment_cache: dict[int, dict] = {}

    for cell_number in spacer_cells:
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        condition = _membrane_x_segment_condition(x_min_m, x_max_m)
        segment = _compute_membrane_segment_reductions(
            reduction,
            wall_locations,
            condition,
            b_perm,
            c0,
        )
        if segment is None:
            raise ValueError(
                f"Membrane segment for cell {cell_number} has no positive area."
            )
        segment_cache[cell_number] = segment
        area_m2 = segment["area_m2"]
        cm_avg = segment["cm_area_sum"] / area_m2
        jw_avg = segment["jw_area_sum"] / area_m2
        cp_inlet_avg = average_of_ratios_cp_bae_approx(
            segment["cp_udm9_area_sum"],
            area_m2,
        )
        cp_perm_avg = segment["cp_perm_area_sum"] / area_m2

        bulk_mol_per_m3 = mass_fraction_to_molar_concentration(
            mixing_cup_mass_fraction_by_boundary.get(cell_number),
            density_kg_per_m3,
            molecular_weight_kg_per_mol,
        )
        cp_bulk = (
            None
            if bulk_mol_per_m3 in (None, 0.0)
            else cm_avg / bulk_mol_per_m3
        )

        metrics.update({
            f"pp_membrane_area_cell_{cell_number}_m2": area_m2,
            f"pp_cm_mol_m3_cell_{cell_number}": cm_avg,
            f"pp_jw_m_per_s_cell_{cell_number}": jw_avg,
            f"pp_cp_inlet_unit_cell_boundary_{cell_number}": cp_inlet_avg,
            f"pp_cp_bulk_unit_cell_boundary_{cell_number}": cp_bulk,
            f"pp_cp_perm_mol_m3_cell_{cell_number}": cp_perm_avg,
        })

    if evaluation_cell_numbers is not None:
        if c_b_by_cell_mol_per_m3 is None:
            raise ValueError(
                "c_b_by_cell_mol_per_m3 is required when evaluation_cell_numbers "
                "is set."
            )
        if midplane_area_by_cell_m2 is None:
            raise ValueError(
                "midplane_area_by_cell_m2 is required when evaluation_cell_numbers "
                "is set."
            )
        missing_cells = [
            cell_number
            for cell_number in evaluation_cell_numbers
            if cell_number not in c_b_by_cell_mol_per_m3
        ]
        if missing_cells:
            raise ValueError(
                f"c_b missing for evaluation cells: {missing_cells!r}."
            )

        canon_avg: dict[int, float] = {}
        canon_max: dict[int, float] = {}
        l1_avg: dict[int, float] = {}
        l1_max: dict[int, float] = {}
        l2_avg: dict[int, float] = {}
        l2_max: dict[int, float] = {}
        membrane_area: dict[int, float] = {}
        delta_values: dict[int, float] = {}

        for cell_number in evaluation_cell_numbers:
            if cell_number not in segment_cache:
                x_min_m = unit_cell_boundary_x_m[cell_number - 1]
                x_max_m = unit_cell_boundary_x_m[cell_number]
                condition = _membrane_x_segment_condition(x_min_m, x_max_m)
                segment = _compute_membrane_segment_reductions(
                    reduction,
                    wall_locations,
                    condition,
                    b_perm,
                    c0,
                )
                if segment is None:
                    raise ValueError(
                        f"Membrane segment for evaluation cell {cell_number} "
                        "has no positive area."
                    )
                segment_cache[cell_number] = segment
            segment = segment_cache[cell_number]
            c_b_cell = c_b_by_cell_mol_per_m3[cell_number]
            cell_metrics = _segment_metrics_from_reductions(
                segment,
                cell_number,
                c_b_cell,
                c0,
            )
            metrics.update(cell_metrics)
            membrane_area[cell_number] = segment["area_m2"]
            canon_avg[cell_number] = cell_metrics[
                f"pp_cp_canon_cell_{cell_number}"
            ]
            canon_max[cell_number] = cell_metrics[
                f"pp_cp_canon_max_cell_{cell_number}"
            ]
            l1_avg[cell_number] = cell_metrics[f"pp_cp_L1_cell_{cell_number}"]
            l1_max[cell_number] = cell_metrics[f"pp_cp_L1_max_cell_{cell_number}"]
            l2_avg[cell_number] = cell_metrics[f"pp_cp_L2_cell_{cell_number}"]
            l2_max[cell_number] = cell_metrics[f"pp_cp_L2_max_cell_{cell_number}"]
            delta_values[cell_number] = cell_metrics[
                f"pp_cp_canon_rescale_delta_cell_{cell_number}"
            ]

        metrics["c_b_window_mol_m3"] = midplane_window_bulk_aggregate(
            c_b_by_cell_mol_per_m3,
            midplane_area_by_cell_m2 or {},
            evaluation_cell_numbers,
        )
        metrics["c_inlet_ref_mol_m3"] = c0
        metrics["cp_scalar_rescale_guard_threshold"] = (
            CP_SCALAR_RESCALE_GUARD_THRESHOLD
        )
        metrics["cp_canon_rescale_delta_max"] = max(delta_values.values())

        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                canon_avg,
                canon_max,
                "canon",
                "window",
            )
        )
        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                l1_avg,
                l1_max,
                "L1",
                "window",
            )
        )
        metrics.update(
            _cp_scope_aggregates(
                evaluation_cell_numbers,
                membrane_area,
                l2_avg,
                l2_max,
                "L2",
                "window",
            )
        )

        spacer_with_c_b = [
            cell_number
            for cell_number in spacer_cells
            if cell_number in c_b_by_cell_mol_per_m3
        ]
        if spacer_with_c_b:
            all_canon_avg: dict[int, float] = {}
            all_canon_max: dict[int, float] = {}
            all_l1_avg: dict[int, float] = {}
            all_l1_max: dict[int, float] = {}
            all_l2_avg: dict[int, float] = {}
            all_l2_max: dict[int, float] = {}
            all_membrane_area: dict[int, float] = {}
            for cell_number in spacer_with_c_b:
                if cell_number not in segment_cache:
                    continue
                segment = segment_cache[cell_number]
                c_b_cell = c_b_by_cell_mol_per_m3[cell_number]
                cell_metrics = _segment_metrics_from_reductions(
                    segment,
                    cell_number,
                    c_b_cell,
                    c0,
                )
                all_membrane_area[cell_number] = segment["area_m2"]
                all_canon_avg[cell_number] = cell_metrics[
                    f"pp_cp_canon_cell_{cell_number}"
                ]
                all_canon_max[cell_number] = cell_metrics[
                    f"pp_cp_canon_max_cell_{cell_number}"
                ]
                all_l1_avg[cell_number] = cell_metrics[f"pp_cp_L1_cell_{cell_number}"]
                all_l1_max[cell_number] = cell_metrics[f"pp_cp_L1_max_cell_{cell_number}"]
                all_l2_avg[cell_number] = cell_metrics[f"pp_cp_L2_cell_{cell_number}"]
                all_l2_max[cell_number] = cell_metrics[f"pp_cp_L2_max_cell_{cell_number}"]
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_canon_avg,
                    all_canon_max,
                    "canon",
                    "all_active",
                )
            )
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_l1_avg,
                    all_l1_max,
                    "L1",
                    "all_active",
                )
            )
            metrics.update(
                _cp_scope_aggregates(
                    spacer_with_c_b,
                    all_membrane_area,
                    all_l2_avg,
                    all_l2_max,
                    "L2",
                    "all_active",
                )
            )

        if wall_locations_by_name:
            for wall_name, wall_locs in wall_locations_by_name.items():
                suffix = _wall_metric_suffix(wall_name)
                per_wall_canon_avg: dict[int, float] = {}
                per_wall_canon_max: dict[int, float] = {}
                per_wall_area: dict[int, float] = {}
                for cell_number in evaluation_cell_numbers:
                    x_min_m = unit_cell_boundary_x_m[cell_number - 1]
                    x_max_m = unit_cell_boundary_x_m[cell_number]
                    condition = _membrane_x_segment_condition(x_min_m, x_max_m)
                    segment = _compute_membrane_segment_reductions(
                        reduction,
                        wall_locs,
                        condition,
                        b_perm,
                        c0,
                    )
                    if segment is None:
                        raise ValueError(
                            f"Membrane segment for {wall_name} cell "
                            f"{cell_number} has no positive area."
                        )
                    c_b_cell = c_b_by_cell_mol_per_m3[cell_number]
                    cell_metrics = _segment_metrics_from_reductions(
                        segment,
                        cell_number,
                        c_b_cell,
                        c0,
                    )
                    per_wall_area[cell_number] = segment["area_m2"]
                    per_wall_canon_avg[cell_number] = cell_metrics[
                        f"pp_cp_canon_cell_{cell_number}"
                    ]
                    per_wall_canon_max[cell_number] = cell_metrics[
                        f"pp_cp_canon_max_cell_{cell_number}"
                    ]
                wall_agg = _cp_scope_aggregates(
                    evaluation_cell_numbers,
                    per_wall_area,
                    per_wall_canon_avg,
                    per_wall_canon_max,
                    f"canon_{suffix}",
                    "window",
                )
                metrics.update(wall_agg)

    return metrics


def _wall_metric_suffix(wall_zone_name: str) -> str:
    lowered = wall_zone_name.lower()
    if "bottom" in lowered:
        return "lower"
    if "top" in lowered:
        return "upper"
    return wall_zone_name.replace("wall_", "")


def exception_details(exc):
    """Return stable exception fields for JSON and CSV diagnostics."""
    error_type = type(exc).__name__
    message = str(exc)
    return {
        "type": error_type,
        "message": message,
        "combined": f"{error_type}: {message}",
    }


def summary_rows_to_wide_record(summary_rows):
    """Build one wide record without dropping metrics whose values are missing."""
    record = {}
    for row in summary_rows:
        metric = row["metric"]
        if metric in record:
            raise ValueError(f"Duplicate summary metric: {metric!r}.")
        record[metric] = row.get("value")
    return record


def concentration_range_diagnostics(
    reduction,
    fluid_zone_locations,
    species_name,
    lower_threshold,
    upper_threshold,
):
    """Compute conditional cell counts and extrema for one species mass fraction."""
    if not fluid_zone_locations:
        raise ValueError("At least one fluid zone is required.")
    if any(isinstance(location, str) for location in fluid_zone_locations):
        raise TypeError(
            "Reduction locations must be Fluent settings objects, not names."
        )
    if not isinstance(species_name, str) or not species_name.strip():
        raise ValueError("species_name must be a non-empty string.")

    lower_threshold, upper_threshold = validate_concentration_thresholds(
        lower_threshold,
        upper_threshold,
    )
    expression = f'MassFraction(species="{species_name}")'
    locations = list(fluid_zone_locations)

    return {
        "pp_salt_mass_fraction_cells_above_threshold": reduction.count_if(
            condition=f"{expression} > {upper_threshold!r}",
            locations=locations,
        ),
        "pp_salt_mass_fraction_cells_below_threshold": reduction.count_if(
            condition=f"{expression} < {lower_threshold!r}",
            locations=locations,
        ),
        "pp_salt_mass_fraction_max": reduction.maximum(
            expression=expression,
            locations=locations,
        ),
        "pp_salt_mass_fraction_min": reduction.minimum(
            expression=expression,
            locations=locations,
        ),
    }


def derive_spacer_cell_metrics(
    computed_values,
    n_unit_cells,
    n_buffer_cells_each_end,
):
    """Derive pressure drop and salt-mass-fraction rise for each spacer cell."""
    derived = {}
    for cell_number in spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    ):
        upstream_index = cell_number - 1
        downstream_index = cell_number
        p_upstream = computed_values.get(
            unit_cell_pressure_report_name(upstream_index)
        )
        p_downstream = computed_values.get(
            unit_cell_pressure_report_name(downstream_index)
        )
        c_upstream = computed_values.get(
            unit_cell_concentration_report_name(upstream_index)
        )
        c_downstream = computed_values.get(
            unit_cell_concentration_report_name(downstream_index)
        )

        derived[f"pp_pressure_drop_cell_{cell_number}"] = (
            None
            if p_upstream is None or p_downstream is None
            else p_upstream - p_downstream
        )
        derived[f"pp_salt_mass_fraction_rise_cell_{cell_number}"] = (
            None
            if c_upstream is None or c_downstream is None
            else c_downstream - c_upstream
        )

    return derived


def derive_spacer_cell_metrics_for_layout(
    computed_values: Mapping[str, Any],
    layout: DomainLayout,
) -> dict[str, Optional[float]]:
    """Asymmetric DomainLayout variant of :func:`derive_spacer_cell_metrics`."""
    derived: dict[str, Optional[float]] = {}
    for cell_number in layout.active_cell_numbers():
        upstream_index = cell_number - 1
        downstream_index = cell_number
        p_upstream = computed_values.get(
            unit_cell_pressure_report_name(upstream_index)
        )
        p_downstream = computed_values.get(
            unit_cell_pressure_report_name(downstream_index)
        )
        c_upstream = computed_values.get(
            unit_cell_concentration_report_name(upstream_index)
        )
        c_downstream = computed_values.get(
            unit_cell_concentration_report_name(downstream_index)
        )

        derived[f"pp_pressure_drop_cell_{cell_number}"] = (
            None
            if p_upstream is None or p_downstream is None
            else p_upstream - p_downstream
        )
        derived[f"pp_salt_mass_fraction_rise_cell_{cell_number}"] = (
            None
            if c_upstream is None or c_downstream is None
            else c_downstream - c_upstream
        )

    return derived


def derive_periodic_spacer_pressure_metrics(
    unit_cell_metrics,
    domain_length_m,
    n_unit_cells,
    n_buffer_cells_each_end,
    n_inlet_spacer_cells_excluded,
):
    """Derive periodic-cell pressure gradient and entrance contamination."""
    if (
        isinstance(n_inlet_spacer_cells_excluded, bool)
        or not isinstance(n_inlet_spacer_cells_excluded, int)
    ):
        raise TypeError(
            "n_inlet_spacer_cells_excluded must be an integer."
        )
    if n_inlet_spacer_cells_excluded < 0:
        raise ValueError(
            "n_inlet_spacer_cells_excluded must be non-negative."
        )

    all_spacer_cells = spacer_cell_numbers(
        n_unit_cells,
        n_buffer_cells_each_end,
    )
    periodic_cells = all_spacer_cells[n_inlet_spacer_cells_excluded:]
    if not periodic_cells:
        raise ValueError(
            "At least one spacer cell must remain for the periodic average."
        )

    periodic_pressure_drops = [
        unit_cell_metrics.get(f"pp_pressure_drop_cell_{cell_number}")
        for cell_number in periodic_cells
    ]
    if any(value is None for value in periodic_pressure_drops):
        periodic_per_m = None
    else:
        cell_length_m = float(domain_length_m) / n_unit_cells
        periodic_length_m = len(periodic_cells) * cell_length_m
        periodic_per_m = sum(periodic_pressure_drops) / periodic_length_m

    cell2_drop = unit_cell_metrics.get("pp_pressure_drop_cell_2")
    cell3_drop = unit_cell_metrics.get("pp_pressure_drop_cell_3")
    cell2_over_cell3 = (
        None
        if cell2_drop is None or cell3_drop in (None, 0.0)
        else cell2_drop / cell3_drop
    )

    return {
        "pp_pressure_drop_periodic_per_m": periodic_per_m,
        "pp_pressure_drop_cell2_over_cell3": cell2_over_cell3,
    }


def derive_periodic_spacer_pressure_metrics_for_layout(
    unit_cell_metrics: Mapping[str, Any],
    layout: DomainLayout,
    evaluation_window: EvaluationWindow,
) -> dict[str, Optional[float]]:
    """Asymmetric DomainLayout variant of periodic spacer pressure metrics."""
    periodic_cells = evaluation_window.evaluation_cell_numbers(layout)
    if not periodic_cells:
        raise ValueError(
            "At least one spacer cell must remain for the periodic average."
        )

    periodic_pressure_drops = [
        unit_cell_metrics.get(f"pp_pressure_drop_cell_{cell_number}")
        for cell_number in periodic_cells
    ]
    if any(value is None for value in periodic_pressure_drops):
        periodic_per_m = None
    else:
        cell_length_m = float(layout.cell_length_x_m)
        periodic_length_m = len(periodic_cells) * cell_length_m
        periodic_per_m = sum(periodic_pressure_drops) / periodic_length_m

    # Intentionally fixed to cells 2 and 3 (not layout-relative).
    cell2_drop = unit_cell_metrics.get("pp_pressure_drop_cell_2")
    cell3_drop = unit_cell_metrics.get("pp_pressure_drop_cell_3")
    cell2_over_cell3 = (
        None
        if cell2_drop is None or cell3_drop in (None, 0.0)
        else cell2_drop / cell3_drop
    )

    return {
        "pp_pressure_drop_periodic_per_m": periodic_per_m,
        "pp_pressure_drop_cell2_over_cell3": cell2_over_cell3,
    }
