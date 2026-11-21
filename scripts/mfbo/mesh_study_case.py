"""Mesh, solve, and extract one campaign case with mesh-knob overrides.

The production baseline is not rebuilt here. Re-extract that leaf with
``reextract_runs.py --copy-from-production``. This driver refuses a study
``mesh_id`` that matches the production mesh id.

Before the meshing worker starts, the production geometry file
``C:/ro_data/geometries/<family>/<geo_id>/<geo_id>.dsco`` is copied into
the study data root. An existing copy is reused only when its sha256
matches. The source is never moved. ``--geometry-suffix .pmdb`` with
``--geometry-root`` copies the converted file from that root instead
(never from ``C:/ro_data``), requires parity status ``passed`` or
``passed_without_volume``,
and passes ``geometry_suffix`` and ``geometry_root`` to the meshing child.
The default remains the production ``.dsco``.

``format_production_mesh_id`` encodes max/min/cpg/bl/peel and omits the
default first-height factor. A factor that differs from the template is
inserted as ``_fNNN`` (``round(factor * 100)``, so 0.40 is ``f040``) before
``_peel``, which is the token ``MESH_ID_RE`` already accepts. A spacer
layer count that differs from ``bl_layers`` is ``bl{membrane}s{spacer}``.
``--local-proximity`` and ``--local-face-size`` append ``_lpx…`` / ``_fs…``
after ``_peel``. ``--membrane-bl-y1-um`` appends ``_y1hNNN`` after those
(``round(um * 10)``, so 2.0 um is ``_y1h020``) and records the last-ratio
stack on the mesh manifest. ``--membrane-bl-layers`` pins that stack's
layer count. The token ``_mblN`` is added only when the pin differs from
the count ``m_max`` would choose. ``--bulk-max-size`` appends ``_bmsNNN``
after those (``round(mm * 1000)``, so 0.034 mm is ``_bms034``) and fills
the volume with polyhedra at that max cell length. Omitted, the volume
fill stays poly-hexcore. ``--max-aspect-ratio 250`` appends ``_ar250``
and uses a hard global ceiling without the prism override. The ceiling,
gate mode, and measured maximum are recorded in the mesh manifest.
``--mesh-only`` prints the mesh cell count and
the recorded membrane layer count, and does not solve.
``--mesh-spec`` checks the knobs against that family's entry and stamps
the file's SHA-256 and the git HEAD on the mesh and run manifests.
``--peel-layers`` overrides the template peel count.

``--reuse-mesh`` skips meshing when that mesh leaf already exists, its
``mesh_run_record.json`` status is ``SUCCESS``, and the manifest settings
match the request. An empty ``wall_spacer_labels`` request also requires
``spacer_boundary_layers`` ``skipped``. A request that has spacer walls
requires that field to be absent. The run leaf is still refused when it
exists.

``--init-from RUN_LEAF`` is a fresh solve on an existing mesh whose initial
field is that leaf's PASS final (same geometry and mesh id, final hashes
verified against its manifest). This run's settings and inlet velocity
apply; the solver reads a staged copy of the source data instead of
hybrid initialisation and resets the iteration counter, so the UDF ramp
restarts. An existing leaf for this run is moved aside
(``<run>.prev_<UTC>``) first; the manifest records ``init_from``.

``--require-mesh`` implies ``--reuse-mesh`` and fails before any Fluent
launch when the mesh leaf does not exist, so a solve never meshes.

``--checkpoint-every N`` writes case and data every N iterations during
the solve and keeps the latest two pairs. It is off when omitted.
After the extract succeeds and the run manifest gate is PASS, those
pairs and the setup case are deleted. The final case, final data,
``post/``, logs, and the manifest stay. A failed solve, a failed
extract, a failed metric check, or a gate that is not PASS keeps the
files so ``--resume`` can continue. ``--keep-checkpoints`` skips the
deletion. The leaf size is printed after that decision. ``job_queue``
runs this script, so a checkpointed queue job uses the same rule.
``--resume`` reuses the mesh and continues the latest checkpoint's
iteration counter. A resume with no run leaf, or a run leaf with no
checkpoint pair, starts a fresh solve and records ``resume_fallback``
``fresh`` on the manifest. A finished final pair is not deleted.
``--continue-iterations`` extends that final pair under the same stop
rule, writing ``post/reports_iter<N>/`` and an ``_iter_<N>`` case/data
pair every ``--continue-every`` iterations. ``post/reports`` is left
in place. The setup case is not a checkpoint.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import secrets
import shutil
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
from ro.paths import is_production_path, refuse_production_root  # noqa: E402
from ro.atomic_reports import utc_stamp
from ro.bulk_size import bulk_max_size_suffix
from ro.aspect_ceiling import aspect_ceiling_suffix
from ro.campaign_matrix import format_production_mesh_id
from ro.local_sizing import (
    local_sizing_suffix,
    parse_local_face_size,
    parse_local_proximity,
)
from ro.membrane_bl import (
    SPACER_BOUNDARY_LAYERS_SKIPPED,
    derived_membrane_layer_count,
    membrane_bl_suffix,
    membrane_layer_override_suffix,
    resolve_membrane_bl,
    specs_match,
)
from ro.convergence_quality import continuity_final_from_case_dir
from ro.init_from import resolve_init_from_leaf
from ro.convergence_stop import (
    CAMPAIGN_V3_PROFILE,
    campaign_v3_stop_overrides,
)
from ro.leaf_disk import cleanup_line, final_pair_ready, release_after_pass
from ro.mesh_common import load_mesh_run_record, parse_meshing_input_summary
from ro.paths import MESH_ID_RE, project_root
from ro.solve_checkpoint import (
    checkpoint_case_path,
    checkpoint_data_path,
    continue_absolute_targets,
    latest_checkpoint,
    refuse_finished_checkpoint,
    remove_checkpoints_except,
    require_checkpoint_every,
)
from ro.mesh_spec import (
    effective_mesh_settings,
    family_settings,
    legacy_manifest_mismatches,
    load_mesh_spec,
    provenance_fields,
    require_changes_match,
    resolve_spec_path,
)
from ro.solver_common import STOP_REASON_MAX_ITER_REACHED, sha256_file

_FLOAT_REL_TOL = 1.0e-9
_FLOAT_ABS_TOL = 1.0e-12
PRODUCTION_DATA_ROOT = Path("C:/ro_data")
ADDITIONAL_P6M_VELOCITIES = {
    "u0p05_p6M": 0.05,
    "u0p15_p6M": 0.15,
    "u0p25_p6M": 0.25,
}

MESH_PRINT_FIELDS = (
    "cell_count",
    "skewness_max",
    "ortho_min",
    "AR_max",
)
SUMMARY_PRINT_FIELDS = (
    "lmh_mass_balance",
    "pressure_drop_spacer_per_m",
    "cp_canon_live_window_avg",
    "cp_meanfirst_live_window_avg",
    "dead_zone_area_fraction_window",
    "cpc_window_avg_flux",
    "cp_canon_window_avg",
    "cp_q99_window_flux",
    "cp_q999_window_flux",
)


def resolve_data_root(value):
    """Return an absolute data root that is not C:/ro_data or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    refuse_production_root(text)
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"data root must be absolute, got {value!r}.")
        path = path.resolve()
    refuse_production_root(path)
    return path


def _is_windows_absolute(text):
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def require_base_case(case):
    return campaign.require_base_case(case)


def load_solver_template(geo_id, mesh_id, case_id):
    """Additional v3 speeds inherit every setting from the u0p2 p6M case."""
    velocity = ADDITIONAL_P6M_VELOCITIES.get(case_id)
    source_id = "u0p2_p6M" if velocity is not None else case_id
    entry, overrides, retries, settle_s = campaign.load_template(
        geo_id, mesh_id, source_id
    )
    if velocity is not None:
        changes = {
            "run_id": case_id,
            "case_name": case_id,
            "inlet_velocity_value": velocity,
        }
        entry = {**entry, **changes}
        overrides = {**overrides, **changes}
    return entry, overrides, retries, settle_s


def collect_overrides(
    *,
    m_max=None,
    m_min=None,
    m_cpg=None,
    bl_layers=None,
    bl_height_factor=None,
    spacer_bl_layers=None,
    local_proximity=None,
    local_face_size=None,
    membrane_bl_y1_um=None,
    membrane_bl_layers=None,
    bulk_max_size_mm=None,
    peel_layers=None,
    max_aspect_ratio=None,
):
    """Return the mesh knobs the caller set. At least one is required."""
    changes = {}
    if max_aspect_ratio is not None:
        aspect_ceiling_suffix(max_aspect_ratio)
        changes["max_aspect_ratio_threshold"] = float(max_aspect_ratio)
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
    if local_proximity is not None:
        changes["local_proximity"] = parse_local_proximity(local_proximity)
    if local_face_size is not None:
        changes["local_face_size"] = parse_local_face_size(local_face_size)
    if membrane_bl_y1_um is not None:
        _require_positive("membrane_bl_y1_um", membrane_bl_y1_um)
        changes["membrane_bl_y1_um"] = float(membrane_bl_y1_um)
    if membrane_bl_layers is not None:
        _require_positive_int("membrane_bl_layers", membrane_bl_layers)
        if membrane_bl_y1_um is None:
            raise ValueError(
                "membrane_bl_layers requires membrane_bl_y1_um."
            )
        changes["membrane_bl_layers"] = int(membrane_bl_layers)
    if bulk_max_size_mm is not None:
        _require_positive("bulk_max_size_mm", bulk_max_size_mm)
        changes["bulk_max_size_mm"] = float(bulk_max_size_mm)
        bulk_max_size_suffix(changes["bulk_max_size_mm"])
    if peel_layers is not None:
        _require_nonnegative_int("peel_layers", peel_layers)
        changes["peel_layers"] = int(peel_layers)
    if not changes:
        raise ValueError(
            "At least one mesh override is required "
            "(--m-max, --m-min, --m-cpg, --bl-layers, "
            "--bl-first-height-factor, --spacer-bl-layers, "
            "--local-proximity, --local-face-size, "
            "--membrane-bl-y1-um, --membrane-bl-layers, "
            "--bulk-max-size, --peel-layers, --max-aspect-ratio). "
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


def _require_nonnegative_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0, got {value!r}.")


def default_bl_growth_rate():
    """``run_config.bl_growth_rate``. The production template does not set it."""
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "mesh_study_run_config_growth",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return float(module.bl_growth_rate)


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
    local_proximity=None,
    local_face_size=None,
    membrane_bl_y1_um=None,
    membrane_bl_layers=None,
    bulk_max_size_mm=None,
    bl_growth_rate=None,
    max_aspect_ratio=None,
):
    """Production formatter, plus ``_fNNN``, local-sizing, ``_y1h``, ``_mbl``, and ``_bms``."""
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
    mesh_id += local_sizing_suffix(local_proximity, local_face_size)
    mesh_id += membrane_bl_suffix(membrane_bl_y1_um)
    if membrane_bl_layers is not None:
        if membrane_bl_y1_um is None:
            raise ValueError("membrane_bl_layers requires membrane_bl_y1_um.")
        growth = (
            default_bl_growth_rate()
            if bl_growth_rate is None
            else float(bl_growth_rate)
        )
        derived = derived_membrane_layer_count(
            membrane_bl_y1_um,
            size_mm=m_max,
            bl_layers=bl_layers,
            growth_rate=growth,
        )
        mesh_id += membrane_layer_override_suffix(membrane_bl_layers, derived)
    mesh_id += bulk_max_size_suffix(bulk_max_size_mm)
    mesh_id += aspect_ceiling_suffix(max_aspect_ratio)
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
    if "local_proximity" in overrides:
        overrides["local_proximity"] = parse_local_proximity(
            overrides["local_proximity"]
        )
    if "local_face_size" in overrides:
        overrides["local_face_size"] = parse_local_face_size(
            overrides["local_face_size"]
        )
    factor = float(
        overrides.get("bl_height_factor", template_bl_height_factor(template))
    )
    m_max = float(overrides["m_max"])
    m_min = float(overrides["m_min"])
    if m_min > m_max:
        raise ValueError(
            f"m_min must be <= m_max. m_min={m_min!r}, m_max={m_max!r}."
        )
    growth = overrides.get("bl_growth_rate", default_bl_growth_rate())
    mesh_id = study_mesh_id(
        m_max=m_max,
        m_min=m_min,
        m_cpg=int(overrides["m_cpg"]),
        bl_layers=int(overrides["bl_layers"]),
        peel_layers=int(overrides["peel_layers"]),
        bl_height_factor=factor,
        template_factor=template_bl_height_factor(template),
        spacer_bl_layers=overrides.get("spacer_bl_layers"),
        local_proximity=overrides.get("local_proximity"),
        local_face_size=overrides.get("local_face_size"),
        membrane_bl_y1_um=overrides.get("membrane_bl_y1_um"),
        membrane_bl_layers=overrides.get("membrane_bl_layers"),
        bulk_max_size_mm=overrides.get("bulk_max_size_mm"),
        bl_growth_rate=growth,
        max_aspect_ratio=overrides.get("max_aspect_ratio_threshold"),
    )
    layer_count = overrides.pop("membrane_bl_layers", None)
    if overrides.get("membrane_bl_y1_um") is not None:
        y1_um = overrides.pop("membrane_bl_y1_um")
        labels = overrides.get("active_membrane_wall_labels")
        if not labels:
            labels = ["wall_top_mem", "wall_bottom_mem"]
        overrides["membrane_bl_y1"] = resolve_membrane_bl(
            y1_um,
            size_mm=m_max,
            bl_layers=int(overrides["bl_layers"]),
            growth_rate=growth,
            labels=labels,
            layers=layer_count,
        )
    elif layer_count is not None:
        raise ValueError("membrane_bl_layers requires membrane_bl_y1_um.")
    require_distinct_mesh_id(mesh_id, template["mesh_id"])
    overrides["mesh_id"] = mesh_id
    overrides["case_name"] = mesh_id
    if "max_aspect_ratio_threshold" in changes:
        # This study uses the measured global maximum, including prism cells.
        overrides["max_prism_aspect_ratio_threshold"] = None
        overrides["fail_if_quality_not_parsed"] = True
    if "bl_height_factor" in changes:
        overrides["bl_height_factor"] = factor
    return overrides


def _membrane_bl_equal(recorded, requested):
    if recorded is None and requested is None:
        return True
    if not isinstance(recorded, dict) or not isinstance(requested, dict):
        return False
    return specs_match(recorded, requested)


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
    return is_production_path(str(text))


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


_ACCEPTED_PARITY_STATUSES = ("passed", "passed_without_volume")


def require_passed_parity(geometry_root, geo_id):
    """Raise unless parity status is passed or passed_without_volume."""
    path = parity_record_path(geometry_root, geo_id)
    if not path.is_file():
        raise FileNotFoundError(f"Parity record not found: {path}")
    payload = _read_json_object(path)
    status = payload.get("status")
    passed = payload.get("geo_id") == geo_id and status in _ACCEPTED_PARITY_STATUSES
    if status == "passed":
        passed = passed and payload.get("passed") is True
    if not passed:
        raise RuntimeError(
            "Parity record did not pass for "
            f"{geo_id}: status {payload.get('status')!r}, "
            f"passed {payload.get('passed')!r}, path {path}."
        )
    return path


def _under_production_tree(path):
    return is_production_path(path)


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
    requires parity status ``passed`` or ``passed_without_volume``.

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


def require_reusable_mesh(
    mesh_directory, overrides, geometry_file, geometry_sha256, *, spec_effective=None
):
    """Raise unless ``mesh_directory`` is a successful mesh of these settings.

    Success is ``mesh_run_record.json`` status ``SUCCESS`` together with a
    manifest object. Compared settings are ``m_max``, ``m_min``, ``m_cpg``,
    ``bl_layers``, ``spacer_bl_layers``, ``peel_layers``, and the geometry
    SHA-256. An empty ``wall_spacer_labels`` list requires
    ``spacer_boundary_layers`` ``skipped``. Spacer walls require that field
    to be absent, which is how every existing spacer mesh is recorded.
    ``geometry_sha256`` is the hash this job requires. A manifest
    field ``geometry_sha256`` is the recorded hash when present; otherwise
    the hash of ``geometry_file`` is the recorded hash.

    Mesh spec: a manifest with ``mesh_spec_effective_sha256`` must equal the
    requested fingerprint; the whole-file ``mesh_spec_sha256`` is not
    compared then. A manifest with only the whole-file hash is reused when
    that hash is unchanged, or when its recorded parameters equal
    ``spec_effective`` (the settings the current spec resolves to for this
    geometry); that acceptance is printed. Any differing parameter refuses.
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
    requested_ar = overrides.get("max_aspect_ratio_threshold")
    if requested_ar is not None:
        recorded_ar = manifest.get("max_aspect_ratio_threshold")
        if recorded_ar != requested_ar or manifest.get("aspect_ratio_gate") != "hard":
            mismatches.append("max_aspect_ratio_threshold or aspect_ratio_gate differs")
        measured = manifest.get("AR_max")
        if (
            isinstance(measured, bool)
            or not isinstance(measured, (int, float))
            or not math.isfinite(measured)
            or measured <= 0
            or measured > requested_ar
            or manifest.get("max_prism_aspect_ratio_threshold") is not None
        ):
            mismatches.append("AR_max does not establish the requested hard global ceiling")
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
    for key in ("local_proximity", "local_face_size", "membrane_bl_y1"):
        recorded = manifest[key] if key in manifest else None
        requested = overrides[key] if key in overrides else None
        if key == "membrane_bl_y1":
            matches = _membrane_bl_equal(recorded, requested)
        else:
            matches = _values_equal(recorded, requested)
        if not matches:
            mismatches.append(
                f"{key}: recorded {_setting_text(recorded)}, "
                f"requested {_setting_text(requested)}"
            )
    recorded_bulk = (
        manifest["bulk_max_size_mm"] if "bulk_max_size_mm" in manifest else None
    )
    requested_bulk = overrides.get("bulk_max_size_mm")
    if not _values_equal(recorded_bulk, requested_bulk):
        mismatches.append(
            "bulk_max_size_mm: recorded "
            f"{_setting_text(recorded_bulk)}, "
            f"requested {_setting_text(requested_bulk)}"
        )
    spec_note = None
    if "mesh_spec_sha256" in overrides:
        recorded_spec = manifest.get("mesh_spec_sha256")
        requested_spec = overrides["mesh_spec_sha256"]
        recorded_effective = manifest.get("mesh_spec_effective_sha256")
        requested_effective = overrides.get("mesh_spec_effective_sha256")
        if recorded_effective is not None:
            if recorded_effective != requested_effective:
                mismatches.append(
                    "mesh_spec_effective_sha256: recorded "
                    f"{_setting_text(recorded_effective)}, "
                    f"requested {_setting_text(requested_effective)}"
                )
        elif recorded_spec == requested_spec:
            pass
        elif recorded_spec is None or spec_effective is None:
            mismatches.append(
                "mesh_spec_sha256: recorded "
                f"{_setting_text(recorded_spec)}, "
                f"requested {_setting_text(requested_spec)}"
            )
        else:
            differences = legacy_manifest_mismatches(manifest, spec_effective)
            if differences:
                mismatches.append(
                    "mesh_spec_sha256: recorded "
                    f"{_setting_text(recorded_spec)}, "
                    f"requested {_setting_text(requested_spec)}, and the "
                    "effective parameters differ: " + "; ".join(differences)
                )
            else:
                spec_note = (
                    "Mesh spec: accepted on effective parameters. The recorded "
                    f"whole-file mesh_spec_sha256 {recorded_spec} differs from the "
                    f"current {requested_spec}, but every recorded mesh parameter "
                    "equals what the current spec resolves to for this geometry: "
                    + json.dumps(spec_effective, sort_keys=True)
                )
    labels = overrides.get("wall_spacer_labels", _MISSING)
    if isinstance(labels, (list, tuple)):
        recorded_skip = manifest.get("spacer_boundary_layers", _MISSING)
        if len(labels) == 0:
            if recorded_skip != SPACER_BOUNDARY_LAYERS_SKIPPED:
                mismatches.append(
                    "spacer_boundary_layers: recorded "
                    f"{_setting_text(recorded_skip)}, "
                    f"requested {SPACER_BOUNDARY_LAYERS_SKIPPED!r}"
                )
        elif recorded_skip is not _MISSING:
            mismatches.append(
                "spacer_boundary_layers: recorded "
                f"{_setting_text(recorded_skip)}, requested absent"
            )
    if mismatches:
        raise ValueError(
            f"Refusing to reuse mesh leaf {directory}: " + "; ".join(mismatches) + "."
        )
    if spec_note is not None:
        print(spec_note, flush=True)
    return manifest


def run_spec_provenance(provenance, mesh_manifest):
    """Spec provenance for the solve on a reused mesh.

    The run records the mesh's spec hash (the solver refuses a run hash that
    differs from the mesh manifest). When reuse was accepted on effective
    parameters, the mesh's whole-file hash replaces the current one.
    """
    recorded = mesh_manifest.get("mesh_spec_sha256") if isinstance(mesh_manifest, dict) else None
    if not provenance or not recorded or recorded == provenance.get("mesh_spec_sha256"):
        return provenance
    updated = dict(provenance)
    updated["mesh_spec_sha256"] = recorded
    print(
        f"Run mesh_spec_sha256 set to the reused mesh's {recorded} "
        f"(current spec file {provenance.get('mesh_spec_sha256')}).",
        flush=True,
    )
    return updated


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
        runner=batch_meshing.guarded_meshing_runner(
            Path(mesh_directory) / f"mesh_log_{mesh_id}.txt"
        ),
    )
    if result is None:
        raise RuntimeError("Meshing worker did not return a process result.")
    print(
        f"Meshing return code {result.returncode}, attempts={attempts}, "
        f"retry_kinds={retry_kinds}"
    )
    return result


def convergence_profile_overrides(profile):
    """Empty for the production stop. ``campaign_v3`` tightens it."""
    if profile is None:
        return {}
    if profile != CAMPAIGN_V3_PROFILE:
        raise ValueError(
            f"Unknown convergence profile {profile!r}. "
            f"Expected {CAMPAIGN_V3_PROFILE!r}."
        )
    path = project_root() / "configs" / "run_config.py"
    cfg = batch_meshing._load_module("mesh_study_run_config_stop", path)
    return campaign_v3_stop_overrides(
        stop_criterion=cfg.campaign_v3_qoi_stop_criterion,
        window=cfg.campaign_v3_qoi_previous_values_to_consider,
        initial_values_to_ignore=cfg.qoi_initial_values_to_ignore,
        ramp_full_iteration=cfg.ramp_full_iteration,
        residual_target=cfg.residual_target,
        max_iterations=cfg.max_iterations,
    )


def inherit_recorded_stop_rule(run_directory, solver_overrides):
    """Keep an existing leaf on the stop rule it was solved with.

    A continue or a checkpoint resume extends a leaf; switching CP in or out
    of its QoI stop part-way would leave one leaf under two rules. The
    leaf's recorded ``qoi_stop_include_cp`` wins and is printed. A leaf whose
    recorded stop used ``cp_live`` (infeasible in Fluent 25.1) refuses.
    """
    manifest_path = Path(run_directory) / "manifest.json"
    if not manifest_path.is_file():
        return solver_overrides
    manifest = _read_json(manifest_path)
    reports = manifest.get("qoi_stop_reports") or []
    if "cp_live" in reports or manifest.get("cp_monitor_name") == "cp_live":
        raise ValueError(
            f"{run_directory} was solved with cp_live in its stop; Fluent 25.1 "
            "cannot evaluate cp_live. Refusing to extend it; inspect the leaf."
        )
    recorded = manifest.get("qoi_stop_include_cp")
    if not isinstance(recorded, bool):
        return solver_overrides
    requested = bool(solver_overrides.get("qoi_stop_include_cp", False))
    if recorded != requested:
        print(
            f"Stop rule inherited from {manifest_path}: qoi_stop_include_cp="
            f"{recorded} (this command alone would use {requested}).",
            flush=True,
        )
    solver_overrides["qoi_stop_include_cp"] = recorded
    return solver_overrides


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


def mesh_cell_count(mesh_directory):
    """Cell count recorded on the mesh manifest after a successful mesh."""
    manifest_path = Path(mesh_directory) / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Mesh manifest not found: {manifest_path}")
    count = _read_json(manifest_path).get("cell_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise RuntimeError(
            "Mesh manifest cell_count is not a positive integer: "
            f"{count!r} ({manifest_path})."
        )
    return count


def membrane_layers_text(mesh_directory):
    """Resolved membrane layer count, or ``unset`` when the stack is absent."""
    manifest = _read_json(Path(mesh_directory) / "manifest.json")
    spec = manifest.get("membrane_bl_y1")
    if not isinstance(spec, dict) or "layers" not in spec:
        return "unset"
    return str(spec["layers"])


def print_mesh_cell_count(mesh_directory, mesh_id):
    """Print the cell count and the recorded membrane layer count."""
    count = mesh_cell_count(mesh_directory)
    layers = membrane_layers_text(mesh_directory)
    print(
        f"Mesh cell count before solve: {mesh_id} {count} "
        f"membrane_bl_layers={layers}"
    )
    return count


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
        "--local-proximity",
        default=None,
        help=(
            "JSON object: pairs of label lists, cells_per_gap, min_size_mm. "
            "Encoded as _lpx{cpg}m{min} after _peel."
        ),
    )
    parser.add_argument(
        "--local-face-size",
        default=None,
        help=(
            "JSON object: labels, size_mm, growth_rate. "
            "Encoded as _fs{size} after _peel (gNNN when growth is not 1.2)."
        ),
    )
    parser.add_argument(
        "--membrane-bl-y1-um",
        type=float,
        default=None,
        help=(
            "Target membrane wall-to-centroid y1 in micrometres. "
            "Encoded as _y1hNNN after _peel (2.0 um is _y1h020). "
            "Last-ratio on the membrane labels; spacer BL unchanged."
        ),
    )
    parser.add_argument(
        "--membrane-bl-layers",
        type=int,
        default=None,
        help=(
            "Pin the last-ratio membrane layer count. Requires "
            "--membrane-bl-y1-um. Omitted, the count follows m_max. "
            "A count that differs adds _mblN after _y1h."
        ),
    )
    parser.add_argument(
        "--bulk-max-size",
        type=float,
        default=None,
        help=(
            "Polyhedra max cell length in mm. Omitted, the volume fill "
            "stays poly-hexcore. Encoded as _bmsNNN after _y1h "
            "(0.034 mm is _bms034). Surface sizing and the membrane "
            "stack are unchanged. Growth rate stays 1.2."
        ),
    )
    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Mesh, print the cell count, and do not solve or extract.",
    )
    parser.add_argument(
        "--reuse-mesh",
        action="store_true",
        help=(
            "Reuse an existing mesh leaf when its mesh record is SUCCESS "
            "and the recorded settings match. The run leaf must not exist."
        ),
    )
    parser.add_argument(
        "--init-from",
        default=None,
        metavar="RUN_LEAF",
        help=(
            "Fresh solve initialised from this run leaf's PASS final (same data "
            "root, geometry and mesh id; final hashes verified). This run's "
            "settings and inlet velocity apply; the iteration counter and UDF "
            "ramp restart. An existing leaf for this run is moved aside first."
        ),
    )
    parser.add_argument(
        "--require-mesh",
        action="store_true",
        help=(
            "Fail before any Fluent launch when the mesh leaf does not exist "
            "(implies --reuse-mesh; never meshes). Campaign main-queue solves "
            "pass it."
        ),
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=None,
        help=(
            "Write case and data every N iterations and keep the latest "
            "two pairs. Default off. 200 is the interval for a long "
            "mesh-study solve."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Continue the existing run from its latest checkpoint with "
            "the same settings. The iteration counter is the one in the "
            "data file. No run leaf, or no checkpoint pair, starts a "
            "fresh solve and records resume_fallback=fresh. A finished "
            "final pair is not deleted."
        ),
    )
    parser.add_argument(
        "--continue-iterations",
        nargs="?",
        const=2000,
        type=int,
        default=None,
        help=(
            "Continue a finished leaf from its final case/data for up to "
            "N more iterations (default 2000 when the flag is present). "
            "The campaign stop rule still applies. Every --continue-every "
            "iterations, save a case/data pair and extract into "
            "post/reports_iter<N>/. post/reports is kept."
        ),
    )
    parser.add_argument(
        "--continue-every",
        type=int,
        default=500,
        help=(
            "Iteration interval for a continue snapshot and extract. "
            "Default 500."
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help=(
            "Override the solve iteration cap. A value inside the flux "
            "ramp runs that many iterations with residual and QoI "
            "stopping left off."
        ),
    )
    cp_live = parser.add_mutually_exclusive_group()
    cp_live.add_argument(
        "--no-cp-live",
        action="store_true",
        help=(
            "Accepted for old queue files; the default already has no live "
            "CP report and keeps CP out of the campaign_v3 stop."
        ),
    )
    cp_live.add_argument(
        "--cp-live",
        action="store_true",
        help=(
            "Create the solve-time cp_live report and put it in the campaign_v3 "
            "stop. Infeasible in Fluent 25.1 (named expressions cannot read "
            "UDM fields); the expression check refuses it."
        ),
    )
    parser.add_argument(
        "--convergence-profile",
        default=None,
        choices=(CAMPAIGN_V3_PROFILE,),
        help=(
            "campaign_v3 tightens the QoI stop to 1e-4 on LMH, spacer "
            "pressure drop, and CP, with the 100-iteration window starting "
            "after the flux ramp. Omitted, the production 1e-3 LMH and "
            "pressure-drop stop is unchanged."
        ),
    )
    parser.add_argument(
        "--keep-checkpoints",
        action="store_true",
        help=(
            "Keep checkpoint pairs and the setup case after a PASS "
            "extract. Default deletes them once the extract succeeds."
        ),
    )
    parser.add_argument(
        "--geometry-suffix",
        default=".dsco",
        help="CAD suffix copied into the study tree. Default .dsco.",
    )
    parser.add_argument(
        "--peel-layers",
        type=int,
        default=None,
        help="Peel layer count. Omitted, the production template value is kept.",
    )
    parser.add_argument(
        "--max-prism-aspect-ratio",
        type=float,
        default=None,
        help=(
            "Prism-zone Fluent aspect-ratio ceiling. Omitted, the global "
            "ceiling is the only aspect-ratio check. Bulk zones keep that "
            "global ceiling. Recorded on the mesh manifest. Does not change "
            "the mesh id."
        ),
    )
    parser.add_argument(
        "--max-aspect-ratio", type=int, default=None,
        help=(
            "Hard global aspect-ratio ceiling for a sensitivity mesh. "
            "Adds _arN to its mesh id; orthogonal quality stays a hard limit. "
            "Cannot combine with --max-prism-aspect-ratio."
        ),
    )
    parser.add_argument(
        "--mesh-spec",
        default=None,
        help=(
            "Campaign mesh spec JSON. The knobs must match this geometry's "
            "family. The file SHA-256 and git HEAD are written on the manifests."
        ),
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


def _json_object(flag, text):
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{flag} is not valid JSON: {exc}") from exc
    return value


def move_leaf_aside(run_directory, *, now=None):
    """Rename a leaf to ``<name>.prev_<UTC>``. Never deletes; refuses a clash."""
    run_directory = Path(run_directory)
    refuse_production_root(run_directory)
    aside = run_directory.with_name(f"{run_directory.name}.prev_{utc_stamp(now)}")
    if aside.exists():
        raise FileExistsError(f"Refusing to replace moved-aside leaf: {aside}")
    run_directory.rename(aside)
    return aside


def report_dirname_for_iteration(iteration):
    """Extract directory name ``reports_iter<N>`` under ``post/``."""
    if isinstance(iteration, bool) or not isinstance(iteration, int) or iteration < 1:
        raise ValueError(
            f"iteration must be an integer >= 1, got {iteration!r}."
        )
    return f"reports_iter{iteration}"


def _positive_count(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return value


def _final_paths(run_directory, geo_id, case_id):
    directory = Path(run_directory)
    return (
        directory / f"{geo_id}_{case_id}_final.cas.h5",
        directory / f"{geo_id}_{case_id}_final.dat.h5",
    )


_PAIR_COPY_TMP = ".copy_tmp"
_PAIR_COPY_BAK = ".copy_bak"


def _pair_copy_leftovers(dest):
    """``*.copy_tmp*`` / ``*.copy_bak*`` files an earlier copy left next to ``dest``."""
    dest = Path(dest)
    if not dest.parent.is_dir():
        return []
    found = []
    for marker in (_PAIR_COPY_TMP, _PAIR_COPY_BAK):
        found.extend(dest.parent.glob(dest.name + marker + "*"))
    return sorted(found)


def require_pair_copy_clean(dest_case):
    """Refuse a pair copy into ``dest_case`` that an earlier copy left unfinished.

    An interrupted swap can leave the canonical pair absent or mismatched
    (one file new, one old) with the previous file under ``*.copy_bak*``.
    Exactly one of case/data present, or any leftover backup, refuses; the
    leaf is inspected by hand rather than repaired here. A leftover
    ``*.copy_tmp*`` with an intact pair is only a partial copy and is removed.
    """
    dest_case = Path(dest_case)
    dest_data = checkpoint_data_path(dest_case)
    leftovers = _pair_copy_leftovers(dest_case) + _pair_copy_leftovers(dest_data)
    backups = [path for path in leftovers if _PAIR_COPY_BAK in path.name]
    present = [path.exists() for path in (dest_case, dest_data)]
    if backups or present[0] != present[1]:
        state = (
            "canonical pair complete" if all(present)
            else f"{'case' if present[0] else 'data'} present, "
                 f"{'data' if present[0] else 'case'} absent"
            if any(present) else "canonical pair absent"
        )
        raise RuntimeError(
            f"An earlier case/data copy into {dest_case} did not finish ({state}); "
            f"the pair may be absent or mismatched. Leftovers: "
            f"{[str(path) for path in leftovers] or 'none'}. Restore the pair from "
            "the *.copy_bak* files or a snapshot by hand, then rerun."
        )
    for path in leftovers:
        path.unlink()


def _copy_case_data(source_case, source_data, dest_case):
    """Copy a case/data pair, both or neither. The data name matches the case stem.

    Both files are copied to ``*.copy_tmp_<id>`` first (``<id>`` is unique to
    this call). Existing destinations are moved to ``*.copy_bak_<id>`` and
    the temporaries swapped in; a failure at any step puts the previous pair
    back and removes the temporaries. ``require_pair_copy_clean`` runs first,
    so a copy interrupted between those moves is detected, not built on.
    """
    dest_case = Path(dest_case)
    if not str(dest_case).endswith(".cas.h5"):
        raise ValueError(f"destination case must end in .cas.h5, got {dest_case}.")
    dest_data = checkpoint_data_path(dest_case)
    require_pair_copy_clean(dest_case)
    token = f"{utc_stamp()}_{os.getpid()}_{secrets.token_hex(4)}"
    pairs = [(Path(source_case), dest_case), (Path(source_data), dest_data)]
    temps = [dest.with_name(f"{dest.name}{_PAIR_COPY_TMP}_{token}") for _src, dest in pairs]
    backups = []
    placed = []
    try:
        for (source, _dest), temp in zip(pairs, temps):
            shutil.copyfile(source, temp)
        for _source, dest in pairs:
            if dest.exists():
                backup = dest.with_name(f"{dest.name}{_PAIR_COPY_BAK}_{token}")
                os.replace(dest, backup)
                backups.append((backup, dest))
        for (_source, dest), temp in zip(pairs, temps):
            os.replace(temp, dest)
            placed.append(dest)
    except BaseException:
        for dest in placed:
            if dest.exists() and not any(dest == original for _b, original in backups):
                dest.unlink()
        for backup, dest in backups:
            os.replace(backup, dest)
        raise
    finally:
        for temp in temps:
            if temp.exists():
                temp.unlink()
    for backup, _dest in backups:
        backup.unlink()
    return dest_case, dest_data


def _pair_hashes(case_path):
    case_path = Path(case_path)
    return sha256_file(case_path), sha256_file(checkpoint_data_path(case_path))


def _pair_matches(left_case, right_case):
    return _pair_hashes(left_case) == _pair_hashes(right_case)


# A chunk that ends at the iteration it started from may still rewrite the
# final (Fluent writes it again). The driver records that rewrite here, with
# the hashes of the pair the chunk read and of the pair it wrote. Only this
# record allows a snapshot to be copied over a different final.
REWRITE_IDENTITY_FILENAME = "continue_rewrite_identity.json"


def record_zero_iteration_rewrite(run_directory, iteration, source_hashes, final_case):
    """Write the zero-iteration rewrite identity; returns the record."""
    rewritten = _pair_hashes(final_case)
    record = {
        "iteration": int(iteration),
        "iterations_advanced": 0,
        "source_case_sha256": source_hashes[0],
        "source_data_sha256": source_hashes[1],
        "rewritten_final_case_sha256": rewritten[0],
        "rewritten_final_data_sha256": rewritten[1],
        "recorded_at_utc": utc_stamp(),
    }
    path = Path(run_directory) / REWRITE_IDENTITY_FILENAME
    refuse_production_root(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return record


def zero_iteration_rewrite_proves(run_directory, iteration, snapshot_hashes, final_hashes):
    """None when the record proves the final is a zero-iteration rewrite of
    the snapshot at ``iteration``; otherwise the reason it does not."""
    path = Path(run_directory) / REWRITE_IDENTITY_FILENAME
    if not path.is_file():
        return f"no {REWRITE_IDENTITY_FILENAME}"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"{REWRITE_IDENTITY_FILENAME} unreadable: {exc}"
    expected = {
        "iteration": int(iteration),
        "iterations_advanced": 0,
        "source_case_sha256": snapshot_hashes[0],
        "source_data_sha256": snapshot_hashes[1],
        "rewritten_final_case_sha256": final_hashes[0],
        "rewritten_final_data_sha256": final_hashes[1],
    }
    wrong = [key for key, value in expected.items() if record.get(key) != value]
    if wrong:
        return f"{REWRITE_IDENTITY_FILENAME} does not match: " + ", ".join(wrong)
    return None


def _keep_or_restore_final(final_case, final_data, snapshot, iteration):
    """Keep an existing iteration snapshot; never overwrite a different final blind.

    A snapshot equal to the final is kept. A snapshot that differs is
    copied over the final only when ``continue_rewrite_identity.json``
    proves the final is a zero-iteration rewrite of exactly that snapshot.
    Otherwise this refuses: the final may hold iterations the manifest
    does not count, and copying would destroy them.
    """
    snapshot = Path(snapshot)
    if snapshot.is_file() and checkpoint_data_path(snapshot).is_file():
        snapshot_hashes = _pair_hashes(snapshot)
        final_hashes = _pair_hashes(final_case)
        if snapshot_hashes == final_hashes:
            print(f"Continue snapshot at iteration {iteration} kept: {snapshot}")
            return
        reason = zero_iteration_rewrite_proves(
            Path(final_case).parent, iteration, snapshot_hashes, final_hashes
        )
        if reason is not None:
            raise RuntimeError(
                f"Refusing to copy the iteration {iteration} snapshot {snapshot} over "
                f"the final {final_case}: the pair differs and {reason}. Inspect the "
                "leaf: the final may hold iterations the manifest does not count."
            )
        print(
            f"Restoring the final from the iteration {iteration} snapshot "
            f"{snapshot}: the recorded zero-iteration rewrite matches both pairs."
        )
        _copy_case_data(snapshot, checkpoint_data_path(snapshot), final_case)
        return
    _copy_case_data(final_case, final_data, snapshot)
    print(f"Continue snapshot at iteration {iteration}: {snapshot}")


def _iteration_from_manifest(run_directory):
    path = Path(run_directory) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Run manifest not found: {path}.")
    payload = _read_json(path)
    value = payload.get("iterations_run")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"continue requires iterations_run >= 1 in {path}, got {value!r}."
        )
    return value, payload.get("stop_reason")


def _extract_dirname(
    data_root,
    run_directory,
    entry,
    *,
    geo_id,
    mesh_id,
    run_id,
    dirname,
):
    """Extract into ``post/<dirname>/`` and validate the wide summary."""
    result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        entry,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
        report_dirname=dirname,
    )
    if result.returncode != 0:
        return _exit_code(result.returncode)
    summary = Path(run_directory) / "post" / dirname / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    return 0


def continue_finished_leaf(
    *,
    data_root,
    run_directory,
    entry,
    solver_overrides,
    geo_id,
    mesh_id,
    case_id,
    max_retries,
    settle_s,
    additional,
    every,
    keep_checkpoints,
):
    """Extend a finished final pair and extract every ``every`` iterations.

    The first extract is the saved final, written to
    ``post/reports_iter<start>/``. ``post/reports`` is not renamed or deleted.
    Each later chunk reads the final pair as a checkpoint, raises the
    iteration cap by one interval, then saves ``*_iter_<N>`` and extracts.
    """
    additional = _positive_count("continue-iterations", additional)
    every = _positive_count("continue-every", every)
    if not final_pair_ready(run_directory, geo_id, case_id):
        raise FileNotFoundError(
            "Continue requires the final case+data pair in "
            f"{run_directory}."
        )
    start, _ignored = _iteration_from_manifest(run_directory)
    final_case, final_data = _final_paths(run_directory, geo_id, case_id)
    snapshot = (
        Path(run_directory) / f"{geo_id}_{case_id}_iter_{start:06d}.cas.h5"
    )
    _keep_or_restore_final(final_case, final_data, snapshot, start)
    code = _extract_dirname(
        data_root,
        run_directory,
        entry,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=case_id,
        dirname=report_dirname_for_iteration(start),
    )
    if code:
        return code
    limits = continue_absolute_targets(start, additional, every)
    overall = limits["overall_limit"]
    current = start
    for target in limits["chunk_targets"]:
        removed = remove_checkpoints_except(
            run_directory, geo_id, case_id, current
        )
        for path in removed:
            print(f"Removed stale continue checkpoint: {path}")
        checkpoint = checkpoint_case_path(
            run_directory, geo_id, case_id, current
        )
        _copy_case_data(final_case, final_data, checkpoint)
        print(
            f"Continue checkpoint at iteration {current}: {checkpoint}"
        )
        # The pair this chunk reads; a zero-iteration rewrite is recorded
        # against it.
        chunk_source_hashes = _pair_hashes(checkpoint)
        chunk_overrides = dict(solver_overrides)
        chunk_overrides["checkpoint_resume"] = True
        # Absolute end for this chunk, not the previous cap and not the
        # additional count. The loaded case still says 2000.
        chunk_overrides["max_iterations"] = target
        chunk_overrides["continue_iteration_target"] = target
        chunk_overrides["continue_overall_limit"] = overall
        chunk_overrides["continue_start"] = start
        chunk_overrides["continue_every"] = every
        chunk_overrides["continue_from_iteration"] = current
        print(
            f"Continue {run_directory} from iteration {current} "
            f"toward {target} (overall limit {overall})."
        )
        solver_result = campaign.launch_solver(
            chunk_overrides,
            data_root,
            run_directory,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=case_id,
            max_retries=max_retries,
            settle_s=settle_s,
        )
        if not batch_solver_sweep.solver_worker_succeeded(solver_result.returncode):
            return _exit_code(solver_result.returncode)
        new_iteration, stop_reason = _iteration_from_manifest(run_directory)
        if new_iteration <= current:
            saved = (
                Path(run_directory)
                / f"{geo_id}_{case_id}_iter_{current:06d}.cas.h5"
            )
            if new_iteration == current:
                record = record_zero_iteration_rewrite(
                    run_directory, current, chunk_source_hashes, final_case
                )
                print(
                    f"Recorded zero-iteration rewrite at iteration {current} in "
                    f"{REWRITE_IDENTITY_FILENAME}."
                )
                if saved.is_file() and checkpoint_data_path(saved).is_file():
                    saved_hashes = _pair_hashes(saved)
                    rewritten = (
                        record["rewritten_final_case_sha256"],
                        record["rewritten_final_data_sha256"],
                    )
                    if saved_hashes == rewritten:
                        print(f"Final already equals {saved}.")
                    elif zero_iteration_rewrite_proves(
                        run_directory, current, saved_hashes, rewritten
                    ) is None:
                        _copy_case_data(
                            saved, checkpoint_data_path(saved), final_case
                        )
                        print(
                            f"Restored the final from {saved}. Continue stayed at "
                            f"iteration {new_iteration} and must not leave a "
                            "different final in place."
                        )
                    else:
                        print(
                            f"NOT restoring the final from {saved}: that snapshot "
                            "is not the pair this chunk read. Final left as written."
                        )
            else:
                print(
                    f"NOT restoring the final: the manifest went back from "
                    f"{current} to {new_iteration}. Final left as written."
                )
            raise RuntimeError(
                "Continue did not advance the iteration counter: "
                f"{current} -> {new_iteration}."
            )
        final_case, final_data = _final_paths(run_directory, geo_id, case_id)
        snapshot = (
            Path(run_directory)
            / f"{geo_id}_{case_id}_iter_{new_iteration:06d}.cas.h5"
        )
        _copy_case_data(final_case, final_data, snapshot)
        print(f"Continue snapshot at iteration {new_iteration}: {snapshot}")
        code = _extract_dirname(
            data_root,
            run_directory,
            entry,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=case_id,
            dirname=report_dirname_for_iteration(new_iteration),
        )
        if code:
            return code
        outcome = release_after_pass(
            run_directory,
            geo_id,
            case_id,
            keep=keep_checkpoints,
        )
        for error in outcome["errors"]:
            print(f"CHECKPOINT_CLEANUP_FAILED: {error}")
        print(cleanup_line(outcome))
        current = new_iteration
        if stop_reason != STOP_REASON_MAX_ITER_REACHED:
            print(
                f"Continue stopped at iteration {current}: {stop_reason}. "
                "post/reports kept."
            )
            return 0
    print(
        f"Continue finished at iteration {current} "
        f"({current - start} iterations added). post/reports kept."
    )
    return 0


# Set once main knows the run leaf; read by the job-result writer.
_JOB_RESULT_LEAF = {}
JOB_RESULT_ENV = "RO_JOB_RESULT"


def write_job_result(path, run_directory):
    """``{"run_directory", "convergence_quality"}`` for the queue state.

    ``job_queue`` sets ``RO_JOB_RESULT`` per job and records this next to
    the return code, so a succeeded (exit 0) job still shows FAIL or None.
    """
    gate = None
    if run_directory is not None:
        manifest = Path(run_directory) / "manifest.json"
        if manifest.is_file():
            try:
                gate = _read_json(manifest).get("convergence_quality")
            except (OSError, ValueError):
                gate = None
    payload = {
        "run_directory": None if run_directory is None else str(run_directory),
        "convergence_quality": gate,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def require_published_final(run_directory, geo_id, case_id):
    """The final pair on disk is the one the run manifest published."""
    manifest = _read_json(Path(run_directory) / "manifest.json")
    for path, field in zip(
        _final_paths(run_directory, geo_id, case_id),
        ("final_case_sha256", "final_data_sha256"),
    ):
        expected = manifest.get(field)
        if not expected or sha256_file(path) != expected:
            raise RuntimeError(
                f"Resume: {path.name} does not match manifest {field}; "
                "refusing to extract or re-solve an ambiguous final."
            )


def extract_and_validate(data_root, run_directory, entry, *, geo_id, mesh_id, case_id):
    """Extract the final into post/reports and validate the wide summary."""
    extract_result = mfbo_common.launch_extract(
        data_root,
        run_directory,
        entry,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=case_id,
    )
    if extract_result.returncode != 0:
        return _exit_code(extract_result.returncode)
    summary = Path(run_directory) / "post" / "reports" / "summary_metrics_wide.csv"
    valid, message = batch_report_extract.validate_summary_wide_csv(summary)
    if not valid:
        print(f"FAILED_METRIC_VALIDATION: {message}")
        return 1
    return 0


def main(argv=None):
    _JOB_RESULT_LEAF.clear()
    try:
        return _main(argv)
    finally:
        result_path = os.environ.get(JOB_RESULT_ENV)
        if result_path:
            try:
                write_job_result(result_path, _JOB_RESULT_LEAF.get("run_directory"))
            except OSError as exc:
                print(f"WARNING: could not write job result {result_path}: {exc}")


def _main(argv=None):
    args = build_parser().parse_args(argv)
    if args.max_aspect_ratio is not None and args.max_prism_aspect_ratio is not None:
        raise ValueError("--max-aspect-ratio cannot combine with --max-prism-aspect-ratio.")
    if args.resume and args.mesh_only:
        raise ValueError("--resume does not combine with --mesh-only.")
    if args.require_mesh and args.mesh_only:
        raise ValueError("--require-mesh does not combine with --mesh-only.")
    if args.init_from is not None and (
        args.mesh_only or args.resume or args.continue_iterations is not None
    ):
        raise ValueError(
            "--init-from is a fresh solve; it does not combine with --mesh-only, "
            "--resume or --continue-iterations."
        )
    if args.continue_iterations is not None and args.resume:
        raise ValueError(
            "--continue-iterations does not combine with --resume."
        )
    if args.continue_iterations is not None and args.mesh_only:
        raise ValueError(
            "--continue-iterations does not combine with --mesh-only."
        )
    if args.continue_iterations is not None:
        _positive_count("continue-iterations", args.continue_iterations)
        _positive_count("continue-every", args.continue_every)
    data_root = resolve_data_root(args.data_root)
    case_id = require_base_case(args.case)
    changes = collect_overrides(
        m_max=args.m_max,
        m_min=args.m_min,
        m_cpg=args.m_cpg,
        bl_layers=args.bl_layers,
        bl_height_factor=args.bl_first_height_factor,
        spacer_bl_layers=args.spacer_bl_layers,
        local_proximity=_json_object("--local-proximity", args.local_proximity),
        local_face_size=_json_object("--local-face-size", args.local_face_size),
        membrane_bl_y1_um=args.membrane_bl_y1_um,
        membrane_bl_layers=args.membrane_bl_layers,
        bulk_max_size_mm=args.bulk_max_size,
        peel_layers=args.peel_layers,
        max_aspect_ratio=args.max_aspect_ratio,
    )
    case, template, max_retries = load_mesh_template(args.geo_id)
    family = case["family"]
    provenance = {}
    spec_effective = None
    if args.mesh_spec:
        spec_path = resolve_spec_path(args.mesh_spec)
        spec = load_mesh_spec(spec_path)
        require_changes_match(changes, family_settings(spec, family))
        spec_effective = effective_mesh_settings(spec, family, args.geo_id)
        provenance = provenance_fields(spec_path, family=family, geo_id=args.geo_id)
    production_mesh_id = case["mesh_id"]
    suffix, geometry_root = resolve_geometry_request(
        args.geometry_suffix, args.geometry_root
    )
    overrides = apply_study_overrides(template, changes)
    overrides.update(geometry_meshing_overrides(suffix, geometry_root))
    overrides.update(provenance)
    if args.max_prism_aspect_ratio is not None:
        _require_positive(
            "max_prism_aspect_ratio", args.max_prism_aspect_ratio
        )
        overrides["max_prism_aspect_ratio_threshold"] = float(
            args.max_prism_aspect_ratio
        )
    print_override_table(override_rows(template, overrides))
    study_id = overrides["mesh_id"]
    leaf = mesh_leaf(data_root, family, args.geo_id, study_id)
    geometry_kwargs = {
        "suffix": suffix,
        "geometry_root": None if geometry_root is None else geometry_root.as_posix(),
        "require_parity": suffix == ".pmdb",
    }
    if args.resume and not leaf.is_dir():
        raise FileNotFoundError(
            f"Checkpoint resume requires the mesh leaf: {leaf}."
        )
    if args.continue_iterations is not None and not leaf.is_dir():
        raise FileNotFoundError(f"Continue requires the mesh leaf: {leaf}.")
    if (args.require_mesh or args.init_from is not None) and not leaf.is_dir():
        flag = "--require-mesh" if args.require_mesh else "--init-from"
        raise FileNotFoundError(
            f"{flag}: mesh leaf is missing (this job never meshes): {leaf}."
        )
    reused_manifest = None
    reuse_mesh = (
        args.reuse_mesh
        or args.require_mesh
        or args.init_from is not None
        or args.resume
        or args.continue_iterations is not None
    )
    if reuse_mesh and leaf.exists():
        geometry = ensure_study_geometry(
            data_root,
            family,
            args.geo_id,
            **geometry_kwargs,
        )
        reused_manifest = require_reusable_mesh(
            leaf,
            overrides,
            geometry,
            sha256_file(geometry),
            spec_effective=spec_effective,
        )
        print(f"Reusing mesh leaf {leaf}")
        provenance = run_spec_provenance(provenance, reused_manifest)
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

    print_mesh_cell_count(leaf, study_id)
    if args.mesh_only:
        return 0

    checkpoint_every = require_checkpoint_every(args.checkpoint_every)
    _entry, solver_template, solver_retries, settle_s = load_solver_template(
        args.geo_id,
        production_mesh_id,
        case_id,
    )
    solver_overrides = solver_overrides_for_study(solver_template, study_id)
    solver_overrides.update(provenance)
    run_directory = run_leaf(data_root, family, args.geo_id, study_id, case_id)
    _JOB_RESULT_LEAF["run_directory"] = run_directory
    if checkpoint_every is not None:
        solver_overrides["checkpoint_every"] = checkpoint_every
    solver_overrides["run_id"] = case_id
    solver_overrides["case_name"] = case_id
    solver_overrides.update(convergence_profile_overrides(args.convergence_profile))
    if args.max_iterations is not None:
        _require_positive("max-iterations", args.max_iterations)
        solver_overrides["max_iterations"] = int(args.max_iterations)
    solver_overrides["enable_cp_live_monitor"] = bool(args.cp_live)
    if args.cp_live and args.convergence_profile is not None:
        solver_overrides["qoi_stop_include_cp"] = True
    if args.continue_iterations is not None:
        inherit_recorded_stop_rule(run_directory, solver_overrides)
        return continue_finished_leaf(
            data_root=data_root,
            run_directory=run_directory,
            entry=_entry,
            solver_overrides=solver_overrides,
            geo_id=args.geo_id,
            mesh_id=study_id,
            case_id=case_id,
            max_retries=solver_retries,
            settle_s=settle_s,
            additional=args.continue_iterations,
            every=args.continue_every,
            keep_checkpoints=args.keep_checkpoints,
        )
    if args.init_from is not None:
        init_record = resolve_init_from_leaf(
            args.init_from,
            data_root=data_root,
            family=family,
            geo_id=args.geo_id,
            mesh_id=study_id,
            case_id=case_id,
            mesh_sha256=(reused_manifest or {}).get("mesh_sha256"),
        )
        print(
            f"init_from: {init_record['leaf']} (iteration "
            f"{init_record['iterations_run']}, gate PASS, final hashes verified)."
        )
        if run_directory.exists():
            aside = move_leaf_aside(run_directory)
            print(f"init_from: existing run leaf moved aside to {aside}")
        solver_overrides["init_from"] = init_record
    if args.resume and run_directory.is_dir() and final_pair_ready(
        run_directory, args.geo_id, case_id
    ):
        # The solve already published its final; only the extract failed
        # (hang, gate exception). Re-solving would overwrite hours of work.
        require_published_final(run_directory, args.geo_id, case_id)
        print(
            f"Resume: final pair is ready in {run_directory}; running the "
            "extract only."
        )
        return extract_and_validate(
            data_root,
            run_directory,
            _entry,
            geo_id=args.geo_id,
            mesh_id=study_id,
            case_id=case_id,
        )
    if args.resume:
        found = (
            latest_checkpoint(run_directory, args.geo_id, case_id)
            if run_directory.is_dir()
            else None
        )
        if found is None:
            if run_directory.is_dir() and final_pair_ready(
                run_directory, args.geo_id, case_id
            ):
                raise FileExistsError(
                    "Resume has no checkpoint pair, and the final case+data "
                    f"are already in {run_directory}. Refusing to delete "
                    "them. Use --continue-iterations to extend this leaf."
                )
            if run_directory.is_dir():
                aside = move_leaf_aside(run_directory)
                print(
                    "Resume fallback: incomplete run leaf moved aside to "
                    f"{aside}"
                )
            solver_overrides["resume_fallback"] = "fresh"
            print(f"resume_fallback: fresh ({run_directory})")
        else:
            manifest_path = run_directory / "manifest.json"
            manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
            settings = manifest.get("solver_settings")
            if isinstance(settings, dict):
                refuse_finished_checkpoint(
                    found.iteration, settings.get("max_iterations")
                )
            if checkpoint_every is None:
                checkpoint_every = require_checkpoint_every(
                    manifest.get("checkpoint_every")
                )
                if checkpoint_every is not None:
                    solver_overrides["checkpoint_every"] = checkpoint_every
            solver_overrides["checkpoint_resume"] = True
            # Only a resume that continues this leaf keeps its stop rule; the
            # fresh fallback above uses the current one.
            inherit_recorded_stop_rule(run_directory, solver_overrides)
            print(
                f"Resuming {run_directory} from checkpoint iteration "
                f"{found.iteration}."
            )
    else:
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

    code = extract_and_validate(
        data_root,
        run_directory,
        _entry,
        geo_id=args.geo_id,
        mesh_id=study_id,
        case_id=case_id,
    )
    if code:
        return code
    if checkpoint_every is not None:
        outcome = release_after_pass(
            run_directory,
            args.geo_id,
            case_id,
            keep=args.keep_checkpoints,
        )
        for error in outcome["errors"]:
            print(f"CHECKPOINT_CLEANUP_FAILED: {error}")
        print(cleanup_line(outcome))
    print_study_report(study_report(leaf, study_id, run_directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
