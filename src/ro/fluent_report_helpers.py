"""Shared helpers for Fluent report surfaces and unit-cell diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ro.cp_metrics import (
    CP_SCALAR_RESCALE_GUARD_THRESHOLD,
    FACET_MIN_REJECT_AREA_FRAC,
    FACET_MIN_TARGET_AREA_FRAC,
    canonical_rescale_factor,
    cp_l1_gu2017,
    facet_min_check_threshold,
    film_theory_cp_perm_mol_m3,
    midplane_window_bulk_aggregate,
    resolve_area_backed_minimum,
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


_SURFACE_AREA = "surface-area"
_SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
_SURFACE_MASS_WEIGHTED_AVG = "surface-massavg"
_SURFACE_FACET_MAX = "surface-facetmax"
_SURFACE_FACET_MIN = "surface-facetmin"

# Required in summary_metrics_wide.csv when report extraction succeeds.
CANONICAL_CP_SUMMARY_COLUMNS = (
    "c_b_window_mol_m3",
    "cp_canon_window_avg",
    "cp_canon_window_max",
    "cp_L1_window_avg",
    "cp_L2_window_avg",
    "cp_canon_rescale_delta_max",
    "cp_scalar_rescale_guard_threshold",
)


def require_canonical_cp_summary_columns(wide_record: Mapping[str, Any]) -> None:
    """Raise if a successful extract is missing campaign canonical CP columns."""
    missing = [
        column
        for column in CANONICAL_CP_SUMMARY_COLUMNS
        if column not in wide_record or wide_record[column] in (None, "")
    ]
    if missing:
        raise RuntimeError(
            "Canonical CP cannot be computed: summary_metrics_wide is missing "
            f"required columns {missing!r}. "
            "cp_inlet_avg (L2) is not a substitute for canonical CP."
        )


def iso_surface_reduction_locations(solver, iso_surface_names):
    """Resolve iso-surface names via ``solver.settings.results.surfaces``.

    Pass the solver session (object with ``.settings.results``), not
    ``solver.settings.setup``. On Fluent 25.1 setup has no ``.results``.
    """
    if not iso_surface_names:
        raise ValueError("At least one iso-surface name is required.")
    try:
        iso_group = solver.settings.results.surfaces.iso_surface
    except AttributeError as exc:
        raise TypeError(
            "iso_surface_reduction_locations expects the solver session "
            "(solver.settings.results.surfaces.iso_surface). "
            "Do not pass solver.settings.setup — setup has no .results "
            "on Fluent 25.1."
        ) from exc
    locations = []
    for surface_name in iso_surface_names:
        try:
            locations.append(iso_group[surface_name])
        except Exception as err:
            raise ValueError(
                f"Could not resolve iso-surface {surface_name!r} "
                "to a settings object."
            ) from err
    return locations


def _find_first_number(obj):
    """Recursively find the first numeric value inside a compute result."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_first_number(value)
            if found is not None:
                return found
    if isinstance(obj, (list, tuple)):
        for value in obj:
            found = _find_first_number(value)
            if found is not None:
                return found
    return None


def create_x_range_iso_clip(solver, clip_name, surface_names, x_min_m, x_max_m):
    """Create an x-coordinate iso-clip of named surfaces (Fluent 25.1 path).

    Live-verified attributes (ansys-fluent-core 0.38.0):
      clip.field = "x-coordinate"
      clip.surfaces = [...]
      clip.range.minimum / clip.range.maximum
    """
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
    iso_group.create(clip_name)
    clip = iso_group[clip_name]
    clip.field = "x-coordinate"
    clip.surfaces = list(surface_names)
    clip.range.minimum = float(x_min_m)
    clip.range.maximum = float(x_max_m)
    return clip_name


def create_field_iso_clip(
    solver,
    clip_name: str,
    surface_names: list[str],
    field: str,
    range_min: float,
    range_max: float,
) -> str:
    """Create a field-value iso-clip of named surfaces (Fluent 25.1 path)."""
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
    iso_group.create(clip_name)
    clip = iso_group[clip_name]
    clip.field = field
    clip.surfaces = list(surface_names)
    clip.range.minimum = float(range_min)
    clip.range.maximum = float(range_max)
    return clip_name


def delete_iso_clip(solver, clip_name):
    """Delete an iso-clip surface if it exists."""
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)


def create_or_update_surface_field_report(
    solution,
    report_name,
    report_type,
    field_name,
    surface_names,
):
    """Create/update a surface report definition (shared post path)."""
    if not surface_names:
        raise ValueError(f"Cannot create {report_name}: no surfaces.")
    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")
    if report_name in names:
        rd = group[report_name]
    else:
        rd = group.create(report_name)
    rd.report_type = report_type
    if field_name is not None:
        rd.field = field_name
    rd.surface_names = list(surface_names)
    rd.per_surface = False
    return report_name


def delete_surface_field_report(solution, report_name):
    """Delete a surface report definition if it exists."""
    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")
    if report_name in names:
        group.delete(report_name)


def compute_surface_report_value(solution, report_name):
    """Compute one surface report and return its first numeric value."""
    result = solution.report_definitions.compute(report_defs=[report_name])
    value = _find_first_number(result)
    if value is None:
        raise RuntimeError(
            f"Could not extract numeric value from report {report_name!r}. "
            f"Raw result: {result!r}"
        )
    return value


def evaluation_window_midplane_bulk_concentrations(
    solver,
    solution,
    midplane_surface_names,
    unit_cell_boundary_x_m,
    evaluation_cell_numbers,
    salt_field,
    *,
    density_kg_per_m3,
    molecular_weight_kg_per_mol,
    salt_is_mass_fraction=True,
):
    """Mid-plane (z = h/2) mixing-cup salt concentration per evaluation cell.

    Uses x-range iso_clip on the mid-plane iso-surface plus surface-area and
    surface-massavg reports (no ``reduction.sum_if``). Mass-weighted average
    is the campaign bulk for conserved species; see metrics_conventions.md.

    Returns (c_b_by_cell, midplane_area_by_cell, c_b_window) in mol/m3.
    Whole-domain mid-plane averages include inlet-buffer regions and must not
    be substituted here.
    """
    if not midplane_surface_names:
        raise ValueError("At least one mid-plane surface name is required.")
    if not evaluation_cell_numbers:
        raise ValueError("At least one evaluation cell is required.")

    c_b_by_cell: dict[int, float] = {}
    midplane_area_by_cell: dict[int, float] = {}

    for cell_number in evaluation_cell_numbers:
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        tag = f"cb_cell_{cell_number}"
        clip_name = f"pp_mid_clip_{tag}"
        report_area = f"pp_mid_area_{tag}"
        report_salt = f"pp_mid_salt_{tag}"
        created_reports: list[str] = []
        create_x_range_iso_clip(
            solver,
            clip_name,
            list(midplane_surface_names),
            x_min_m,
            x_max_m,
        )
        try:
            create_or_update_surface_field_report(
                solution,
                report_area,
                _SURFACE_AREA,
                None,
                [clip_name],
            )
            created_reports.append(report_area)
            area_m2 = float(compute_surface_report_value(solution, report_area))
            if area_m2 <= 0.0:
                raise ValueError(
                    f"Mid-plane segment for evaluation cell {cell_number} has no "
                    f"positive area (x=[{x_min_m!r}, {x_max_m!r} m])."
                )
            create_or_update_surface_field_report(
                solution,
                report_salt,
                _SURFACE_MASS_WEIGHTED_AVG,
                salt_field,
                [clip_name],
            )
            created_reports.append(report_salt)
            salt_avg = float(compute_surface_report_value(solution, report_salt))
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
        finally:
            for report_name in created_reports:
                try:
                    delete_surface_field_report(solution, report_name)
                except Exception:
                    pass
            try:
                delete_iso_clip(solver, clip_name)
            except Exception:
                pass

    c_b_window = midplane_window_bulk_aggregate(
        c_b_by_cell,
        midplane_area_by_cell,
        evaluation_cell_numbers,
    )
    return c_b_by_cell, midplane_area_by_cell, c_b_window


def _compute_membrane_segment_via_iso_clip(
    solver,
    solution,
    wall_surface_names,
    x_min_m,
    x_max_m,
    b_perm,
    *,
    tag,
    cm_field="udm-7",
    jw_field="udm-6",
    cp_field="udm-9",
):
    """Area-weighted membrane metrics on one x-clipped wall surface set.

    iso_clip + surface-area / surface-areaavg / surface-facetmax|min on UDM
    fields only. Film-theory c_p for the rescale guard is derived from cm/Jw
    facet stats — Fluent 25.1 surface-report ``field`` does not accept setup
    named expressions (``'field' has no attribute 'pp_expr_cp_perm'``).

    Facet minima for cm and Jw are area-backed: a surface-facetmin that has
    negligible iso_clip area below facet_min*(1+1e-3) is rejected (zero-area
    clip-cut artifact) and replaced by the lowest threshold whose area
    fraction reaches 1e-4.
    """
    if not wall_surface_names:
        return None

    clip_name = f"pp_mem_clip_{tag}"
    reports = {
        "area": f"pp_mem_area_{tag}",
        "cm": f"pp_mem_cm_{tag}",
        "jw": f"pp_mem_jw_{tag}",
        "cp": f"pp_mem_cp_{tag}",
        "cp_max": f"pp_mem_cp_max_{tag}",
        "cm_max": f"pp_mem_cm_max_{tag}",
        "cm_min": f"pp_mem_cm_min_{tag}",
        "jw_max": f"pp_mem_jw_max_{tag}",
        "jw_min": f"pp_mem_jw_min_{tag}",
    }
    created_reports: list[str] = []
    created_clips: list[str] = [clip_name]
    create_x_range_iso_clip(
        solver,
        clip_name,
        list(wall_surface_names),
        x_min_m,
        x_max_m,
    )
    try:
        create_or_update_surface_field_report(
            solution,
            reports["area"],
            _SURFACE_AREA,
            None,
            [clip_name],
        )
        created_reports.append(reports["area"])
        area_m2 = float(compute_surface_report_value(solution, reports["area"]))
        if area_m2 <= 0.0:
            return None

        def _avg(report_key, field):
            create_or_update_surface_field_report(
                solution,
                reports[report_key],
                _SURFACE_AREA_WEIGHTED_AVG,
                field,
                [clip_name],
            )
            created_reports.append(reports[report_key])
            return float(compute_surface_report_value(solution, reports[report_key]))

        def _facet(report_key, report_type, field):
            create_or_update_surface_field_report(
                solution,
                reports[report_key],
                report_type,
                field,
                [clip_name],
            )
            created_reports.append(reports[report_key])
            return float(compute_surface_report_value(solution, reports[report_key]))

        def _area_frac_field_below(field: str, threshold: float, sub_tag: str) -> float:
            """Area fraction on the x-clip with field in [0, threshold]."""
            thr = float(threshold)
            if thr < 0.0:
                return 0.0
            child = f"pp_mem_ab_{sub_tag}_{tag}"
            create_field_iso_clip(
                solver,
                child,
                [clip_name],
                field,
                0.0,
                thr,
            )
            if child not in created_clips:
                created_clips.append(child)
            area_name = f"pp_mem_ab_area_{sub_tag}_{tag}"
            create_or_update_surface_field_report(
                solution,
                area_name,
                _SURFACE_AREA,
                None,
                [child],
            )
            if area_name not in created_reports:
                created_reports.append(area_name)
            try:
                sub_area = float(compute_surface_report_value(solution, area_name))
            except Exception:
                sub_area = 0.0
            return max(0.0, sub_area) / area_m2

        def _area_backed_min(field: str, facet_min: float, search_upper: float, label: str):
            check_hi = facet_min_check_threshold(facet_min)
            frac_at_check = _area_frac_field_below(field, check_hi, f"{label}_chk")
            used, rejected = resolve_area_backed_minimum(
                facet_min,
                area_frac_at_check=frac_at_check,
                area_frac_below_fn=lambda thr, _f=field, _l=label: (
                    _area_frac_field_below(_f, thr, f"{_l}_bis")
                ),
                search_upper=search_upper,
                reject_frac=FACET_MIN_REJECT_AREA_FRAC,
                target_frac=FACET_MIN_TARGET_AREA_FRAC,
            )
            return used, rejected, frac_at_check

        cm_avg = _avg("cm", cm_field)
        jw_avg = _avg("jw", jw_field)
        cp_udm9_avg = _avg("cp", cp_field)
        cp_udm9_max = _facet("cp_max", _SURFACE_FACET_MAX, cp_field)
        cm_max = _facet("cm_max", _SURFACE_FACET_MAX, cm_field)
        cm_min_raw = _facet("cm_min", _SURFACE_FACET_MIN, cm_field)
        jw_max = _facet("jw_max", _SURFACE_FACET_MAX, jw_field)
        jw_min_raw = _facet("jw_min", _SURFACE_FACET_MIN, jw_field)

        cm_min_used, cm_min_rejected, _cm_frac = _area_backed_min(
            cm_field,
            cm_min_raw,
            max(cm_avg, cm_min_raw),
            "cm",
        )
        jw_min_used, jw_min_rejected, _jw_frac = _area_backed_min(
            jw_field,
            jw_min_raw,
            max(jw_avg, jw_min_raw),
            "jw",
        )
        facet_min_rejected = bool(cm_min_rejected or jw_min_rejected)

        # c_p = B*cm/(Jw+B): increases with cm, decreases with Jw.
        # Unpaired bounds use area-backed minima for cm_min and jw_min.
        cp_perm_avg = film_theory_cp_perm_mol_m3(cm_avg, jw_avg, b_perm)
        cp_perm_min = film_theory_cp_perm_mol_m3(cm_min_used, jw_max, b_perm)
        cp_perm_max = film_theory_cp_perm_mol_m3(cm_max, jw_min_used, b_perm)

        return {
            "area_m2": area_m2,
            "cm_avg": cm_avg,
            "jw_avg": jw_avg,
            "cp_udm9_avg": cp_udm9_avg,
            "cp_perm_avg": cp_perm_avg,
            "cp_perm_min": cp_perm_min,
            "cp_perm_max": cp_perm_max,
            "cp_udm9_max": cp_udm9_max,
            "cm_max": cm_max,
            "cm_min_raw": cm_min_raw,
            "cm_min_used": cm_min_used,
            "jw_min_raw": jw_min_raw,
            "jw_min_used": jw_min_used,
            "facet_min_rejected": facet_min_rejected,
            "cm_min_rejected": cm_min_rejected,
            "jw_min_rejected": jw_min_rejected,
        }
    finally:
        for report_name in reversed(created_reports):
            try:
                delete_surface_field_report(solution, report_name)
            except Exception:
                pass
        for name in reversed(created_clips):
            try:
                delete_iso_clip(solver, name)
            except Exception:
                pass


def _segment_metrics_from_reductions(
    segment,
    cell_number,
    c_b_cell_mol_per_m3,
    c0_mol_per_m3,
):
    """Build per-cell CP metrics from iso_clip membrane segment averages."""
    area_m2 = segment["area_m2"]
    cm_avg = segment["cm_avg"]
    jw_avg = segment["jw_avg"]
    cp_udm9_avg = segment["cp_udm9_avg"]
    cp_perm_avg = segment["cp_perm_avg"]

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
        f"cp_facet_min_rejected_cell_{cell_number}": bool(
            segment.get("facet_min_rejected", False)
        ),
        f"cm_min_raw_cell_{cell_number}": segment.get("cm_min_raw"),
        f"cm_min_used_cell_{cell_number}": segment.get("cm_min_used"),
        f"jw_min_raw_cell_{cell_number}": segment.get("jw_min_raw"),
        f"jw_min_used_cell_{cell_number}": segment.get("jw_min_used"),
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
    solver,
    solution,
    wall_surface_names,
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
    wall_surfaces_by_name=None,
):
    """Compute x-segmented membrane CP metrics (all-active and optional window).

    Uses iso_clip + surface-area / surface-areaavg / facet max|min reports.
    No ``reduction.sum_if``. When ``evaluation_cell_numbers`` and
    ``c_b_by_cell_mol_per_m3`` are supplied, also emits canonical/L1/L2 window
    aggregates. UDM-9 values are average-of-ratios; canonical applies a
    per-cell scalar rescale.
    """
    if not wall_surface_names:
        raise ValueError("At least one membrane wall surface name is required.")
    b_perm = float(salt_permeability_m_per_s)
    c0 = float(c_inlet_ref_mol_per_m3)
    if b_perm <= 0.0 or c0 <= 0.0:
        raise ValueError("Salt permeability and inlet concentration must be positive.")

    metrics: dict[str, Any] = {}
    segment_cache: dict[int, dict] = {}

    def _segment_for(cell_number, surfaces, tag_prefix):
        x_min_m = unit_cell_boundary_x_m[cell_number - 1]
        x_max_m = unit_cell_boundary_x_m[cell_number]
        return _compute_membrane_segment_via_iso_clip(
            solver,
            solution,
            list(surfaces),
            x_min_m,
            x_max_m,
            b_perm,
            tag=f"{tag_prefix}_{cell_number}",
        )

    for cell_number in spacer_cells:
        segment = _segment_for(cell_number, wall_surface_names, "comb")
        if segment is None:
            raise ValueError(
                f"Membrane segment for cell {cell_number} has no positive area."
            )
        segment_cache[cell_number] = segment
        area_m2 = segment["area_m2"]
        cm_avg = segment["cm_avg"]
        jw_avg = segment["jw_avg"]
        cp_inlet_avg = segment["cp_udm9_avg"]
        cp_perm_avg = segment["cp_perm_avg"]

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
                segment = _segment_for(cell_number, wall_surface_names, "comb")
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

        if wall_surfaces_by_name:
            for wall_name, wall_names in wall_surfaces_by_name.items():
                suffix = _wall_metric_suffix(wall_name)
                per_wall_canon_avg: dict[int, float] = {}
                per_wall_canon_max: dict[int, float] = {}
                per_wall_area: dict[int, float] = {}
                for cell_number in evaluation_cell_numbers:
                    segment = _segment_for(
                        cell_number,
                        wall_names,
                        f"w_{suffix}",
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
