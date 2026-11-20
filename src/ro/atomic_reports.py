"""Stage an extract in ``post/<dir>.tmp_<id>`` and swap it in at the end.

A report directory is only replaced after every gate has passed and the
run manifest was updated. A failed or killed extract leaves its outputs
in the staging directory for inspection; the previous report directory
is never overwritten with half-written CSVs. The replaced directory is
renamed to ``<dir>_prev_<UTC>``, never deleted.

At the start of an extract of the canonical final, the target report
directory's ``extract_source.json`` is renamed aside
(``extract_source.invalidated_<UTC>.json``) and the run manifest loses
its ``convergence_quality``. A crash before the swap therefore leaves no
report that still claims to describe the current final, and no PASS.

``publish_extract`` is the end of the transaction, in this order: hash
the final pair, write the sidecar into the staged directory (with a
``report_generation`` id), swap the staged directory in, and only then
write the gate to the manifest together with
``convergence_quality_report_generation``. A failure at any step leaves
the previous reports in place (a failed staging rename puts the archived
directory back) and the manifest gate cleared.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ro.extract_skip import EXTRACT_SOURCE_FILENAME, write_extract_source_record
from ro.manifest import update_run_manifest_fields
from ro.paths import refuse_production_root
# report_generation (sidecar) / convergence_quality_report_generation (manifest)
from ro.report_binding import GATE_GENERATION_FIELD, REPORT_GENERATION_FIELD
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    sha256_file,
)

STAGING_MARKER = ".tmp_"
INVALIDATED_SIDECAR_PREFIX = "extract_source.invalidated_"


def utc_stamp(now=None) -> str:
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def staging_report_path(report_path, attempt_id=None, *, now=None) -> Path:
    """``post/<dir>.tmp_<id>``; ``<id>`` defaults to ``<UTC>_<pid>``."""
    report_path = Path(report_path)
    refuse_production_root(report_path)
    if attempt_id is None:
        attempt_id = f"{utc_stamp(now)}_{os.getpid()}"
    return report_path.parent / f"{report_path.name}{STAGING_MARKER}{attempt_id}"


def is_staging_dirname(name: str) -> bool:
    return STAGING_MARKER in str(name)


def is_staging_path(path) -> bool:
    """True for anything inside a ``post/<dir>.tmp_<id>`` staging dir.

    A staging dir is an unpublished (possibly killed) extract. Reporting
    and scans skip it; prune it by hand after inspection (AGENTS.md).
    """
    return any(is_staging_dirname(part) for part in Path(path).parts)


def begin_report_extract(case_path, report_path, *, bind_manifest=True):
    """Invalidate the target's binding before any Fluent work.

    Removes only ``<report_path>/extract_source.json`` (the CSVs stay for
    inspection). With ``bind_manifest`` the run manifest's
    ``convergence_quality`` is cleared to ``None``. Returns the list of
    actions taken, for logging.
    """
    refuse_production_root(report_path)
    actions = []
    aside = invalidate_sidecar(report_path)
    if aside is not None:
        actions.append(f"moved {EXTRACT_SOURCE_FILENAME} aside to {aside}")
    if bind_manifest:
        clear_manifest_gate(case_path)
        actions.append("cleared manifest convergence_quality")
    return actions


def invalidate_sidecar(report_path, *, now=None):
    """Rename ``extract_source.json`` aside. Never deletes. Returns the new path."""
    sidecar = Path(report_path) / EXTRACT_SOURCE_FILENAME
    if not sidecar.is_file():
        return None
    stamp = utc_stamp(now)
    aside = sidecar.with_name(f"{INVALIDATED_SIDECAR_PREFIX}{stamp}.json")
    counter = 1
    while aside.exists():
        aside = sidecar.with_name(f"{INVALIDATED_SIDECAR_PREFIX}{stamp}_{counter}.json")
        counter += 1
    sidecar.rename(aside)
    return aside


def clear_manifest_gate(case_path):
    """Set ``convergence_quality`` and its report generation to None.

    Plain JSON rewrite (temp file + ``os.replace``) so it also works on a
    manifest that an older schema would not validate. A missing manifest
    raises ``FileNotFoundError``.
    """
    path = Path(case_path) / "manifest.json"
    refuse_production_root(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run manifest is not a JSON object: {path}")
    payload["convergence_quality"] = None
    payload[GATE_GENERATION_FIELD] = None
    write_manifest_json(path, payload)


def write_manifest_json(path, payload) -> Path:
    """Plain JSON rewrite of a manifest: temp file in the same dir + ``os.replace``."""
    path = Path(path)
    refuse_production_root(path)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".manifest.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
    return path


def report_generation_id(staging) -> str:
    """The attempt id after ``.tmp_`` in a staging directory name."""
    name = Path(staging).name
    if STAGING_MARKER not in name:
        raise ValueError(f"not a staging report directory: {staging}")
    return name.split(STAGING_MARKER, 1)[1]


def publish_extract(
    *,
    case_path,
    staging,
    final,
    source_case_file,
    source_data_file,
    sidecar_fields=None,
    gate_payload=None,
    bind_manifest=True,
    hasher=sha256_file,
    now=None,
):
    """Hash, write the staged sidecar, promote, then publish the gate.

    ``sidecar_fields`` adds provenance to the sidecar. With
    ``bind_manifest`` false (snapshot extract) the sidecar records
    ``snapshot_case_sha256`` / ``snapshot_data_sha256`` instead of the
    final-pair fields and the manifest is never written. Returns
    ``(archive_or_None, generation)``. Any exception propagates after
    the manifest gate has been cleared again.
    """
    generation = report_generation_id(staging)
    try:
        case_hash = hasher(source_case_file)
        data_hash = hasher(source_data_file)
        if bind_manifest:
            record = {FINAL_CASE_SHA256_FIELD: case_hash, FINAL_DATA_SHA256_FIELD: data_hash}
        else:
            record = {"snapshot_case_sha256": case_hash, "snapshot_data_sha256": data_hash}
        record.update(sidecar_fields or {})
        record[REPORT_GENERATION_FIELD] = generation
        write_extract_source_record(staging, record)
        archive = promote_staged_reports(staging, final, now=now)
        if bind_manifest:
            update_run_manifest_fields(
                case_path, {**(gate_payload or {}), GATE_GENERATION_FIELD: generation}
            )
    except BaseException:
        if bind_manifest:
            try:
                clear_manifest_gate(case_path)
            except Exception as exc:  # noqa: BLE001 - report, keep the first error
                print(f"WARNING: could not clear the manifest gate after a failure: {exc}")
        raise
    return archive, generation


def promote_staged_reports(staging, final, *, now=None):
    """Swap ``staging`` into ``final``. Returns the archive path or None.

    An existing ``final`` is renamed to ``<final>_prev_<UTC>`` first. An
    existing archive of that name is refused, never replaced.
    """
    staging, final = Path(staging), Path(final)
    refuse_production_root(final)
    if not staging.is_dir():
        raise FileNotFoundError(f"staged report directory is missing: {staging}")
    archive = None
    if final.exists():
        if not final.is_dir():
            raise RuntimeError(f"report path is not a directory: {final}")
        archive = final.parent / f"{final.name}_prev_{utc_stamp(now)}"
        if archive.exists():
            raise FileExistsError(f"Refusing to replace report archive: {archive}")
        final.rename(archive)
    try:
        staging.rename(final)
    except BaseException:
        if archive is not None and not final.exists():
            archive.rename(final)
        raise
    return archive
