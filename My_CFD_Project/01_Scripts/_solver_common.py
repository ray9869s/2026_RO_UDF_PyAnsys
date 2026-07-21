"""Pure shared primitives for solver batch drivers and workers.

Phase 1 (07 consolidation): path helpers only. No PyFluent imports.
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
