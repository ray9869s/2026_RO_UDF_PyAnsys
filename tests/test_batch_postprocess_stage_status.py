"""Tests for 06 batch post-process JSON stage-status inference (F-03 partial)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import load_batch_postprocess


@pytest.fixture
def batch_post():
    return load_batch_postprocess()


def write_contour_status(path: Path, *, summary: dict, records: list[dict], overall_status: str = "") -> None:
    payload: dict = {"summary": summary, "records": records}
    if overall_status:
        payload["overall_status"] = overall_status
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_shear_status(path: Path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


class TestInferContourStageStatus:
    def test_success_when_all_requested_fields_succeed(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 2, "failed": 0, "warn": 0, "total": 2},
            records=[
                {"field_key": "cp_inlet", "status": "SUCCESS"},
                {"field_key": "water_flux", "status": "SUCCESS"},
            ],
        )
        status, note = batch_post.infer_contour_stage_status(
            status_file, 0, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_SUCCESS
        assert note == ""

    def test_warn_from_summary_count(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 1, "total": 2},
            records=[
                {"field_key": "cp_inlet", "status": "SUCCESS"},
                {"field_key": "water_flux", "status": "WARN"},
            ],
        )
        status, note = batch_post.infer_contour_stage_status(
            status_file, 0, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_WARN
        assert note == ""

    def test_warn_from_top_level_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 2, "failed": 0, "warn": 0, "total": 2},
            records=[
                {"field_key": "cp_inlet", "status": "SUCCESS"},
                {"field_key": "water_flux", "status": "SUCCESS"},
            ],
            overall_status="WARN",
        )
        status, _note = batch_post.infer_contour_stage_status(
            status_file, 0, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_WARN

    def test_failed_from_summary_count(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 1, "warn": 0, "total": 2},
            records=[
                {"field_key": "cp_inlet", "status": "SUCCESS"},
                {"field_key": "water_flux", "status": "FAILED"},
            ],
        )
        status, note = batch_post.infer_contour_stage_status(
            status_file, 0, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_failed_from_requested_field_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 0, "total": 1},
            records=[{"field_key": "cp_inlet", "status": "FAILED"}],
        )
        status, _note = batch_post.infer_contour_stage_status(status_file, 0, ["cp_inlet"])
        assert status == batch_post.STATUS_FAILED

    def test_missing_requested_field_is_unknown_not_failed(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 0, "total": 1},
            records=[{"field_key": "cp_inlet", "status": "SUCCESS"}],
        )
        status, note = batch_post.infer_contour_stage_status(
            status_file, 0, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_UNKNOWN
        assert "water_flux" in note

    def test_worker_exit_2_skips_json(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 2, "failed": 0, "warn": 0, "total": 2},
            records=[{"field_key": "cp_inlet", "status": "SUCCESS"}],
        )
        status, note = batch_post.infer_contour_stage_status(status_file, 2, ["cp_inlet"])
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_worker_exit_1_is_failed_without_json(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 1, "total": 2},
            records=[
                {"field_key": "cp_inlet", "status": "WARN"},
                {"field_key": "water_flux", "status": "SUCCESS"},
            ],
        )
        status, note = batch_post.infer_contour_stage_status(
            status_file, 1, ["cp_inlet", "water_flux"]
        )
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_missing_json_after_exit_zero_is_unknown(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        status, note = batch_post.infer_contour_stage_status(status_file, 0, ["cp_inlet"])
        assert status == batch_post.STATUS_UNKNOWN
        assert note == "status JSON missing after worker exit 0"

    def test_malformed_json_after_exit_zero_is_unknown(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        status_file.write_text("{not-json", encoding="utf-8")
        status, note = batch_post.infer_contour_stage_status(status_file, 0, ["cp_inlet"])
        assert status == batch_post.STATUS_UNKNOWN
        assert note.startswith("status JSON unreadable after worker exit 0:")


class TestInferShearStageStatus:
    def test_success_from_top_level_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(status_file, status="SUCCESS", field_key="shear_rate")
        status, note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_SUCCESS
        assert note == ""

    def test_warn_from_top_level_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(status_file, status="WARN", field_key="shear_rate")
        status, note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_WARN
        assert note == ""

    def test_failed_from_top_level_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(status_file, status="FAILED", field_key="shear_rate")
        status, note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_failed_from_native_status(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(
            status_file,
            status="SUCCESS",
            native_attempted=True,
            native_status="FAILED",
        )
        status, _note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_FAILED

    def test_worker_exit_2_skips_json(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(status_file, status="SUCCESS")
        status, note = batch_post.infer_shear_stage_status(status_file, 2)
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_worker_exit_1_is_failed_without_json(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        write_shear_status(status_file, status="WARN", field_key="shear_rate")
        status, note = batch_post.infer_shear_stage_status(status_file, 1)
        assert status == batch_post.STATUS_FAILED
        assert note == ""

    def test_missing_json_after_exit_zero_is_unknown(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        status, note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_UNKNOWN
        assert note == "status JSON missing after worker exit 0"

    def test_malformed_json_after_exit_zero_is_unknown(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "shear_contour_status.json"
        status_file.write_text("[]", encoding="utf-8")
        status, note = batch_post.infer_shear_stage_status(status_file, 0)
        assert status == batch_post.STATUS_UNKNOWN
        assert note.startswith("status JSON unreadable after worker exit 0:")


class TestRefineRecordedStageStatus:
    def test_skip_labels_are_not_refined(self, batch_post, tmp_path: Path):
        skipped = batch_post.StageResult(status=batch_post.STATUS_SKIPPED_EXISTING)
        status, note = batch_post.refine_recorded_stage_status(
            batch_post.STATUS_SKIPPED_EXISTING,
            skipped,
            batch_post.infer_contour_stage_status,
            tmp_path / "contour_export_status.json",
            0,
            ["cp_inlet"],
        )
        assert status == batch_post.STATUS_SKIPPED_EXISTING
        assert note == ""

    def test_planned_exit_zero_is_refined(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 0, "total": 1},
            records=[{"field_key": "cp_inlet", "status": "WARN"}],
        )
        ran = batch_post.StageResult(status=batch_post.STATUS_SUCCESS, returncode=0)
        status, _note = batch_post.refine_recorded_stage_status(
            batch_post.STATUS_PLANNED,
            ran,
            batch_post.infer_contour_stage_status,
            status_file,
            0,
            ["cp_inlet"],
        )
        assert status == batch_post.STATUS_WARN

    def test_planned_exit_one_is_failed(self, batch_post, tmp_path: Path):
        status_file = tmp_path / "contour_export_status.json"
        write_contour_status(
            status_file,
            summary={"success": 1, "failed": 0, "warn": 1, "total": 1},
            records=[{"field_key": "cp_inlet", "status": "WARN"}],
        )
        # Even a stale WARN label must not survive a non-zero exit.
        ran = batch_post.StageResult(status=batch_post.STATUS_WARN, returncode=1)
        status, _note = batch_post.refine_recorded_stage_status(
            batch_post.STATUS_PLANNED,
            ran,
            batch_post.infer_contour_stage_status,
            status_file,
            1,
            ["cp_inlet"],
        )
        assert status == batch_post.STATUS_FAILED


class TestWorkerExitCodeHelpers:
    def test_stage_status_from_worker_returncode(self, batch_post):
        assert batch_post.stage_status_from_worker_returncode(0) == batch_post.STATUS_SUCCESS
        assert batch_post.stage_status_from_worker_returncode(1) == batch_post.STATUS_FAILED
        assert batch_post.stage_status_from_worker_returncode(2) == batch_post.STATUS_FAILED
        assert batch_post.stage_status_from_worker_returncode(3) == batch_post.STATUS_FAILED

    def test_is_worker_hard_failure(self, batch_post):
        assert batch_post.is_worker_hard_failure(None) is False
        assert batch_post.is_worker_hard_failure(0) is False
        assert batch_post.is_worker_hard_failure(1) is True
        assert batch_post.is_worker_hard_failure(2) is True
        assert batch_post.is_worker_hard_failure(3) is True

    def test_shear_retry_on_any_nonzero_exit(self, batch_post):
        assert batch_post.shear_stage_eligible_for_fallback_retry(0) is False
        assert batch_post.shear_stage_eligible_for_fallback_retry(1) is True
        assert batch_post.shear_stage_eligible_for_fallback_retry(2) is True
        assert batch_post.shear_stage_eligible_for_fallback_retry(None) is False

    def test_worker_exit_1_counts_as_failed_in_summary(self, batch_post):
        results = [
            {
                "selected_index": 1,
                "geo_name": "D2450_a45",
                "case_name": "u0p1_p6M",
                "report_stage_status": batch_post.STATUS_FAILED,
                "pyensight_contour_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "shear_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "report_returncode": 1,
            },
            {
                "selected_index": 2,
                "geo_name": "D2450_a45",
                "case_name": "u0p2_p6M",
                "report_stage_status": batch_post.STATUS_SUCCESS,
                "pyensight_contour_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "shear_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "report_returncode": 0,
            },
            {
                "selected_index": 3,
                "geo_name": "D2450_a45",
                "case_name": "u0p3_p6M",
                "report_stage_status": batch_post.STATUS_SUCCESS,
                "pyensight_contour_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "shear_stage_status": batch_post.STATUS_SKIPPED_DISABLED,
                "report_returncode": 0,
            },
        ]
        assert batch_post.stage_status_from_worker_returncode(1) == batch_post.STATUS_FAILED
        counts = batch_post.stage_counts(results, "report_stage_status")
        assert counts[batch_post.STATUS_SUCCESS] == 2
        assert counts[batch_post.STATUS_FAILED] == 1
        assert counts.get(batch_post.STATUS_WARN, 0) == 0
        assert (
            counts[batch_post.STATUS_SUCCESS]
            + counts[batch_post.STATUS_FAILED]
            + counts.get(batch_post.STATUS_WARN, 0)
        ) == 3

        class Args:
            inventory_csv = "inventory.csv"
            results_root = "results"
            dry_run = False

        summary = batch_post.build_summary_text([], [], results, Args())
        assert "failed: 1" in summary
        assert "warn: 0" in summary
        assert "report=FAILED" in summary
        assert "report=PLANNED" not in summary


def test_run_returns_2_when_inventory_missing(batch_post, tmp_path: Path, capsys):
    args = batch_post.parse_args([
        "--inventory-csv",
        str(tmp_path / "missing_inventory.csv"),
        "--results-root",
        str(tmp_path / "results"),
        "--run-reports",
        "--no-run-pyensight-contours",
        "--no-run-shear",
    ])
    rc = batch_post.run(args)
    captured = capsys.readouterr()
    assert rc == 2
    assert "Inventory CSV not found" in captured.err
