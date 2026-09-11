"""Pure-Python tests for asymmetric DomainLayout / EvaluationWindow helpers."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from ro.domain_layout import (
    BUFFER_LENGTH_IN_M,
    BUFFER_LENGTH_OUT_M,
    CELL_LENGTH_X_M,
    CURRENT_LAYOUT,
    DomainLayout,
    EvaluationWindow,
    LEGACY_LAYOUT,
    assert_replace_log_matches_mesh_manifest,
    layout_from_mesh_manifest,
    layout_from_run_directory,
    layout_post_config_values,
    mesh_case_name_from_solver_replace_log,
    parse_solver_log_domain_extents_m,
    require_layout_matches_measured_x_extent,
    require_mesh_manifest_x_extent_matches_layout,
    resolve_layout,
    validate_layout_against_x_extent,
)
from ro.fluent_report_helpers import (
    spacer_cell_numbers,
    unit_cell_boundary_positions,
)
from ro.manifest import ManifestError, write_mesh_manifest
from ro.paths import mesh_dir, run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload, run_payload, write_test_run


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
     z-coordinate: min (m) = -3.850000e-04, max (m) = 3.850000e-04
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
        assert math.isclose(
            layout.buffer_length_in_m, BUFFER_LENGTH_IN_M, rel_tol=1.0e-12
        )
        assert math.isclose(
            layout.buffer_length_out_m, BUFFER_LENGTH_OUT_M, rel_tol=1.0e-12
        )

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


class TestDomainLayoutD2450A30:
    """Non-integer buffer/pitch: independent buffer lengths, pitch for active only."""

    PITCH_M = 0.002829
    N_ACTIVE = 9

    def test_buffer_in_nine_pitch_steps_buffer_out(self):
        layout = DomainLayout(
            1,
            self.N_ACTIVE,
            2,
            self.PITCH_M,
            BUFFER_LENGTH_IN_M,
            BUFFER_LENGTH_OUT_M,
        )
        expected_total = (
            BUFFER_LENGTH_IN_M
            + self.N_ACTIVE * self.PITCH_M
            + BUFFER_LENGTH_OUT_M
        )
        assert math.isclose(expected_total, 0.035856, rel_tol=1.0e-12)
        assert math.isclose(layout.total_length_m, expected_total, rel_tol=1.0e-12)
        assert not math.isclose(
            layout.total_length_m,
            layout.n_total * self.PITCH_M,
            rel_tol=1.0e-9,
        )

        boundaries = layout.boundary_positions(0.0)
        assert len(boundaries) == 13
        expected = [0.0, BUFFER_LENGTH_IN_M]
        for k in range(1, self.N_ACTIVE + 1):
            expected.append(BUFFER_LENGTH_IN_M + k * self.PITCH_M)
        expected.append(expected[-1] + BUFFER_LENGTH_OUT_M / 2.0)
        expected.append(expected[-1] + BUFFER_LENGTH_OUT_M / 2.0)
        _assert_close_sequence(boundaries, expected)

        active_min, active_max = layout.active_span(0.0)
        assert math.isclose(active_min, BUFFER_LENGTH_IN_M, rel_tol=1.0e-12)
        assert math.isclose(
            active_max,
            BUFFER_LENGTH_IN_M + self.N_ACTIVE * self.PITCH_M,
            rel_tol=1.0e-12,
        )
        assert math.isclose(
            boundaries[-1] - active_max,
            BUFFER_LENGTH_OUT_M,
            rel_tol=1.0e-12,
        )


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
            DomainLayout(
                1, 0, 1, CELL_LENGTH_X_M, BUFFER_LENGTH_IN_M, BUFFER_LENGTH_IN_M
            )

    def test_negative_counts_raise(self):
        with pytest.raises(ValueError, match="n_buffer_in"):
            DomainLayout(
                -1, 3, 1, CELL_LENGTH_X_M, BUFFER_LENGTH_IN_M, BUFFER_LENGTH_IN_M
            )
        with pytest.raises(ValueError, match="n_buffer_out"):
            DomainLayout(
                1, 3, -1, CELL_LENGTH_X_M, BUFFER_LENGTH_IN_M, BUFFER_LENGTH_IN_M
            )

    def test_nonpositive_cell_length_raises(self):
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(
                1, 3, 1, 0.0, BUFFER_LENGTH_IN_M, BUFFER_LENGTH_IN_M
            )
        with pytest.raises(ValueError, match="cell_length_x_m"):
            DomainLayout(
                1, 3, 1, -0.003465, BUFFER_LENGTH_IN_M, BUFFER_LENGTH_IN_M
            )

    def test_nonpositive_buffer_length_raises(self):
        with pytest.raises(ValueError, match="buffer_length_in_m"):
            DomainLayout(
                1, 3, 1, CELL_LENGTH_X_M, 0.0, BUFFER_LENGTH_IN_M
            )
        with pytest.raises(ValueError, match="buffer_length_out_m"):
            DomainLayout(
                1, 3, 1, CELL_LENGTH_X_M, BUFFER_LENGTH_IN_M, -0.00693
            )

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

        shorter = DomainLayout(
            1,
            5,
            2,
            CELL_LENGTH_X_M,
            BUFFER_LENGTH_IN_M,
            BUFFER_LENGTH_OUT_M,
        )
        assert window.evaluation_local_indices(shorter) == [2, 3, 4, 5]
        assert window.evaluation_cell_numbers(shorter) == [3, 4, 5, 6]


class TestLayoutFromMeshManifest:
    def test_reads_layout_wall_names_and_window(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        payload = mesh_payload()
        payload["n_buffer_out"] = 2
        write_mesh_manifest(directory, payload)

        record = layout_from_mesh_manifest(directory)
        assert record.layout.n_buffer_in == 1
        assert record.layout.n_active == 7
        assert record.layout.n_buffer_out == 2
        assert record.layout.cell_length_x_m == 0.003465
        assert record.layout.buffer_length_in_m == 0.003465
        assert record.layout.buffer_length_out_m == 0.00693
        assert record.membrane_wall_base_names == (
            "wall_top_mem",
            "wall_bottom_mem",
        )
        assert record.buffer_wall_base_names == (
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
        )
        assert record.evaluation_window.n_lead_excluded == 3
        assert record.evaluation_window.n_trail_excluded == 0
        assert record.evaluation_window.evaluation_local_indices(
            record.layout
        ) == [4, 5, 6, 7]

    def test_missing_manifest_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        with pytest.raises(ManifestError):
            layout_from_mesh_manifest(directory)

    def test_post_config_values_from_record(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        payload = mesh_payload()
        payload["n_buffer_out"] = 2
        write_mesh_manifest(directory, payload)

        values = layout_post_config_values(layout_from_mesh_manifest(directory))
        assert values["n_buffer_in"] == 1
        assert values["n_active"] == 7
        assert values["n_buffer_out"] == 2
        assert values["domain_length_m"] == 0.03465
        assert values["buffer_length_in_m"] == 0.003465
        assert values["buffer_length_out_m"] == 0.00693
        assert values["buffer_length_m"] == 0.003465
        assert values["n_unit_cells"] == 10
        assert values["n_buffer_cells_each_end"] is None
        assert values["n_lead_excluded"] == 3
        assert values["n_trail_excluded"] == 0
        assert values["n_inlet_spacer_cells_excluded"] == 3
        assert values["active_membrane_base_names"] == [
            "wall_top_mem",
            "wall_bottom_mem",
        ]


class TestLayoutFromRunDirectory:
    def test_reads_mesh_layout_via_run_manifest(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        mesh_directory.mkdir(parents=True)
        mesh = mesh_payload()
        mesh["n_buffer_out"] = 2
        write_mesh_manifest(mesh_directory, mesh)
        run_directory = write_test_run(stop_reason="max_iter_reached")

        record, payload = layout_from_run_directory(run_directory)
        assert payload["run_id"] == "u0p2_p6M"
        assert record.layout.n_buffer_in == 1
        assert record.layout.n_active == 7
        assert record.layout.n_buffer_out == 2

    def test_missing_run_manifest_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = run_dir(FAMILY, GEO_ID, MESH_ID, "u0p2_p6M")
        directory.mkdir(parents=True)
        with pytest.raises(ManifestError):
            layout_from_run_directory(directory)


class TestResolveLayoutRaises:
    def test_name_keyed_lookup_always_raises(self):
        with pytest.raises(RuntimeError, match="layout comes from the mesh manifest"):
            resolve_layout("D2450_a45_7c_brg110", "mesh_max085_min006_cpg5_bl4")
        with pytest.raises(RuntimeError, match="layout comes from the mesh manifest"):
            resolve_layout("Sin_ST", "mesh_max100_min006_cpg3_bl3")


class TestMeshCaseNameFromSolverReplaceLog:
    def test_log_resolves_to_msh_parent_directory(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG, encoding="utf-8"
        )
        assert (
            mesh_case_name_from_solver_replace_log(case_dir)
            == "mesh_max085_min005_cpg5_bl4"
        )

    def test_log_with_template_cas_then_msh_uses_msh_parent(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG_WITH_TEMPLATE, encoding="utf-8"
        )
        parent = mesh_case_name_from_solver_replace_log(case_dir)
        assert parent == "mesh_max085_min005_cpg5_bl4"
        assert parent != "01_Templates"
        assert "template" not in parent.lower()

    def test_log_with_two_msh_reads_uses_last(self, tmp_path: Path):
        case_dir = tmp_path / "Pillar" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG_TWO_MSH, encoding="utf-8"
        )
        assert (
            mesh_case_name_from_solver_replace_log(case_dir)
            == "mesh_max085_min005_cpg5_bl4"
        )

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
        assert (
            mesh_case_name_from_solver_replace_log(case_dir)
            == "mesh_max085_min005_cpg5_bl4"
        )


class TestReplaceLogMatchesMeshManifest:
    def _write_mesh(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        write_mesh_manifest(directory, mesh_payload())
        return directory

    def test_matching_parent_is_silent(self, monkeypatch, tmp_path: Path):
        mesh_directory = self._write_mesh(monkeypatch, tmp_path)
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        matching_log = (
            f'Reading from HOST:"C:/ro_data/meshes/{FAMILY}/{GEO_ID}/'
            f'{MESH_ID}/{GEO_ID}_{MESH_ID}.msh.h5"\n'
        )
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            matching_log, encoding="utf-8"
        )
        assert_replace_log_matches_mesh_manifest(case_dir, mesh_directory)

    def test_mismatch_raises(self, monkeypatch, tmp_path: Path):
        mesh_directory = self._write_mesh(monkeypatch, tmp_path)
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        (case_dir / "solver_mesh_replace_log_u0p2_p6M.txt").write_text(
            FIXTURE_REPLACE_LOG, encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="does not match manifest mesh_id"):
            assert_replace_log_matches_mesh_manifest(case_dir, mesh_directory)

    def test_missing_log_is_not_a_mismatch(self, monkeypatch, tmp_path: Path):
        mesh_directory = self._write_mesh(monkeypatch, tmp_path)
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        assert_replace_log_matches_mesh_manifest(case_dir, mesh_directory)


class TestSolverLogDomainExtents:
    def test_returns_metres_without_1e3_scale(self):
        x_min, x_max, y_min, y_max, z_min, z_max = parse_solver_log_domain_extents_m(
            SOLVER_EXTENTS_SNIPPET
        )
        assert x_max == 1.7325e-02
        assert math.isclose(x_min, -8.673617e-18, rel_tol=0.0, abs_tol=1e-30)
        assert math.isclose(y_min, -1.7325e-03, rel_tol=1e-12)
        assert math.isclose(y_max, 1.7325e-03, rel_tol=1e-12)
        assert math.isclose(z_min, -3.85e-04, rel_tol=1e-12)
        assert math.isclose(z_max, 3.85e-04, rel_tol=1e-12)

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


# n_total * pitch = 0.02772; total_length_m = 0.03465 (D0817_a45-style buffers).
ASYMMETRIC_BUFFER_LAYOUT = DomainLayout(
    n_buffer_in=1,
    n_active=5,
    n_buffer_out=2,
    cell_length_x_m=CELL_LENGTH_X_M,
    buffer_length_in_m=3 * CELL_LENGTH_X_M,
    buffer_length_out_m=BUFFER_LENGTH_OUT_M,
)


class TestRequireLayoutMatchesMeasuredXExtent:
    def test_matching_layout_total_passes(self):
        result = require_layout_matches_measured_x_extent(
            ASYMMETRIC_BUFFER_LAYOUT, ASYMMETRIC_BUFFER_LAYOUT.total_length_m
        )
        assert result.ok is True

    def test_missing_or_null_extent_raises(self):
        with pytest.raises(ValueError, match="missing or null"):
            require_layout_matches_measured_x_extent(ASYMMETRIC_BUFFER_LAYOUT, None)

    def test_mismatch_raises_using_total_length_not_n_total_pitch(self):
        naive = (
            ASYMMETRIC_BUFFER_LAYOUT.cell_length_x_m
            * ASYMMETRIC_BUFFER_LAYOUT.n_total
        )
        assert naive != ASYMMETRIC_BUFFER_LAYOUT.total_length_m
        with pytest.raises(ValueError, match="does not match layout nominal"):
            require_layout_matches_measured_x_extent(
                ASYMMETRIC_BUFFER_LAYOUT, naive
            )

    def test_mesh_manifest_missing_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        write_mesh_manifest(directory, mesh_payload())
        with pytest.raises(ValueError, match="missing or null"):
            require_mesh_manifest_x_extent_matches_layout(directory)

    def test_mesh_manifest_null_extent_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        payload = mesh_payload()
        payload["domain_extent_x_m"] = None
        write_mesh_manifest(directory, payload)
        with pytest.raises(ValueError, match="missing or null"):
            require_mesh_manifest_x_extent_matches_layout(directory)

    def test_mesh_manifest_matching_layout_passes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
        directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
        directory.mkdir(parents=True)
        payload = mesh_payload()
        payload["domain_extent_x_m"] = (
            payload["buffer_length_in_m"]
            + payload["n_active_cells"] * payload["cell_length_x_m"]
            + payload["buffer_length_out_m"]
        )
        write_mesh_manifest(directory, payload)
        result = require_mesh_manifest_x_extent_matches_layout(directory)
        assert result.ok is True
