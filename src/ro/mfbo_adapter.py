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
exists. A missing field is not success. A leaf that exists without that
record returns ``execution_failed`` and is not run again.
``convergence_quality`` ``FAIL`` becomes ``diverged`` when
``stop_reason`` is ``diverged``, and ``invalid`` otherwise. Missing
quantities stay missing; they are not stored as LMH = 0.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ro.campaign_matrix import format_production_mesh_id
from ro.geometry_registry import format_mfbo_pillar_geo_id
from ro.paths import RUN_ID_RE
from ro.solver_common import sha256_file

FAMILY = "pillar"
STATUS_VALID = "valid"
STATUS_DIVERGED = "diverged"
STATUS_INVALID = "invalid"
STATUS_EXECUTION_FAILED = "execution_failed"
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
_COMPLETED_STOP_REASONS = frozenset(
    {"residual_converged", "qoi_converged", "max_iter_reached"}
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
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
    "solver_time_s",
    "leaf_path",
    "processor_count",
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
    checked_run = _require_run_id(run_id)
    root = resolve_data_root(data_root)
    settings = _require_fidelity(fidelity_table, fidelity)
    mesh_id = mesh_id_for_settings(settings)
    geo_id = format_mfbo_pillar_geo_id(point.d_p_mm, point.d_h_mm, point.d_f_mm)
    gap = opening_gap_mm(point.d_p_mm, point.d_h_mm, point.d_f_mm)
    base = _identity(
        point,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=checked_run,
        fidelity=fidelity,
    )
    if 0.0 < gap < settings["m_min"]:
        return _finish(
            base,
            status=STATUS_INVALID,
            failure_reason=REASON_OPENING_GAP_SLIVER,
        )

    geo_dir = root / "geometries" / FAMILY / geo_id
    mesh_dir = root / "meshes" / FAMILY / geo_id / mesh_id
    run_dir = root / "runs" / FAMILY / geo_id / mesh_id / checked_run
    failed = _ensure_geometry(drivers, point, geo_id, geo_dir)
    if failed is not None:
        return _finish(base, status=STATUS_EXECUTION_FAILED, failure_reason=failed[0], leaf_path=failed[1])
    failed = _ensure_mesh(drivers, point, geo_id, mesh_id, settings, mesh_dir)
    if failed is not None:
        return _finish(base, status=STATUS_EXECUTION_FAILED, failure_reason=failed[0], leaf_path=failed[1])
    failed = _ensure_solve(drivers, point, geo_id, mesh_id, checked_run, run_dir)
    if failed is not None:
        return _finish(base, status=STATUS_EXECUTION_FAILED, failure_reason=failed[0], leaf_path=failed[1])
    summary_path = run_dir / _SUMMARY_CSV
    if not summary_path.is_file():
        drivers.extract(
            design=point,
            geo_id=geo_id,
            mesh_id=mesh_id,
            run_id=checked_run,
            run_dir=run_dir,
        )
    return _result_from_leaves(base, mesh_dir, run_dir, summary_path)


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
    return _mesh_settings(table[fidelity])


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


def _ensure_geometry(drivers: Drivers, design: PillarDesign, geo_id: str, geo_dir: Path):
    state = _geometry_state(geo_dir, geo_id, design)
    if state == "success":
        return None
    if state == "failed":
        return ("leaf_not_successful", str(geo_dir))
    drivers.generate(design=design, geo_id=geo_id, out_dir=geo_dir)
    if _geometry_state(geo_dir, geo_id, design) != "success":
        return ("leaf_not_successful", str(geo_dir))
    return None


def _ensure_mesh(drivers, design, geo_id, mesh_id, settings, mesh_dir: Path):
    state = _mesh_state(mesh_dir)
    if state == "success":
        return None
    if state == "failed":
        return ("leaf_not_successful", str(mesh_dir))
    drivers.mesh(
        design=design,
        geo_id=geo_id,
        mesh_id=mesh_id,
        mesh_settings=settings,
        mesh_dir=mesh_dir,
    )
    if _mesh_state(mesh_dir) != "success":
        return ("leaf_not_successful", str(mesh_dir))
    return None


def _ensure_solve(drivers, design, geo_id, mesh_id, run_id, run_dir: Path):
    state = _run_state(run_dir)
    if state in ("success", "needs_extract"):
        return None
    if state == "failed":
        return ("leaf_not_successful", str(run_dir))
    drivers.solve(
        design=design,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
        run_dir=run_dir,
    )
    if _run_state(run_dir) not in ("success", "needs_extract"):
        return ("leaf_not_successful", str(run_dir))
    return None


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
    return True


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


def _result_from_leaves(base, mesh_dir: Path, run_dir: Path, summary_path: Path):
    mesh_manifest = _read_object(mesh_dir / "manifest.json") or {}
    mesh_record = _read_object(mesh_dir / "mesh_run_record.json") or {}
    run_manifest = _read_object(run_dir / "manifest.json") or {}
    retry = _read_object(run_dir / "solver_retry_record.json") or {}
    extract_record = _read_object(summary_path.parent / "extract_record.json") or {}
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
            extract_record=extract_record,
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
            extract_record=extract_record,
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
        extract_record=extract_record,
        summary=summary,
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
    lmh = _finite(None if summary is None else summary.get("lmh_mass_balance"))
    pressure = _finite(
        None if summary is None else summary.get("pressure_drop_spacer_per_m")
    )
    module = None
    if lmh is not None and summary is not None:
        area_mem = _finite(summary.get("area_mem"))
        n_active = _finite(mesh_manifest.get("n_active_cells"))
        length = _finite(mesh_manifest.get("cell_length_x_m"))
        shift = _finite(mesh_manifest.get("periodic_shift_y_m"))
        if None not in (area_mem, n_active, length, shift):
            try:
                module = lmh_module_area(lmh, area_mem, n_active, length, shift)
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
    extract_record: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mesh_manifest = mesh_manifest or {}
    mesh_record = mesh_record or {}
    run_manifest = run_manifest or {}
    retry = retry or {}
    extract_record = extract_record or {}
    quantities = _quantities(summary, mesh_manifest)
    record = {key: None for key in _RESULT_FIELDS}
    record.update(base)
    record.update(quantities)
    record["status"] = status
    record["failure_reason"] = failure_reason
    record["cell_count"] = _finite(mesh_manifest.get("cell_count"))
    record["mesh_wall_time_s"] = _finite(mesh_record.get("wall_time_seconds"))
    record["solver_wall_time_s"] = _first_finite(
        run_manifest.get("solver_wall_time_s"),
        retry.get("wall_time_seconds"),
    )
    record["extraction_wall_time_s"] = _first_finite(
        run_manifest.get("extraction_wall_time_s"),
        extract_record.get("wall_time_seconds"),
    )
    record["solver_time_s"] = _finite(run_manifest.get("solver_time_s"))
    record["leaf_path"] = leaf_path
    record["processor_count"] = _optional_processor_count(
        run_manifest.get("processor_count")
    )
    return record


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
