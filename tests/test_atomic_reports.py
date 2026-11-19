"""B2-02: an extract is staged and swapped in only after every gate passed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro import atomic_reports
from ro.atomic_reports import (
    begin_report_extract,
    publish_extract,
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
    # Moved aside, never deleted.
    assert [path.name.startswith("extract_source.invalidated_") for path in reports.glob("*.json")] == [True]
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


def test_extract_publishes_gate_only_after_promote():
    """Gates, then non-gate manifest fields, then publish_extract (hash,
    sidecar, promote, gate). The gate payload is never written before."""
    assert "report_path = staging_report_path(final_report_path)" in EXTRACT_SOURCE
    begin = EXTRACT_SOURCE.index("begin_report_extract(\n")
    first_fluent = EXTRACT_SOURCE.index("sync_run_manifest_analytic_cwall(case_path)\n")
    assert begin < first_fluent
    wide_csv = EXTRACT_SOURCE.index("summary_wide_df.to_csv(summary_wide_csv_path")
    gates = EXTRACT_SOURCE.index("require_canonical_cp_summary_columns(wide_record)")
    non_gate = EXTRACT_SOURCE.index("**_evaluation_window_selection.manifest_fields()")
    publish = EXTRACT_SOURCE.index("publish_extract(\n")
    assert wide_csv < gates < non_gate < publish
    assert EXTRACT_SOURCE.count("manifest_quality_payload(quality_result)") == 1
    assert EXTRACT_SOURCE.index("gate_payload=manifest_quality_payload(quality_result)") > publish
    assert "promote_staged_reports(" not in EXTRACT_SOURCE
    assert "write_extract_source_record(" not in EXTRACT_SOURCE
    # Any exception in the extract clears the gate again.
    handler = EXTRACT_SOURCE.index("ERROR: PyFluent post-processing report extraction failed.")
    assert "clear_manifest_gate(case_path)" in EXTRACT_SOURCE[handler - 600:handler]


def test_publish_extract_source_order_is_hash_sidecar_promote_gate():
    source = (Path(atomic_reports.__file__)).read_text(encoding="utf-8")
    body = source[source.index("def publish_extract("):]
    order = [
        body.index("hasher(source_case_file)"),
        body.index("write_extract_source_record(staging"),
        body.index("promote_staged_reports(staging"),
        body.index("GATE_GENERATION_FIELD: generation"),
    ]
    assert order == sorted(order)


def _published_leaf(monkeypatch, tmp_path):
    """A leaf with PASS reports from generation g0, and a new staged extract."""
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    leaf = write_test_run()
    case, data = leaf / "f.cas.h5", leaf / "f.dat.h5"
    case.write_bytes(b"case")
    data.write_bytes(b"data")
    final = leaf / "post" / "reports"
    final.mkdir(parents=True)
    (final / "summary_metrics_wide.csv").write_text("old\n", encoding="utf-8")
    update_run_manifest_fields(leaf, {"convergence_quality": "PASS"})
    begin_report_extract(leaf, final)
    staged = staging_report_path(final, "g1")
    staged.mkdir()
    (staged / "summary_metrics_wide.csv").write_text("new\n", encoding="utf-8")
    kwargs = dict(
        case_path=leaf, staging=staged, final=final, source_case_file=case,
        source_data_file=data, gate_payload={"convergence_quality": "PASS"}, now=MOMENT,
    )
    return leaf, final, staged, kwargs


def test_publish_extract_success_binds_gate_to_generation(monkeypatch, tmp_path):
    from ro.report_binding import report_binding_reason
    from ro.solver_common import FINAL_CASE_SHA256_FIELD, FINAL_DATA_SHA256_FIELD, sha256_file

    leaf, final, staged, kwargs = _published_leaf(monkeypatch, tmp_path)
    archive, generation = publish_extract(**kwargs)
    assert generation == "g1"
    assert (final / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "new\n"
    assert (archive / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "old\n"
    manifest = read_run_manifest(leaf)
    assert manifest["convergence_quality"] == "PASS"
    assert manifest["convergence_quality_report_generation"] == "g1"
    sidecar = json.loads((final / EXTRACT_SOURCE_FILENAME).read_text(encoding="utf-8"))
    assert sidecar["report_generation"] == "g1"
    update_run_manifest_fields(leaf, {
        FINAL_CASE_SHA256_FIELD: sha256_file(leaf / "f.cas.h5"),
        FINAL_DATA_SHA256_FIELD: sha256_file(leaf / "f.dat.h5"),
    })
    assert report_binding_reason(leaf) is None
    # A gate from another generation does not bind.
    update_run_manifest_fields(leaf, {"convergence_quality_report_generation": "g0"})
    assert "generation" in report_binding_reason(leaf)


def _fail(*_args, **_kwargs):
    raise OSError("injected failure")


@pytest.mark.parametrize("stage", ["hash", "sidecar", "archive_rename", "staging_rename"])
def test_publish_extract_failure_leaves_no_pass_and_old_reports(monkeypatch, tmp_path, stage):
    leaf, final, staged, kwargs = _published_leaf(monkeypatch, tmp_path)
    if stage == "hash":
        kwargs["hasher"] = _fail
    elif stage == "sidecar":
        monkeypatch.setattr(atomic_reports, "write_extract_source_record", _fail)
    else:
        original = Path.rename
        target = final if stage == "archive_rename" else staged

        def rename(self, destination):
            if self == target:
                raise OSError("injected rename failure")
            return original(self, destination)

        monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="injected"):
        publish_extract(**kwargs)
    monkeypatch.undo()
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["convergence_quality"] is None
    assert manifest["convergence_quality_report_generation"] is None
    assert (final / "summary_metrics_wide.csv").read_text(encoding="utf-8") == "old\n"
    assert not (final / EXTRACT_SOURCE_FILENAME).exists()
    assert staged.is_dir()  # left for inspection


def test_publish_extract_gate_write_failure_clears_gate(monkeypatch, tmp_path):
    leaf, final, staged, kwargs = _published_leaf(monkeypatch, tmp_path)
    monkeypatch.setattr(atomic_reports, "update_run_manifest_fields", _fail)
    with pytest.raises(OSError, match="injected"):
        publish_extract(**kwargs)
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["convergence_quality"] is None


def test_extract_worker_exits_nonzero_when_publish_fails():
    """The worker re-raises after clearing the gate; batch_report_extract
    maps a raising worker to a non-zero return code."""
    handler = EXTRACT_SOURCE.index("ERROR: PyFluent post-processing report extraction failed.")
    tail = EXTRACT_SOURCE[handler:handler + 400]
    assert "raise" in tail


def test_reextract_archive_keeps_manifest_copy(tmp_path):
    reextract_runs = load_module(
        "reextract_runs_atomic_test", SCRIPTS_DIR / "mfbo" / "reextract_runs.py"
    )
    leaf = tmp_path / "leaf"
    (leaf / "post" / "reports").mkdir(parents=True)
    (leaf / "post" / "reports" / EXTRACT_SOURCE_FILENAME).write_text("{}", encoding="utf-8")
    (leaf / "manifest.json").write_text(json.dumps({"convergence_quality": "PASS"}), encoding="utf-8")
    dest = reextract_runs.archive_reports(leaf, now=MOMENT)
    # The archive keeps the gate as it was; the live manifest is cleared.
    assert json.loads((dest / "manifest.json").read_text(encoding="utf-8")) == {
        "convergence_quality": "PASS"
    }
    assert json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))["convergence_quality"] is None
    assert not (dest / EXTRACT_SOURCE_FILENAME).exists()
    assert list(dest.glob("extract_source.invalidated_*.json"))


def test_reextract_archive_invalidates_before_rename(monkeypatch, tmp_path):
    """A failed archive rename still leaves no PASS and no binding sidecar."""
    reextract_runs = load_module(
        "reextract_runs_atomic_order", SCRIPTS_DIR / "mfbo" / "reextract_runs.py"
    )
    leaf = tmp_path / "leaf"
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / EXTRACT_SOURCE_FILENAME).write_text("{}", encoding="utf-8")
    (leaf / "manifest.json").write_text(json.dumps({"convergence_quality": "PASS"}), encoding="utf-8")
    original = Path.rename

    def rename(self, destination):
        if self == reports:
            raise OSError("injected archive failure")
        return original(self, destination)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="injected"):
        reextract_runs.archive_reports(leaf, now=MOMENT)
    monkeypatch.undo()
    assert json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))["convergence_quality"] is None
    assert not (reports / EXTRACT_SOURCE_FILENAME).exists()
