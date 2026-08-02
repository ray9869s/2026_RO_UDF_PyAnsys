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


def udm_area_sum_report_spec(field_name="udm-11"):
    """Return the Fluent report spec for the unweighted membrane-area UDM sum."""
    return ("pp_udm_area_sum", "volume-sum", field_name)


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
