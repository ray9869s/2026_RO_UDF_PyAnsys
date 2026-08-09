"""Additive domain-layout wiring into batch post config producers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_batch_postprocess, load_batch_report_extract

CURRENT_MESH = "mesh_max085_min006_cpg5_bl4"
LEGACY_MESH = "mesh_max100_min006_cpg3_bl3"


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


@pytest.fixture
def batch_report():
    return load_batch_report_extract()


class TestWriteReportConfigLayoutKeys:
    def test_registered_current_geometry_writes_layout_and_buffer_names(
        self, batch_post, tmp_path: Path
    ):
        case_dir = tmp_path / "D2450_a45_7c_brg110" / f"u0p2_p6M__{CURRENT_MESH}"
        case_dir.mkdir(parents=True)
        config_path = tmp_path / "report_config.py"
        layout_settings, error = batch_post.try_resolve_post_layout_settings(
            "D2450_a45_7c_brg110", CURRENT_MESH
        )
        assert error is None
        assert layout_settings is not None

        batch_post.write_report_config(
            config_path,
            tmp_path,
            "D2450_a45_7c_brg110",
            f"u0p2_p6M__{CURRENT_MESH}",
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
        # Legacy length keys still present and unchanged in meaning.
        assert "domain_length_m = 0.017325" in text
        assert "buffer_length_m = 0.003465" in text

    def test_registered_legacy_geometry_writes_legacy_buffer_names(
        self, batch_post, tmp_path: Path
    ):
        case_dir = tmp_path / "Sin_ST" / f"u0p1_p4M__{LEGACY_MESH}"
        case_dir.mkdir(parents=True)
        config_path = tmp_path / "report_config.py"
        layout_settings, error = batch_post.try_resolve_post_layout_settings(
            "Sin_ST", LEGACY_MESH
        )
        assert error is None
        batch_post.write_report_config(
            config_path,
            tmp_path,
            "Sin_ST",
            f"u0p1_p4M__{LEGACY_MESH}",
            {"case_dir": case_dir},
            layout_settings,
        )
        text = config_path.read_text(encoding="utf-8")
        assert "n_buffer_in = 1" in text
        assert "n_active = 3" in text
        assert "n_buffer_out = 1" in text
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

    def test_unregistered_geometry_is_layout_unknown_and_does_not_raise(
        self, batch_post, tmp_path: Path
    ):
        results_root = tmp_path / "03_Results"
        # Plain name, no replace log → mesh unresolved → LAYOUT_UNKNOWN.
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
        assert "mesh_case_name" in result["error_summary"]
        assert "UnknownGeo" in result["error_summary"]
        # Must not count as STATUS_FAILED (continue_on_error only stops on FAILED).
        assert result["report_stage_status"] != batch_post.STATUS_FAILED

    def test_dry_run_still_resolves_registered_layout_without_writing_config(
        self, batch_post, tmp_path: Path
    ):
        results_root = tmp_path / "03_Results"
        case_name = f"u0p1_p4M__{LEGACY_MESH}"
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
        assert result["report_stage_status"] == batch_post.STATUS_DRY_RUN
        config_dir = batch_dir / "report_configs"
        assert not config_dir.exists() or not any(config_dir.iterdir())


class TestBatchReportOverridesLayoutKeys:
    def test_registered_overrides_include_layout_keys(self, batch_report):
        overrides, error = batch_report.build_post_case_overrides(
            geo_name="D2450_a45_7c_brg110",
            case_name=f"u0p2_p6M__{CURRENT_MESH}",
            final_case_file="final.cas.h5",
            final_data_file="final.dat.h5",
            inlet_velocity_value=0.2,
            outlet_gauge_pressure=6.0e6,
            mesh_case_name=CURRENT_MESH,
        )
        assert error is None
        assert overrides["n_buffer_in"] == 1
        assert overrides["n_active"] == 7
        assert overrides["n_buffer_out"] == 2
        assert overrides["cell_length_x_m"] == 0.003465
        assert overrides["buffer_wall_base_names"] == [
            "wall_top_buffer_in",
            "wall_top_buffer_out",
            "wall_bottom_buffer_in",
            "wall_bottom_buffer_out",
        ]
        assert overrides["active_membrane_base_names"] == [
            "wall_top_mem",
            "wall_bottom_mem",
        ]

    def test_unregistered_overrides_are_layout_unknown(self, batch_report):
        overrides, error = batch_report.build_post_case_overrides(
            geo_name="UnknownGeo",
            case_name="u0p1_p4M",
            final_case_file="final.cas.h5",
            final_data_file="final.dat.h5",
        )
        assert overrides is None
        assert error is not None
        assert "mesh_case_name" in error
        assert "UnknownGeo" in error
