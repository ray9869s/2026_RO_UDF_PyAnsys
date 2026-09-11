#!/usr/bin/env python3
"""Report R-10 spacer wall zones from manifests and solver stdout. No Fluent.

Registry vs manifest is not the Fluent gate. The Fluent comparison needs
``Detected spacer wall zones:`` and ``All boundary zones:`` from solver
Python prints (attempt logs / stdout), not the mesh-import zone *count*.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ro.campaign_geo_ids import CAMPAIGN_GEO_IDS
from ro.campaign_geometry import geometry_parameters_for_geo_id
from ro.manifest import ManifestError, iter_run_manifests, read_mesh_manifest
from ro.manifest_validation import inspect_solver_log_spacer_zones
from ro.paths import data_root, mesh_dir, meshes_root, runs_root


def _run_log_text(run_directory: Path) -> str | None:
    chunks = []
    for pattern in ("*solver_attempt*.log", "solver_log_*.txt"):
        for path in sorted(run_directory.glob(pattern)):
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    if not chunks:
        return None
    return "\n".join(chunks)


def _label(ok: bool) -> str:
    return "PASS" if ok else "REJECT"


def main(argv=None) -> int:
    del argv
    data_root()
    print("SPACER WALL ZONE REPORT (no live Fluent)")
    print(f"meshes_root={meshes_root()}")
    print(f"runs_root={runs_root()}")

    registry_reject = 0
    mesh_n = 0
    mesh_root = meshes_root()
    if not mesh_root.is_dir():
        raise NotADirectoryError(f"meshes_root is not a directory: {mesh_root}")
    for manifest_path in sorted(mesh_root.rglob("manifest.json")):
        mesh_n += 1
        try:
            payload = read_mesh_manifest(manifest_path.parent)
        except (OSError, ManifestError) as exc:
            print(f"mesh  UNREADABLE  {manifest_path}: {exc}")
            continue
        leaf = f"{payload['family']}/{payload['geo_id']}/{payload['mesh_id']}"
        if payload["geo_id"] not in CAMPAIGN_GEO_IDS:
            print(f"mesh  registry=N/A  extra  {leaf}")
            continue
        expected = geometry_parameters_for_geo_id(payload["geo_id"])[
            "spacer_wall_zones"
        ]
        declared = payload.get("spacer_wall_zones")
        ok = list(declared or []) == list(expected)
        if not ok:
            registry_reject += 1
        print(
            f"mesh  registry={_label(ok)}  "
            f"declared={list(declared or [])}  registry={list(expected)}  {leaf}"
        )

    if not runs_root().is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {runs_root()}")

    log_counts = {
        "NO_LOG": 0,
        "NO_LINE": 0,
        "PASS": 0,
        "NAME_ONLY": 0,
        "REJECT": 0,
        "UNREADABLE": 0,
    }
    skipped: list[tuple[Path, str]] = []
    run_n = 0
    for manifest_path, payload in iter_run_manifests(
        skip_invalid=True,
        skipped_manifests=skipped,
    ):
        run_n += 1
        leaf = (
            f"{payload['family']}/{payload['geo_id']}/"
            f"{payload['mesh_id']}/{payload['run_id']}"
        )
        text = _run_log_text(manifest_path.parent)
        if text is None:
            log_counts["NO_LOG"] += 1
            print(f"run   log=NO_LOG  {leaf}")
            continue
        try:
            mesh_payload = read_mesh_manifest(
                mesh_dir(payload["family"], payload["geo_id"], payload["mesh_id"])
            )
            declared = mesh_payload.get("spacer_wall_zones") or []
            status, reason = inspect_solver_log_spacer_zones(
                text,
                declared_zones=declared,
                geo_id=payload["geo_id"],
            )
        except (OSError, ManifestError, KeyError) as exc:
            status, reason = "UNREADABLE", str(exc)
        log_counts[status] = log_counts.get(status, 0) + 1
        extra = f"  {reason}" if reason else ""
        print(f"run   log={status}  {leaf}{extra}")

    for manifest_path, error in skipped:
        log_counts["UNREADABLE"] += 1
        print(f"run   log=UNREADABLE  {manifest_path}: {error}")

    print(
        "Summary: "
        f"mesh {mesh_n} registry_reject={registry_reject}; "
        f"run {run_n + len(skipped)} "
        + " ".join(f"{key}={log_counts[key]}" for key in log_counts)
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
