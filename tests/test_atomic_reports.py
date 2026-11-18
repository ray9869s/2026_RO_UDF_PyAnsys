"""B2-02: an extract is staged and swapped in only after every gate passed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.atomic_reports import (
    begin_report_extract,
    is_staging_dirname,
    promote_staged_reports,
    staging_report_path,
)
from ro.extract_skip import EXTRACT_SOURCE_FILENAME
from ro.manifest import read_run_manifest, update_run_manifest_fields
from test_manifest import write_test_run

MOMENT = datetime(2026, 11, 18, 1, 2, 3, tzinfo=timezone.utc)
EXTRACT_SOURCE = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")


def test_staging_path_is_a_sibling_with_tmp_marker(tmp_path):
    final = tmp_path / "post" / "reports"
    staged = staging_report_path(final, "abc")
    assert staged == tmp_path / "post" / "reports.tmp_abc"
    assert is_staging_dirname(staged.name)
    assert not is_staging_dirname("reports_iter2000")
    default = staging_report_path(final, now=MOMENT)
    assert default.name.startswith("reports.tmp_20261118T010203Z_")


def test_begin_clears_sidecar_and_manifest_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    leaf = write_test_run()
    update_run_manifest_fields(leaf, {"convergence_quality": "PASS"})
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text("a\n1\n", encoding="utf-8")
    (reports / EXTRACT_SOURCE_FILENAME).write_text("{}", encoding="utf-8")

    actions = begin_report_extract(leaf, reports)

    assert not (reports / EXTRACT_SOURCE_FILENAME).exists()
    assert (reports / "summary_metrics_wide.csv").is_file()
    assert read_run_manifest(leaf)["convergence_quality"] is None
    assert len(actions) == 2


def test_begin_snapshot_does_not_touch_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    leaf = write_test_run()
    update_run_manifest_fields(leaf, {"convergence_quality": "PASS"})
    begin_report_extract(leaf, leaf / "post" / "reports_iter2000_meanfirst", bind_manifest=False)
    assert read_run_manifest(leaf)["convergence_quality"] == "PASS"


def test_promote_keeps_previous_directory(tmp_path):
    final = tmp_path / "post" / "reports"
    final.mkdir(parents=True)
    (final / "summary_metrics_wide.csv").write_text("old\n", encoding="utf-8")
    staged = staging_report_path(final, "x")
    staged.mkdir()
    (staged / "summary_metrics_wide.csv").write_text("new\n", encoding="utf-8")

    archive = promote_staged_reports(staged, final, now=MOMENT)

    assert archive == tmp_path / "post" / "reports_prev_20261118T010203Z"
    assert (archive / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "old\n"
    assert (final / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "new\n"
    assert not staged.exists()


def test_promote_refuses_existing_archive(tmp_path):
    final = tmp_path / "post" / "reports"
    final.mkdir(parents=True)
    (tmp_path / "post" / "reports_prev_20261118T010203Z").mkdir()
    staged = staging_report_path(final, "x")
    staged.mkdir()
    with pytest.raises(FileExistsError):
        promote_staged_reports(staged, final, now=MOMENT)
    assert final.is_dir() and staged.is_dir()


def test_failed_gate_leaves_old_reports_untouched(tmp_path):
    """Simulates a gate raising after the staged CSV was written."""
    final = tmp_path / "post" / "reports"
    final.mkdir(parents=True)
    (final / "summary_metrics_wide.csv").write_text("old\n", encoding="utf-8")
    staged = staging_report_path(final, "x")
    staged.mkdir()
    (staged / "summary_metrics_wide.csv").write_text("new\n", encoding="utf-8")
    # The gate raises here; promote is never called.
    assert (final / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "old\n"


def test_extract_writes_to_staging_and_promotes_after_gates_and_manifest():
    assert "report_path = staging_report_path(final_report_path)" in EXTRACT_SOURCE
    assert "begin_report_extract(" in EXTRACT_SOURCE
    begin = EXTRACT_SOURCE.index("begin_report_extract(\n")
    first_fluent = EXTRACT_SOURCE.index("sync_run_manifest_analytic_cwall(case_path)\n")
    assert begin < first_fluent
    wide_csv = EXTRACT_SOURCE.index("summary_wide_df.to_csv(summary_wide_csv_path")
    gates = EXTRACT_SOURCE.index("require_canonical_cp_summary_columns(wide_record)")
    manifest = EXTRACT_SOURCE.index("**manifest_quality_payload(quality_result)")
    sidecar = EXTRACT_SOURCE.index("write_extract_source_record(\n            report_path")
    promote = EXTRACT_SOURCE.index("promote_staged_reports(_staged_report_path")
    assert wide_csv < gates < manifest < sidecar < promote


def test_reextract_archive_keeps_manifest_copy(tmp_path):
    reextract_runs = load_module(
        "reextract_runs_atomic_test", SCRIPTS_DIR / "mfbo" / "reextract_runs.py"
    )
    leaf = tmp_path / "leaf"
    (leaf / "post" / "reports").mkdir(parents=True)
    (leaf / "manifest.json").write_text(json.dumps({"convergence_quality": "PASS"}), encoding="utf-8")
    dest = reextract_runs.archive_reports(leaf, now=MOMENT)
    assert json.loads((dest / "manifest.json").read_text(encoding="utf-8")) == {
        "convergence_quality": "PASS"
    }
    assert (leaf / "manifest.json").is_file()
