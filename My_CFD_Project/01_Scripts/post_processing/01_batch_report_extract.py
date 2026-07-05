import os
import sys
import importlib.util
import subprocess
from pathlib import Path

import pandas as pd


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

# ============================================================
# Temporary config writer
# ============================================================

def write_temp_config(
    temp_config_path,
    geo_name,
    case_name,
    inlet_velocity_value,
    outlet_gauge_pressure,
    final_case_file,
    final_data_file,
):
    project_root_val      = getattr(base_cfg, "project_root",               "C:/PyFluent/My_CFD_Project")
    active_mem_names      = getattr(base_cfg, "active_membrane_base_names",  ["wall_top_mem", "wall_bottom_mem"])
    buffer_wall_names     = getattr(base_cfg, "buffer_wall_base_names",      ["wall_top_buffer", "wall_bottom_buffer"])
    rho                   = getattr(base_cfg, "rho",                         998.2)
    mu                    = getattr(base_cfg, "mu",                          8.93e-4)
    product_version       = getattr(base_cfg, "product_version",             "25.1.0")
    processor_count       = getattr(base_cfg, "processor_count",             1)
    graphics_driver       = getattr(base_cfg, "graphics_driver",             "dx11")
    fluent_start_timeout  = getattr(base_cfg, "fluent_start_timeout",        300)
    fluent_health_timeout = getattr(base_cfg, "fluent_health_timeout",       300)
    domain_x_min_m        = getattr(base_cfg, "domain_x_min_m",              0.0)
    domain_length_m       = getattr(base_cfg, "domain_length_m",             0.017325)
    buffer_length_m       = getattr(base_cfg, "buffer_length_m",             0.003465)

    lines = [
        f"project_root = {repr(str(project_root_val))}",
        "",
        f"geo_name = {repr(geo_name)}",
        f"case_name = {repr(case_name)}",
        f"inlet_velocity_value = {repr(inlet_velocity_value)}",
        f"outlet_gauge_pressure = {repr(outlet_gauge_pressure)}",
        "",
        f"final_case_file = {repr(str(final_case_file))}",
        f"final_data_file = {repr(str(final_data_file))}",
        "",
        f"active_membrane_base_names = {repr(active_mem_names)}",
        f"buffer_wall_base_names = {repr(buffer_wall_names)}",
        "",
        f"rho = {repr(rho)}",
        f"mu = {repr(mu)}",
        "",
        f"product_version = {repr(product_version)}",
        f"processor_count = {repr(processor_count)}",
        f"graphics_driver = {repr(graphics_driver)}",
        f"fluent_start_timeout = {repr(fluent_start_timeout)}",
        f"fluent_health_timeout = {repr(fluent_health_timeout)}",
        "",
        f"domain_x_min_m = {repr(domain_x_min_m)}",
        f"domain_length_m = {repr(domain_length_m)}",
        f"buffer_length_m = {repr(buffer_length_m)}",
    ]

    temp_config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# The batch run below executes only when this file is run directly.
# Importing this module must not run the batch or write any files.
if __name__ == "__main__":
    BATCH_CONFIG_PATH = Path(__file__).parent / "00_batch_post_config.py"
    bcfg = load_python_config(BATCH_CONFIG_PATH, "batch_post_config")

    BASE_CONFIG_PATH = Path(bcfg.base_post_config)
    if not BASE_CONFIG_PATH.is_file():
        # On Windows the absolute path is used; on Linux (WSL testing) fall back
        # to the file sitting in the same directory as this script.
        BASE_CONFIG_PATH = Path(__file__).parent / BASE_CONFIG_PATH.name
    base_cfg = load_python_config(BASE_CONFIG_PATH, "base_post_config")



    # ============================================================
    # Build case lists
    # ============================================================

    all_cases_full = [
        (geo, u, p)
        for geo in bcfg.geometries
        for u in bcfg.inlet_velocities
        for p in bcfg.outlet_gauge_pressures
    ]

    total_defined = len(all_cases_full)

    cases_to_run = (
        all_cases_full[: bcfg.MAX_CASES]
        if bcfg.MAX_CASES is not None
        else all_cases_full
    )

    case_count = len(cases_to_run)

    print(f"Total cases defined : {total_defined}")
    print(f"Cases to process    : {case_count}")
    print(f"DRY_RUN             : {bcfg.DRY_RUN}")
    print(f"SKIP_EXISTING       : {bcfg.SKIP_EXISTING_REPORTS}")
    print(f"CONTINUE_ON_FAILURE : {bcfg.CONTINUE_ON_FAILURE}")
    print(f"MAX_CASES           : {bcfg.MAX_CASES}")


    # ============================================================
    # Paths
    # ============================================================

    results_dir = Path(bcfg.project_root) / "03_Results"
    temp_config_dir = Path(bcfg.temporary_config_dir)
    worker_script = Path(bcfg.single_case_worker)
    merged_summary_csv = Path(bcfg.merged_summary_csv)
    status_csv_path = Path(bcfg.status_csv)



    # ============================================================
    # Process cases
    # ============================================================

    status_records = []

    for idx, (geo_name, inlet_velocity_value, outlet_gauge_pressure) in enumerate(cases_to_run):
        case_name = make_case_name(inlet_velocity_value, outlet_gauge_pressure)
        case_idx = idx + 1

        case_result_dir  = results_dir / geo_name / case_name
        final_case_file  = case_result_dir / f"{geo_name}_{case_name}_final.cas.h5"
        final_data_file  = case_result_dir / f"{geo_name}_{case_name}_final.dat.h5"
        report_dir       = case_result_dir / "post" / "reports"
        summary_wide_csv = report_dir / "summary_metrics_wide.csv"

        print(f"\n[{case_idx}/{case_count}] {geo_name} / {case_name}")

        record = {
            "case_index":            case_idx,
            "total_cases":           case_count,
            "geo_name":              geo_name,
            "case_name":             case_name,
            "inlet_velocity_value":  inlet_velocity_value,
            "outlet_gauge_pressure": outlet_gauge_pressure,
            "final_case_file":       str(final_case_file),
            "final_data_file":       str(final_data_file),
            "summary_wide_csv":      str(summary_wide_csv),
            "status":                None,
            "return_code":           None,
            "message":               "",
        }

        # --- Gate 1: DRY_RUN ---
        if bcfg.DRY_RUN:
            temp_name = f"{geo_name}_{case_name}_post_config.py"
            print(f"  [DRY_RUN] Worker     : {worker_script}")
            print(f"  [DRY_RUN] Temp config: {temp_config_dir / temp_name}")
            print(f"  [DRY_RUN] Case file  : {final_case_file}")
            print(f"  [DRY_RUN] Data file  : {final_data_file}")
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

        # --- Write temp config ---
        temp_config_dir.mkdir(parents=True, exist_ok=True)
        temp_config_path = temp_config_dir / f"{geo_name}_{case_name}_post_config.py"
        write_temp_config(
            temp_config_path,
            geo_name,
            case_name,
            inlet_velocity_value,
            outlet_gauge_pressure,
            final_case_file,
            final_data_file,
        )
        print(f"  Temp config: {temp_config_path}")

        # --- Run worker ---
        env = os.environ.copy()
        env["PYFLUENT_POST_CONFIG"] = str(temp_config_path)

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
    # Save status CSV
    # ============================================================

    print(f"\n{'='*60}")
    print("Saving status CSV ...")

    status_df = pd.DataFrame(status_records)

    try:
        status_csv_path.parent.mkdir(parents=True, exist_ok=True)
        status_df.to_csv(status_csv_path, index=False, encoding="utf-8-sig")
        print(f"Status CSV saved : {status_csv_path}")
    except Exception as exc:
        print(f"Warning: could not save status CSV: {exc}")

    if status_records:
        counts = status_df["status"].value_counts().to_dict()
        for status_val, count in sorted(counts.items()):
            print(f"  {status_val}: {count}")
    else:
        print("No cases were processed.")


    # ============================================================
    # Merge summary CSVs
    # ============================================================

    print(f"\n{'='*60}")
    print("Merging summary CSVs ...")

    merged_dfs = []

    for geo_name, inlet_velocity_value, outlet_gauge_pressure in all_cases_full:
        case_name = make_case_name(inlet_velocity_value, outlet_gauge_pressure)
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
        df["inlet_velocity_value"] = inlet_velocity_value
        df["outlet_gauge_pressure"]= outlet_gauge_pressure
        df["outlet_pressure_MPa"]  = outlet_gauge_pressure / 1.0e6

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
