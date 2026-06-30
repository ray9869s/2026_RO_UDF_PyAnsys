# -*- coding: utf-8 -*-
"""
03b_pyfluent_shear_contour_export.py

Export a membrane wall shear-rate contour image using PyFluent (Fluent solver).

WHY THIS SCRIPT EXISTS:
  wall-shear is not present in the EnSight variable inventory for this case.
  03_pyensight_contour_export.py records STATUS_WARN with
  derived_variable_mode="wall_shear_unavailable" when shear_rate is requested.
  This script accesses the Fluent solver directly, where wall-shear is always
  available as a built-in wall boundary result.

Formula:
  wall_shear_rate [1/s] = wall-shear [Pa] / mu [Pa·s]
  - wall-shear : Fluent built-in wall shear stress magnitude
  - mu         : dynamic viscosity from 00_post_config.py (default 8.93e-4 Pa·s)

  UDM_10 (cell_strain_rate) is NOT used.  Cell strain rate is NOT used.

Output:
  <figures_dir>/<geo>_<case>_shear_rate_membrane.png
  <figures_dir>/shear_contour_status.json

Usage:
  python 03b_pyfluent_shear_contour_export.py \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03b_pyfluent_shear_contour_export.py --dry-run \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03b_pyfluent_shear_contour_export.py --membrane-surface both
  python 03b_pyfluent_shear_contour_export.py --shear-range 0,5000
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import traceback
from pathlib import Path
from typing import Any, List, Optional, Tuple

import ansys.fluent.core as pyfluent

# ---------------------------------------------------------------------------
# Paths — resolved from this script's own location (WSL/Linux-safe)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

# Names used inside Fluent for the custom field function and contour object
CFF_NAME = "cff_wall_shear_rate"
CONTOUR_NAME = "pp_shear_rate"


# ---------------------------------------------------------------------------
# Path safety helpers (mirrors 03_pyensight_contour_export.py)
# ---------------------------------------------------------------------------

def _has_windows_drive(p: Path) -> bool:
    return any(re.match(r"^[A-Za-z]:$", part) for part in p.parts)


def _safe_mkdir(p: Path) -> bool:
    if platform.system() != "Windows" and _has_windows_drive(p):
        return False
    p.mkdir(parents=True, exist_ok=True)
    return True


def as_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


def as_fluent_path(path: Any) -> str:
    return os.path.abspath(str(path)).replace("\\", "/")


# ---------------------------------------------------------------------------
# Config loading (mirrors 03_pyensight_contour_export.py)
# ---------------------------------------------------------------------------

def load_python_config(config_path: Path) -> Any:
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build module spec: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


# ---------------------------------------------------------------------------
# Path building (mirrors 03_pyensight_contour_export.py)
# ---------------------------------------------------------------------------

def get_case_paths(
    cfg: Any,
    geo_name_override: Optional[str] = None,
    case_name_override: Optional[str] = None,
) -> dict:
    project_root = as_path(cfg_get(cfg, "project_root", PROJECT_ROOT_DEFAULT)).resolve()
    geo_name = geo_name_override or str(cfg_get(cfg, "geo_name", ""))
    case_name = case_name_override or str(cfg_get(cfg, "case_name", ""))

    if not geo_name or geo_name == "===== Edit here =====":
        raise ValueError("geo_name is unset. Pass --geo-name or edit 00_post_config.py.")
    if not case_name or case_name == "===== Edit here =====":
        raise ValueError("case_name is unset. Pass --case-name or edit 00_post_config.py.")

    results_root = as_path(cfg_get(cfg, "results_dir", project_root / "03_Results"))
    case_path = results_root / geo_name / case_name

    final_case_file = as_path(
        cfg_get(cfg, "final_case_file", case_path / f"{geo_name}_{case_name}_final.cas.h5")
    )
    final_data_file = as_path(
        cfg_get(cfg, "final_data_file", case_path / f"{geo_name}_{case_name}_final.dat.h5")
    )
    figures_dir = case_path / "post" / "figures" / "contours"

    return {
        "project_root": project_root,
        "geo_name": geo_name,
        "case_name": case_name,
        "case_path": case_path,
        "final_case_file": final_case_file,
        "final_data_file": final_data_file,
        "figures_dir": figures_dir,
    }


# ---------------------------------------------------------------------------
# Zone helpers (mirrors 01_pyfluent_report_extract.py)
# ---------------------------------------------------------------------------

def _zone_matches_base(zone_name: str, base_name: str) -> bool:
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def find_zones_by_base_names(zone_names: List[str], base_names: List[str]) -> List[str]:
    return sorted({n for n in zone_names for b in base_names if _zone_matches_base(n, b)})


def list_named_object_names(named_object: Any, label: str = "") -> List[str]:
    try:
        names = named_object.get_object_names()
        if names is not None:
            return list(names)
    except Exception:
        pass
    try:
        state = named_object.get_state()
        if isinstance(state, dict):
            return sorted(state.keys())
        if isinstance(state, list):
            return list(state)
    except Exception:
        pass
    try:
        names = named_object.list_1()
        if names is not None:
            return list(names)
    except Exception as exc:
        if label:
            print(f"  WARN: could not list names for '{label}': {exc}")
    return []


def collect_wall_zones(setup: Any) -> List[str]:
    try:
        return list_named_object_names(setup.boundary_conditions.wall, "wall")
    except Exception as exc:
        print(f"  WARN: collect_wall_zones: {exc}")
        return []


# ---------------------------------------------------------------------------
# Custom field function
# Confirmed API (pyfluent 0.38, Fluent 25.1):
#   solver.tui.define.custom_field_functions.delete(name)
#   solver.tui.define.custom_field_functions.define(name, expression)
# ---------------------------------------------------------------------------

def create_cff(solver: Any, mu: float) -> None:
    """Define Fluent CFF: CFF_NAME = wall-shear / mu.  Raises on failure."""
    definition = f"wall-shear / {mu:.6e}"

    # Remove a pre-existing CFF with the same name to avoid overwrite prompts
    try:
        solver.tui.define.custom_field_functions.delete(CFF_NAME)
    except Exception:
        pass  # Not present — fine

    solver.tui.define.custom_field_functions.define(CFF_NAME, definition)
    print(f"  CFF defined: {CFF_NAME!r} = {definition!r}")


# ---------------------------------------------------------------------------
# Contour creation, display, and image export
# Confirmed API (pyfluent 0.38, Fluent 25.1, settings_251.py):
#   solver.settings.results.graphics.contour                          NamedObject
#   contour.field                                                      String
#   contour.surfaces_list                                              StringList
#   contour.range_options.auto_range  (Boolean)                        True=auto
#   contour.range_options.minimum / .maximum                           Real
#   contour.display()                                                  Command
#   solver.settings.results.graphics.picture.x_resolution             Integer
#   solver.settings.results.graphics.picture.y_resolution             Integer
#   solver.settings.results.graphics.picture.save_picture(file_name)  Command
#   solver.tui.display.save_picture(path)                              TUI fallback
# ---------------------------------------------------------------------------

def export_contour(
    solver: Any,
    membrane_zones: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
) -> None:
    """Create contour, display, and save to output_file.  Raises on any failure."""

    # Delete any existing output file so is_file() confirms this run wrote it
    if output_file.exists():
        output_file.unlink()

    graphics = solver.settings.results.graphics

    # Remove a stale contour object if one exists from a previous run
    try:
        existing = list_named_object_names(graphics.contour, "graphics.contour")
        if CONTOUR_NAME in existing:
            graphics.contour[CONTOUR_NAME].delete()
    except Exception:
        pass

    # Create contour
    contour = graphics.contour.create(CONTOUR_NAME)
    contour.field = CFF_NAME
    contour.surfaces_list = list(membrane_zones)

    # Range — uses range_options (Group with Boolean auto_range + min/max)
    if shear_range is not None:
        contour.range_options.auto_range = False
        contour.range_options.minimum = float(shear_range[0])
        contour.range_options.maximum = float(shear_range[1])
        print(f"  Contour range: [{shear_range[0]}, {shear_range[1]}] 1/s (fixed)")
    else:
        contour.range_options.auto_range = True
        contour.range_options.global_range = True
        print("  Contour range: auto (global)")

    print(f"  Displaying on: {membrane_zones}")
    contour.display()

    # Set picture resolution
    pic = solver.settings.results.graphics.picture
    pic.x_resolution = image_width
    pic.y_resolution = image_height

    output_posix = as_fluent_path(output_file)

    # Save — settings API primary, TUI as fallback
    try:
        pic.save_picture(file_name=output_posix)
    except Exception as exc_settings:
        print(f"  WARN: picture.save_picture error ({exc_settings}); trying TUI")
        try:
            solver.tui.display.save_picture(output_posix)
        except Exception as exc_tui:
            raise RuntimeError(
                "Both save_picture approaches failed.\n"
                f"  Settings API: {exc_settings}\n"
                f"  TUI:          {exc_tui}"
            ) from exc_tui

    if not output_file.is_file():
        raise RuntimeError(
            f"save_picture appeared to succeed but file is missing: {output_file}"
        )

    print(f"  Image saved: {output_file}")


# ---------------------------------------------------------------------------
# Status JSON
# ---------------------------------------------------------------------------

STATUS_OK   = "SUCCESS"
STATUS_FAIL = "FAILED"
STATUS_DRY  = "DRY_RUN"
STATUS_SKIP = "SKIPPED_EXISTING"


def write_status_json(
    status_file: Path,
    geo_name: str,
    case_name: str,
    status: str,
    selected_surfaces: List[str],
    mu_used: float,
    output_file: Path,
    message: str,
) -> None:
    payload = {
        "geo_name": geo_name,
        "case_name": case_name,
        "field_key": "shear_rate",
        "status": status,
        "selected_variable": "wall-shear",
        "derived_variable_mode": "pyfluent_wall_shear_over_mu",
        "contour_target_type": "membrane_wall",
        "selected_surfaces": selected_surfaces,
        "mu_used": mu_used,
        "formula_summary": f"wall-shear / {mu_used:.6e}",
        "output_file": str(output_file),
        "message": message,
    }
    _safe_mkdir(status_file.parent)
    with status_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"Status written: {status_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_range_arg(s: str) -> Tuple[float, float]:
    parts = s.strip().split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected MIN,MAX, got: {s!r}")
    return float(parts[0]), float(parts[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export membrane wall shear-rate contour via PyFluent.\n"
            "Formula: wall_shear_rate [1/s] = wall-shear [Pa] / mu [Pa·s]"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help="Post-processing config path (default: 00_post_config.py beside this script).",
    )
    parser.add_argument("--geo-name",  type=str, default=None)
    parser.add_argument("--case-name", type=str, default=None)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate inputs without launching Fluent.",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Exit 0 without re-exporting if the output PNG already exists.",
    )
    parser.add_argument(
        "--membrane-surface", type=str, default="top",
        choices=["top", "bottom", "both"],
        help="Membrane side to export (default: top).",
    )
    parser.add_argument(
        "--shear-range", type=_parse_range_arg, default=None, metavar="MIN,MAX",
        help="Fixed colorbar range [1/s], e.g. --shear-range 0,5000.",
    )
    parser.add_argument("--image-width",  type=int, default=1920)
    parser.add_argument("--image-height", type=int, default=1080)
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    config_path = Path(args.config).resolve()
    try:
        cfg = load_python_config(config_path)
    except Exception as exc:
        print(f"ERROR: Cannot load config {config_path}: {exc}")
        return 3

    try:
        paths = get_case_paths(
            cfg,
            geo_name_override=args.geo_name,
            case_name_override=args.case_name,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 3

    geo_name    = paths["geo_name"]
    case_name   = paths["case_name"]
    case_path   = Path(paths["case_path"])
    case_file   = Path(paths["final_case_file"])
    data_file   = Path(paths["final_data_file"])
    figures_dir = Path(paths["figures_dir"])

    mu: float               = float(cfg_get(cfg, "mu", 8.93e-4))
    active_mem_bases: List[str] = list(
        cfg_get(cfg, "active_membrane_base_names", ["wall_top_mem", "wall_bottom_mem"])
    )
    product_version: str    = str(cfg_get(cfg, "product_version", "25.1.0"))
    processor_count: int    = int(cfg_get(cfg, "processor_count", 1))
    graphics_driver: str    = str(cfg_get(cfg, "graphics_driver", "dx11"))
    start_timeout:   int    = int(cfg_get(cfg, "fluent_start_timeout", 600))
    health_timeout:  int    = int(cfg_get(cfg, "fluent_health_timeout", 600))

    output_file = figures_dir / f"{geo_name}_{case_name}_shear_rate_membrane.png"
    status_file = figures_dir / "shear_contour_status.json"
    shear_range: Optional[Tuple[float, float]] = args.shear_range

    print(f"Geo          : {geo_name}")
    print(f"Case         : {case_name}")
    print(f"Case file    : {case_file}")
    print(f"Output PNG   : {output_file}")
    print(f"Status JSON  : {status_file}")
    print(f"mu           : {mu:.6e} Pa·s")
    print(f"Formula      : wall-shear / {mu:.6e}")
    print(f"Membrane side: {args.membrane_surface}")
    if shear_range:
        print(f"Shear range  : {shear_range[0]} – {shear_range[1]} [1/s]")

    # --- Dry run ---
    if args.dry_run:
        print("\nDRY RUN — no Fluent launch, no image written.")
        _safe_mkdir(figures_dir)
        write_status_json(
            status_file=status_file, geo_name=geo_name, case_name=case_name,
            status=STATUS_DRY, selected_surfaces=active_mem_bases,
            mu_used=mu, output_file=output_file, message="dry-run only",
        )
        return 0

    # --- Skip existing ---
    if args.skip_existing and output_file.is_file():
        print(f"SKIP: output already exists: {output_file}")
        write_status_json(
            status_file=status_file, geo_name=geo_name, case_name=case_name,
            status=STATUS_SKIP, selected_surfaces=[],
            mu_used=mu, output_file=output_file,
            message=f"Skipped: {output_file.name} already exists.",
        )
        return 0

    # --- Validate input files (only possible when path is reachable) ---
    if case_file.is_file() is False and not _has_windows_drive(case_file):
        print(f"ERROR: Case file not found: {case_file}")
        return 2
    if data_file.is_file() is False and not _has_windows_drive(data_file):
        print(f"ERROR: Data file not found: {data_file}")
        return 2

    _safe_mkdir(figures_dir)
    pyfluent.config.check_health_timeout = health_timeout

    meshing = None
    solver  = None
    final_status  = STATUS_FAIL
    final_message = "Not attempted."
    selected_surfaces: List[str] = []

    try:
        # --- Launch ---
        print("\nLaunching Fluent (meshing mode → solver)...")
        meshing = pyfluent.launch_fluent(
            product_version=product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=processor_count,
            ui_mode="gui",
            graphics_driver=graphics_driver,
            start_timeout=start_timeout,
            cwd=as_fluent_path(case_path),
        )
        print("Switching to solver...")
        solver = meshing.switch_to_solver()
        meshing = None
        print("Solver ready.")

        setup = solver.settings.setup

        # --- Read case and data ---
        print(f"Reading: {as_fluent_path(case_file)}")
        solver.settings.file.read_case_data(file_name=as_fluent_path(case_file))
        print("Case loaded.")

        # --- Detect membrane wall zones ---
        all_wall_zones = collect_wall_zones(setup)
        membrane_zones = find_zones_by_base_names(all_wall_zones, active_mem_bases)
        print(f"Membrane zones found: {membrane_zones}")

        if not membrane_zones:
            raise RuntimeError(
                f"No membrane zones found for base names {active_mem_bases}. "
                f"Available wall zones (first 20): {all_wall_zones[:20]}"
            )

        # Apply membrane-side filter
        if args.membrane_surface == "top":
            flt = [z for z in membrane_zones if "top" in z.lower()]
            if flt:
                membrane_zones = flt
            else:
                print(f"  WARN: no 'top' zones found; using all: {membrane_zones}")
        elif args.membrane_surface == "bottom":
            flt = [z for z in membrane_zones if "bot" in z.lower()]
            if flt:
                membrane_zones = flt
            else:
                print(f"  WARN: no 'bottom' zones found; using all: {membrane_zones}")
        # "both" → keep all

        selected_surfaces = list(membrane_zones)
        print(f"Selected surfaces: {selected_surfaces}")

        # --- Create Custom Field Function ---
        print(f"\nCreating CFF: {CFF_NAME} = wall-shear / {mu:.6e}")
        create_cff(solver, mu)

        # --- Export contour image ---
        print("\nExporting contour image...")
        export_contour(
            solver=solver,
            membrane_zones=selected_surfaces,
            shear_range=shear_range,
            output_file=output_file,
            image_width=args.image_width,
            image_height=args.image_height,
        )

        final_status  = STATUS_OK
        final_message = (
            f"wall-shear / mu={mu:.6e}: contour on {selected_surfaces} "
            f"→ {output_file.name}"
        )
        print(f"\nSUCCESS: {output_file}")

    except Exception as exc:
        final_status  = STATUS_FAIL
        final_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        print(f"\nFAIL: {exc}")
        print(traceback.format_exc())

    finally:
        if meshing is not None:
            try:
                meshing.exit()
            except Exception:
                pass
        if solver is not None:
            try:
                solver.exit()
                print("Fluent session closed.")
            except Exception as ec:
                print(f"WARN: solver.exit() raised: {ec}")

    write_status_json(
        status_file=status_file,
        geo_name=geo_name,
        case_name=case_name,
        status=final_status,
        selected_surfaces=selected_surfaces,
        mu_used=mu,
        output_file=output_file,
        message=final_message,
    )

    return 0 if final_status == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
