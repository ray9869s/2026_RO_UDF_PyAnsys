"""Stage an extract in ``post/<dir>.tmp_<id>`` and swap it in at the end.

A report directory is only replaced after every gate has passed and the
run manifest was updated. A failed or killed extract leaves its outputs
in the staging directory for inspection; the previous report directory
is never overwritten with half-written CSVs. The replaced directory is
renamed to ``<dir>_prev_<UTC>``, never deleted.

At the start of an extract of the canonical final, the target report
directory loses its ``extract_source.json`` and the run manifest loses
its ``convergence_quality``. A crash before the swap therefore leaves no
report that still claims to describe the current final, and no PASS.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ro.extract_skip import EXTRACT_SOURCE_FILENAME
from ro.manifest import update_run_manifest_fields

STAGING_MARKER = ".tmp_"


def utc_stamp(now=None) -> str:
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def staging_report_path(report_path, attempt_id=None, *, now=None) -> Path:
    """``post/<dir>.tmp_<id>``; ``<id>`` defaults to ``<UTC>_<pid>``."""
    report_path = Path(report_path)
    if attempt_id is None:
        attempt_id = f"{utc_stamp(now)}_{os.getpid()}"
    return report_path.parent / f"{report_path.name}{STAGING_MARKER}{attempt_id}"


def is_staging_dirname(name: str) -> bool:
    return STAGING_MARKER in str(name)


def begin_report_extract(case_path, report_path, *, bind_manifest=True):
    """Invalidate the target's binding before any Fluent work.

    Removes only ``<report_path>/extract_source.json`` (the CSVs stay for
    inspection). With ``bind_manifest`` the run manifest's
    ``convergence_quality`` is cleared to ``None``. Returns the list of
    actions taken, for logging.
    """
    actions = []
    sidecar = Path(report_path) / EXTRACT_SOURCE_FILENAME
    if sidecar.is_file():
        sidecar.unlink()
        actions.append(f"removed {sidecar}")
    if bind_manifest:
        update_run_manifest_fields(case_path, {"convergence_quality": None})
        actions.append("cleared manifest convergence_quality")
    return actions


def promote_staged_reports(staging, final, *, now=None):
    """Swap ``staging`` into ``final``. Returns the archive path or None.

    An existing ``final`` is renamed to ``<final>_prev_<UTC>`` first. An
    existing archive of that name is refused, never replaced.
    """
    staging, final = Path(staging), Path(final)
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
    staging.rename(final)
    return archive
