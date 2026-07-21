"""Pure shared primitives for solver batch drivers and workers.

Phase 1 (07 consolidation): path helpers.
Phase 2: case naming helpers (batch wired; 07 selection unchanged).
No PyFluent imports.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def path_to_fluent_str(path: Any) -> str:
    """Convert a path to a Fluent-friendly absolute path.

    Semantics match solver_code_260616.as_fluent_path: os.path.abspath plus
    forward slashes. Accepts str or pathlib.Path.
    """
    return os.path.abspath(path).replace("\\", "/")


def normalize_path(path: Any) -> str:
    """Normalize a path for comparison (solver semantics)."""
    return os.path.normcase(os.path.abspath(path))


def final_case_data_paths(
    project_root: Any,
    geo_name: str,
    case_name: str,
) -> tuple[str, str, str]:
    """Return (case_dir, final_case_path, final_data_path) as strings.

    Layout matches batch_solver_sweep.py expected_final_case/expected_final_data
    construction (project_root/03_Results/<geo>/<case>/...).
    """
    case_dir = os.path.join(project_root, "03_Results", geo_name, case_name)
    final_case = os.path.join(case_dir, f"{geo_name}_{case_name}_final.cas.h5")
    final_data = final_case.replace(".cas.h5", ".dat.h5")
    return case_dir, final_case, final_data


def find_windows_drive_paths(paths: Iterable[Any]) -> list[str]:
    """Return inputs whose string form starts with a Windows drive letter."""
    unsafe: list[str] = []
    for path in paths:
        text = str(path)
        if WINDOWS_DRIVE_RE.match(text):
            unsafe.append(text)
    return unsafe


def reject_windows_drive_paths_on_non_windows(
    paths: Iterable[Any],
    *,
    host_os_name: str | None = None,
) -> None:
    """Refuse Windows drive paths on non-Windows hosts (WSL/repo safety).

    Not wired into solver/batch in phase 1; provided for future shared use.
    """
    if (host_os_name if host_os_name is not None else os.name) == "nt":
        return

    unsafe = find_windows_drive_paths(paths)
    if not unsafe:
        return

    raise ValueError(
        "Windows drive paths are not accepted on this non-Windows host because "
        "they can create C: folders inside the WSL repo. Run live/server paths "
        f"from Windows Python instead. Unsafe path(s): {unsafe}"
    )


# ---------------------------------------------------------------------------
# naming — case token build/resolve (batch_solver_sweep semantics)
# ---------------------------------------------------------------------------

MATRIX_BASE_CASE_RE = re.compile(r"^u\d+p\d+_p\d+M$")


def velocity_to_case_token(u) -> str:
    # 0.1 -> "u0p1"
    return f"u0p{int(round(float(u) * 10))}"


def pressure_to_case_token(p) -> str:
    # 4.0e6 -> "p4M"
    return f"p{int(round(float(p) / 1.0e6))}M"


def make_base_case_name(u, p) -> str:
    # (0.1, 4.0e6) -> "u0p1_p4M"
    return f"{velocity_to_case_token(u)}_{pressure_to_case_token(p)}"


def make_mesh_qualified_case_name(base_case_name, mesh_case_name) -> str:
    if mesh_case_name:
        return f"{base_case_name}__{mesh_case_name}"
    return base_case_name


def resolve_case_names(case_dict) -> tuple[Any, str]:
    """Return (base_case_name, case_name) for a solver sweep entry.

    Priority:
      1. Explicit "case_name" is used as-is (legacy behavior).
      2. Explicit "base_case_name" is mesh-qualified with mesh_case_name.
      3. inlet_velocity_value + outlet_gauge_pressure + mesh_case_name derive both.
      4. Otherwise "case_name" is required, as before.
    """
    base_case_name = case_dict.get("base_case_name")
    mesh_case_name = case_dict.get("mesh_case_name")
    explicit_case_name = case_dict.get("case_name")

    if explicit_case_name:
        return base_case_name, explicit_case_name

    if base_case_name and mesh_case_name:
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    u = case_dict.get("inlet_velocity_value")
    p = case_dict.get("outlet_gauge_pressure")
    if u is not None and p is not None and mesh_case_name:
        base_case_name = make_base_case_name(u, p)
        return base_case_name, make_mesh_qualified_case_name(base_case_name, mesh_case_name)

    # Legacy behavior: an explicit case_name is required when it cannot be derived.
    return base_case_name, case_dict["case_name"]


def strip_mesh_suffix(case_name: str) -> tuple[str, str | None]:
    """Split a mesh-qualified case name into (base_case_name, mesh_suffix).

    Staged for F-05; not wired into selection logic in phase 2.
    """
    if "__" not in case_name:
        return case_name, None
    base, mesh = case_name.split("__", 1)
    return base, mesh


def is_matrix_base_case_name(case_name: str) -> bool:
    """True when case_name matches the plain matrix token (no mesh suffix).

    Staged for F-05; not wired into 07 select_candidates in phase 2.
    """
    return bool(MATRIX_BASE_CASE_RE.match(case_name))
