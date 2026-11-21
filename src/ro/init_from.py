"""Fresh solve initialised from another run leaf's converged final.

``mesh_study_case --init-from <run leaf>`` (D2450_a30 ``u0p2_p6M`` from its
``u0p1_p6M`` final). The source must be on the same data root, geometry
and mesh id, have a PASS gate, a finished stop reason and a final pair
whose SHA-256 equals the manifest. The solver sets this run up exactly
like a fresh solve (template, mesh, this run's inlet velocity, UDF
compiled for this run), then reads a staged copy of the source final
data instead of hybrid initialisation and resets the residual history
and iteration counter (``solution.monitor.residual.reset``), so the UDF
ramp (``N_ITER``) starts again. The run manifest records ``init_from``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ro.solver_common import FINAL_CASE_SHA256_FIELD, FINAL_DATA_SHA256_FIELD, sha256_file

INIT_FROM_FIELD = "init_from"
_RECORD_KEYS = (
    "leaf",
    "run_id",
    "mesh_id",
    "final_case_file",
    "final_data_file",
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    "iterations_run",
    "stop_reason",
    "convergence_quality",
)


class InitFromError(ValueError):
    pass


def staged_init_data_path(case_path, geo_id, case_id):
    """Copy of the source data inside the target leaf (Fluent reads only there)."""
    return Path(case_path) / f"{geo_id}_{case_id}_init_from.dat.h5"


def resolve_init_from_leaf(
    source,
    *,
    data_root,
    family,
    geo_id,
    mesh_id,
    case_id,
    mesh_sha256=None,
    hasher=sha256_file,
):
    """Validate the source leaf and return the ``init_from`` record."""
    leaf = Path(source).expanduser().resolve()
    expected_parent = (Path(data_root) / "runs" / family / geo_id / mesh_id).resolve()
    if leaf.parent != expected_parent:
        raise InitFromError(
            f"--init-from must be a run leaf of the same geometry and mesh: expected "
            f"{expected_parent}/<run>, got {leaf}."
        )
    run_id = leaf.name
    if run_id == case_id:
        raise InitFromError(f"--init-from names the run being solved ({case_id}): {leaf}.")
    if (leaf / "BROKEN").exists():
        raise InitFromError(f"--init-from leaf has a BROKEN marker: {leaf}.")
    manifest_path = leaf / "manifest.json"
    if not manifest_path.is_file():
        raise InitFromError(f"--init-from leaf has no manifest: {manifest_path}.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise InitFromError(f"--init-from manifest is not a JSON object: {manifest_path}.")
    gate = manifest.get("convergence_quality")
    if gate != "PASS":
        raise InitFromError(f"--init-from leaf gate is {gate!r}, not PASS: {leaf}.")
    stop_reason = manifest.get("stop_reason")
    if stop_reason in (None, "RUNNING"):
        raise InitFromError(f"--init-from leaf is not finished (stop_reason={stop_reason!r}): {leaf}.")
    if manifest.get("run_id") not in (None, run_id):
        raise InitFromError(
            f"--init-from manifest run_id {manifest.get('run_id')!r} differs from {run_id}."
        )
    recorded_mesh = manifest.get("mesh_sha256")
    if mesh_sha256 and recorded_mesh and recorded_mesh != mesh_sha256:
        raise InitFromError(
            f"--init-from leaf was solved on a different mesh file (mesh_sha256 "
            f"{str(recorded_mesh)[:12]} vs {str(mesh_sha256)[:12]}): {leaf}."
        )
    case_file = leaf / f"{geo_id}_{run_id}_final.cas.h5"
    data_file = leaf / f"{geo_id}_{run_id}_final.dat.h5"
    for path in (case_file, data_file):
        if not path.is_file() or path.stat().st_size <= 0:
            raise InitFromError(f"--init-from final is missing or empty: {path}.")
    recorded = (manifest.get(FINAL_CASE_SHA256_FIELD), manifest.get(FINAL_DATA_SHA256_FIELD))
    if not all(isinstance(value, str) and value for value in recorded):
        raise InitFromError(f"--init-from manifest has no final hashes: {manifest_path}.")
    actual = (hasher(case_file), hasher(data_file))
    if actual != recorded:
        raise InitFromError(
            "--init-from final pair does not match its manifest hashes "
            f"(case {actual[0][:12]} vs {recorded[0][:12]}, data {actual[1][:12]} vs "
            f"{recorded[1][:12]}): {leaf}."
        )
    return {
        "leaf": str(leaf),
        "run_id": run_id,
        "mesh_id": mesh_id,
        "final_case_file": str(case_file),
        "final_data_file": str(data_file),
        FINAL_CASE_SHA256_FIELD: actual[0],
        FINAL_DATA_SHA256_FIELD: actual[1],
        "iterations_run": manifest.get("iterations_run"),
        "stop_reason": stop_reason,
        "convergence_quality": gate,
    }


def require_init_from_record(record):
    """Shape check for the solver override; returns the record."""
    if not isinstance(record, dict):
        raise InitFromError(f"init_from must be a dict, got {type(record).__name__}.")
    missing = [key for key in _RECORD_KEYS if key not in record]
    if missing:
        raise InitFromError(f"init_from is missing {missing}.")
    for key in ("leaf", "final_data_file", FINAL_DATA_SHA256_FIELD, FINAL_CASE_SHA256_FIELD):
        if not isinstance(record[key], str) or not record[key].strip():
            raise InitFromError(f"init_from {key} must be a non-empty string.")
    if record["convergence_quality"] != "PASS":
        raise InitFromError("init_from source gate must be PASS.")
    return record


def stage_init_data(record, case_path, geo_id, case_id, *, hasher=sha256_file):
    """Copy the source data into the target leaf and prove the copy by hash."""
    source = Path(record["final_data_file"])
    staged = staged_init_data_path(case_path, geo_id, case_id)
    if staged.resolve() == source.resolve():
        raise InitFromError(f"init_from staging would overwrite the source: {source}.")
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, staged)
    digest = hasher(staged)
    if digest != record[FINAL_DATA_SHA256_FIELD]:
        staged.unlink(missing_ok=True)
        raise InitFromError(
            f"init_from staged data hash {digest[:12]} differs from the source "
            f"{record[FINAL_DATA_SHA256_FIELD][:12]}: {source}."
        )
    return staged


def require_counter_restarted(bounds):
    """The solve's residual rows must start at iteration 1 (counter reset)."""
    if bounds is None:
        raise InitFromError("init_from: no residual rows; cannot confirm the counter restarted.")
    first = int(bounds[0])
    if first > 1:
        raise InitFromError(
            f"init_from: the first residual row is iteration {first}, not 1; the "
            "iteration counter (and the UDF ramp) did not restart. Refusing the final."
        )
    return first


def manifest_record(record):
    """The ``init_from`` manifest field (no local-only keys)."""
    return {key: record[key] for key in _RECORD_KEYS}
