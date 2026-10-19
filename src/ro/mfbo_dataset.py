"""Collect evaluated 3D ``HF_m`` and ``LF_d`` leaves into one CSV.

Reads run leaves under ``runs/pillar``. Does not launch Fluent and does
not write manifests. ``lmh`` is ``lmh_window_module``. ``wall_s`` is mesh
plus solver plus extraction, the success charge in the 3D loop. A design
failure in that loop also adds generate time; ``recorded_cost_s`` is that
sum, with a missing stage treated as zero once any stage is present.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ro.geometry_registry import parse_mfbo_pillar_geo_id
from ro.mfbo_adapter import (
    FAMILY,
    STATUS_EXECUTION_FAILED,
    STATUS_INVALID,
    STATUS_VALID,
    _SUMMARY_CSV,
    _extraction_wall_time,
    _first_finite,
    _generate_wall_time,
    _physics_status,
    _quantities,
    _read_object,
    _read_summary_row,
    resolve_data_root,
)

HF_M = "HF_m"
LF_D = "LF_d"
FIDELITY_LEVELS = (HF_M, LF_D)
STATUS_VALID_NAME = "valid"
STATUS_GATE_FAIL = "gate_fail"
STATUS_CRASH = "crash"
DATASET_STATUSES = (STATUS_VALID_NAME, STATUS_GATE_FAIL, STATUS_CRASH)
_RUN_SUFFIX = {HF_M: "hfm", LF_D: "lfd"}
_LEVEL_NAMES = ("d_p_mm", "d_h_mm", "d_f_mm")

COLUMNS = (
    "d_p_mm",
    "d_h_mm",
    "d_f_mm",
    "fidelity",
    "status",
    "lmh",
    "cp_excess",
    "dp_per_l",
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
    "generate_wall_time_s",
    "wall_s",
    "recorded_cost_s",
    "geo_id",
    "mesh_id",
    "run_id",
    "failure_reason",
    "leaf_path",
)


@dataclass(frozen=True)
class Observation:
    d_p_mm: float
    d_h_mm: float
    d_f_mm: float
    fidelity: str
    status: str
    lmh: float | None = None
    cp_excess: float | None = None
    dp_per_l: float | None = None
    mesh_wall_time_s: float | None = None
    solver_wall_time_s: float | None = None
    extraction_wall_time_s: float | None = None
    generate_wall_time_s: float | None = None
    wall_s: float | None = None
    recorded_cost_s: float | None = None
    geo_id: str = ""
    mesh_id: str = ""
    run_id: str = ""
    failure_reason: str | None = None
    leaf_path: str = ""

    def as_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for name in COLUMNS:
            value = getattr(self, name)
            row[name] = "" if value is None else value
        return row


def collect_observations(data_root: str | Path, *, run_id: str = "u0p2_p6M") -> list[Observation]:
    """Every ``HF_m`` and ``LF_d`` run leaf at ``run_id``, including archived crashes."""
    root = resolve_data_root(data_root)
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"run_id must be a non-empty string, got {run_id!r}.")
    runs = root / "runs" / FAMILY
    if not runs.is_dir():
        raise ValueError(f"No pillar runs under {root}.")
    found: list[Observation] = []
    for geo_dir in _child_dirs(runs):
        if parse_mfbo_pillar_geo_id(geo_dir.name) is None:
            continue
        for mesh_dir in _child_dirs(geo_dir):
            for run_dir in _child_dirs(mesh_dir):
                fidelity = _fidelity_of(run_dir.name, run_id)
                if fidelity is None:
                    continue
                found.append(
                    _observation(
                        root,
                        geo_id=geo_dir.name,
                        mesh_id=mesh_dir.name,
                        run_dir=run_dir,
                        fidelity=fidelity,
                    )
                )
    found.sort(key=lambda item: (item.d_p_mm, item.d_h_mm, item.fidelity, item.leaf_path))
    if not found:
        raise ValueError(
            f"No {HF_M} or {LF_D} leaves for run_id {run_id} under {runs}."
        )
    return found


def write_dataset(path: str | Path, rows: Sequence[Observation]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return destination


def read_dataset(path: str | Path) -> list[Observation]:
    file = Path(path)
    with file.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "d_p_mm" not in reader.fieldnames:
            raise ValueError(f"Dataset CSV is missing d_p_mm: {file}")
        missing = [name for name in COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"Dataset CSV {file} is missing {missing}.")
        rows = [_observation_from_csv(row, index) for index, row in enumerate(reader)]
    if not rows:
        raise ValueError(f"Dataset CSV has no rows: {file}")
    return rows


def _observation(
    data_root: Path,
    *,
    geo_id: str,
    mesh_id: str,
    run_dir: Path,
    fidelity: str,
) -> Observation:
    d_p_mm, d_h_mm, d_f_mm = _design_mm(data_root, geo_id)
    mesh_manifest = _read_object(data_root / "meshes" / FAMILY / geo_id / mesh_id / "manifest.json") or {}
    mesh_record = _read_object(data_root / "meshes" / FAMILY / geo_id / mesh_id / "mesh_run_record.json") or {}
    run_manifest = _read_object(run_dir / "manifest.json") or {}
    retry = _read_object(run_dir / "solver_retry_record.json") or {}
    summary_path = run_dir / _SUMMARY_CSV
    summary = _read_summary_row(summary_path)
    status, reason, quantities = _leaf_status(summary_path, summary, run_manifest, mesh_manifest)
    dataset_status = _dataset_status(status, run_manifest)
    mesh_s = _first_finite(
        mesh_manifest.get("mesh_wall_time_s"),
        mesh_record.get("mesh_wall_time_s"),
        mesh_record.get("wall_time_seconds"),
    )
    solver_s = _first_finite(
        run_manifest.get("solver_wall_time_s"),
        retry.get("wall_time_seconds"),
    )
    extraction_s, _source = _extraction_wall_time(run_manifest, run_dir)
    generate_s = _generate_wall_time(data_root, {"geo_id": geo_id})
    wall_s = _sum_present(mesh_s, solver_s, extraction_s)
    recorded = _recorded_cost(generate_s, mesh_s, solver_s, extraction_s)
    cp_average = quantities.get("cp_average")
    return Observation(
        d_p_mm=d_p_mm,
        d_h_mm=d_h_mm,
        d_f_mm=d_f_mm,
        fidelity=fidelity,
        status=dataset_status,
        lmh=quantities.get("lmh"),
        cp_excess=None if cp_average is None else cp_average - 1.0,
        dp_per_l=quantities.get("pressure_drop_per_length_pa_per_m"),
        mesh_wall_time_s=mesh_s,
        solver_wall_time_s=solver_s,
        extraction_wall_time_s=extraction_s,
        generate_wall_time_s=generate_s,
        wall_s=wall_s,
        recorded_cost_s=recorded,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_dir.name,
        failure_reason=reason,
        leaf_path=str(run_dir),
    )


def _leaf_status(
    summary_path: Path,
    summary: Mapping[str, Any] | None,
    run_manifest: Mapping[str, Any],
    mesh_manifest: Mapping[str, Any],
) -> tuple[str, str | None, dict[str, Any]]:
    """Same status rules as ``_result_from_leaves``, without writing the manifest."""
    status, reason = _physics_status(run_manifest)
    if summary_path.is_file() and summary is None:
        return STATUS_EXECUTION_FAILED, "summary_metrics_unreadable", {}
    if summary is None and status == STATUS_VALID:
        return STATUS_EXECUTION_FAILED, "summary_metrics_missing", {}
    quantities = _quantities(summary, mesh_manifest)
    if status == STATUS_VALID and (
        quantities["lmh"] is None or quantities["pressure_drop_per_length_pa_per_m"] is None
    ):
        status = STATUS_INVALID
        reason = "missing_qoi"
    if status == STATUS_VALID and quantities["lmh_module_area"] is None:
        status = STATUS_INVALID
        reason = "missing_lmh_module_area"
    return status, reason, quantities


def _dataset_status(adapter_status: str, run_manifest: Mapping[str, Any]) -> str:
    if adapter_status == STATUS_VALID:
        return STATUS_VALID_NAME
    quality = run_manifest.get("convergence_quality")
    stop = run_manifest.get("stop_reason")
    if quality == "FAIL" and stop != "diverged":
        return STATUS_GATE_FAIL
    return STATUS_CRASH


def _design_mm(data_root: Path, geo_id: str) -> tuple[float, float, float]:
    encoded = parse_mfbo_pillar_geo_id(geo_id)
    if encoded is None:
        raise ValueError(f"Not an MFBO pillar geo_id: {geo_id!r}.")
    meta = data_root / "geometries" / FAMILY / geo_id / f"{geo_id}_meta.json"
    payload = _read_object(meta)
    inputs = payload.get("inputs") if isinstance(payload, dict) else None
    if isinstance(inputs, dict) and all(name in inputs for name in _LEVEL_NAMES):
        values = tuple(float(inputs[name]) for name in _LEVEL_NAMES)
        for name, micrometres, millimetres in zip(_LEVEL_NAMES, encoded, values):
            if int(round(millimetres * 1000.0)) != micrometres:
                raise ValueError(
                    f"{geo_id} meta {name}={millimetres} does not round to "
                    f"{micrometres} um."
                )
        return values[0], values[1], values[2]
    return tuple(micrometres / 1000.0 for micrometres in encoded)


def _fidelity_of(name: str, run_id: str) -> str | None:
    for fidelity, suffix in _RUN_SUFFIX.items():
        base = f"{run_id}_{suffix}"
        if name == base or name.startswith(f"{base}_run_failed_"):
            return fidelity
    return None


def _observation_from_csv(row: Mapping[str, str], index: int) -> Observation:
    fidelity = (row.get("fidelity") or "").strip()
    status = (row.get("status") or "").strip()
    if fidelity not in FIDELITY_LEVELS:
        raise ValueError(f"Row {index} fidelity must be one of {FIDELITY_LEVELS}, got {fidelity!r}.")
    if status not in DATASET_STATUSES:
        raise ValueError(f"Row {index} status must be one of {DATASET_STATUSES}, got {status!r}.")
    return Observation(
        d_p_mm=_required_float(row, "d_p_mm", index),
        d_h_mm=_required_float(row, "d_h_mm", index),
        d_f_mm=_required_float(row, "d_f_mm", index),
        fidelity=fidelity,
        status=status,
        lmh=_optional_float(row.get("lmh")),
        cp_excess=_optional_float(row.get("cp_excess")),
        dp_per_l=_optional_float(row.get("dp_per_l")),
        mesh_wall_time_s=_optional_float(row.get("mesh_wall_time_s")),
        solver_wall_time_s=_optional_float(row.get("solver_wall_time_s")),
        extraction_wall_time_s=_optional_float(row.get("extraction_wall_time_s")),
        generate_wall_time_s=_optional_float(row.get("generate_wall_time_s")),
        wall_s=_optional_float(row.get("wall_s")),
        recorded_cost_s=_optional_float(row.get("recorded_cost_s")),
        geo_id=(row.get("geo_id") or "").strip(),
        mesh_id=(row.get("mesh_id") or "").strip(),
        run_id=(row.get("run_id") or "").strip(),
        failure_reason=(row.get("failure_reason") or "").strip() or None,
        leaf_path=(row.get("leaf_path") or "").strip(),
    )


def _required_float(row: Mapping[str, str], name: str, index: int) -> float:
    value = _optional_float(row.get(name))
    if value is None:
        raise ValueError(f"Row {index} is missing {name}.")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a number, got {value!r}.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _sum_present(*parts: float | None) -> float | None:
    present = [float(part) for part in parts if part is not None]
    if not present:
        return None
    return float(sum(present))


def _recorded_cost(*parts: float | None) -> float | None:
    """Loop failure charge: present stage times, missing stages as zero."""
    if all(part is None for part in parts):
        return None
    return float(sum(0.0 if part is None else float(part) for part in parts))


def _child_dirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())
