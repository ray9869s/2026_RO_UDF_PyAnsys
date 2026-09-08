#!/usr/bin/env python3
"""Backfill absent keys on existing run manifests without read_run_manifest.

Older run leaves may lack keys added to RUN_MANIFEST_REQUIRED_FIELDS after
the run was written. This script json.load's the raw payload, adds only
missing geometry fields from geometry_parameters_for_geo_id(geo_id), then
validates and writes atomically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from ro.campaign_geometry import geometry_parameters_for_geo_id
from ro.campaign_geo_ids import family_for_geo_id
from ro.manifest import (
    RUN_MANIFEST_REQUIRED_FIELDS,
    ManifestError,
    _GEOMETRY_FIELDS,
    upgrade_run_manifest_in_place,
)
from ro.paths import runs_root

_PRESERVED_FIELDS = frozenset({
    "created_utc",
    "mesh_sha256",
    "u_mean_ms",
    "stop_reason",
    "schema_version",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "p_gauge_pa",
    "u_target_ms",
    "inlet_bc_type",
    "udf_version",
    "analytic_cwall",
    "solver_settings",
    "u_mean_source_mesh_id",
})

_GEOMETRY_FIELD_SET = frozenset(_GEOMETRY_FIELDS)


def _read_raw_manifest(run_directory: Path) -> dict[str, Any]:
    manifest_path = run_directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ManifestError(
            f"manifest.json must be a JSON object, got {type(payload).__name__}."
        )
    return payload


def _iter_run_directories(
    *,
    family: str | None,
    geo_id: str | None,
) -> Iterator[Path]:
    root = runs_root()
    if not root.is_dir():
        return

    if geo_id is not None:
        family = family or family_for_geo_id(geo_id)
        geo_dir = root / family / geo_id
        if not geo_dir.is_dir():
            return
        for mesh_dir in sorted(geo_dir.iterdir()):
            if not mesh_dir.is_dir():
                continue
            for run_dir in sorted(mesh_dir.iterdir()):
                if run_dir.is_dir() and (run_dir / "manifest.json").is_file():
                    yield run_dir
        return

    if family is None:
        return

    family_dir = root / family
    if not family_dir.is_dir():
        return
    for geo_dir in sorted(family_dir.iterdir()):
        if not geo_dir.is_dir():
            continue
        for mesh_dir in sorted(geo_dir.iterdir()):
            if not mesh_dir.is_dir():
                continue
            for run_dir in sorted(mesh_dir.iterdir()):
                if run_dir.is_dir() and (run_dir / "manifest.json").is_file():
                    yield run_dir


def backfill_run_manifest(
    run_directory: Path,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Return {field: value} keys that would be added; write when apply=True."""
    run_directory = Path(run_directory)
    payload = _read_raw_manifest(run_directory)

    geo_id = payload.get("geo_id")
    if not isinstance(geo_id, str) or not geo_id:
        raise ManifestError(
            f"{run_directory}: manifest geo_id must be a non-empty string."
        )

    geometry = geometry_parameters_for_geo_id(geo_id)
    additions: dict[str, Any] = {}
    for field in RUN_MANIFEST_REQUIRED_FIELDS:
        if field in payload:
            continue
        if field in _PRESERVED_FIELDS:
            raise ManifestError(
                f"{run_directory}: required field {field!r} is missing but is "
                "preserved and cannot be invented by backfill."
            )
        if field not in _GEOMETRY_FIELD_SET:
            raise ManifestError(
                f"{run_directory}: required field {field!r} is missing and "
                "is not a geometry registry field."
            )
        if field not in geometry:
            raise ManifestError(
                f"{run_directory}: required field {field!r} is missing and "
                "cannot be sourced from geometry_parameters_for_geo_id."
            )
        additions[field] = geometry[field]

    if not additions:
        return {}

    updated = dict(payload)
    updated.update(additions)

    if apply:
        upgrade_run_manifest_in_place(run_directory, updated)
    else:
        from ro.manifest import _validate_run_location, _validate_run_payload

        _validate_run_payload(updated)
        _validate_run_location(run_directory, updated)
    return additions


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill missing RUN_MANIFEST_REQUIRED_FIELDS from the geometry "
            "registry (dry-run by default)."
        ),
    )
    parser.add_argument("--geo-id", help="Backfill all run leaves under one geo_id.")
    parser.add_argument("--family", help="Backfill all run leaves under one family.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write manifest.json (default is dry-run only).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    if args.family is None and args.geo_id is None:
        raise SystemExit("Specify --geo-id and/or --family.")

    run_directories = list(
        _iter_run_directories(family=args.family, geo_id=args.geo_id)
    )
    if not run_directories:
        raise SystemExit("No run directories matched the selection.")

    failures: list[str] = []
    changed = 0
    for run_directory in run_directories:
        label = run_directory.relative_to(runs_root())
        try:
            additions = backfill_run_manifest(
                run_directory,
                apply=args.apply,
            )
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        if not additions:
            print(f"{label}: complete")
            continue

        changed += 1
        mode = "APPLY" if args.apply else "DRY-RUN"
        verb = "added" if args.apply else "would add"
        keys = ", ".join(sorted(additions))
        print(f"{label} [{mode}] {verb}: {keys}")
        for key in sorted(additions):
            print(f"  {key}: {additions[key]!r}")

    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)

    print(
        f"\nSummary: {len(run_directories)} leaf(s), "
        f"{changed} with additions, {len(failures)} failure(s), "
        f"mode={'apply' if args.apply else 'dry-run'}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
