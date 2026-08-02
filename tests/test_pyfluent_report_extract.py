"""Pure-Python tests for per-unit-cell report extraction helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from _fluent_report_helpers import (
    concentration_range_diagnostics,
    create_x_normal_plane,
    derive_spacer_cell_metrics,
    unit_cell_boundary_positions,
    unit_cell_concentration_report_name,
    unit_cell_pressure_report_name,
    udm_area_sum_report_spec,
    validate_concentration_thresholds,
    validate_unit_cell_layout,
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
    diagnostics = concentration_range_diagnostics(
        reduction,
        ["fluid", "fluid.1"],
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
            ["fluid", "fluid.1"],
        ),
        (
            "count_if",
            'MassFraction(species="nacl") < 1e-06',
            ["fluid", "fluid.1"],
        ),
        (
            "maximum",
            'MassFraction(species="nacl")',
            ["fluid", "fluid.1"],
        ),
        (
            "minimum",
            'MassFraction(species="nacl")',
            ["fluid", "fluid.1"],
        ),
    ]


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
    assert cfg.salt_mass_fraction_upper_threshold == 0.99
    assert cfg.salt_mass_fraction_lower_threshold == 1.0e-6
