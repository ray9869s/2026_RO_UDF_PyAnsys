"""Pure-Python tests for per-unit-cell report extraction helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from _fluent_report_helpers import (
    concentration_range_diagnostics,
    create_x_normal_plane,
    derive_periodic_spacer_pressure_metrics,
    derive_spacer_cell_metrics,
    exception_details,
    fluid_zone_reduction_locations,
    mass_fraction_to_molar_concentration,
    segmented_membrane_cp_metrics,
    summary_rows_to_wide_record,
    unit_cell_boundary_positions,
    unit_cell_concentration_report_name,
    unit_cell_mixing_cup_report_name,
    unit_cell_mixing_cup_report_spec,
    unit_cell_plane_area_report_name,
    unit_cell_pressure_report_name,
    udm_area_sum_report_spec,
    validate_concentration_thresholds,
    validate_unit_cell_layout,
    wall_zone_reduction_locations,
)
from helpers import load_post_config


def legacy_create_x_normal_plane(solver_obj, surface_name, x_value_m):
    """Inline implementation retained as an extraction parity harness."""
    settings_error = None
    try:
        iso_group = solver_obj.settings.results.surfaces.iso_surface
        existing = list(iso_group.get_object_names())
        if surface_name in existing:
            iso_group.delete(surface_name)
        iso_group.create(surface_name)
        iso_group[surface_name].field = "x-coordinate"
        iso_group[surface_name].iso_values = [x_value_m]
        return
    except Exception as exc:
        settings_error = exc

    try:
        solver_obj.tui.surface.iso_surface(
            "x-coordinate",
            surface_name,
            "()",
            "()",
            str(x_value_m),
            "0",
        )
    except Exception as tui_error:
        raise RuntimeError(
            f"Could not create iso-surface '{surface_name}'. "
            f"Settings error: {settings_error}. TUI error: {tui_error}"
        ) from tui_error


class FakeIsoSurface:
    def __init__(self, existing=()):
        self.objects = {
            name: SimpleNamespace(field=None, iso_values=None)
            for name in existing
        }
        self.events = []

    def get_object_names(self):
        return list(self.objects)

    def delete(self, name):
        self.events.append(("delete", name))
        del self.objects[name]

    def create(self, name):
        self.events.append(("create", name))
        self.objects[name] = SimpleNamespace(field=None, iso_values=None)

    def __getitem__(self, name):
        return self.objects[name]


def make_solver(iso_group):
    return SimpleNamespace(
        settings=SimpleNamespace(
            results=SimpleNamespace(
                surfaces=SimpleNamespace(iso_surface=iso_group)
            )
        )
    )


def test_shared_plane_helper_matches_extracted_inline_behavior():
    legacy_group = FakeIsoSurface(existing=["plane"])
    shared_group = FakeIsoSurface(existing=["plane"])

    legacy_create_x_normal_plane(make_solver(legacy_group), "plane", 0.003465)
    create_x_normal_plane(make_solver(shared_group), "plane", 0.003465)

    assert shared_group.events == legacy_group.events
    assert shared_group["plane"].field == legacy_group["plane"].field
    assert shared_group["plane"].iso_values == legacy_group["plane"].iso_values


def test_default_unit_cell_layout_matches_existing_spacer_edges():
    cfg = load_post_config()
    positions = unit_cell_boundary_positions(
        cfg.domain_x_min_m,
        cfg.domain_length_m,
        cfg.n_unit_cells,
    )

    assert positions == pytest.approx([
        0.0,
        0.003465,
        0.006930,
        0.010395,
        0.013860,
        0.017325,
    ])
    assert validate_unit_cell_layout(
        cfg.domain_length_m,
        cfg.buffer_length_m,
        cfg.n_unit_cells,
        cfg.n_buffer_cells_each_end,
    ) == [2, 3, 4]


def test_inconsistent_legacy_buffer_length_is_rejected():
    with pytest.raises(ValueError, match="inconsistent with the unit-cell layout"):
        validate_unit_cell_layout(0.017325, 0.002, 5, 1)


def test_spacer_cell_deltas_use_adjacent_boundary_reports():
    values = {}
    pressures = [100.0, 90.0, 77.0, 61.0, 42.0, 20.0]
    concentrations = [0.035, 0.036, 0.038, 0.041, 0.045, 0.050]
    for index, value in enumerate(pressures):
        values[unit_cell_pressure_report_name(index)] = value
    for index, value in enumerate(concentrations):
        values[unit_cell_concentration_report_name(index)] = value

    derived = derive_spacer_cell_metrics(values, 5, 1)

    assert derived == pytest.approx({
        "pp_pressure_drop_cell_2": 13.0,
        "pp_salt_mass_fraction_rise_cell_2": 0.002,
        "pp_pressure_drop_cell_3": 16.0,
        "pp_salt_mass_fraction_rise_cell_3": 0.003,
        "pp_pressure_drop_cell_4": 19.0,
        "pp_salt_mass_fraction_rise_cell_4": 0.004,
    })


def test_missing_boundary_value_produces_missing_delta():
    values = {
        unit_cell_pressure_report_name(1): 90.0,
        unit_cell_concentration_report_name(1): 0.036,
    }
    derived = derive_spacer_cell_metrics(values, 3, 1)
    assert derived["pp_pressure_drop_cell_2"] is None
    assert derived["pp_salt_mass_fraction_rise_cell_2"] is None


def test_periodic_pressure_gradient_excludes_first_spacer_cell():
    metrics = {
        "pp_pressure_drop_cell_2": 225.54,
        "pp_pressure_drop_cell_3": 159.92,
        "pp_pressure_drop_cell_4": 158.15,
    }

    derived = derive_periodic_spacer_pressure_metrics(
        metrics,
        domain_length_m=0.017325,
        n_unit_cells=5,
        n_buffer_cells_each_end=1,
        n_inlet_spacer_cells_excluded=1,
    )

    assert derived["pp_pressure_drop_periodic_per_m"] == pytest.approx(
        (159.92 + 158.15) / (2 * 0.003465)
    )
    assert derived["pp_pressure_drop_cell2_over_cell3"] == pytest.approx(
        225.54 / 159.92
    )


def test_periodic_pressure_exclusion_must_leave_one_cell():
    with pytest.raises(ValueError, match="At least one spacer cell"):
        derive_periodic_spacer_pressure_metrics(
            {},
            domain_length_m=0.017325,
            n_unit_cells=5,
            n_buffer_cells_each_end=1,
            n_inlet_spacer_cells_excluded=3,
        )


def test_mixing_cup_and_plane_area_report_names():
    assert unit_cell_mixing_cup_report_spec(3, "nacl") == (
        "pp_salt_mass_fraction_unit_cell_boundary_3_massavg",
        "surface-massavg",
        "nacl",
    )
    assert unit_cell_mixing_cup_report_name(3).endswith("_3_massavg")
    assert unit_cell_plane_area_report_name(3) == (
        "pp_area_unit_cell_boundary_3"
    )


def test_udm_area_uses_unweighted_volume_sum_report():
    assert udm_area_sum_report_spec("udm-11") == (
        "pp_udm_area_sum",
        "volume-sum",
        "udm-11",
    )


class FakeReduction:
    def __init__(self):
        self.calls = []

    def count_if(self, *, condition, locations):
        self.calls.append(("count_if", condition, locations))
        return 7 if ">" in condition else 3

    def maximum(self, *, expression, locations):
        self.calls.append(("maximum", expression, locations))
        return 1.0

    def minimum(self, *, expression, locations):
        self.calls.append(("minimum", expression, locations))
        return 0.0


def test_concentration_range_diagnostics_use_conditional_cell_counts():
    reduction = FakeReduction()
    fluid = SimpleNamespace(obj_name="solid")
    fluid_1 = SimpleNamespace(obj_name="solid.1")
    diagnostics = concentration_range_diagnostics(
        reduction,
        [fluid, fluid_1],
        "nacl",
        1.0e-6,
        0.99,
    )

    assert diagnostics == {
        "pp_salt_mass_fraction_cells_above_threshold": 7,
        "pp_salt_mass_fraction_cells_below_threshold": 3,
        "pp_salt_mass_fraction_max": 1.0,
        "pp_salt_mass_fraction_min": 0.0,
    }
    assert reduction.calls == [
        (
            "count_if",
            'MassFraction(species="nacl") > 0.99',
            [fluid, fluid_1],
        ),
        (
            "count_if",
            'MassFraction(species="nacl") < 1e-06',
            [fluid, fluid_1],
        ),
        (
            "maximum",
            'MassFraction(species="nacl")',
            [fluid, fluid_1],
        ),
        (
            "minimum",
            'MassFraction(species="nacl")',
            [fluid, fluid_1],
        ),
    ]


def test_fluid_zone_names_resolve_to_reduction_settings_objects():
    solid = SimpleNamespace(obj_name="solid")
    solid_1 = SimpleNamespace(obj_name="solid.1")
    setup = SimpleNamespace(
        cell_zone_conditions=SimpleNamespace(
            fluid={"solid": solid, "solid.1": solid_1}
        )
    )

    locations = fluid_zone_reduction_locations(
        setup,
        ["solid", "solid.1"],
    )

    assert locations == [solid, solid_1]
    assert all(not isinstance(location, str) for location in locations)


def test_wall_zone_names_resolve_to_reduction_settings_objects():
    wall_top = SimpleNamespace(obj_name="wall_top_mem")
    wall_bottom = SimpleNamespace(obj_name="wall_bottom_mem")
    setup = SimpleNamespace(
        boundary_conditions=SimpleNamespace(
            wall={
                "wall_top_mem": wall_top,
                "wall_bottom_mem": wall_bottom,
            }
        )
    )
    assert wall_zone_reduction_locations(
        setup,
        ["wall_top_mem", "wall_bottom_mem"],
    ) == [wall_top, wall_bottom]


class FakeSegmentReduction:
    def __init__(self):
        self.calls = []

    def sum_if(self, *, expression, condition, locations, weight):
        self.calls.append((expression, condition, locations, weight))
        if expression == "1":
            return 2.0
        if expression == "udm-7":
            return 1200.0
        if expression == "udm-6":
            return 2.0e-5
        if expression == "udm-9":
            return 2.1
        if expression.startswith("((("):
            return 2.2
        return 1.0


def test_segmented_membrane_cp_uses_facewise_gu_reduction():
    reduction = FakeSegmentReduction()
    wall = SimpleNamespace(obj_name="wall_top_mem")
    metrics = segmented_membrane_cp_metrics(
        reduction=reduction,
        wall_locations=[wall],
        unit_cell_boundary_x_m=[
            0.0,
            0.003465,
            0.00693,
            0.010395,
            0.01386,
            0.017325,
        ],
        spacer_cells=[2],
        mixing_cup_mass_fraction_by_boundary={2: 0.035},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
    )

    bulk_mol = mass_fraction_to_molar_concentration(
        0.035,
        998.2,
        0.05844,
    )
    assert metrics == pytest.approx({
        "pp_membrane_area_cell_2_m2": 2.0,
        "pp_cm_mol_m3_cell_2": 600.0,
        "pp_jw_m_per_s_cell_2": 1.0e-5,
        "pp_cp_inlet_unit_cell_boundary_2": 1.05,
        "pp_cp_bulk_unit_cell_boundary_2": 600.0 / bulk_mol,
        "pp_cp_perm_mol_m3_cell_2": 0.5,
        "pp_cp_gu_unit_cell_boundary_2": 1.1,
    })
    assert all(call[2] == [wall] for call in reduction.calls)
    assert all(call[3] == "Area" for call in reduction.calls)
    assert all(
        call[1] == "AND(x >= 0.003465 [m], x <= 0.00693 [m])"
        for call in reduction.calls
    )
    assert any(call[0].startswith("(((") for call in reduction.calls)


def test_concentration_diagnostics_reject_zone_name_strings():
    with pytest.raises(TypeError, match="settings objects"):
        concentration_range_diagnostics(
            FakeReduction(),
            ["solid"],
            "nacl",
            1.0e-6,
            0.99,
        )


def test_exception_details_preserve_type_and_message():
    details = exception_details(ValueError("Invalid location input: 'solid'"))
    assert details == {
        "type": "ValueError",
        "message": "Invalid location input: 'solid'",
        "combined": "ValueError: Invalid location input: 'solid'",
    }


def test_every_long_metric_is_preserved_as_a_wide_column():
    summary_rows = [
        {"metric": "geo_name", "value": "Sin_ST", "unit": "-"},
        {
            "metric": "pp_salt_mass_fraction_cells_above_threshold",
            "value": None,
            "unit": "cells",
        },
        {
            "metric": "pp_salt_mass_fraction_min",
            "value": None,
            "unit": "-",
        },
        {
            "metric": "concentration_diagnostic_error",
            "value": "ValueError: invalid location",
            "unit": "-",
        },
    ]

    wide_record = summary_rows_to_wide_record(summary_rows)

    assert set(wide_record) == {
        row["metric"] for row in summary_rows
    }
    assert "pp_salt_mass_fraction_min" in wide_record
    assert wide_record["pp_salt_mass_fraction_min"] is None


def test_duplicate_long_metrics_are_rejected_before_wide_export():
    with pytest.raises(ValueError, match="Duplicate summary metric"):
        summary_rows_to_wide_record([
            {"metric": "duplicate", "value": 1},
            {"metric": "duplicate", "value": 2},
        ])


@pytest.mark.parametrize(
    ("lower_threshold", "upper_threshold"),
    [
        (-1.0e-6, 0.99),
        (0.99, 0.99),
        (0.5, 0.1),
        (0.0, 1.01),
    ],
)
def test_invalid_concentration_thresholds_are_rejected(
    lower_threshold,
    upper_threshold,
):
    with pytest.raises(ValueError, match="0 <= lower < upper <= 1"):
        validate_concentration_thresholds(lower_threshold, upper_threshold)


def test_default_concentration_thresholds():
    cfg = load_post_config()
    assert cfg.n_inlet_spacer_cells_excluded == 1
    assert cfg.salt_molecular_weight_kg_per_mol == 0.05844
    assert cfg.salt_permeability_m_per_s == 2.50e-8
    assert cfg.salt_mass_fraction_upper_threshold == 0.99
    assert cfg.salt_mass_fraction_lower_threshold == 1.0e-6
