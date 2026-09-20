"""Parse membrane transport constants from an RO UDF C source.

There is no single campaign source for A, B, kappa, p_perm, MW, rho, and
c_0. The compiled solver uses the case-local UDF copy. post_config carries
the overlapping subset (rho, B, c_0, MW) used by extract. This module
parses the UDF as the reconstruction source and asserts post_config against
it at figure render time.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

from ro.udm_layout import find_case_udf_path

# Keys stored after parse. Identifiers match the UDF token, lowercased.
UDF_MEMBRANE_CONSTANT_KEYS = (
    "a_perm",
    "b_perm",
    "kappa",
    "p_perm",
    "mw_salt",
    "rho_ref",
    "c_inlet_ref",
    "ms_to_lmh",
)

_STATIC_REAL_RE = re.compile(
    r"static\s+real\s+(?P<name>A_perm|B_perm|kappa|p_perm)\s*=\s*(?P<value>[^;]+);",
)
_DEFINE_RE = re.compile(
    r"#define\s+(?P<name>MW_SALT|C_INLET_REF|RHO_REF|MS_TO_LMH)\s+"
    r"(?P<value>[^\s/]+)",
)

_STATIC_TO_KEY = {
    "A_perm": "a_perm",
    "B_perm": "b_perm",
    "kappa": "kappa",
    "p_perm": "p_perm",
}
_DEFINE_TO_KEY = {
    "MW_SALT": "mw_salt",
    "C_INLET_REF": "c_inlet_ref",
    "RHO_REF": "rho_ref",
    "MS_TO_LMH": "ms_to_lmh",
}

# post_config attribute -> UDF parse key. Only the overlapping subset.
_POST_CONFIG_TO_UDF = {
    "rho": "rho_ref",
    "salt_permeability_m_per_s": "b_perm",
    "c_inlet_ref": "c_inlet_ref",
    "salt_molecular_weight_kg_per_mol": "mw_salt",
}

_CONST_REL_TOL = 1.0e-9
_CONST_ABS_TOL = 0.0


def parse_udf_membrane_constants(source: str) -> dict[str, float]:
    """Return membrane constants from UDF C source. Raises if any key is missing."""
    parsed: dict[str, float] = {}
    for match in _STATIC_REAL_RE.finditer(source):
        key = _STATIC_TO_KEY[match.group("name")]
        parsed[key] = float(match.group("value").strip())
    for match in _DEFINE_RE.finditer(source):
        key = _DEFINE_TO_KEY[match.group("name")]
        parsed[key] = float(match.group("value").strip())
    missing = [key for key in UDF_MEMBRANE_CONSTANT_KEYS if key not in parsed]
    if missing:
        raise ValueError(
            "UDF membrane constants missing from source: "
            + ", ".join(missing)
        )
    return {key: parsed[key] for key in UDF_MEMBRANE_CONSTANT_KEYS}


def load_udf_membrane_constants_from_case(case_dir) -> dict[str, float]:
    """Parse the case-local ``*_RO_UDF.c``. Does not fall back to ``udfs/``."""
    udf_path = find_case_udf_path(case_dir)
    source = Path(udf_path).read_text(encoding="utf-8")
    return parse_udf_membrane_constants(source)


def assert_post_config_matches_udf(
    cfg: Any,
    udf_constants: Mapping[str, float],
) -> None:
    """Fail if post_config overlapping constants disagree with the UDF."""
    mismatches: list[str] = []
    for attr, udf_key in _POST_CONFIG_TO_UDF.items():
        if isinstance(cfg, Mapping):
            raw = cfg.get(attr)
        else:
            raw = getattr(cfg, attr, None)
        if raw is None:
            mismatches.append(
                f"{attr} missing from post_config (UDF {udf_key}="
                f"{udf_constants[udf_key]!r})"
            )
            continue
        cfg_value = float(raw)
        udf_value = float(udf_constants[udf_key])
        if not math.isclose(
            cfg_value,
            udf_value,
            rel_tol=_CONST_REL_TOL,
            abs_tol=_CONST_ABS_TOL,
        ):
            mismatches.append(
                f"{attr}={cfg_value!r} disagrees with UDF {udf_key}="
                f"{udf_value!r}"
            )
    if mismatches:
        raise ValueError(
            "post_config constants disagree with the case-local UDF: "
            + "; ".join(mismatches)
        )
