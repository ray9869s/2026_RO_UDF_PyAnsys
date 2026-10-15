"""Fidelity-table levels for the 3D pillar loop.

Importing this module does not launch Fluent. A level is either mesh
settings alone (the existing LF/HF tables) or mesh settings plus an
explicit domain and extract profile. Solver and convergence-gate entries
are optional. Omitted ones stay the production values and are not written
onto the solver override dict.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Mapping
from typing import Any

from ro.convergence_quality import (
    CONTINUITY_FINAL_MAX,
    LMH_REL_ABS_MAX,
    MASS_BALANCE_REL_ABS_MAX,
)
from ro.extract_profile import PROFILES
from ro.geometry_registry import DEFAULT_PILLAR_N_ACTIVE
from ro.hf_monitor_stop import require_hf_monitor_stop
from ro.lf_monitor_stop import GATE_LF_MONITOR, require_lf_monitor_stop
from ro.paths import RUN_ID_RE, project_root
from ro.solver_common import operating_point_token_from_run_id

WINDOW_SOURCE_FIDELITY_TABLE = "fidelity_table"
WINDOW_SOURCE_FAMILY_DEFAULT = "family_default"
SOURCE_PRODUCTION = "production"
SOURCE_FIDELITY_TABLE = "fidelity_table"

_MESH_KEYS = ("m_max", "m_min", "m_cpg", "bl_layers", "peel_layers")
_REQUIRED_EXTENDED = ("n_active", "n_lead_excluded", "extract_profile")
_SOLVER_KEYS = (
    "max_iterations",
    "residual_target",
    "turbulence_residual_target",
    "qoi_stop_criterion",
    "qoi_previous_values_to_consider",
    "qoi_initial_values_to_ignore",
    "lf_monitor_stop",
    "hf_monitor_stop",
)
_SOLVER_INTS = frozenset(
    {
        "max_iterations",
        "qoi_previous_values_to_consider",
        "qoi_initial_values_to_ignore",
    }
)
_GATE_KEYS = (
    "mode",
    "lmh_rel_abs_max",
    "mass_balance_rel_abs_max",
    "continuity_final_max",
)
_PRODUCTION_GATE_KEYS = (
    "lmh_rel_abs_max",
    "mass_balance_rel_abs_max",
    "continuity_final_max",
)
_RUN_ID_SUFFIX_RE = re.compile(r"^[a-z][a-z0-9]*$")
_EXTENDED_MARKERS = (
    *_REQUIRED_EXTENDED,
    "window_source",
    "run_id_suffix",
    "solver_overrides",
    "convergence_gate",
)
_LEVEL_KEYS = frozenset(
    {
        *_MESH_KEYS,
        "spacer_bl_layers",
        *_REQUIRED_EXTENDED,
        "window_source",
        "run_id_suffix",
        "solver_overrides",
        "convergence_gate",
    }
)

_SOLVER_FLAGS = (
    ("max_iterations", "--max-iterations"),
    ("residual_target", "--residual-target"),
    ("turbulence_residual_target", "--turbulence-residual-target"),
    ("qoi_stop_criterion", "--qoi-stop-criterion"),
    ("qoi_previous_values_to_consider", "--qoi-previous-values-to-consider"),
    ("qoi_initial_values_to_ignore", "--qoi-initial-values-to-ignore"),
)
_GATE_FLAGS = (
    ("lmh_rel_abs_max", "--lmh-rel-abs-max"),
    ("mass_balance_rel_abs_max", "--mass-balance-rel-abs-max"),
    ("continuity_final_max", "--continuity-final-max"),
)


def production_solver_convergence() -> dict[str, Any]:
    """Production QoI and residual settings from ``configs/run_config.py``.

    ``turbulence_residual_target`` has no production value on the laminar
    default. It is recorded only when a fidelity level sets it.
    """
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location("mfbo_fidelity_run_config", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load run config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        "max_iterations": int(module.max_iterations),
        "residual_target": float(module.residual_target),
        "qoi_stop_criterion": float(module.qoi_stop_criterion),
        "qoi_previous_values_to_consider": int(module.qoi_previous_values_to_consider),
        "qoi_initial_values_to_ignore": int(module.qoi_initial_values_to_ignore),
    }


def production_gate_thresholds() -> dict[str, float]:
    """Thresholds ``evaluate_convergence_quality`` uses when none are passed."""
    return {
        "lmh_rel_abs_max": float(LMH_REL_ABS_MAX),
        "mass_balance_rel_abs_max": float(MASS_BALANCE_REL_ABS_MAX),
        "continuity_final_max": float(CONTINUITY_FINAL_MAX),
    }


def parse_fidelity_level(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one fidelity object.

    Mesh-only entries stay mesh settings. Any domain, extract, solver, or
    gate key requires ``n_active`` and ``extract_profile``.
    ``n_lead_excluded`` is required unless ``window_source`` is
    ``family_default``, which leaves the pillar family window in place.
    """
    if not isinstance(entry, Mapping):
        raise TypeError(
            "A fidelity entry must be a mapping of mesh settings, "
            f"got {type(entry).__name__}."
        )
    unknown = sorted(set(entry) - _LEVEL_KEYS)
    if unknown:
        raise ValueError(f"fidelity level has unknown keys: {unknown}.")
    mesh = _mesh_fields(entry)
    extended = any(key in entry for key in _EXTENDED_MARKERS)
    if not extended:
        mesh["extended"] = False
        return mesh
    window_source = _window_source(entry)
    if window_source == WINDOW_SOURCE_FAMILY_DEFAULT:
        if "n_lead_excluded" in entry:
            raise ValueError(
                "window_source family_default does not take n_lead_excluded. "
                "The pillar family default supplies the lead."
            )
        missing = [
            key for key in ("n_active", "extract_profile") if key not in entry
        ]
    else:
        missing = [key for key in _REQUIRED_EXTENDED if key not in entry]
    if missing:
        raise KeyError(
            "A fidelity level that sets a domain, extract profile, solver "
            f"override, or gate threshold is missing {missing}."
        )
    n_active = _positive_int("n_active", entry["n_active"])
    if window_source == WINDOW_SOURCE_FAMILY_DEFAULT:
        n_lead = None
    else:
        n_lead = _nonnegative_int("n_lead_excluded", entry["n_lead_excluded"])
        if n_lead >= n_active:
            raise ValueError(
                f"n_lead_excluded={n_lead} must be < n_active={n_active}."
            )
    profile = entry["extract_profile"]
    if profile not in PROFILES:
        raise ValueError(
            f"extract_profile must be one of {sorted(PROFILES)}, got {profile!r}."
        )
    solver_specified = _optional_group(
        entry,
        "solver_overrides",
        _SOLVER_KEYS,
        _solver_value,
    )
    gate_specified = _optional_gate(entry)
    _require_gate_matches_stop(solver_specified, gate_specified)
    suffix = _run_id_suffix(entry)
    mesh.update(
        {
            "extended": True,
            "n_active": n_active,
            "n_lead_excluded": n_lead,
            "window_source": window_source,
            "extract_profile": profile,
            "run_id_suffix": suffix,
            "solver_overrides": solver_specified,
            "convergence_gate": gate_specified,
        }
    )
    return mesh


def fidelity_settings_used(level: Mapping[str, Any]) -> dict[str, Any] | None:
    """Values this extended level will run with. Mesh-only levels return None."""
    if not level.get("extended"):
        return None
    solver_used = {}
    for key, value in production_solver_convergence().items():
        if key in level["solver_overrides"]:
            solver_used[key] = {
                "value": level["solver_overrides"][key],
                "source": SOURCE_FIDELITY_TABLE,
            }
        else:
            solver_used[key] = {"value": value, "source": SOURCE_PRODUCTION}
    if "turbulence_residual_target" in level["solver_overrides"]:
        solver_used["turbulence_residual_target"] = {
            "value": level["solver_overrides"]["turbulence_residual_target"],
            "source": SOURCE_FIDELITY_TABLE,
        }
    if "lf_monitor_stop" in level["solver_overrides"]:
        solver_used["lf_monitor_stop"] = {
            "value": dict(level["solver_overrides"]["lf_monitor_stop"]),
            "source": SOURCE_FIDELITY_TABLE,
        }
    if "hf_monitor_stop" in level["solver_overrides"]:
        solver_used["hf_monitor_stop"] = {
            "value": dict(level["solver_overrides"]["hf_monitor_stop"]),
            "source": SOURCE_FIDELITY_TABLE,
        }
    gate_used = _gate_settings_used(level)
    mesh = {key: level[key] for key in _MESH_KEYS}
    if "spacer_bl_layers" in level:
        mesh["spacer_bl_layers"] = level["spacer_bl_layers"]
    return {
        "n_active": level["n_active"],
        "n_lead_excluded": level["n_lead_excluded"],
        "extract_profile": level["extract_profile"],
        "window_source": level.get("window_source", WINDOW_SOURCE_FIDELITY_TABLE),
        "mesh": mesh,
        "solver": solver_used,
        "convergence_gate": gate_used,
    }


def extraction_cli_args(level: Mapping[str, Any]) -> list[str]:
    """Flags for the extract worker. Empty for a mesh-only level."""
    if not level.get("extended"):
        return []
    args = ["--profile", level["extract_profile"]]
    if level.get("window_source") == WINDOW_SOURCE_FAMILY_DEFAULT:
        args.extend(["--window-source", WINDOW_SOURCE_FAMILY_DEFAULT])
    else:
        args.extend(
            ["--fidelity-n-lead-excluded", str(level["n_lead_excluded"])]
        )
    gate = level["convergence_gate"]
    if gate.get("mode") == GATE_LF_MONITOR:
        spec = level["solver_overrides"]["lf_monitor_stop"]
        args.extend(
            [
                "--convergence-gate",
                GATE_LF_MONITOR,
                "--lf-tol-lmh",
                _cli_number(spec["tol_lmh"]),
                "--lf-tol-dp",
                _cli_number(spec["tol_dp"]),
                "--lf-monitor-n",
                str(spec["n"]),
            ]
        )
        return args
    for key, flag in _GATE_FLAGS:
        if key in gate:
            args.extend([flag, _cli_number(gate[key])])
    return args


def solver_cli_args(level: Mapping[str, Any]) -> list[str]:
    """Flags for solve_mfbo_case. Empty when the level sets no solver override."""
    if not level.get("extended"):
        return []
    args: list[str] = []
    for key, flag in _SOLVER_FLAGS:
        if key in level["solver_overrides"]:
            value = level["solver_overrides"][key]
            text = str(value) if key in _SOLVER_INTS else _cli_number(value)
            args.extend([flag, text])
    if "lf_monitor_stop" in level["solver_overrides"]:
        args.extend(
            [
                "--lf-monitor-stop",
                json.dumps(
                    level["solver_overrides"]["lf_monitor_stop"],
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ]
        )
    if "hf_monitor_stop" in level["solver_overrides"]:
        args.extend(
            [
                "--hf-monitor-stop",
                json.dumps(
                    level["solver_overrides"]["hf_monitor_stop"],
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ]
        )
    return args


def run_id_for_level(run_id: str, level: Mapping[str, Any]) -> str:
    """Operating-point run id, plus this level's suffix when it has one.

    A level without ``run_id_suffix`` returns ``run_id`` unchanged.
    """
    suffix = level.get("run_id_suffix")
    if not suffix:
        return run_id
    token = operating_point_token_from_run_id(run_id)
    if run_id != token:
        raise ValueError(
            f"run_id {run_id!r} already has a suffix; "
            f"refusing to append {suffix!r}."
        )
    leaf = f"{token}_{suffix}"
    if RUN_ID_RE.fullmatch(leaf) is None:
        raise ValueError(f"suffixed run id is not a run id: {leaf!r}.")
    return leaf


def _mesh_fields(entry: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in _MESH_KEYS if key not in entry]
    if missing:
        raise KeyError(f"fidelity mesh settings missing {missing}.")
    checked = {
        "m_max": _positive_float("m_max", entry["m_max"]),
        "m_min": _positive_float("m_min", entry["m_min"]),
        "m_cpg": _positive_int("m_cpg", entry["m_cpg"]),
        "bl_layers": _positive_int("bl_layers", entry["bl_layers"]),
        "peel_layers": _positive_int("peel_layers", entry["peel_layers"]),
    }
    if "spacer_bl_layers" in entry and entry["spacer_bl_layers"] is not None:
        checked["spacer_bl_layers"] = _positive_int(
            "spacer_bl_layers",
            entry["spacer_bl_layers"],
        )
    return checked


def _optional_group(entry, name, allowed, convert):
    if name not in entry:
        return {}
    raw = entry[name]
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(f"{name} must be a non-empty object, got {raw!r}.")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f"{name} has unknown keys: {unknown}.")
    return {key: convert(key, raw[key]) for key in raw}


def _solver_value(name, value):
    if name == "lf_monitor_stop":
        return require_lf_monitor_stop(value)
    if name == "hf_monitor_stop":
        return require_hf_monitor_stop(value)
    if name in _SOLVER_INTS:
        return _positive_int(name, value)
    return _positive_float(name, value)


def _optional_gate(entry):
    if "convergence_gate" not in entry:
        return {}
    raw = entry["convergence_gate"]
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(
            f"convergence_gate must be a non-empty object, got {raw!r}."
        )
    unknown = sorted(set(raw) - set(_GATE_KEYS))
    if unknown:
        raise ValueError(f"convergence_gate has unknown keys: {unknown}.")
    if raw.get("mode") == GATE_LF_MONITOR:
        extra = sorted(set(raw) - {"mode"})
        if extra:
            raise ValueError(
                "convergence_gate mode lf_monitor does not take production "
                f"thresholds, got {extra}."
            )
        return {"mode": GATE_LF_MONITOR}
    if "mode" in raw:
        raise ValueError(
            f"convergence_gate mode must be {GATE_LF_MONITOR!r}, got {raw['mode']!r}."
        )
    return {
        key: _positive_float(key, raw[key])
        for key in raw
    }


def _window_source(entry: Mapping[str, Any]) -> str:
    if "window_source" not in entry:
        return WINDOW_SOURCE_FIDELITY_TABLE
    source = entry["window_source"]
    if source not in (WINDOW_SOURCE_FIDELITY_TABLE, WINDOW_SOURCE_FAMILY_DEFAULT):
        raise ValueError(
            "window_source must be "
            f"{WINDOW_SOURCE_FIDELITY_TABLE!r} or "
            f"{WINDOW_SOURCE_FAMILY_DEFAULT!r}, got {source!r}."
        )
    return source


def _require_gate_matches_stop(solver, gate):
    if "lf_monitor_stop" in solver and "hf_monitor_stop" in solver:
        raise ValueError(
            "lf_monitor_stop and hf_monitor_stop cannot both be set."
        )
    if "hf_monitor_stop" in solver and gate.get("mode") == GATE_LF_MONITOR:
        raise ValueError(
            "hf_monitor_stop keeps the production convergence gate."
        )
    if gate.get("mode") != GATE_LF_MONITOR:
        if "lf_monitor_stop" in solver:
            raise ValueError(
                "lf_monitor_stop requires convergence_gate mode "
                f"{GATE_LF_MONITOR!r}."
            )
        return
    if "lf_monitor_stop" not in solver:
        raise ValueError(
            "convergence_gate mode lf_monitor requires "
            "solver_overrides.lf_monitor_stop."
        )


def _run_id_suffix(entry):
    if "run_id_suffix" not in entry:
        return None
    suffix = entry["run_id_suffix"]
    if not isinstance(suffix, str) or _RUN_ID_SUFFIX_RE.fullmatch(suffix) is None:
        raise ValueError(
            "run_id_suffix must be a letter-led token, got "
            f"{suffix!r}."
        )
    return suffix


def _gate_settings_used(level):
    gate = level["convergence_gate"]
    if gate.get("mode") == GATE_LF_MONITOR:
        spec = level["solver_overrides"]["lf_monitor_stop"]
        return {
            "mode": {"value": GATE_LF_MONITOR, "source": SOURCE_FIDELITY_TABLE},
            "tol_lmh": {"value": spec["tol_lmh"], "source": SOURCE_FIDELITY_TABLE},
            "tol_dp": {"value": spec["tol_dp"], "source": SOURCE_FIDELITY_TABLE},
            "n": {"value": spec["n"], "source": SOURCE_FIDELITY_TABLE},
        }
    used = {}
    production = production_gate_thresholds()
    for key in _PRODUCTION_GATE_KEYS:
        if key in gate:
            used[key] = {"value": gate[key], "source": SOURCE_FIDELITY_TABLE}
        else:
            used[key] = {"value": production[key], "source": SOURCE_PRODUCTION}
    return used


def _positive_int(name, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value!r}.")
    return value


def _nonnegative_int(name, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}.")
    return value


def _positive_float(name, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if number <= 0.0 or number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite and positive, got {value!r}.")
    return number


def _cli_number(value: float) -> str:
    return format(float(value), ".12g")


def default_domain_n_active() -> int:
    """The unsuffixed MFP domain. Present so callers do not hard-code a second 7."""
    return DEFAULT_PILLAR_N_ACTIVE
