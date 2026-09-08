#!/usr/bin/env python3
"""Rebuild mesh manifest.json from run config, mesh log, and hashed mesh file.

No Fluent session. Uses the same payload builder as the meshing worker.
Rebuild cfg overrides come from the stored manifest (not batch_config) so
values written at mesh time are not replaced by later config drift.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Collection, Iterator

from ro.campaign_geo_ids import CAMPAIGN_GEO_IDS
from ro.manifest import (
    ManifestError,
    read_mesh_manifest,
    upgrade_mesh_manifest_in_place,
)
from ro.mesh_common import parse_mesh_metrics_from_log, parse_meshing_input_summary
from ro.paths import meshes_root, project_root

SCRIPT_DIR = Path(__file__).resolve().parent
RUN_CONFIG_PATH = project_root() / "configs" / "run_config.py"
MESHING_SCRIPT_PATH = SCRIPT_DIR / "meshing_code_260616.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_meshing_helpers():
    return _load_module("meshing_manifest_rebuild_helpers", MESHING_SCRIPT_PATH)


def _manifest_layout_overrides(manifest: dict[str, Any]) -> dict[str, Any]:
    """Map stored manifest layout knobs back to run_config override names."""
    return {
        "family": manifest["family"],
        "geo_id": manifest["geo_id"],
        "mesh_id": manifest["mesh_id"],
        "spacing_code": manifest["spacing_code"],
        "attack_angle_deg": manifest["attack_angle_deg"],
        "filament_d_m": manifest["filament_d_m"],
        "bridge_radius_m": manifest["bridge_radius_m"],
        "overlap_m": manifest["overlap_m"],
        "n_active_cells": manifest["n_active_cells"],
        "n_buffer_in": manifest["n_buffer_in"],
        "n_buffer_out": manifest["n_buffer_out"],
        "cell_length_x_m": manifest["cell_length_x_m"],
        "buffer_length_in_m": manifest["buffer_length_in_m"],
        "buffer_length_out_m": manifest["buffer_length_out_m"],
        "n_lead_excluded": manifest["n_lead_excluded"],
        "n_trail_excluded": manifest["n_trail_excluded"],
        "periodic_shift_y": float(manifest["periodic_shift_y_m"]) * 1.0e3,
        "active_membrane_wall_labels": list(manifest["membrane_wall_base_names"]),
        "buffer_wall_labels": list(manifest["buffer_wall_base_names"]),
        "wall_spacer_labels": list(manifest["spacer_wall_zones"]),
        "m_max": manifest["max_size_mm"],
        "m_min": manifest["min_size_mm"],
        "m_cpg": manifest["cpg"],
        "bl_layers": manifest["bl"],
        "peel_layers": manifest["peel"],
    }


def _build_cfg_overrides(
    mesh_directory: Path,
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild run_config overrides from manifest + optional mesh-log summary."""
    overrides = _manifest_layout_overrides(existing)

    mesh_log_path = mesh_directory / f"mesh_log_{existing['mesh_id']}.txt"
    parsed = parse_meshing_input_summary(
        mesh_log_path.read_text(encoding="utf-8", errors="ignore")
    )
    for key, value in parsed.items():
        if value is not None:
            overrides[key] = value
    return overrides


def _load_cfg(overrides: dict[str, Any]):
    cfg = _load_module("active_run_config_rebuild", RUN_CONFIG_PATH)
    cfg.apply_run_config_overrides(cfg, overrides)
    return cfg


def _mesh_paths(mesh_directory: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    geo_id = manifest["geo_id"]
    mesh_id = manifest["mesh_id"]
    mesh_file = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
    mesh_log = mesh_directory / f"mesh_log_{mesh_id}.txt"
    return mesh_file, mesh_log


def _payload_diff(
    existing: dict[str, Any],
    rebuilt: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    keys = sorted(set(existing) | set(rebuilt))
    diff: dict[str, tuple[Any, Any]] = {}
    for key in keys:
        old = existing.get(key, "<missing>")
        new = rebuilt.get(key, "<missing>")
        if old != new:
            diff[key] = (old, new)
    return diff


def enforce_diff_allowlist(
    diff: dict[str, tuple[Any, Any]],
    allowed_fields: Collection[str],
) -> None:
    """Abort when a diff touches manifest keys outside the CLI allowlist."""
    if not diff:
        return
    if not allowed_fields:
        raise ManifestError(
            "Diff is non-empty but no --allow-field names were supplied."
        )
    allowed = set(allowed_fields)
    disallowed = sorted(set(diff) - allowed)
    if disallowed:
        raise ManifestError(
            "Diff touches fields outside --allow-field allowlist: "
            f"{disallowed!r}. Allowed: {sorted(allowed)!r}."
        )


def _iter_mesh_directories(
    *,
    family: str | None,
    geo_id: str | None,
    select_all: bool,
) -> Iterator[Path]:
    root = meshes_root()
    if not root.is_dir():
        return

    for family_dir in sorted(root.iterdir()):
        if not family_dir.is_dir():
            continue
        if family is not None and family_dir.name != family:
            continue
        for geo_dir in sorted(family_dir.iterdir()):
            if not geo_dir.is_dir():
                continue
            if geo_id is not None and geo_dir.name != geo_id:
                continue
            if geo_id is None and not select_all and geo_dir.name not in CAMPAIGN_GEO_IDS:
                continue
            for mesh_dir in sorted(geo_dir.iterdir()):
                if not mesh_dir.is_dir():
                    continue
                if (mesh_dir / "manifest.json").is_file():
                    yield mesh_dir


def rebuild_mesh_manifest(
    mesh_directory: Path,
    *,
    apply: bool,
    allow_fields: Collection[str],
) -> dict[str, tuple[Any, Any]]:
    """Return per-field diff for one mesh leaf; write manifest when apply=True."""
    mesh_directory = Path(mesh_directory)
    existing = read_mesh_manifest(mesh_directory)

    mesh_file, mesh_log = _mesh_paths(mesh_directory, existing)
    if not mesh_log.is_file():
        raise FileNotFoundError(
            f"mesh log required for rebuild: {mesh_log}"
        )
    if not mesh_file.is_file():
        raise FileNotFoundError(f"mesh file not found: {mesh_file}")

    meshing = _load_meshing_helpers()
    mesh_sha256 = meshing._sha256_file(mesh_file)
    recorded_sha = existing.get("mesh_sha256")
    if recorded_sha and recorded_sha != mesh_sha256:
        raise ManifestError(
            f"mesh_sha256 mismatch for {mesh_directory}: "
            f"manifest={recorded_sha!r}, file={mesh_sha256!r}. "
            "The mesh file changed; refusing to rewrite the record."
        )

    mesh_metrics = parse_mesh_metrics_from_log(mesh_log)
    cfg = _load_cfg(_build_cfg_overrides(mesh_directory, existing))
    rebuilt = meshing.build_mesh_manifest_payload(
        cfg,
        mesh_metrics,
        mesh_sha256,
        created_utc=existing.get("created_utc"),
    )
    rebuilt["generator_version"] = existing.get(
        "generator_version",
        rebuilt.get("generator_version"),
    )
    inlet_profile_g = existing.get("inlet_profile_G")
    if inlet_profile_g is not None:
        rebuilt["inlet_profile_G"] = inlet_profile_g

    diff = _payload_diff(existing, rebuilt)
    enforce_diff_allowlist(diff, allow_fields)
    if apply and diff:
        upgrade_mesh_manifest_in_place(mesh_directory, rebuilt)
    return diff


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild mesh manifest.json from stored manifest + mesh log + .msh.h5 "
            "(dry-run by default)."
        ),
    )
    parser.add_argument("--geo-id", help="Rebuild one campaign geo_id.")
    parser.add_argument("--family", help="Rebuild all meshes under one family.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Rebuild every mesh leaf that has manifest.json.",
    )
    parser.add_argument(
        "--allow-field",
        action="append",
        default=[],
        dest="allow_fields",
        metavar="NAME",
        help=(
            "Manifest field allowed to change (repeatable). "
            "Abort if the diff touches any other key."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write manifest.json (default is dry-run diff only).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    if not args.all and args.family is None and args.geo_id is None:
        raise SystemExit(
            "Specify --geo-id, --family, or --all."
        )

    mesh_directories = list(
        _iter_mesh_directories(
            family=args.family,
            geo_id=args.geo_id,
            select_all=args.all,
        )
    )
    if not mesh_directories:
        raise SystemExit("No mesh directories matched the selection.")

    failures: list[str] = []
    changed = 0
    for mesh_directory in mesh_directories:
        label = mesh_directory.relative_to(meshes_root())
        try:
            diff = rebuild_mesh_manifest(
                mesh_directory,
                apply=args.apply,
                allow_fields=args.allow_fields,
            )
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        if not diff:
            print(f"{label}: unchanged")
            continue

        changed += 1
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"\n{label} [{mode}]")
        for key, (old, new) in diff.items():
            print(f"  {key}: {old!r} -> {new!r}")

    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)

    print(
        f"\nSummary: {len(mesh_directories)} leaf(s), "
        f"{changed} with diffs, {len(failures)} failure(s), "
        f"mode={'apply' if args.apply else 'dry-run'}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
