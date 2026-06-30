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
  UDM_10 is cell-centered strain rate magnitude and is intentionally not used
  for wall shear-rate contouring.

Paths:
  CFF direct:      <figures_dir>/<geo>_<case>_shear_rate_membrane_<side>.png
  Fallback (mpl):  <figures_dir>/<geo>_<case>_shear_rate_membrane_<side>.png
  Status JSON:     <figures_dir>/shear_contour_status.json

Usage:
  python 03b_pyfluent_shear_contour_export.py \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M \\
      --membrane-surface top --background white \\
      --view-margin 1.20 --width 1600 --height 1200
  python 03b_pyfluent_shear_contour_export.py --dry-run \\
      --geo-name Diamond_Spacer --case-name u0p2_p6M
  python 03b_pyfluent_shear_contour_export.py --membrane-surface both
  python 03b_pyfluent_shear_contour_export.py --shear-range 0,5000
  python 03b_pyfluent_shear_contour_export.py --print-cff-manual-steps
  python 03b_pyfluent_shear_contour_export.py --cff-file C:/path/to/shear_rate.cff
"""
from __future__ import annotations

import argparse
import contextlib
import io
import importlib.util
import json
import os
import platform
import re
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ansys.fluent.core as pyfluent
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri

# ---------------------------------------------------------------------------
# Paths — resolved from this script's own location (WSL/Linux-safe)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT_DEFAULT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "00_post_config.py"
CONFIG_ENV_VAR = "PYFLUENT_POST_CONFIG"

# Fluent CFF and contour object names
DEFAULT_CFF_NAME = "cff_wall_shear_rate"
CONTOUR_NAME = "pp_shear_rate"
DEFAULT_MU = 8.93e-4
CFF_CELL_FUNCTION_TOKEN = "wall_shear"
CLEAN_DISPLAY_STATE_NAME = "pp_shear_clean_scene"

# Candidate Fluent scalar variable names for wall shear stress magnitude.
# These are searched against the Fluent field_info registry; only confirmed
# names are used.  DO NOT assume "wall-shear" works in CFF expressions —
# Fluent's Scheme evaluator may parse hyphens as subtraction operators.
SHEAR_FIELD_CANDIDATES = [
    "wall-shear",
    "wall_shear",
    "wall-shear-stress",
    "wall-shear-stress-magnitude",
    "wall-shear-magnitude",
    "Wall Shear Stress",
    "wall shear",
]


# ---------------------------------------------------------------------------
# Path safety helpers
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
# Config loading
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
# Zone helpers
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
# Diagnostic: detect shear-related field and CFF candidates
# ---------------------------------------------------------------------------

def _unique_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def detect_shear_candidates(
    solver: Any,
) -> Tuple[List[str], List[str], List[str], List[str], str]:
    """Return shear-related scalar/CFF diagnostics.

    scalar_candidates:    solverName keys from field_info that match shear/wall keywords.
    cff_candidates:       names from list_valid_cell_function_names that match.
    all_scalar_names_head: first 100 scalar field names when scalar_candidates is empty
                           (populated so a single server run reveals the actual names);
                           empty list when scalar_candidates is non-empty.
    inferred_cff_candidates: CFF tokens inferred from scalar names when the valid CFF
                             list cannot be captured programmatically.
    cff_candidate_source: valid_cff_list, inferred_from_scalar_field_name, mixed, or none.
    """
    scalar_candidates: List[str] = []
    cff_candidates: List[str] = []
    all_scalar_names_head: List[str] = []
    inferred_cff_candidates: List[str] = []
    cff_candidate_source = "none"

    # Method 1: enumerate scalar fields via field_data field_info
    print("  [Diag] Enumerating scalar field names via field_info ...")
    try:
        field_info = solver.fields.field_data._field_info
        all_fields = field_info._get_scalar_fields_info()
        kw = {"wall", "shear"}
        scalar_candidates = [
            name for name in all_fields
            if any(k in name.lower() for k in kw)
        ]
        print(f"  [Diag] Wall/shear scalar fields ({len(scalar_candidates)}): {scalar_candidates}")
        if not scalar_candidates:
            all_scalar_names_head = list(all_fields.keys())[:100]
            print(f"  [Diag] (No wall/shear match — full scalar field list head): "
                  f"{all_scalar_names_head!r}")
    except Exception as exc:
        print(f"  [Diag] scalar field enumeration failed: {exc}")

    # Method 2: list valid CFF cell function names
    print("  [Diag] Listing valid CFF cell function names ...")
    try:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            result = solver.tui.define.custom_field_functions.list_valid_cell_function_names()
        raw_parts = []
        if result is not None:
            raw_parts.append(str(result))
        if stdout_buf.getvalue():
            raw_parts.append(stdout_buf.getvalue())
        if stderr_buf.getvalue():
            raw_parts.append(stderr_buf.getvalue())
        raw = "\n".join(raw_parts)
        # The output is typically a multiline string of names
        tokens = [t.strip("()[]{}'\",;") for t in re.split(r"[\s,]+", raw)]
        cff_candidates = _unique_preserve_order([
            t for t in tokens
            if t and any(k in t.lower() for k in ("wall", "shear"))
        ])
        print(f"  [Diag] Wall/shear CFF candidates ({len(cff_candidates)}): {cff_candidates}")
        if not cff_candidates:
            print(f"  [Diag] (Full CFF list head): {raw[:500]!r}")
    except Exception as exc:
        print(f"  [Diag] CFF name listing failed: {exc}")

    # Fluent display/contour scalar fields may use hyphens (wall-shear) while
    # Custom Field Function cell function tokens may use underscores (wall_shear).
    # If the valid CFF list could not be captured but wall-shear is visible as a
    # scalar field, record wall_shear as inferred, not confirmed.
    scalar_norms = {str(name).strip().lower() for name in scalar_candidates}
    if "wall-shear" in scalar_norms and "wall_shear" not in cff_candidates:
        inferred_cff_candidates.append("wall_shear")

    inferred_cff_candidates = _unique_preserve_order(inferred_cff_candidates)
    if cff_candidates and inferred_cff_candidates:
        cff_candidate_source = "valid_cff_list+inferred_from_scalar_field_name"
    elif cff_candidates:
        cff_candidate_source = "valid_cff_list"
    elif inferred_cff_candidates:
        cff_candidate_source = "inferred_from_scalar_field_name"

    return (
        scalar_candidates,
        cff_candidates,
        all_scalar_names_head,
        inferred_cff_candidates,
        cff_candidate_source,
    )


# ---------------------------------------------------------------------------
# CFF direct path
# ---------------------------------------------------------------------------

def _manual_cff_steps(cff_name: str, mu: float = DEFAULT_MU) -> str:
    return (
        "\nManual Fluent CFF setup steps for server use\n"
        "============================================\n"
        f"Recommended CFF name: {cff_name}\n"
        f"Try this Fluent CFF expression first: wall_shear / {mu:.6f}\n\n"
        "On the Windows server, open the solved case/data in Fluent and create a "
        "Custom Field Function in the Fluent GUI with the name and definition "
        "above. If the GUI inserts a different token automatically, use the "
        "GUI-inserted wall shear magnitude token. If your Fluent GUI version "
        "supports saving/exporting Custom Field "
        "Functions, save/export that CFF file.\n\n"
        "Note: the display/contour field may appear as wall-shear, but the CFF "
        "cell function token may be wall_shear.\n\n"
        "Then rerun this script with:\n"
        f"  --cff-file <path_to_saved_cff_file> --cff-name {cff_name}\n\n"
        "Exact GUI menu names vary by Fluent version, so use the Custom Field "
        "Function editor provided by your installed Fluent GUI.\n"
    )


def _format_exception(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _short_text(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, default=str, sort_keys=True)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _safe_public_attrs(obj: Any, limit: int = 120) -> List[str]:
    try:
        names = [str(name) for name in dir(obj) if not str(name).startswith("_")]
    except Exception as exc:
        return [f"<dir failed: {_format_exception(exc)}>"]
    return sorted(names)[:limit]


def _safe_state_head(obj: Any, limit: int = 2000) -> str:
    if not hasattr(obj, "get_state"):
        return ""
    try:
        return _short_text(obj.get_state(), limit=limit)
    except Exception as exc:
        return f"<get_state failed: {_format_exception(exc)}>"


def _try_call(label: str, func: Any) -> Tuple[bool, str]:
    try:
        func()
        return True, ""
    except Exception as exc:
        return False, f"{label}: {_format_exception(exc)}"


def _load_cff_file(solver: Any, cff_file: Path) -> Tuple[bool, str, str]:
    """Best-effort CFF file load through likely Fluent TUI entry points.

    Returns (success, method_label, error_summary). The exact PyFluent TUI path
    varies by Fluent version, so each attempt is isolated and recorded.
    """
    cff_path = as_fluent_path(cff_file)
    attempts: List[Tuple[str, Any]] = [
        (
            "tui.define.custom_field_functions.read",
            lambda: solver.tui.define.custom_field_functions.read(cff_path),
        ),
        (
            "tui.define.custom_field_functions.load",
            lambda: solver.tui.define.custom_field_functions.load(cff_path),
        ),
        (
            "tui.define.custom_field_functions.read_cff_file",
            lambda: solver.tui.define.custom_field_functions.read_cff_file(cff_path),
        ),
        (
            "tui.file.read_custom_field_functions",
            lambda: solver.tui.file.read_custom_field_functions(cff_path),
        ),
    ]

    errors: List[str] = []
    for label, func in attempts:
        print(f"  Trying CFF file load via {label}: {cff_path}")
        ok, err = _try_call(label, func)
        if ok:
            print(f"  CFF file load succeeded via {label}")
            return True, label, ""
        errors.append(err)
        print(f"  CFF file load failed: {err}")

    return False, "", " | ".join(errors)


def _cff_expression_variants(mu: float) -> List[str]:
    return [
        f"{CFF_CELL_FUNCTION_TOKEN} / {mu:.6e}",
        f"wall-shear / {mu:.6e}",
        f'"wall-shear" / {mu:.6e}',
        f"{{wall-shear}} / {mu:.6e}",
        f"[wall-shear] / {mu:.6e}",
    ]


def _default_cff_expression(mu: float) -> str:
    return f"{CFF_CELL_FUNCTION_TOKEN} / {mu:.6e}"


def _cell_function_token_from_expression(expression: str) -> str:
    token = expression.split("/", 1)[0].strip()
    return token.strip("\"'{}[]")


def _find_expression_state_key(state: Dict[Any, Any]) -> Optional[Any]:
    for key in state.keys():
        key_l = str(key).lower().replace("-", "_")
        if any(marker in key_l for marker in ("definition", "expression", "formula")):
            return key
    return None


def _try_set_settings_target_expression(
    target: Any,
    expression: str,
    errors: List[str],
) -> Tuple[bool, str]:
    target_attrs = _safe_public_attrs(target, limit=10000)

    for attr_name in (
        "definition",
        "expression",
        "field_function",
        "field_function_definition",
        "formula",
    ):
        if attr_name not in target_attrs:
            continue
        try:
            setattr(target, attr_name, expression)
            return True, f"target setattr {attr_name}"
        except Exception as exc:
            errors.append(f"target setattr {attr_name}: {_format_exception(exc)}")

    if "get_state" in target_attrs and "set_state" in target_attrs:
        try:
            state = target.get_state()
            if isinstance(state, dict):
                key = _find_expression_state_key(state)
                if key is not None:
                    new_state = dict(state)
                    new_state[key] = expression
                    target.set_state(new_state)
                    return True, f"target set_state key {key!r}"
                errors.append("target set_state: no expression/definition/formula key")
            else:
                errors.append(f"target get_state returned {type(state).__name__}, not dict")
        except Exception as exc:
            errors.append(f"target set_state: {_format_exception(exc)}")

    return False, ""


def _try_get_settings_named_object(
    collection: Any,
    cff_name: str,
    errors: List[str],
) -> Optional[Any]:
    if hasattr(collection, "__getitem__"):
        try:
            return collection[cff_name]
        except Exception as exc:
            errors.append(f"settings getitem {cff_name!r}: {_format_exception(exc)}")
    else:
        errors.append("settings getitem: __getitem__ not available")

    attrs = _safe_public_attrs(collection, limit=10000)
    if "get" in attrs:
        try:
            return collection.get(cff_name)
        except Exception as exc:
            errors.append(f"settings get {cff_name!r}: {_format_exception(exc)}")

    return None


def _try_settings_collection_state(
    collection: Any,
    cff_name: str,
    expression: str,
    errors: List[str],
) -> Tuple[bool, str]:
    attrs = _safe_public_attrs(collection, limit=10000)
    if "get_state" not in attrs or "set_state" not in attrs:
        return False, ""

    try:
        state = collection.get_state()
    except Exception as exc:
        errors.append(f"collection get_state: {_format_exception(exc)}")
        return False, ""

    if not isinstance(state, dict):
        errors.append(f"collection get_state returned {type(state).__name__}, not dict")
        return False, ""

    new_state = dict(state)
    if cff_name in new_state and isinstance(new_state[cff_name], dict):
        entry = dict(new_state[cff_name])
        key = _find_expression_state_key(entry)
        if key is not None:
            entry[key] = expression
            new_state[cff_name] = entry
            try:
                collection.set_state(new_state)
                return True, f"collection set_state existing {cff_name!r} key {key!r}"
            except Exception as exc:
                errors.append(f"collection set_state existing: {_format_exception(exc)}")

    for exemplar_name, exemplar in state.items():
        if not isinstance(exemplar, dict):
            continue
        key = _find_expression_state_key(exemplar)
        if key is None:
            continue
        entry = dict(exemplar)
        entry[key] = expression
        name_key = next(
            (k for k in entry.keys() if str(k).lower() in ("name", "field_function_name")),
            None,
        )
        if name_key is not None:
            entry[name_key] = cff_name
        new_state[cff_name] = entry
        try:
            collection.set_state(new_state)
            return (
                True,
                f"collection set_state cloned {exemplar_name!r} with key {key!r}",
            )
        except Exception as exc:
            errors.append(f"collection set_state cloned exemplar: {_format_exception(exc)}")
        break

    errors.append("collection set_state: no existing state shape with expression key")
    return False, ""


def create_cff_via_settings_api(
    solver: Any,
    cff_name: str,
    expression: str,
) -> Dict[str, Any]:
    """Best-effort CFF creation through discovered settings API objects."""
    result: Dict[str, Any] = {
        "attempted": True,
        "status": "FAILED",
        "error": "",
        "available_attrs": [],
        "state_head": "",
        "method_used": "",
        "errors": [],
        "selected_cff_cell_function": _cell_function_token_from_expression(expression),
    }
    errors: List[str] = []

    try:
        cff_settings = solver.settings.results.custom_field_functions
    except Exception as exc:
        err = f"solver.settings.results.custom_field_functions: {_format_exception(exc)}"
        result["error"] = err
        result["errors"] = [err]
        return result

    result["available_attrs"] = _safe_public_attrs(cff_settings)
    result["state_head"] = _safe_state_head(cff_settings)
    attrs = set(_safe_public_attrs(cff_settings, limit=10000))

    print("  Trying settings API CFF creation ...")
    print(f"  Settings CFF expression: {cff_name!r} = {expression!r}")

    target = _try_get_settings_named_object(cff_settings, cff_name, errors)
    if target is not None:
        ok, method = _try_set_settings_target_expression(target, expression, errors)
        if ok:
            result["status"] = "SUCCESS"
            result["method_used"] = f"settings existing object via {method}"
            return result

    if "delete" in attrs:
        try:
            cff_settings.delete(cff_name)
        except Exception as exc:
            errors.append(f"settings delete existing {cff_name!r}: {_format_exception(exc)}")

    if "create" in attrs:
        create_attempts: List[Tuple[str, Any]] = [
            ("create(cff_name)", lambda: cff_settings.create(cff_name)),
            ("create(name=cff_name)", lambda: cff_settings.create(name=cff_name)),
        ]
        for label, create_call in create_attempts:
            try:
                created = create_call()
                target = created
                if target is None:
                    target = _try_get_settings_named_object(cff_settings, cff_name, errors)
                if target is None:
                    errors.append(f"settings {label}: created object was not retrievable")
                    continue
                ok, method = _try_set_settings_target_expression(target, expression, errors)
                if ok:
                    result["status"] = "SUCCESS"
                    result["method_used"] = f"settings {label} + {method}"
                    return result
                retrieved = _try_get_settings_named_object(cff_settings, cff_name, errors)
                if retrieved is not None and retrieved is not target:
                    ok, method = _try_set_settings_target_expression(
                        retrieved, expression, errors
                    )
                    if ok:
                        result["status"] = "SUCCESS"
                        result["method_used"] = (
                            f"settings {label} + retrieved object + {method}"
                        )
                        return result
                errors.append(f"settings {label}: could not set expression on target")
            except Exception as exc:
                errors.append(f"settings {label}: {_format_exception(exc)}")
    else:
        errors.append("settings create: method not available")

    ok, method = _try_settings_collection_state(cff_settings, cff_name, expression, errors)
    if ok:
        result["status"] = "SUCCESS"
        result["method_used"] = f"settings {method}"
        return result

    result["errors"] = errors
    result["error"] = " | ".join(errors) or "Settings API CFF creation failed"
    return result


def try_tui_journal_style_cff_creation(
    solver: Any,
    cff_name: str,
    expression: str,
) -> Dict[str, Any]:
    """Record journal-style status without inventing an unverified CFF sequence."""
    return {
        "attempted": False,
        "status": "SKIPPED",
        "error": "No verified command-string CFF definition sequence found in repo/static inspection.",
    }


def _delete_cff_if_exists(solver: Any, cff_name: str) -> None:
    try:
        solver.tui.define.custom_field_functions.delete(cff_name)
    except Exception:
        pass


def _try_define_cff_expression(
    solver: Any,
    cff_name: str,
    expression: str,
) -> Tuple[bool, str]:
    _delete_cff_if_exists(solver, cff_name)
    solver.tui.define.custom_field_functions.define(cff_name, expression)
    return True, ""


def _create_cff_direct(
    solver: Any,
    cff_name: str,
    mu: float,
) -> Tuple[bool, List[Dict[str, str]], List[str]]:
    """Try bounded CFF expression variants for wall-shear/mu."""
    attempts: List[Dict[str, str]] = []
    errors: List[str] = []

    for expression in _cff_expression_variants(mu):
        print(f"  Trying direct CFF: {cff_name!r} = {expression!r}")
        try:
            _try_define_cff_expression(solver, cff_name, expression)
            attempts.append({
                "cff_name": cff_name,
                "expression": expression,
                "cell_function_token": _cell_function_token_from_expression(expression),
                "status": "SUCCESS",
                "error": "",
            })
            print(f"  Direct CFF created: {cff_name!r}")
            return True, attempts, errors
        except Exception as exc:
            err = _format_exception(exc)
            attempts.append({
                "cff_name": cff_name,
                "expression": expression,
                "cell_function_token": _cell_function_token_from_expression(expression),
                "status": "FAILED",
                "error": err,
            })
            errors.append(f"{expression}: {err}")
            print(f"  Direct CFF failed: {err}")

    return False, attempts, errors


def prepare_native_cff(
    solver: Any,
    cff_name: str,
    cff_file: Optional[Path],
    mu: float,
) -> Dict[str, Any]:
    """Load or create a CFF whose value is wall shear rate [1/s]."""
    result: Dict[str, Any] = {
        "native_variable_used": "",
        "cff_file_load_attempted": False,
        "cff_file_load_status": "SKIPPED",
        "cff_file_load_error": "",
        "settings_cff_attempted": False,
        "settings_cff_status": "SKIPPED",
        "settings_cff_error": "",
        "settings_cff_available_attrs": [],
        "settings_cff_state_head": "",
        "tui_direct_attempted": False,
        "tui_direct_status": "SKIPPED",
        "tui_direct_errors": [],
        "tui_journal_style_attempted": False,
        "tui_journal_style_status": "SKIPPED",
        "tui_journal_style_error": "",
        "cff_creation_method_used": "",
        "cff_expression": _default_cff_expression(mu),
        "cff_direct_create_attempted": False,
        "cff_direct_create_status": "SKIPPED",
        "cff_direct_create_errors": [],
        "cff_expression_attempts": [],
        "selected_cff_cell_function": "",
        "ready": False,
        "error": "",
    }

    if cff_file is not None:
        result["cff_file_load_attempted"] = True
        ok, method, err = _load_cff_file(solver, cff_file)
        if ok:
            result["cff_file_load_status"] = "SUCCESS"
            result["native_variable_used"] = cff_name
            result["selected_cff_cell_function"] = "from_cff_file"
            result["cff_creation_method_used"] = f"cff_file:{method}"
            result["ready"] = True
            return result
        result["cff_file_load_status"] = "FAILED"
        result["cff_file_load_error"] = err or "CFF file load failed"

    result["settings_cff_attempted"] = True
    settings_result = create_cff_via_settings_api(
        solver=solver,
        cff_name=cff_name,
        expression=result["cff_expression"],
    )
    result["settings_cff_status"] = settings_result["status"]
    result["settings_cff_error"] = settings_result["error"]
    result["settings_cff_available_attrs"] = settings_result["available_attrs"]
    result["settings_cff_state_head"] = settings_result["state_head"]
    if settings_result["status"] == "SUCCESS":
        result["native_variable_used"] = cff_name
        result["selected_cff_cell_function"] = settings_result["selected_cff_cell_function"]
        result["cff_creation_method_used"] = settings_result["method_used"]
        result["ready"] = True
        return result

    journal_result = try_tui_journal_style_cff_creation(
        solver=solver,
        cff_name=cff_name,
        expression=result["cff_expression"],
    )
    result["tui_journal_style_attempted"] = journal_result["attempted"]
    result["tui_journal_style_status"] = journal_result["status"]
    result["tui_journal_style_error"] = journal_result["error"]

    result["tui_direct_attempted"] = True
    result["cff_direct_create_attempted"] = True
    ok, attempts, errors = _create_cff_direct(solver, cff_name, mu)
    result["cff_expression_attempts"] = attempts
    result["cff_direct_create_errors"] = errors
    result["tui_direct_errors"] = errors

    if ok:
        result["cff_direct_create_status"] = "SUCCESS"
        result["tui_direct_status"] = "SUCCESS"
        result["native_variable_used"] = cff_name
        success_attempt = next(
            (attempt for attempt in attempts if attempt.get("status") == "SUCCESS"),
            {},
        )
        result["selected_cff_cell_function"] = success_attempt.get(
            "cell_function_token", ""
        )
        result["cff_creation_method_used"] = "tui_direct_define"
        result["ready"] = True
        return result

    result["cff_direct_create_status"] = "FAILED"
    result["tui_direct_status"] = "FAILED"
    error_parts = [
        str(result["cff_file_load_error"]),
        str(result["settings_cff_error"]),
        str(result["tui_journal_style_error"]),
        "; ".join(errors),
    ]
    result["error"] = " | ".join(part for part in error_parts if part) or (
        "Direct CFF creation failed for all expression variants."
    )
    return result


def _default_scene_cleanup_diag(background: str, view_margin: float) -> Dict[str, Any]:
    return {
        "scene_cleanup_attempted": False,
        "scene_cleanup_status": "SKIPPED",
        "scene_cleanup_error": "",
        "requested_view_preset": "",
        "camera_mode_used": "",
        "camera_method_used": "",
        "camera_method_attempts": [],
        "camera_errors": [],
        "view_direction_used": "",
        "view_up_vector_used": "",
        "view_fit_applied": False,
        "projection_mode_used": "",
        "floor_hidden": None,
        "shadow_hidden": None,
        "reflection_hidden": None,
        "triad_hidden": None,
        "logo_hidden": None,
        "background_used": background,
        "view_margin_used": view_margin,
    }


def _merge_scene_cleanup_diag(
    current: Dict[str, Any],
    new: Dict[str, Any],
) -> Dict[str, Any]:
    if not current.get("scene_cleanup_attempted"):
        return dict(new)
    if not new.get("scene_cleanup_attempted"):
        return dict(current)

    merged = dict(current)
    status_rank = {"SKIPPED": 0, "SUCCESS": 1, "WARN": 2, "FAILED": 3}
    current_status = str(current.get("scene_cleanup_status", "SKIPPED"))
    new_status = str(new.get("scene_cleanup_status", "SKIPPED"))
    merged["scene_cleanup_status"] = (
        current_status
        if status_rank.get(current_status, 0) >= status_rank.get(new_status, 0)
        else new_status
    )

    errors = [
        str(current.get("scene_cleanup_error", "")),
        str(new.get("scene_cleanup_error", "")),
    ]
    merged["scene_cleanup_error"] = " | ".join(e for e in errors if e)

    for key in (
        "camera_mode_used",
        "projection_mode_used",
        "requested_view_preset",
        "camera_method_used",
        "view_direction_used",
        "view_up_vector_used",
    ):
        values = _unique_preserve_order([
            str(current.get(key, "")),
            str(new.get(key, "")),
        ])
        merged[key] = ";".join(v for v in values if v)

    for key in ("camera_method_attempts", "camera_errors"):
        cur_list = list(current.get(key, []))
        new_list = [x for x in new.get(key, []) if x not in set(cur_list)]
        merged[key] = cur_list + new_list

    merged["view_fit_applied"] = bool(current.get("view_fit_applied")) or bool(
        new.get("view_fit_applied")
    )

    for key in (
        "floor_hidden",
        "shadow_hidden",
        "reflection_hidden",
        "triad_hidden",
        "logo_hidden",
    ):
        cur = current.get(key)
        nxt = new.get(key)
        if cur is False or nxt is False:
            merged[key] = False
        elif cur is True or nxt is True:
            merged[key] = True
        else:
            merged[key] = None

    merged["background_used"] = new.get("background_used", current.get("background_used", ""))
    merged["view_margin_used"] = new.get("view_margin_used", current.get("view_margin_used", ""))
    return merged


def _try_scene_call(
    label: str,
    variants: List[Tuple[str, Any]],
    errors: List[str],
) -> str:
    variant_errors: List[str] = []
    for method_label, func in variants:
        try:
            func()
            return method_label
        except Exception as exc:
            variant_errors.append(f"{label} via {method_label}: {_format_exception(exc)}")
    errors.extend(variant_errors)
    return ""


def _try_assign_child(
    parent: Any,
    child_name: str,
    value: Any,
    label: str,
    errors: List[str],
) -> bool:
    if child_name not in _safe_public_attrs(parent, limit=10000):
        errors.append(f"{label}: child {child_name!r} not available")
        return False
    try:
        setattr(parent, child_name, value)
        return True
    except Exception as exc:
        errors.append(f"{label}: {_format_exception(exc)}")
        return False


def _get_or_create_named_object(
    collection: Any,
    name: str,
    errors: List[str],
) -> Optional[Any]:
    attrs = set(_safe_public_attrs(collection, limit=10000))

    if "create" in attrs:
        create_errors: List[str] = []
        for method_label, func in (
            ("create(name)", lambda: collection.create(name)),
            ("create(name=name)", lambda: collection.create(name=name)),
        ):
            try:
                obj = func()
                if obj is not None:
                    return obj
                break
            except Exception as exc:
                create_errors.append(f"display state {method_label}: {_format_exception(exc)}")
    else:
        create_errors = ["display state create: method not available"]

    if hasattr(collection, "__getitem__"):
        try:
            return collection[name]
        except Exception as exc:
            errors.append(f"display state getitem {name!r}: {_format_exception(exc)}")

    errors.extend(create_errors)
    return None


def _configure_display_state(graphics: Any, errors: List[str]) -> Dict[str, Optional[bool]]:
    """Configure and restore a clean display state when Fluent exposes it."""
    flags: Dict[str, Optional[bool]] = {
        "floor_hidden": None,
        "shadow_hidden": None,
        "reflection_hidden": None,
        "triad_hidden": None,
    }

    try:
        states = graphics.views.display_states
    except Exception as exc:
        errors.append(f"display_states access: {_format_exception(exc)}")
        return flags

    state = _get_or_create_named_object(states, CLEAN_DISPLAY_STATE_NAME, errors)
    if state is None:
        errors.append("display state object not available")
        return flags

    state_attrs = set(_safe_public_attrs(state, limit=10000))
    requested = {
        "projection": "orthographic",
        "axes": "disable",
        "ruler": "disable",
        "title": "disable",
        "boundary_marker": "disable",
        "reflections": "disable",
        "static_shadows": "disable",
        "dynamic_shadows": "disable",
        "grid_plane": "disable",
    }
    set_ok: Dict[str, bool] = {}
    for child_name, value in requested.items():
        if child_name not in state_attrs:
            continue
        try:
            setattr(state, child_name, value)
            set_ok[child_name] = True
        except Exception as exc:
            errors.append(f"display state {child_name}={value!r}: {_format_exception(exc)}")

    restore_ok = bool(_try_scene_call(
        "display state restore",
        [
            (
                "settings.views.display_states.restore_state(state_name=...)",
                lambda: states.restore_state(state_name=CLEAN_DISPLAY_STATE_NAME),
            ),
            (
                "settings.views.display_states.restore_state(...)",
                lambda: states.restore_state(CLEAN_DISPLAY_STATE_NAME),
            ),
        ],
        errors,
    ))

    if restore_ok:
        if set_ok.get("grid_plane"):
            flags["floor_hidden"] = True
        if set_ok.get("static_shadows") or set_ok.get("dynamic_shadows"):
            flags["shadow_hidden"] = True
        if set_ok.get("reflections"):
            flags["reflection_hidden"] = True
        if set_ok.get("axes") or set_ok.get("ruler"):
            flags["triad_hidden"] = True

    return flags


def _attempt_named_view(
    solver: Any,
    views: Any,
    view_name: str,
    errors: List[str],
) -> Tuple[str, List[str]]:
    """Try to set a named Fluent view via settings/TUI paths.

    Returns (method_label_on_success_or_empty, list_of_labels_attempted).
    Errors from failed attempts are appended to errors.
    """
    if view_name == "top":
        variants: List[Tuple[str, Any]] = [
            ("views.restore_view('top')", lambda: views.restore_view("top")),
            ("views.restore_view(view_name='top')", lambda: views.restore_view(view_name="top")),
            ("views.top()", lambda: views.top()),
            ("tui.display.views.restore_view('top')", lambda: solver.tui.display.views.restore_view("top")),
            ("tui.display.views.top()", lambda: solver.tui.display.views.top()),
        ]
    elif view_name == "bottom":
        variants = [
            ("views.restore_view('bottom')", lambda: views.restore_view("bottom")),
            ("views.restore_view(view_name='bottom')", lambda: views.restore_view(view_name="bottom")),
            ("views.bottom()", lambda: views.bottom()),
            ("tui.display.views.restore_view('bottom')", lambda: solver.tui.display.views.restore_view("bottom")),
            ("tui.display.views.bottom()", lambda: solver.tui.display.views.bottom()),
        ]
    elif view_name == "front":
        variants = [
            ("views.reset_to_default_view()", lambda: views.reset_to_default_view()),
            ("tui.display.views.restore_view('front')", lambda: solver.tui.display.views.restore_view("front")),
            ("views.restore_view('front')", lambda: views.restore_view("front")),
            ("views.front()", lambda: views.front()),
            ("tui.display.views.default_view()", lambda: solver.tui.display.views.default_view()),
            ("tui.display.views.front()", lambda: solver.tui.display.views.front()),
        ]
    elif view_name == "iso":
        variants = [
            ("views.restore_view('isometric')", lambda: views.restore_view("isometric")),
            ("tui.display.views.restore_view('isometric')", lambda: solver.tui.display.views.restore_view("isometric")),
            ("views.isometric()", lambda: views.isometric()),
            ("tui.display.views.isometric()", lambda: solver.tui.display.views.isometric()),
        ]
    else:
        return "", []

    attempted: List[str] = []
    for label, func in variants:
        attempted.append(label)
        ok, err = _try_call(label, func)
        if ok:
            return label, attempted
        errors.append(f"named view {view_name!r} via {label}: {err}")

    return "", attempted


def _attempt_camera_direction(
    camera: Any,
    effective_view: str,
    errors: List[str],
) -> Tuple[str, str]:
    """Try to orient camera along ±z axis via position/target/look APIs.

    Returns (method_label_on_success_or_empty, direction_str).
    Errors appended to errors on failure.
    """
    if effective_view == "top":
        # Camera above, looking down -z
        pos = [0.0, 0.0, 1.0]
        tgt = [0.0, 0.0, 0.0]
        up = [0.0, 1.0, 0.0]
        dir_str = "0,0,-1"
    elif effective_view == "bottom":
        # Camera below, looking up +z
        pos = [0.0, 0.0, -1.0]
        tgt = [0.0, 0.0, 0.0]
        up = [0.0, 1.0, 0.0]
        dir_str = "0,0,1"
    else:
        return "", ""

    # Capture loop variables before lambdas
    _pos, _tgt, _up = pos, tgt, up
    variants: List[Tuple[str, Any]] = [
        (
            "camera.position+target+up_vector",
            lambda: (
                camera.position(xyz=_pos),
                camera.target(xyz=_tgt),
                camera.up_vector(xyz=_up),
            ),
        ),
        (
            "camera.position([...])+target([...])+up_vector([...])",
            lambda: (
                camera.position(_pos),
                camera.target(_tgt),
                camera.up_vector(_up),
            ),
        ),
        (
            "camera.look_at(position=...,target=...,up=...)",
            lambda: camera.look_at(position=_pos, target=_tgt, up=_up),
        ),
    ]

    for label, func in variants:
        ok, err = _try_call(label, func)
        if ok:
            return label, dir_str
        errors.append(f"camera direction ({effective_view}) via {label}: {err}")

    return "", ""


def setup_fluent_clean_scene(
    solver: Any,
    background: str,
    view_margin: float,
    view_preset: str = "auto",
    membrane_side: str = "top",
) -> Dict[str, Any]:
    """Best-effort Fluent scene cleanup to match the CP contour presentation style."""
    diag = _default_scene_cleanup_diag(background, view_margin)
    diag["scene_cleanup_attempted"] = True
    errors: List[str] = []
    non_critical_errors: List[str] = []
    successful_steps = 0

    try:
        graphics = solver.settings.results.graphics
    except Exception as exc:
        diag["scene_cleanup_status"] = "FAILED"
        diag["scene_cleanup_error"] = (
            f"solver.settings.results.graphics unavailable: {_format_exception(exc)}"
        )
        return diag

    try:
        colors = graphics.colors
        if background == "white":
            if _try_assign_child(colors, "background", "white", "graphics.colors.background", non_critical_errors):
                diag["background_used"] = "white"
                successful_steps += 1
            _try_assign_child(colors, "foreground", "black", "graphics.colors.foreground", non_critical_errors)
        elif background == "black":
            if _try_assign_child(colors, "background", "black", "graphics.colors.background", non_critical_errors):
                diag["background_used"] = "black"
                successful_steps += 1
            _try_assign_child(colors, "foreground", "white", "graphics.colors.foreground", non_critical_errors)
    except Exception as exc:
        non_critical_errors.append(f"graphics.colors setup: {_format_exception(exc)}")

    try:
        pic = graphics.picture
        _try_assign_child(pic, "use_window_resolution", False, "picture.use_window_resolution", errors)
        _try_assign_child(pic, "invert_background", False, "picture.invert_background", errors)
        if _try_assign_child(pic, "raytracer_image", False, "picture.raytracer_image", errors):
            successful_steps += 1
    except Exception as exc:
        errors.append(f"picture cleanup: {_format_exception(exc)}")

    try:
        windows = graphics.windows
        try:
            axes = windows.axes
            if _try_assign_child(axes, "visible", False, "graphics.windows.axes.visible", errors):
                diag["triad_hidden"] = True
                successful_steps += 1
        except Exception as exc:
            errors.append(f"graphics.windows.axes cleanup: {_format_exception(exc)}")
        if _try_assign_child(windows, "logo", False, "graphics.windows.logo", errors):
            diag["logo_hidden"] = True
            successful_steps += 1
        if _try_assign_child(windows, "ruler", False, "graphics.windows.ruler", errors):
            if diag["triad_hidden"] is None:
                diag["triad_hidden"] = True
            successful_steps += 1
    except Exception as exc:
        errors.append(f"graphics.windows cleanup: {_format_exception(exc)}")

    state_flags = _configure_display_state(graphics, errors)
    for key, value in state_flags.items():
        if value is not None:
            diag[key] = value
            successful_steps += 1

    try:
        ray_bg = graphics.raytracing_options.background
        if _try_assign_child(ray_bg, "activate_env_ground", False,
                             "raytracing.background.activate_env_ground", errors):
            diag["floor_hidden"] = True
            successful_steps += 1
        if _try_assign_child(ray_bg, "activate_env_ground_shadow", False,
                             "raytracing.background.activate_env_ground_shadow", errors):
            diag["shadow_hidden"] = True
            successful_steps += 1
        _try_assign_child(ray_bg, "show_backplate", False,
                          "raytracing.background.show_backplate", errors)
    except Exception as exc:
        errors.append(f"raytracing background cleanup: {_format_exception(exc)}")

    try:
        views = graphics.views
        camera = views.camera
        camera_errors: List[str] = []

        # Resolve effective view name from preset + membrane side
        if view_preset == "auto":
            effective_view = "bottom" if membrane_side == "bottom" else "top"
        elif view_preset in ("top", "bottom", "front", "iso"):
            effective_view = view_preset
        else:
            effective_view = "top"
        diag["requested_view_preset"] = view_preset
        all_cam_attempts: List[str] = []

        # Rung 1 + 2: named view via settings/TUI APIs
        view_method, view_attempts = _attempt_named_view(solver, views, effective_view, camera_errors)
        all_cam_attempts.extend(view_attempts)

        if view_method:
            diag["camera_mode_used"] = f"{effective_view}_view"
            diag["camera_method_used"] = view_method
            diag["view_direction_used"] = f"preset:{effective_view}"
            successful_steps += 1
        else:
            # Rung 3: explicit camera position/target along ±z axis
            dir_method, dir_str = _attempt_camera_direction(camera, effective_view, camera_errors)
            if dir_method:
                all_cam_attempts.append(dir_method)
                diag["camera_mode_used"] = f"{effective_view}_view_camera_dir"
                diag["camera_method_used"] = dir_method
                diag["view_direction_used"] = dir_str
                successful_steps += 1
            elif effective_view != "front":
                # Fallback to front/default view — triggers WARN
                fallback_method, fallback_attempts = _attempt_named_view(
                    solver, views, "front", camera_errors
                )
                all_cam_attempts.extend(fallback_attempts)
                if fallback_method:
                    diag["camera_mode_used"] = "fallback_front_default_view"
                    diag["camera_method_used"] = fallback_method
                    diag["view_direction_used"] = "preset:front"
                    successful_steps += 1
                    errors.append(
                        f"membrane-normal view ({effective_view!r}) unavailable; "
                        "fell back to front/default_view"
                    )
                else:
                    errors.extend(camera_errors)
                    camera_errors = []
                    errors.append(f"all view attempts failed (preset={view_preset!r})")
            else:
                errors.extend(camera_errors)
                camera_errors = []

        diag["camera_method_attempts"] = all_cam_attempts
        diag["camera_errors"] = camera_errors

        projection_method = _try_scene_call(
            "orthographic projection",
            [
                (
                    "settings.views.camera.projection(type='orthographic')",
                    lambda: camera.projection(type="orthographic"),
                ),
                (
                    "settings.views.camera.projection('orthographic')",
                    lambda: camera.projection("orthographic"),
                ),
                (
                    "settings.views.camera.projection(type='parallel')",
                    lambda: camera.projection(type="parallel"),
                ),
                (
                    "tui.display.views.camera.projection('orthographic')",
                    lambda: solver.tui.display.views.camera.projection("orthographic"),
                ),
            ],
            errors,
        )
        if projection_method:
            diag["projection_mode_used"] = "orthographic"
            successful_steps += 1

        up_vector_method = _try_scene_call(
            "camera up vector",
            [
                (
                    "settings.views.camera.up_vector(xyz=[0, 1, 0])",
                    lambda: camera.up_vector(xyz=[0.0, 1.0, 0.0]),
                ),
                (
                    "settings.views.camera.up_vector([0, 1, 0])",
                    lambda: camera.up_vector([0.0, 1.0, 0.0]),
                ),
            ],
            errors,
        )
        if up_vector_method:
            diag["view_up_vector_used"] = "0,1,0"

        if _try_scene_call(
            "view auto-scale",
            [
                ("settings.views.auto_scale()", lambda: views.auto_scale()),
                ("tui.display.views.auto_scale()", lambda: solver.tui.display.views.auto_scale()),
            ],
            errors,
        ):
            diag["view_fit_applied"] = True
            successful_steps += 1

        if view_margin > 1.0:
            zoom_factor = 1.0 / float(view_margin)
            if _try_scene_call(
                "view margin zoom",
                [
                    (
                        "settings.views.camera.zoom(factor=...)",
                        lambda: camera.zoom(factor=zoom_factor),
                    ),
                    (
                        "settings.views.camera.zoom(...)",
                        lambda: camera.zoom(zoom_factor),
                    ),
                    (
                        "tui.display.views.camera.zoom_camera(...)",
                        lambda: solver.tui.display.views.camera.zoom_camera(zoom_factor),
                    ),
                ],
                errors,
            ):
                successful_steps += 1
    except Exception as exc:
        errors.append(f"camera/view cleanup: {_format_exception(exc)}")

    if successful_steps > 0 and errors:
        diag["scene_cleanup_status"] = "WARN"
    elif successful_steps > 0:
        diag["scene_cleanup_status"] = "SUCCESS"
    else:
        diag["scene_cleanup_status"] = "FAILED"
    all_errors = errors + non_critical_errors
    diag["scene_cleanup_error"] = " | ".join(all_errors)
    return diag


def try_native_cff_export(
    solver: Any,
    cff_name: str,
    membrane_zones: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
    background: str,
    view_margin: float,
    view_preset: str = "auto",
    membrane_side: str = "top",
) -> Tuple[bool, str, str, str, Dict[str, Any]]:
    """Attempt native Fluent contour export using a prepared CFF variable.

    Returns (success, native_status, used_var, error_msg, scene_cleanup_diag).
    success=True means the output_file was written.
    """
    scene_diag = _default_scene_cleanup_diag(background, view_margin)
    try:
        scene_diag = _export_contour_cff(
            solver, cff_name, membrane_zones, shear_range, output_file,
            image_width, image_height, background, view_margin,
            view_preset=view_preset, membrane_side=membrane_side,
        )
        if output_file.is_file():
            scene_error = str(scene_diag.get("scene_cleanup_error", ""))
            return True, "SUCCESS", cff_name, scene_error, scene_diag
        return (
            False, "FAILED", cff_name,
            "File not written after native contour export",
            scene_diag,
        )
    except Exception as exc:
        return False, "FAILED", cff_name, _format_exception(exc), scene_diag


def _export_contour_cff(
    solver: Any,
    cff_name: str,
    membrane_zones: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
    background: str,
    view_margin: float,
    view_preset: str = "auto",
    membrane_side: str = "top",
) -> Dict[str, Any]:
    """Create Fluent contour using a shear-rate CFF, display, and save."""
    if output_file.exists():
        output_file.unlink()

    graphics = solver.settings.results.graphics

    try:
        existing = list_named_object_names(graphics.contour, "graphics.contour")
        if CONTOUR_NAME in existing:
            graphics.contour[CONTOUR_NAME].delete()
    except Exception:
        pass

    contour = graphics.contour.create(CONTOUR_NAME)
    contour.field = cff_name
    contour.surfaces_list = list(membrane_zones)

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
    scene_diag = setup_fluent_clean_scene(
        solver=solver,
        background=background,
        view_margin=view_margin,
        view_preset=view_preset,
        membrane_side=membrane_side,
    )
    if scene_diag.get("scene_cleanup_status") == "FAILED":
        print(f"  WARN: scene cleanup failed: {scene_diag.get('scene_cleanup_error', '')}")
    elif scene_diag.get("scene_cleanup_status") == "WARN":
        print(f"  WARN: scene cleanup partially applied: "
              f"{scene_diag.get('scene_cleanup_error', '')}")

    pic = solver.settings.results.graphics.picture
    pic.x_resolution = image_width
    pic.y_resolution = image_height

    output_posix = as_fluent_path(output_file)
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
    print(f"  CFF contour image saved: {output_file}")
    return scene_diag


# ---------------------------------------------------------------------------
# Field-data fallback (matplotlib)
# ---------------------------------------------------------------------------

def _find_valid_scalar_field(
    solver: Any,
    scalar_candidates: List[str],
    surface: str,
) -> Optional[str]:
    """Return the first scalar field name that successfully returns data."""
    # Build candidates: confirmed from field_info first, then hardcoded list
    ordered: List[str] = list(scalar_candidates)
    for c in SHEAR_FIELD_CANDIDATES:
        if c not in ordered:
            ordered.append(c)

    for name in ordered:
        print(f"  Trying scalar field: {name!r}")
        try:
            data = solver.fields.field_data.get_scalar_field_data(
                field_name=name,
                surfaces=[surface],
                node_value=False,
                boundary_value=True,
            )
            if data:
                sizes = {k: np.asarray(v).size for k, v in data.items()}
                print(f"  Field {name!r} OK: {sizes}")
                return name
        except Exception as exc:
            print(f"  Field {name!r} failed: {exc}")
    return None


def try_field_data_fallback(
    solver: Any,
    mu: float,
    membrane_zones: List[str],
    scalar_candidates: List[str],
    shear_range: Optional[Tuple[float, float]],
    output_file: Path,
    image_width: int,
    image_height: int,
    background: str,
) -> Tuple[bool, str, str, str]:
    """Retrieve wall-shear field data, compute shear_rate=wall_shear/mu, save PNG.

    Returns (success, fallback_status, used_var, error_msg).
    """
    from ansys.fluent.core.field_data_interfaces import SurfaceDataType

    if output_file.exists():
        output_file.unlink()

    # Use the first (or only) surface — if --membrane-surface top/bottom
    surface = membrane_zones[0]

    # Confirm which scalar field name works
    found_field = _find_valid_scalar_field(solver, scalar_candidates, surface)
    if found_field is None:
        msg = "No valid wall-shear scalar field found in field_data"
        print(f"  FALLBACK: {msg}")
        return False, "FAILED", "", msg

    # Gather scalar data from all selected zones
    all_values_list: List[np.ndarray] = []
    all_coords_list: List[np.ndarray] = []

    for zone in membrane_zones:
        print(f"  Fetching scalar data for zone: {zone}")
        try:
            scalar_data = solver.fields.field_data.get_scalar_field_data(
                field_name=found_field,
                surfaces=[zone],
                node_value=False,
                boundary_value=True,
            )
        except Exception as exc:
            print(f"  WARN: get_scalar_field_data on {zone}: {exc}")
            continue

        print(f"  Fetching centroid coordinates for zone: {zone}")
        try:
            surf_data = solver.fields.field_data.get_surface_data(
                data_types=[SurfaceDataType.FacesCentroid],
                surfaces=[zone],
            )
        except Exception as exc:
            print(f"  WARN: get_surface_data on {zone}: {exc}")
            continue

        # Concatenate data from all surface_id entries
        for sid, arr in scalar_data.items():
            vals = np.asarray(arr).ravel()
            all_values_list.append(vals)

        for sid, type_dict in surf_data.items():
            centroids = np.asarray(type_dict[SurfaceDataType.FacesCentroid])
            if centroids.ndim == 1:
                centroids = centroids.reshape(-1, 3)
            all_coords_list.append(centroids)

    if not all_values_list or not all_coords_list:
        msg = "No data retrieved from field_data for any membrane zone"
        return False, "FAILED", found_field, msg

    all_values = np.concatenate(all_values_list)
    all_coords = np.concatenate(all_coords_list, axis=0)

    # Sanity: lengths must match (face-center values vs. face centroids)
    min_len = min(len(all_values), len(all_coords))
    if len(all_values) != len(all_coords):
        print(
            f"  WARN: value count ({len(all_values)}) != centroid count ({len(all_coords)}); "
            f"truncating to {min_len}"
        )
        all_values = all_values[:min_len]
        all_coords = all_coords[:min_len]

    # Compute shear rate [1/s] = wall_shear [Pa] / mu [Pa·s]
    shear_rate = all_values / mu
    print(
        f"  shear_rate stats: min={shear_rate.min():.3f}, "
        f"max={shear_rate.max():.3f}, mean={shear_rate.mean():.3f} [1/s]"
    )

    # Auto-detect in-plane axes (largest spread → x_plot, y_plot)
    spreads = [np.ptp(all_coords[:, i]) for i in range(3)]
    sorted_axes = sorted(range(3), key=lambda i: spreads[i], reverse=True)
    ax1, ax2 = sorted_axes[0], sorted_axes[1]
    ax_labels = ["x [m]", "y [m]", "z [m]"]
    x_plot = all_coords[:, ax1]
    y_plot = all_coords[:, ax2]

    # Colorbar range
    vmin = float(shear_range[0]) if shear_range is not None else float(shear_rate.min())
    vmax = float(shear_range[1]) if shear_range is not None else float(shear_rate.max())

    # Attempt tripcolor (triangulation from face centroids); fall back to scatter
    fig_w = image_width / 100.0
    fig_h = image_height / 100.0
    bg_color = "white" if background == "white" else "black"
    fg_color = "black" if background == "white" else "white"

    fig, ax_mpl = plt.subplots(figsize=(fig_w, fig_h), facecolor=bg_color)
    ax_mpl.set_facecolor(bg_color)

    try:
        triang = mtri.Triangulation(x_plot, y_plot)
        tcf = ax_mpl.tripcolor(
            triang, shear_rate, shading="flat", cmap="rainbow", vmin=vmin, vmax=vmax
        )
        plot_type = "tripcolor"
    except Exception as exc_tri:
        print(f"  tripcolor failed ({exc_tri}); using scatter")
        point_size = max(0.1, 72 * min(spreads[ax1], spreads[ax2]) / len(x_plot) ** 0.5)
        tcf = ax_mpl.scatter(
            x_plot, y_plot, c=shear_rate, cmap="rainbow",
            s=point_size, vmin=vmin, vmax=vmax, linewidths=0
        )
        plot_type = "scatter"

    cbar = fig.colorbar(tcf, ax=ax_mpl)
    cbar.set_label("Wall shear rate [1/s]", color=fg_color)
    cbar.ax.yaxis.set_tick_params(color=fg_color)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=fg_color)

    ax_mpl.set_xlabel(ax_labels[ax1], color=fg_color)
    ax_mpl.set_ylabel(ax_labels[ax2], color=fg_color)
    ax_mpl.tick_params(colors=fg_color)
    for spine in ax_mpl.spines.values():
        spine.set_edgecolor(fg_color)

    zone_label = ", ".join(membrane_zones)
    ax_mpl.set_title(
        f"Wall shear rate: wall-shear / mu,  mu = {mu:.6e} Pa·s\n"
        f"Zones: {zone_label}  |  {plot_type}",
        color=fg_color,
    )

    _safe_mkdir(output_file.parent)
    fig.savefig(str(output_file), dpi=100, bbox_inches="tight", facecolor=bg_color)
    plt.close(fig)

    if not output_file.is_file():
        return False, "FAILED", found_field, "matplotlib savefig produced no file"

    print(f"  Fallback PNG saved: {output_file}")
    return True, "SUCCESS", found_field, ""


# ---------------------------------------------------------------------------
# Status JSON
# ---------------------------------------------------------------------------

STATUS_OK   = "SUCCESS"
STATUS_FAIL = "FAILED"
STATUS_DRY  = "DRY_RUN"
STATUS_SKIP = "SKIPPED_EXISTING"
STATUS_WARN = "WARN"


def write_status_json(status_file: Path, payload: Dict[str, Any]) -> None:
    _safe_mkdir(status_file.parent)
    with status_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"Status written: {status_file}")


def build_status_payload(
    geo_name: str,
    case_name: str,
    status: str,
    selected_surfaces: List[str],
    mu_used: float,
    output_files: List[Path],
    message: str,
    selected_variable: Optional[str],
    selected_variable_for_field_data: Optional[str],
    selected_cff_cell_function: str,
    selected_wall_shear_source: str,
    derived_variable_mode: str,
    native_attempted: bool,
    native_status: str,
    native_error: str,
    native_variable_used: Optional[str],
    cff_name: str,
    cff_file: Optional[Path],
    cff_file_load_attempted: bool,
    cff_file_load_status: str,
    cff_file_load_error: str,
    cff_direct_create_attempted: bool,
    cff_direct_create_status: str,
    cff_direct_create_errors: List[str],
    cff_expression_attempts: List[Dict[str, str]],
    fallback_attempted: bool,
    fallback_status: str,
    fallback_error: str,
    shear_related_field_candidates: List[str],
    shear_related_cff_candidates: List[str],
    inferred_cff_cell_function_candidates: List[str],
    cff_candidate_source: str,
    background: str,
    view_margin: float,
    image_width: int,
    image_height: int,
    all_scalar_field_names_head: Optional[List[str]] = None,
    settings_cff_attempted: bool = False,
    settings_cff_status: str = "SKIPPED",
    settings_cff_error: str = "",
    settings_cff_available_attrs: Optional[List[str]] = None,
    settings_cff_state_head: str = "",
    tui_direct_attempted: bool = False,
    tui_direct_status: str = "SKIPPED",
    tui_direct_errors: Optional[List[str]] = None,
    tui_journal_style_attempted: bool = False,
    tui_journal_style_status: str = "SKIPPED",
    tui_journal_style_error: str = "",
    cff_creation_method_used: str = "",
    cff_expression: str = "",
    scene_cleanup_attempted: bool = False,
    scene_cleanup_status: str = "SKIPPED",
    scene_cleanup_error: str = "",
    camera_mode_used: str = "",
    projection_mode_used: str = "",
    floor_hidden: Optional[bool] = None,
    shadow_hidden: Optional[bool] = None,
    reflection_hidden: Optional[bool] = None,
    triad_hidden: Optional[bool] = None,
    logo_hidden: Optional[bool] = None,
    background_used: Optional[str] = None,
    view_margin_used: Optional[float] = None,
) -> Dict[str, Any]:
    cff_attempted = (
        cff_file_load_attempted
        or settings_cff_attempted
        or cff_direct_create_attempted
    )
    cff_status = (
        "SUCCESS"
        if (
            cff_file_load_status == "SUCCESS"
            or settings_cff_status == "SUCCESS"
            or cff_direct_create_status == "SUCCESS"
        )
        else "FAILED"
        if (
            cff_file_load_status == "FAILED"
            or settings_cff_status == "FAILED"
            or cff_direct_create_status == "FAILED"
        )
        else "SKIPPED"
    )
    cff_error = (
        cff_file_load_error
        or settings_cff_error
        or "; ".join(cff_direct_create_errors)
    )

    return {
        "geo_name": geo_name,
        "case_name": case_name,
        "field_key": "shear_rate",
        "status": status,
        "selected_variable": selected_variable,
        "selected_variable_for_field_data": selected_variable_for_field_data,
        "selected_cff_cell_function": selected_cff_cell_function,
        "selected_wall_shear_source": selected_wall_shear_source,
        "derived_variable_mode": derived_variable_mode,
        "formula_summary": "wall-shear / mu",
        "contour_target_type": "membrane_wall",
        "selected_surfaces": selected_surfaces,
        "mu_used": mu_used,
        "output_file": [str(f) for f in output_files] if len(output_files) != 1
                       else str(output_files[0]),
        "message": message,
        "native_attempted": native_attempted,
        "native_status": native_status,
        "native_error": native_error,
        "native_variable_used": native_variable_used,
        "cff_name": cff_name,
        "cff_file": "" if cff_file is None else str(cff_file),
        "cff_expression": cff_expression,
        "cff_creation_method_used": cff_creation_method_used,
        "cff_file_load_attempted": cff_file_load_attempted,
        "cff_file_load_status": cff_file_load_status,
        "cff_file_load_error": cff_file_load_error,
        "settings_cff_attempted": settings_cff_attempted,
        "settings_cff_status": settings_cff_status,
        "settings_cff_error": settings_cff_error,
        "settings_cff_available_attrs": settings_cff_available_attrs or [],
        "settings_cff_state_head": settings_cff_state_head,
        "tui_direct_attempted": tui_direct_attempted,
        "tui_direct_status": tui_direct_status,
        "tui_direct_errors": tui_direct_errors or [],
        "tui_journal_style_attempted": tui_journal_style_attempted,
        "tui_journal_style_status": tui_journal_style_status,
        "tui_journal_style_error": tui_journal_style_error,
        "cff_direct_create_attempted": cff_direct_create_attempted,
        "cff_direct_create_status": cff_direct_create_status,
        "cff_direct_create_errors": cff_direct_create_errors,
        "cff_expression_attempts": cff_expression_attempts,
        # Backward-compatible aliases for older status readers.
        "cff_attempted": cff_attempted,
        "cff_status": cff_status,
        "cff_error": cff_error,
        "fallback_attempted": fallback_attempted,
        "fallback_status": fallback_status,
        "fallback_error": fallback_error,
        "shear_related_field_candidates": shear_related_field_candidates,
        "shear_related_cff_candidates": shear_related_cff_candidates,
        "inferred_cff_cell_function_candidates": inferred_cff_cell_function_candidates,
        "cff_candidate_source": cff_candidate_source,
        "all_scalar_field_names_head": all_scalar_field_names_head or [],
        "background": background,
        "view_margin": view_margin,
        "scene_cleanup_attempted": scene_cleanup_attempted,
        "scene_cleanup_status": scene_cleanup_status,
        "scene_cleanup_error": scene_cleanup_error,
        "camera_mode_used": camera_mode_used,
        "projection_mode_used": projection_mode_used,
        "floor_hidden": floor_hidden,
        "shadow_hidden": shadow_hidden,
        "reflection_hidden": reflection_hidden,
        "triad_hidden": triad_hidden,
        "logo_hidden": logo_hidden,
        "background_used": background_used if background_used is not None else background,
        "view_margin_used": view_margin_used if view_margin_used is not None else view_margin,
        "image_width": image_width,
        "image_height": image_height,
    }


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
            "Formula: wall_shear_rate [1/s] = wall-shear [Pa] / mu [Pa·s]\n\n"
            "Width/height priority: --width beats --image-width; "
            "--height beats --image-height."
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
        "--cff-file", type=str, default=None,
        help=(
            "Optional Fluent Custom Field Function file saved on the server. "
            "The path is loaded after case/data are loaded; local WSL validation "
            "does not require this file to exist."
        ),
    )
    parser.add_argument(
        "--cff-name", type=str, default=DEFAULT_CFF_NAME,
        help=f"Fluent CFF name to use for native contouring (default: {DEFAULT_CFF_NAME}).",
    )
    parser.add_argument(
        "--print-cff-manual-steps", action="store_true",
        help="Print manual Fluent GUI CFF setup instructions and exit without launching Fluent.",
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
    # Legacy resolution options
    parser.add_argument("--image-width",  type=int, default=1920,
                        help="Image width (px). Overridden by --width if provided.")
    parser.add_argument("--image-height", type=int, default=1080,
                        help="Image height (px). Overridden by --height if provided.")
    # New resolution options (take priority over --image-width/--image-height)
    parser.add_argument("--width",  type=int, default=None,
                        help="Image width (px); if given, overrides --image-width.")
    parser.add_argument("--height", type=int, default=None,
                        help="Image height (px); if given, overrides --image-height.")
    # Visual options for native Fluent scene cleanup and matplotlib fallback
    parser.add_argument(
        "--background", type=str, default="white", choices=["white", "black"],
        help="Background colour (default: white). Applied to native scene and fallback.",
    )
    parser.add_argument(
        "--view-margin", type=float, default=1.20, metavar="FACTOR",
        help="View zoom margin factor (default: 1.20). Applied when supported.",
    )
    parser.add_argument(
        "--view-preset", type=str, default="auto",
        choices=["auto", "top", "bottom", "front", "iso"],
        help=(
            "Camera view preset for contour display (default: auto). "
            "auto: top view for --membrane-surface top, bottom for bottom. "
            "top/bottom: z-normal membrane view (looking down/up z axis). "
            "front: Fluent default front view (legacy/debug). "
            "iso: isometric view (debug)."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    if args.print_cff_manual_steps:
        print(_manual_cff_steps(args.cff_name, DEFAULT_MU))
        return 0

    # Resolve effective resolution
    eff_width  = args.width  if args.width  is not None else args.image_width
    eff_height = args.height if args.height is not None else args.image_height
    cff_file: Optional[Path] = as_path(args.cff_file) if args.cff_file else None

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

    shear_range: Optional[Tuple[float, float]] = args.shear_range

    # Determine which sides to export
    if args.membrane_surface == "both":
        sides = ["top", "bottom"]
    else:
        sides = [args.membrane_surface]

    # Build per-side output filenames (always include _top / _bottom suffix)
    output_files_by_side: Dict[str, Path] = {
        side: figures_dir / f"{geo_name}_{case_name}_shear_rate_membrane_{side}.png"
        for side in sides
    }
    status_file = figures_dir / "shear_contour_status.json"

    print(f"Geo          : {geo_name}")
    print(f"Case         : {case_name}")
    print(f"Case file    : {case_file}")
    for side, of in output_files_by_side.items():
        print(f"Output ({side:6s}): {of}")
    print(f"Status JSON  : {status_file}")
    print(f"mu           : {mu:.6e} Pa·s")
    print(f"Formula      : wall-shear / {mu:.6e}")
    print(f"CFF name     : {args.cff_name}")
    if cff_file is not None:
        print(f"CFF file     : {cff_file}")
    print(f"Membrane side: {args.membrane_surface}")
    print(f"Background   : {args.background}")
    print(f"View margin  : {args.view_margin}")
    print(f"Resolution   : {eff_width} x {eff_height} px")
    if shear_range:
        print(f"Shear range  : {shear_range[0]} – {shear_range[1]} [1/s]")

    # --- Dry run ---
    if args.dry_run:
        print("\nDRY RUN — no Fluent launch, no image written.")
        _safe_mkdir(figures_dir)
        payload = build_status_payload(
            geo_name=geo_name, case_name=case_name,
            status=STATUS_DRY, selected_surfaces=active_mem_bases,
            mu_used=mu, output_files=list(output_files_by_side.values()),
            message="dry-run only",
            selected_variable=None,
            selected_variable_for_field_data=None,
            selected_cff_cell_function="",
            selected_wall_shear_source="",
            derived_variable_mode="pyfluent_native_cff_wall_shear_over_mu",
            native_attempted=False, native_status="SKIPPED", native_error="",
            native_variable_used=None,
            cff_name=args.cff_name, cff_file=cff_file,
            cff_file_load_attempted=False,
            cff_file_load_status="SKIPPED", cff_file_load_error="",
            cff_direct_create_attempted=False,
            cff_direct_create_status="SKIPPED",
            cff_direct_create_errors=[],
            cff_expression_attempts=[],
            fallback_attempted=False, fallback_status="SKIPPED", fallback_error="",
            shear_related_field_candidates=[], shear_related_cff_candidates=[],
            inferred_cff_cell_function_candidates=[],
            cff_candidate_source="none",
            background=args.background, view_margin=args.view_margin,
            image_width=eff_width, image_height=eff_height,
            cff_expression=_default_cff_expression(mu),
        )
        write_status_json(status_file, payload)
        return 0

    # --- Skip existing ---
    if args.skip_existing:
        all_exist = all(f.is_file() for f in output_files_by_side.values())
        if all_exist:
            print(f"SKIP: all output files already exist.")
            payload = build_status_payload(
                geo_name=geo_name, case_name=case_name,
                status=STATUS_SKIP, selected_surfaces=[],
                mu_used=mu, output_files=list(output_files_by_side.values()),
                message="Skipped: all output files already exist.",
                selected_variable=None,
                selected_variable_for_field_data=None,
                selected_cff_cell_function="",
                selected_wall_shear_source="",
                derived_variable_mode="pyfluent_native_cff_wall_shear_over_mu",
                native_attempted=False, native_status="SKIPPED", native_error="",
                native_variable_used=None,
                cff_name=args.cff_name, cff_file=cff_file,
                cff_file_load_attempted=False,
                cff_file_load_status="SKIPPED", cff_file_load_error="",
                cff_direct_create_attempted=False,
                cff_direct_create_status="SKIPPED",
                cff_direct_create_errors=[],
                cff_expression_attempts=[],
                fallback_attempted=False, fallback_status="SKIPPED", fallback_error="",
                shear_related_field_candidates=[], shear_related_cff_candidates=[],
                inferred_cff_cell_function_candidates=[],
                cff_candidate_source="none",
                background=args.background, view_margin=args.view_margin,
                image_width=eff_width, image_height=eff_height,
                cff_expression=_default_cff_expression(mu),
            )
            write_status_json(status_file, payload)
            return 0

    # --- Validate input files (only when path is locally reachable) ---
    if not _has_windows_drive(case_file):
        if not case_file.is_file():
            print(f"ERROR: Case file not found: {case_file}")
            return 2
    if not _has_windows_drive(data_file):
        if not data_file.is_file():
            print(f"ERROR: Data file not found: {data_file}")
            return 2

    _safe_mkdir(figures_dir)
    pyfluent.config.check_health_timeout = health_timeout

    meshing = None
    solver  = None

    # Accumulate per-run state
    final_status       = STATUS_FAIL
    final_message      = "Not attempted."
    all_selected_surfs: List[str] = []
    all_output_files:   List[Path] = []
    used_variable:      Optional[str] = None
    selected_variable_for_field_data: Optional[str] = None
    selected_cff_cell_function = ""
    selected_wall_shear_source = ""
    successful_side_modes: List[str] = []
    derived_mode        = "pyfluent_native_cff_wall_shear_over_mu"
    native_attempted    = False
    native_status_str   = "SKIPPED"
    native_error_str    = ""
    native_variable_used: Optional[str] = None
    cff_file_load_attempted = False
    cff_file_load_status    = "SKIPPED"
    cff_file_load_error     = ""
    settings_cff_attempted  = False
    settings_cff_status     = "SKIPPED"
    settings_cff_error      = ""
    settings_cff_available_attrs: List[str] = []
    settings_cff_state_head = ""
    tui_direct_attempted    = False
    tui_direct_status       = "SKIPPED"
    tui_direct_errors: List[str] = []
    tui_journal_style_attempted = False
    tui_journal_style_status    = "SKIPPED"
    tui_journal_style_error     = ""
    cff_creation_method_used    = ""
    cff_expression              = _default_cff_expression(mu)
    cff_direct_create_attempted = False
    cff_direct_create_status    = "SKIPPED"
    cff_direct_create_errors: List[str] = []
    cff_expression_attempts: List[Dict[str, str]] = []
    fb_attempted        = False
    fb_status_str       = "SKIPPED"
    fb_error_str        = ""
    native_side_results: List[Tuple[bool, str, str]] = []
    fallback_side_results: List[Tuple[bool, str, str]] = []
    scalar_candidates:      List[str] = []
    cff_candidates:         List[str] = []
    inferred_cff_candidates: List[str] = []
    cff_candidate_source = "none"
    all_scalar_names_head:  List[str] = []
    scene_cleanup_diag = _default_scene_cleanup_diag(args.background, args.view_margin)

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
        print(f"\nReading: {as_fluent_path(case_file)}")
        solver.settings.file.read_case_data(file_name=as_fluent_path(case_file))
        print("Case loaded.")

        # --- Detect membrane wall zones ---
        all_wall_zones = collect_wall_zones(setup)
        all_membrane_zones = find_zones_by_base_names(all_wall_zones, active_mem_bases)
        print(f"Membrane zones found: {all_membrane_zones}")

        if not all_membrane_zones:
            raise RuntimeError(
                f"No membrane zones found for base names {active_mem_bases}. "
                f"Available wall zones (first 20): {all_wall_zones[:20]}"
            )

        # --- Diagnostics: detect shear-related candidates ---
        print("\nRunning field/CFF diagnostics ...")
        (
            scalar_candidates,
            cff_candidates,
            all_scalar_names_head,
            inferred_cff_candidates,
            cff_candidate_source,
        ) = detect_shear_candidates(solver)

        # --- Prepare native Fluent CFF for wall shear rate ---
        native_attempted = True
        print("\nPreparing native Fluent CFF for wall shear rate ...")
        cff_prep = prepare_native_cff(
            solver=solver,
            cff_name=args.cff_name,
            cff_file=cff_file,
            mu=mu,
        )
        native_variable_used = cff_prep["native_variable_used"] or None
        selected_cff_cell_function = str(cff_prep["selected_cff_cell_function"])
        cff_file_load_attempted = bool(cff_prep["cff_file_load_attempted"])
        cff_file_load_status = str(cff_prep["cff_file_load_status"])
        cff_file_load_error = str(cff_prep["cff_file_load_error"])
        settings_cff_attempted = bool(cff_prep["settings_cff_attempted"])
        settings_cff_status = str(cff_prep["settings_cff_status"])
        settings_cff_error = str(cff_prep["settings_cff_error"])
        settings_cff_available_attrs = list(cff_prep["settings_cff_available_attrs"])
        settings_cff_state_head = str(cff_prep["settings_cff_state_head"])
        tui_direct_attempted = bool(cff_prep["tui_direct_attempted"])
        tui_direct_status = str(cff_prep["tui_direct_status"])
        tui_direct_errors = list(cff_prep["tui_direct_errors"])
        tui_journal_style_attempted = bool(cff_prep["tui_journal_style_attempted"])
        tui_journal_style_status = str(cff_prep["tui_journal_style_status"])
        tui_journal_style_error = str(cff_prep["tui_journal_style_error"])
        cff_creation_method_used = str(cff_prep["cff_creation_method_used"])
        cff_expression = str(cff_prep["cff_expression"])
        cff_direct_create_attempted = bool(cff_prep["cff_direct_create_attempted"])
        cff_direct_create_status = str(cff_prep["cff_direct_create_status"])
        cff_direct_create_errors = list(cff_prep["cff_direct_create_errors"])
        cff_expression_attempts = list(cff_prep["cff_expression_attempts"])
        native_ready = bool(cff_prep["ready"])
        if native_ready:
            print(f"  Native CFF ready: {args.cff_name}")
        else:
            native_status_str = "FAILED"
            native_error_str = str(cff_prep["error"] or "CFF preparation failed")
            print(f"  Native CFF not ready: {native_error_str}")

        # --- Process each side ---
        side_results: List[Tuple[bool, str, str]] = []  # (success, side, output_path)

        for side in sides:
            print(f"\n{'='*60}")
            print(f"Processing membrane side: {side}")
            print(f"{'='*60}")

            output_file = output_files_by_side[side]

            # Filter zones for this side
            if side == "top":
                flt = [z for z in all_membrane_zones if "top" in z.lower()]
                membrane_zones = flt
            elif side == "bottom":
                flt = [z for z in all_membrane_zones if "bot" in z.lower()]
                membrane_zones = flt
            else:
                membrane_zones = list(all_membrane_zones)

            print(f"  Selected surfaces: {membrane_zones}")
            if not membrane_zones:
                msg = (
                    f"No {side} membrane wall zones found. "
                    f"Available active membrane zones: {all_membrane_zones}"
                )
                print(f"  FAILED: {msg}")
                if native_attempted:
                    native_side_results.append((False, side, msg))
                side_results.append((False, side, str(output_file)))
                continue
            all_selected_surfs.extend(membrane_zones)

            side_ok      = False
            side_var     = None
            side_mode    = ""

            # A. Try native Fluent CFF contour export first.
            if native_ready:
                print(f"\n  [A] Attempting native Fluent CFF contour export ...")
                native_ok, nat_st, nat_var, nat_err, scene_diag = try_native_cff_export(
                    solver=solver,
                    cff_name=args.cff_name,
                    membrane_zones=membrane_zones,
                    shear_range=shear_range,
                    output_file=output_file,
                    image_width=eff_width,
                    image_height=eff_height,
                    background=args.background,
                    view_margin=args.view_margin,
                    view_preset=args.view_preset,
                    membrane_side=side,
                )
                scene_cleanup_diag = _merge_scene_cleanup_diag(
                    scene_cleanup_diag,
                    scene_diag,
                )
                native_side_results.append((native_ok, side, nat_err))

                if native_ok:
                    side_ok   = True
                    side_var  = nat_var
                    side_mode = "pyfluent_native_cff_wall_shear_over_mu"
                    print(f"  [A] Native CFF SUCCESS: {output_file.name}")
                else:
                    print(f"  [A] Native CFF FAILED: {nat_err}")
            else:
                native_side_results.append((False, side, native_error_str))
                print(f"\n  [A] Native CFF skipped: {native_error_str}")

            if not side_ok:
                # B. Try field-data fallback.
                print(f"\n  [B] Attempting field-data fallback (matplotlib) ...")
                fb_attempted = True
                fb_ok, fb_st, fb_var, fb_err = try_field_data_fallback(
                    solver=solver,
                    mu=mu,
                    membrane_zones=membrane_zones,
                    scalar_candidates=scalar_candidates,
                    shear_range=shear_range,
                    output_file=output_file,
                    image_width=eff_width,
                    image_height=eff_height,
                    background=args.background,
                )
                fallback_side_results.append((fb_ok, side, fb_err))

                if fb_ok:
                    side_ok   = True
                    side_var  = fb_var
                    if selected_variable_for_field_data is None:
                        selected_variable_for_field_data = fb_var
                    side_mode = "pyfluent_field_data_wall_shear_over_mu"
                    print(f"  [B] Fallback SUCCESS: {output_file.name}")
                else:
                    print(f"  [B] Fallback FAILED: {fb_err}")

            side_results.append((side_ok, side, str(output_file)))
            if side_ok:
                all_output_files.append(output_file)
                if used_variable is None:
                    used_variable = side_var
                if side_mode:
                    successful_side_modes.append(side_mode)

        if native_attempted and native_side_results:
            native_ok_count = sum(1 for ok, _, _ in native_side_results if ok)
            if native_ok_count == len(native_side_results):
                native_status_str = "SUCCESS"
            elif native_ok_count > 0:
                native_status_str = "WARN"
            else:
                native_status_str = "FAILED"
            native_errors = [
                f"{side}: {err}" for ok, side, err in native_side_results
                if not ok and err
            ]
            native_error_str = "; ".join(native_errors)

        if fb_attempted and fallback_side_results:
            fb_ok_count = sum(1 for ok, _, _ in fallback_side_results if ok)
            if fb_ok_count == len(fallback_side_results):
                fb_status_str = "SUCCESS"
            elif fb_ok_count > 0:
                fb_status_str = "WARN"
            else:
                fb_status_str = "FAILED"
            fb_errors = [
                f"{side}: {err}" for ok, side, err in fallback_side_results
                if not ok and err
            ]
            fb_error_str = "; ".join(fb_errors)

        if successful_side_modes:
            unique_modes = sorted(set(successful_side_modes))
            if len(unique_modes) == 1:
                derived_mode = unique_modes[0]
            else:
                derived_mode = "mixed_native_cff_and_field_data_wall_shear_over_mu"

        if derived_mode == "pyfluent_native_cff_wall_shear_over_mu":
            selected_wall_shear_source = selected_cff_cell_function
        elif derived_mode == "pyfluent_field_data_wall_shear_over_mu":
            selected_wall_shear_source = selected_variable_for_field_data or ""
        elif derived_mode == "mixed_native_cff_and_field_data_wall_shear_over_mu":
            selected_wall_shear_source = (
                f"native:{selected_cff_cell_function}; "
                f"fallback:{selected_variable_for_field_data or ''}"
            )

        # --- Overall status ---
        any_ok  = any(ok for ok, _, _ in side_results)
        all_ok  = all(ok for ok, _, _ in side_results)

        if all_ok:
            final_status  = STATUS_OK
            final_message = (
                f"wall-shear / mu={mu:.6e}: "
                + ", ".join(f"{s}→{Path(p).name}" for _, s, p in side_results)
            )
        elif any_ok:
            final_status  = STATUS_WARN
            failed_sides  = [s for ok, s, _ in side_results if not ok]
            final_message = f"Partial success; failed sides: {failed_sides}"
        else:
            final_status  = STATUS_FAIL
            final_message = "Both CFF and fallback failed for all sides."

        print(f"\nOverall: {final_status} — {final_message}")

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

    payload = build_status_payload(
        geo_name=geo_name,
        case_name=case_name,
        status=final_status,
        selected_surfaces=sorted(set(all_selected_surfs)),
        mu_used=mu,
        output_files=all_output_files if all_output_files else list(output_files_by_side.values()),
        message=final_message,
        selected_variable=used_variable,
        selected_variable_for_field_data=selected_variable_for_field_data,
        selected_cff_cell_function=selected_cff_cell_function,
        selected_wall_shear_source=selected_wall_shear_source,
        derived_variable_mode=derived_mode,
        native_attempted=native_attempted,
        native_status=native_status_str,
        native_error=native_error_str,
        native_variable_used=native_variable_used,
        cff_name=args.cff_name,
        cff_file=cff_file,
        cff_file_load_attempted=cff_file_load_attempted,
        cff_file_load_status=cff_file_load_status,
        cff_file_load_error=cff_file_load_error,
        settings_cff_attempted=settings_cff_attempted,
        settings_cff_status=settings_cff_status,
        settings_cff_error=settings_cff_error,
        settings_cff_available_attrs=settings_cff_available_attrs,
        settings_cff_state_head=settings_cff_state_head,
        tui_direct_attempted=tui_direct_attempted,
        tui_direct_status=tui_direct_status,
        tui_direct_errors=tui_direct_errors,
        tui_journal_style_attempted=tui_journal_style_attempted,
        tui_journal_style_status=tui_journal_style_status,
        tui_journal_style_error=tui_journal_style_error,
        cff_creation_method_used=cff_creation_method_used,
        cff_expression=cff_expression,
        cff_direct_create_attempted=cff_direct_create_attempted,
        cff_direct_create_status=cff_direct_create_status,
        cff_direct_create_errors=cff_direct_create_errors,
        cff_expression_attempts=cff_expression_attempts,
        fallback_attempted=fb_attempted,
        fallback_status=fb_status_str,
        fallback_error=fb_error_str,
        shear_related_field_candidates=scalar_candidates,
        shear_related_cff_candidates=cff_candidates,
        inferred_cff_cell_function_candidates=inferred_cff_candidates,
        cff_candidate_source=cff_candidate_source,
        all_scalar_field_names_head=all_scalar_names_head,
        background=args.background,
        view_margin=args.view_margin,
        scene_cleanup_attempted=bool(scene_cleanup_diag.get("scene_cleanup_attempted", False)),
        scene_cleanup_status=str(scene_cleanup_diag.get("scene_cleanup_status", "SKIPPED")),
        scene_cleanup_error=str(scene_cleanup_diag.get("scene_cleanup_error", "")),
        camera_mode_used=str(scene_cleanup_diag.get("camera_mode_used", "")),
        projection_mode_used=str(scene_cleanup_diag.get("projection_mode_used", "")),
        floor_hidden=scene_cleanup_diag.get("floor_hidden"),
        shadow_hidden=scene_cleanup_diag.get("shadow_hidden"),
        reflection_hidden=scene_cleanup_diag.get("reflection_hidden"),
        triad_hidden=scene_cleanup_diag.get("triad_hidden"),
        logo_hidden=scene_cleanup_diag.get("logo_hidden"),
        background_used=scene_cleanup_diag.get("background_used"),
        view_margin_used=scene_cleanup_diag.get("view_margin_used"),
        image_width=eff_width,
        image_height=eff_height,
    )
    payload.update({
        "requested_view_preset": str(scene_cleanup_diag.get("requested_view_preset", "")),
        "camera_method_used": str(scene_cleanup_diag.get("camera_method_used", "")),
        "camera_method_attempts": list(scene_cleanup_diag.get("camera_method_attempts", [])),
        "camera_errors": list(scene_cleanup_diag.get("camera_errors", [])),
        "view_direction_used": str(scene_cleanup_diag.get("view_direction_used", "")),
        "view_up_vector_used": str(scene_cleanup_diag.get("view_up_vector_used", "")),
        "view_fit_applied": bool(scene_cleanup_diag.get("view_fit_applied", False)),
        "cli_view_preset_arg": str(args.view_preset),
    })
    write_status_json(status_file, payload)

    return 0 if final_status in (STATUS_OK, STATUS_WARN) else 2


if __name__ == "__main__":
    raise SystemExit(main())
