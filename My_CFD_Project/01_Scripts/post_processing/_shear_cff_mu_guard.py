# -*- coding: utf-8 -*-
"""Observe-only guard: compare native CFF .scm wall-shear divisor to config mu.

Log-only; never raises into the caller, never mutates status JSON schemas.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional, Union

MU_MISMATCH_REL_TOL = 1e-4

# Primary: (code (field-/ (field-load "wall-shear") 0.000893))
_PRIMARY_DIVISOR_RE = re.compile(
    r'field-/\s*\(\s*field-load\s+"wall-shear"\s*\)\s*'
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
)
# Fallback: (syntax-tree ("/" "wall-shear" 0.000893))
_FALLBACK_DIVISOR_RE = re.compile(
    r'\(\s*"/"\s+"wall-shear"\s+'
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*\)"
)


def parse_scm_wall_shear_divisor(scm_path: Union[str, Path]) -> Optional[float]:
    """Return the unique wall-shear divisor from a Fluent CFF .scm, or None.

    Never raises. OSError / decode errors / zero or conflicting divisors -> None.
    """
    try:
        text = Path(scm_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return None

    values: list[float] = []
    for pattern in (_PRIMARY_DIVISOR_RE, _FALLBACK_DIVISOR_RE):
        for match in pattern.finditer(text):
            try:
                values.append(float(match.group(1)))
            except ValueError:
                return None

    if not values:
        return None
    first = values[0]
    if any(v != first for v in values[1:]):
        return None
    return first


def emit_scm_mu_guard_message(
    cff_file: Any,
    cfg_mu: float,
    *,
    case_label: str = "",
    log: Callable[..., None] = print,
) -> None:
    """Log one WARNING if .scm divisor diverges from config mu; otherwise quiet.

    Side-effect (log) only. Never raises; unexpected errors become INFO.
    """
    try:
        if cff_file is None or cff_file == "":
            return

        scm_mu = parse_scm_wall_shear_divisor(cff_file)
        if scm_mu is None:
            log(
                "INFO: CFF mu guard could not parse; skipping"
                f" (file={cff_file}, case={case_label})"
            )
            return

        rel_err = abs(scm_mu - cfg_mu) / cfg_mu
        if rel_err > MU_MISMATCH_REL_TOL:
            log(
                "WARNING: CFF .scm wall-shear divisor diverges from config mu: "
                f"scm={scm_mu!r}, cfg_mu={cfg_mu!r}, "
                f"file={cff_file}, case={case_label}"
            )
    except Exception as err:
        log(f"INFO: CFF mu guard skipped: {err}")
