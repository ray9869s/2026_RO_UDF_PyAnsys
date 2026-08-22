"""Additive domain-layout wiring into batch post config producers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_batch_postprocess, load_batch_report_extract
from ro.domain_layout import layout_from_mesh_manifest, layout_post_config_values
from ro.manifest import write_mesh_manifest
from ro.paths import mesh_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


@pytest.fixture
def batch_report():
    return load_batch_report_extract()


def _current_layout_settings(monkeypatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["n_buffer_out"] = 2
    write_mesh_manifest(directory, payload)
    return layout_post_config_values(layout_from_mesh_manifest(directory))


def _legacy_layout_settings() -> dict:
    from ro.domain_layout import (
        EvaluationWindow,
        GeometryLayoutRecord,
        LEGACY_BUFFER_WALL_BASE_NAMES,
        LEGACY_LAYOUT,
        MEMBRANE_WALL_BASE_NAMES,
        layout_post_config_values,
    )

    record = GeometryLayoutRecord(
        layout=LEGACY_LAYOUT,
        membrane_wall_base_names=MEMBRANE_WALL_BASE_NAMES,
        buffer_wall_base_names=LEGACY_BUFFER_WALL_BASE_NAMES,
        evaluation_window=EvaluationWindow(1, 0),
    )
    return layout_post_config_values(record)


class TestWriteReportConfigLayoutKeys:
    def test_current_geometry_writes_layout_and_buffer_names(
        self, batch_post, monkeypatch, tmp_path: Path
    ):
        case_dir = tmp_path / "D2450_a45" / "u0p2_p6M"
        case_dir.mkdir(parents=True)
        config_path = tmp_path / "report_config.py"
        layout_settings = _current_layout_settings(monkeypatch, tmp_path)

        batch_post.write_report_config(
            config_path,
            tmp_path,
            "D2450_a45",
            "u0p2_p6M",
            {"case_dir": case_dir},
            layout_settings,
        )
        text = config_path.read_text(encoding="utf-8")

        assert "n_buffer_in = 1" in text
        assert "n_active = 7" in text
        assert "n_buffer_out = 2" in text
        assert "cell_length_x_m = 0.003465" in text
        assert "wall_top_buffer_in" in text
        assert "wall_top_buffer_out" in text
        assert "wall_bottom_buffer_in" in text
        assert "wall_bottom_buffer_out" in text
        assert "wall_top_mem" in text
        assert "domain_length_m = 0.03465" in text
        assert "buffer_length_m = 0.003465" in text
        assert "n_unit_cells = 10" in text
        assert "n_buffer_cells_each_end = None" in text
        assert "0.017325" not in text

    def test_legacy_record_writes_legacy_buffer_names(
        self, batch_post, tmp_path: Path
    ):
        case_dir = tmp_path / "Sin_ST" / "u0p1_p4M"
        case_dir.mkdir(parents=True)
        config_path = tmp_path / "report_config.py"
        layout_settings = _legacy_layout_settings()
        batch_post.write_report_config(
            config_path,
            tmp_path,
            "Sin_ST",
            "u0p1_p4M",
            {"case_dir": case_dir},
            layout_settings,
        )
        text = config_path.read_text(encoding="utf-8")
        assert "n_buffer_in = 1" in text
        assert "n_active = 3" in text
        assert "n_buffer_out = 1" in text
        assert "domain_length_m = 0.017325" in text
        assert "n_unit_cells = 5" in text
        assert "n_buffer_cells_each_end = 1" in text
        assert "buffer_wall_base_names = ['wall_top_buffer', 'wall_bottom_buffer']" in text


class TestBatchPostLayoutUnknown:
    def _minimal_args(self, batch_post, results_root: Path, *, dry_run: bool):
        return SimpleNamespace(
            results_root=results_root,
            python_exe=Path("python"),
            fields="cp_inlet,water_flux,lmh,salt_flux",
            membrane_surface="top",
            run_reports=True,
            auto_run_missing_reports=True,
            run_pyensight_contours=False,
            run_shear=False,
            skip_existing=False,
            force=False,
            dry_run=dry_run,
            continue_on_error=True,
            shear_export_mode=batch_post.SHEAR_EXPORT_MODE_AUTO,
            shear_range="0,5000",
            shear_view_margin=1.2,
            shear_width=1600,
            shear_height=1200,
            cff_name="cff_wall_shear_rate",
            cff_file_template=None,
            legend_mode="hide",
            manual_view_bounds="0,0.010395,0,0.003465",
            manual_view_plane="xy",
            view_margin=1.25,
            zoom_out=1.0,
            retry_shear_fallback_on_failure=False,
        )

    def test_name_keyed_lookup_is_layout_unknown_and_does_not_raise(
        self, batch_post, tmp_path: Path
    ):
        results_root = tmp_path / "runs"
        case_dir = results_root / "UnknownGeo" / "u0p1_p4M"
        case_dir.mkdir(parents=True)
        batch_dir = tmp_path / "batch"
        log_dir = tmp_path / "logs"
        batch_dir.mkdir()
        log_dir.mkdir()

        args = self._minimal_args(batch_post, results_root, dry_run=True)
        plan, result = batch_post.execute_case(
            row={
                "geo_name": "UnknownGeo",
                "case_name": "u0p1_p4M",
                "case_status": "READY_FOR_POSTPROCESSING",
                "convergence_status": "MAX_ITER_REACHED",
            },
            selected_index=1,
            args=args,
            fields=["cp_inlet"],
            batch_dir=batch_dir,
            log_dir=log_dir,
        )

        assert plan["report_stage_status"] == batch_post.STATUS_LAYOUT_UNKNOWN
        assert result["report_stage_status"] == batch_post.STATUS_LAYOUT_UNKNOWN
        assert result["pyensight_contour_stage_status"] == batch_post.STATUS_LAYOUT_UNKNOWN
        assert result["shear_stage_status"] == batch_post.STATUS_LAYOUT_UNKNOWN
        assert "layout comes from the mesh manifest" in result["error_summary"]
        assert result["report_stage_status"] != batch_post.STATUS_FAILED

    def test_dry_run_does_not_write_config_when_layout_unknown(
        self, batch_post, tmp_path: Path
    ):
        results_root = tmp_path / "runs"
        case_name = "u0p1_p4M"
        case_dir = results_root / "Sin_ST" / case_name
        case_dir.mkdir(parents=True)
        batch_dir = tmp_path / "batch"
        log_dir = tmp_path / "logs"
        batch_dir.mkdir()
        log_dir.mkdir()

        args = self._minimal_args(batch_post, results_root, dry_run=True)
        _plan, result = batch_post.execute_case(
            row={
                "geo_name": "Sin_ST",
                "case_name": case_name,
                "case_status": "READY_FOR_POSTPROCESSING",
                "convergence_status": "MAX_ITER_REACHED",
            },
            selected_index=1,
            args=args,
            fields=["cp_inlet"],
            batch_dir=batch_dir,
            log_dir=log_dir,
        )
        assert result["report_stage_status"] == batch_post.STATUS_LAYOUT_UNKNOWN
        config_dir = batch_dir / "report_configs"
        assert not config_dir.exists() or not any(config_dir.iterdir())


class TestBatchReportOverridesLayoutKeys:
    def test_name_keyed_overrides_are_layout_unknown(self, batch_report):
        overrides, error = batch_report.build_post_case_overrides(
            geo_name="D2450_a45_7c_brg110",
            case_name="u0p2_p6M__mesh_max085_min006_cpg5_bl4",
            final_case_file="final.cas.h5",
            final_data_file="final.dat.h5",
            inlet_velocity_value=0.2,
            outlet_gauge_pressure=6.0e6,
            mesh_case_name="mesh_max085_min006_cpg5_bl4",
        )
        assert overrides is None
        assert error is not None
        assert "layout comes from the mesh manifest" in error

    def test_unregistered_overrides_are_layout_unknown(self, batch_report):
        overrides, error = batch_report.build_post_case_overrides(
            geo_name="UnknownGeo",
            case_name="u0p1_p4M",
            final_case_file="final.cas.h5",
            final_data_file="final.dat.h5",
        )
        assert overrides is None
        assert error is not None
        assert "layout comes from the mesh manifest" in error

