# ==========================================================
# pyfluent_field_check.py
# Field/report sanity checker for PyFluent post-processing
# Location: My_CFD_Project/01_Scripts/post_processing/
# ==========================================================
#
# Purpose:
#   - Check whether a completed solver case has the expected final .cas.h5/.dat.h5 files.
#   - Check whether post-processing report outputs exist.
#   - Validate critical metrics in summary_metrics_wide.csv.
#   - Inspect raw_report_values.json for failed report definitions or None values.
#   - Optionally launch PyFluent and check available zones/fields with --with-fluent.
#
# Default mode:
#   - Safe file/report check only.
#   - Does NOT launch Fluent.
#
# Typical usage:
#   python My_CFD_Project/01_Scripts/post_processing/pyfluent_field_check.py
#
# Optional PyFluent live check:
#   python My_CFD_Project/01_Scripts/post_processing/pyfluent_field_check.py --with-fluent
#
# Config loading:
#   - Uses PYFLUENT_POST_CONFIG if set.
#   - Otherwise falls back to <project>/configs/post_config.py.
#
# Outputs:
#   03_Results/<geo_name>/<case_name>/post/checks/field_check_summary.csv
#   03_Results/<geo_name>/<case_name>/post/checks/field_check_summary.json
# ==========================================================

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


# ----------------------------------------------------------
# Paths and config defaults
# ----------------------------------------------------------

CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

from ro.paths import project_root, resolve_selected_run_directory  # noqa: E402
from ro.udm_layout import (  # noqa: E402
    expected_udm_fields_from_enum,
    find_case_udf_path,
    parse_udm_enum_from_c,
)

DEFAULT_CONFIG_PATH = project_root() / "configs" / "post_config.py"


# ----------------------------------------------------------
# Critical columns expected from summary_metrics_wide.csv
# ----------------------------------------------------------

CRITICAL_SUMMARY_COLUMNS = [
    "lmh_mass_balance",
    "pressure_drop_spacer",
    "pressure_drop_spacer_per_m",
    "cp_inlet_avg",
    "wall_shear_rate_avg",
    "mass_balance_relative_error",
]


# ----------------------------------------------------------
# Expected report output files
# ----------------------------------------------------------

EXPECTED_REPORT_FILES = [
    "summary_metrics.csv",
    "summary_metrics_wide.csv",
    "mass_balance.csv",
    "pressure_report.csv",
    "wall_shear_report.csv",
    "raw_report_values.json",
]


# ----------------------------------------------------------
# Check result container
# ----------------------------------------------------------

@dataclass
class CheckRecord:
    category: str
    item: str
    status: str
    message: str = ""
    value: str = ""


def add_check(
    records: list[CheckRecord],
    category: str,
    item: str,
    status: str,
    message: str = "",
    value: Any = "",
) -> None:
    records.append(
        CheckRecord(
            category=category,
            item=item,
            status=status,
            message=str(message),
            value="" if value is None else str(value),
        )
    )


# ----------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------

def load_python_config(config_path: Path) -> Any:
    config_path = Path(config_path).resolve()

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location("post_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config spec from: {config_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


def as_path(value: Any) -> Path:
    if isinstance(value, Path):
        return value
    return Path(str(value))


def normalize_name(name: str) -> str:
    return (
        str(name)
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def is_invalid_value(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def safe_float(value: Any) -> float | None:
    if is_invalid_value(value):
        return None

    try:
        out = float(value)
    except Exception:
        return None

    if not math.isfinite(out):
        return None

    return out


def get_project_root(cfg: Any) -> Path:
    configured = cfg_get(cfg, "project_root")
    if configured:
        return as_path(configured).resolve()
    return project_root()


def get_case_paths(
    cfg: Any,
    geo_name_override: str | None = None,
    case_name_override: str | None = None,
    family: str | None = None,
    geo_id: str | None = None,
    mesh_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Path | str]:
    project_root = get_project_root(cfg)

    geo_name = geo_name_override or cfg_get(cfg, "geo_name", None)
    case_name = case_name_override or cfg_get(cfg, "case_name", None)

    if not geo_name:
        raise ValueError("geo_name is not defined in config and was not provided by --geo-name.")
    if not case_name:
        raise ValueError("case_name is not defined in config and was not provided by --case-name.")

    case_path = resolve_selected_run_directory(
        family=family or cfg_get(cfg, "family"),
        geo_id=geo_id or cfg_get(cfg, "geo_id"),
        mesh_id=mesh_id or cfg_get(cfg, "mesh_id"),
        run_id=run_id or cfg_get(cfg, "run_id"),
        case_path=cfg_get(cfg, "case_path"),
    )
    results_raw = cfg_get(cfg, "results_dir")
    results_dir = as_path(results_raw) if results_raw else case_path.parent

    final_case_file = as_path(
        cfg_get(
            cfg,
            "final_case_file",
            case_path / f"{geo_name}_{case_name}_final.cas.h5",
        )
    )
    final_data_file = as_path(
        cfg_get(
            cfg,
            "final_data_file",
            case_path / f"{geo_name}_{case_name}_final.dat.h5",
        )
    )

    reports_dir = case_path / "post" / "reports"
    checks_dir = case_path / "post" / "checks"

    return {
        "project_root": project_root,
        "results_dir": results_dir,
        "geo_name": str(geo_name),
        "case_name": str(case_name),
        "case_path": case_path,
        "final_case_file": final_case_file,
        "final_data_file": final_data_file,
        "reports_dir": reports_dir,
        "checks_dir": checks_dir,
    }


# ----------------------------------------------------------
# File and report checks
# ----------------------------------------------------------

def check_input_files(records: list[CheckRecord], paths: dict[str, Any]) -> None:
    final_case_file = Path(paths["final_case_file"])
    final_data_file = Path(paths["final_data_file"])

    if final_case_file.is_file():
        add_check(records, "input_file", "final_case_file", "PASS", "Found.", final_case_file)
    else:
        add_check(records, "input_file", "final_case_file", "FAIL", "Missing.", final_case_file)

    if final_data_file.is_file():
        add_check(records, "input_file", "final_data_file", "PASS", "Found.", final_data_file)
    else:
        add_check(records, "input_file", "final_data_file", "FAIL", "Missing.", final_data_file)


def check_report_files(records: list[CheckRecord], reports_dir: Path) -> None:
    if reports_dir.is_dir():
        add_check(records, "report_dir", "reports_dir", "PASS", "Found.", reports_dir)
    else:
        add_check(records, "report_dir", "reports_dir", "WARN", "Reports directory not found.", reports_dir)

    for file_name in EXPECTED_REPORT_FILES:
        file_path = reports_dir / file_name

        if file_path.is_file():
            add_check(records, "report_file", file_name, "PASS", "Found.", file_path)
        else:
            add_check(records, "report_file", file_name, "WARN", "Missing.", file_path)


def validate_summary_metrics_wide(
    records: list[CheckRecord],
    summary_wide_csv: Path,
) -> bool:
    if not summary_wide_csv.is_file():
        add_check(
            records,
            "summary_validation",
            "summary_metrics_wide.csv",
            "FAIL",
            "summary_metrics_wide.csv does not exist.",
            summary_wide_csv,
        )
        return False

    try:
        df = pd.read_csv(summary_wide_csv, encoding="utf-8-sig")
    except Exception as exc:
        add_check(
            records,
            "summary_validation",
            "read_csv",
            "FAIL",
            f"Could not read summary_metrics_wide.csv: {exc}",
            summary_wide_csv,
        )
        return False

    if df.empty:
        add_check(
            records,
            "summary_validation",
            "row_count",
            "FAIL",
            "summary_metrics_wide.csv has no rows.",
            summary_wide_csv,
        )
        return False

    add_check(
        records,
        "summary_validation",
        "row_count",
        "PASS",
        "summary_metrics_wide.csv has at least one row.",
        len(df),
    )

    missing_columns = [col for col in CRITICAL_SUMMARY_COLUMNS if col not in df.columns]

    if missing_columns:
        add_check(
            records,
            "summary_validation",
            "critical_columns",
            "FAIL",
            "Missing critical columns: " + ", ".join(missing_columns),
            len(missing_columns),
        )
        return False

    add_check(
        records,
        "summary_validation",
        "critical_columns",
        "PASS",
        "All critical columns are present.",
        ", ".join(CRITICAL_SUMMARY_COLUMNS),
    )

    first_row = df.iloc[0]
    invalid_columns: list[str] = []

    for col in CRITICAL_SUMMARY_COLUMNS:
        value = first_row[col]

        if is_invalid_value(value):
            invalid_columns.append(col)

    if invalid_columns:
        add_check(
            records,
            "summary_validation",
            "critical_values",
            "FAIL",
            "Invalid NaN/None/empty values in: " + ", ".join(invalid_columns),
            len(invalid_columns),
        )
        return False

    add_check(
        records,
        "summary_validation",
        "critical_values",
        "PASS",
        "All critical columns have valid first-row values.",
    )

    run_basic_numeric_sanity_checks(records, first_row)

    return True


def run_basic_numeric_sanity_checks(records: list[CheckRecord], first_row: pd.Series) -> None:
    lmh = safe_float(first_row.get("lmh_mass_balance"))
    if lmh is not None:
        if lmh > 0.0:
            add_check(records, "numeric_sanity", "lmh_mass_balance", "PASS", "Positive LMH.", lmh)
        else:
            add_check(records, "numeric_sanity", "lmh_mass_balance", "WARN", "LMH is not positive.", lmh)

    dp_spacer = safe_float(first_row.get("pressure_drop_spacer"))
    if dp_spacer is not None:
        if dp_spacer > 0.0:
            add_check(records, "numeric_sanity", "pressure_drop_spacer", "PASS", "Positive spacer pressure drop.", dp_spacer)
        else:
            add_check(records, "numeric_sanity", "pressure_drop_spacer", "WARN", "Spacer pressure drop is not positive.", dp_spacer)

    dp_per_m = safe_float(first_row.get("pressure_drop_spacer_per_m"))
    if dp_per_m is not None:
        if dp_per_m > 0.0:
            add_check(records, "numeric_sanity", "pressure_drop_spacer_per_m", "PASS", "Positive spacer pressure drop per length.", dp_per_m)
        else:
            add_check(records, "numeric_sanity", "pressure_drop_spacer_per_m", "WARN", "Spacer pressure drop per length is not positive.", dp_per_m)

    cp_avg = safe_float(first_row.get("cp_inlet_avg"))
    if cp_avg is not None:
        if cp_avg > 0.0:
            add_check(records, "numeric_sanity", "cp_inlet_avg", "PASS", "Positive CP value.", cp_avg)
        else:
            add_check(records, "numeric_sanity", "cp_inlet_avg", "WARN", "CP value is not positive.", cp_avg)

        if cp_avg < 0.5 or cp_avg > 2.0:
            add_check(records, "numeric_sanity", "cp_inlet_avg_range", "WARN", "CP_avg is outside the loose expected range 0.5-2.0.", cp_avg)
        else:
            add_check(records, "numeric_sanity", "cp_inlet_avg_range", "PASS", "CP_avg is within the loose expected range 0.5-2.0.", cp_avg)

    shear_rate = safe_float(first_row.get("wall_shear_rate_avg"))
    if shear_rate is not None:
        if shear_rate > 0.0:
            add_check(records, "numeric_sanity", "wall_shear_rate_avg", "PASS", "Positive wall shear rate.", shear_rate)
        else:
            add_check(records, "numeric_sanity", "wall_shear_rate_avg", "WARN", "Wall shear rate is not positive.", shear_rate)

    mb_rel = safe_float(first_row.get("mass_balance_relative_error"))
    if mb_rel is not None:
        abs_mb_rel = abs(mb_rel)

        if abs_mb_rel <= 0.05:
            add_check(records, "numeric_sanity", "mass_balance_relative_error", "PASS", "Mass balance relative error <= 5%.", mb_rel)
        else:
            add_check(records, "numeric_sanity", "mass_balance_relative_error", "WARN", "Mass balance relative error > 5%.", mb_rel)


def check_raw_report_json(records: list[CheckRecord], raw_json_path: Path) -> None:
    if not raw_json_path.is_file():
        add_check(records, "raw_json", "raw_report_values.json", "WARN", "Missing.", raw_json_path)
        return

    try:
        with raw_json_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as exc:
        add_check(records, "raw_json", "read_json", "WARN", f"Could not read raw_report_values.json: {exc}", raw_json_path)
        return

    add_check(records, "raw_json", "read_json", "PASS", "raw_report_values.json is readable.", raw_json_path)

    failed_specs = find_key_recursive(raw, "failed_report_specs")
    if isinstance(failed_specs, list) and len(failed_specs) > 0:
        add_check(records, "raw_json", "failed_report_specs", "WARN", "Some report specs failed.", len(failed_specs))
    elif isinstance(failed_specs, list):
        add_check(records, "raw_json", "failed_report_specs", "PASS", "No failed report specs listed.", 0)
    else:
        add_check(records, "raw_json", "failed_report_specs", "INFO", "failed_report_specs key not found.", "")

    report_values = find_key_recursive(raw, "computed_values")
    if isinstance(report_values, dict):
        none_keys = [str(k) for k, v in report_values.items() if v is None]
        if none_keys:
            add_check(records, "raw_json", "none_report_values", "WARN", "Some raw report values are None: " + ", ".join(none_keys[:20]), len(none_keys))
        else:
            add_check(records, "raw_json", "none_report_values", "PASS", "No None values found in report_values.", 0)
    else:
        add_check(records, "raw_json", "report_values", "INFO", "report_values key not found or not a dict.", "")


def find_key_recursive(obj: Any, target_key: str) -> Any:
    if isinstance(obj, dict):
        if target_key in obj:
            return obj[target_key]

        for value in obj.values():
            found = find_key_recursive(value, target_key)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = find_key_recursive(item, target_key)
            if found is not None:
                return found

    return None


# ----------------------------------------------------------
# Optional PyFluent live checks
# ----------------------------------------------------------

def run_pyfluent_live_checks(
    records: list[CheckRecord],
    cfg: Any,
    paths: dict[str, Any],
    expected_udm_fields: dict[str, str],
) -> None:
    add_check(records, "pyfluent_live", "enabled", "INFO", "--with-fluent was requested.")

    try:
        import ansys.fluent.core as pyfluent
    except Exception as exc:
        add_check(records, "pyfluent_live", "import_pyfluent", "FAIL", f"Could not import ansys.fluent.core: {exc}")
        return

    session = None

    try:
        launch_kwargs = build_fluent_launch_kwargs(cfg)

        add_check(records, "pyfluent_live", "launch_kwargs", "INFO", "Launching Fluent with kwargs.", launch_kwargs)

        session = pyfluent.launch_fluent(**launch_kwargs)

        try:
            # This matches the current project convention:
            # launch in meshing mode and switch to solver.
            session = session.switch_to_solver()
            add_check(records, "pyfluent_live", "switch_to_solver", "PASS", "Switched to solver.")
        except Exception as exc:
            add_check(records, "pyfluent_live", "switch_to_solver", "WARN", f"Could not switch to solver, continuing with current session: {exc}")

        read_case_data_with_fallbacks(
            records=records,
            session=session,
            case_file=Path(paths["final_case_file"]),
            data_file=Path(paths["final_data_file"]),
        )

        check_solver_surfaces(records, session, cfg)
        check_solver_fields(
            records,
            session,
            expected_udm_fields=expected_udm_fields,
        )

    except Exception as exc:
        add_check(records, "pyfluent_live", "unhandled_exception", "FAIL", f"{type(exc).__name__}: {exc}")
        add_check(records, "pyfluent_live", "traceback", "INFO", traceback.format_exc())

    finally:
        if session is not None:
            try:
                session.exit()
                add_check(records, "pyfluent_live", "session_exit", "PASS", "Fluent session exited.")
            except Exception as exc:
                add_check(records, "pyfluent_live", "session_exit", "WARN", f"Could not exit Fluent cleanly: {exc}")


def build_fluent_launch_kwargs(cfg: Any) -> dict[str, Any]:
    processor_count = int(cfg_get(cfg, "processor_count", 1))

    precision = str(cfg_get(cfg, "precision", "double"))
    mode = str(cfg_get(cfg, "mode", "meshing"))

    ui_mode = cfg_get(cfg, "ui_mode", None)
    graphics_driver = cfg_get(cfg, "graphics_driver", None)

    if ui_mode is None:
        ui_mode = "gui"

    if graphics_driver is None:
        if platform.system().lower().startswith("win"):
            graphics_driver = "dx11"
        else:
            graphics_driver = "opengl"

    launch_kwargs = {
        "precision": precision,
        "processor_count": processor_count,
        "mode": mode,
        "ui_mode": ui_mode,
        "graphics_driver": graphics_driver,
    }

    return launch_kwargs


def read_case_data_with_fallbacks(
    records: list[CheckRecord],
    session: Any,
    case_file: Path,
    data_file: Path,
) -> None:
    if not case_file.is_file():
        add_check(records, "pyfluent_live", "read_case_data", "FAIL", "Case file missing before Fluent read.", case_file)
        return

    if not data_file.is_file():
        add_check(records, "pyfluent_live", "read_case_data", "FAIL", "Data file missing before Fluent read.", data_file)
        return

    attempts: list[tuple[str, Any]] = []

    if hasattr(session, "file"):
        attempts.append(
            (
                "session.file.read(file_type='case-data', file_name=case_file)",
                lambda: session.file.read(file_type="case-data", file_name=str(case_file)),
            )
        )

        attempts.append(
            (
                "session.file.read_case + session.file.read_data",
                lambda: (
                    session.file.read_case(file_name=str(case_file)),
                    session.file.read_data(file_name=str(data_file)),
                ),
            )
        )

    if hasattr(session, "tui"):
        attempts.append(
            (
                "session.tui.file.read_case_data(case_file)",
                lambda: session.tui.file.read_case_data(str(case_file)),
            )
        )

        attempts.append(
            (
                "session.tui.file.read_case + session.tui.file.read_data",
                lambda: (
                    session.tui.file.read_case(str(case_file)),
                    session.tui.file.read_data(str(data_file)),
                ),
            )
        )

    errors: list[str] = []

    for attempt_name, attempt_func in attempts:
        try:
            attempt_func()
            add_check(records, "pyfluent_live", "read_case_data", "PASS", f"Read succeeded using: {attempt_name}")
            return
        except Exception as exc:
            errors.append(f"{attempt_name}: {type(exc).__name__}: {exc}")

    add_check(
        records,
        "pyfluent_live",
        "read_case_data",
        "FAIL",
        "All case/data read attempts failed. " + " | ".join(errors[:4]),
    )


def check_solver_surfaces(records: list[CheckRecord], session: Any, cfg: Any) -> None:
    expected_active_membrane = list(
        cfg_get(
            cfg,
            "active_membrane_base_names",
            ["wall_top_mem", "wall_bottom_mem"],
        )
    )

    expected_buffer = list(
        cfg_get(
            cfg,
            "buffer_wall_base_names",
            ["wall_top_buffer", "wall_bottom_buffer"],
        )
    )

    expected_spacer = list(cfg_get(cfg, "wall_spacer_labels", []))

    expected_surfaces = expected_active_membrane + expected_buffer + expected_spacer

    if not expected_surfaces:
        add_check(records, "pyfluent_live", "expected_surfaces", "INFO", "No expected surface labels configured.")
        return

    try:
        surfaces_info = session.fields.field_info.get_surfaces_info()
    except Exception as exc:
        add_check(records, "pyfluent_live", "get_surfaces_info", "WARN", f"Could not query surfaces info: {exc}")
        return

    surface_names = extract_names_from_nested_object(surfaces_info)
    surface_names_norm = {normalize_name(name) for name in surface_names}

    add_check(records, "pyfluent_live", "surface_count", "INFO", "Number of surface names detected.", len(surface_names))

    for expected in expected_surfaces:
        expected_norm = normalize_name(expected)

        if expected_norm in surface_names_norm:
            add_check(records, "pyfluent_live_surface", expected, "PASS", "Surface found.")
        else:
            add_check(records, "pyfluent_live_surface", expected, "WARN", "Surface not found in field_info surface list.")


def check_solver_fields(
    records: list[CheckRecord],
    session: Any,
    expected_udm_fields: dict[str, str],
) -> None:
    field_names: set[str] = set()

    try:
        scalar_info = session.fields.field_info.get_scalar_fields_info()
        field_names.update(extract_names_from_nested_object(scalar_info))
    except Exception as exc:
        add_check(records, "pyfluent_live", "get_scalar_fields_info", "WARN", f"Could not query scalar fields info: {exc}")

    try:
        vector_info = session.fields.field_info.get_vector_fields_info()
        field_names.update(extract_names_from_nested_object(vector_info))
    except Exception:
        # Some Fluent versions may not expose this method. Not critical.
        pass

    field_names_norm = {normalize_name(name) for name in field_names}

    if field_names:
        add_check(records, "pyfluent_live", "field_count", "INFO", "Number of field names detected.", len(field_names))
    else:
        add_check(records, "pyfluent_live", "field_count", "WARN", "No fields detected through field_info.")

    for field_name, description in expected_udm_fields.items():
        if normalize_name(field_name) in field_names_norm:
            add_check(records, "pyfluent_live_field", field_name, "PASS", f"Found expected field: {description}")
        else:
            add_check(records, "pyfluent_live_field", field_name, "WARN", f"Expected field not found through field_info: {description}")

    if normalize_name("wall-shear") in field_names_norm or normalize_name("wall_shear") in field_names_norm:
        add_check(records, "pyfluent_live_field", "wall-shear", "PASS", "Found wall-shear field.")
    else:
        add_check(records, "pyfluent_live_field", "wall-shear", "WARN", "wall-shear field not found through field_info.")


def extract_names_from_nested_object(obj: Any) -> set[str]:
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, val in value.items():
                if isinstance(key, str):
                    names.add(key)

                if isinstance(val, str):
                    names.add(val)

                if isinstance(val, dict):
                    for possible_name_key in ["name", "surface_name", "field_name", "display_name"]:
                        if possible_name_key in val and isinstance(val[possible_name_key], str):
                            names.add(val[possible_name_key])

                walk(val)

        elif isinstance(value, list):
            for item in value:
                walk(item)

        elif isinstance(value, tuple):
            for item in value:
                walk(item)

    walk(obj)
    return names


# ----------------------------------------------------------
# Output writing
# ----------------------------------------------------------

def summarize_status(records: list[CheckRecord], fail_on_warn: bool = False) -> str:
    statuses = [rec.status for rec in records]

    if "FAIL" in statuses:
        return "FAIL"

    if fail_on_warn and "WARN" in statuses:
        return "FAIL"

    if "WARN" in statuses:
        return "WARN"

    return "PASS"


def save_check_outputs(
    records: list[CheckRecord],
    paths: dict[str, Any],
    overall_status: str,
) -> None:
    checks_dir = Path(paths["checks_dir"])
    checks_dir.mkdir(parents=True, exist_ok=True)

    rows = [asdict(record) for record in records]
    df = pd.DataFrame(rows)

    csv_path = checks_dir / "field_check_summary.csv"
    json_path = checks_dir / "field_check_summary.json"

    df.to_csv(csv_path, index=False)

    payload = {
        "geo_name": paths["geo_name"],
        "case_name": paths["case_name"],
        "overall_status": overall_status,
        "records": rows,
    }

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("")
    print("Saved field check outputs:")
    print(f"  {csv_path}")
    print(f"  {json_path}")


def print_console_summary(
    records: list[CheckRecord],
    paths: dict[str, Any],
    overall_status: str,
) -> None:
    print("")
    print("=" * 72)
    print("FIELD CHECK SUMMARY")
    print("=" * 72)
    print(f"Geometry: {paths['geo_name']}")
    print(f"Case:     {paths['case_name']}")
    print(f"Status:   {overall_status}")
    print("-" * 72)

    df = pd.DataFrame([asdict(record) for record in records])

    if df.empty:
        print("No check records.")
        return

    status_counts = df["status"].value_counts().reindex(["PASS", "WARN", "FAIL", "INFO"], fill_value=0)
    print("Status counts:")
    print(status_counts.to_string())

    problem_df = df[df["status"].isin(["FAIL", "WARN"])].copy()

    if not problem_df.empty:
        print("")
        print("Warnings/failures:")
        print(problem_df[["category", "item", "status", "message", "value"]].to_string(index=False))
    else:
        print("")
        print("No warnings or failures.")

    print("=" * 72)


# ----------------------------------------------------------
# Main checking routine
# ----------------------------------------------------------

def run_field_check(
    config_path: Path,
    geo_name_override: str | None = None,
    case_name_override: str | None = None,
    family: str | None = None,
    geo_id: str | None = None,
    mesh_id: str | None = None,
    run_id: str | None = None,
    with_fluent: bool = False,
    fail_on_warn: bool = False,
) -> int:
    records: list[CheckRecord] = []

    cfg = load_python_config(config_path)
    paths = get_case_paths(
        cfg=cfg,
        geo_name_override=geo_name_override,
        case_name_override=case_name_override,
        family=family,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
    )

    add_check(records, "config", "config_path", "INFO", "Loaded config.", config_path)
    add_check(records, "case", "geo_name", "INFO", "Geometry name.", paths["geo_name"])
    add_check(records, "case", "case_name", "INFO", "Case name.", paths["case_name"])

    check_input_files(records, paths)

    reports_dir = Path(paths["reports_dir"])
    check_report_files(records, reports_dir)

    summary_wide_csv = reports_dir / "summary_metrics_wide.csv"
    validate_summary_metrics_wide(records, summary_wide_csv)

    raw_json_path = reports_dir / "raw_report_values.json"
    check_raw_report_json(records, raw_json_path)

    if with_fluent:
        # Layout comes from the case-local UDF copy. Missing copy is fatal
        # (no fallback to the live master's udm-N meanings) and is raised
        # before Fluent is launched.
        udf_path = find_case_udf_path(paths["case_path"])
        expected_udm_fields = expected_udm_fields_from_enum(
            parse_udm_enum_from_c(udf_path.read_text(encoding="utf-8"))
        )
        add_check(
            records,
            "pyfluent_live",
            "case_udf_layout",
            "INFO",
            "UDM fields derived from case-local UDF.",
            udf_path.name,
        )
        run_pyfluent_live_checks(records, cfg, paths, expected_udm_fields)
    else:
        add_check(records, "pyfluent_live", "enabled", "INFO", "Skipped. Use --with-fluent to run live PyFluent checks.")

    overall_status = summarize_status(records, fail_on_warn=fail_on_warn)

    print_console_summary(records, paths, overall_status)
    save_check_outputs(records, paths, overall_status)

    if overall_status == "FAIL":
        return 2

    return 0


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check final case/data files and post-processing report outputs for one PyFluent case."
    )

    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help="Path to post config file. Defaults to PYFLUENT_POST_CONFIG or <project>/configs/post_config.py.",
    )

    parser.add_argument(
        "--family",
        type=str,
        default=None,
        help="Run family selector (with --geo-id --mesh-id --run-id).",
    )
    parser.add_argument(
        "--geo-id",
        type=str,
        default=None,
        help="geo_id selector.",
    )
    parser.add_argument(
        "--mesh-id",
        type=str,
        default=None,
        help="mesh_id selector.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="run_id selector.",
    )
    parser.add_argument(
        "--geo-name",
        type=str,
        default=None,
        help="Override geo_name from config (filename label).",
    )

    parser.add_argument(
        "--case-name",
        type=str,
        default=None,
        help="Override case_name from config (filename label).",
    )

    parser.add_argument(
        "--with-fluent",
        action="store_true",
        help="Launch PyFluent and attempt live zone/field checks.",
    )

    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Return nonzero exit code if any WARN is present.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    config_path = Path(args.config).resolve()

    try:
        return run_field_check(
            config_path=config_path,
            geo_name_override=args.geo_name,
            case_name_override=args.case_name,
            family=args.family,
            geo_id=args.geo_id,
            mesh_id=args.mesh_id,
            run_id=args.run_id,
            with_fluent=args.with_fluent,
            fail_on_warn=args.fail_on_warn,
        )
    except Exception as exc:
        print("")
        print("=" * 72)
        print("FIELD CHECK SCRIPT FAILED")
        print("=" * 72)
        print(f"{type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        print("=" * 72)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())