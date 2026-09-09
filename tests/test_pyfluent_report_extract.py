"""Pure-Python tests for per-unit-cell report extraction helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ro.fluent_report_helpers import (
    concentration_range_diagnostics,
    concentration_metric_unit,
    create_x_normal_plane,
    derive_periodic_spacer_pressure_metrics,
    derive_spacer_cell_metrics,
    evaluation_window_midplane_bulk_concentrations,
    exception_details,
    fluid_zone_reduction_locations,
    iso_surface_reduction_locations,
    mass_fraction_to_molar_concentration,
    molar_concentration_to_mass_fraction,
    segmented_membrane_cp_metrics,
    summary_rows_to_wide_record,
    unit_cell_boundary_positions,
    unit_cell_areaavg_molar_concentration_name,
    unit_cell_concentration_report_name,
    unit_cell_mixing_cup_report_name,
    unit_cell_mixing_cup_report_spec,
    unit_cell_mixing_cup_molar_concentration_name,
    unit_cell_plane_area_report_name,
    unit_cell_pressure_report_name,
    udm_area_sum_report_spec,
    validate_concentration_thresholds,
    validate_unit_cell_layout,
    wall_zone_reduction_locations,
)
from helpers import POST_DIR, load_module, load_post_config


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
    domain_x_min_m = 0.0
    domain_length_m = 0.017325
    buffer_length_m = 0.003465
    n_unit_cells = 5
    n_buffer_cells_each_end = 1
    positions = unit_cell_boundary_positions(
        domain_x_min_m,
        domain_length_m,
        n_unit_cells,
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
        domain_length_m,
        buffer_length_m,
        n_unit_cells,
        n_buffer_cells_each_end,
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
    assert unit_cell_areaavg_molar_concentration_name(3).endswith(
        "_3_areaavg_mol_m3"
    )
    assert unit_cell_mixing_cup_molar_concentration_name(3).endswith(
        "_3_massavg_mol_m3"
    )


def test_concentration_units_are_explicit_and_convert_consistently():
    mass_fraction = 0.035
    molar = mass_fraction_to_molar_concentration(
        mass_fraction,
        998.2,
        0.05844,
    )
    assert molar == pytest.approx(597.8268309)
    assert molar_concentration_to_mass_fraction(
        molar,
        998.2,
        0.05844,
    ) == pytest.approx(mass_fraction)

    expected_units = {
        "cm_avg": "mol/m3",
        "cm_mol_m3_avg": "mol/m3",
        "c_bulk_center_mass_fraction_avg": "mass_fraction",
        "c_bulk_center_mol_m3_avg": "mol/m3",
        "pp_salt_mass_fraction_unit_cell_boundary_2_massavg": (
            "mass_fraction"
        ),
        "pp_salt_mass_fraction_rise_cell_2": "mass_fraction",
    }
    assert {
        name: concentration_metric_unit(name)
        for name in expected_units
    } == expected_units


def test_contour_reader_reads_c_b_window_mol_m3(tmp_path):
    reports = tmp_path / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text(
        "c_b_window_mol_m3\n"
        "612.5\n",
        encoding="utf-8",
    )
    contour = load_module(
        "contour_c_b_window",
        POST_DIR / "pyensight_contour_export.py",
    )

    value, diagnostic = contour._read_pyfluent_c_b_window_mol_m3(
        {"case_path": str(tmp_path)}
    )

    assert value == pytest.approx(612.5)
    assert "c_b_window_mol_m3" in diagnostic


def test_contour_reader_reads_cp_canon_rescale_k_window(tmp_path):
    reports = tmp_path / "post" / "reports"
    reports.mkdir(parents=True)
    payload = {
        "derived_values": {
            "segmented_cp_values": {
                "pp_cp_canon_rescale_k_cell_2": 0.991,
                "pp_membrane_area_cell_2_m2": 0.002,
                "pp_cp_canon_rescale_k_cell_3": 0.993,
                "pp_membrane_area_cell_3_m2": 0.003,
            }
        }
    }
    (reports / "raw_report_values.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    contour = load_module(
        "contour_k_window",
        POST_DIR / "pyensight_contour_export.py",
    )

    value, diagnostic = contour._read_pyfluent_cp_canon_rescale_k_window(
        {"case_path": str(tmp_path)}
    )

    expected = (0.991 * 0.002 + 0.993 * 0.003) / (0.002 + 0.003)
    assert value == pytest.approx(expected)
    assert "k_window" in diagnostic


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


def test_iso_surface_reduction_locations_rejects_setup_only_object():
    setup_only = SimpleNamespace(models=SimpleNamespace())
    with pytest.raises(TypeError, match="Do not pass solver.settings.setup"):
        iso_surface_reduction_locations(setup_only, ["pp_plane_zc"])


def test_iso_surface_reduction_locations_uses_settings_results():
    plane = SimpleNamespace(obj_name="pp_plane_zc")
    iso_group = {"pp_plane_zc": plane}
    solver = SimpleNamespace(
        settings=SimpleNamespace(
            results=SimpleNamespace(
                surfaces=SimpleNamespace(iso_surface=iso_group)
            )
        )
    )
    assert iso_surface_reduction_locations(solver, ["pp_plane_zc"]) == [plane]


class FakeNamedGroup:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def get_object_names(self):
        return list(self.objects)

    def create(self, name):
        obj = SimpleNamespace(
            field=None,
            iso_values=None,
            surfaces=None,
            range=SimpleNamespace(minimum=None, maximum=None),
            report_type=None,
            surface_names=None,
            per_surface=None,
            definition=None,
        )
        self.objects[name] = obj
        return obj

    def delete(self, name):
        self.deleted.append(name)
        self.objects.pop(name, None)

    def __getitem__(self, name):
        return self.objects[name]


class FakeIsoClipSession:
    """Minimal Fluent-shaped session for iso_clip + surface-report CP paths."""

    def __init__(
        self,
        *,
        area=2.0,
        salt_massavg=0.035,
        field_avgs=None,
        field_max=None,
        field_min=None,
        true_field_min=None,
        true_field_max=None,
    ):
        self.iso_clip = FakeNamedGroup()
        self.iso_surface = FakeNamedGroup()
        self.surface_reports = FakeNamedGroup()
        self.area = area
        self.salt_massavg = salt_massavg
        self.field_avgs = field_avgs or {
            "udm-7": 600.0,
            "udm-6": 1.0e-5,
            "udm-9": 1.05,
        }
        self.field_max = field_max or {
            "udm-9": 1.2,
            "udm-7": 650.0,
            "udm-6": 1.2e-5,
        }
        self.field_min = field_min or {
            "udm-7": 550.0,
            "udm-6": 0.8e-5,
        }
        # Area-bearing floor/ceiling per field for the fake CDF.
        # Defaults: floor = facetmin, ceiling = facetmax.
        self.true_field_min = (
            true_field_min
            if true_field_min is not None
            else dict(self.field_min)
        )
        self.true_field_max = (
            true_field_max
            if true_field_max is not None
            else dict(self.field_max)
        )
        self.compute_calls = []

        def compute(*, report_defs):
            name = report_defs[0]
            self.compute_calls.append(name)
            rd = self.surface_reports[name]
            rtype = rd.report_type
            field = rd.field
            if rtype == "surface-area":
                surfaces = list(rd.surface_names or [])
                if surfaces:
                    clip_name = surfaces[0]
                    if clip_name in self.iso_clip.objects:
                        clip = self.iso_clip.objects[clip_name]
                        clip_field = clip.field
                        if (
                            clip_field
                            and clip_field != "x-coordinate"
                            and clip.range.maximum is not None
                        ):
                            thr = float(clip.range.maximum)
                            true_min = float(
                                self.true_field_min.get(clip_field, 0.0)
                            )
                            true_max = float(
                                self.true_field_max.get(
                                    clip_field,
                                    self.field_max.get(clip_field, true_min),
                                )
                            )
                            if thr + 1.0e-15 < true_min:
                                return {name: 0.0}
                            if true_max <= true_min:
                                return {name: self.area}
                            if thr >= true_max:
                                return {name: self.area}
                            frac = (thr - true_min) / (true_max - true_min)
                            return {name: self.area * max(0.0, min(1.0, frac))}
                return {name: self.area}
            if rtype == "surface-massavg":
                return {name: self.salt_massavg}
            if rtype == "surface-areaavg":
                return {name: self.field_avgs[field]}
            if rtype == "surface-facetmax":
                return {name: self.field_max[field]}
            if rtype == "surface-facetmin":
                return {name: self.field_min[field]}
            raise AssertionError(f"unexpected report {name!r} type={rtype!r}")

        self.solver = SimpleNamespace(
            settings=SimpleNamespace(
                results=SimpleNamespace(
                    surfaces=SimpleNamespace(
                        iso_clip=self.iso_clip,
                        iso_surface=self.iso_surface,
                    )
                ),
                solution=SimpleNamespace(
                    report_definitions=SimpleNamespace(
                        surface=self.surface_reports,
                        compute=compute,
                    )
                ),
            )
        )
        self.solution = self.solver.settings.solution


def test_segmented_membrane_cp_uses_iso_clip_surface_reports():
    session = FakeIsoClipSession()
    metrics = segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
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
    from ro.cp_metrics import film_theory_cp_perm_mol_m3

    expected_cp_perm = film_theory_cp_perm_mol_m3(600.0, 1.0e-5, 2.50e-8)
    expected = {
        "pp_membrane_area_cell_2_m2": 2.0,
        "pp_cm_mol_m3_cell_2": 600.0,
        "pp_jw_m_per_s_cell_2": 1.0e-5,
        "pp_cp_inlet_unit_cell_boundary_2": 1.05,
        "pp_cp_bulk_unit_cell_boundary_2": 600.0 / bulk_mol,
        "pp_cp_perm_mol_m3_cell_2": expected_cp_perm,
    }
    for key, value in expected.items():
        assert metrics[key] == pytest.approx(value)
    assert metrics["cp_membrane_segment_fluent_computes"] > 0
    assert any(
        name.startswith("pp_mem_clip_") for name in session.iso_clip.deleted
    )
    # Only UDM fields were used as surface-report fields (no named exprs).
    used_fields = set()
    for name in session.compute_calls:
        # Reports deleted after compute; recover field from call pattern.
        if "_cm_" in name and "max" not in name and "min" not in name:
            used_fields.add("udm-7")
        elif "_jw_" in name and "max" not in name and "min" not in name:
            used_fields.add("udm-6")
        elif "_cp_" in name and "perm" not in name:
            used_fields.add("udm-9")
    assert used_fields == {"udm-6", "udm-7", "udm-9"}
    assert not any("pp_expr" in name for name in session.compute_calls)


def test_segmented_membrane_cp_window_metrics_with_c_b():
    session = FakeIsoClipSession()
    metrics = segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693, 0.010395],
        spacer_cells=[2, 3],
        mixing_cup_mass_fraction_by_boundary={2: 0.035, 3: 0.036},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        evaluation_cell_numbers=[3],
        c_b_by_cell_mol_per_m3={2: 610.0, 3: 615.0},
        midplane_area_by_cell_m2={2: 1.0, 3: 1.0},
        compute_cp_spread=True,
    )
    assert "cp_canon_window_avg" in metrics
    assert "cp_L1_window_avg" in metrics
    assert "cp_L2_window_avg" in metrics
    assert "cp_canon_all_active_avg" in metrics
    assert metrics["c_b_window_mol_m3"] == pytest.approx(615.0)
    assert metrics["cp_facet_min_rejected_cell_3"] is False
    assert metrics["cm_min_raw_cell_3"] == pytest.approx(550.0)
    # Low end: area-backed facetmin (kept when it has area support).
    assert metrics["cm_min_used_cell_3"] == pytest.approx(550.0, rel=1e-2)
    # High end: 99.9% area quantile (near facetmax on the linear fake CDF).
    assert metrics["cm_q_lo_cell_3"] is None
    assert metrics["cm_q_hi_cell_3"] == pytest.approx(650.0, rel=1e-2)
    assert metrics["jw_q_lo_cell_3"] == pytest.approx(0.8e-5, rel=1e-2)
    assert metrics["pp_cp_canon_rescale_delta_cell_3"] < 1.0e-3
    assert metrics["cp_canon_rescale_delta_status"] == "evaluated"
    assert metrics["cp_canon_rescale_delta_max"] is not None


def test_segmented_membrane_cp_spread_off_nulls_delta_keeps_k():
    """Default compute_cp_spread=False: k_N on, delta null, status explicit."""
    from ro.fluent_report_helpers import (
        require_canonical_cp_summary_columns,
        summary_rows_to_wide_record,
    )

    session = FakeIsoClipSession()
    metrics = segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693, 0.010395],
        spacer_cells=[2, 3],
        mixing_cup_mass_fraction_by_boundary={2: 0.035, 3: 0.036},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        evaluation_cell_numbers=[3],
        c_b_by_cell_mol_per_m3={2: 610.0, 3: 615.0},
        midplane_area_by_cell_m2={2: 1.0, 3: 1.0},
        compute_cp_spread=False,
    )
    assert metrics["cp_canon_window_avg"] is not None
    assert metrics["pp_cp_canon_rescale_k_cell_3"] is not None
    assert metrics["pp_cp_canon_rescale_delta_cell_3"] is None
    assert metrics["cp_canon_rescale_delta_max"] is None
    assert metrics["cp_canon_rescale_delta_status"] == "not_evaluated"
    assert metrics["cp_scalar_rescale_guard_threshold"] == pytest.approx(1.0e-3)
    assert metrics["compute_cp_spread"] is False
    assert metrics["cm_q_hi_cell_3"] is None
    assert metrics["jw_q_lo_cell_3"] is None
    # Fewer Fluent computes than the spread-on path (no bisection probes).
    ops_off = metrics["cp_membrane_segment_fluent_computes"]
    assert ops_off > 0

    session_on = FakeIsoClipSession()
    metrics_on = segmented_membrane_cp_metrics(
        solver=session_on.solver,
        solution=session_on.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693, 0.010395],
        spacer_cells=[2, 3],
        mixing_cup_mass_fraction_by_boundary={2: 0.035, 3: 0.036},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        evaluation_cell_numbers=[3],
        c_b_by_cell_mol_per_m3={2: 610.0, 3: 615.0},
        midplane_area_by_cell_m2={2: 1.0, 3: 1.0},
        compute_cp_spread=True,
    )
    assert metrics_on["cp_membrane_segment_fluent_computes"] > ops_off

    summary_rows = [
        {"metric": "geo_name", "value": "D2450_a45", "unit": "-"},
        {"metric": "cp_inlet_avg", "value": 1.05, "unit": "-"},
        {
            "metric": "c_b_window_mol_m3",
            "value": metrics["c_b_window_mol_m3"],
            "unit": "mol/m3",
        },
    ]
    for key, value in metrics.items():
        if key == "c_b_window_mol_m3":
            continue
        summary_rows.append({"metric": key, "value": value, "unit": "-"})
    wide = summary_rows_to_wide_record(summary_rows)
    require_canonical_cp_summary_columns(wide)
    assert wide["cp_canon_rescale_delta_max"] in (None, "")
    assert wide["cp_canon_rescale_delta_status"] == "not_evaluated"


def test_segmented_membrane_cp_subphase_seconds_accumulate():
    session = FakeIsoClipSession()
    subphases: dict[str, float] = {}
    segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693, 0.010395],
        spacer_cells=[2],
        mixing_cup_mass_fraction_by_boundary={2: 0.035},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        subphase_seconds=subphases,
    )
    assert subphases.get("iso_clip_create", 0.0) >= 0.0
    assert subphases.get("surface_report_def_create", 0.0) >= 0.0
    assert subphases.get("surface_report_compute", 0.0) >= 0.0
    assert subphases.get("python_aggregate", 0.0) >= 0.0
    assert sum(subphases.values()) >= 0.0


def test_segmented_membrane_cp_rejects_zero_area_facetmin():
    """Facetmin=0 with no iso_clip area is replaced; cp_max uses quantiles."""
    session = FakeIsoClipSession(
        field_avgs={"udm-7": 620.0, "udm-6": 1.0e-5, "udm-9": 1.087},
        field_max={"udm-7": 700.0, "udm-6": 1.2e-5, "udm-9": 1.2},
        field_min={"udm-7": 0.0, "udm-6": 0.8e-5},
        true_field_min={"udm-7": 618.0, "udm-6": 0.8e-5},
        true_field_max={"udm-7": 700.0, "udm-6": 1.2e-5},
    )
    metrics = segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693],
        spacer_cells=[2],
        mixing_cup_mass_fraction_by_boundary={2: 0.035},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        evaluation_cell_numbers=[2],
        c_b_by_cell_mol_per_m3={2: 626.0},
        midplane_area_by_cell_m2={2: 1.0},
        compute_cp_spread=True,
    )
    assert metrics["cp_facet_min_rejected_cell_2"] is True
    assert metrics["cm_min_raw_cell_2"] == pytest.approx(0.0)
    assert metrics["cm_min_used_cell_2"] == pytest.approx(618.0, rel=1e-2)
    assert metrics["cm_q_hi_cell_2"] == pytest.approx(700.0, rel=1e-2)
    assert metrics["pp_cp_canon_rescale_delta_cell_2"] < 1.0e-3
    assert metrics["pp_cp_L2_cell_2"] == pytest.approx(1.087)


def test_midplane_bulk_uses_surface_massavg_not_areaavg():
    session = FakeIsoClipSession(area=1.5e-5, salt_massavg=0.03512)
    c_b_by_cell, area_by_cell, c_b_window = (
        evaluation_window_midplane_bulk_concentrations(
            solver=session.solver,
            solution=session.solution,
            midplane_surface_names=["pp_plane_zc"],
            unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693],
            evaluation_cell_numbers=[2],
            salt_field="nacl",
            density_kg_per_m3=998.2,
            molecular_weight_kg_per_mol=0.05844,
            salt_is_mass_fraction=True,
        )
    )
    expected = mass_fraction_to_molar_concentration(
        0.03512, 998.2, 0.05844
    )
    assert c_b_by_cell[2] == pytest.approx(expected)
    assert area_by_cell[2] == pytest.approx(1.5e-5)
    assert c_b_window == pytest.approx(expected)
    salt_report_types = []
    for name in session.compute_calls:
        # Reports are deleted after compute; reconstruct type from name.
        if "salt" in name:
            salt_report_types.append("surface-massavg")
        if "area" in name and "salt" not in name:
            salt_report_types.append("surface-area")
    assert "surface-massavg" in salt_report_types
    assert "surface-area" in salt_report_types
    # Ensure no areaavg salt report was computed for mid-plane c_b.
    assert not any(
        "areaavg" in str(getattr(rd, "report_type", ""))
        for rd in session.surface_reports.objects.values()
    )


def test_midplane_bulk_rejects_nonpositive_area():
    session = FakeIsoClipSession(area=0.0)
    with pytest.raises(ValueError, match="no positive area"):
        evaluation_window_midplane_bulk_concentrations(
            solver=session.solver,
            solution=session.solution,
            midplane_surface_names=["pp_plane_zc"],
            unit_cell_boundary_x_m=[0.0, 0.003465],
            evaluation_cell_numbers=[1],
            salt_field="nacl",
            density_kg_per_m3=998.2,
            molecular_weight_kg_per_mol=0.05844,
        )


def test_require_canonical_cp_summary_columns_rejects_l2_only():
    from ro.fluent_report_helpers import require_canonical_cp_summary_columns

    with pytest.raises(RuntimeError, match="Canonical CP cannot be computed"):
        require_canonical_cp_summary_columns(
            {
                "cp_inlet_avg": 1.2,
                "c_bulk_center_mol_m3_avg": 610.0,
            }
        )


def test_successful_wide_summary_must_include_canonical_cp_columns():
    from ro.fluent_report_helpers import (
        CANONICAL_CP_SUMMARY_COLUMNS,
        require_canonical_cp_summary_columns,
        summary_rows_to_wide_record,
    )

    session = FakeIsoClipSession()
    metrics = segmented_membrane_cp_metrics(
        solver=session.solver,
        solution=session.solution,
        wall_surface_names=["wall_top_mem"],
        unit_cell_boundary_x_m=[0.0, 0.003465, 0.00693, 0.010395],
        spacer_cells=[2, 3],
        mixing_cup_mass_fraction_by_boundary={2: 0.035, 3: 0.036},
        density_kg_per_m3=998.2,
        molecular_weight_kg_per_mol=0.05844,
        c_inlet_ref_mol_per_m3=597.8268309,
        salt_permeability_m_per_s=2.50e-8,
        evaluation_cell_numbers=[3],
        c_b_by_cell_mol_per_m3={2: 610.0, 3: 615.0},
        midplane_area_by_cell_m2={2: 1.0, 3: 1.0},
        compute_cp_spread=True,
    )
    summary_rows = [
        {"metric": "geo_name", "value": "D2450_a45", "unit": "-"},
        {"metric": "cp_inlet_avg", "value": 1.05, "unit": "-"},
        {"metric": "c_b_window_mol_m3", "value": metrics["c_b_window_mol_m3"], "unit": "mol/m3"},
    ]
    for key, value in metrics.items():
        if key == "c_b_window_mol_m3":
            continue
        summary_rows.append({"metric": key, "value": value, "unit": "-"})
    wide = summary_rows_to_wide_record(summary_rows)
    require_canonical_cp_summary_columns(wide)
    for column in CANONICAL_CP_SUMMARY_COLUMNS:
        assert column in wide
        if column == "cp_canon_rescale_delta_max":
            assert wide[column] is not None
        else:
            assert wide[column] is not None


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
    assert cfg.n_lead_excluded is None
    assert cfg.n_trail_excluded is None
    assert cfg.n_inlet_spacer_cells_excluded is None
    assert cfg.salt_molecular_weight_kg_per_mol == 0.05844
    assert cfg.salt_permeability_m_per_s == 2.50e-8
    assert cfg.salt_mass_fraction_upper_threshold == 0.99
    assert cfg.salt_mass_fraction_lower_threshold == 1.0e-6
