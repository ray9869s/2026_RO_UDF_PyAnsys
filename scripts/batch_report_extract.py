import os
import sys
import json
import importlib.util
import subprocess
from pathlib import Path

import pandas as pd

from ro.domain_layout import (
    layout_from_mesh_manifest,
    layout_from_run_directory,
    layout_post_config_values,
)
from ro.manifest import ManifestError, iter_run_manifests
from ro.paths import data_root, project_root, run_dir, runs_root


# ============================================================
# Critical columns required to be present and non-NaN/empty
# in summary_metrics_wide.csv for a case to be considered valid.
# These must match the "metric" strings in the worker's summary_rows.
# ============================================================

CRITICAL_SUMMARY_COLUMNS = [
    "lmh_mass_balance",
    "pressure_drop_spacer",
    "pressure_drop_spacer_per_m",
    "cp_inlet_avg",
    "wall_shear_rate_avg",
    "mass_balance_relative_error",
]


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
        "case_name": case_name,
        "base_case_name": base_case_name,
        "mesh_case_name": mesh_case_name,
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
    BATCH_CONFIG_PATH = project_root() / "configs" / "batch_post_config.py"
    bcfg = load_python_config(BATCH_CONFIG_PATH, "batch_post_config")

    BASE_CONFIG_PATH = Path(bcfg.base_post_config)
    if not BASE_CONFIG_PATH.is_file():
        BASE_CONFIG_PATH = project_root() / "configs" / BASE_CONFIG_PATH.name
    base_cfg = load_python_config(BASE_CONFIG_PATH, "base_post_config")



    # ============================================================
    # Build case lists
    # ============================================================
    # When post_cases is non-empty it is used verbatim (mesh-qualified or
    # custom case names); otherwise the legacy geometry x velocity x pressure
    # product is generated.

    explicit_post_cases = list(getattr(bcfg, "post_cases", []) or [])

    if explicit_post_cases:
        all_cases_full = [resolve_post_case(entry) for entry in explicit_post_cases]
        case_source = "explicit post_cases"
    else:
        all_cases_full = cases_from_run_manifests()
        case_source = "run manifests"

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
    print(f"DRY_RUN             : {bcfg.DRY_RUN}")
    print(f"SKIP_EXISTING       : {bcfg.SKIP_EXISTING_REPORTS}")
    print(f"CONTINUE_ON_FAILURE : {bcfg.CONTINUE_ON_FAILURE}")
    print(f"MAX_CASES           : {bcfg.MAX_CASES}")


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
        if bcfg.DRY_RUN:
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
        # If an existing report passes validation, skip. If it fails, fall through
        # and re-run the case so the invalid report is replaced.
        if bcfg.SKIP_EXISTING_REPORTS and summary_wide_csv.is_file():
            is_valid, validation_msg = validate_summary_wide_csv(summary_wide_csv)
            if is_valid:
                print(f"  SKIPPED_EXISTING: {summary_wide_csv}")
                record["status"] = "SKIPPED_EXISTING"
                record["return_code"] = 0
                record["message"] = "summary_metrics_wide.csv already exists and passed validation"
                status_records.append(record)
                continue
            else:
                print(f"  SKIP_EXISTING: existing report failed validation — will re-run.")
                print(f"  Validation failure: {validation_msg}")
                # Fall through: do not skip; re-run this case.

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
        try:
            result = subprocess.run(
                [sys.executable, str(worker_script)],
                env=env,
                check=False,
            )
            return_code = result.returncode
        except Exception as exc:
            record["message"] = f"subprocess exception: {exc}"

        record["return_code"] = return_code

        if return_code != 0:
            record["status"] = "FAILED"
            if not record["message"]:
                record["message"] = f"Worker exited with return code {return_code}"
            print(f"  FAILED (return code {return_code})")

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

    print(f"\n{'='*60}")

    status_df = pd.DataFrame(status_records)

    if write_aggregate_outputs:
        print("Saving status CSV ...")
        try:
            status_csv_path.parent.mkdir(parents=True, exist_ok=True)
            status_df.to_csv(status_csv_path, index=False, encoding="utf-8-sig")
            print(f"Status CSV saved : {status_csv_path}")
        except Exception as exc:
            print(f"Warning: could not save status CSV: {exc}")
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
                print(f"Warning: could not save merged summary CSV: {exc}")


    # ============================================================
    # Final validation summary
    # ============================================================

    print(f"\n{'='*60}")
    print("Batch run validation summary:")

    if status_records:
        all_status_labels = [
            "SUCCESS",
            "SKIPPED_EXISTING",
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
