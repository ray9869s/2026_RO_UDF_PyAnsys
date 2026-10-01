"""Mesh one MFP geometry with the production meshing worker.

Reuses ``scripts/batch_meshing.py`` to build overrides from
``common_mesh_settings`` plus the production ``P_p100_h30`` case, and to
launch ``meshing_code_260616.py``. ``RO_DATA_ROOT`` is set on the child. The
parent sets it only while resolving the MFP registry entry, then restores it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_meshing
from ro.geometry_registry import MFBO_PILLAR_GEO_ID_RE, resolve_geometry_parameters

TEMPLATE_GEO_ID = "P_p100_h30"
DEFAULT_MESH_ID = "max085_min006_cpg5_bl4_peel2"
FAMILY = "pillar"

ALLOWED_OVERRIDE_DELTA = frozenset(
    {
        "geo_id",
        "geo_name",
        "spacing_code",
        "filament_d_m",
        "bridge_radius_m",
        "wall_spacer_labels",
        "geometry_suffix",
    }
)

MANIFEST_PRINT_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
    "porosity_eps",
    "membrane_blocked_area_frac_geometric",
    "spacer_wall_zones",
)

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


def require_mfbo_geo_id(geo_id):
    """Require an MFP pillar id. Campaign ids are not accepted here."""
    if not isinstance(geo_id, str) or MFBO_PILLAR_GEO_ID_RE.fullmatch(geo_id) is None:
        raise ValueError(
            f"geo_id must match MFBO_PILLAR_GEO_ID_RE, got {geo_id!r}."
        )
    return geo_id


def geometry_paths(data_root, geo_id):
    directory = Path(data_root) / "geometries" / FAMILY / geo_id
    return directory / f"{geo_id}.pmdb", directory / f"{geo_id}_meta.json"


def require_geometry(data_root, geo_id):
    """Require the MFP ``.pmdb`` and ``_meta.json`` under the data root."""
    pmdb, meta = geometry_paths(data_root, geo_id)
    missing = [path for path in (pmdb, meta) if not path.is_file()]
    if missing:
        listed = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"MFP geometry is missing: {listed}.")
    return pmdb, meta


def mesh_leaf(data_root, geo_id, mesh_id):
    return Path(data_root) / "meshes" / FAMILY / geo_id / mesh_id


def refuse_existing_mesh(mesh_directory):
    """Refuse to mesh into a leaf that already exists."""
    directory = Path(mesh_directory)
    if directory.exists():
        raise FileExistsError(
            f"Refusing to mesh because the mesh leaf already exists: {directory}."
        )


def call_with_data_root(data_root, fn):
    """Call ``fn`` with ``RO_DATA_ROOT`` set, then restore the parent env."""
    previous = os.environ.get("RO_DATA_ROOT")
    os.environ["RO_DATA_ROOT"] = str(data_root)
    try:
        return fn()
    finally:
        if previous is None:
            os.environ.pop("RO_DATA_ROOT", None)
        else:
            os.environ["RO_DATA_ROOT"] = previous


def resolve_registry(data_root, geo_id):
    """Read the MFP registry entry. Parent ``RO_DATA_ROOT`` is restored."""
    return call_with_data_root(
        data_root,
        lambda: resolve_geometry_parameters(geo_id),
    )


def load_template(mesh_id):
    """Production P_p100_h30 case for ``mesh_id``, merged by batch_meshing."""
    batchcfg = batch_meshing._load_module(
        "batch_config_mfbo_mesh",
        batch_meshing.BATCH_CONFIG_PATH,
    )
    cases = [
        case
        for case in batchcfg.production_mesh_batch_cases
        if case.get("geo_id") == TEMPLATE_GEO_ID and case.get("mesh_id") == mesh_id
    ]
    if len(cases) != 1:
        raise RuntimeError(
            "Expected exactly one production_mesh_batch_cases entry with "
            f"geo_id {TEMPLATE_GEO_ID!r} and mesh_id {mesh_id!r}, "
            f"found {len(cases)}."
        )
    common = getattr(batchcfg, "common_mesh_settings", {})
    overrides = batch_meshing._build_overrides(cases[0], common)
    retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            getattr(batchcfg, "cad_import_max_retries", 2),
        )
    )
    return cases[0], overrides, retries


def apply_mfbo_overrides(template, geo_id, registry):
    """Copy the template and replace only the MFP geometry keys."""
    overrides = dict(template)
    overrides["geo_id"] = geo_id
    overrides["geo_name"] = geo_id
    overrides["spacing_code"] = registry["spacing_code"]
    overrides["filament_d_m"] = registry["filament_d_m"]
    overrides["bridge_radius_m"] = registry["bridge_radius_m"]
    overrides["wall_spacer_labels"] = list(registry["spacer_wall_zones"])
    overrides["geometry_suffix"] = ".pmdb"
    return overrides


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


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def assert_override_delta(template, overrides):
    """Print every override against the template. Only MFP keys may differ."""
    keys = sorted(set(template) | set(overrides))
    rows = []
    unexpected = []
    for key in keys:
        in_template = key in template
        in_overrides = key in overrides
        template_value = template.get(key)
        override_value = overrides.get(key)
        if in_template and in_overrides and _values_equal(template_value, override_value):
            status = "same"
        else:
            status = "differs"
        row = {
            "key": key,
            "template": template_value if in_template else None,
            "override": override_value if in_overrides else None,
            "status": status,
        }
        rows.append(row)
        if status == "differs" and key not in ALLOWED_OVERRIDE_DELTA:
            unexpected.append(key)
    _print_override_table(rows)
    if unexpected:
        raise ValueError(
            "MFBO overrides may only change "
            f"{sorted(ALLOWED_OVERRIDE_DELTA)}. Unexpected differences: "
            f"{unexpected}."
        )
    return rows


def _print_override_table(rows):
    print("MFBO overrides vs production template:")
    print(f"{'key':<32} {'template':<36} {'override':<36} status")
    for row in rows:
        print(
            f"{row['key']:<32} {_cell(row['template']):<36} "
            f"{_cell(row['override']):<36} {row['status']}"
        )


def _read_manifest(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Mesh manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Mesh manifest must be a JSON object: {path}")
    return payload


def print_mesh_manifest(manifest):
    """Print the mesh-quality and geometry fields requested after the worker."""
    if not isinstance(manifest, dict):
        raise TypeError(f"manifest must be a dict, got {type(manifest).__name__}.")
    missing = [key for key in MANIFEST_PRINT_FIELDS if key not in manifest]
    if missing:
        raise KeyError(f"Mesh manifest is missing fields: {missing}.")
    print("Mesh manifest:")
    for key in MANIFEST_PRINT_FIELDS:
        print(f"  {key}: {_cell(manifest[key])}")


def launch_worker(overrides, data_root, mesh_directory, mesh_id, max_retries):
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
        mesh_log_path=Path(mesh_directory) / f"mesh_log_{mesh_id}.txt",
        mesh_run_record_path=Path(mesh_directory) / "mesh_run_record.json",
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
        description="Mesh one MFP geometry with the production meshing worker.",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child process. Not C:/ro_data.",
    )
    parser.add_argument(
        "--geo-id",
        required=True,
        help="MFP pillar geo_id, for example MFP_d1000_h0300_f0400.",
    )
    parser.add_argument(
        "--mesh-id",
        default=DEFAULT_MESH_ID,
        help=f"Production mesh id to copy settings from (default {DEFAULT_MESH_ID}).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    geo_id = require_mfbo_geo_id(args.geo_id)
    require_geometry(data_root, geo_id)
    registry = resolve_registry(data_root, geo_id)
    _case, template, max_retries = load_template(args.mesh_id)
    overrides = apply_mfbo_overrides(template, geo_id, registry)
    assert_override_delta(template, overrides)
    leaf = mesh_leaf(data_root, geo_id, args.mesh_id)
    refuse_existing_mesh(leaf)
    result = launch_worker(overrides, data_root, leaf, args.mesh_id, max_retries)
    manifest_path = leaf / "manifest.json"
    if result.returncode == 0 and not manifest_path.is_file():
        raise FileNotFoundError(
            f"Worker succeeded but manifest is missing: {manifest_path}"
        )
    if manifest_path.is_file():
        print_mesh_manifest(_read_manifest(manifest_path))
    return int(result.returncode)


if __name__ == "__main__":
    sys.exit(main())
