#!/usr/bin/env python3
"""Migrate mesh/run manifests from schema v1 to v2 (idempotent, backed up)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from ro.campaign_geometry import (
    merge_geometry_into_mesh_manifest,
    merge_geometry_into_run_manifest,
)
from ro.manifest import (
    MANIFEST_SCHEMA_VERSION,
    MESH_MANIFEST_REQUIRED_FIELDS,
    RUN_MANIFEST_REQUIRED_FIELDS,
    ManifestError,
    upgrade_mesh_manifest_in_place,
    upgrade_run_manifest_in_place,
)
from ro.paths import meshes_root, runs_root


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_file(path: Path, backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    destination = backup_root / f"{path.name}.{_utc_stamp()}.bak"
    shutil.copy2(path, destination)
    return destination


def _is_v2_complete(payload: dict, required: tuple[str, ...]) -> bool:
    return (
        payload.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and all(field in payload for field in required)
    )


def migrate_mesh_manifest(path: Path, *, backup_root: Path, dry_run: bool) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if _is_v2_complete(raw, MESH_MANIFEST_REQUIRED_FIELDS):
        return False
    if raw.get("schema_version") not in (1, MANIFEST_SCHEMA_VERSION):
        raise ManifestError(
            f"Unsupported mesh schema_version {raw.get('schema_version')!r} in {path}."
        )
    geo_id = raw["geo_id"]
    migrated = merge_geometry_into_mesh_manifest(raw, geo_id)
    migrated["schema_version"] = MANIFEST_SCHEMA_VERSION
    if dry_run:
        print(f"[dry-run] would migrate mesh manifest {path}")
        return True
    _backup_file(path, backup_root)
    upgrade_mesh_manifest_in_place(path.parent, migrated)
    print(f"Migrated mesh manifest {path}")
    return True


def migrate_run_manifest(path: Path, *, backup_root: Path, dry_run: bool) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if _is_v2_complete(raw, RUN_MANIFEST_REQUIRED_FIELDS):
        return False
    if raw.get("schema_version") not in (1, MANIFEST_SCHEMA_VERSION):
        raise ManifestError(
            f"Unsupported run schema_version {raw.get('schema_version')!r} in {path}."
        )
    geo_id = raw["geo_id"]
    mesh_id = raw["mesh_id"]
    migrated = merge_geometry_into_run_manifest(raw, geo_id, mesh_id=mesh_id)
    migrated["schema_version"] = MANIFEST_SCHEMA_VERSION
    if dry_run:
        print(f"[dry-run] would migrate run manifest {path}")
        return True
    _backup_file(path, backup_root)
    upgrade_run_manifest_in_place(path.parent, migrated)
    print(f"Migrated run manifest {path}")
    return True


def migrate_tree(
    *,
    meshes: bool,
    runs: bool,
    dry_run: bool,
    backup_root: Path,
) -> tuple[int, int]:
    mesh_count = 0
    run_count = 0
    if meshes:
        for manifest_path in sorted(meshes_root().rglob("manifest.json")):
            if migrate_mesh_manifest(manifest_path, backup_root=backup_root, dry_run=dry_run):
                mesh_count += 1
    if runs:
        for manifest_path in sorted(runs_root().rglob("manifest.json")):
            if migrate_run_manifest(manifest_path, backup_root=backup_root, dry_run=dry_run):
                run_count += 1
    return mesh_count, run_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing manifests.",
    )
    parser.add_argument(
        "--meshes-only",
        action="store_true",
        help="Migrate mesh manifests only.",
    )
    parser.add_argument(
        "--runs-only",
        action="store_true",
        help="Migrate run manifests only.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Backup directory (default: RO_DATA_ROOT/manifest_migration_backups/<stamp>).",
    )
    args = parser.parse_args(argv)

    migrate_meshes = not args.runs_only
    migrate_runs = not args.meshes_only
    stamp = _utc_stamp()
    backup_root = args.backup_dir
    if backup_root is None:
        backup_root = meshes_root().parent / "manifest_migration_backups" / stamp

    mesh_count, run_count = migrate_tree(
        meshes=migrate_meshes,
        runs=migrate_runs,
        dry_run=args.dry_run,
        backup_root=backup_root,
    )
    print(
        f"Done: {mesh_count} mesh manifest(s), {run_count} run manifest(s) "
        f"{'would be ' if args.dry_run else ''}migrated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
