"""Pillar evaluator with the 2D ``evaluate(design, fidelity)`` shape.

Importing this module does not import Fluent or launch a solver. Geometry,
meshing, the solve, and extraction are callables the caller supplies.
``EXAMPLE_FIDELITY_TABLE`` documents the production operating mesh as
``"LF"``. It has no high-fidelity entry. A fidelity name that is not in
the table passed to ``evaluate`` raises.

A leaf is reused only when the files the pipeline actually writes show
success. Geometry success is a meta object whose ``geo_id`` and ``inputs``
match the requested design, plus a ``.pmdb`` whose sha256 equals
``pmdb_sha256``. Mesh success is ``mesh_run_record.json`` ``status``
``SUCCESS`` and a manifest object. A run leaf is success only when its
manifest object exists and ``post/reports/summary_metrics_wide.csv``
exists. A missing field is not success. A geometry error, a mesh record, or a
run that already has solver output stays ``execution_failed`` and is not
run again. A leaf with no CAD or Fluent output is an infrastructure
failure: it is archived and the next evaluate retries it. A Fluent
crash (segmentation fault, or a meshing or solver process that dies)
is retried once; the second crash stays a design failure.
``convergence_quality`` ``FAIL`` becomes ``diverged`` when
``stop_reason`` is ``diverged``, and ``invalid`` otherwise. Missing
quantities stay missing; they are not stored as LMH = 0.
The objective ``lmh`` is summary column ``lmh_window_module``.
``lmh_module_area`` remains the whole-active rescale of
``lmh_mass_balance`` and is not a fallback for ``lmh``.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ro.campaign_matrix import format_production_mesh_id
from ro.discovery_session import FAILURE_GEOMETRY, FAILURE_INFRASTRUCTURE
from ro.geometry_registry import (
    DEFAULT_PILLAR_N_ACTIVE,
    format_mfbo_pillar_geo_id,
    is_mfbo_pillar_geo_id,
    mfbo_pillar_n_active,
)
from ro.manifest import ManifestError, update_run_manifest_fields, read_run_manifest
from ro.mfbo_fidelity import fidelity_settings_used, parse_fidelity_level, run_id_for_level
from ro.paths import RUN_ID_RE, using_data_root
from ro.solver_common import cost_proxy, iterations_run_from_case, sha256_file

FAMILY = "pillar"
STATUS_VALID = "valid"
STATUS_DIVERGED = "diverged"
STATUS_INVALID = "invalid"
STATUS_EXECUTION_FAILED = "execution_failed"
FAILURE_MESH = "mesh"
FAILURE_SOLVER = "solver"
_ARCHIVE_TOKENS = ("_geometry_failed_", "_mesh_failed_", "_run_failed_")
REASON_OPENING_GAP_SLIVER = "opening_gap_sliver"

LF_FIDELITY = "LF"
LF_MESH_ID = "max085_min006_cpg5_bl4_peel2"
LF_MESH_SETTINGS = {
    "m_max": 0.085,
    "m_min": 0.006,
    "m_cpg": 5,
    "bl_layers": 4,
    "peel_layers": 2,
}
# Documented example only. High fidelity is not defined.
EXAMPLE_FIDELITY_TABLE = {LF_FIDELITY: dict(LF_MESH_SETTINGS)}

# Known-good mesh_run_record.json writes this. Archived failures write FAILED.
# A missing status is not success.
_MESH_RECORD_SUCCESS = "SUCCESS"
_SUMMARY_CSV = Path("post") / "reports" / "summary_metrics_wide.csv"
_EXTRACT_TIMING_JSON = Path("post") / "reports" / "report_extract_timing.json"
_EXTRACTION_SOURCE_MANIFEST = "run_manifest"
_EXTRACTION_SOURCE_TIMING_JSON = "report_extract_timing"
_COMPLETED_STOP_REASONS = frozenset(
    {
        "residual_converged",
        "qoi_converged",
        "lf_monitor_stable",
        "hf_monitor_stable",
        "max_iter_reached",
    }
)
_MESH_SETTING_KEYS = (
    "m_max",
    "m_min",
    "m_cpg",
    "bl_layers",
    "peel_layers",
)
_RESULT_FIELDS = (
    "geo_id",
    "mesh_id",
    "run_id",
    "fidelity",
    "d_p_mm",
    "d_h_mm",
    "d_f_mm",
    "lmh",
    "lmh_module_area",
    "pressure_drop_per_length_pa_per_m",
    "cp_average",
    "cp_q999",
    "cp_canon_window_avg",
    "status",
    "failure_reason",
    "cell_count",
    "iterations_run",
    "stop_reason",
    "generate_wall_time_s",
    "cost_proxy",
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
    "solver_time_s",
    "leaf_path",
    "processor_count",
    "extraction_wall_time_source",
)


@dataclass(frozen=True)
class PillarDesign:
    """Pillar diameters in millimetres. ``d_h_mm`` is 0 when there is no bore."""

    d_p_mm: float
    d_h_mm: float
    d_f_mm: float


@dataclass(frozen=True)
class Drivers:
    """One call each for CAD, meshing, the solve, and report extraction."""

    generate: Callable[..., None]
    mesh: Callable[..., None]
    solve: Callable[..., None]
    extract: Callable[..., None]


def opening_gap_mm(d_p_mm: float, d_h_mm: float, d_f_mm: float) -> float:
    """Filament-to-bore gap on the pillar surface, in millimetres.

    ``G = R_p * (pi/4 - asin(r_f/R_p) - asin(r_h/R_p))``. ``r_h`` is 0
    when ``d_h_mm`` is 0. Positive ``G`` is a separate opening. Mesh
    sizes in the fidelity table use the same millimetre unit, so
    ``m_min = 0.006`` is 6 µm.
    """
    radius_p = _positive_mm("d_p_mm", d_p_mm) / 2.0
    bore = _nonnegative_mm("d_h_mm", d_h_mm)
    filament = _positive_mm("d_f_mm", d_f_mm)
    if bore >= d_p_mm:
        raise ValueError(
            f"d_h_mm must be smaller than d_p_mm, got d_h_mm={d_h_mm}, d_p_mm={d_p_mm}."
        )
    if filament >= d_p_mm:
        raise ValueError(
            f"d_f_mm must be smaller than d_p_mm, got d_f_mm={d_f_mm}, d_p_mm={d_p_mm}."
        )
    radius_h = 0.0 if bore == 0.0 else bore / 2.0
    radius_f = filament / 2.0
    return radius_p * (
        math.pi / 4.0
        - math.asin(radius_f / radius_p)
        - math.asin(radius_h / radius_p)
    )


def opening_is_near_contact(gap_mm: float, m_min_mm: float) -> bool:
    """True when ``|G|`` is below the surface mesher's minimum size.

    A positive gap smaller than ``m_min`` cannot be meshed. A negative gap
    of the same size is the cusp on the other side of contact. A wider
    merged opening is left for the solver.
    """
    limit = _positive_mm("m_min_mm", m_min_mm)
    return abs(float(gap_mm)) < limit


def lmh_module_area(
    lmh_mass_balance: float,
    area_mem: float,
    n_active_cells: float,
    cell_length_x_m: float,
    periodic_shift_y_m: float,
) -> float:
    """Rescale exposed-membrane LMH onto both membranes of the active module.

    Same formula as ``scripts/mfbo/summarize_results.py``:
    ``A_module = 2 * n_active_cells * cell_length_x_m * periodic_shift_y_m``.
    """
    area = (
        2.0
        * float(n_active_cells)
        * float(cell_length_x_m)
        * float(periodic_shift_y_m)
    )
    if area == 0.0:
        raise ZeroDivisionError("A_module is 0.")
    return float(lmh_mass_balance) * float(area_mem) / area


def resolve_data_root(value: str | Path) -> Path:
    """Return an absolute data root that is not ``C:/ro_data`` or under it."""
    if isinstance(value, Path):
        text = value.as_posix().strip()
    elif isinstance(value, str):
        text = value.strip().replace("\\", "/")
    else:
        raise ValueError(f"data root is required, got {value!r}.")
    if not text:
        raise ValueError("data root is required.")
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


def mesh_id_for_settings(settings: Mapping[str, Any]) -> str:
    """Production mesh id for one fidelity-table entry."""
    checked = _mesh_settings(settings)
    return format_production_mesh_id(
        m_max=checked["m_max"],
        m_min=checked["m_min"],
        m_cpg=checked["m_cpg"],
        bl_layers=checked["bl_layers"],
        peel_layers=checked["peel_layers"],
        spacer_bl_layers=checked.get("spacer_bl_layers"),
    )


def evaluate(
    design: PillarDesign | Mapping[str, Any],
    fidelity: str,
    *,
    run_id: str,
    data_root: str | Path,
    fidelity_table: Mapping[str, Mapping[str, Any]],
    drivers: Drivers,
) -> dict[str, Any]:
    """Run or reuse one pillar case and return the 2D-shaped result record."""
    point = _design(design)
    root = resolve_data_root(data_root)
    settings = _require_fidelity(fidelity_table, fidelity)
    checked_run = run_id_for_level(_require_run_id(run_id), settings)
    mesh_id = mesh_id_for_settings(settings)
    n_active = settings["n_active"] if settings.get("extended") else None
    geo_id = format_mfbo_pillar_geo_id(
        point.d_p_mm,
        point.d_h_mm,
        point.d_f_mm,
        n_active=n_active,
    )
    used = fidelity_settings_used(settings)
    gap = opening_gap_mm(point.d_p_mm, point.d_h_mm, point.d_f_mm)
    base = _identity(
        point,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=checked_run,
        fidelity=fidelity,
    )
    if opening_is_near_contact(gap, settings["m_min"]):
        return _finish(
            base,
            status=STATUS_INVALID,
            failure_reason=REASON_OPENING_GAP_SLIVER,
            fidelity_settings=used,
        )

    geo_dir = root / "geometries" / FAMILY / geo_id
    mesh_dir = root / "meshes" / FAMILY / geo_id / mesh_id
    run_dir = root / "runs" / FAMILY / geo_id / mesh_id / checked_run
    archived: list[str] = []
    failed, geo_archived = _ensure_geometry(
        drivers, point, geo_id, geo_dir, n_active=n_active
    )
    _remember_archived(archived, geo_archived)
    if failed is not None:
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason=failed[0],
            leaf_path=failed[1],
            failure_class=failed[2],
            archived_leaves=archived,
            fidelity_settings=used,
            data_root=root,
        )
    failed, mesh_archived = _ensure_mesh(
        drivers,
        point,
        geo_id,
        mesh_id,
        settings,
        mesh_dir,
        n_lead_excluded=settings.get("n_lead_excluded"),
    )
    _remember_archived(archived, mesh_archived)
    if failed is not None:
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason=failed[0],
            leaf_path=failed[1],
            failure_class=failed[2],
            archived_leaves=archived,
            fidelity_settings=used,
            data_root=root,
        )
    failed, run_archived = _ensure_solve(
        drivers,
        point,
        geo_id,
        mesh_id,
        checked_run,
        run_dir,
        level=settings,
    )
    _remember_archived(archived, run_archived)
    if failed is not None:
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason=failed[0],
            leaf_path=failed[1],
            failure_class=failed[2],
            archived_leaves=archived,
            fidelity_settings=used,
            data_root=root,
        )
    summary_path = run_dir / _SUMMARY_CSV
    if not summary_path.is_file():
        drivers.extract(
            design=point,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=checked_run,
            run_dir=run_dir,
            level=settings,
        )
    return _result_from_leaves(
        base,
        mesh_dir,
        run_dir,
        summary_path,
        fidelity_settings=used,
        archived_leaves=archived,
        data_root=root,
    )


def record_from_leaves(
    design: PillarDesign | Mapping[str, Any],
    fidelity: str,
    *,
    run_id: str,
    data_root: str | Path,
    fidelity_table: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Read one pillar result from leaves already on disk. Does not call drivers.

    A missing or unfinished run is ``execution_failed`` and is not archived.
    """
    point = _design(design)
    root = resolve_data_root(data_root)
    settings = _require_fidelity(fidelity_table, fidelity)
    checked_run = run_id_for_level(_require_run_id(run_id), settings)
    mesh_id = mesh_id_for_settings(settings)
    n_active = settings["n_active"] if settings.get("extended") else None
    geo_id = format_mfbo_pillar_geo_id(
        point.d_p_mm,
        point.d_h_mm,
        point.d_f_mm,
        n_active=n_active,
    )
    used = fidelity_settings_used(settings)
    gap = opening_gap_mm(point.d_p_mm, point.d_h_mm, point.d_f_mm)
    base = _identity(
        point,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=checked_run,
        fidelity=fidelity,
    )
    if opening_is_near_contact(gap, settings["m_min"]):
        return _finish(
            base,
            status=STATUS_INVALID,
            failure_reason=REASON_OPENING_GAP_SLIVER,
            fidelity_settings=used,
        )
    mesh_dir = root / "meshes" / FAMILY / geo_id / mesh_id
    run_dir = root / "runs" / FAMILY / geo_id / mesh_id / checked_run
    if _run_state(run_dir) not in ("success", "needs_extract"):
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason="leaf_not_successful",
            leaf_path=str(run_dir),
            fidelity_settings=used,
        )
    return _result_from_leaves(
        base,
        mesh_dir,
        run_dir,
        run_dir / _SUMMARY_CSV,
        fidelity_settings=used,
        data_root=root,
    )


def _design(value: PillarDesign | Mapping[str, Any]) -> PillarDesign:
    if isinstance(value, PillarDesign):
        point = value
    elif isinstance(value, Mapping):
        missing = [key for key in ("d_p_mm", "d_h_mm", "d_f_mm") if key not in value]
        if missing:
            raise KeyError(f"design is missing {missing}.")
        point = PillarDesign(
            d_p_mm=value["d_p_mm"],
            d_h_mm=value["d_h_mm"],
            d_f_mm=value["d_f_mm"],
        )
    else:
        raise TypeError(
            f"design must be a PillarDesign or a mapping, got {type(value).__name__}."
        )
    _positive_mm("d_p_mm", point.d_p_mm)
    _nonnegative_mm("d_h_mm", point.d_h_mm)
    _positive_mm("d_f_mm", point.d_f_mm)
    return point


def _require_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"Invalid run_id: {run_id!r}.")
    return run_id


def _require_fidelity(
    table: Mapping[str, Mapping[str, Any]],
    fidelity: str,
) -> dict[str, Any]:
    if not isinstance(fidelity, str) or not fidelity:
        raise ValueError(f"fidelity must be a non-empty name, got {fidelity!r}.")
    if not isinstance(table, Mapping) or fidelity not in table:
        raise KeyError(
            f"fidelity {fidelity!r} is not in the fidelity table. "
            "High fidelity is not defined."
        )
    return parse_fidelity_level(table[fidelity])


def _mesh_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise TypeError("A fidelity entry must be a mapping of mesh settings.")
    missing = [key for key in _MESH_SETTING_KEYS if key not in settings]
    if missing:
        raise KeyError(f"fidelity mesh settings missing {missing}.")
    checked = {
        "m_max": _positive_mm("m_max", settings["m_max"]),
        "m_min": _positive_mm("m_min", settings["m_min"]),
        "m_cpg": _positive_int("m_cpg", settings["m_cpg"]),
        "bl_layers": _positive_int("bl_layers", settings["bl_layers"]),
        "peel_layers": _positive_int("peel_layers", settings["peel_layers"]),
    }
    if "spacer_bl_layers" in settings and settings["spacer_bl_layers"] is not None:
        checked["spacer_bl_layers"] = _positive_int(
            "spacer_bl_layers",
            settings["spacer_bl_layers"],
        )
    return checked


def _positive_mm(name: str, value: Any) -> float:
    number = _real(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive, got {value!r}.")
    return number


def _nonnegative_mm(name: str, value: Any) -> float:
    number = _real(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be >= 0, got {value!r}.")
    return number


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value!r}.")
    return value


def _real(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return number


def _is_windows_absolute(text: str) -> bool:
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def _ensure_geometry(
    drivers: Drivers,
    design: PillarDesign,
    geo_id: str,
    geo_dir: Path,
    n_active: int | None = None,
):
    """Return ``(failure, archived_path)``. ``failure`` is reason, path, class."""
    archived = None
    if _geometry_state(geo_dir, geo_id, design) == "success":
        return None, None
    kind = _geometry_attempt_class(geo_dir, geo_id, design)
    if kind == FAILURE_GEOMETRY:
        return ("leaf_not_successful", str(geo_dir), FAILURE_GEOMETRY), None
    if kind == FAILURE_INFRASTRUCTURE:
        archived = str(archive_failed_leaf(geo_dir, "geometry_failed"))
    try:
        drivers.generate(design=design, geo_id=geo_id, out_dir=geo_dir, n_active=n_active)
    except Exception as exc:
        if type(exc).__name__ != "DriverFailed":
            raise
        return (
            ("leaf_not_successful", str(geo_dir), _class_from_driver(exc, geo_dir, geo_id)),
            archived,
        )
    if _geometry_state(geo_dir, geo_id, design) != "success":
        kind = _geometry_attempt_class(geo_dir, geo_id, design)
        if kind in ("absent", "success"):
            kind = FAILURE_GEOMETRY
        return ("leaf_not_successful", str(geo_dir), kind), archived
    return None, archived


_CRASH_MARKERS = (
    "segmentation violation",
    "segmentation fault",
    "fatal signal",
)
_WINDOWS_CRASH_CODES = frozenset({3221225477, 3221225725, 3221226505})


def fluent_process_crash(directory: Path | None = None, exc: BaseException | None = None) -> bool:
    """True for a Fluent segfault or a meshing/solver process that died."""
    if exc is not None and _exception_is_process_crash(exc):
        return True
    if directory is not None and Path(directory).is_dir():
        return _directory_has_crash(Path(directory))
    return False


def _exception_is_process_crash(exc: BaseException) -> bool:
    code = getattr(exc, "returncode", None)
    if isinstance(code, int) and not isinstance(code, bool):
        if code < 0 or code in _WINDOWS_CRASH_CODES:
            return True
    text = f"{type(exc).__name__}: {exc}".casefold()
    if any(marker in text for marker in _CRASH_MARKERS):
        return True
    log_path = getattr(exc, "log_path", None)
    if isinstance(log_path, Path) and log_path.is_file() and _file_has_crash(log_path):
        return True
    return False


def _directory_has_crash(directory: Path) -> bool:
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.casefold()
        if name != "cortexerror.log" and not name.endswith((".log", ".txt", ".err", ".json")):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        if _file_has_crash(path):
            return True
    return False


def _file_has_crash(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
    except OSError:
        return False
    return any(marker in text for marker in _CRASH_MARKERS)


def _fill_missing_stage_wall(record: dict[str, Any], directory: Path) -> None:
    for key, value in _leaf_stage_seconds(directory).items():
        if record.get(key) is None:
            record[key] = value


def _add_crash_archive_wall(record: dict[str, Any], archived: list[str]) -> None:
    for item in archived:
        path = Path(item)
        if not fluent_process_crash(path):
            continue
        extra = _leaf_stage_seconds(path)
        for key, value in extra.items():
            current = record.get(key)
            base = 0.0 if current is None else float(current)
            record[key] = base + value


def _leaf_stage_seconds(directory: Path) -> dict[str, float]:
    record = _read_object(directory / "mesh_run_record.json") or {}
    manifest = _read_object(directory / "manifest.json") or {}
    if not isinstance(record, Mapping):
        record = {}
    if not isinstance(manifest, Mapping):
        manifest = {}
    found = {
        "mesh_wall_time_s": _first_finite(
            manifest.get("mesh_wall_time_s"),
            record.get("mesh_wall_time_s"),
            record.get("wall_time_seconds"),
        ),
        "solver_wall_time_s": _first_finite(manifest.get("solver_wall_time_s")),
        "extraction_wall_time_s": _first_finite(manifest.get("extraction_wall_time_s")),
    }
    return {key: float(value) for key, value in found.items() if value is not None}


def _remember_archived(bucket: list[str], extra) -> None:
    if extra is None:
        return
    if isinstance(extra, str):
        bucket.append(extra)
        return
    bucket.extend(path for path in extra if path)


def _ensure_mesh(
    drivers,
    design,
    geo_id,
    mesh_id,
    settings,
    mesh_dir: Path,
    n_lead_excluded: int | None = None,
):
    archived: list[str] = []
    state = _mesh_state(mesh_dir)
    if state == "success":
        return None, archived
    prior_crash = False
    if state == "failed":
        prior_crash = fluent_process_crash(mesh_dir)
        kind = _mesh_attempt_class(mesh_dir)
        if kind != FAILURE_INFRASTRUCTURE and not prior_crash:
            return ("leaf_not_successful", str(mesh_dir), FAILURE_MESH), archived
        archived.append(str(archive_failed_leaf(mesh_dir, "mesh_failed")))

    def invoke():
        try:
            drivers.mesh(
                design=design,
                geo_id=geo_id,
                mesh_id=mesh_id,
                mesh_settings=settings,
                mesh_dir=mesh_dir,
                n_lead_excluded=n_lead_excluded,
            )
        except Exception as exc:
            if type(exc).__name__ != "DriverFailed":
                raise
            if fluent_process_crash(mesh_dir, exc):
                return exc, FAILURE_MESH
            kind = FAILURE_MESH if _mesh_has_fluent_output(mesh_dir) else FAILURE_INFRASTRUCTURE
            return exc, kind
        if _mesh_state(mesh_dir) == "success":
            return None
        if fluent_process_crash(mesh_dir):
            return None, FAILURE_MESH
        kind = _mesh_attempt_class(mesh_dir)
        if kind in ("absent", "success"):
            kind = FAILURE_MESH
        return None, kind

    failure = invoke()
    if failure is None:
        return None, archived
    exc, kind = failure
    crash = kind == FAILURE_MESH and fluent_process_crash(mesh_dir, exc)
    if crash and not prior_crash:
        if mesh_dir.exists():
            archived.append(str(archive_failed_leaf(mesh_dir, "mesh_failed")))
        failure = invoke()
        if failure is None:
            return None, archived
        exc, kind = failure
        if fluent_process_crash(mesh_dir, exc):
            kind = FAILURE_MESH
    elif prior_crash and fluent_process_crash(mesh_dir, exc):
        kind = FAILURE_MESH
    return ("leaf_not_successful", str(mesh_dir), kind), archived


def _ensure_solve(drivers, design, geo_id, mesh_id, run_id, run_dir: Path, level=None):
    archived: list[str] = []
    state = _run_state(run_dir)
    if state in ("success", "needs_extract"):
        return None, archived
    prior_crash = False
    if state == "failed":
        prior_crash = fluent_process_crash(run_dir)
        kind = _run_attempt_class(run_dir)
        if kind != FAILURE_INFRASTRUCTURE and not prior_crash:
            return ("leaf_not_successful", str(run_dir), FAILURE_SOLVER), archived
        archived.append(str(archive_failed_leaf(run_dir, "run_failed")))

    def invoke():
        try:
            drivers.solve(
                design=design,
                geo_id=geo_id,
                mesh_id=mesh_id,
                run_id=run_id,
                run_dir=run_dir,
                level=level,
            )
        except Exception as exc:
            if type(exc).__name__ != "DriverFailed":
                raise
            if fluent_process_crash(run_dir, exc):
                return exc, FAILURE_SOLVER
            kind = FAILURE_SOLVER if _run_has_fluent_output(run_dir) else FAILURE_INFRASTRUCTURE
            return exc, kind
        if _run_state(run_dir) in ("success", "needs_extract"):
            return None
        if fluent_process_crash(run_dir):
            return None, FAILURE_SOLVER
        kind = _run_attempt_class(run_dir)
        if kind in ("absent", "success", "needs_extract"):
            kind = FAILURE_SOLVER
        return None, kind

    failure = invoke()
    if failure is None:
        return None, archived
    exc, kind = failure
    crash = kind == FAILURE_SOLVER and fluent_process_crash(run_dir, exc)
    if crash and not prior_crash:
        if run_dir.exists():
            archived.append(str(archive_failed_leaf(run_dir, "run_failed")))
        failure = invoke()
        if failure is None:
            return None, archived
        exc, kind = failure
        if fluent_process_crash(run_dir, exc):
            kind = FAILURE_SOLVER
    elif prior_crash and fluent_process_crash(run_dir, exc):
        kind = FAILURE_SOLVER
    return ("leaf_not_successful", str(run_dir), kind), archived


def _geometry_state(directory: Path, geo_id: str, design: PillarDesign) -> str:
    """Success matches ``pillar_cad`` meta: no ``status`` field is written."""
    if not directory.exists():
        return "absent"
    meta = _read_object(directory / f"{geo_id}_meta.json")
    pmdb = directory / f"{geo_id}.pmdb"
    if meta is None or not pmdb.is_file():
        return "failed"
    if meta.get("geo_id") != geo_id:
        return "failed"
    if not _geometry_inputs_match(meta.get("inputs"), geo_id, design):
        return "failed"
    digest = meta.get("pmdb_sha256")
    if not isinstance(digest, str) or not digest:
        return "failed"
    if sha256_file(pmdb) != digest:
        return "failed"
    return "success"


def _geometry_inputs_match(inputs: Any, geo_id: str, design: PillarDesign) -> bool:
    if not isinstance(inputs, dict):
        return False
    if inputs.get("geo_id") != geo_id:
        return False
    expected = {
        "d_p_mm": design.d_p_mm,
        "d_h_mm": design.d_h_mm,
        "d_f_mm": design.d_f_mm,
    }
    for key, value in expected.items():
        raw = inputs.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return False
        if float(raw) != float(value):
            return False
    recorded = inputs.get("n_active", DEFAULT_PILLAR_N_ACTIVE)
    if isinstance(recorded, bool) or not isinstance(recorded, int):
        return False
    try:
        encoded = mfbo_pillar_n_active(geo_id)
    except ValueError:
        return False
    return recorded == encoded


def _mesh_state(directory: Path) -> str:
    """``status`` ``SUCCESS`` plus a manifest object. ``FAILED`` is not success."""
    if not directory.exists():
        return "absent"
    record = _read_object(directory / "mesh_run_record.json")
    manifest = _read_object(directory / "manifest.json")
    if record is None or manifest is None:
        return "failed"
    if record.get("status") != _MESH_RECORD_SUCCESS:
        return "failed"
    return "success"


def _run_state(directory: Path) -> str:
    """Success is a manifest object and the summary CSV. No status field is required.

    A manifest without the CSV is ``needs_extract``: the solve is not run
    again, and extraction can still write the CSV. A missing manifest on an
    existing directory is failed.
    """
    if not directory.exists():
        return "absent"
    manifest = _read_object(directory / "manifest.json")
    if manifest is None:
        return "failed"
    if not (directory / _SUMMARY_CSV).is_file():
        return "needs_extract"
    return "success"


def is_archived_leaf_name(name: str) -> bool:
    return any(token in name for token in _ARCHIVE_TOKENS)


def archive_failed_leaf(directory: Path, prefix: str, now=None) -> Path:
    """Move a failed leaf beside itself as ``<name>_<prefix>_<UTC>``."""
    source = Path(directory)
    if not source.is_dir():
        raise FileNotFoundError(f"Cannot archive missing leaf: {source}")
    destination = source.with_name(f"{source.name}_{prefix}_{_utc_stamp(now)}")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}.")
    source.rename(destination)
    return destination


def _utc_stamp(now) -> str:
    moment = datetime.now(timezone.utc) if now is None else now
    if moment.tzinfo is None:
        raise ValueError("archive timestamp must be timezone-aware.")
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _geometry_attempt_class(directory: Path, geo_id: str, design: PillarDesign) -> str:
    if not directory.exists():
        return "absent"
    if _geometry_state(directory, geo_id, design) == "success":
        return "success"
    error = _read_object(directory / f"{geo_id}_error.json")
    if isinstance(error, dict) and error.get("failure_class") == FAILURE_GEOMETRY:
        return FAILURE_GEOMETRY
    if isinstance(error, dict) and error.get("failure_class") == FAILURE_INFRASTRUCTURE:
        return FAILURE_INFRASTRUCTURE
    if (directory / f"{geo_id}.pmdb").is_file():
        return FAILURE_GEOMETRY
    return FAILURE_INFRASTRUCTURE


def _class_from_driver(exc: BaseException, geo_dir: Path, geo_id: str) -> str:
    error = _read_object(geo_dir / f"{geo_id}_error.json")
    if isinstance(error, dict) and error.get("failure_class") in (
        FAILURE_GEOMETRY,
        FAILURE_INFRASTRUCTURE,
    ):
        return str(error["failure_class"])
    recorded = getattr(exc, "failure_class", None)
    if recorded in (FAILURE_GEOMETRY, FAILURE_INFRASTRUCTURE):
        return str(recorded)
    return FAILURE_INFRASTRUCTURE


def _mesh_has_fluent_output(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    if (directory / "mesh_run_record.json").is_file():
        return True
    if (directory / "manifest.json").is_file():
        return True
    return any(directory.glob("*.msh.h5"))


def _mesh_attempt_class(directory: Path) -> str:
    if not directory.exists():
        return "absent"
    if _mesh_state(directory) == "success":
        return "success"
    if _mesh_has_fluent_output(directory):
        return FAILURE_MESH
    return FAILURE_INFRASTRUCTURE


def _run_has_fluent_output(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    if (directory / "manifest.json").is_file():
        return True
    return any(directory.glob("*.cas.h5")) or any(directory.glob("*.dat.h5"))


def _run_attempt_class(directory: Path) -> str:
    if not directory.exists():
        return "absent"
    state = _run_state(directory)
    if state == "success":
        return "success"
    if state == "needs_extract":
        return "needs_extract"
    if _run_has_fluent_output(directory):
        return FAILURE_SOLVER
    return FAILURE_INFRASTRUCTURE


def _geometry_sha_success(directory: Path) -> bool:
    geo_id = directory.name
    pmdb = directory / f"{geo_id}.pmdb"
    meta = _read_object(directory / f"{geo_id}_meta.json")
    if not pmdb.is_file() or not isinstance(meta, dict):
        return False
    if meta.get("geo_id") != geo_id:
        return False
    digest = meta.get("pmdb_sha256")
    return isinstance(digest, str) and bool(digest) and sha256_file(pmdb) == digest


def summarize_mfbo_leaves(data_root: Path) -> dict[str, Any]:
    """Classify MFBO leaves. Successful leaves are counted and not listed."""
    root = Path(data_root)
    failed: list[dict[str, Any]] = []
    success_count = 0
    geometries = root / "geometries"
    if geometries.is_dir():
        for family in _child_dirs(geometries):
            for leaf in _child_dirs(family):
                if is_archived_leaf_name(leaf.name) or not is_mfbo_pillar_geo_id(leaf.name):
                    continue
                if _geometry_sha_success(leaf):
                    success_count += 1
                    continue
                error = _read_object(leaf / f"{leaf.name}_error.json")
                if isinstance(error, dict) and error.get("failure_class") == FAILURE_GEOMETRY:
                    kind = FAILURE_GEOMETRY
                elif (leaf / f"{leaf.name}.pmdb").is_file():
                    kind = FAILURE_GEOMETRY
                else:
                    kind = FAILURE_INFRASTRUCTURE
                failed.append(_leaf_row("geometry", kind, leaf, "geometry_failed"))
    meshes = root / "meshes"
    if meshes.is_dir():
        for family in _child_dirs(meshes):
            for geo in _child_dirs(family):
                if not is_mfbo_pillar_geo_id(geo.name):
                    continue
                for leaf in _child_dirs(geo):
                    if is_archived_leaf_name(leaf.name):
                        continue
                    kind = _mesh_attempt_class(leaf)
                    if kind == "success":
                        success_count += 1
                        continue
                    failed.append(_leaf_row("mesh", kind, leaf, "mesh_failed"))
    runs = root / "runs"
    if runs.is_dir():
        for family in _child_dirs(runs):
            for geo in _child_dirs(family):
                if not is_mfbo_pillar_geo_id(geo.name):
                    continue
                for mesh in _child_dirs(geo):
                    for leaf in _child_dirs(mesh):
                        if is_archived_leaf_name(leaf.name):
                            continue
                        kind = _run_attempt_class(leaf)
                        if kind in ("success", "needs_extract"):
                            success_count += 1
                            continue
                        failed.append(_leaf_row("run", kind, leaf, "run_failed"))
    return {"failed": failed, "success_count": success_count}


def archive_infrastructure_leaves(data_root: Path, now=None) -> list[dict[str, Any]]:
    """Archive infrastructure leftovers. Geometry, mesh, and solver failures stay."""
    archived = []
    for row in summarize_mfbo_leaves(data_root)["failed"]:
        if row["class"] != FAILURE_INFRASTRUCTURE:
            continue
        destination = archive_failed_leaf(Path(row["path"]), row["prefix"], now)
        archived.append({**row, "archived": str(destination)})
    return archived


def _child_dirs(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_dir())


def _leaf_row(kind: str, failure_class: str, path: Path, prefix: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "class": failure_class,
        "path": str(path),
        "prefix": prefix,
    }


def _result_from_leaves(
    base,
    mesh_dir: Path,
    run_dir: Path,
    summary_path: Path,
    fidelity_settings=None,
    archived_leaves=None,
    data_root: Path | None = None,
):
    mesh_manifest = _read_object(mesh_dir / "manifest.json") or {}
    mesh_record = _read_object(mesh_dir / "mesh_run_record.json") or {}
    run_manifest = _read_object(run_dir / "manifest.json") or {}
    retry = _read_object(run_dir / "solver_retry_record.json") or {}
    summary = _read_summary_row(summary_path)
    if summary_path.is_file() and summary is None:
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason="summary_metrics_unreadable",
            leaf_path=str(summary_path),
            mesh_manifest=mesh_manifest,
            mesh_record=mesh_record,
            run_manifest=run_manifest,
            retry=retry,
            run_dir=run_dir,
            fidelity_settings=fidelity_settings,
            archived_leaves=archived_leaves,
            data_root=data_root,
        )
    status, reason = _physics_status(run_manifest)
    if summary is None and status == STATUS_VALID:
        return _finish(
            base,
            status=STATUS_EXECUTION_FAILED,
            failure_reason="summary_metrics_missing",
            leaf_path=str(summary_path),
            mesh_manifest=mesh_manifest,
            mesh_record=mesh_record,
            run_manifest=run_manifest,
            retry=retry,
            run_dir=run_dir,
            fidelity_settings=fidelity_settings,
            archived_leaves=archived_leaves,
            data_root=data_root,
        )
    values = _quantities(summary, mesh_manifest)
    if status == STATUS_VALID and (
        values["lmh"] is None or values["pressure_drop_per_length_pa_per_m"] is None
    ):
        status = STATUS_INVALID
        reason = "missing_qoi"
    if status == STATUS_VALID and values["lmh_module_area"] is None:
        status = STATUS_INVALID
        reason = "missing_lmh_module_area"
    return _finish(
        base,
        status=status,
        failure_reason=reason,
        leaf_path=str(run_dir),
        mesh_manifest=mesh_manifest,
        mesh_record=mesh_record,
        run_manifest=run_manifest,
        retry=retry,
        run_dir=run_dir,
        summary=summary,
        fidelity_settings=fidelity_settings,
        archived_leaves=archived_leaves,
        data_root=data_root,
    )


def _physics_status(run_manifest: Mapping[str, Any]) -> tuple[str, str | None]:
    stop_reason = run_manifest.get("stop_reason")
    quality = run_manifest.get("convergence_quality")
    if stop_reason == "diverged":
        return STATUS_DIVERGED, "diverged"
    if quality == "FAIL":
        failures = run_manifest.get("convergence_quality_failures")
        if isinstance(failures, list) and failures:
            return STATUS_INVALID, ",".join(str(item) for item in failures)
        return STATUS_INVALID, "convergence_quality"
    if quality == "PASS" and stop_reason in _COMPLETED_STOP_REASONS:
        return STATUS_VALID, None
    if isinstance(stop_reason, str) and stop_reason:
        return STATUS_INVALID, stop_reason
    if quality == "UNKNOWN":
        return STATUS_INVALID, "convergence_quality_unknown"
    return STATUS_INVALID, "convergence_quality_unknown"


def _quantities(summary: Mapping[str, Any] | None, mesh_manifest: Mapping[str, Any]):
    lmh = _finite(None if summary is None else summary.get("lmh_window_module"))
    mass_balance = _finite(
        None if summary is None else summary.get("lmh_mass_balance")
    )
    pressure = _finite(
        None if summary is None else summary.get("pressure_drop_spacer_per_m")
    )
    module = None
    if mass_balance is not None and summary is not None:
        area_mem = _finite(summary.get("area_mem"))
        n_active = _finite(mesh_manifest.get("n_active_cells"))
        length = _finite(mesh_manifest.get("cell_length_x_m"))
        shift = _finite(mesh_manifest.get("periodic_shift_y_m"))
        if None not in (area_mem, n_active, length, shift):
            try:
                module = lmh_module_area(
                    mass_balance, area_mem, n_active, length, shift
                )
            except ZeroDivisionError:
                module = None
    return {
        "lmh": lmh,
        "lmh_module_area": module,
        "pressure_drop_per_length_pa_per_m": pressure,
        "cp_average": _finite(None if summary is None else summary.get("cpc_window_avg_flux")),
        "cp_q999": _finite(None if summary is None else summary.get("cp_q999_window_flux")),
        "cp_canon_window_avg": _finite(
            None if summary is None else summary.get("cp_canon_window_avg")
        ),
    }


def _identity(design: PillarDesign, *, geo_id: str, mesh_id: str, run_id: str, fidelity: str):
    return {
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
        "fidelity": fidelity,
        "d_p_mm": design.d_p_mm,
        "d_h_mm": design.d_h_mm,
        "d_f_mm": design.d_f_mm,
    }


def _finish(
    base,
    *,
    status: str,
    failure_reason: str | None,
    leaf_path: str | None = None,
    mesh_manifest: Mapping[str, Any] | None = None,
    mesh_record: Mapping[str, Any] | None = None,
    run_manifest: Mapping[str, Any] | None = None,
    retry: Mapping[str, Any] | None = None,
    run_dir: Path | None = None,
    summary: Mapping[str, Any] | None = None,
    fidelity_settings: Mapping[str, Any] | None = None,
    failure_class: str | None = None,
    archived_leaves: list[str] | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    mesh_manifest = mesh_manifest or {}
    mesh_record = mesh_record or {}
    run_manifest = run_manifest or {}
    retry = retry or {}
    quantities = _quantities(summary, mesh_manifest)
    record = {key: None for key in _RESULT_FIELDS}
    record.update(base)
    record.update(quantities)
    record["status"] = status
    record["failure_reason"] = failure_reason
    record["cell_count"] = _optional_positive_int(mesh_manifest.get("cell_count"))
    record["iterations_run"] = _iterations_run(run_manifest, run_dir)
    record["stop_reason"] = run_manifest.get("stop_reason")
    record["generate_wall_time_s"] = _generate_wall_time(data_root, base)
    record["cost_proxy"] = _cost_proxy(record["cell_count"], record["iterations_run"])
    record["mesh_wall_time_s"] = _first_finite(
        mesh_manifest.get("mesh_wall_time_s"),
        mesh_record.get("mesh_wall_time_s"),
        mesh_record.get("wall_time_seconds"),
    )
    record["solver_wall_time_s"] = _first_finite(
        run_manifest.get("solver_wall_time_s"),
        retry.get("wall_time_seconds"),
    )
    extraction_s, extraction_source = _extraction_wall_time(run_manifest, run_dir)
    record["extraction_wall_time_s"] = extraction_s
    record["extraction_wall_time_source"] = extraction_source
    record["solver_time_s"] = _finite(run_manifest.get("solver_time_s"))
    record["leaf_path"] = leaf_path
    record["processor_count"] = _optional_processor_count(
        run_manifest.get("processor_count")
    )
    if failure_class is not None:
        record["failure_class"] = failure_class
    if archived_leaves:
        record["archived_leaves"] = list(archived_leaves)
    if fidelity_settings is not None:
        record["fidelity_settings"] = dict(fidelity_settings)
    if run_dir is not None and (Path(run_dir) / "manifest.json").is_file():
        updates = _manifest_accounting(record, fidelity_settings)
        if updates:
            _write_run_accounting(
                run_dir,
                updates,
                data_root=data_root,
                require_valid=fidelity_settings is not None,
            )
    if run_manifest.get("convergence_gate") is not None:
        record["convergence_gate"] = run_manifest["convergence_gate"]
        for key in (
            "lf_monitor_ramp_end",
            "lf_monitor_window",
            "lf_monitor_lmh_range",
            "lf_monitor_dp_range",
            "lf_monitor_tol_lmh",
            "lf_monitor_tol_dp",
            "lf_monitor_n",
            "hf_monitor_ramp_end",
            "hf_monitor_window",
            "hf_monitor_lmh_range",
            "hf_monitor_dp_range",
            "hf_monitor_tol_lmh",
            "hf_monitor_tol_dp",
            "hf_monitor_n",
            "hf_monitor_continuity",
            "hf_monitor_continuity_max",
        ):
            if key in run_manifest:
                record[key] = run_manifest[key]
    if isinstance(run_manifest.get("hf_solve_phases"), list):
        record["hf_solve_phases"] = list(run_manifest["hf_solve_phases"])
    leaf = record.get("leaf_path")
    if isinstance(leaf, str) and Path(leaf).is_dir():
        _fill_missing_stage_wall(record, Path(leaf))
    if archived_leaves:
        _add_crash_archive_wall(record, archived_leaves)
    return record


def _iterations_run(
    run_manifest: Mapping[str, Any],
    run_dir: Path | None,
) -> int | None:
    recorded = _optional_positive_int(run_manifest.get("iterations_run"))
    if recorded is not None:
        return recorded
    if run_dir is None:
        return None
    return iterations_run_from_case(run_dir)


def _generate_wall_time(data_root: Path | None, base: Mapping[str, Any]) -> float | None:
    if data_root is None:
        return None
    geo_id = base.get("geo_id")
    if not isinstance(geo_id, str) or not geo_id:
        return None
    path = (
        Path(data_root)
        / "geometries"
        / FAMILY
        / geo_id
        / f"{geo_id}_generate_wall_time.json"
    )
    payload = _read_object(path)
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or "generate_wall_time_s" not in payload:
        raise ValueError(
            f"generate wall-time file {path} is missing generate_wall_time_s."
        )
    value = payload["generate_wall_time_s"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"generate_wall_time_s must be a number, got {value!r}."
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0.0:
        raise ValueError(
            f"generate_wall_time_s must be finite and >= 0, got {value!r}."
        )
    return number


def _cost_proxy(cell_count: int | None, iterations_run: int | None) -> int | None:
    if cell_count is None or iterations_run is None:
        return None
    return cost_proxy(cell_count, iterations_run)


def _write_run_accounting(
    run_dir: Path,
    updates: Mapping[str, Any],
    *,
    data_root: Path | None,
    require_valid: bool,
) -> None:
    """Merge accounting onto a real run manifest.

    A JSON object that is not a run manifest is left unchanged. A requested
    fidelity-settings write still raises when the file is not a run manifest.
    """
    if data_root is None:
        if require_valid:
            raise ValueError("data_root is required to update the run manifest.")
        return
    if not require_valid:
        try:
            with using_data_root(data_root):
                read_run_manifest(run_dir)
        except ManifestError:
            return
    with using_data_root(data_root):
        update_run_manifest_fields(run_dir, updates)


def _manifest_accounting(
    record: Mapping[str, Any],
    fidelity_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if fidelity_settings is not None:
        updates["fidelity_settings"] = dict(fidelity_settings)
    for key in (
        "cell_count",
        "iterations_run",
        "generate_wall_time_s",
        "cost_proxy",
    ):
        if record.get(key) is not None:
            updates[key] = record[key]
    return updates


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer >= 1, got {value!r}.")
    if value < 1:
        raise ValueError(f"expected an integer >= 1, got {value!r}.")
    return value


def _extraction_wall_time(
    run_manifest: Mapping[str, Any],
    run_dir: Path | None,
) -> tuple[float | None, str | None]:
    """Manifest field first, then ``report_extract_timing.json`` ``total_seconds``."""
    from_manifest = _finite(run_manifest.get("extraction_wall_time_s"))
    if from_manifest is not None:
        return from_manifest, _EXTRACTION_SOURCE_MANIFEST
    if run_dir is None:
        return None, None
    timing = _read_object(run_dir / _EXTRACT_TIMING_JSON)
    if timing is None:
        return None, None
    from_file = _finite(timing.get("total_seconds"))
    if from_file is None:
        return None, None
    return from_file, _EXTRACTION_SOURCE_TIMING_JSON


def _optional_processor_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _first_finite(*values: Any) -> float | None:
    for value in values:
        number = _finite(value)
        if number is not None:
            return number
    return None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _read_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _read_summary_row(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return None
    if len(rows) != 1:
        return None
    return rows[0]
