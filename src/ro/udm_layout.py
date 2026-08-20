"""Canonical UDM indices matching 260813_RO_UDF.c (default dual build).

Keep this module in lockstep with the C enum. tests/test_udm_layout_parity.py
parses the .c enum and fails if these integers drift.

Live post-processing of a *case* must not use EXPECTED_UDM_FIELDS: udm-N
meanings change across dated UDFs. Use expected_udm_fields_from_case().
"""

from __future__ import annotations

import re
from pathlib import Path

# Indices — must match 260813_RO_UDF.c with RO_UDM_CELL_DIAGNOSTICS=1.
# 0–10 are unchanged from 260810. UDM_Y1 is appended; UDM_COUNT is 13.
UDM_SI = 0
UDM_TOTAL_S = 1
UDM_XMOM = 2
UDM_YMOM = 3
UDM_ZMOM = 4
UDM_STRAIN_RATE = 5
UDM_JW = 6
UDM_CM = 7
UDM_LMH = 8
UDM_CP = 9
UDM_SALT_FLUX = 10
UDM_AREA = 11
UDM_Y1 = 12
UDM_COUNT = 13

# Fluent field-name strings used by surface/volume reports.
FIELD_UDM_SI = f"udm-{UDM_SI}"
FIELD_UDM_TOTAL_S = f"udm-{UDM_TOTAL_S}"
FIELD_UDM_JW = f"udm-{UDM_JW}"
FIELD_UDM_CM = f"udm-{UDM_CM}"
FIELD_UDM_LMH = f"udm-{UDM_LMH}"
FIELD_UDM_CP = f"udm-{UDM_CP}"
# Alias kept while CSV / report keys still say cp_inlet.
FIELD_UDM_CP_INLET = FIELD_UDM_CP
FIELD_UDM_CELL_STRAIN_RATE = f"udm-{UDM_STRAIN_RATE}"
FIELD_UDM_MEMBRANE_AREA_ACC = f"udm-{UDM_AREA}"
FIELD_UDM_SALT_FLUX = f"udm-{UDM_SALT_FLUX}"
FIELD_UDM_Y1 = f"udm-{UDM_Y1}"

# Human-readable map for field-check / post_config.
UDM_INDEX_BY_ROLE = {
    "salt_mass_source": UDM_SI,
    "total_mass_source": UDM_TOTAL_S,
    "jw": UDM_JW,
    "cm": UDM_CM,
    "lmh": UDM_LMH,
    "cp": UDM_CP,
    "cell_strain_rate": UDM_STRAIN_RATE,
    "membrane_area_acc": UDM_AREA,
    "salt_mass_flux": UDM_SALT_FLUX,
    "wall_centroid_distance": UDM_Y1,
}

EXPECTED_UDM_FIELDS = {
    FIELD_UDM_SI: "salt_mass_source",
    FIELD_UDM_TOTAL_S: "total_mass_source",
    FIELD_UDM_CELL_STRAIN_RATE: "cell_strain_rate",
    FIELD_UDM_JW: "Jw",
    FIELD_UDM_CM: "Cm",
    FIELD_UDM_LMH: "LMH",
    FIELD_UDM_CP: "CP",
    FIELD_UDM_SALT_FLUX: "salt_mass_flux",
    FIELD_UDM_MEMBRANE_AREA_ACC: "membrane_area_acc",
    FIELD_UDM_Y1: "wall_centroid_distance",
}

# Case-local UDF copies are named <date>_RO_UDF.c (solver patches into the
# case folder). Field-check must parse THAT copy: udm-12 is salt_flux in
# 260612, out of range in 260810, and y1 in 260813.
_CASE_UDF_GLOB = "*_RO_UDF.c"

# Descriptions for field-check. Momentum coefficients are allocated but are
# not exposed as named post fields the way the diagnostic slots are.
_UDM_FIELD_DESCRIPTIONS = {
    "UDM_SI": "salt_mass_source",
    "UDM_SM": "water_mass_source",
    "UDM_TOTAL_S": "total_mass_source",
    "UDM_STRAIN_RATE": "cell_strain_rate",
    "UDM_CELL_STRAIN_RATE": "cell_strain_rate",
    "UDM_JW": "Jw",
    "UDM_CM": "Cm",
    "UDM_LMH": "LMH",
    "UDM_CP": "CP",
    "UDM_CP_INLET": "CP",
    "UDM_SALT_FLUX": "salt_mass_flux",
    "UDM_AREA": "membrane_area_acc",
    "UDM_Y1": "wall_centroid_distance",
}
_UDM_FIELD_SKIP = frozenset(
    {"UDM_COUNT", "UDM_XMOM", "UDM_YMOM", "UDM_ZMOM"}
)


def parse_udm_enum_from_c(source: str) -> dict[str, int]:
    """Parse `enum { NAME = N, ... }` UDM block from an RO UDF.

    Evaluates the default dual-on branch: keeps lines under
    `#if RO_UDM_CELL_DIAGNOSTICS` and drops any `#else` ... `#endif` arm so
    UDM_AREA is visible. Symbols outside that switch (UDM_Y1, UDM_COUNT)
    stay as written.
    """
    enum_match = re.search(
        r"enum\s*\{(?P<body>.*?)\n\};",
        source,
        flags=re.DOTALL,
    )
    if enum_match is None:
        raise ValueError("Could not find UDM enum { ... }; in UDF source")

    body = enum_match.group("body")
    body = re.sub(r"#else.*?#endif", "", body, flags=re.DOTALL)
    body = re.sub(r"#if[^\n]*\n", "", body)
    body = re.sub(r"#endif[^\n]*\n?", "", body)

    parsed: dict[str, int] = {}
    for match in re.finditer(r"\b(UDM_[A-Z0-9_]+)\s*=\s*(\d+)\b", body):
        parsed[match.group(1)] = int(match.group(2))
    if not parsed:
        raise ValueError("UDM enum body contained no NAME = N entries")
    return parsed


def expected_udm_fields_from_enum(parsed: dict[str, int]) -> dict[str, str]:
    """Map a parsed UDM enum to Fluent field names (`udm-N` → description)."""
    if "UDM_COUNT" not in parsed:
        raise ValueError(f"UDM enum has no UDM_COUNT: {sorted(parsed)}")
    count = parsed["UDM_COUNT"]
    fields: dict[str, str] = {}
    for name, index in parsed.items():
        if name in _UDM_FIELD_SKIP:
            continue
        if name not in _UDM_FIELD_DESCRIPTIONS:
            raise ValueError(
                f"Unmapped UDM symbol {name}={index}. "
                "Add it to _UDM_FIELD_DESCRIPTIONS or _UDM_FIELD_SKIP."
            )
        if not 0 <= index < count:
            raise ValueError(
                f"{name}={index} is outside 0..{count - 1} (UDM_COUNT={count})"
            )
        field_name = f"udm-{index}"
        description = _UDM_FIELD_DESCRIPTIONS[name]
        previous = fields.get(field_name)
        if previous is not None and previous != description:
            raise ValueError(
                f"{field_name} mapped twice: {previous!r} and {description!r}"
            )
        fields[field_name] = description
    return fields


def find_case_udf_path(case_dir) -> Path:
    """Return the single `*_RO_UDF.c` in a case folder.

    Raises if the copy is missing or if more than one dated UDF is present.
    Does not fall back to 02_UDFs/.
    """
    case_path = Path(case_dir)
    matches = sorted(
        path for path in case_path.glob(_CASE_UDF_GLOB) if path.is_file()
    )
    if not matches:
        raise FileNotFoundError(
            f"No case-local UDF ({_CASE_UDF_GLOB}) in {case_path}. "
            "Cannot derive the UDM field layout. The solver copies the UDF "
            "into the case folder at compile time; a missing copy means this "
            "case cannot be field-checked against its own layout."
        )
    if len(matches) > 1:
        names = [path.name for path in matches]
        raise ValueError(
            f"Multiple case-local UDFs in {case_path}: {names}. "
            "Cannot choose a UDM layout."
        )
    return matches[0]


def expected_udm_fields_from_case(case_dir) -> dict[str, str]:
    """Parse the case-local UDF and return the expected `udm-N` field set."""
    udf_path = find_case_udf_path(case_dir)
    source = udf_path.read_text(encoding="utf-8")
    return expected_udm_fields_from_enum(parse_udm_enum_from_c(source))
