#!/usr/bin/env python3
"""Report R-10 replace-log identity on existing run leaves. No Fluent.

Reads solver_mesh_replace_log_{run_id}.txt only (current attempt, not
__attemptN archives). Missing log is NOT_CHECKED, not PASS. A quoted msh
path must be meshes/{family}/{geo_id}/{mesh_id}/. This is not byte identity.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ro.domain_layout import (
    current_solver_replace_log_path,
    inspect_replace_log_identity,
)
from ro.manifest import ManifestError, iter_run_manifests
from ro.paths import data_root, mesh_dir, runs_root


def main(argv=None) -> int:
    del argv
    data_root()
    print("REPLACE LOG IDENTITY REPORT (no Fluent)")
    print(f"runs_root={runs_root()}")
    if not runs_root().is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {runs_root()}")

    counts = {"NOT_CHECKED": 0, "PASS": 0, "REJECT": 0, "UNREADABLE": 0}
    skipped: list[tuple[Path, str]] = []
    n = 0
    for manifest_path, payload in iter_run_manifests(
        skip_invalid=True,
        skipped_manifests=skipped,
    ):
        n += 1
        leaf = (
            f"{payload['family']}/{payload['geo_id']}/"
            f"{payload['mesh_id']}/{payload['run_id']}"
        )
        log_path = current_solver_replace_log_path(
            manifest_path.parent, payload["run_id"]
        )
        try:
            mesh_directory = mesh_dir(
                payload["family"], payload["geo_id"], payload["mesh_id"]
            )
            status, reason = inspect_replace_log_identity(
                log_path=log_path,
                mesh_directory=mesh_directory,
            )
        except (OSError, ManifestError, KeyError) as exc:
            status, reason = "UNREADABLE", str(exc)
        counts[status] = counts.get(status, 0) + 1
        extra = f"  {reason}" if reason else ""
        print(f"run   {status}  {leaf}{extra}")

    for manifest_path, error in skipped:
        counts["UNREADABLE"] += 1
        print(f"run   UNREADABLE  {manifest_path}: {error}")

    print(
        "Summary: "
        f"run {n + len(skipped)} "
        f"not_checked={counts['NOT_CHECKED']} pass={counts['PASS']} "
        f"reject={counts['REJECT']} unread={counts['UNREADABLE']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
