"""Run the production meshing worker under a separate data root.

Reuses ``scripts/batch_meshing.py`` to build overrides and to launch
``meshing_code_260616.py``. The child process is the only place
``RO_DATA_ROOT`` is set.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

from ro.solver_common import sha256_file

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_meshing

SOURCE_GEO_ID = "P_p100_h30"
SOURCE_MESH_ID = "max085_min006_cpg5_bl4_peel2"
SOURCE_FAMILY = "pillar"

REFERENCE_MANIFEST = Path(
    "C:/ro_data/meshes/pillar/P_p100_h30/max085_min006_cpg5_bl4_peel2/manifest.json"
)
REFERENCE_DSCO = Path(
    "C:/ro_data/geometries/pillar/P_p100_h30/P_p100_h30.dsco"
)

# Manifest field -> override key. periodic_shift_y on the override is
# millimetres; the manifest stores metres (worker: float(periodic_shift_y) * 1e-3).
MESH_SETTING_MAP = {
    "family": ("family", None),
    "geo_id": ("geo_id", None),
    "mesh_id": ("mesh_id", None),
    "spacing_code": ("spacing_code", None),
    "attack_angle_deg": ("attack_angle_deg", None),
    "filament_d_m": ("filament_d_m", None),
    "bridge_radius_m": ("bridge_radius_m", None),
    "overlap_m": ("overlap_m", None),
    "n_active_cells": ("n_active_cells", None),
    "n_buffer_in": ("n_buffer_in", None),
    "n_buffer_out": ("n_buffer_out", None),
    "cell_length_x_m": ("cell_length_x_m", None),
    "buffer_length_in_m": ("buffer_length_in_m", None),
    "buffer_length_out_m": ("buffer_length_out_m", None),
    "n_lead_excluded": ("n_lead_excluded", None),
    "n_trail_excluded": ("n_trail_excluded", None),
    "max_size_mm": ("m_max", None),
    "min_size_mm": ("m_min", None),
    "cpg": ("m_cpg", None),
    "bl": ("bl_layers", None),
    "peel": ("peel_layers", None),
    "membrane_wall_base_names": ("active_membrane_wall_labels", None),
    "buffer_wall_base_names": ("buffer_wall_labels", None),
    "periodic_shift_y_m": ("periodic_shift_y", 1.0e-3),
}

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12


def resolve_data_root(value):
    """Return an absolute data root that is not C:/ro_data or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    folded = text.casefold().rstrip("/")
    if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"data root must be absolute, got {value!r}.")
        path = path.resolve()
    resolved = path.as_posix().casefold().rstrip("/")
    if resolved == "c:/ro_data" or resolved.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if resolved == "/mnt/c/ro_data" or resolved.startswith("/mnt/c/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    return path


def _is_windows_absolute(text):
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def assert_sha256_equal(source, dest):
    """Raise if two files do not have the same SHA-256."""
    source_hash = sha256_file(source)
    dest_hash = sha256_file(dest)
    if source_hash != dest_hash:
        raise RuntimeError(
            "Geometry sha256 mismatch: "
            f"source {source} {source_hash}, dest {dest} {dest_hash}."
        )
    return dest_hash


def copy_geometry_file(source, dest):
    """Copy ``source`` to ``dest``. Refuse overwrite. Verify SHA-256."""
    source = Path(source)
    dest = Path(dest)
    if not source.is_file():
        raise FileNotFoundError(f"Geometry file not found: {source}")
    if dest.exists():
        raise FileExistsError(f"Refusing to overwrite geometry file: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    if not source.is_file():
        raise RuntimeError(f"Geometry source missing after copy: {source}")
    return assert_sha256_equal(source, dest)


def _projected_override(manifest_key, overrides):
    override_key, scale = MESH_SETTING_MAP[manifest_key]
    if override_key not in overrides:
        raise KeyError(
            f"Override dict has no {override_key!r} for manifest field {manifest_key!r}."
        )
    value = overrides[override_key]
    if scale is None:
        return override_key, value
    return override_key, float(value) * scale


def _values_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=_FLOAT_REL_TOL,
            abs_tol=_FLOAT_ABS_TOL,
        )
    return left == right


def compare_mesh_settings(manifest, overrides):
    """Compare mapped manifest settings with the overrides that will be sent."""
    if not isinstance(manifest, dict):
        raise TypeError(f"manifest must be a dict, got {type(manifest).__name__}.")
    rows = []
    mismatches = []
    for manifest_key in MESH_SETTING_MAP:
        if manifest_key not in manifest:
            continue
        override_key, projected = _projected_override(manifest_key, overrides)
        manifest_value = manifest[manifest_key]
        match = _values_equal(manifest_value, projected)
        status = "ok" if match else "mismatch"
        row = {
            "manifest_key": manifest_key,
            "override_key": override_key,
            "manifest_value": manifest_value,
            "override_value": projected,
            "status": status,
        }
        rows.append(row)
        if status == "mismatch":
            mismatches.append(row)
    _print_setting_table(rows)
    if mismatches:
        detail = "; ".join(
            f"{row['manifest_key']}: manifest={row['manifest_value']!r} "
            f"override={row['override_value']!r}"
            for row in mismatches
        )
        raise ValueError(f"Mesh settings differ from the reference manifest: {detail}")
    return rows


def _print_setting_table(rows):
    print("Mesh setting comparison (manifest vs overrides to be sent):")
    print(
        f"{'manifest_key':<28} {'override_key':<32} "
        f"{'manifest':<24} {'override':<24} status"
    )
    for row in rows:
        print(
            f"{row['manifest_key']:<28} {row['override_key']:<32} "
            f"{_cell(row['manifest_value']):<24} {_cell(row['override_value']):<24} "
            f"{row['status']}"
        )


def _cell(value):
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_field_rows(new_manifest, reference_manifest):
    """Numeric fields present in both manifests. No pass/fail threshold."""
    if not isinstance(new_manifest, dict) or not isinstance(reference_manifest, dict):
        raise TypeError("manifests must be dicts.")
    rows = []
    for key in sorted(set(new_manifest) & set(reference_manifest)):
        value = new_manifest[key]
        reference = reference_manifest[key]
        if not _is_number(value) or not _is_number(reference):
            continue
        abs_diff = abs(float(value) - float(reference))
        if float(reference) == 0.0:
            rel_diff = 0.0 if abs_diff == 0.0 else None
        else:
            rel_diff = abs_diff / abs(float(reference))
        rows.append(
            {
                "field": key,
                "value": value,
                "reference": reference,
                "abs_diff": abs_diff,
                "rel_diff": rel_diff,
            }
        )
    return rows


def _print_parity_table(rows):
    print("Numeric parity vs production manifest:")
    print(f"{'field':<32} {'value':<16} {'reference':<16} {'abs_diff':<16} rel_diff")
    for row in rows:
        rel = "" if row["rel_diff"] is None else f"{row['rel_diff']:.6g}"
        print(
            f"{row['field']:<32} {_cell(row['value']):<16} "
            f"{_cell(row['reference']):<16} {_cell(row['abs_diff']):<16} {rel}"
        )


def load_source_case():
    """Return the single production P_p100_h30 / max085 mesh case and overrides."""
    batchcfg = batch_meshing._load_module(
        "batch_config_parity_mesh",
        batch_meshing.BATCH_CONFIG_PATH,
    )
    cases = [
        case
        for case in batchcfg.production_mesh_batch_cases
        if case.get("geo_id") == SOURCE_GEO_ID and case.get("mesh_id") == SOURCE_MESH_ID
    ]
    if len(cases) != 1:
        raise RuntimeError(
            "Expected exactly one production_mesh_batch_cases entry with "
            f"geo_id {SOURCE_GEO_ID!r} and mesh_id {SOURCE_MESH_ID!r}, "
            f"found {len(cases)}."
        )
    case = cases[0]
    if case.get("family") != SOURCE_FAMILY:
        raise RuntimeError(
            f"Source case family must be {SOURCE_FAMILY!r}, got {case.get('family')!r}."
        )
    common = getattr(batchcfg, "common_mesh_settings", {})
    overrides = batch_meshing._build_overrides(case, common)
    retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            getattr(batchcfg, "cad_import_max_retries", 2),
        )
    )
    return case, overrides, retries


def assert_geometry_override_delta(source_overrides, overrides):
    """Geometry stage may change only geometry_suffix, and only to .pmdb."""
    if overrides.get("geometry_suffix") != ".pmdb":
        raise ValueError("geometry stage must set geometry_suffix='.pmdb'.")
    source = dict(source_overrides)
    sent = dict(overrides)
    source.pop("geometry_suffix", None)
    sent.pop("geometry_suffix", None)
    if source != sent:
        raise ValueError(
            "Geometry stage may only change geometry_suffix. "
            f"source={source!r} sent={sent!r}."
        )


def refuse_code_stage_dsco(geometry_dir):
    """Refuse a geometry-stage root that already holds the code-stage .dsco."""
    dsco = Path(geometry_dir) / f"{SOURCE_GEO_ID}.dsco"
    if dsco.exists():
        raise FileExistsError(
            f"Refusing geometry stage because {dsco} already exists. "
            "Use a data root that is not the code-stage root."
        )


def overrides_for_stage(source_overrides, stage):
    """Copy source overrides. Geometry stage sets geometry_suffix only."""
    overrides = dict(source_overrides)
    if stage == "code":
        return overrides
    if stage == "geometry":
        overrides["geometry_suffix"] = ".pmdb"
        assert_geometry_override_delta(source_overrides, overrides)
        return overrides
    raise ValueError(f"Unknown stage {stage!r}.")


def _read_manifest(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Mesh manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Mesh manifest must be a JSON object: {path}")
    return payload


def _geometry_destination(data_root, geo_id, suffix):
    return data_root / "geometries" / SOURCE_FAMILY / geo_id / f"{geo_id}{suffix}"


def _mesh_leaf(data_root, geo_id):
    return data_root / "meshes" / SOURCE_FAMILY / geo_id / SOURCE_MESH_ID


def write_parity_report(mesh_leaf, reference_manifest, new_manifest, *, stage, geo_id, reference_path):
    rows = numeric_field_rows(new_manifest, reference_manifest)
    _print_parity_table(rows)
    payload = {
        "stage": stage,
        "geo_id": geo_id,
        "reference_manifest": str(reference_path),
        "mesh_manifest": str(mesh_leaf / "manifest.json"),
        "rows": rows,
    }
    path = mesh_leaf / "parity_vs_production.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def launch_worker(overrides, data_root, mesh_leaf, max_retries):
    """Launch meshing_code_260616.py the way batch_meshing.main does."""
    cmd = [sys.executable, str(batch_meshing.MESHING_SCRIPT_PATH)]
    env = {
        **os.environ,
        "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides),
        "RO_DATA_ROOT": str(data_root),
    }
    env.pop("PYFLUENT_RUN_CONFIG", None)
    env.pop("PYFLUENT_SKIP_VALIDATION", None)
    print(f"Command: {' '.join(cmd)}")
    print(f"cwd: {batch_meshing.SCRIPT_DIR}")
    print(f"RO_DATA_ROOT (child only): {data_root}")
    result, attempts, retry_kinds = batch_meshing.run_meshing_attempts(
        cmd=cmd,
        env=env,
        cwd=str(batch_meshing.SCRIPT_DIR),
        mesh_log_path=mesh_leaf / f"mesh_log_{SOURCE_MESH_ID}.txt",
        mesh_run_record_path=mesh_leaf / "mesh_run_record.json",
        max_retries=max_retries,
    )
    if result is None:
        raise RuntimeError("Meshing worker did not return a process result.")
    print(
        f"Worker return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run the production meshing worker for P_p100_h30 parity.",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child process. Not C:/ro_data.",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=("code", "geometry"),
        help="code copies the production .dsco. geometry copies a .pmdb.",
    )
    parser.add_argument(
        "--pmdb",
        default=None,
        help="Source .pmdb for --stage geometry.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.stage == "geometry" and not args.pmdb:
        raise ValueError("--stage geometry requires --pmdb.")
    if args.stage == "code" and args.pmdb:
        raise ValueError("--pmdb is only valid with --stage geometry.")

    data_root = resolve_data_root(args.data_root)
    _case, source_overrides, max_retries = load_source_case()
    overrides = overrides_for_stage(source_overrides, args.stage)
    geo_id = overrides["geo_id"]
    if geo_id != SOURCE_GEO_ID:
        raise RuntimeError(
            f"Parity mesh geo_id must stay {SOURCE_GEO_ID!r}, got {geo_id!r}."
        )
    reference = _read_manifest(REFERENCE_MANIFEST)
    compare_mesh_settings(reference, overrides)

    if args.stage == "code":
        source = REFERENCE_DSCO
        suffix = ".dsco"
    else:
        source = Path(args.pmdb)
        suffix = ".pmdb"
    destination = _geometry_destination(data_root, geo_id, suffix)
    if args.stage == "geometry":
        refuse_code_stage_dsco(destination.parent)
    digest = copy_geometry_file(source, destination)
    print(f"Copied {source} -> {destination} sha256={digest}")

    mesh_leaf = _mesh_leaf(data_root, geo_id)
    result = launch_worker(overrides, data_root, mesh_leaf, max_retries)
    manifest_path = mesh_leaf / "manifest.json"
    if result.returncode == 0 and not manifest_path.is_file():
        raise FileNotFoundError(f"Worker succeeded but manifest is missing: {manifest_path}")
    if manifest_path.is_file():
        write_parity_report(
            mesh_leaf,
            reference,
            _read_manifest(manifest_path),
            stage=args.stage,
            geo_id=geo_id,
            reference_path=REFERENCE_MANIFEST,
        )
    if result.returncode != 0:
        return int(result.returncode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
