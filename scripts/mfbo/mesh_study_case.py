"""Mesh, solve, and extract one campaign case with mesh-knob overrides.

The production baseline is not rebuilt here. Re-extract that leaf with
``reextract_runs.py --copy-from-production``. This driver refuses a study
``mesh_id`` that matches the production mesh id.

Before the meshing worker starts, the production geometry file
``C:/ro_data/geometries/<family>/<geo_id>/<geo_id>.dsco`` is copied into
the study data root. An existing copy is reused only when its sha256
matches. The source is never moved. ``--geometry-suffix .pmdb`` with
``--geometry-root`` copies the converted file from that root instead
(never from ``C:/ro_data``), requires its parity record to have passed,
and passes ``geometry_suffix`` and ``geometry_root`` to the meshing child.
The default remains the production ``.dsco``.

``format_production_mesh_id`` encodes max/min/cpg/bl/peel and omits the
default first-height factor. A factor that differs from the template is
inserted as ``_fNNN`` (``round(factor * 100)``, so 0.40 is ``f040``) before
``_peel``, which is the token ``MESH_ID_RE`` already accepts. A spacer
layer count that differs from ``bl_layers`` is ``bl{membrane}s{spacer}``.

``--reuse-mesh`` skips meshing when that mesh leaf already exists, its
``mesh_run_record.json`` status is ``SUCCESS``, and the manifest settings
match the request. The run leaf is still refused when it exists.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_meshing
import batch_report_extract
import batch_solver_sweep
import mfbo._common as mfbo_common
import mfbo.solve_campaign_case as campaign
from ro.campaign_matrix import format_production_mesh_id
from ro.convergence_quality import continuity_final_from_case_dir
from ro.mesh_common import load_mesh_run_record, parse_meshing_input_summary
from ro.paths import MESH_ID_RE, project_root
from ro.solver_common import sha256_file

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12
PRODUCTION_DATA_ROOT = Path("C:/ro_data")

MESH_PRINT_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
)
SUMMARY_PRINT_FIELDS = (
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cpc_window_avg_flux",
    "cp_q99_window_flux",
    "cp_q999_window_flux",
)


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


def require_base_case(case):
    return campaign.require_base_case(case)


def collect_overrides(
    *,
    m_max=None,
    m_min=None,
    m_cpg=None,
    bl_layers=None,
    bl_height_factor=None,
    spacer_bl_layers=None,
):
    """Return the mesh knobs the caller set. At least one is required."""
    changes = {}
    if m_max is not None:
        _require_positive("m_max", m_max)
        changes["m_max"] = float(m_max)
    if m_min is not None:
        _require_positive("m_min", m_min)
        changes["m_min"] = float(m_min)
    if m_cpg is not None:
        _require_positive_int("m_cpg", m_cpg)
        changes["m_cpg"] = int(m_cpg)
    if bl_layers is not None:
        _require_positive_int("bl_layers", bl_layers)
        changes["bl_layers"] = int(bl_layers)
    if bl_height_factor is not None:
        _require_positive("bl_height_factor", bl_height_factor)
        changes["bl_height_factor"] = float(bl_height_factor)
    if spacer_bl_layers is not None:
        _require_positive_int("spacer_bl_layers", spacer_bl_layers)
        changes["spacer_bl_layers"] = int(spacer_bl_layers)
    if not changes:
        raise ValueError(
            "At least one mesh override is required "
            "(--m-max, --m-min, --m-cpg, --bl-layers, "
            "--bl-first-height-factor, --spacer-bl-layers). "
            "The production baseline is "
            "reextract_runs.py --copy-from-production."
        )
    return changes


def _require_positive(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number, got {value!r}.")
    if not math.isfinite(float(value)) or float(value) <= 0.0:
        raise ValueError(f"{name} must be a positive number, got {value!r}.")


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")


def default_bl_height_factor():
    """``run_config.bl_height_factor``. The production template does not set it."""
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "mesh_study_run_config",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return float(module.bl_height_factor)


def template_bl_height_factor(template):
    if template.get("bl_height_factor") is not None:
        return float(template["bl_height_factor"])
    return default_bl_height_factor()


def with_height_factor_token(mesh_id, factor):
    """Insert ``_fNNN`` before ``_peel``. ``NNN`` is ``round(factor * 100)``."""
    number = int(round(float(factor) * 100.0))
    if number < 0 or number > 999:
        raise ValueError(
            f"bl_height_factor {factor!r} does not fit the _fNNN mesh_id token."
        )
    stem, peel = str(mesh_id).rsplit("_peel", 1)
    return f"{stem}_f{number:03d}_peel{peel}"


def study_mesh_id(
    *,
    m_max,
    m_min,
    m_cpg,
    bl_layers,
    peel_layers,
    bl_height_factor,
    template_factor,
    spacer_bl_layers=None,
):
    """Production formatter, plus ``_fNNN`` when the factor is not the template."""
    mesh_id = format_production_mesh_id(
        m_max=m_max,
        m_min=m_min,
        m_cpg=m_cpg,
        bl_layers=bl_layers,
        peel_layers=peel_layers,
        spacer_bl_layers=spacer_bl_layers,
    )
    if not math.isclose(
        float(bl_height_factor),
        float(template_factor),
        rel_tol=_FLOAT_REL_TOL,
        abs_tol=_FLOAT_ABS_TOL,
    ):
        mesh_id = with_height_factor_token(mesh_id, bl_height_factor)
    if MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"study mesh_id is invalid: {mesh_id!r}.")
    return mesh_id


def require_distinct_mesh_id(study_id, production_id):
    if study_id == production_id:
        raise ValueError(
            f"study mesh_id {study_id!r} equals the production mesh_id. "
            "The baseline is reextract_runs.py --copy-from-production."
        )
    return study_id


def select_production_mesh_case(cases, geo_id):
    selected = [case for case in cases if case.get("geo_id") == geo_id]
    if len(selected) != 1:
        raise RuntimeError(
            "Expected exactly one production_mesh_batch_cases entry with "
            f"geo_id {geo_id!r}, found {len(selected)}."
        )
    return selected[0]


def load_mesh_template(geo_id):
    """One production mesh case, merged by the batch meshing override builder."""
    batchcfg = batch_meshing._load_module(
        "batch_config_mesh_study",
        batch_meshing.BATCH_CONFIG_PATH,
    )
    case = select_production_mesh_case(
        batchcfg.production_mesh_batch_cases,
        geo_id,
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


def apply_study_overrides(template, changes):
    """Copy the template, apply knobs, and set the study mesh_id."""
    overrides = dict(template)
    overrides.update(changes)
    factor = float(
        overrides.get("bl_height_factor", template_bl_height_factor(template))
    )
    m_max = float(overrides["m_max"])
    m_min = float(overrides["m_min"])
    if m_min > m_max:
        raise ValueError(
            f"m_min must be <= m_max. m_min={m_min!r}, m_max={m_max!r}."
        )
    mesh_id = study_mesh_id(
        m_max=m_max,
        m_min=m_min,
        m_cpg=int(overrides["m_cpg"]),
        bl_layers=int(overrides["bl_layers"]),
        peel_layers=int(overrides["peel_layers"]),
        bl_height_factor=factor,
        template_factor=template_bl_height_factor(template),
        spacer_bl_layers=overrides.get("spacer_bl_layers"),
    )
    require_distinct_mesh_id(mesh_id, template["mesh_id"])
    overrides["mesh_id"] = mesh_id
    overrides["case_name"] = mesh_id
    if "bl_height_factor" in changes:
        overrides["bl_height_factor"] = factor
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


def override_rows(template, overrides):
    keys = sorted(set(template) | set(overrides))
    rows = []
    for key in keys:
        in_template = key in template
        in_overrides = key in overrides
        template_value = template.get(key)
        override_value = overrides.get(key)
        if in_template and in_overrides and _values_equal(template_value, override_value):
            status = "same"
        else:
            status = "differs"
        rows.append(
            {
                "key": key,
                "template": template_value if in_template else None,
                "override": override_value if in_overrides else None,
                "status": status,
            }
        )
    return rows


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def print_override_table(rows):
    print("Mesh study overrides vs production template:")
    print(f"{'key':<32} {'template':<36} {'override':<36} status")
    for row in rows:
        print(
            f"{row['key']:<32} {_cell(row['template']):<36} "
            f"{_cell(row['override']):<36} {row['status']}"
        )


def production_geometry_dsco(family, geo_id):
    """``C:/ro_data/geometries/<family>/<geo_id>/<geo_id>.dsco``."""
    return (
        PRODUCTION_DATA_ROOT
        / "geometries"
        / family
        / geo_id
        / f"{geo_id}.dsco"
    )


def study_geometry_file(data_root, family, geo_id, suffix=".dsco"):
    return (
        Path(data_root) / "geometries" / family / geo_id / f"{geo_id}{suffix}"
    )


def study_geometry_dsco(data_root, family, geo_id):
    return study_geometry_file(data_root, family, geo_id, ".dsco")


def resolve_geometry_request(suffix, geometry_root):
    """Default ``.dsco`` with no root keeps the production CAD.

    ``.pmdb`` requires a geometry root that is not under ``C:/ro_data``.
    """
    if suffix is None:
        suffix = ".dsco"
    if suffix not in (".dsco", ".pmdb"):
        raise ValueError(
            f"geometry suffix must be '.dsco' or '.pmdb', got {suffix!r}."
        )
    if suffix == ".dsco":
        if geometry_root not in (None, ""):
            raise ValueError(
                "--geometry-root is only valid with --geometry-suffix .pmdb."
            )
        return ".dsco", None
    if geometry_root in (None, ""):
        raise ValueError("--geometry-suffix .pmdb requires --geometry-root.")
    return ".pmdb", resolve_geometry_root(geometry_root)


def resolve_geometry_root(value):
    """Absolute converted-CAD root. Refuses ``C:/ro_data``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("geometry root is required.")
    text = value.strip().replace("\\", "/")
    if _under_production_text(text):
        raise ValueError(
            f"Refusing to read campaign .pmdb from C:/ro_data: {value}"
        )
    if _is_windows_absolute(text):
        return Path(text)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"geometry root must be absolute, got {value!r}.")
    path = path.resolve()
    if _under_production_tree(path):
        raise ValueError(
            f"Refusing to read campaign .pmdb from C:/ro_data: {value}"
        )
    return path


def _under_production_text(text):
    folded = str(text).replace("\\", "/").casefold().rstrip("/")
    return (
        folded == "c:/ro_data"
        or folded.startswith("c:/ro_data/")
        or folded == "/mnt/c/ro_data"
        or folded.startswith("/mnt/c/ro_data/")
    )


def pmdb_source_file(geometry_root, family, geo_id):
    return Path(geometry_root) / family / geo_id / f"{geo_id}.pmdb"


def parity_record_path(geometry_root, geo_id):
    """``<geometry-root>_parity/<geo_id>/parity.json``.

    That is where ``parity_campaign_pmdb.py`` writes when ``--pmdb-root``
    is this geometry root and ``--work-root`` is the default sibling.
    """
    text = Path(geometry_root).as_posix().rstrip("/")
    return Path(text + "_parity") / geo_id / "parity.json"


def geometry_meshing_overrides(suffix, geometry_root):
    """Child overrides. Empty for the production ``.dsco`` default."""
    if suffix == ".dsco":
        return {}
    return {
        "geometry_suffix": ".pmdb",
        "geometry_root": Path(geometry_root).as_posix(),
    }


def _read_json_object(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def require_converted_pmdb(geometry_root, family, geo_id):
    """Return the converted ``.pmdb`` after its meta sha256 matches."""
    source = pmdb_source_file(geometry_root, family, geo_id)
    if _under_production_tree(source) or _under_production_text(source):
        raise ValueError(
            f"Refusing to read campaign .pmdb from C:/ro_data: {source}"
        )
    if not source.is_file():
        raise FileNotFoundError(f"Converted .pmdb not found: {source}")
    meta_path = source.with_name(f"{geo_id}_meta.json")
    if not meta_path.is_file():
        raise FileNotFoundError(f"Conversion meta not found: {meta_path}")
    meta = _read_json_object(meta_path)
    digest = sha256_file(source)
    if meta.get("status") != "converted" or meta.get("pmdb_sha256") != digest:
        raise RuntimeError(
            "Converted .pmdb sha256 does not match its meta: "
            f"file {digest}, meta status {meta.get('status')!r}, "
            f"meta pmdb_sha256 {meta.get('pmdb_sha256')!r}, path {source}."
        )
    return source


def require_passed_parity(geometry_root, geo_id):
    """Raise unless the parity record for this geometry passed."""
    path = parity_record_path(geometry_root, geo_id)
    if not path.is_file():
        raise FileNotFoundError(f"Parity record not found: {path}")
    payload = _read_json_object(path)
    passed = (
        payload.get("geo_id") == geo_id
        and payload.get("status") == "passed"
        and payload.get("passed") is True
    )
    if not passed:
        raise RuntimeError(
            "Parity record did not pass for "
            f"{geo_id}: status {payload.get('status')!r}, "
            f"passed {payload.get('passed')!r}, path {path}."
        )
    return path


def _under_production_tree(path):
    folded = Path(path).as_posix().casefold().rstrip("/")
    return (
        folded == "c:/ro_data"
        or folded.startswith("c:/ro_data/")
        or folded == "/mnt/c/ro_data"
        or folded.startswith("/mnt/c/ro_data/")
    )


def ensure_production_geometry_dsco(data_root, family, geo_id):
    """Copy the production .dsco. See ``ensure_study_geometry``."""
    return ensure_study_geometry(data_root, family, geo_id)


def ensure_study_geometry(
    data_root,
    family,
    geo_id,
    *,
    suffix=".dsco",
    geometry_root=None,
    require_parity=False,
):
    """Copy the CAD into the study tree, or reuse it when the sha256 matches.

    The default source is the production ``.dsco``. ``suffix='.pmdb'``
    reads the converted file from ``geometry_root`` (never ``C:/ro_data``)
    and checks it against the conversion meta. ``require_parity`` also
    requires that geometry's parity record to have passed.

    Uses ``run_parity_mesh.copy_geometry_file`` (copy only; sha256 checked
    after the copy). An existing destination is not rewritten: the same
    helper's sha256 compare must match, or this raises and leaves the file
    in place. The source is never moved.
    """
    import mfbo.run_parity_mesh as parity_mesh

    suffix, root = resolve_geometry_request(suffix, geometry_root)
    dest = study_geometry_file(data_root, family, geo_id, suffix)
    if _under_production_tree(dest):
        raise ValueError(f"Refusing to copy geometry into C:/ro_data: {dest}")
    if suffix == ".dsco":
        source = production_geometry_dsco(family, geo_id)
        if not source.is_file():
            raise FileNotFoundError(f"Geometry file not found: {source}")
    else:
        source = require_converted_pmdb(root, family, geo_id)
        if require_parity:
            require_passed_parity(root, geo_id)
    if dest.exists():
        if not dest.is_file():
            raise FileExistsError(
                f"Refusing to overwrite geometry path that is not a file: {dest}"
            )
        digest = parity_mesh.assert_sha256_equal(source, dest)
        print(f"Reusing geometry {dest} sha256={digest}")
        return dest
    digest = parity_mesh.copy_geometry_file(source, dest)
    print(f"Copied geometry {source} -> {dest} sha256={digest}")
    return dest


def mesh_leaf(data_root, family, geo_id, mesh_id):
    return Path(data_root) / "meshes" / family / geo_id / mesh_id


def run_leaf(data_root, family, geo_id, mesh_id, run_id):
    return Path(data_root) / "runs" / family / geo_id / mesh_id / run_id


def refuse_existing_directory(path, label):
    directory = Path(path)
    if directory.exists():
        raise FileExistsError(
            f"Refusing to continue because the {label} already exists: {directory}."
        )


_MISSING = object()
_REUSE_MANIFEST_FIELDS = (
    ("m_max", "max_size_mm"),
    ("m_min", "min_size_mm"),
    ("m_cpg", "cpg"),
    ("bl_layers", "bl"),
    ("peel_layers", "peel"),
)
_MESH_RECORD_SUCCESS = "SUCCESS"


def _recorded_spacer_layers(manifest):
    """Spacer layer count stored on the manifest.

    ``spacer_bl`` is written only when it differs from ``bl``. Absence
    means the spacer count equals the membrane count.
    """
    if "spacer_bl" in manifest:
        return manifest["spacer_bl"]
    if "bl" in manifest:
        return manifest["bl"]
    return _MISSING


def _requested_spacer_layers(overrides):
    if "spacer_bl_layers" in overrides and overrides["spacer_bl_layers"] is not None:
        return overrides["spacer_bl_layers"]
    return overrides["bl_layers"]


def _setting_text(value):
    if value is _MISSING:
        return "missing"
    return repr(value)


def require_reusable_mesh(mesh_directory, overrides, geometry_file, geometry_sha256):
    """Raise unless ``mesh_directory`` is a successful mesh of these settings.

    Success is ``mesh_run_record.json`` status ``SUCCESS`` together with a
    manifest object. Compared settings are ``m_max``, ``m_min``, ``m_cpg``,
    ``bl_layers``, ``spacer_bl_layers``, ``peel_layers``, and the geometry
    SHA-256. ``geometry_sha256`` is the hash this job requires. A manifest
    field ``geometry_sha256`` is the recorded hash when present; otherwise
    the hash of ``geometry_file`` is the recorded hash.
    """
    directory = Path(mesh_directory)
    manifest_path = directory / "manifest.json"
    record_path = directory / "mesh_run_record.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"Refusing to reuse mesh leaf {directory}: "
            "mesh manifest manifest.json is missing."
        )
    manifest = _read_json(manifest_path)
    record = load_mesh_run_record(record_path)
    status = None if record is None else record.get("status")
    if status != _MESH_RECORD_SUCCESS:
        raise ValueError(
            f"Refusing to reuse mesh leaf {directory}: "
            "mesh does not record success "
            f"(mesh_run_record status={status!r})."
        )
    mismatches = []
    for override_key, manifest_key in _REUSE_MANIFEST_FIELDS:
        recorded = manifest[manifest_key] if manifest_key in manifest else _MISSING
        requested = overrides[override_key] if override_key in overrides else _MISSING
        if (
            recorded is _MISSING
            or requested is _MISSING
            or not _values_equal(recorded, requested)
        ):
            mismatches.append(
                f"{override_key}: recorded {_setting_text(recorded)}, "
                f"requested {_setting_text(requested)}"
            )
    recorded_spacer = _recorded_spacer_layers(manifest)
    requested_spacer = (
        _requested_spacer_layers(overrides)
        if "bl_layers" in overrides
        else _MISSING
    )
    if (
        recorded_spacer is _MISSING
        or requested_spacer is _MISSING
        or not _values_equal(recorded_spacer, requested_spacer)
    ):
        mismatches.append(
            "spacer_bl_layers: recorded "
            f"{_setting_text(recorded_spacer)}, "
            f"requested {_setting_text(requested_spacer)}"
        )
    on_disk_sha256 = sha256_file(geometry_file)
    if "geometry_sha256" in manifest:
        recorded_sha256 = manifest["geometry_sha256"]
    else:
        recorded_sha256 = on_disk_sha256
    if recorded_sha256 != geometry_sha256:
        mismatches.append(
            "geometry_sha256: recorded "
            f"{_setting_text(recorded_sha256)}, "
            f"requested {_setting_text(geometry_sha256)}"
        )
    elif on_disk_sha256 != geometry_sha256:
        mismatches.append(
            "geometry_sha256: recorded "
            f"{_setting_text(on_disk_sha256)}, "
            f"requested {_setting_text(geometry_sha256)}"
        )
    if mismatches:
        raise ValueError(
            f"Refusing to reuse mesh leaf {directory}: " + "; ".join(mismatches) + "."
        )
    return manifest


def launch_meshing(overrides, data_root, mesh_directory, mesh_id, max_retries):
    """Launch the production meshing worker. ``RO_DATA_ROOT`` is child-only."""
    cmd = [sys.executable, str(batch_meshing.MESHING_SCRIPT_PATH)]
    env = {
        **os.environ,
        "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides),
        "RO_DATA_ROOT": str(data_root),
    }
    env.pop("PYFLUENT_RUN_CONFIG", None)
    env.pop("PYFLUENT_SKIP_VALIDATION", None)
    print(f"Command: {' '.join(cmd)}")
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
        f"Meshing return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result


def solver_overrides_for_study(solver_overrides, study_mesh_id):
    """Replace only ``mesh_id`` on the production solver overrides."""
    updated = dict(solver_overrides)
    if updated.get("mesh_id") == study_mesh_id:
        raise ValueError(
            "solver template mesh_id is already the study mesh_id "
            f"{study_mesh_id!r}."
        )
    updated["mesh_id"] = study_mesh_id
    return updated


def first_bl_height_mm(mesh_directory, mesh_id):
    """Height the worker printed and passed to Add Boundary Layers, in mm.

    The mesh manifest does not store it. The input summary in the mesh log
    does, and ``mesh_run_record.json`` stores the same ``bl_height``.
    """
    directory = Path(mesh_directory)
    log_path = directory / f"mesh_log_{mesh_id}.txt"
    if log_path.is_file():
        parsed = parse_meshing_input_summary(
            log_path.read_text(encoding="utf-8", errors="ignore")
        )
        if parsed.get("bl_height") is not None:
            return parsed["bl_height"]
    record = load_mesh_run_record(directory / "mesh_run_record.json")
    if record is not None and record.get("bl_height") is not None:
        return record["bl_height"]
    raise RuntimeError(
        "Worker did not record the boundary-layer first height in "
        f"{log_path} or mesh_run_record.json."
    )


def _read_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file must be an object: {path}")
    return payload


def read_summary_wide(path):
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: {path}"
        )
    return rows[0]


def study_report(mesh_directory, mesh_id, run_directory):
    manifest_path = Path(mesh_directory) / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Mesh manifest not found: {manifest_path}")
    mesh_manifest = _read_json(manifest_path)
    missing = [key for key in MESH_PRINT_FIELDS if key not in mesh_manifest]
    if missing:
        raise KeyError(f"Mesh manifest is missing fields: {missing}.")
    run_manifest_path = Path(run_directory) / "manifest.json"
    run_manifest = (
        _read_json(run_manifest_path) if run_manifest_path.is_file() else {}
    )
    summary = read_summary_wide(
        Path(run_directory) / "post" / "reports" / "summary_metrics_wide.csv"
    )
    continuity = run_manifest.get("continuity_final")
    if continuity is None and Path(run_directory).is_dir():
        continuity = continuity_final_from_case_dir(run_directory)
    report = {
        "mesh_id": mesh_id,
        "first_bl_height_mm": first_bl_height_mm(mesh_directory, mesh_id),
        "stop_reason": run_manifest.get("stop_reason"),
        "continuity_final": continuity,
    }
    for key in MESH_PRINT_FIELDS:
        report[key] = mesh_manifest[key]
    for key in SUMMARY_PRINT_FIELDS:
        report[key] = summary.get(key)
    return report


def print_study_report(report):
    fields = (
        "mesh_id",
        *MESH_PRINT_FIELDS,
        "first_bl_height_mm",
        "stop_reason",
        "continuity_final",
        *SUMMARY_PRINT_FIELDS,
    )
    print("Mesh study:")
    for key in fields:
        print(f"  {key}: {_cell(report.get(key))}")


def _exit_code(returncode):
    if returncode:
        return int(returncode)
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Mesh, solve, and extract one campaign case with mesh overrides. "
            "Refuses C:/ro_data and a mesh_id equal to production."
        ),
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Absolute RO_DATA_ROOT for the child processes. Not C:/ro_data.",
    )
    parser.add_argument("--geo-id", required=True, help="Campaign geo_id.")
    parser.add_argument(
        "--case",
        default="u0p2_p6M",
        help="Base run id (default u0p2_p6M).",
    )
    parser.add_argument("--m-max", type=float, default=None)
    parser.add_argument("--m-min", type=float, default=None)
    parser.add_argument("--m-cpg", type=int, default=None)
    parser.add_argument("--bl-layers", type=int, default=None)
    parser.add_argument("--bl-first-height-factor", type=float, default=None)
    parser.add_argument("--spacer-bl-layers", type=int, default=None)
    parser.add_argument(
        "--reuse-mesh",
        action="store_true",
        help=(
            "Reuse an existing mesh leaf when its mesh record is SUCCESS "
            "and the recorded settings match. The run leaf must not exist."
        ),
    )
    parser.add_argument(
        "--geometry-suffix",
        default=".dsco",
        help="CAD suffix copied into the study tree. Default .dsco.",
    )
    parser.add_argument(
        "--geometry-root",
        default=None,
        help=(
            "Converted CAD root for --geometry-suffix .pmdb "
            "(<family>/<geo_id>/<geo_id>.pmdb). Not C:/ro_data."
        ),
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    case_id = require_base_case(args.case)
    changes = collect_overrides(
        m_max=args.m_max,
        m_min=args.m_min,
        m_cpg=args.m_cpg,
        bl_layers=args.bl_layers,
        bl_height_factor=args.bl_first_height_factor,
        spacer_bl_layers=args.spacer_bl_layers,
    )
    case, template, max_retries = load_mesh_template(args.geo_id)
    family = case["family"]
    production_mesh_id = case["mesh_id"]
    suffix, geometry_root = resolve_geometry_request(
        args.geometry_suffix, args.geometry_root
    )
    overrides = apply_study_overrides(template, changes)
    overrides.update(geometry_meshing_overrides(suffix, geometry_root))
    print_override_table(override_rows(template, overrides))
    study_id = overrides["mesh_id"]
    leaf = mesh_leaf(data_root, family, args.geo_id, study_id)
    geometry_kwargs = {
        "suffix": suffix,
        "geometry_root": None if geometry_root is None else geometry_root.as_posix(),
        "require_parity": suffix == ".pmdb",
    }
    if args.reuse_mesh and leaf.exists():
        geometry = ensure_study_geometry(
            data_root,
            family,
            args.geo_id,
            **geometry_kwargs,
        )
        require_reusable_mesh(
            leaf,
            overrides,
            geometry,
            sha256_file(geometry),
        )
        print(f"Reusing mesh leaf {leaf}")
    else:
        refuse_existing_directory(leaf, "mesh leaf")
        ensure_study_geometry(
            data_root,
            family,
            args.geo_id,
            **geometry_kwargs,
        )
        mesh_result = launch_meshing(
            overrides,
            data_root,
            leaf,
            study_id,
            max_retries,
        )
        if mesh_result.returncode != 0:
            return _exit_code(mesh_result.returncode)

    _entry, solver_template, solver_retries, settle_s = campaign.load_template(
        args.geo_id,
        production_mesh_id,
        case_id,
    )
    solver_overrides = solver_overrides_for_study(solver_template, study_id)
    run_directory = run_leaf(data_root, family, args.geo_id, study_id, case_id)
    refuse_existing_directory(run_directory, "run leaf")
    solver_result = campaign.launch_solver(
        solver_overrides,
        data_root,
        run_directory,
        geo_id=args.geo_id,
        mesh_id=study_id,
        run_id=case_id,
        max_retries=solver_retries,
        settle_s=settle_s,
    )
    if not batch_solver_sweep.solver_worker_succeeded(solver_result.returncode):
        return _exit_code(solver_result.returncode)

    extract_result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        _entry,
        geo_id=args.geo_id,
        mesh_id=study_id,
        run_id=case_id,
    )
    if extract_result.returncode != 0:
        return _exit_code(extract_result.returncode)

    summary = run_directory / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    print_study_report(study_report(leaf, study_id, run_directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
