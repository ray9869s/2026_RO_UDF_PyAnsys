"""Is ``post/reports`` an extract of the leaf's current final pair?

A continue writes ``reports_iter<N>`` and a new final, but keeps the old
``post/reports``. Reporting must not pair those numbers with the newer
manifest gate (audit B2-01). The binding is the hash pair in
``post/reports/extract_source.json`` against ``final_case_sha256`` /
``final_data_sha256`` on the run manifest. Missing evidence is unbound.

This module only reads JSON. It never hashes the multi-GB data files.
"""

from __future__ import annotations

import json
from pathlib import Path

from ro.extract_skip import EXTRACT_SOURCE_FILENAME
from ro.solver_common import FINAL_CASE_SHA256_FIELD, FINAL_DATA_SHA256_FIELD

REPORT_BOUND = "bound"


def _read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def _short(value):
    return str(value)[:12] if value else "missing"


def report_binding_reason(run_directory, reports_dirname="reports"):
    """None when the reports describe the current final; else why not."""
    run_directory = Path(run_directory)
    reports = run_directory / "post" / reports_dirname
    if not (reports / "summary_metrics_wide.csv").is_file():
        return f"post/{reports_dirname}/summary_metrics_wide.csv is missing"
    manifest_path = run_directory / "manifest.json"
    if not manifest_path.is_file():
        return "run manifest is missing"
    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError) as exc:
        return f"run manifest unreadable: {exc}"
    expected = {
        FINAL_CASE_SHA256_FIELD: manifest.get(FINAL_CASE_SHA256_FIELD),
        FINAL_DATA_SHA256_FIELD: manifest.get(FINAL_DATA_SHA256_FIELD),
    }
    if not all(expected.values()):
        return "run manifest has no final case/data sha256"
    sidecar = reports / EXTRACT_SOURCE_FILENAME
    if not sidecar.is_file():
        return f"post/{reports_dirname} has no {EXTRACT_SOURCE_FILENAME}"
    try:
        source = _read_json(sidecar)
    except (OSError, ValueError) as exc:
        return f"{EXTRACT_SOURCE_FILENAME} unreadable: {exc}"
    for field, value in expected.items():
        if source.get(field) != value:
            return (
                f"post/{reports_dirname} is from a different final "
                f"({field} {_short(source.get(field))} != manifest {_short(value)})"
            )
    return None


def report_binding_status(run_directory, reports_dirname="reports"):
    """``"bound"`` or the reason string, for a CSV column."""
    reason = report_binding_reason(run_directory, reports_dirname)
    return REPORT_BOUND if reason is None else reason
