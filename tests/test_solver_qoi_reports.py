"""Tests for fresh-solve QoI report definitions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import load_run_config, load_solver_code, populate_valid_solver_config


class SettingsObject:
    def get_state(self):
        return dict(vars(self))


class NamedGroup:
    def __init__(self):
        self.objects = {}

    def get_object_names(self):
        return list(self.objects)

    def create(self, name):
        obj = SettingsObject()
        self.objects[name] = obj
        return obj

    def delete(self, name):
        del self.objects[name]

    def __getitem__(self, name):
        return self.objects[name]


def make_solver_and_solution():
    iso_surfaces = NamedGroup()
    solver = SimpleNamespace(
        settings=SimpleNamespace(
            results=SimpleNamespace(
                surfaces=SimpleNamespace(iso_surface=iso_surfaces)
            )
        )
    )
    surface_reports = NamedGroup()
    expression_reports = NamedGroup()
    solution = SimpleNamespace(
        report_definitions=SimpleNamespace(
            surface=surface_reports,
            single_valued_expression=expression_reports,
        )
    )
    return solver, solution, iso_surfaces, surface_reports, expression_reports


def test_solve_time_qoi_reports_cover_requested_fields_and_pressure_drop():
    solver_code = load_solver_code("solver_qoi_reports")
    (
        solver,
        solution,
        iso_surfaces,
        surface_reports,
        expression_reports,
    ) = make_solver_and_solution()

    names = solver_code.update_solve_time_qoi_report_definitions(
        solver=solver,
        solution=solution,
        membrane_wall_zones=["wall_top_mem", "wall_bottom_mem"],
        domain_x_min_m=0.0,
        domain_length_m=0.017325,
        buffer_length_m=0.003465,
    )

    assert names == [
        "pressure_spacer_in_avg",
        "pressure_spacer_out_avg",
        "cm_membrane_avg",
        "cm_membrane_max",
        "cp_membrane_avg",
        "cp_membrane_max",
        "wall_shear_membrane_avg",
        "lmh_udm_avg",
        "pressure_drop_spacer",
    ]
    assert iso_surfaces["plane_spacer_in"].iso_values == [0.003465]
    assert iso_surfaces["plane_spacer_out"].iso_values == pytest.approx(
        [0.01386]
    )

    expected_fields = {
        "pressure_spacer_in_avg": ("surface-areaavg", "pressure"),
        "pressure_spacer_out_avg": ("surface-areaavg", "pressure"),
        "cm_membrane_avg": ("surface-areaavg", "udm-7"),
        "cm_membrane_max": ("surface-facetmax", "udm-7"),
        "cp_membrane_avg": ("surface-areaavg", "udm-9"),
        "cp_membrane_max": ("surface-facetmax", "udm-9"),
        "wall_shear_membrane_avg": ("surface-areaavg", "wall-shear"),
        "lmh_udm_avg": ("surface-areaavg", "udm-8"),
    }
    for report_name, (report_type, field_name) in expected_fields.items():
        report = surface_reports[report_name]
        assert report.report_type == report_type
        assert report.field == field_name
        assert getattr(report, "print") is True
        assert report.create_report_file is False
        assert report.create_report_plot is False

    assert expression_reports["pressure_drop_spacer"].definition == (
        "pressure_spacer_in_avg - pressure_spacer_out_avg"
    )
    assert getattr(expression_reports["pressure_drop_spacer"], "print") is True


def test_solve_time_qoi_reports_require_positive_spacer_length():
    solver_code = load_solver_code("solver_qoi_invalid_geometry")
    solver, solution, *_ = make_solver_and_solution()
    with pytest.raises(ValueError, match="non-positive spacer length"):
        solver_code.update_solve_time_qoi_report_definitions(
            solver,
            solution,
            ["wall_top_mem"],
            0.0,
            0.006,
            0.003,
        )


def test_solve_time_qoi_config_defaults_and_validation():
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    assert cfg.enable_solve_time_qoi_reports is True
    assert cfg.domain_x_min_m == 0.0
    assert cfg.domain_length_m == 0.017325
    assert cfg.buffer_length_m == 0.003465
    cfg.validate_for_solver()


def test_solve_time_qoi_flag_must_be_bool():
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.enable_solve_time_qoi_reports = "yes"
    with pytest.raises(TypeError, match="must be bool"):
        cfg.validate_for_solver()
