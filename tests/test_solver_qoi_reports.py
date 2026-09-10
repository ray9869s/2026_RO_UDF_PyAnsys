"""Tests for fresh-solve QoI report definitions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)
from ro.domain_layout import (
    BUFFER_LENGTH_IN_M,
    BUFFER_LENGTH_OUT_M,
    CURRENT_LAYOUT,
    DomainLayout,
)


class SettingsObject:
    def get_state(self):
        return dict(vars(self))


class NamedGroup:
    def __init__(self, fail_create=False):
        self.objects = {}
        self.fail_create = fail_create

    def get_object_names(self):
        return list(self.objects)

    def create(self, name):
        if self.fail_create:
            raise RuntimeError("object is not active")
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


def test_transport_reports_keep_magnitude_lmh_and_add_signed_companion():
    solver_code = load_solver_code("solver_signed_lmh")
    solution = SimpleNamespace(
        report_definitions=SimpleNamespace(
            flux=NamedGroup(),
            surface=NamedGroup(),
            single_valued_expression=NamedGroup(),
        )
    )

    solver_code.update_transport_report_definitions_for_current_zones(
        solution=solution,
        inlet_zones=["inlet"],
        outlet_zones=["outlet"],
        membrane_wall_zones=["wall_top_mem", "wall_bottom_mem"],
        density_value=998.2,
        membrane_blocked_area_frac=0.0,
        lmh_name="lmh",
        lmh_signed_name="lmh_signed",
    )

    expressions = solution.report_definitions.single_valued_expression
    assert expressions["lmh"].definition == (
        "abs(m_in + m_out) / (998.2 * area_mem * 1.0) * 3600000.0"
    )
    assert expressions["lmh_signed"].definition == (
        "(m_in + m_out) / (998.2 * area_mem * 1.0) * 3600000.0"
    )
    assert getattr(expressions["lmh_signed"], "print") is True


def test_solve_time_qoi_reports_are_split_across_initialization_boundary():
    solver_code = load_solver_code("solver_qoi_reports")
    (
        solver,
        solution,
        iso_surfaces,
        surface_reports,
        expression_reports,
    ) = make_solver_and_solution()

    wall_names, deferred = (
        solver_code.update_solve_time_wall_qoi_report_definitions(
            solution=solution,
            membrane_wall_zones=["wall_top_mem", "wall_bottom_mem"],
        )
    )

    assert wall_names == [
        "cm_membrane_avg",
        "cm_membrane_max",
        "cp_membrane_avg",
        "cp_membrane_max",
        "wall_shear_membrane_avg",
        "lmh_udm_avg",
    ]
    assert deferred == []
    assert iso_surfaces.get_object_names() == []

    pressure_names = (
        solver_code.update_solve_time_pressure_qoi_report_definitions(
            solver=solver,
            solution=solution,
            layout=CURRENT_LAYOUT,
            domain_x_min_m=0.0,
        )
    )

    assert pressure_names == [
        "pressure_spacer_in_avg",
        "pressure_spacer_out_avg",
        "pressure_drop_spacer",
    ]
    spacer_x_in_m, spacer_x_out_m = CURRENT_LAYOUT.active_span(0.0)
    assert iso_surfaces["plane_spacer_in"].iso_values == pytest.approx(
        [spacer_x_in_m]
    )
    assert iso_surfaces["plane_spacer_out"].iso_values == pytest.approx(
        [spacer_x_out_m]
    )
    assert iso_surfaces["plane_spacer_in"].iso_values == pytest.approx(
        [0.003465]
    )
    assert iso_surfaces["plane_spacer_out"].iso_values == pytest.approx(
        [0.02772]
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
        # Fluent 25.1 surface defs have no create_report_file attribute; the
        # helper still attempts the setattr and the mock records it as False.
        assert report.create_report_file is False
        assert report.create_report_plot is False

    assert expression_reports["pressure_drop_spacer"].definition == (
        "pressure_spacer_in_avg - pressure_spacer_out_avg"
    )
    assert getattr(expression_reports["pressure_drop_spacer"], "print") is True


def test_lmh_udm_avg_report_file_uses_monitor_report_files():
    solver_code = load_solver_code("solver_qoi_report_file")
    report_files = NamedGroup()
    solution = SimpleNamespace(
        monitor=SimpleNamespace(report_files=report_files)
    )

    object_name = solver_code.ensure_lmh_udm_avg_report_file(
        solution,
        report_name="lmh_udm_avg",
        file_name="lmh_udm_avg.out",
    )
    assert object_name == "lmh_udm_avg_rfile"
    report_file = report_files["lmh_udm_avg_rfile"]
    assert report_file.report_defs == ["lmh_udm_avg"]
    assert report_file.file_name == "lmh_udm_avg.out"
    assert report_file.active is True


def test_pressure_drop_spacer_report_file_uses_monitor_report_files():
    solver_code = load_solver_code("solver_qoi_dp_report_file")
    report_files = NamedGroup()
    solution = SimpleNamespace(
        monitor=SimpleNamespace(report_files=report_files)
    )

    object_name = solver_code.ensure_lmh_udm_avg_report_file(
        solution,
        report_name="pressure_drop_spacer",
        file_name="pressure_drop_spacer.out",
    )
    assert object_name == "pressure_drop_spacer_rfile"
    report_file = report_files["pressure_drop_spacer_rfile"]
    assert report_file.report_defs == ["pressure_drop_spacer"]
    assert report_file.file_name == "pressure_drop_spacer.out"
    assert report_file.active is True


def test_qoi_convergence_condition_uses_all_conditions_are_met():
    solver_code = load_solver_code("solver_qoi_convergence")

    class ConvergenceReports(NamedGroup):
        pass

    convergence_reports = ConvergenceReports()
    convergence_conditions = SettingsObject()
    convergence_conditions.convergence_reports = convergence_reports
    solution = SimpleNamespace(
        monitor=SimpleNamespace(convergence_conditions=convergence_conditions)
    )

    object_name = solver_code.configure_qoi_convergence_condition(
        solution,
        report_name="lmh_udm_avg",
        stop_criterion=1e-3,
        previous_values_to_consider=100,
        initial_values_to_ignore=200,
        active=False,
    )
    assert object_name == "lmh_udm_avg_conv"
    assert convergence_conditions.condition == "all-conditions-are-met"
    report = convergence_reports["lmh_udm_avg_conv"]
    assert report.report_defs == "lmh_udm_avg"
    assert report.stop_criterion == pytest.approx(1e-3)
    assert report.previous_values_to_consider == 100
    assert report.initial_values_to_ignore == 200
    assert report.active is False


def test_qoi_convergence_stop_requires_lmh_and_spacer_dp():
    solver_code = load_solver_code("solver_qoi_lmh_and_dp")
    text = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    assert solver_code.QOI_CONVERGENCE_CONDITION == "all-conditions-are-met"
    assert solver_code.QOI_STOP_PRESSURE_REPORT_NAME == "pressure_drop_spacer"
    assert "qoi_stop_report_names = [" in text
    assert "qoi_stop_report_file_paths.append(lmh_report_file_path)" in text
    assert "qoi_stop_report_file_paths.append(dp_report_file_path)" in text
    assert "enable=not enable_qoi_convergence_stop" not in text


def test_phase_a_failures_are_retried_after_initialization():
    solver_code = load_solver_code("solver_qoi_deferred")
    surface_reports = NamedGroup(fail_create=True)
    solution = SimpleNamespace(
        report_definitions=SimpleNamespace(surface=surface_reports)
    )

    created, deferred = (
        solver_code.update_solve_time_wall_qoi_report_definitions(
            solution,
            ["wall_top_mem"],
        )
    )
    assert created == []
    assert [spec[0] for spec in deferred] == [
        "cm_membrane_avg",
        "cm_membrane_max",
        "cp_membrane_avg",
        "cp_membrane_max",
        "wall_shear_membrane_avg",
        "lmh_udm_avg",
    ]

    surface_reports.fail_create = False
    retried = (
        solver_code.retry_deferred_solve_time_qoi_report_definitions(
            solution,
            deferred,
        )
    )
    assert retried == [spec[0] for spec in deferred]


def test_plane_failure_warns_and_does_not_abort(capsys):
    solver_code = load_solver_code("solver_qoi_inactive_planes")
    solution = SimpleNamespace(
        report_definitions=SimpleNamespace(
            surface=NamedGroup(),
            single_valued_expression=NamedGroup(),
        )
    )
    inactive_solver = SimpleNamespace(
        settings=SimpleNamespace(results=SimpleNamespace())
    )

    created = (
        solver_code.update_solve_time_pressure_qoi_report_definitions(
            solver=inactive_solver,
            solution=solution,
            layout=CURRENT_LAYOUT,
            domain_x_min_m=0.0,
        )
    )

    assert created == []
    warning = capsys.readouterr().out
    assert "continuing solver run" in warning
    assert "pressure_spacer_in_avg" in warning
    assert "pressure_spacer_out_avg" in warning
    assert "pressure_drop_spacer" in warning


def test_runtime_orders_phase_b_after_initialization_before_residuals():
    source = (
        SCRIPTS_DIR / "solver_code_260616.py"
    ).read_text(encoding="utf-8")
    workflow = source[
        source.index("deferred_solve_time_qoi_report_specs = []"):
    ]

    phase_a = workflow.index(
        "update_solve_time_wall_qoi_report_definitions("
    )
    initialize = workflow.index(
        "solution.initialization.hybrid_initialize()"
    )
    phase_b = workflow.index(
        "update_solve_time_pressure_qoi_report_definitions("
    )
    residuals = workflow.index("##### [15] Residual Settings #####")
    iterate = workflow.index("solution.run_calculation.iterate(")

    assert phase_a < initialize < phase_b < residuals < iterate


class _DegenerateLayout:
    def active_span(self, x0):
        return (0.0, 0.0)


def test_solve_time_qoi_reports_require_positive_spacer_length():
    solver_code = load_solver_code("solver_qoi_invalid_geometry")
    solver, solution, *_ = make_solver_and_solution()
    with pytest.raises(ValueError, match="non-positive spacer length"):
        solver_code.update_solve_time_pressure_qoi_report_definitions(
            solver=solver,
            solution=solution,
            layout=_DegenerateLayout(),
            domain_x_min_m=0.0,
        )


def test_solve_time_qoi_planes_follow_active_span_for_asymmetric_pitch():
    solver_code = load_solver_code("solver_qoi_d2450_a30_planes")
    solver, solution, iso_surfaces, *_ = make_solver_and_solution()
    layout = DomainLayout(
        n_buffer_in=1,
        n_active=9,
        n_buffer_out=2,
        cell_length_x_m=0.002829,
        buffer_length_in_m=BUFFER_LENGTH_IN_M,
        buffer_length_out_m=BUFFER_LENGTH_OUT_M,
    )
    solver_code.update_solve_time_pressure_qoi_report_definitions(
        solver=solver,
        solution=solution,
        layout=layout,
        domain_x_min_m=0.0,
    )
    spacer_x_in_m, spacer_x_out_m = layout.active_span(0.0)
    assert iso_surfaces["plane_spacer_in"].iso_values == pytest.approx(
        [spacer_x_in_m]
    )
    assert iso_surfaces["plane_spacer_out"].iso_values == pytest.approx(
        [spacer_x_out_m]
    )
    assert iso_surfaces["plane_spacer_in"].iso_values == pytest.approx(
        [0.003465]
    )
    assert iso_surfaces["plane_spacer_out"].iso_values == pytest.approx(
        [0.028926]
    )


def test_solver_qoi_planes_come_from_mesh_manifest_layout():
    source = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    assert "layout_from_mesh_manifest(mesh_case_path)" in source
    assert "layout.active_span(domain_x_min_m)" in source
    assert "domain_length_m - 2.0 * buffer_length_m" not in source


def test_solve_time_qoi_config_defaults_and_validation():
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    assert cfg.enable_solve_time_qoi_reports is True
    assert cfg.lmh_signed_report_name == "lmh_signed"
    assert cfg.domain_x_min_m == 0.0
    assert cfg.domain_length_m == 0.017325
    assert cfg.buffer_length_m == 0.003465
    assert cfg.enable_qoi_convergence_stop is True
    assert cfg.qoi_convergence_report_name == "lmh_udm_avg"
    assert cfg.enable_pressure_drop_spacer_report_file is True
    assert cfg.pressure_drop_spacer_report_file_name == "pressure_drop_spacer.out"
    cfg.validate_for_solver()


def test_solve_time_qoi_flag_must_be_bool():
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.enable_solve_time_qoi_reports = "yes"
    with pytest.raises(TypeError, match="must be bool"):
        cfg.validate_for_solver()
