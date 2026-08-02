"""Shared helpers for Fluent report surfaces and unit-cell diagnostics."""

from __future__ import annotations

import math


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


def unit_cell_mixing_cup_report_spec(boundary_index, field_name="nacl"):
    """Return the Fluent mass-weighted plane concentration report spec."""
    return (
        unit_cell_mixing_cup_report_name(boundary_index),
        "surface-massavg",
        field_name,
    )


def udm_area_sum_report_spec(field_name="udm-11"):
    """Return the Fluent report spec for the unweighted membrane-area UDM sum."""
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
