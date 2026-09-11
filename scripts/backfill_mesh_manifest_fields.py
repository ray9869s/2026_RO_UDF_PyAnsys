#!/usr/bin/env python3
"""Backfill absent keys on existing mesh manifests without read_mesh_manifest.

Two sources, kept separate:

- Required geometry keys come from ``geometry_parameters_for_geo_id``.
- ``domain_extent_x/y/z_m`` come from ``parse_mesh_metrics_from_log``
  (Fluent ``/mesh/check``). They are not in the registry. A missing log
  cannot be replaced by config arithmetic.

Only absent keys are filled. An existing JSON null is left alone: the
writer already ran and the parser found nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping

from ro.campaign_geo_ids import family_for_geo_id
from ro.campaign_geometry import geometry_parameters_for_geo_id
from ro.manifest import (
    MESH_MANIFEST_REQUIRED_FIELDS,
    ManifestError,
    upgrade_mesh_manifest_in_place,
    _validate_mesh_location,
    _validate_mesh_payload,
)
from ro.mesh_common import parse_mesh_metrics_from_log
from ro.paths import meshes_root

_PRESERVED_FIELDS = frozenset({
    "created_utc",
    "generator_version",
    "inlet_profile_G",
    "mesh_sha256",
})

# Measured /mesh/check lengths. Not registry fields.
DOMAIN_EXTENT_FIELDS = (
    "domain_extent_x_m",
    "domain_extent_y_m",
    "domain_extent_z_m",
)

EXTENT_STATUS_PRESENT = "present"
EXTENT_STATUS_FROM_LOG = "from-log"
EXTENT_STATUS_NO_MEASUREMENT = "NO-MEASUREMENT"


def _read_raw_manifest(mesh_directory: Path) -> dict[str, Any]:
    manifest_path = mesh_directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.json: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ManifestError(
            f"manifest.json must be a JSON object, got {type(payload).__name__}."
        )
    return payload


def _iter_mesh_directories(
    *,
    family: str | None,
    geo_id: str | None,
) -> Iterator[Path]:
    root = meshes_root()
    if not root.is_dir():
        return

    if geo_id is not None:
        family = family or family_for_geo_id(geo_id)
        geo_dir = root / family / geo_id
        if not geo_dir.is_dir():
            return
        for mesh_dir in sorted(geo_dir.iterdir()):
            if mesh_dir.is_dir() and (mesh_dir / "manifest.json").is_file():
                yield mesh_dir
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
            if mesh_dir.is_dir() and (mesh_dir / "manifest.json").is_file():
                yield mesh_dir


def mesh_log_path(mesh_directory: Path, payload: Mapping[str, Any]) -> Path:
    mesh_id = payload.get("mesh_id")
    if not isinstance(mesh_id, str) or not mesh_id:
        raise ManifestError(
            f"{mesh_directory}: manifest mesh_id must be a non-empty string "
            "to locate the mesh log."
        )
    return Path(mesh_directory) / f"mesh_log_{mesh_id}.txt"


def registry_additions_for_payload(
    mesh_directory: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Absent MESH_MANIFEST_REQUIRED_FIELDS from the geometry registry."""
    geo_id = payload.get("geo_id")
    if not isinstance(geo_id, str) or not geo_id:
        raise ManifestError(
            f"{mesh_directory}: manifest geo_id must be a non-empty string."
        )

    geometry = geometry_parameters_for_geo_id(geo_id)
    additions: dict[str, Any] = {}
    for field in MESH_MANIFEST_REQUIRED_FIELDS:
        if field in payload:
            continue
        if field in _PRESERVED_FIELDS:
            raise ManifestError(
                f"{mesh_directory}: required field {field!r} is missing but is "
                "preserved and cannot be invented by backfill."
            )
        if field not in geometry:
            raise ManifestError(
                f"{mesh_directory}: required field {field!r} is missing and "
                "cannot be sourced from geometry_parameters_for_geo_id."
            )
        additions[field] = geometry[field]
    return additions


def plan_extent_backfill_from_log(
    mesh_directory: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Plan domain_extent_*_m fills from the mesh log only.

    Absent keys only. Existing values, including JSON null, are left alone.
    Missing log while any extent key is absent is a hard refuse.
    A parse of None is NO-MEASUREMENT: report, do not write null.
    """
    absent = tuple(field for field in DOMAIN_EXTENT_FIELDS if field not in payload)
    log_path = mesh_log_path(mesh_directory, payload)
    log_exists = log_path.is_file()

    if absent and not log_exists:
        raise FileNotFoundError(
            f"{mesh_directory}: mesh log required to backfill measured "
            f"{', '.join(absent)}; missing {log_path}. There is no registry "
            "or config source for domain_extent_*_m."
        )

    parsed = {field: None for field in DOMAIN_EXTENT_FIELDS}
    if log_exists:
        metrics = parse_mesh_metrics_from_log(log_path)
        parsed = {field: metrics.get(field) for field in DOMAIN_EXTENT_FIELDS}

    usable = bool(log_exists) and all(
        parsed[field] is not None for field in DOMAIN_EXTENT_FIELDS
    )

    if not absent:
        return {
            "status": EXTENT_STATUS_PRESENT,
            "additions": {},
            "parsed": parsed,
            "usable_mesh_check": usable if log_exists else False,
            "log_exists": log_exists,
            "log_path": log_path,
            "absent": absent,
        }

    if not usable:
        return {
            "status": EXTENT_STATUS_NO_MEASUREMENT,
            "additions": {},
            "parsed": parsed,
            "usable_mesh_check": False,
            "log_exists": log_exists,
            "log_path": log_path,
            "absent": absent,
        }

    additions = {field: parsed[field] for field in absent}
    return {
        "status": EXTENT_STATUS_FROM_LOG,
        "additions": additions,
        "parsed": parsed,
        "usable_mesh_check": True,
        "log_exists": log_exists,
        "log_path": log_path,
        "absent": absent,
    }


def _maybe_validate(mesh_directory: Path, payload: Mapping[str, Any]) -> None:
    _validate_mesh_payload(payload)
    _validate_mesh_location(mesh_directory, payload)


def backfill_mesh_manifest(
    mesh_directory: Path,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Return {field: value} registry keys that would be added; write when apply=True.

    Extent keys are not handled here. Use ``backfill_mesh_leaf``.
    """
    mesh_directory = Path(mesh_directory)
    payload = _read_raw_manifest(mesh_directory)
    additions = registry_additions_for_payload(mesh_directory, payload)
    if not additions:
        return {}

    updated = dict(payload)
    updated.update(additions)
    if apply:
        upgrade_mesh_manifest_in_place(mesh_directory, updated)
    else:
        _maybe_validate(mesh_directory, updated)
    return additions


def backfill_mesh_leaf(
    mesh_directory: Path,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Registry required fields and log-measured extents, one write if needed."""
    mesh_directory = Path(mesh_directory)
    payload = _read_raw_manifest(mesh_directory)
    registry = registry_additions_for_payload(mesh_directory, payload)
    extent = plan_extent_backfill_from_log(mesh_directory, payload)

    updated = dict(payload)
    updated.update(registry)
    updated.update(extent["additions"])
    will_write = bool(registry or extent["additions"])
    if will_write:
        if apply:
            upgrade_mesh_manifest_in_place(mesh_directory, updated)
        else:
            _maybe_validate(mesh_directory, updated)

    return {"registry": registry, "extent": extent}


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill missing mesh-manifest keys. Required geometry fields "
            "come from the registry; domain_extent_*_m come from the mesh log "
            "/mesh/check parse. Dry-run by default. No --all."
        ),
    )
    parser.add_argument("--geo-id", help="Backfill one campaign geo_id.")
    parser.add_argument("--family", help="Backfill all mesh leaves under one family.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write manifest.json (default is dry-run only).",
    )
    return parser.parse_args(argv)


def _print_source_block(
    label: object,
    mode: str,
    source: str,
    additions: Mapping[str, Any],
    apply: bool,
) -> None:
    verb = "added" if apply else "would add"
    keys = ", ".join(sorted(additions))
    print(f"{label} [{mode}] {verb} from {source}: {keys}")
    for key in sorted(additions):
        print(f"  {key}: {additions[key]!r}")


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    if args.family is None and args.geo_id is None:
        raise SystemExit("Specify --geo-id and/or --family.")

    mesh_directories = list(
        _iter_mesh_directories(family=args.family, geo_id=args.geo_id)
    )
    if not mesh_directories:
        raise SystemExit("No mesh directories matched the selection.")

    failures: list[str] = []
    registry_changed = 0
    log_changed = 0
    no_measurement = 0
    usable_mesh_check = 0
    mode = "APPLY" if args.apply else "DRY-RUN"

    for mesh_directory in mesh_directories:
        label = mesh_directory.relative_to(meshes_root())
        try:
            result = backfill_mesh_leaf(mesh_directory, apply=args.apply)
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        registry = result["registry"]
        extent = result["extent"]
        if extent.get("usable_mesh_check"):
            usable_mesh_check += 1

        printed = False
        if registry:
            registry_changed += 1
            printed = True
            _print_source_block(label, mode, "registry", registry, args.apply)
        if extent["status"] == EXTENT_STATUS_FROM_LOG and extent["additions"]:
            log_changed += 1
            printed = True
            _print_source_block(
                label, mode, "log (/mesh/check)", extent["additions"], args.apply
            )
        elif extent["status"] == EXTENT_STATUS_NO_MEASUREMENT:
            no_measurement += 1
            printed = True
            print(
                f"{label} [{mode}] NO-MEASUREMENT: mesh log has no parseable "
                "/mesh/check domain extents; writing nothing for "
                "domain_extent_*_m"
            )
        if not printed:
            print(f"{label}: complete")

    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)

    print(
        f"\nSummary: {len(mesh_directories)} leaf(s), "
        f"{registry_changed} registry addition(s), "
        f"{log_changed} log addition(s), "
        f"{no_measurement} NO-MEASUREMENT, "
        f"{usable_mesh_check} usable /mesh/check, "
        f"{len(failures)} failure(s), "
        f"mode={'apply' if args.apply else 'dry-run'}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
