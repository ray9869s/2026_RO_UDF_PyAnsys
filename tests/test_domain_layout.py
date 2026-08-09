"""Pure-Python tests for asymmetric DomainLayout / EvaluationWindow helpers."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from _domain_layout import (
    CELL_LENGTH_X_M,
    CURRENT_LAYOUT,
    DomainLayout,
    EvaluationWindow,
    LEGACY_LAYOUT,
    parse_solver_log_domain_extents_m,
    resolve_layout,
    resolve_mesh_case_name,
    validate_layout_against_x_extent,
)
from _fluent_report_helpers import (
    spacer_cell_numbers,
    unit_cell_boundary_positions,
)


CURRENT_BOUNDARIES = [
    0.0,
    0.003465,
    0.00693,
    0.010395,
    0.01386,
    0.017325,
    0.02079,
    0.024255,
    0.02772,
    0.031185,
    0.03465,
]

CURRENT_SPAN_LABELS = [
    "buffer_in_1",
    "active_1",
    "active_2",
    "active_3",
    "active_4",
    "active_5",
    "active_6",
    "active_7",
    "buffer_out_1",
    "buffer_out_2",
]

SOLVER_EXTENTS_SNIPPET = """\
   Domain Extents:
     x-coordinate: min (m) = -8.673617e-18, max (m) = 1.732500e-02
     y-coordinate: min (m) = -1.732500e-03, max (m) = 1.732500e-03
     z-coordinate: min (m) = 0.000000e+00, max (m) = 7.700000e-04
"""

MESHING_EXTENTS_SNIPPET = """\
Domain extents.
x-coordinate: min = -8.673617e-15, max = 1.732500e+01.
y-coordinate: min = -1.732500e+00, max = 1.732500e+00.
z-coordinate: min = 0.000000e+00, max = 7.700000e-01.
"""

FIXTURE_REPLACE_LOG = (
    'Some preamble\n'
    'Reading from HOST:"C:/PyFluent/My_CFD_Project/03_Results/Pillar/'
    'mesh_max085_min005_cpg5_bl4/Pillar_mesh_max085_min005_cpg5_bl4.msh.h5"\n'
    "trailing noise\n"
)

# Real Fluent mesh-replace logs always read the template .cas.h5 first.
FIXTURE_REPLACE_LOG_WITH_TEMPLATE = """\
Reading from HOST:"C:/PyFluent/My_CFD_Project/01_Templates/
      template_RO_setup.cas.h5" in NODE0 mode ...
Reading from HOST:"C:/PyFluent/My_CFD_Project/03_Results/Pillar/
      mesh_max085_min005_cpg5_bl4/Pillar_mesh_max085_min005_cpg5_bl4.msh.h5"
      in NODE0 mode
"""

FIXTURE_REPLACE_LOG_TWO_MSH = """\
Reading from HOST:"C:/PyFluent/My_CFD_Project/03_Results/Pillar/
      mesh_max085_min006_cpg5_bl4/Pillar_mesh_max085_min006_cpg5_bl4.msh.h5"
      in NODE0 mode
Reading from HOST:"C:/PyFluent/My_CFD_Project/03_Results/Pillar/
      mesh_max085_min005_cpg5_bl4/Pillar_mesh_max085_min005_cpg5_bl4.msh.h5"
      in NODE0 mode
"""


def _assert_close_sequence(actual, expected, *, rel_tol=1.0e-12):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert math.isclose(got, want, rel_tol=rel_tol), (got, want)


class TestDomainLayoutCurrent:
    def test_current_boundaries_and_spans(self):
        layout = CURRENT_LAYOUT
        assert layout.n_buffer_in == 1
        assert layout.n_active == 7
        assert layout.n_buffer_out == 2
        assert math.isclose(layout.cell_length_x_m, CELL_LENGTH_X_M, rel_tol=1.0e-12)

        boundaries = layout.boundary_positions(0.0)
        assert len(boundaries) == 11
        _assert_close_sequence(boundaries, CURRENT_BOUNDARIES)

        spans = layout.spans(0.0)
        assert len(spans) == 10
        assert [label for label, _, _ in spans] == CURRENT_SPAN_LABELS
        for index, (label, x_min, x_max) in enumerate(spans):
            assert label == CURRENT_SPAN_LABELS[index]
            assert math.isclose(x_min, CURRENT_BOUNDARIES[index], rel_tol=1.0e-12)
            assert math.isclose(x_max, CURRENT_BOUNDARIES[index + 1], rel_tol=1.0e-12)

        active_min, active_max = layout.active_span(0.0)
        assert math.isclose(active_min, 0.003465, rel_tol=1.0e-12)
        assert math.isclose(active_max, 0.02772, rel_tol=1.0e-12)
        assert layout.active_cell_numbers() == [2, 3, 4, 5, 6, 7, 8]


class TestDomainLayoutLegacy:
    def test_legacy_boundaries(self):
        layout = LEGACY_LAYOUT
        boundaries = layout.boundary_positions(0.0)
        assert len(boundaries) == 6
        expected = [
            0.0,
            0.003465,
            0.00693,
            0.010395,
            0.01386,
            0.017325,
        ]
        _assert_close_sequence(boundaries, expected)
        assert len(layout.spans(0.0)) == 5

    def test_legacy_parity_with_fluent_report_helpers(self):
        layout = LEGACY_LAYOUT
        new_boundaries = layout.boundary_positions(0.0)
        old_boundaries = unit_cell_boundary_positions(0.0, 0.017325, 5)
        assert len(new_boundaries) == len(old_boundaries) == 6
        for got, want in zip(new_boundaries, old_boundaries):
            assert math.isclose(got, want, rel_tol=1.0e-12)

        assert layout.active_cell_numbers() == spacer_cell_numbers(5, 1) == [2, 3, 4]


class TestDomainLayoutX0Shift:
    def test_nonzero_x0_shifts_every_boundary(self):
        x0 = 0.001
        layout = CURRENT_LAYOUT
        shifted = layout.boundary_positions(x0)
        assert len(shifted) == 11
        for got, base in zip(shifted, CURRENT_BOUNDARIES):
            assert math.isclose(got, base + x0, rel_tol=1.0e-12)

        active_min, active_max = layout.active_span(x0)
        assert math.isclose(active_min, 0.003465 + x0, rel_tol=1.0e-12)
        assert math.isclose(active_max, 0.02772 + x0, rel_tol=1.0e-12)


class TestDomainLayoutValidation:
    def test_n_active_zero_raises(self):
        with pytest.raises(ValueError, match="n_active"):
            DomainLayout(1, 0, 1, CELL_LENGTH_X_M)

    def test_negative_counts_raise(self):
        with pytest.raises(ValueError, match="n_buffer_in"):
            DomainLayout(-1, 3, 1, CELL_LENGTH_X_M)
        with pytest.raises(ValueError, match="n_buffer_out"):
            DomainLayout(1, 3, -1, CELL_LENGTH_X_M)

    def test_nonpositive_cell_length_raises(self):
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(1, 3, 1, 0.0)
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(1, 3, 1, -0.003465)

    def test_window_consuming_active_span_raises(self):
        layout = CURRENT_LAYOUT
        window = EvaluationWindow(3, 4)  # 3 + 4 == 7
        with pytest.raises(ValueError, match="n_lead_excluded"):
            window.evaluation_cell_numbers(layout)
        with pytest.raises(ValueError, match="n_lead_excluded"):
            window.evaluation_local_indices(layout)


class TestEvaluationWindow:
    def test_lead_exclusion_on_current_and_shorter_active(self):
        window = EvaluationWindow(1, 0)

        assert window.evaluation_local_indices(CURRENT_LAYOUT) == [
            2,
            3,
            4,
            5,
            6,
            7,
        ]
        assert window.evaluation_cell_numbers(CURRENT_LAYOUT) == [
            3,
            4,
            5,
            6,
            7,
            8,
        ]

        shorter = DomainLayout(1, 5, 2, CELL_LENGTH_X_M)
        assert window.evaluation_local_indices(shorter) == [2, 3, 4, 5]
        assert window.evaluation_cell_numbers(shorter) == [3, 4, 5, 6]


class TestGeometryRegistry:
    def test_resolve_registered_geo_mesh_pair(self):
        record = resolve_layout(
            "D2450_a45_7c_brg110", "mesh_max085_min006_cpg5_bl4"
        )
        assert record.layout == CURRENT_LAYOUT
        assert record.buffer_wall_base_names == (
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
        )
        assert record.membrane_wall_base_names == (
            "wall_top_mem",
            "wall_bottom_mem",
        )

    def test_same_geo_with_3cell_mesh_raises_naming_both(self):
        # 3-cell meshes are deliberately unregistered (LAYOUT_UNKNOWN).
        with pytest.raises(KeyError, match="geo_name='Sin_ST'") as exc_info:
            resolve_layout("Sin_ST", "mesh_max100_min006_cpg3_bl3_3cell_fake")
        message = str(exc_info.value)
        assert "mesh_case_name=" in message
        assert "Sin_ST" in message
        assert "mesh_max100_min006_cpg3_bl3_3cell_fake" in message

    def test_registered_5cell_sin_st(self):
        record = resolve_layout("Sin_ST", "mesh_max100_min006_cpg3_bl3")
        assert record.layout == LEGACY_LAYOUT


class TestResolveMeshCaseName:
    def test_log_resolves_to_msh_parent_directory(
        self, tmp_path: Path
    ):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG, encoding="utf-8"
        )
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "log"

    def test_log_with_template_cas_then_msh_uses_msh_parent(
        self, tmp_path: Path
    ):
        # Real logs always read template .cas.h5 before the .msh.h5 mesh.
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG_WITH_TEMPLATE, encoding="utf-8"
        )
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "log"
        assert mesh_name != "01_Templates"
        assert "template" not in mesh_name.lower()

    def test_log_with_two_msh_reads_uses_last(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG_TWO_MSH, encoding="utf-8"
        )
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "log"

    def test_multiline_host_path_still_resolves_via_log(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        multiline = (
            'Reading from HOST:"C:/PyFluent/My_CFD_Project/03_Results/Pillar/\n'
            "    mesh_max085_min005_cpg5_bl4/"
            'Pillar_mesh_max085_min005_cpg5_bl4.msh.h5"\n'
        )
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            multiline, encoding="utf-8"
        )
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "log"

    def test_dated_case_dir_resolves_to_full_name(self, tmp_path: Path):
        case_dir = tmp_path / "Diamond_Spacer" / "260615_u0p2_p6M"
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Diamond_Spacer")
        assert mesh_name == "260615_u0p2_p6M"
        assert source == "name"

    def test_bad_unstable_suffix_strips_to_mesh_prefix(self, tmp_path: Path):
        case_dir = (
            tmp_path
            / "Sin_ST"
            / "mesh_max085_min005_cpg5_bl4_u0p2_p6M_BAD_UNSTABLE"
        )
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Sin_ST")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "name"

    def test_attempt_suffix_without_log_returns_none(self, tmp_path: Path):
        case_dir = tmp_path / "Diamond_Spacer" / "u0p1_p4M__attempt_20260705_161718"
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Diamond_Spacer")
        assert mesh_name is None
        assert source is None

    def test_suffix_shape(self, tmp_path: Path):
        case_dir = tmp_path / "Sin_ST" / "u0p1_p4M__mesh_max085_min006_cpg5_bl4"
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Sin_ST")
        assert mesh_name == "mesh_max085_min006_cpg5_bl4"
        assert source == "name"

    def test_prefix_shape(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "mesh_max085_min005_cpg5_bl4_u0p1_p4M"
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "name"

    def test_plain_shape_without_log_returns_none(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name is None
        assert source is None

    def test_log_preferred_over_misleading_dirname(self, tmp_path: Path):
        # Dirname would parse as 'attempt_…'; log is ground truth.
        case_dir = tmp_path / "Pillar" / "u0p1_p4M__attempt_20260705_161718"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p1_p4M__attempt_20260705_161718.txt").write_text(
            FIXTURE_REPLACE_LOG, encoding="utf-8"
        )
        mesh_name, source = resolve_mesh_case_name(case_dir, "Pillar")
        assert mesh_name == "mesh_max085_min005_cpg5_bl4"
        assert source == "log"

class TestSolverLogDomainExtents:
    def test_returns_metres_without_1e3_scale(self):
        x_min, x_max, y_min, y_max, z_min, z_max = parse_solver_log_domain_extents_m(
            SOLVER_EXTENTS_SNIPPET
        )
        assert x_max == 1.7325e-02
        assert math.isclose(x_min, -8.673617e-18, rel_tol=0.0, abs_tol=1e-30)
        assert math.isclose(y_min, -1.7325e-03, rel_tol=1e-12)
        assert math.isclose(y_max, 1.7325e-03, rel_tol=1e-12)
        assert math.isclose(z_min, 0.0, abs_tol=0.0)
        assert math.isclose(z_max, 7.7e-04, rel_tol=1e-12)

    def test_raises_on_meshing_log_block_without_m_marker(self):
        with pytest.raises(ValueError, match=r"\(m\)"):
            parse_solver_log_domain_extents_m(MESHING_EXTENTS_SNIPPET)


class TestValidateLayoutAgainstXExtent:
    def test_diamond_ov020_measured_extent_passes(self):
        result = validate_layout_against_x_extent(
            LEGACY_LAYOUT, 0.0, 0.017324968
        )
        assert result.ok is True
        assert math.isclose(result.expected_length_m, 5 * CELL_LENGTH_X_M, rel_tol=1e-12)

    def test_3cell_extent_fails_against_5cell_layout(self):
        result = validate_layout_against_x_extent(
            LEGACY_LAYOUT, 0.0, 0.010395
        )
        assert result.ok is False
        assert result.measured_length_m == 0.010395
