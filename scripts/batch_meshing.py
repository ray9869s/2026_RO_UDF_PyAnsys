# ==========================================================
# batch_meshing.py
# Sequential batch driver for meshing multiple geometries
# Location: My_CFD_Project/01_Scripts/batch_meshing.py
# Usage: python My_CFD_Project/01_Scripts/batch_meshing.py
# ==========================================================

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ro.campaign_geo_ids import assert_selected_cases_are_not_legacy_ml
from ro.campaign_matrix import (
    CASE_SET_CHOICES,
    CASE_SET_EXPLORATORY,
    CASE_SET_PRODUCTION,
    cases_for_case_set,
)
from ro.mesh_common import (
    MESH_METRIC_NAMES,
    MESH_PARAMETER_NAMES,
    build_mesh_ledger_record,
    load_mesh_run_record,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_from_log,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
    write_mesh_run_record,
)
from ro.manifest import ManifestError, read_mesh_manifest
from ro.paths import data_root, mesh_dir, meshes_root, project_root
from ro.session_retry import (
    RETRY_KIND_SOCKET_RESET,
    is_session_socket_reset_failure,
)
from ro.solver_common import merge_batch_case_overrides, sha256_file

SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_CONFIG_PATH = project_root() / "configs" / "batch_config.py"
BASE_RUN_CONFIG_PATH = project_root() / "configs" / "run_config.py"
MESHING_SCRIPT_PATH = SCRIPT_DIR / "meshing_code_260616.py"

# Narrow CAD-import contention signatures (Fluent Discovery / PartMgr).
CAD_ATTACH_ASSEMBLY_PATTERNS = (
    re.compile(r"AttachAssembly", re.IGNORECASE),
    re.compile(r"attaching to assembly failed", re.IGNORECASE),
    re.compile(r"Error in CAD Import", re.IGNORECASE),
    re.compile(r"pIPartMgr", re.IGNORECASE),
)

RETRY_KIND_CAD_ATTACH = "cad_attach_assembly"

# Process-image markers checked after a failed case settles.
LEFTOVER_PROCESS_MARKERS = (
    "fluent",
    "discovery",
    "cadreaders",
    "cadreader",
)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_overrides(case_dict, common_settings):
    """Merge common settings and one case entry into the worker override dict."""
    overrides = merge_batch_case_overrides(common_settings, case_dict)
    overrides["geo_name"] = overrides["geo_id"]
    if "case_name" not in overrides:
        overrides["case_name"] = overrides["mesh_id"]
    overrides.pop("mesh_case_name", None)
    return overrides


def _continue_on_failure(batchcfg):
    return getattr(batchcfg, "continue_on_failure", True)


def mesh_case_label(geo_id, mesh_id):
    return f"{geo_id}/{mesh_id}"


def format_selected_mesh_case(index, total, case):
    """One family/geo_id/mesh_id line for the pre-Fluent case list."""
    family = case.get("family") or "?"
    geo_id = case.get("geo_id") or "?"
    mesh_id = case.get("mesh_id") or "?"
    return f"  {index}/{total}  {family}/{geo_id}/{mesh_id}"


MESH_SKIP_LAYOUT_FIELDS = (
    "n_active_cells",
    "cell_length_x_m",
    "buffer_length_in_m",
    "buffer_length_out_m",
    "periodic_shift_y_m",
)


def expected_mesh_layout_from_case(case_dict, common_mesh_settings=None):
    """Layout skip compares against: current case after common-mesh merge.

    ``periodic_shift_y`` on the case is millimetres (Fluent ShiftY). The
    manifest stores metres.
    """
    merged = merge_batch_case_overrides(common_mesh_settings or {}, case_dict)
    missing = [
        key
        for key in (
            "n_active_cells",
            "cell_length_x_m",
            "buffer_length_in_m",
            "buffer_length_out_m",
        )
        if merged.get(key) is None
    ]
    if merged.get("periodic_shift_y_m") is None and merged.get("periodic_shift_y") is None:
        missing.append("periodic_shift_y")
    if missing:
        raise ValueError(
            "case dict missing layout fields for mesh skip: "
            + ", ".join(missing)
        )
    if merged.get("periodic_shift_y_m") is not None:
        shift_m = float(merged["periodic_shift_y_m"])
    else:
        shift_m = float(merged["periodic_shift_y"]) * 1.0e-3
    return {
        "n_active_cells": int(merged["n_active_cells"]),
        "cell_length_x_m": float(merged["cell_length_x_m"]),
        "buffer_length_in_m": float(merged["buffer_length_in_m"]),
        "buffer_length_out_m": float(merged["buffer_length_out_m"]),
        "periodic_shift_y_m": shift_m,
    }


def _mesh_skip_layout_field_equal(field, observed, expected):
    if field == "n_active_cells":
        try:
            return int(observed) == int(expected)
        except (TypeError, ValueError):
            return False
    try:
        return math.isclose(
            float(observed),
            float(expected),
            rel_tol=1.0e-9,
            abs_tol=0.0,
        )
    except (TypeError, ValueError):
        return False


def mesh_skip_block_reason(mesh_directory, mesh_file, expected_layout):
    """Return None when this leaf may be skipped; else why it must remesh.

    File existence is not enough. Skip requires a nonempty ``.msh.h5``, a
    ``manifest.json`` that ``read_mesh_manifest`` accepts, a matching
    ``mesh_sha256``, and layout fields equal to the current case dict.
    """
    if expected_layout is None:
        return "no current-case layout for skip"
    mesh_file = Path(mesh_file)
    if not mesh_file.is_file():
        return f"mesh file was not found: {mesh_file}"
    try:
        size = mesh_file.stat().st_size
    except OSError as exc:
        return f"mesh file size could not be read ({mesh_file}): {exc}"
    if size <= 0:
        return f"mesh file is empty: {mesh_file}"
    try:
        payload = read_mesh_manifest(mesh_directory)
    except (OSError, ManifestError) as exc:
        return f"mesh manifest not usable for skip: {exc}"
    recorded = payload.get("mesh_sha256")
    if not recorded:
        return "missing mesh_sha256"
    try:
        digest = sha256_file(mesh_file)
    except OSError as exc:
        return f"mesh file not readable for skip: {exc}"
    if digest != recorded:
        return "mesh_sha256 does not match mesh file"
    for field in MESH_SKIP_LAYOUT_FIELDS:
        if field not in payload:
            return f"manifest missing layout field {field}"
        if not _mesh_skip_layout_field_equal(
            field, payload[field], expected_layout[field]
        ):
            return (
                f"manifest {field}={payload[field]!r} does not match "
                f"current case {expected_layout[field]!r}"
            )
    return None


def parse_batch_meshing_cli(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Sequential meshing batch from configs/batch_config.py. "
            "Default --case-set exploratory uses mesh_batch_cases. "
            "Pass --case-set production for production_mesh_batch_cases (31)."
        ),
    )
    parser.add_argument(
        "--case-set",
        choices=CASE_SET_CHOICES,
        default=CASE_SET_EXPLORATORY,
        help=(
            "exploratory: mesh_batch_cases "
            "(D0817_a30 max085_min006_cpg7_bl4_peel2). "
            "production: production_mesh_batch_cases (31). "
            "Default exploratory so existing batch runs cannot launch the 31-mesh set."
        ),
    )
    parser.add_argument(
        "--geo-id",
        help="Run only selected case-set entries with this geo_id.",
    )
    parser.add_argument(
        "--mesh-id",
        help="Run only selected case-set entries with this mesh_id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the selected cases and skip/dry-run decisions without "
            "launching Fluent. ORs with batch_config.dry_run. Existing-mesh "
            "skip still wins when skip evidence is complete."
        ),
    )
    parser.add_argument(
        "--report-skip",
        action="store_true",
        help=(
            "Print skip evidence for the 31 production mesh leaves plus any "
            "other leaf under meshes/. Does not launch Fluent. Ignores "
            "--case-set (always production + extras)."
        ),
    )
    return parser.parse_args(argv)


def select_mesh_batch_cases(cases, *, geo_id=None, mesh_id=None):
    """Return mesh_batch_cases entries matching optional geo_id / mesh_id filters."""
    selected = list(cases)
    if geo_id is not None:
        selected = [case for case in selected if case["geo_id"] == geo_id]
    if mesh_id is not None:
        selected = [case for case in selected if case["mesh_id"] == mesh_id]
    return selected


def classify_mesh_pre_execution(
    *,
    skip_existing_mesh,
    dry_run,
    mesh_directory=None,
    mesh_file=None,
    expected_layout=None,
    skip_block=None,
):
    """Decide dry-run vs complete-mesh skip before Fluent is launched.

    Existing-mesh skip wins over dry-run only when the mesh file, validated
    manifest, SHA, and current-case layout all match. File existence alone
    is not enough.
    """
    if skip_existing_mesh:
        if skip_block is None:
            skip_block = mesh_skip_block_reason(
                mesh_directory,
                mesh_file,
                expected_layout,
            )
        if skip_block is None:
            return "skipped_existing", "complete current-case mesh"
    if dry_run:
        return "dry_run", skip_block
    return "run", skip_block


def _mesh_file_for_leaf(mesh_directory, geo_id, mesh_id):
    expected = Path(mesh_directory) / f"{geo_id}_{mesh_id}.msh.h5"
    if expected.is_file():
        return expected
    matches = sorted(Path(mesh_directory).glob("*.msh.h5"))
    if matches:
        return matches[0]
    return expected


def _iter_on_disk_mesh_leaves(root):
    """Yield three-level mesh directories that have a .msh.h5 or manifest.json."""
    root = Path(root)
    if not root.is_dir():
        return
    for family_dir in sorted(root.iterdir()):
        if not family_dir.is_dir():
            continue
        for geo_dir in sorted(family_dir.iterdir()):
            if not geo_dir.is_dir():
                continue
            for mesh_directory in sorted(geo_dir.iterdir()):
                if not mesh_directory.is_dir():
                    continue
                has_msh = any(mesh_directory.glob("*.msh.h5"))
                has_manifest = (mesh_directory / "manifest.json").is_file()
                if has_msh or has_manifest:
                    yield mesh_directory


def _case_for_skip_report(family, geo_id, mesh_id, production_by_geo, common_mesh_settings):
    """Return (case_dict, layout_error) for --report-skip layout comparison."""
    case = production_by_geo.get(geo_id)
    if case is None:
        return None, f"no current-case layout (geo_id {geo_id!r} not in production set)"
    synthetic = dict(case)
    synthetic["family"] = family
    synthetic["geo_id"] = geo_id
    synthetic["mesh_id"] = mesh_id
    try:
        expected_mesh_layout_from_case(synthetic, common_mesh_settings)
    except (TypeError, ValueError) as exc:
        return None, f"no current-case layout: {exc}"
    return synthetic, None


def inspect_mesh_skip_leaf(
    mesh_directory,
    mesh_file,
    expected_layout,
    *,
    in_production,
    family,
    geo_id,
    mesh_id,
):
    """Skip evidence for one leaf. ``skip_decision`` is SKIP or RUN."""
    mesh_directory = Path(mesh_directory)
    mesh_file = Path(mesh_file)
    msh_exists = mesh_file.is_file()
    msh_size = mesh_file.stat().st_size if msh_exists else 0
    manifest_path = mesh_directory / "manifest.json"
    manifest_exists = manifest_path.is_file()
    manifest_valid = False
    sha_match = None
    layout_match = None
    if expected_layout is None:
        skip_block = "no current-case layout for skip"
    else:
        skip_block = mesh_skip_block_reason(
            mesh_directory, mesh_file, expected_layout
        )
    if manifest_exists:
        try:
            payload = read_mesh_manifest(mesh_directory)
            manifest_valid = True
        except (OSError, ManifestError):
            payload = None
        if payload is not None and msh_exists and msh_size > 0:
            try:
                digest = sha256_file(mesh_file)
            except OSError:
                sha_match = False
            else:
                sha_match = payload.get("mesh_sha256") == digest
            if expected_layout is not None:
                layout_match = all(
                    field in payload
                    and _mesh_skip_layout_field_equal(
                        field, payload[field], expected_layout[field]
                    )
                    for field in MESH_SKIP_LAYOUT_FIELDS
                )
    return {
        "in_production": bool(in_production),
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "leaf": f"{family}/{geo_id}/{mesh_id}",
        "mesh_directory": str(mesh_directory),
        "msh_exists": msh_exists,
        "msh_size": msh_size,
        "manifest_exists": manifest_exists,
        "manifest_valid": manifest_valid,
        "sha_match": sha_match,
        "layout_match": layout_match,
        "skip_block": skip_block,
        "skip_decision": "SKIP" if skip_block is None else "RUN",
    }


def _format_skip_report_flag(value, *, yes="match", no="mismatch"):
    if value is True:
        return yes
    if value is False:
        return no
    return "n/a"


def format_mesh_skip_report_row(row):
    msh = (
        f"msh=yes:{row['msh_size']}" if row["msh_exists"] else "msh=no"
    )
    if not row["manifest_exists"]:
        manifest = "manifest=missing"
    elif row["manifest_valid"]:
        manifest = "manifest=ok"
    else:
        manifest = "manifest=invalid"
    scope = "production" if row["in_production"] else "extra"
    reason = "" if row["skip_block"] is None else row["skip_block"]
    return (
        f"{scope:11} {row['skip_decision']:4}  {msh}  {manifest}  "
        f"sha={_format_skip_report_flag(row['sha_match'])}  "
        f"layout={_format_skip_report_flag(row['layout_match'])}  "
        f"{row['leaf']}"
        + (f"  reason={reason}" if reason else "")
    )


def report_mesh_skip_status(
    production_cases,
    common_mesh_settings,
    *,
    root=None,
    file=None,
):
    """Inspect production leaves plus any extra on-disk mesh leaf.

    Does not launch Fluent. Returns the row dicts; also prints them.
    """
    if file is None:
        file = sys.stdout
    root = Path(root) if root is not None else meshes_root()
    production_cases = list(production_cases)
    production_by_geo = {}
    for case in production_cases:
        production_by_geo[case["geo_id"]] = case

    rows = []
    seen = set()
    for case in production_cases:
        family = case["family"]
        geo_id = case["geo_id"]
        mesh_id = case["mesh_id"]
        seen.add((family, geo_id, mesh_id))
        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        mesh_file = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
        expected_layout = expected_mesh_layout_from_case(
            case, common_mesh_settings
        )
        rows.append(
            inspect_mesh_skip_leaf(
                mesh_directory,
                mesh_file,
                expected_layout,
                in_production=True,
                family=family,
                geo_id=geo_id,
                mesh_id=mesh_id,
            )
        )

    extra_count = 0
    for mesh_directory in _iter_on_disk_mesh_leaves(root):
        family = mesh_directory.parent.parent.name
        geo_id = mesh_directory.parent.name
        mesh_id = mesh_directory.name
        key = (family, geo_id, mesh_id)
        if key in seen:
            continue
        extra_count += 1
        seen.add(key)
        case, layout_error = _case_for_skip_report(
            family, geo_id, mesh_id, production_by_geo, common_mesh_settings
        )
        expected_layout = None
        if case is not None:
            expected_layout = expected_mesh_layout_from_case(
                case, common_mesh_settings
            )
        mesh_file = _mesh_file_for_leaf(mesh_directory, geo_id, mesh_id)
        row = inspect_mesh_skip_leaf(
            mesh_directory,
            mesh_file,
            expected_layout,
            in_production=False,
            family=family,
            geo_id=geo_id,
            mesh_id=mesh_id,
        )
        if layout_error is not None and row["skip_block"] == "no current-case layout for skip":
            row["skip_block"] = layout_error
        rows.append(row)

    print("MESH SKIP REPORT (no Fluent)", file=file)
    print(f"meshes_root={root}", file=file)
    for row in rows:
        print(format_mesh_skip_report_row(row), file=file)

    prod_rows = [row for row in rows if row["in_production"]]
    extra_rows = [row for row in rows if not row["in_production"]]
    prod_skip = sum(1 for row in prod_rows if row["skip_decision"] == "SKIP")
    extra_skip = sum(1 for row in extra_rows if row["skip_decision"] == "SKIP")
    print(
        f"Summary: production {prod_skip} SKIP / "
        f"{len(prod_rows) - prod_skip} RUN "
        f"(listed {len(prod_rows)}); extra {extra_skip} SKIP / "
        f"{len(extra_rows) - extra_skip} RUN "
        f"(listed {len(extra_rows)}, walked {extra_count}).",
        file=file,
    )
    return rows


def is_cad_attach_assembly_failure(text):
    """True when failure text matches the Discovery AttachAssembly signature."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in CAD_ATTACH_ASSEMBLY_PATTERNS)


def classify_retryable_session_failure(text):
    """Return retry kind for session-contention failures, else None.

    CAD AttachAssembly and socket-reset are both retryable but kept as
    distinct kinds so logs and mesh_run_record stay distinguishable.
    """
    if is_cad_attach_assembly_failure(text):
        return RETRY_KIND_CAD_ATTACH
    if is_session_socket_reset_failure(text):
        return RETRY_KIND_SOCKET_RESET
    return None


def collect_session_failure_evidence(mesh_log_path, mesh_run_record_path):
    """Concatenate worker error_summary and mesh log for retry matching.

    pyfluent_watchdog.err is intentionally excluded: it is usually benign
    noise and must not drive retry classification.
    """
    chunks = []
    record = load_mesh_run_record(mesh_run_record_path)
    if record:
        error_summary = record.get("error_summary") or ""
        if error_summary:
            chunks.append(str(error_summary))
    mesh_log_path = Path(mesh_log_path)
    if mesh_log_path.is_file():
        try:
            chunks.append(
                mesh_log_path.read_text(encoding="utf-8", errors="ignore")
            )
        except OSError:
            pass
    return "\n".join(chunks)


# Backward-compatible alias used by older tests/imports.
collect_cad_failure_evidence = collect_session_failure_evidence


def cleanup_fm_scratch_dirs(mesh_directory):
    """Remove Fluent FM_<HOST>_<PID>/ scratch dirs under a mesh leaf.

    Returns the list of removed directory paths.
    """
    mesh_directory = Path(mesh_directory)
    removed = []
    if not mesh_directory.is_dir():
        return removed
    for child in sorted(mesh_directory.iterdir()):
        if child.is_dir() and child.name.startswith("FM_"):
            shutil.rmtree(child)
            removed.append(child)
            print(f"Removed Fluent scratch directory: {child}")
    return removed


def _process_line_matches_leftover(line):
    lowered = line.lower()
    return any(marker in lowered for marker in LEFTOVER_PROCESS_MARKERS)


def list_leftover_meshing_processes(*, runner=None):
    """Return leftover Fluent/Discovery/CADReaders process lines, if any."""
    if runner is None:
        runner = subprocess.run
    lines = []
    try:
        if os.name == "nt":
            result = runner(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            raw = result.stdout or ""
            for line in raw.splitlines():
                if _process_line_matches_leftover(line):
                    lines.append(line.strip())
        else:
            result = runner(
                ["ps", "-eo", "pid=,comm=,args="],
                capture_output=True,
                text=True,
                check=False,
            )
            raw = result.stdout or ""
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped and _process_line_matches_leftover(stripped):
                    lines.append(stripped)
    except OSError as exc:
        print(f"Warning: could not list leftover meshing processes: {exc}")
        return []
    return lines


def settle_before_next_case(
    *,
    previous_status,
    inter_case_delay_s,
    post_failure_settle_s,
    sleeper=time.sleep,
    process_lister=None,
):
    """Sleep and scan for leftovers before starting the next meshing case.

    After a non-SUCCESS case, always settle at least post_failure_settle_s
    (even when inter_case_delay_s is 0). Returns leftover process lines.
    """
    if process_lister is None:
        process_lister = list_leftover_meshing_processes

    previous_failed = previous_status not in ("SUCCESS", "SUCCESS_AFTER_RETRY")
    if previous_failed:
        delay_s = max(float(inter_case_delay_s), float(post_failure_settle_s))
        reason = "post-failure"
    else:
        delay_s = float(inter_case_delay_s)
        reason = "inter-case"

    if delay_s > 0.0:
        print(
            f"{reason.capitalize()} settle delay: {delay_s:g}s "
            f"(inter_case_delay_s={inter_case_delay_s:g}, "
            f"post_failure_settle_s={post_failure_settle_s:g})."
        )
        sleeper(delay_s)

    leftovers = process_lister()
    if leftovers:
        print(
            "WARNING: leftover Fluent/Discovery/CADReaders processes still "
            f"present after {reason} settle ({len(leftovers)}):"
        )
        for line in leftovers:
            print(f"  LEFTOVER: {line}")
    elif previous_failed:
        print("No leftover Fluent/Discovery/CADReaders processes after settle.")
    return leftovers


def record_session_attempt_metadata(
    mesh_run_record_path,
    *,
    attempts,
    status,
    retry_kinds=None,
    prior_case_leftover_processes=None,
):
    """Persist attempt/retry metadata on the worker mesh_run_record.json."""
    path = Path(mesh_run_record_path)
    record = load_mesh_run_record(path) or {}
    attempts = int(attempts)
    retry_kinds = list(retry_kinds or [])
    record["transient_failure_attempts"] = attempts
    # Backward-compatible alias used by earlier batch summaries.
    record["cad_import_attempts"] = attempts
    record["succeeded_on_transient_retry"] = status == "SUCCESS_AFTER_RETRY"
    record["succeeded_on_cad_import_retry"] = status == "SUCCESS_AFTER_RETRY"
    record["retry_reasons"] = retry_kinds
    record["retry_reason"] = retry_kinds[-1] if retry_kinds else None
    record["session_retry_kinds"] = retry_kinds
    if prior_case_leftover_processes:
        record["prior_case_leftover_processes"] = list(
            prior_case_leftover_processes
        )
        record["prior_case_leftovers_detected"] = True
    write_mesh_run_record(path, record)
    return record


# Backward-compatible alias.
record_cad_import_attempts = record_session_attempt_metadata


def run_meshing_attempts(
    *,
    cmd,
    env,
    cwd,
    mesh_log_path,
    mesh_run_record_path,
    max_retries,
    runner=None,
):
    """Run the meshing worker, retrying CAD AttachAssembly / socket-reset.

    max_retries is the number of *retries* after the first attempt (default 2
    means up to 3 total invocations).

    Returns (result, attempts, retry_kinds).
    """
    if runner is None:
        runner = subprocess.run
    max_retries = max(0, int(max_retries))
    max_attempts = max_retries + 1
    result = None
    retry_kinds = []
    for attempt in range(1, max_attempts + 1):
        print(
            f"Meshing worker attempt {attempt}/{max_attempts}: "
            f"{' '.join(cmd)}"
        )
        result = runner(cmd, env=env, cwd=cwd, check=False)
        if result.returncode == 0:
            return result, attempt, retry_kinds

        evidence = collect_session_failure_evidence(
            mesh_log_path, mesh_run_record_path
        )
        retry_kind = classify_retryable_session_failure(evidence)
        can_retry = attempt < max_attempts and retry_kind is not None
        if can_retry:
            retry_kinds.append(retry_kind)
            if retry_kind == RETRY_KIND_CAD_ATTACH:
                print(
                    f"CAD AttachAssembly / Import failure on attempt {attempt}; "
                    f"retrying ({max_attempts - attempt} retry left)."
                )
            else:
                print(
                    f"Session socket-reset failure on attempt {attempt}; "
                    f"retrying ({max_attempts - attempt} retry left)."
                )
            continue

        if attempt < max_attempts:
            print(
                f"Non-retryable meshing failure on attempt {attempt} "
                f"(return code {result.returncode}); not retrying."
            )
        return result, attempt, retry_kinds

    return result, max_attempts, retry_kinds


def _format_summary_case_line(entry):
    label = entry["label"]
    status = entry.get("status", "")
    attempts = entry.get("attempts")
    wall = entry.get("wall_time_seconds")
    cell_count = entry.get("cell_count")
    attempts_s = "-" if attempts is None else str(attempts)
    wall_s = "-" if wall is None else f"{float(wall):.1f}s"
    cell_s = "-" if cell_count is None else str(cell_count)
    return (
        f"    {label}  status={status}  attempts={attempts_s}  "
        f"wall={wall_s}  cell_count={cell_s}"
    )


def _print_batch_summary(dry_run_cases, skipped_existing, successes, failures):
    print(f"\n{'='*72}")
    print("BATCH MESHING SUMMARY")
    print(f"{'='*72}")
    print(f"  dry_run          : {len(dry_run_cases)}")
    print(f"  skipped_existing : {len(skipped_existing)}")
    print(f"  succeeded        : {len(successes)}")
    print(f"  failed           : {len(failures)}")
    if dry_run_cases:
        print("  Dry-run cases:")
        for label in dry_run_cases:
            print(f"    {label}")
    if skipped_existing:
        print("  Skipped existing:")
        for label, reason in skipped_existing:
            print(f"    {label}  ({reason})")
    if successes:
        print("  Succeeded cases:")
        for entry in successes:
            if isinstance(entry, dict):
                print(_format_summary_case_line(entry))
            else:
                print(f"    {entry}")
    if failures:
        print("  FAILED cases:")
        for entry in failures:
            if isinstance(entry, dict):
                print(_format_summary_case_line(entry))
            else:
                print(f"    {entry}")
    print(f"{'='*72}\n")


def _resolved_mesh_parameters(base_cfg, overrides):
    values = {
        name: getattr(base_cfg, name, None)
        for name in MESH_PARAMETER_NAMES
    }
    values.update(overrides)
    return mesh_parameters_from_mapping(values)


def _write_case_ledger(
    *,
    ledger_path,
    geo_name,
    mesh_case_name,
    mesh_parameters,
    status,
    exit_code,
    wall_time_seconds,
    mesh_log_path,
    mesh_file_path,
    error_summary="",
):
    """Merge worker/log details and upsert one aggregate ledger row."""
    mesh_log_path = Path(mesh_log_path)
    worker_record_path = mesh_log_path.parent / "mesh_run_record.json"
    worker_record = load_mesh_run_record(worker_record_path)

    actual_parameters = dict(mesh_parameters)
    metrics = parse_mesh_metrics_from_log(mesh_log_path)
    if mesh_log_path.is_file():
        text = mesh_log_path.read_text(encoding="utf-8", errors="ignore")
        parsed_parameters = parse_meshing_input_summary(text)
        actual_parameters.update({
            key: value
            for key, value in parsed_parameters.items()
            if value is not None
        })

        if worker_record:
            actual_parameters.update({
                name: worker_record.get(name)
                for name in MESH_PARAMETER_NAMES
                if worker_record.get(name) is not None
            })
            metrics.update({
                name: worker_record.get(name)
                for name in MESH_METRIC_NAMES
                if worker_record.get(name) is not None
            })
            worker_error = worker_record.get("error_summary") or ""
            if worker_error:
                error_summary = worker_error

    record = build_mesh_ledger_record(
        geo_name=geo_name,
        mesh_case_name=mesh_case_name,
        mesh_parameters=actual_parameters,
        status=status,
        exit_code=exit_code,
        wall_time_seconds=wall_time_seconds,
        metrics=metrics,
        mesh_log_path=mesh_log_path,
        mesh_file_path=mesh_file_path,
        error_summary=error_summary,
    )
    upsert_mesh_ledger_csv(ledger_path, [record])
    return record


def main(argv=None):
    cli_args = parse_batch_meshing_cli(argv)
    batchcfg = _load_module("batch_config", BATCH_CONFIG_PATH)
    common_mesh_settings = getattr(batchcfg, "common_mesh_settings", {})

    if cli_args.report_skip:
        production_cases = cases_for_case_set(
            batchcfg,
            CASE_SET_PRODUCTION,
            exploratory_attr="mesh_batch_cases",
            production_attr="production_mesh_batch_cases",
        )
        report_mesh_skip_status(production_cases, common_mesh_settings)
        return 0

    dry_run = bool(getattr(batchcfg, "dry_run", False) or cli_args.dry_run)
    continue_on_failure = _continue_on_failure(batchcfg)
    skip_existing_mesh = getattr(batchcfg, "skip_existing_mesh", True)
    inter_case_delay_s = float(getattr(batchcfg, "inter_case_delay_s", 0.0))
    post_failure_settle_s = float(
        getattr(batchcfg, "post_failure_settle_s", 15.0)
    )
    # New name covers CAD AttachAssembly and socket-reset; keep reading the
    # old key so existing batch_config files still work.
    transient_failure_max_retries = int(
        getattr(
            batchcfg,
            "transient_failure_max_retries",
            getattr(batchcfg, "cad_import_max_retries", 2),
        )
    )
    clean_fm_scratch_on_success = bool(
        getattr(batchcfg, "clean_fm_scratch_on_success", True)
    )
    mesh_batch_cases = select_mesh_batch_cases(
        cases_for_case_set(
            batchcfg,
            cli_args.case_set,
            exploratory_attr="mesh_batch_cases",
            production_attr="production_mesh_batch_cases",
        ),
        geo_id=cli_args.geo_id,
        mesh_id=cli_args.mesh_id,
    )
    if cli_args.geo_id is not None or cli_args.mesh_id is not None:
        if not mesh_batch_cases:
            filters = []
            if cli_args.geo_id is not None:
                filters.append(f"geo_id={cli_args.geo_id!r}")
            if cli_args.mesh_id is not None:
                filters.append(f"mesh_id={cli_args.mesh_id!r}")
            raise SystemExit(
                f"No mesh_batch_cases match {' and '.join(filters)}."
            )
    assert_selected_cases_are_not_legacy_ml(mesh_batch_cases)

    base_cfg = _load_module("_base_cfg", BASE_RUN_CONFIG_PATH)
    ledger_path = data_root() / "inventory" / "mesh_ledger.csv"

    successes = []
    failures = []
    dry_run_cases = []
    skipped_existing = []
    executed_case_count = 0
    previous_status = None
    pending_leftovers = None

    total = len(mesh_batch_cases)
    print(f"\n{'='*72}")
    print(f"BATCH MESHING: {total} case(s)  case_set={cli_args.case_set}")
    print(
        f"dry_run={dry_run}  continue_on_failure={continue_on_failure}  "
        f"skip_existing_mesh={skip_existing_mesh}"
    )
    print(
        f"inter_case_delay_s={inter_case_delay_s}  "
        f"post_failure_settle_s={post_failure_settle_s}  "
        f"transient_failure_max_retries={transient_failure_max_retries}  "
        f"clean_fm_scratch_on_success={clean_fm_scratch_on_success}"
    )
    print("Selected cases (Fluent has not launched):")
    if not mesh_batch_cases:
        print("  (none)")
    else:
        for listed_idx, listed_case in enumerate(mesh_batch_cases, start=1):
            print(format_selected_mesh_case(listed_idx, total, listed_case))
    print(f"{'='*72}\n")

    for i, case_dict in enumerate(mesh_batch_cases):
        family = case_dict["family"]
        geo_id = case_dict["geo_id"]
        mesh_id = case_dict["mesh_id"]
        label = mesh_case_label(geo_id, mesh_id)
        overrides = _build_overrides(case_dict, common_mesh_settings)
        mesh_parameters = _resolved_mesh_parameters(base_cfg, overrides)

        print(f"\n{'='*72}")
        print(f"CASE {i + 1}/{total}: {label}")
        print(f"{'='*72}")

        mesh_directory = mesh_dir(family, geo_id, mesh_id)
        expected_mesh = mesh_directory / f"{geo_id}_{mesh_id}.msh.h5"
        mesh_log_path = mesh_directory / f"mesh_log_{mesh_id}.txt"
        mesh_run_record_path = mesh_directory / "mesh_run_record.json"
        print(f"Expected mesh output: {expected_mesh}")

        expected_layout = None
        if skip_existing_mesh:
            expected_layout = expected_mesh_layout_from_case(
                case_dict, common_mesh_settings
            )
        outcome, skip_reason = classify_mesh_pre_execution(
            skip_existing_mesh=skip_existing_mesh,
            dry_run=dry_run,
            mesh_directory=mesh_directory,
            mesh_file=expected_mesh,
            expected_layout=expected_layout,
        )
        if outcome == "skipped_existing":
            print(f"SKIP: {skip_reason}: {expected_mesh}")
            skipped_existing.append((label, skip_reason))
            _write_case_ledger(
                ledger_path=ledger_path,
                geo_name=geo_id,
                mesh_case_name=mesh_id,
                mesh_parameters=mesh_parameters,
                status="SKIPPED_EXISTING",
                exit_code=None,
                wall_time_seconds=0.0,
                mesh_log_path=mesh_log_path,
                mesh_file_path=expected_mesh,
            )
            continue

        if skip_existing_mesh and skip_reason:
            print(f"Not skipping existing mesh: {skip_reason}")

        print(f"Overrides: {json.dumps(overrides, indent=2)}")

        cmd = [sys.executable, str(MESHING_SCRIPT_PATH)]
        env = {**os.environ, "PYFLUENT_RUN_OVERRIDES": json.dumps(overrides)}
        # The worker must load the base run_config.py, not a leftover env config.
        env.pop("PYFLUENT_RUN_CONFIG", None)
        env.pop("PYFLUENT_SKIP_VALIDATION", None)

        print(f"Command: {' '.join(cmd)}")

        if outcome == "dry_run":
            print("[DRY RUN] Skipping Fluent execution.")
            dry_run_cases.append(label)
            continue

        prior_leftovers_for_record = None
        if executed_case_count > 0 and previous_status is not None:
            leftovers = settle_before_next_case(
                previous_status=previous_status,
                inter_case_delay_s=inter_case_delay_s,
                post_failure_settle_s=post_failure_settle_s,
            )
            if leftovers:
                prior_leftovers_for_record = leftovers
            pending_leftovers = leftovers

        started = time.monotonic()
        result, attempts, retry_kinds = run_meshing_attempts(
            cmd=cmd,
            env=env,
            cwd=str(SCRIPT_DIR),
            mesh_log_path=mesh_log_path,
            mesh_run_record_path=mesh_run_record_path,
            max_retries=transient_failure_max_retries,
        )
        wall_time_seconds = time.monotonic() - started
        executed_case_count += 1

        if result.returncode == 0:
            status = (
                "SUCCESS_AFTER_RETRY" if attempts > 1 else "SUCCESS"
            )
            print(
                f"\nSUCCESS: {label} (return code {result.returncode}, "
                f"attempts={attempts}, status={status})"
            )
        else:
            status = "FAILED"
            print(
                f"\nFAILED: {label} (return code {result.returncode}, "
                f"attempts={attempts})"
            )

        record_session_attempt_metadata(
            mesh_run_record_path,
            attempts=attempts,
            status=status,
            retry_kinds=retry_kinds,
            prior_case_leftover_processes=prior_leftovers_for_record,
        )

        ledger_record = _write_case_ledger(
            ledger_path=ledger_path,
            geo_name=geo_id,
            mesh_case_name=mesh_id,
            mesh_parameters=mesh_parameters,
            status=status,
            exit_code=result.returncode,
            wall_time_seconds=wall_time_seconds,
            mesh_log_path=mesh_log_path,
            mesh_file_path=expected_mesh,
            error_summary=(
                ""
                if result.returncode == 0
                else f"meshing worker exited with code {result.returncode}"
            ),
        )

        summary_entry = {
            "label": label,
            "status": status,
            "attempts": attempts,
            "wall_time_seconds": wall_time_seconds,
            "cell_count": ledger_record.get("cell_count"),
        }

        previous_status = status
        if result.returncode == 0:
            successes.append(summary_entry)
            if clean_fm_scratch_on_success:
                cleanup_fm_scratch_dirs(mesh_directory)
        else:
            failures.append(summary_entry)
            if not continue_on_failure:
                print("Stopping batch because continue_on_failure=False.")
                break

    _print_batch_summary(dry_run_cases, skipped_existing, successes, failures)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
