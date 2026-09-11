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


SOCKET_RESET_ERROR = (
    "RuntimeError: IOCP/Socket: Connection reset "
    "(An existing connection was forcibly closed by the remote host, 10054)"
)
SCHEME_HEAP_ERROR = (
    "RuntimeError: wta(1st) to string->symbol\n"
    "Error Object: #[free (3 cells)]\n"
    "Error: Attempt to mark a free block\n"
    "Error encountered in critical code section"
)
CANONICAL_CP_WRAPPED_SCHEME = (
    "Canonical CP cannot be computed: segmented membrane CP failed "
    "(RuntimeError: wta(1st) to string->symbol / #[free (3 cells)])."
)
CANONICAL_CP_LOGIC = (
    "Canonical CP cannot be computed: summary_metrics_wide is missing "
    "required columns ['cp_canon_window_avg']."
)
LOAD_BEARING_ERROR = (
    "Load-bearing report compute failed: pp_m_in: missing or None"
)
MISSING_CAS_ERROR = (
    "Final case file not found: C:/ro_data/runs/diamond/D0817_a45/"
    "mesh/u0p3_p6M/D0817_a45_u0p3_p6M_final.cas.h5"
)
LAUNCH_SPAWN_ERROR = (
    "ansys.fluent.core.launcher.error_handler.LaunchFluentError:\n"
    "Fluent Launch command: fluent 3ddp -sifile=C:/tmp/serverinfo-hwtree.txt\n"
    "Deadline Exceeded\n"
    "Failed to construct hwtree for collect command. 0x8000ffff\n"
    "Aborting:"
)


class TestReportTransientRetry:
    def test_socket_reset_is_retryable(self, batch_post):
        assert (
            batch_post.classify_retryable_report_failure(SOCKET_RESET_ERROR)
            == batch_post.RETRY_KIND_SOCKET_RESET
        )

    def test_scheme_heap_is_retryable(self, batch_post):
        assert (
            batch_post.classify_retryable_report_failure(SCHEME_HEAP_ERROR)
            == batch_post.RETRY_KIND_SCHEME_HEAP
        )

    def test_canonical_cp_wrapper_around_scheme_is_retryable(self, batch_post):
        assert (
            batch_post.classify_retryable_report_failure(CANONICAL_CP_WRAPPED_SCHEME)
            == batch_post.RETRY_KIND_SCHEME_HEAP
        )

    def test_canonical_cp_without_session_signature_is_not_retryable(self, batch_post):
        assert batch_post.classify_retryable_report_failure(CANONICAL_CP_LOGIC) is None

    def test_load_bearing_without_session_signature_is_not_retryable(self, batch_post):
        assert batch_post.classify_retryable_report_failure(LOAD_BEARING_ERROR) is None

    def test_missing_cas_is_not_retryable(self, batch_post):
        assert batch_post.classify_retryable_report_failure(MISSING_CAS_ERROR) is None

    def test_launch_spawn_is_retryable(self, batch_post):
        assert (
            batch_post.classify_retryable_report_failure(LAUNCH_SPAWN_ERROR)
            == batch_post.RETRY_KIND_LAUNCH_SPAWN
        )

    def test_launch_spawn_wrapped_in_non_retryable_is_retryable(self, batch_post):
        wrapped = (
            "RuntimeError: Inlet BC readback failed on inlet: stale magnitude\n"
            + LAUNCH_SPAWN_ERROR
        )
        assert (
            batch_post.classify_retryable_report_failure(wrapped)
            == batch_post.RETRY_KIND_LAUNCH_SPAWN
        )

    def test_retries_socket_reset_then_succeeds(self, batch_post, tmp_path):
        calls = []
        sleeps = []

        def runner(stage, command, log_path, dry_run, env=None):
            calls.append(log_path.name)
            log_path.write_text(SOCKET_RESET_ERROR if len(calls) == 1 else "ok", encoding="utf-8")
            if len(calls) == 1:
                return batch_post.StageResult(
                    status=batch_post.STATUS_FAILED,
                    returncode=1,
                    log_file=log_path.as_posix(),
                    error_summary=SOCKET_RESET_ERROR,
                    stdout_tail=SOCKET_RESET_ERROR,
                )
            return batch_post.StageResult(
                status=batch_post.STATUS_SUCCESS,
                returncode=0,
                log_file=log_path.as_posix(),
            )

        result, attempts, kinds = batch_post.run_report_stage_with_retries(
            ["python", "scripts/pyfluent_report_extract.py"],
            tmp_path,
            "D2450_a45",
            "max085_min006_cpg5_bl6_peel2",
            "u0p2_p8M",
            False,
            max_retries=2,
            settle_s=15.0,
            sleeper=sleeps.append,
            stage_runner=runner,
        )
        assert result.status == batch_post.STATUS_SUCCESS
        assert attempts == 2
        assert kinds == [batch_post.RETRY_KIND_SOCKET_RESET]
        assert sleeps == [15.0]
        assert calls[0].endswith("__report.log")
        assert "bl6" in calls[0]
        assert calls[1].endswith("__report_retry1.log")

    def test_does_not_retry_load_bearing(self, batch_post, tmp_path):
        calls = []

        def runner(stage, command, log_path, dry_run, env=None):
            calls.append(1)
            log_path.write_text(LOAD_BEARING_ERROR, encoding="utf-8")
            return batch_post.StageResult(
                status=batch_post.STATUS_FAILED,
                returncode=1,
                log_file=log_path.as_posix(),
                error_summary=LOAD_BEARING_ERROR,
            )

        result, attempts, kinds = batch_post.run_report_stage_with_retries(
            ["python", "scripts/pyfluent_report_extract.py"],
            tmp_path,
            "D0817_a45",
            "max085_min006_cpg5_bl4_peel2",
            "u0p3_p6M",
            False,
            max_retries=2,
            settle_s=15.0,
            sleeper=lambda _s: None,
            stage_runner=runner,
        )
        assert result.status == batch_post.STATUS_FAILED
        assert attempts == 1
        assert kinds == []
        assert calls == [1]

    def test_retries_wrapped_scheme_heap(self, batch_post, tmp_path):
        calls = []

        def runner(stage, command, log_path, dry_run, env=None):
            calls.append(1)
            log_path.write_text(CANONICAL_CP_WRAPPED_SCHEME, encoding="utf-8")
            return batch_post.StageResult(
                status=batch_post.STATUS_FAILED,
                returncode=1,
                log_file=log_path.as_posix(),
                error_summary=CANONICAL_CP_WRAPPED_SCHEME,
            )

        result, attempts, kinds = batch_post.run_report_stage_with_retries(
            ["python", "scripts/pyfluent_report_extract.py"],
            tmp_path,
            "D0817_a45",
            "max085_min006_cpg5_bl4_peel2",
            "u0p3_p6M",
            False,
            max_retries=2,
            settle_s=0.0,
            sleeper=lambda _s: None,
            stage_runner=runner,
        )
        assert attempts == 3
        assert kinds == [
            batch_post.RETRY_KIND_SCHEME_HEAP,
            batch_post.RETRY_KIND_SCHEME_HEAP,
        ]
        assert result.status == batch_post.STATUS_FAILED
        assert calls == [1, 1, 1]


def test_stage_log_path_includes_mesh_id(batch_post, tmp_path):
    path = batch_post.stage_log_path(
        tmp_path,
        "D2450_a45",
        "u0p2_p8M",
        "report",
        "max085_min006_cpg5_bl6_peel2",
    )
    assert path.name == (
        "D2450_a45__max085_min006_cpg5_bl6_peel2__u0p2_p8M__report.log"
    )
    bl4 = batch_post.stage_log_path(
        tmp_path,
        "D2450_a45",
        "u0p2_p8M",
        "report",
        "max085_min006_cpg5_bl4_peel2",
    )
    assert path != bl4


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
