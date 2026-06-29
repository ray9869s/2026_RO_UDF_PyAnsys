# -*- coding: utf-8 -*-
"""
03_pyensight_contour_export.py

Export presentation-quality contour images from a solved Fluent case using
PyEnSight (ansys.pyensight.core v0.11+, EnSight 25.1).

Fields exported:
  cp_inlet         - UDM-9  on membrane walls
  lmh              - UDM-8  on membrane walls
  wall_shear_rate  - wall-shear / mu on membrane + spacer walls
  velocity_midplane- velocity-magnitude on best-available plane/fluid surface

Usage:
  python 03_pyensight_contour_export.py --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03_pyensight_contour_export.py --dry-run --fields cp_inlet,lmh
  python 03_pyensight_contour_export.py --skip-existing --image-width 2560 --image-height 1440
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# PyEnSight import guard — fail early with a clear message if not installed
# ---------------------------------------------------------------------------

_PYENSIGHT_AVAILABLE = False
_PYENSIGHT_IMPORT_ERROR = ""

try:
    import ansys.pyensight.core as _pyensight_pkg  # noqa: F401
    _PYENSIGHT_AVAILABLE = True
except ImportError as _exc:
    _PYENSIGHT_IMPORT_ERROR = str(_exc)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

# ---------------------------------------------------------------------------
# Field specifications
# ---------------------------------------------------------------------------

DEFAULT_FIELDS: List[str] = ["cp_inlet", "lmh", "wall_shear_rate", "velocity_midplane"]

# Candidates are tried in order (exact, then normalised) against ENS_VAR.DESCRIPTION
FIELD_SPECS: dict = {
    "cp_inlet": {
        "display_label": "CP_inlet [-]",
        "var_candidates": ["udm-9", "UDM-9", "User Defined Memory 9", "udm_9"],
        "surface_type": "membrane",
        "output_suffix": "cp_inlet_membrane",
        "derive_shear_rate": False,
    },
    "lmh": {
        "display_label": "LMH [LMH]",
        "var_candidates": ["udm-8", "UDM-8", "User Defined Memory 8", "udm_8"],
        "surface_type": "membrane",
        "output_suffix": "lmh_membrane",
        "derive_shear_rate": False,
    },
    "wall_shear_rate": {
        "display_label": "Wall shear rate [1/s]",
        "var_candidates": [
            "wall-shear", "Wall Shear Stress", "wall_shear",
            "wall-shear-stress", "Wall Shear", "wall-shear-1",
        ],
        "surface_type": "membrane_and_spacer",
        "output_suffix": "wall_shear_rate_membrane",
        "derive_shear_rate": True,
    },
    "velocity_midplane": {
        "display_label": "Velocity magnitude [m/s]",
        "var_candidates": [
            "velocity-magnitude", "Velocity Magnitude", "velocity_magnitude",
            "Velocity", "velocity",
        ],
        "surface_type": "midplane",
        "output_suffix": "velocity_midplane",
        "derive_shear_rate": False,
    },
}

# ---------------------------------------------------------------------------
# Status constants and record dataclass
# ---------------------------------------------------------------------------

STATUS_SUCCESS = "SUCCESS"
STATUS_WARN = "WARN"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED_EXISTING = "SKIPPED_EXISTING"
STATUS_DRY_RUN = "DRY_RUN"


@dataclass
class ExportRecord:
    geo_name: str
    case_name: str
    field_key: str
    field_name: str
    target_surface_or_plane: str
    output_file: str
    status: str
    message: str = ""


# ---------------------------------------------------------------------------
# Config loading — importlib because filename starts with a digit
# ---------------------------------------------------------------------------

def load_python_config(config_path: Path) -> Any:
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    spec = importlib.util.spec_from_file_location("post_config", str(config_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build module spec for: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cfg_get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


def as_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


# ---------------------------------------------------------------------------
# Path building
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
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export contour images from a solved Fluent case via PyEnSight.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Fields available: " + ", ".join(FIELD_SPECS) + "\n\n"
            "Examples:\n"
            "  python 03_pyensight_contour_export.py "
            "--geo-name Diamond_Spacer --case-name u0p2_p6M\n"
            "  python 03_pyensight_contour_export.py --dry-run --fields cp_inlet,lmh\n"
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help=(
            "Path to the post-processing config Python file. "
            f"Defaults to ${CONFIG_ENV_VAR} or 00_post_config.py."
        ),
    )
    parser.add_argument("--geo-name", type=str, default=None, help="Override geo_name from config.")
    parser.add_argument("--case-name", type=str, default=None, help="Override case_name from config.")
    parser.add_argument(
        "--fields",
        type=str,
        default=None,
        help="Comma-separated fields to export. Default: all four fields.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and plan only; do not launch PyEnSight or write images.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip export if the output PNG already exists.",
    )
    parser.add_argument("--image-width", type=int, default=1920, help="Image width in pixels (default: 1920).")
    parser.add_argument("--image-height", type=int, default=1080, help="Image height in pixels (default: 1080).")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Export plan
# ---------------------------------------------------------------------------

def build_export_plan(
    cfg: Any,
    paths: dict,
    field_keys: List[str],
) -> List[dict]:
    geo_name = paths["geo_name"]
    case_name = paths["case_name"]
    figures_dir = Path(paths["figures_dir"])

    mu = float(cfg_get(cfg, "mu", 8.93e-4))
    active_membrane_base_names = list(
        cfg_get(cfg, "active_membrane_base_names", ["wall_top_mem", "wall_bottom_mem"])
    )
    buffer_wall_base_names = list(
        cfg_get(cfg, "buffer_wall_base_names", ["wall_top_buffer", "wall_bottom_buffer"])
    )
    wall_spacer_labels = list(cfg_get(cfg, "wall_spacer_labels", []))

    plan = []
    for key in field_keys:
        if key not in FIELD_SPECS:
            print(f"WARNING: Unknown field '{key}', skipping.")
            continue
        spec = FIELD_SPECS[key]
        out_file = figures_dir / f"{geo_name}_{case_name}_{spec['output_suffix']}.png"
        plan.append({
            "field_key": key,
            "field_name": spec["display_label"],
            "var_candidates": spec["var_candidates"],
            "surface_type": spec["surface_type"],
            "output_file": out_file,
            "mu": mu,
            "derive_shear_rate": spec["derive_shear_rate"],
            "active_membrane_base_names": active_membrane_base_names,
            "buffer_wall_base_names": buffer_wall_base_names,
            "wall_spacer_labels": wall_spacer_labels,
        })

    return plan


# ---------------------------------------------------------------------------
# Surface matching helpers (mirrors 01_pyfluent_report_extract.py conventions)
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _zone_matches_base(zone_name: str, base_name: str) -> bool:
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def _find_by_base_names(all_names: List[str], base_names: List[str]) -> List[str]:
    matched = [n for n in all_names for b in base_names if _zone_matches_base(n, b)]
    return sorted(set(matched))


def _find_normalized(all_names: List[str], base_names: List[str]) -> List[str]:
    result = []
    for base in base_names:
        base_n = _normalize(base)
        for name in all_names:
            nn = _normalize(name)
            if nn == base_n or nn.startswith(base_n + "_"):
                result.append(name)
    return sorted(set(result))


def _find_by_keywords(all_names: List[str], keywords: List[str]) -> List[str]:
    result = []
    for kw in keywords:
        kw_n = _normalize(kw)
        for name in all_names:
            if kw_n in _normalize(name):
                result.append(name)
    return sorted(set(result))


# ---------------------------------------------------------------------------
# Find surfaces in the loaded PyEnSight session
# ---------------------------------------------------------------------------

def find_surfaces(
    session: Any,
    surface_type: str,
    plan_item: dict,
) -> Tuple[List[str], str]:
    """Return (matched_part_names, warning_message). warning_message is '' on clean match."""
    active_membrane = plan_item["active_membrane_base_names"]
    spacer_walls = plan_item["wall_spacer_labels"]

    try:
        all_parts = session.ensight.objs.core.PARTS
        all_names = [p.DESCRIPTION for p in all_parts if p.DESCRIPTION]
    except Exception as exc:
        return [], f"Could not list EnSight parts: {exc}"

    if surface_type == "midplane":
        # Prefer parts already named as a plane/interior slice
        plane_kws = ["midplane", "mid_plane", "interior", "mid-plane", "plane_mid", "symm"]
        matched = _find_by_keywords(all_names, plane_kws)
        if matched:
            return matched, ""
        # Fall back: select 3D volume parts
        try:
            vol_parts = session.ensight.utils.parts.select_parts_by_dimension(3)
            vol_names = [p.DESCRIPTION for p in vol_parts if p.DESCRIPTION]
            if vol_names:
                return vol_names, (
                    "No named mid-plane part found; using 3D volume parts for velocity. "
                    "For a true cross-section, pre-create a mid-plane surface in Fluent/EnSight."
                )
        except Exception as exc_dim:
            pass
        return all_names[:10], "Mid-plane and 3D part lookup both failed; using first available parts."

    if surface_type == "membrane":
        target_bases = active_membrane
    elif surface_type == "membrane_and_spacer":
        target_bases = active_membrane + spacer_walls if spacer_walls else active_membrane
    else:
        target_bases = active_membrane

    # Tier 1: exact / base.N match
    matched = _find_by_base_names(all_names, target_bases)
    if matched:
        return matched, ""

    # Tier 2: normalised match
    matched = _find_normalized(all_names, target_bases)
    if matched:
        return matched, f"Used normalised name matching for bases: {target_bases}"

    # Tier 3: keyword contains
    keywords = [b.split("_")[-1] for b in target_bases if "_" in b] + target_bases
    matched = _find_by_keywords(all_names, keywords)
    if matched:
        return matched, f"Keyword-matched surfaces {matched}; verify. Available ({len(all_names)}): {all_names[:20]}"

    return [], (
        f"No surfaces found for base names {target_bases}. "
        f"Available ({len(all_names)}): {all_names[:20]}"
    )


# ---------------------------------------------------------------------------
# Find EnSight variable by candidate name list
# ---------------------------------------------------------------------------

def find_ensight_variable(
    session: Any,
    candidates: List[str],
) -> Tuple[Optional[Any], Optional[str]]:
    """Return (var_object, matched_DESCRIPTION) or (None, None) if not found."""
    try:
        all_vars = list(session.ensight.objs.core.VARIABLES)
        desc_to_var = {v.DESCRIPTION: v for v in all_vars if v.DESCRIPTION}

        for c in candidates:
            if c in desc_to_var:
                return desc_to_var[c], c

        norm_map = {_normalize(k): (k, v) for k, v in desc_to_var.items()}
        for c in candidates:
            cn = _normalize(c)
            if cn in norm_map:
                orig_name, var_obj = norm_map[cn]
                return var_obj, orig_name

        return None, None
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Open case in PyEnSight
# ---------------------------------------------------------------------------

def open_case_in_pyensight(paths: dict) -> Tuple[Optional[Any], str]:
    """Return (session, error_msg). error_msg is '' on success."""
    if not _PYENSIGHT_AVAILABLE:
        return None, (
            "ansys.pyensight.core is not installed or could not be imported.\n"
            f"  Install : pip install ansys-pyensight-core\n"
            f"  Error   : {_PYENSIGHT_IMPORT_ERROR}"
        )

    from ansys.pyensight.core import LocalLauncher

    case_file = Path(paths["final_case_file"])
    data_file = Path(paths["final_data_file"])

    if not case_file.is_file():
        return None, f"Case file not found: {case_file}"
    if not data_file.is_file():
        return None, f"Data file not found: {data_file}"

    session = None
    try:
        print("Launching PyEnSight (batch/headless mode)...")
        # LocalLauncher defaults to batch=True; finds EnSight via ANS_SENV_* env vars
        session = LocalLauncher().start()
        print("PyEnSight session started.")

        case_str = str(case_file).replace("\\", "/")
        data_str = str(data_file).replace("\\", "/")
        print(f"Loading case : {case_str}")
        print(f"Loading data : {data_str}")

        errors: List[str] = []
        loaded = False

        # Attempt 1: standard Fluent dual-file (case = geometry, result = data)
        try:
            session.load_data(case_str, result_file=data_str)
            loaded = True
            print("Case loaded (attempt 1: dual-file via load_data).")
        except Exception as e1:
            errors.append(f"attempt1 (dual-file): {e1}")

        # Attempt 2: case file only (EnSight may locate paired .dat.h5 automatically)
        if not loaded:
            try:
                session.load_data(case_str)
                loaded = True
                print("Case loaded (attempt 2: case file only).")
            except Exception as e2:
                errors.append(f"attempt2 (case only): {e2}")

        # Attempt 3: explicit Fluent reader hint
        if not loaded:
            try:
                session.load_data(case_str, result_file=data_str, file_format="Fluent Fluent")
                loaded = True
                print("Case loaded (attempt 3: explicit Fluent reader).")
            except Exception as e3:
                errors.append(f"attempt3 (explicit format): {e3}")

        if not loaded:
            return session, "All load attempts failed:\n  " + "\n  ".join(errors)

        return session, ""

    except Exception as exc:
        return session, f"Failed to start PyEnSight or load case: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Export one contour image
# ---------------------------------------------------------------------------

def export_contour(
    session: Any,
    plan_item: dict,
    image_width: int,
    image_height: int,
    records: List[ExportRecord],
    geo_name: str,
    case_name: str,
) -> None:
    field_key = plan_item["field_key"]
    field_name = plan_item["field_name"]
    output_file = Path(plan_item["output_file"])
    surface_type = plan_item["surface_type"]
    var_candidates = plan_item["var_candidates"]
    derive_shear_rate = plan_item["derive_shear_rate"]
    mu = plan_item["mu"]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []

    def _record(status: str, surface_desc: str, message: str) -> None:
        records.append(ExportRecord(
            geo_name=geo_name,
            case_name=case_name,
            field_key=field_key,
            field_name=field_name,
            target_surface_or_plane=surface_desc,
            output_file=str(output_file),
            status=status,
            message=message,
        ))

    # 1. Locate surfaces
    surface_names, surface_warn = find_surfaces(session, surface_type, plan_item)
    if surface_warn:
        warnings.append(surface_warn)
    surface_desc = ", ".join(surface_names) if surface_names else "none"

    if not surface_names:
        _record(STATUS_WARN, surface_desc, f"No surfaces found: {surface_warn}")
        return

    # 2. Locate variable
    var_obj, matched_var_desc = find_ensight_variable(session, var_candidates)
    if var_obj is None:
        _record(
            STATUS_WARN, surface_desc,
            f"Variable not found among candidates {var_candidates}. "
            "Ensure the .dat.h5 contains UDM/field data and the file loaded correctly."
        )
        return

    # 3. Optionally derive wall shear rate (wall-shear / mu)
    display_var_desc = matched_var_desc
    if derive_shear_rate:
        try:
            derived_name = "pp_wall_shear_rate"
            # EnSight expression language requires quoting names containing hyphens or spaces
            safe_src = (
                f"'{matched_var_desc}'"
                if ("-" in matched_var_desc or " " in matched_var_desc)
                else matched_var_desc
            )
            session.ensight.part.select_all()
            session.ensight.variables.evaluate(f"{derived_name} = {safe_src} / {mu}")
            derived_var, derived_desc = find_ensight_variable(session, [derived_name])
            if derived_var is not None:
                var_obj = derived_var
                display_var_desc = derived_desc
                warnings.append(f"Derived {derived_name} = '{matched_var_desc}' / mu={mu} Pa·s.")
            else:
                warnings.append(
                    f"Derived variable not found after evaluate(); "
                    f"using '{matched_var_desc}' (Pa, not 1/s)."
                )
        except Exception as exc_derive:
            warnings.append(
                f"Derivation failed ({exc_derive}); "
                f"using '{matched_var_desc}' (Pa, not 1/s)."
            )

    # 4. Select parts and apply palette coloring
    try:
        all_parts = session.ensight.objs.core.PARTS

        # Clear all selections
        all_parts.set_attr("SELECTED", False)

        surface_name_set = set(surface_names)
        target_parts = [p for p in all_parts if p.DESCRIPTION in surface_name_set]

        if not target_parts:
            _record(
                STATUS_WARN, surface_desc,
                f"Found {len(surface_names)} surface name(s) but none matched a loaded part."
            )
            return

        # Synchronise selection into EnSight command language
        session.ensight.utils.parts.select_parts(target_parts)

        # Assign the variable palette to each target part
        for p in target_parts:
            p.COLORBYPALETTE = display_var_desc

        # Fit view to selected geometry
        try:
            session.ensight.view_transf.fit(0)
        except Exception:
            pass

    except Exception as exc_setup:
        _record(STATUS_FAILED, surface_desc, f"Part selection/coloring failed: {exc_setup}")
        return

    # 5. Render and save image
    # session.ensight.utils.export.image() saves to the LOCAL client filesystem (not EnSight server).
    try:
        png_path = str(output_file).replace("\\", "/")
        session.ensight.utils.export.image(png_path, width=image_width, height=image_height, passes=4)
    except Exception as exc_export:
        _record(STATUS_FAILED, surface_desc, f"Image export failed: {exc_export}")
        return

    if output_file.is_file():
        final_status = STATUS_WARN if warnings else STATUS_SUCCESS
        msg = "; ".join(warnings) if warnings else f"Exported {output_file.name}"
        _record(final_status, surface_desc, msg)
    else:
        _record(
            STATUS_FAILED, surface_desc,
            "export.image() completed without error but output file was not created."
        )


# ---------------------------------------------------------------------------
# Save status JSON
# ---------------------------------------------------------------------------

def save_status(
    records: List[ExportRecord],
    figures_dir: Path,
    geo_name: str,
    case_name: str,
) -> None:
    status_file = figures_dir / "contour_export_status.json"
    figures_dir.mkdir(parents=True, exist_ok=True)

    n_success = sum(1 for r in records if r.status == STATUS_SUCCESS)
    n_warn = sum(1 for r in records if r.status == STATUS_WARN)
    n_failed = sum(1 for r in records if r.status == STATUS_FAILED)
    n_skipped = sum(1 for r in records if r.status in (STATUS_SKIPPED_EXISTING, STATUS_DRY_RUN))

    payload = {
        "geo_name": geo_name,
        "case_name": case_name,
        "summary": {
            "total": len(records),
            "success": n_success,
            "warn": n_warn,
            "failed": n_failed,
            "skipped": n_skipped,
        },
        "records": [asdict(r) for r in records],
    }

    with status_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    print(f"Status saved: {status_file}")


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------

def _print_summary(records: List[ExportRecord], figures_dir: Path) -> None:
    n_success = sum(1 for r in records if r.status == STATUS_SUCCESS)
    n_warn = sum(1 for r in records if r.status == STATUS_WARN)
    n_failed = sum(1 for r in records if r.status == STATUS_FAILED)
    n_skipped = sum(1 for r in records if r.status in (STATUS_SKIPPED_EXISTING, STATUS_DRY_RUN))

    print()
    print("=" * 72)
    print("CONTOUR EXPORT SUMMARY")
    print("=" * 72)
    print(f"  Total     : {len(records)}")
    print(f"  SUCCESS   : {n_success}")
    print(f"  WARN      : {n_warn}")
    print(f"  FAILED    : {n_failed}")
    print(f"  SKIPPED   : {n_skipped}")
    print(f"  Output dir: {figures_dir}")
    print("-" * 72)
    for r in records:
        msg_short = r.message[:56] if len(r.message) > 56 else r.message
        print(f"  [{r.status:<18}] {r.field_key:<22} {msg_short}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    config_path = Path(args.config).resolve()
    try:
        cfg = load_python_config(config_path)
    except Exception as exc:
        print(f"ERROR: Cannot load config from {config_path}: {exc}")
        return 3

    print(f"Config : {config_path}")

    try:
        paths = get_case_paths(cfg, geo_name_override=args.geo_name, case_name_override=args.case_name)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 3

    geo_name = paths["geo_name"]
    case_name = paths["case_name"]
    figures_dir = Path(paths["figures_dir"])

    print(f"Geo    : {geo_name}")
    print(f"Case   : {case_name}")
    print(f"Case f : {paths['final_case_file']}")
    print(f"Data f : {paths['final_data_file']}")
    print(f"Output : {figures_dir}")

    if args.fields:
        field_keys = [k.strip() for k in args.fields.split(",") if k.strip()]
    else:
        field_keys = list(DEFAULT_FIELDS)
    print(f"Fields : {field_keys}")

    plan = build_export_plan(cfg, paths, field_keys)
    records: List[ExportRecord] = []

    # ---- Dry-run ----
    if args.dry_run:
        print("\nDRY RUN — PyEnSight will not be launched; no images will be written.\n")
        figures_dir.mkdir(parents=True, exist_ok=True)
        for item in plan:
            records.append(ExportRecord(
                geo_name=geo_name,
                case_name=case_name,
                field_key=item["field_key"],
                field_name=item["field_name"],
                target_surface_or_plane=item["surface_type"],
                output_file=str(item["output_file"]),
                status=STATUS_DRY_RUN,
                message="dry-run: no export performed",
            ))
        save_status(records, figures_dir, geo_name, case_name)
        _print_summary(records, figures_dir)
        return 0

    # ---- Skip existing ----
    remaining: List[dict] = []
    if args.skip_existing:
        for item in plan:
            if Path(item["output_file"]).is_file():
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_SKIPPED_EXISTING,
                    message=f"Already exists: {Path(item['output_file']).name}",
                ))
            else:
                remaining.append(item)
        plan = remaining

    if not plan:
        figures_dir.mkdir(parents=True, exist_ok=True)
        save_status(records, figures_dir, geo_name, case_name)
        _print_summary(records, figures_dir)
        return 0

    # ---- PyEnSight must be importable ----
    if not _PYENSIGHT_AVAILABLE:
        print(f"\nFATAL: PyEnSight is not importable.\n  {_PYENSIGHT_IMPORT_ERROR}")
        print("  Install: pip install ansys-pyensight-core")
        return 2

    figures_dir.mkdir(parents=True, exist_ok=True)
    session = None

    try:
        session, open_error = open_case_in_pyensight(paths)

        if open_error:
            print(f"\nFATAL: Could not open case in PyEnSight:\n  {open_error}")
            for item in plan:
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_FAILED,
                    message=f"Case load failed: {open_error[:120]}",
                ))
            save_status(records, figures_dir, geo_name, case_name)
            _print_summary(records, figures_dir)
            return 2

        for item in plan:
            try:
                export_contour(
                    session=session,
                    plan_item=item,
                    image_width=args.image_width,
                    image_height=args.image_height,
                    records=records,
                    geo_name=geo_name,
                    case_name=case_name,
                )
            except Exception as exc_item:
                records.append(ExportRecord(
                    geo_name=geo_name,
                    case_name=case_name,
                    field_key=item["field_key"],
                    field_name=item["field_name"],
                    target_surface_or_plane=item["surface_type"],
                    output_file=str(item["output_file"]),
                    status=STATUS_FAILED,
                    message=f"Unhandled exception: {exc_item}",
                ))

    except Exception as exc_outer:
        print(f"\nFATAL: Unhandled exception:\n{traceback.format_exc()}")
        return 3

    finally:
        if session is not None:
            try:
                session.close()
                print("PyEnSight session closed.")
            except Exception as exc_close:
                print(f"Warning: session.close() raised: {exc_close}")

    save_status(records, figures_dir, geo_name, case_name)
    _print_summary(records, figures_dir)

    n_failed = sum(1 for r in records if r.status == STATUS_FAILED)
    return 2 if n_failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
