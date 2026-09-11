"""Skip extract/post only when the wide CSV is tied to the current solve.

The current solve is the R-02 pair on the run manifest
(``final_case_sha256``, ``final_data_sha256``, ``solver_attempt_id``).
Extract records those same field names in ``extract_source.json`` next to
``summary_metrics_wide.csv``. File existence and mtime are not enough:
a later copy can look fresh, and a re-solve leaves the old CSV in place.
Absent evidence is a re-extract, not a skip.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from ro.manifest import ManifestError, read_run_manifest
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
    sha256_file,
)

EXTRACT_SOURCE_FILENAME = "extract_source.json"

CsvValidator = Callable[[Path], tuple[bool, str]]


def extract_source_path(summary_wide_csv) -> Path:
    return Path(summary_wide_csv).parent / EXTRACT_SOURCE_FILENAME


def write_extract_source_record(
    reports_dir,
    record: Mapping[str, Any],
) -> Path:
    """Atomically write the extract source binding next to the wide CSV."""
    reports_dir = Path(reports_dir)
    path = reports_dir / EXTRACT_SOURCE_FILENAME
    payload = {
        FINAL_CASE_SHA256_FIELD: record.get(FINAL_CASE_SHA256_FIELD),
        FINAL_DATA_SHA256_FIELD: record.get(FINAL_DATA_SHA256_FIELD),
        SOLVER_ATTEMPT_ID_FIELD: record.get(SOLVER_ATTEMPT_ID_FIELD),
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=reports_dir,
            prefix=f".{EXTRACT_SOURCE_FILENAME}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        raise OSError(f"Could not write extract source record {path}: {exc}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return path


def read_extract_source_record(summary_wide_csv) -> dict[str, Any]:
    path = extract_source_path(summary_wide_csv)
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(
            f"extract source record must be a JSON object, got {type(payload).__name__}."
        )
    return payload


def extract_skip_block_reason(
    run_directory,
    summary_wide_csv,
    final_case_path,
    final_data_path,
    *,
    csv_validator: CsvValidator | None = None,
):
    """Return None when extract may be skipped; else why it must re-run.

    Missing R-02 hashes, a missing sidecar, or a sidecar that does not
    match the run manifest all force a re-extract. mtime is not consulted.
    """
    summary_wide_csv = Path(summary_wide_csv)
    if not summary_wide_csv.is_file():
        return f"summary_metrics_wide.csv was not found: {summary_wide_csv}"
    try:
        size = summary_wide_csv.stat().st_size
    except OSError as exc:
        return f"summary_metrics_wide.csv size could not be read ({summary_wide_csv}): {exc}"
    if size <= 0:
        return f"summary_metrics_wide.csv is empty: {summary_wide_csv}"
    if csv_validator is not None:
        ok, message = csv_validator(summary_wide_csv)
        if not ok:
            return f"summary_metrics_wide.csv failed validation: {message}"

    sidecar = extract_source_path(summary_wide_csv)
    if not sidecar.is_file():
        return f"extract source record was not found: {sidecar}"
    try:
        source = read_extract_source_record(summary_wide_csv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"extract source record not usable for skip: {exc}"

    try:
        payload = read_run_manifest(run_directory)
    except (OSError, ManifestError) as exc:
        return f"run manifest not usable for extract skip: {exc}"

    attempt_id = payload.get(SOLVER_ATTEMPT_ID_FIELD)
    case_sha = payload.get(FINAL_CASE_SHA256_FIELD)
    data_sha = payload.get(FINAL_DATA_SHA256_FIELD)
    if not attempt_id:
        return "missing solver_attempt_id"
    if not case_sha or not data_sha:
        return "missing current-attempt final artifact hashes"

    final_case_path = Path(final_case_path)
    final_data_path = Path(final_data_path)
    if not final_case_path.is_file():
        return f"final case file was not found: {final_case_path}"
    if not final_data_path.is_file():
        return f"final data file was not found: {final_data_path}"
    try:
        if sha256_file(final_case_path) != case_sha:
            return "final case sha256 does not match run manifest"
        if sha256_file(final_data_path) != data_sha:
            return "final data sha256 does not match run manifest"
    except OSError as exc:
        return f"final artifact not readable for extract skip: {exc}"

    if source.get(FINAL_CASE_SHA256_FIELD) != case_sha:
        return "extract source final_case_sha256 does not match run manifest"
    if source.get(FINAL_DATA_SHA256_FIELD) != data_sha:
        return "extract source final_data_sha256 does not match run manifest"
    if source.get(SOLVER_ATTEMPT_ID_FIELD) != attempt_id:
        return "extract source solver_attempt_id does not match run manifest"
    return None


def inspect_extract_skip_leaf(
    *,
    family: str,
    geo_id: str,
    mesh_id: str,
    run_id: str,
    run_directory,
    summary_wide_csv,
    final_case_path,
    final_data_path,
    csv_validator: CsvValidator | None = None,
) -> dict[str, Any]:
    """Skip evidence for ``--report-skip``. ``skip_decision`` is SKIP or RUN."""
    run_directory = Path(run_directory)
    summary_wide_csv = Path(summary_wide_csv)
    sidecar = extract_source_path(summary_wide_csv)
    csv_exists = summary_wide_csv.is_file()
    csv_size = summary_wide_csv.stat().st_size if csv_exists else 0
    sidecar_exists = sidecar.is_file()
    manifest_has_hashes = False
    try:
        payload = read_run_manifest(run_directory)
        manifest_has_hashes = bool(
            payload.get(SOLVER_ATTEMPT_ID_FIELD)
            and payload.get(FINAL_CASE_SHA256_FIELD)
            and payload.get(FINAL_DATA_SHA256_FIELD)
        )
    except (OSError, ManifestError):
        payload = None
    skip_block = extract_skip_block_reason(
        run_directory,
        summary_wide_csv,
        final_case_path,
        final_data_path,
        csv_validator=csv_validator,
    )
    return {
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
        "leaf": f"{family}/{geo_id}/{mesh_id}/{run_id}",
        "csv_exists": csv_exists,
        "csv_size": csv_size,
        "sidecar_exists": sidecar_exists,
        "manifest_has_hashes": manifest_has_hashes,
        "skip_block": skip_block,
        "skip_decision": "SKIP" if skip_block is None else "RUN",
    }
