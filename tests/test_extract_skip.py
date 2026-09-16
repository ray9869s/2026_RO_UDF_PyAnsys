"""Extract skip requires the wide CSV to match the current R-02 solve hashes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

from helpers import load_batch_postprocess, load_batch_report_extract
from ro.extract_skip import (
    EXTRACT_SOURCE_FILENAME,
    extract_skip_block_reason,
    inspect_extract_skip_leaf,
    read_extract_source_record,
    write_extract_source_record,
)
from ro.manifest import write_mesh_manifest, write_run_manifest
from ro.paths import mesh_dir
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
)
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, mesh_payload, run_payload, write_test_run

ATTEMPT_ID = "attempt-1"
CASE_BYTES = b"cas-bytes"
DATA_BYTES = b"dat-bytes"


def _ok_csv(_path):
    return True, "OK"


def _write_extract_leaf(
    monkeypatch,
    tmp_path,
    *,
    stamp_hashes=True,
    attempt_id=ATTEMPT_ID,
    write_csv=True,
    write_sidecar=True,
    csv_text="metric,value\ncp,1.0\n",
    case_bytes=CASE_BYTES,
    data_bytes=DATA_BYTES,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = write_test_run()
    cas = directory / f"{GEO_ID}_{RUN_ID}_final.cas.h5"
    dat = directory / f"{GEO_ID}_{RUN_ID}_final.dat.h5"
    cas.write_bytes(case_bytes)
    dat.write_bytes(data_bytes)
    payload = run_payload()
    if attempt_id:
        payload[SOLVER_ATTEMPT_ID_FIELD] = attempt_id
    if stamp_hashes:
        payload[FINAL_CASE_SHA256_FIELD] = hashlib.sha256(case_bytes).hexdigest()
        payload[FINAL_DATA_SHA256_FIELD] = hashlib.sha256(data_bytes).hexdigest()
    write_run_manifest(directory, payload)
    reports = directory / "post" / "reports"
    reports.mkdir(parents=True)
    csv_path = reports / "summary_metrics_wide.csv"
    if write_csv:
        csv_path.write_text(csv_text, encoding="utf-8")
    if write_sidecar:
        write_extract_source_record(
            reports,
            {
                FINAL_CASE_SHA256_FIELD: hashlib.sha256(case_bytes).hexdigest(),
                FINAL_DATA_SHA256_FIELD: hashlib.sha256(data_bytes).hexdigest(),
                SOLVER_ATTEMPT_ID_FIELD: attempt_id,
            },
        )
    return {
        "directory": directory,
        "cas": cas,
        "dat": dat,
        "csv": csv_path,
        "reports": reports,
    }


def _block(leaf, **kwargs):
    return extract_skip_block_reason(
        leaf["directory"],
        leaf["csv"],
        leaf["cas"],
        leaf["dat"],
        **kwargs,
    )


def test_csv_only_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, write_sidecar=False)
    reason = _block(leaf)
    assert reason is not None
    assert "extract source record was not found" in reason


def test_empty_csv_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, csv_text="")
    reason = _block(leaf)
    assert reason is not None
    assert "empty" in reason


def test_missing_r02_hashes_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, stamp_hashes=False, attempt_id="")
    reason = _block(leaf)
    assert reason is not None
    assert "missing solver_attempt_id" in reason


def test_missing_artifact_hashes_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, stamp_hashes=False)
    reason = _block(leaf)
    assert reason is not None
    assert "missing current-attempt final artifact hashes" in reason


def test_complete_hash_sidecar_is_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)
    assert _block(leaf) is None


def test_older_csv_still_skips_when_hashes_match(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)
    os.utime(leaf["csv"], (1_000_000, 1_000_000))
    os.utime(leaf["dat"], (2_000_000_000, 2_000_000_000))
    assert _block(leaf) is None


def test_re_solved_data_hash_mismatch_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)
    leaf["dat"].write_bytes(b"new-dat-bytes")
    payload = run_payload()
    payload[SOLVER_ATTEMPT_ID_FIELD] = ATTEMPT_ID
    payload[FINAL_CASE_SHA256_FIELD] = hashlib.sha256(CASE_BYTES).hexdigest()
    payload[FINAL_DATA_SHA256_FIELD] = hashlib.sha256(b"new-dat-bytes").hexdigest()
    write_run_manifest(leaf["directory"], payload)
    reason = _block(leaf)
    assert reason is not None
    assert "final_data_sha256" in reason


def test_sidecar_attempt_mismatch_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)
    write_extract_source_record(
        leaf["reports"],
        {
            FINAL_CASE_SHA256_FIELD: hashlib.sha256(CASE_BYTES).hexdigest(),
            FINAL_DATA_SHA256_FIELD: hashlib.sha256(DATA_BYTES).hexdigest(),
            SOLVER_ATTEMPT_ID_FIELD: "attempt-old",
        },
    )
    reason = _block(leaf)
    assert reason is not None
    assert "solver_attempt_id" in reason


def test_csv_validator_failure_is_not_skipped(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)

    def bad(_path):
        return False, "missing critical columns"

    reason = _block(leaf, csv_validator=bad)
    assert reason is not None
    assert "failed validation" in reason


def test_write_extract_source_record_round_trip(tmp_path):
    reports = tmp_path / "reports"
    path = write_extract_source_record(
        reports,
        {
            FINAL_CASE_SHA256_FIELD: "a" * 64,
            FINAL_DATA_SHA256_FIELD: "b" * 64,
            SOLVER_ATTEMPT_ID_FIELD: ATTEMPT_ID,
        },
    )
    assert path.name == EXTRACT_SOURCE_FILENAME
    payload = read_extract_source_record(reports / "summary_metrics_wide.csv")
    assert payload[FINAL_CASE_SHA256_FIELD] == "a" * 64
    assert payload[SOLVER_ATTEMPT_ID_FIELD] == ATTEMPT_ID


def test_inspect_csv_only_decides_run(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, write_sidecar=False)
    row = inspect_extract_skip_leaf(
        family=FAMILY,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        run_directory=leaf["directory"],
        summary_wide_csv=leaf["csv"],
        final_case_path=leaf["cas"],
        final_data_path=leaf["dat"],
    )
    assert row["csv_exists"] is True
    assert row["sidecar_exists"] is False
    assert row["manifest_has_hashes"] is True
    assert row["skip_decision"] == "RUN"


def test_inspect_complete_leaf_decides_skip(monkeypatch, tmp_path):
    leaf = _write_extract_leaf(monkeypatch, tmp_path)
    row = inspect_extract_skip_leaf(
        family=FAMILY,
        geo_id=GEO_ID,
        mesh_id=MESH_ID,
        run_id=RUN_ID,
        run_directory=leaf["directory"],
        summary_wide_csv=leaf["csv"],
        final_case_path=leaf["cas"],
        final_data_path=leaf["dat"],
    )
    assert row["skip_decision"] == "SKIP"
    assert row["skip_block"] is None


def test_extract_batch_exit_code_empty_is_zero():
    batch = load_batch_report_extract()
    assert batch.extract_batch_exit_code([]) == 0


def test_extract_batch_exit_code_failed_is_nonzero():
    batch = load_batch_report_extract()
    assert batch.extract_batch_exit_code([{"status": "SUCCESS"}]) == 0
    assert batch.extract_batch_exit_code([{"status": "SKIPPED_EXISTING"}]) == 0
    assert batch.extract_batch_exit_code(
        [{"status": batch.EXTRACT_SKIPPED_MISSING_FINALS_STATUS}]
    ) == 0
    assert batch.extract_batch_exit_code([{"status": "FAILED"}]) == 1
    assert batch.extract_batch_exit_code([{"status": "FAILED_METRIC_VALIDATION"}]) == 1
    assert batch.extract_batch_exit_code([{"status": "MISSING_CASE_DATA"}]) == 1
    assert batch.extract_batch_exit_code([], status_write_failed=True) == 1
    assert batch.extract_batch_exit_code([], merge_write_failed=True) == 1


def test_report_extract_skip_lists_run_leaves(monkeypatch, tmp_path, capsys):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, write_sidecar=False)
    batch = load_batch_report_extract()
    rows = batch.report_extract_skip_status(csv_validator=_ok_csv)
    captured = capsys.readouterr()
    assert len(rows) == 1
    assert rows[0]["skip_decision"] == "RUN"
    assert "EXTRACT SKIP REPORT" in captured.out
    assert "sidecar=no" in captured.out
    assert "hashes=yes" in captured.out
    assert str(leaf["directory"].name) in captured.out
    assert "Summary: 0 SKIP / 1 RUN" in captured.out


def test_postprocess_batch_exit_code_empty_is_zero():
    batch_post = load_batch_postprocess()
    assert batch_post.postprocess_batch_exit_code([]) == 0
    assert (
        batch_post.postprocess_batch_exit_code(
            [{"report_stage_status": batch_post.STATUS_SUCCESS}]
        )
        == 0
    )
    assert (
        batch_post.postprocess_batch_exit_code(
            [{"report_stage_status": batch_post.STATUS_FAILED}]
        )
        == 1
    )


def _minimal_post_args(batch_post, results_root: Path, **overrides):
    args = SimpleNamespace(
        results_root=results_root,
        python_exe=Path("python"),
        fields="cp_inlet",
        membrane_surface="top",
        run_reports=True,
        auto_run_missing_reports=True,
        run_pyensight_contours=True,
        run_shear=True,
        skip_existing=True,
        force=False,
        dry_run=True,
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
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _write_mesh_and_extract_leaf(monkeypatch, tmp_path, **kwargs):
    leaf = _write_extract_leaf(monkeypatch, tmp_path, **kwargs)
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True, exist_ok=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    contours = leaf["directory"] / "post" / "figures" / "contours"
    contours.mkdir(parents=True, exist_ok=True)
    (contours / "cp_inlet_membrane.png").write_bytes(b"png")
    (contours / "shear_rate_membrane.png").write_bytes(b"png")
    (contours / "contour_colorbar_ranges.json").write_text("{}", encoding="utf-8")
    (contours / "shear_colorbar_range.json").write_text("{}", encoding="utf-8")
    return leaf


def _execute(batch_post, leaf, tmp_path, **arg_overrides):
    args = _minimal_post_args(batch_post, tmp_path / "runs", **arg_overrides)
    return batch_post.execute_case(
        row={
            "geo_name": GEO_ID,
            "case_name": RUN_ID,
            "case_dir": str(leaf["directory"]),
            "family": FAMILY,
            "geo_id": GEO_ID,
            "mesh_id": MESH_ID,
            "run_id": RUN_ID,
            "case_status": "READY_FOR_POSTPROCESSING",
            "convergence_status": "MAX_ITER_REACHED",
        },
        selected_index=1,
        args=args,
        fields=["cp_inlet"],
        batch_dir=tmp_path / "batch",
        log_dir=tmp_path / "logs",
    )


def test_postprocess_skips_only_when_extract_is_current(monkeypatch, tmp_path):
    batch_post = load_batch_postprocess()
    (tmp_path / "batch").mkdir()
    (tmp_path / "logs").mkdir()
    leaf = _write_mesh_and_extract_leaf(monkeypatch, tmp_path)
    _plan, result = _execute(batch_post, leaf, tmp_path)
    assert result["report_stage_status"] == batch_post.STATUS_SKIPPED_EXISTING
    assert result["pyensight_contour_stage_status"] == batch_post.STATUS_SKIPPED_EXISTING
    assert result["shear_stage_status"] == batch_post.STATUS_SKIPPED_EXISTING


def test_postprocess_does_not_skip_csv_or_pngs_when_sidecar_missing(
    monkeypatch, tmp_path, capsys
):
    batch_post = load_batch_postprocess()
    (tmp_path / "batch").mkdir()
    (tmp_path / "logs").mkdir()
    leaf = _write_mesh_and_extract_leaf(monkeypatch, tmp_path, write_sidecar=False)
    _plan, result = _execute(batch_post, leaf, tmp_path)
    captured = capsys.readouterr()
    assert result["report_stage_status"] == batch_post.STATUS_DRY_RUN
    assert result["pyensight_contour_stage_status"] == batch_post.STATUS_DRY_RUN
    assert result["shear_stage_status"] == batch_post.STATUS_DRY_RUN
    assert "Not skipping existing report" in captured.out
    assert "extract source record was not found" in captured.out
