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

# Sidecar / manifest fields written by ro.atomic_reports.publish_extract.
REPORT_GENERATION_FIELD = "report_generation"
GATE_GENERATION_FIELD = "convergence_quality_report_generation"

REPORT_BOUND = "bound"
# v2 baseline only: reports that exist but carry no hash evidence either way.
REPORT_UNVERIFIED = "unverified"
# Written by scripts/host/pending_checks.py when a smoke restore fails.
BROKEN_MARKER = "BROKEN"


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
    if (run_directory / BROKEN_MARKER).exists():
        return f"leaf has a {BROKEN_MARKER} marker (failed restore)"
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
    return gate_generation_reason(manifest, source)


def gate_generation_reason(manifest, source):
    """None when the manifest gate belongs to these reports.

    Reports written by ``ro.atomic_reports.publish_extract`` carry a
    ``report_generation``; the gate written with them carries the same id
    in ``convergence_quality_report_generation``. Older reports have
    neither and are not checked. A gate without an id next to reports
    with one, or a different id, is from another extract.
    """
    reports_generation = source.get(REPORT_GENERATION_FIELD)
    gate_generation = manifest.get(GATE_GENERATION_FIELD)
    if reports_generation is None and gate_generation is None:
        return None
    if manifest.get("convergence_quality") in (None, "") and gate_generation is None:
        return None  # no gate published (yet); the reports themselves are bound
    if reports_generation != gate_generation:
        return (
            f"manifest gate is from report generation {gate_generation or 'missing'}, "
            f"reports are generation {reports_generation or 'missing'}"
        )
    return None


def report_binding_status(run_directory, reports_dirname="reports"):
    """``"bound"`` or the reason string, for a CSV column."""
    reason = report_binding_reason(run_directory, reports_dirname)
    return REPORT_BOUND if reason is None else reason


def report_binding_unverifiable(run_directory, reports_dirname="reports"):
    """True when binding fails only for lack of hash evidence.

    That is: the reports and a readable manifest exist, no BROKEN marker,
    and either the manifest has no final hashes, the sidecar is missing,
    or the sidecar omits a hash field. A sidecar hash that differs from the
    manifest is evidence of a different final and is never unverifiable.
    Only the frozen v2 baseline may be reported on this basis.
    """
    run_directory = Path(run_directory)
    if report_binding_reason(run_directory, reports_dirname) is None:
        return False
    if (run_directory / BROKEN_MARKER).exists():
        return False
    reports = run_directory / "post" / reports_dirname
    if not (reports / "summary_metrics_wide.csv").is_file():
        return False
    try:
        manifest = _read_json(run_directory / "manifest.json")
    except (OSError, ValueError):
        return False
    expected = {
        FINAL_CASE_SHA256_FIELD: manifest.get(FINAL_CASE_SHA256_FIELD),
        FINAL_DATA_SHA256_FIELD: manifest.get(FINAL_DATA_SHA256_FIELD),
    }
    if not all(expected.values()):
        return True
    sidecar = reports / EXTRACT_SOURCE_FILENAME
    if not sidecar.is_file():
        return True
    try:
        source = _read_json(sidecar)
    except (OSError, ValueError):
        return False
    conflicts = [
        field for field, value in expected.items()
        if source.get(field) and source.get(field) != value
    ]
    return not conflicts
