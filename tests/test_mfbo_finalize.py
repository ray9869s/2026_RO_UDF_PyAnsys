"""Finalize a completed mfbo extract without launching Fluent."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.extract_profile import MFBO_REQUIRED_SUMMARY_COLUMNS, PROFILE_MFBO
from ro.mfbo_adapter import _run_state
from test_manifest import write_test_run

finalize = load_module(
    "finalize_run_leaf_under_test",
    SCRIPTS_DIR / "mfbo" / "finalize_run_leaf.py",
)


def _write_reports(run_directory, row):
    reports = run_directory / "post" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / "summary_metrics_wide.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    for name in finalize.REQUIRED_REPORT_FILES:
        target = reports / name
        if not target.exists():
            target.write_text("{}\n", encoding="utf-8")


def _mfbo_row(**overrides):
    row = {name: "1" for name in MFBO_REQUIRED_SUMMARY_COLUMNS}
    row["profile"] = PROFILE_MFBO
    row.update(overrides)
    return row


def _leaf(tmp_path, monkeypatch, run_id="u0p2_p6M", **manifest):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    directory = write_test_run(
        run_id=run_id,
        convergence_quality="PASS",
        **manifest,
    )
    _write_reports(directory, _mfbo_row())
    return directory


def test_finalize_script_does_not_launch_fluent():
    text = (SCRIPTS_DIR / "mfbo" / "finalize_run_leaf.py").read_text(encoding="utf-8")
    assert "launch_extract" not in text
    assert "launch_solver" not in text
    assert "ansys" not in text


def test_finalize_refuses_a_missing_report_and_does_not_write_status(
    tmp_path, monkeypatch
):
    directory = _leaf(tmp_path, monkeypatch)
    (directory / "post" / "reports" / "mass_balance.csv").unlink()
    before = (directory / "manifest.json").read_text(encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="mass_balance.csv"):
        finalize.finalize_run_leaf(directory)
    assert (directory / "manifest.json").read_text(encoding="utf-8") == before
    assert "extract_validation" not in before


def test_finalize_records_a_pass_and_the_adapter_reuses_the_leaf(
    tmp_path, monkeypatch
):
    directory = _leaf(tmp_path, monkeypatch)
    finalize.finalize_run_leaf(directory)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert payload["extract_profile"] == PROFILE_MFBO
    assert payload["extract_validation"] == "PASS"
    assert payload["convergence_quality"] == "PASS"
    assert payload["stop_reason"] == "qoi_converged"
    assert _run_state(directory) == "success"
    finalize.finalize_run_leaf(directory)
    again = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert again["extract_validation"] == "PASS"
    assert again["stop_reason"] == "qoi_converged"


def test_finalize_refuses_a_blank_score_column(tmp_path, monkeypatch):
    directory = _leaf(tmp_path, monkeypatch)
    _write_reports(directory, _mfbo_row(lmh_window_module=""))
    with pytest.raises(ValueError, match="lmh_window_module"):
        finalize.finalize_run_leaf(directory)
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert "extract_validation" not in payload


def test_walk_finalizes_complete_mfbo_extracts_and_refuses_incomplete_ones(
    tmp_path, monkeypatch, capsys
):
    complete = _leaf(tmp_path, monkeypatch, run_id="u0p2_p6M")
    incomplete = _leaf(tmp_path, monkeypatch, run_id="u0p3_p6M")
    (incomplete / "post" / "reports" / "pressure_report.csv").unlink()
    untouched = write_test_run(run_id="u0p1_p6M", convergence_quality="PASS")
    code = finalize.main(["--data-root", str(tmp_path)])
    assert code == 1
    printed = capsys.readouterr().out
    assert f"finalized {complete}" in printed
    assert f"REFUSE {incomplete}" in printed
    assert "pressure_report.csv" in printed
    assert f"skip {untouched}" in printed
    assert "finalized: 1" in printed
    done = json.loads((complete / "manifest.json").read_text(encoding="utf-8"))
    assert done["extract_validation"] == "PASS"
    assert _run_state(complete) == "success"
    left = json.loads((incomplete / "manifest.json").read_text(encoding="utf-8"))
    assert "extract_validation" not in left


def test_explicit_leaf_outside_the_data_root_is_refused(tmp_path, monkeypatch):
    directory = _leaf(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    code = finalize.main(
        ["--data-root", str(other), "--run-leaf", str(directory)]
    )
    assert code == 1
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert "extract_validation" not in payload


def test_production_data_root_is_refused(tmp_path):
    assert finalize.main(["--data-root", "C:/ro_data"]) == 1
    assert finalize.main(["--data-root", str(tmp_path)]) == 1
