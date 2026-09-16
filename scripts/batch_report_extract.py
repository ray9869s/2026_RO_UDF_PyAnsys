import os
import sys
import json
import argparse
import importlib.util
import subprocess
import time
from pathlib import Path

import pandas as pd

from ro.campaign_matrix import (
    CASE_SET_CHOICES,
    cases_for_case_set,
    filter_cases_by_geo_id,
    filter_cases_by_outlet_gauge_pressure,
)
from ro.domain_layout import (
    layout_from_mesh_manifest,
    layout_from_run_directory,
    layout_post_config_values,
)
from ro.extract_skip import extract_skip_block_reason, inspect_extract_skip_leaf
from ro.manifest import ManifestError, iter_run_manifests
from ro.fluent_report_helpers import LOAD_BEARING_SUMMARY_METRICS
from ro.paths import data_root, project_root, run_dir, runs_root
from ro.session_retry import (
    classify_retryable_session_crash,
    describe_attempt_orphans,
)


# ============================================================
# Critical columns required to be present and non-NaN/empty
# in summary_metrics_wide.csv for a case to be considered valid.
# Kept in sync with LOAD_BEARING_SUMMARY_METRICS in report extract.
# ============================================================

CRITICAL_SUMMARY_COLUMNS = list(LOAD_BEARING_SUMMARY_METRICS)

# Same defaults as batch_postprocess_all_cases / batch_solver_sweep:
# 2 retries after the first attempt (3 total), 15 s settle.
EXTRACT_TRANSIENT_FAILURE_MAX_RETRIES = 2
EXTRACT_POST_FAILURE_SETTLE_S = 15.0


def try_resolve_post_layout_overrides(mesh_directory):
    """Return (layout_overrides, None) or (None, error_summary)."""
    try:
        record = layout_from_mesh_manifest(Path(mesh_directory))
    except (ManifestError, OSError, ValueError) as exc:
        return None, str(exc)
    return layout_post_config_values(record), None


def build_post_case_overrides(
    geo_name,
    case_name,
    final_case_file,
    final_data_file,
    inlet_velocity_value=None,
    outlet_gauge_pressure=None,
    mesh_case_name=None,
    case_dir=None,
    mesh_directory=None,
):
    """Build PYFLUENT_POST_OVERRIDES including additive layout keys.

    Layout comes from the mesh manifest. Requires ``case_dir`` (run
    directory) or ``mesh_directory``. Missing manifests are an error, not
    LAYOUT_UNKNOWN.
    """
    overrides = {
        "geo_name": geo_name,
        "case_name": case_name,
        "final_case_file": str(final_case_file),
        "final_data_file": str(final_data_file),
        "project_root": str(project_root()),
    }
    if case_dir is not None:
        overrides["case_path"] = str(case_dir)
    if inlet_velocity_value is not None:
        overrides["inlet_velocity_value"] = inlet_velocity_value
    if outlet_gauge_pressure is not None:
        overrides["outlet_gauge_pressure"] = outlet_gauge_pressure

    run_payload = None
    if case_dir is not None:
        try:
            record, run_payload = layout_from_run_directory(Path(case_dir))
        except (ManifestError, OSError, ValueError) as exc:
            return None, str(exc)
        layout_overrides = layout_post_config_values(record)
        if inlet_velocity_value is None:
            overrides["inlet_velocity_value"] = run_payload["u_target_ms"]
        if outlet_gauge_pressure is None:
            overrides["outlet_gauge_pressure"] = run_payload["p_gauge_pa"]
        mesh_case_name = run_payload["mesh_id"]
    elif mesh_directory is not None:
        layout_overrides, layout_error = try_resolve_post_layout_overrides(
            mesh_directory
        )
        if layout_error is not None:
            return None, layout_error
        mesh_case_name = Path(mesh_directory).name
    else:
        return None, (
            "Layout requires case_dir or mesh_directory; "
            "layout comes from the mesh manifest."
        )

    overrides.update(layout_overrides)
    overrides["mesh_case_name"] = mesh_case_name
    overrides["mesh_resolution_source"] = "mesh_manifest"
    return overrides, None


# ============================================================
# Load batch config and base post config
# ============================================================

def load_python_config(config_path, module_name):
    config_path = Path(config_path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    spec = importlib.util.spec_from_file_location(module_name, str(config_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_summary_wide_csv(summary_wide_csv):
    """
    Validate a per-case summary_metrics_wide.csv against CRITICAL_SUMMARY_COLUMNS.

    Returns (True, "OK") when all critical columns are present and non-NaN/empty.
    Returns (False, detailed_message) on any validation failure.

    Uses pd.isna for NaN/None detection. Empty string "" is also treated as invalid.
    Numeric zero (0.0) is valid.
    """
    if not summary_wide_csv.is_file():
        return False, f"summary_metrics_wide.csv not found: {summary_wide_csv}"

    try:
        df = pd.read_csv(summary_wide_csv, encoding="utf-8-sig")
    except Exception as exc:
        return False, f"Could not read summary_metrics_wide.csv: {exc}"

    if len(df) == 0:
        return False, "summary_metrics_wide.csv has no data rows."

    missing_cols = [col for col in CRITICAL_SUMMARY_COLUMNS if col not in df.columns]
    if missing_cols:
        return False, f"Missing critical columns: {missing_cols}"

    row = df.iloc[0]
    invalid_cols = [
        col for col in CRITICAL_SUMMARY_COLUMNS
        if pd.isna(row[col]) or row[col] == ""
    ]

    if invalid_cols:
        return False, f"NaN/None/empty in critical columns: {invalid_cols}"

    return True, "OK"


EXTRACT_BATCH_FAILED_STATUSES = frozenset(
    {
        "FAILED",
        "FAILED_METRIC_VALIDATION",
        "MISSING_CASE_DATA",
    }
)
EXTRACT_SKIPPED_MISSING_FINALS_STATUS = "SKIPPED_MISSING_FINALS"


def extract_batch_exit_code(
    status_records,
    *,
    status_write_failed=False,
    merge_write_failed=False,
) -> int:
    """Nonzero when a processed case failed or an aggregate write failed.

    Zero selected cases is not a failure.
    """
    if status_write_failed or merge_write_failed:
        return 1
    if any(
        record.get("status") in EXTRACT_BATCH_FAILED_STATUSES
        for record in status_records
    ):
        return 1
    return 0


def parse_batch_report_extract_cli(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Batch report extract. Default (no --case-set) uses post_cases "
            "or walks run manifests. Pass --case-set production to select "
            "from the production solver matrix. --report-skip prints skip "
            "evidence for every run leaf without launching Fluent."
        ),
    )
    parser.add_argument(
        "--case-set",
        choices=CASE_SET_CHOICES,
        default=None,
        help=(
            "production: production_solver_sweep_cases (279) from "
            "batch_config. exploratory: solver_sweep_cases. "
            "Omitted: post_cases if set, otherwise walk run manifests."
        ),
    )
    parser.add_argument(
        "--geo-id",
        action="append",
        dest="geo_ids",
        default=None,
        metavar="GEO_ID",
        help=(
            "Restrict the selected cases to these geo_id values. "
            "Repeatable. Omitted: keep the whole selection. "
            "A geo_id that is not in the selection is an error, not an empty run."
        ),
    )
    parser.add_argument(
        "--outlet-gauge-pressure",
        action="append",
        dest="outlet_gauge_pressures",
        default=None,
        type=float,
        metavar="PA",
        help=(
            "Restrict the selected cases to these outlet_gauge_pressure "
            "values in Pa. Repeatable. Example: 6.0e6 for the p6M column "
            "(93 production cases). A value that is not in the selection "
            "is an error, not an empty run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the selected cases and per-case dry-run details without "
            "launching Fluent. ORs with batch_post_config.DRY_RUN."
        ),
    )
    parser.add_argument(
        "--report-skip",
        action="store_true",
        help=(
            "Print whether each run leaf's wide CSV is current with the run "
            "manifest hashes. Does not launch Fluent."
        ),
    )
    return parser.parse_args(argv)


def format_extract_skip_report_row(row):
    csv = f"csv=yes:{row['csv_size']}" if row["csv_exists"] else "csv=no"
    sidecar = "sidecar=yes" if row["sidecar_exists"] else "sidecar=no"
    hashes = "hashes=yes" if row["manifest_has_hashes"] else "hashes=no"
    reason = "" if row["skip_block"] is None else row["skip_block"]
    return (
        f"{row['skip_decision']:4}  {csv}  {sidecar}  {hashes}  {row['leaf']}"
        + (f"  reason={reason}" if reason else "")
    )


def report_extract_skip_status(*, file=None, csv_validator=None):
    """Inspect every run leaf under runs_root(). Returns row dicts."""
    if file is None:
        file = sys.stdout
    if csv_validator is None:
        csv_validator = validate_summary_wide_csv
    rows = []
    print("EXTRACT SKIP REPORT (no Fluent)", file=file)
    print(f"runs_root={runs_root()}", file=file)
    for manifest_path, payload in iter_run_manifests():
        run_directory = manifest_path.parent
        geo_id = payload["geo_id"]
        run_id = payload["run_id"]
        summary_wide_csv = (
            run_directory / "post" / "reports" / "summary_metrics_wide.csv"
        )
        final_case = run_directory / f"{geo_id}_{run_id}_final.cas.h5"
        final_data = run_directory / f"{geo_id}_{run_id}_final.dat.h5"
        row = inspect_extract_skip_leaf(
            family=payload["family"],
            geo_id=geo_id,
            mesh_id=payload["mesh_id"],
            run_id=run_id,
            run_directory=run_directory,
            summary_wide_csv=summary_wide_csv,
            final_case_path=final_case,
            final_data_path=final_data,
            csv_validator=csv_validator if summary_wide_csv.is_file() else None,
        )
        rows.append(row)
        print(format_extract_skip_report_row(row), file=file)
    skip_n = sum(1 for row in rows if row["skip_decision"] == "SKIP")
    print(
        f"Summary: {skip_n} SKIP / {len(rows) - skip_n} RUN (listed {len(rows)}).",
        file=file,
    )
    return rows


# ============================================================
# Case name helper
# ============================================================

def make_case_name(u, p):
    u_str = f"u0p{int(round(u * 10))}"
    p_str = f"p{int(round(p / 1.0e6))}M"
    return f"{u_str}_{p_str}"


def resolve_post_case(entry):
    """Normalize a post case entry into the fields the batch loop needs.

    case_name priority:
      1. Explicit "run_id".
      2. Explicit "case_name" (must not be mesh-qualified).
      3. Derived from inlet_velocity_value + outlet_gauge_pressure or
         base_case_name. Mesh identity is not encoded in the filename.
    """
    geo_name = entry.get("geo_id") or entry["geo_name"]
    inlet_velocity_value = entry.get("inlet_velocity_value")
    outlet_gauge_pressure = entry.get("outlet_gauge_pressure")
    base_case_name = entry.get("base_case_name")
    mesh_case_name = entry.get("mesh_case_name")
    case_name = entry.get("run_id") or entry.get("case_name")

    if isinstance(case_name, str) and "__" in case_name:
        raise ValueError(
            "mesh-qualified names are not used for artifacts: "
            f"{case_name!r}. Use run_id; mesh_id lives in the path."
        )

    if not case_name:
        if not base_case_name and inlet_velocity_value is not None and outlet_gauge_pressure is not None:
            base_case_name = make_case_name(inlet_velocity_value, outlet_gauge_pressure)
        case_name = base_case_name
    if not case_name:
        raise ValueError(
            f"post case entry for geo '{geo_name}' needs 'run_id', 'case_name', "
            "'base_case_name', or inlet_velocity_value + outlet_gauge_pressure."
        )

    return {
        "geo_name": geo_name,
        "geo_id": entry.get("geo_id") or geo_name,
        "family": entry.get("family"),
        "mesh_id": entry.get("mesh_id"),
        "run_id": entry.get("run_id") or case_name,
        "case_name": case_name,
        "base_case_name": base_case_name,
        "mesh_case_name": mesh_case_name or entry.get("mesh_id"),
        "inlet_velocity_value": inlet_velocity_value,
        "outlet_gauge_pressure": outlet_gauge_pressure,
        "final_case_file": entry.get("final_case_file"),
        "final_data_file": entry.get("final_data_file"),
    }


def resolve_batch_results_dir(bcfg) -> Path:
    configured = getattr(bcfg, "results_dir", None)
    if configured:
        return Path(configured)
    return runs_root()


def cases_from_run_manifests():
    """One post case per validated run manifest under runs_root()."""
    cases = []
    for manifest_path, payload in iter_run_manifests():
        run_directory = manifest_path.parent
        cases.append(
            {
                "geo_name": payload["geo_id"],
                "case_name": payload["run_id"],
                "base_case_name": payload["run_id"],
                "mesh_case_name": payload["mesh_id"],
                "family": payload["family"],
                "geo_id": payload["geo_id"],
                "mesh_id": payload["mesh_id"],
                "run_id": payload["run_id"],
                "inlet_velocity_value": payload["u_target_ms"],
                "outlet_gauge_pressure": payload["p_gauge_pa"],
                "final_case_file": None,
                "final_data_file": None,
                "case_dir": run_directory,
            }
        )
    return cases


def select_extract_cases(post_cfg, cli_args, *, solver_batch_cfg=None):
    """Choose extract cases. No --case-set keeps post_cases / manifest walk."""
    if cli_args.case_set:
        if solver_batch_cfg is None:
            solver_batch_cfg = load_python_config(
                project_root() / "configs" / "batch_config.py",
                "batch_config",
            )
        raw = cases_for_case_set(
            solver_batch_cfg,
            cli_args.case_set,
            exploratory_attr="solver_sweep_cases",
            production_attr="production_solver_sweep_cases",
        )
        cases = [resolve_post_case(entry) for entry in raw]
        source = f"{cli_args.case_set} solver matrix"
    else:
        explicit_post_cases = list(getattr(post_cfg, "post_cases", []) or [])
        if explicit_post_cases:
            cases = [resolve_post_case(entry) for entry in explicit_post_cases]
            source = "explicit post_cases"
        else:
            cases = cases_from_run_manifests()
            source = "run manifests"
    cases = filter_cases_by_geo_id(cases, cli_args.geo_ids)
    cases = filter_cases_by_outlet_gauge_pressure(
        cases,
        cli_args.outlet_gauge_pressures,
    )
    return cases, source


def missing_finals_status(*, matrix_selected):
    """Matrix selection reports unfinished solves as skip, not failure."""
    if matrix_selected:
        return EXTRACT_SKIPPED_MISSING_FINALS_STATUS
    return "MISSING_CASE_DATA"


def resolve_case_run_directory(case):
    raw = case.get("case_dir")
    if raw:
        return Path(raw)
    family = case.get("family")
    geo_id = case.get("geo_id")
    mesh_id = case.get("mesh_id")
    run_id = case.get("run_id")
    if family and geo_id and mesh_id and run_id:
        return run_dir(family, geo_id, mesh_id, run_id)
    return None


def format_selected_post_case(index, total, case):
    """One four-id line for the pre-Fluent case list."""
    family = case.get("family") or "?"
    geo_id = case.get("geo_id") or case.get("geo_name") or "?"
    mesh_id = case.get("mesh_id") or case.get("mesh_case_name") or "?"
    run_id = case.get("run_id") or case.get("case_name") or "?"
    return f"  {index}/{total}  {family}/{geo_id}/{mesh_id}/{run_id}"


def extract_attempt_log_path(run_directory, geo_id, mesh_id, run_id, attempt):
    """Driver log for one extract worker attempt; mesh_id and attempt are required."""
    return Path(run_directory) / (
        f"{geo_id}__{mesh_id}__{run_id}__extract_attempt{int(attempt)}.log"
    )


def run_extract_worker_tee(cmd, env, log_path):
    """Run the extract worker, teeing stdout/stderr to an attempt-specific log."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
        returncode = proc.wait()
    return subprocess.CompletedProcess(cmd, returncode)


def collect_extract_attempt_evidence(log_path):
    """Read this attempt's driver log only.

    Older attempt logs are not included: a leftover socket string must not
    make a later load-bearing failure look retryable.
    """
    path = Path(log_path)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_extract_attempts(
    *,
    cmd,
    env,
    run_directory,
    geo_id,
    mesh_id,
    run_id,
    max_retries=EXTRACT_TRANSIENT_FAILURE_MAX_RETRIES,
    settle_s=EXTRACT_POST_FAILURE_SETTLE_S,
    runner=None,
    sleeper=None,
    process_lister=None,
):
    """Run the extract worker, retrying Fluent session deaths.

    max_retries is retries after the first attempt (default 2 → 3 total).
    Each attempt is a new subprocess and a new Fluent session. Skip is not
    re-evaluated here.

    Returns (result, attempts, retry_kinds, attempt_logs).
    """
    if runner is None:
        runner = run_extract_worker_tee
    if sleeper is None:
        sleeper = time.sleep
    max_retries = max(0, int(max_retries))
    max_attempts = max_retries + 1
    retry_kinds = []
    attempt_logs = []
    result = None
    run_directory = Path(run_directory)
    for attempt in range(1, max_attempts + 1):
        log_path = extract_attempt_log_path(
            run_directory, geo_id, mesh_id, run_id, attempt
        )
        attempt_logs.append(log_path)
        print(
            f"  extract attempt {attempt}/{max_attempts}: "
            f"{' '.join(str(part) for part in cmd)}"
        )
        print(f"  Attempt log: {log_path}")
        result = runner(cmd, env=env, log_path=log_path)
        if result.returncode == 0:
            return result, attempt, retry_kinds, attempt_logs

        evidence = collect_extract_attempt_evidence(log_path)
        for line in describe_attempt_orphans(
            evidence, process_lister=process_lister
        ):
            print(f"  {line}")
        retry_kind = classify_retryable_session_crash(evidence)
        can_retry = attempt < max_attempts and retry_kind is not None
        if can_retry:
            retry_kinds.append(retry_kind)
            remaining = max_attempts - attempt
            print(
                f"  extract: transient {retry_kind} on attempt {attempt}; "
                f"retrying after {float(settle_s):g}s ({remaining} retry left)."
            )
            if settle_s > 0.0:
                sleeper(float(settle_s))
            continue
        if attempt < max_attempts:
            print(
                f"  extract: non-retryable failure on attempt {attempt} "
                f"(return code {result.returncode}); not retrying."
            )
        return result, attempt, retry_kinds, attempt_logs
    return result, max_attempts, retry_kinds, attempt_logs


def aggregate_output_paths() -> tuple[Path, Path]:
    inventory = data_root() / "inventory"
    return (
        inventory / "all_cases_post_summary.csv",
        inventory / "all_cases_post_status.csv",
    )
    inventory = data_root() / "inventory"
    return (
        inventory / "all_cases_post_summary.csv",
        inventory / "all_cases_post_status.csv",
    )


# The batch run below executes only when this file is run directly.
# Importing this module must not run the batch or write any files.
if __name__ == "__main__":
    cli_args = parse_batch_report_extract_cli()
    BATCH_CONFIG_PATH = project_root() / "configs" / "batch_post_config.py"
    bcfg = load_python_config(BATCH_CONFIG_PATH, "batch_post_config")

    BASE_CONFIG_PATH = Path(bcfg.base_post_config)
    if not BASE_CONFIG_PATH.is_file():
        BASE_CONFIG_PATH = project_root() / "configs" / BASE_CONFIG_PATH.name
    base_cfg = load_python_config(BASE_CONFIG_PATH, "base_post_config")

    if cli_args.report_skip:
        report_extract_skip_status()
        raise SystemExit(0)



    # ============================================================
    # Build case lists
    # ============================================================
    # No --case-set: post_cases if non-empty, otherwise walk run manifests.
    # --case-set production|exploratory: solver matrix from batch_config.

    all_cases_full, case_source = select_extract_cases(bcfg, cli_args)
    matrix_selected = cli_args.case_set is not None
    dry_run = bool(getattr(bcfg, "DRY_RUN", False) or cli_args.dry_run)

    total_defined = len(all_cases_full)

    cases_to_run = (
        all_cases_full[: bcfg.MAX_CASES]
        if bcfg.MAX_CASES is not None
        else all_cases_full
    )

    case_count = len(cases_to_run)

    print(f"Case source         : {case_source}")
    print(f"Total cases defined : {total_defined}")
    print(f"Cases to process    : {case_count}")
    if cli_args.case_set:
        print(f"case_set            : {cli_args.case_set}")
    if cli_args.geo_ids:
        print(f"geo_id filter       : {list(dict.fromkeys(cli_args.geo_ids))}")
    if cli_args.outlet_gauge_pressures:
        print(
            "outlet_gauge_pressure filter: "
            f"{list(dict.fromkeys(cli_args.outlet_gauge_pressures))}"
        )
    print(f"DRY_RUN             : {dry_run}")
    print(f"SKIP_EXISTING       : {bcfg.SKIP_EXISTING_REPORTS}")
    print(f"CONTINUE_ON_FAILURE : {bcfg.CONTINUE_ON_FAILURE}")
    print(f"MAX_CASES           : {bcfg.MAX_CASES}")
    print("Selected cases (Fluent has not launched):")
    if not cases_to_run:
        print("  (none)")
    else:
        for listed_idx, listed_case in enumerate(cases_to_run, start=1):
            print(format_selected_post_case(listed_idx, case_count, listed_case))


    # ============================================================
    # Paths
    # ============================================================

    results_dir = resolve_batch_results_dir(bcfg)
    worker_script = Path(bcfg.single_case_worker)
    merged_summary_csv, status_csv_path = aggregate_output_paths()

    # Cross-case aggregate CSVs live under RO_DATA_ROOT/inventory, so writing
    # them is opt-in (see WRITE_AGGREGATE_OUTPUTS in batch_post_config.py).
    write_aggregate_outputs = getattr(bcfg, "WRITE_AGGREGATE_OUTPUTS", False)



    # ============================================================
    # Process cases
    # ============================================================

    status_records = []

    for idx, case in enumerate(cases_to_run):
        geo_name              = case["geo_name"]
        case_name             = case["case_name"]
        base_case_name        = case["base_case_name"]
        mesh_case_name        = case["mesh_case_name"]
        inlet_velocity_value  = case["inlet_velocity_value"]
        outlet_gauge_pressure = case["outlet_gauge_pressure"]
        case_idx = idx + 1
        case_result_dir = resolve_case_run_directory(case)
        if case_result_dir is None:
            print(f"\n[{case_idx}/{case_count}] {geo_name} / {case_name}")
            print("  FAILED: need case_dir or family/geo_id/mesh_id/run_id")
            status_records.append(
                {
                    "case_index": case_idx,
                    "total_cases": case_count,
                    "geo_name": geo_name,
                    "case_name": case_name,
                    "base_case_name": base_case_name,
                    "mesh_case_name": mesh_case_name,
                    "inlet_velocity_value": inlet_velocity_value,
                    "outlet_gauge_pressure": outlet_gauge_pressure,
                    "final_case_file": "",
                    "final_data_file": "",
                    "summary_wide_csv": "",
                    "status": "FAILED",
                    "return_code": None,
                    "message": (
                        "Need case_dir or family/geo_id/mesh_id/run_id; "
                        "layout comes from the mesh manifest."
                    ),
                }
            )
            continue

        final_case_file  = (
            Path(case["final_case_file"]) if case["final_case_file"]
            else case_result_dir / f"{geo_name}_{case_name}_final.cas.h5"
        )
        final_data_file  = (
            Path(case["final_data_file"]) if case["final_data_file"]
            else case_result_dir / f"{geo_name}_{case_name}_final.dat.h5"
        )
        report_dir       = case_result_dir / "post" / "reports"
        summary_wide_csv = report_dir / "summary_metrics_wide.csv"

        print(f"\n[{case_idx}/{case_count}] {geo_name} / {case_name}")

        record = {
            "case_index":            case_idx,
            "total_cases":           case_count,
            "geo_name":              geo_name,
            "case_name":             case_name,
            "base_case_name":        base_case_name,
            "mesh_case_name":        mesh_case_name,
            "inlet_velocity_value":  inlet_velocity_value,
            "outlet_gauge_pressure": outlet_gauge_pressure,
            "final_case_file":       str(final_case_file),
            "final_data_file":       str(final_data_file),
            "summary_wide_csv":      str(summary_wide_csv),
            "status":                None,
            "return_code":           None,
            "message":               "",
        }

        missing_files = [
            str(f) for f in [final_case_file, final_data_file] if not f.is_file()
        ]
        if missing_files and matrix_selected:
            msg = "Missing files: " + "; ".join(missing_files)
            status = missing_finals_status(matrix_selected=True)
            print(f"  {status}: {msg}")
            record["status"] = status
            record["message"] = msg
            status_records.append(record)
            continue

        overrides, layout_error = build_post_case_overrides(
            geo_name=geo_name,
            case_name=case_name,
            final_case_file=final_case_file,
            final_data_file=final_data_file,
            inlet_velocity_value=inlet_velocity_value,
            outlet_gauge_pressure=outlet_gauge_pressure,
            mesh_case_name=mesh_case_name,
            case_dir=case_result_dir,
        )
        if layout_error is not None:
            print(f"  FAILED: {layout_error}")
            record["status"] = "FAILED"
            record["message"] = layout_error
            status_records.append(record)
            continue

        # --- Gate 1: DRY_RUN ---
        if dry_run:
            print(f"  [DRY_RUN] geo_name       : {geo_name}")
            print(f"  [DRY_RUN] base_case_name : {base_case_name if base_case_name else '(n/a)'}")
            print(f"  [DRY_RUN] mesh_case_name : {mesh_case_name if mesh_case_name else '(n/a)'}")
            print(f"  [DRY_RUN] case_name      : {case_name}")
            print(f"  [DRY_RUN] Final case: {final_case_file}")
            print(f"  [DRY_RUN] Final data: {final_data_file}")
            print(f"  [DRY_RUN] Worker   : {worker_script}")
            print(f"  [DRY_RUN] Overrides: {json.dumps(overrides, indent=2)}")
            record["status"] = "DRY_RUN"
            record["message"] = "DRY_RUN: no execution"
            status_records.append(record)
            continue

        # --- Gate 2: SKIP_EXISTING ---
        # Skip only when the CSV is bound to the current R-02 solve hashes.
        if bcfg.SKIP_EXISTING_REPORTS:
            skip_block = extract_skip_block_reason(
                case_result_dir,
                summary_wide_csv,
                final_case_file,
                final_data_file,
                csv_validator=validate_summary_wide_csv,
            )
            if skip_block is None:
                print(f"  SKIPPED_EXISTING: {summary_wide_csv}")
                record["status"] = "SKIPPED_EXISTING"
                record["return_code"] = 0
                record["message"] = (
                    "summary_metrics_wide.csv is current with the run "
                    "manifest hashes"
                )
                status_records.append(record)
                continue
            print(f"  Not skipping existing report: {skip_block}")

        # --- Gate 3: MISSING_CASE_DATA ---
        missing_files = [
            str(f) for f in [final_case_file, final_data_file] if not f.is_file()
        ]
        if missing_files:
            msg = "Missing files: " + "; ".join(missing_files)
            print(f"  MISSING_CASE_DATA: {msg}")
            record["status"] = "MISSING_CASE_DATA"
            record["message"] = msg
            status_records.append(record)
            continue

        # --- Run worker ---
        env = os.environ.copy()
        env["PYFLUENT_POST_CONFIG"] = str(BASE_CONFIG_PATH)
        env["PYFLUENT_POST_OVERRIDES"] = json.dumps(overrides)
        print(f"  Overrides: {json.dumps(overrides)}")

        print(f"  Running worker ...")
        return_code = -1
        attempts = 0
        try:
            result, attempts, retry_kinds, _attempt_logs = run_extract_attempts(
                cmd=[sys.executable, str(worker_script)],
                env=env,
                run_directory=case_result_dir,
                geo_id=case.get("geo_id") or geo_name,
                mesh_id=case.get("mesh_id") or mesh_case_name,
                run_id=case.get("run_id") or case_name,
            )
            return_code = result.returncode
            if retry_kinds:
                print(
                    f"  extract retries: attempts={attempts} "
                    f"kinds={retry_kinds}"
                )
        except Exception as exc:
            record["message"] = f"subprocess exception: {exc}"

        record["return_code"] = return_code

        if return_code != 0:
            record["status"] = "FAILED"
            if not record["message"]:
                record["message"] = (
                    f"Worker exited with return code {return_code} "
                    f"(attempts={attempts})"
                )
            print(
                f"  FAILED (return code {return_code}, attempts={attempts})"
            )

            if not bcfg.CONTINUE_ON_FAILURE:
                status_records.append(record)
                print("CONTINUE_ON_FAILURE=False — stopping batch.")
                break

        else:
            # Worker returned exit 0 — validate the output before recording SUCCESS.
            is_valid, validation_msg = validate_summary_wide_csv(summary_wide_csv)
            if is_valid:
                record["status"] = "SUCCESS"
                record["message"] = "OK"
                print(f"  SUCCESS")
            else:
                record["status"] = "FAILED_METRIC_VALIDATION"
                record["message"] = (
                    f"Worker returned exit 0 but output validation failed: {validation_msg}"
                )
                print(f"  FAILED_METRIC_VALIDATION: {validation_msg}")

                if not bcfg.CONTINUE_ON_FAILURE:
                    status_records.append(record)
                    print("CONTINUE_ON_FAILURE=False — stopping batch.")
                    break

        status_records.append(record)


    # ============================================================
    # Save status CSV (opt-in aggregate output)
    # ============================================================

    status_write_failed = False
    merge_write_failed = False

    print(f"\n{'='*60}")

    status_df = pd.DataFrame(status_records)

    if write_aggregate_outputs:
        print("Saving status CSV ...")
        try:
            status_csv_path.parent.mkdir(parents=True, exist_ok=True)
            status_df.to_csv(status_csv_path, index=False, encoding="utf-8-sig")
            print(f"Status CSV saved : {status_csv_path}")
        except Exception as exc:
            status_write_failed = True
            print(f"FAILED: could not save status CSV: {exc}")
    else:
        print("Status CSV write disabled (WRITE_AGGREGATE_OUTPUTS=False).")

    if status_records:
        counts = status_df["status"].value_counts().to_dict()
        for status_val, count in sorted(counts.items()):
            print(f"  {status_val}: {count}")
    else:
        print("No cases were processed.")


    # ============================================================
    # Merge summary CSVs (opt-in aggregate output)
    # ============================================================

    print(f"\n{'='*60}")

    if not write_aggregate_outputs:
        print("Merged summary CSV disabled (WRITE_AGGREGATE_OUTPUTS=False).")
    else:
        print("Merging summary CSVs ...")

        merged_dfs = []

        for case in all_cases_full:
            geo_name              = case["geo_name"]
            case_name             = case["case_name"]
            inlet_velocity_value  = case["inlet_velocity_value"]
            outlet_gauge_pressure = case["outlet_gauge_pressure"]
            summary_wide_csv = (
                results_dir / geo_name / case_name / "post" / "reports" / "summary_metrics_wide.csv"
            )

            if not summary_wide_csv.is_file():
                continue

            try:
                df = pd.read_csv(summary_wide_csv, encoding="utf-8-sig")
            except Exception as exc:
                print(f"  Warning: could not read {summary_wide_csv}: {exc}")
                continue

            df["geo_name"]             = geo_name
            df["case_name"]            = case_name
            df["mesh_case_name"]       = case["mesh_case_name"]
            df["inlet_velocity_value"] = inlet_velocity_value
            df["outlet_gauge_pressure"]= outlet_gauge_pressure
            df["outlet_pressure_MPa"]  = (
                outlet_gauge_pressure / 1.0e6 if outlet_gauge_pressure is not None else None
            )

            is_non_converged = (geo_name, case_name) in bcfg.non_converged_cases
            if is_non_converged:
                df["convergence_note"]         = "max_iteration_reached_not_for_final_comparison"
                df["use_for_final_comparison"] = False
            else:
                df["convergence_note"]         = "converged_or_accepted"
                df["use_for_final_comparison"] = True

            merged_dfs.append(df)
            print(f"  Included: {geo_name} / {case_name}")

        if not merged_dfs:
            print("No summary CSVs found. Merged summary file not written.")
        else:
            merged_df = pd.concat(merged_dfs, ignore_index=True)

            front_cols = [
                "geo_name", "case_name",
                "inlet_velocity_value", "outlet_gauge_pressure", "outlet_pressure_MPa",
                "convergence_note", "use_for_final_comparison",
            ]
            existing_front = [c for c in front_cols if c in merged_df.columns]
            other_cols     = [c for c in merged_df.columns if c not in existing_front]
            merged_df = merged_df[existing_front + other_cols]

            try:
                merged_summary_csv.parent.mkdir(parents=True, exist_ok=True)
                merged_df.to_csv(merged_summary_csv, index=False, encoding="utf-8-sig")
                print(f"\nMerged summary CSV saved : {merged_summary_csv}")
                print(f"Total rows               : {len(merged_df)}")
            except Exception as exc:
                merge_write_failed = True
                print(f"FAILED: could not save merged summary CSV: {exc}")


    # ============================================================
    # Final validation summary
    # ============================================================

    print(f"\n{'='*60}")
    print("Batch run validation summary:")

    if status_records:
        all_status_labels = [
            "SUCCESS",
            "SKIPPED_EXISTING",
            "SKIPPED_MISSING_FINALS",
            "FAILED",
            "MISSING_CASE_DATA",
            "FAILED_METRIC_VALIDATION",
            "DRY_RUN",
        ]
        counts = status_df["status"].value_counts().to_dict()
        for label in all_status_labels:
            print(f"  {label:<30}: {counts.get(label, 0)}")

        failed_validation = status_df[status_df["status"] == "FAILED_METRIC_VALIDATION"]
        if not failed_validation.empty:
            print(f"\nFAILED_METRIC_VALIDATION cases ({len(failed_validation)}):")
            for _, row in failed_validation.iterrows():
                print(f"  [{row['geo_name']} / {row['case_name']}] {row['message']}")
    else:
        print("No cases were processed.")

    print("\nBatch post-processing complete.")
    raise SystemExit(
        extract_batch_exit_code(
            status_records,
            status_write_failed=status_write_failed,
            merge_write_failed=merge_write_failed,
        )
    )
