"""Additive domain-layout wiring into batch post config producers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_batch_postprocess, load_batch_report_extract
from ro.domain_layout import layout_from_mesh_manifest, layout_post_config_values
from ro.manifest import write_mesh_manifest
from ro.paths import mesh_dir, project_root
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload, run_payload, write_test_run


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


@pytest.fixture
def batch_report():
    return load_batch_report_extract()


def _write_mesh_and_run(monkeypatch, tmp_path: Path, *, n_buffer_out: int = 2):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    payload = mesh_payload()
    payload["n_buffer_out"] = n_buffer_out
    write_mesh_manifest(directory, payload)
    run_directory = write_test_run(stop_reason="max_iter_reached")
    return run_directory, layout_post_config_values(layout_from_mesh_manifest(directory))


class TestBuildReportOverrides:
    def test_overrides_include_layout_and_run_operating_values(
        self, batch_post, monkeypatch, tmp_path: Path
    ):
        run_directory, layout_settings = _write_mesh_and_run(monkeypatch, tmp_path)
        cas = run_directory / f"{GEO_ID}_u0p2_p6M_final.cas.h5"
        dat = run_directory / f"{GEO_ID}_u0p2_p6M_final.dat.h5"
        payload = run_payload()
        payload["stop_reason"] = "max_iter_reached"
        overrides = batch_post.build_report_overrides(
            payload,
            layout_settings,
            run_directory,
            cas,
            dat,
        )
        assert overrides["n_buffer_in"] == 1
        assert overrides["n_active"] == 7
        assert overrides["n_buffer_out"] == 2
        assert overrides["n_lead_excluded"] == 3
        assert overrides["n_trail_excluded"] == 0
        assert overrides["n_inlet_spacer_cells_excluded"] == 3
        assert overrides["inlet_velocity_value"] == 0.2
        assert overrides["outlet_gauge_pressure"] == 6.0e6
        assert overrides["project_root"] == str(project_root())
        assert overrides["case_path"] == str(run_directory)
        assert overrides["mesh_resolution_source"] == "mesh_manifest"
        assert "wall_top_buffer_in" in overrides["buffer_wall_base_names"]


class TestBatchPostLayoutFromManifest:
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

    def test_row_without_run_identity_fails_loudly(
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

        assert plan["report_stage_status"] == batch_post.STATUS_FAILED
        assert result["report_stage_status"] == batch_post.STATUS_FAILED
        assert "family/geo_id/mesh_id/run_id" in result["error_summary"]
        assert result["report_stage_status"] != batch_post.STATUS_LAYOUT_UNKNOWN

    def test_dry_run_uses_manifest_layout(
        self, batch_post, monkeypatch, tmp_path: Path
    ):
        run_directory, _layout = _write_mesh_and_run(monkeypatch, tmp_path)
        batch_dir = tmp_path / "batch"
        log_dir = tmp_path / "logs"
        batch_dir.mkdir()
        log_dir.mkdir()

        args = self._minimal_args(batch_post, tmp_path / "runs", dry_run=True)
        plan, result = batch_post.execute_case(
            row={
                "geo_name": GEO_ID,
                "case_name": "u0p2_p6M",
                "case_dir": str(run_directory),
                "family": FAMILY,
                "geo_id": GEO_ID,
                "mesh_id": MESH_ID,
                "run_id": "u0p2_p6M",
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
        assert result["error_summary"] == ""
        assert not (batch_dir / "report_configs").exists() or not any(
            (batch_dir / "report_configs").iterdir()
        )

    def test_pyensight_command_derives_bounds_from_mesh_extent(self, batch_post):
        args = self._minimal_args(
            batch_post, Path("/tmp/runs"), dry_run=True
        )
        args.manual_view_bounds = None
        payload = mesh_payload()
        payload["domain_extent_x_m"] = 0.03465
        payload["domain_extent_y_m"] = 0.003469131
        command = batch_post.build_pyensight_command(
            args,
            family=FAMILY,
            geo_id=GEO_ID,
            mesh_id=MESH_ID,
            run_id="u0p2_p6M",
            geo_name=GEO_ID,
            case_name="u0p2_p6M",
            mesh_payload=payload,
        )
        joined = " ".join(command)
        assert "0.010395" not in joined
        assert "0.03465" in joined
        assert "--manual-view-bounds" in command

    def test_missing_extent_fails_when_bounds_not_overridden(
        self, batch_post, monkeypatch, tmp_path: Path
    ):
        run_directory, _layout = _write_mesh_and_run(monkeypatch, tmp_path)
        args = self._minimal_args(batch_post, tmp_path / "runs", dry_run=True)
        args.manual_view_bounds = None
        (tmp_path / "batch").mkdir()
        (tmp_path / "logs").mkdir()
        plan, result = batch_post.execute_case(
            row={
                "geo_name": GEO_ID,
                "case_name": "u0p2_p6M",
                "case_dir": str(run_directory),
                "family": FAMILY,
                "geo_id": GEO_ID,
                "mesh_id": MESH_ID,
                "run_id": "u0p2_p6M",
                "case_status": "READY_FOR_POSTPROCESSING",
                "convergence_status": "MAX_ITER_REACHED",
            },
            selected_index=1,
            args=args,
            fields=["cp_inlet"],
            batch_dir=tmp_path / "batch",
            log_dir=tmp_path / "logs",
        )
        assert result["pyensight_contour_stage_status"] == batch_post.STATUS_FAILED
        assert "domain_extent_x_m" in result["error_summary"]


class TestBatchReportOverridesLayoutKeys:
    def test_overrides_from_run_directory(
        self, batch_report, monkeypatch, tmp_path: Path
    ):
        run_directory, _layout = _write_mesh_and_run(monkeypatch, tmp_path)
        cas = run_directory / "final.cas.h5"
        dat = run_directory / "final.dat.h5"
        overrides, error = batch_report.build_post_case_overrides(
            geo_name=GEO_ID,
            case_name="u0p2_p6M",
            final_case_file=cas,
            final_data_file=dat,
            case_dir=run_directory,
        )
        assert error is None
        assert overrides is not None
        assert overrides["n_buffer_out"] == 2
        assert overrides["n_lead_excluded"] == 3
        assert overrides["n_trail_excluded"] == 0
        assert overrides["n_inlet_spacer_cells_excluded"] == 3
        assert overrides["inlet_velocity_value"] == 0.2
        assert overrides["mesh_resolution_source"] == "mesh_manifest"

    def test_missing_case_dir_and_mesh_directory_is_error(self, batch_report):
        overrides, error = batch_report.build_post_case_overrides(
            geo_name="UnknownGeo",
            case_name="u0p1_p4M",
            final_case_file="final.cas.h5",
            final_data_file="final.dat.h5",
        )
        assert overrides is None
        assert error is not None
        assert "case_dir or mesh_directory" in error
        assert "LAYOUT_UNKNOWN" not in error


class TestResolveFinalCasDatNames:
    def test_expected_pair_is_used(self, batch_post, tmp_path: Path):
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        cas = case_dir / f"{GEO_ID}_u0p2_p6M_final.cas.h5"
        dat = case_dir / f"{GEO_ID}_u0p2_p6M_final.dat.h5"
        cas.write_bytes(b"cas")
        dat.write_bytes(b"dat")
        assert batch_post.resolve_final_cas_dat(
            case_dir, GEO_ID, "u0p2_p6M"
        ) == (cas, dat)

    def test_mismatched_finals_raise(self, batch_post, tmp_path: Path):
        case_dir = tmp_path / "run"
        case_dir.mkdir()
        (
            case_dir
            / "D2450_a45_7c_brg110_u0p2_p6M__mesh_max085_min006_cpg5_bl4_final.cas.h5"
        ).write_bytes(b"cas")
        (
            case_dir
            / "D2450_a45_7c_brg110_u0p2_p6M__mesh_max085_min006_cpg5_bl4_final.dat.h5"
        ).write_bytes(b"dat")
        with pytest.raises(FileNotFoundError, match="refusing a glob fallback"):
            batch_post.resolve_final_cas_dat(case_dir, GEO_ID, "u0p2_p6M")
