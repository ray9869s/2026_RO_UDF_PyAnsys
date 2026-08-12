"""Lock Python UDM indices to the enum in 260810_RO_UDF.c.

Parses the C enum rather than duplicating numbers, so Python and UDF cannot
silently drift after a renumber.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _udm_layout import (
    EXPECTED_UDM_FIELDS,
    FIELD_UDM_CELL_STRAIN_RATE,
    FIELD_UDM_CM,
    FIELD_UDM_CP,
    FIELD_UDM_CP_INLET,
    FIELD_UDM_JW,
    FIELD_UDM_LMH,
    FIELD_UDM_MEMBRANE_AREA_ACC,
    FIELD_UDM_SALT_FLUX,
    FIELD_UDM_SI,
    FIELD_UDM_TOTAL_S,
    UDM_AREA,
    UDM_CM,
    UDM_COUNT,
    UDM_CP,
    UDM_INDEX_BY_ROLE,
    UDM_JW,
    UDM_LMH,
    UDM_SALT_FLUX,
    UDM_SI,
    UDM_STRAIN_RATE,
    UDM_TOTAL_S,
    UDM_XMOM,
    UDM_YMOM,
    UDM_ZMOM,
)
from helpers import POST_DIR, REPO_ROOT, SCRIPTS_DIR, load_post_config

UDF_PATH = REPO_ROOT / "My_CFD_Project" / "02_UDFs" / "260810_RO_UDF.c"

# Symbols the default dual build must expose (CELL diagnostics ON).
REQUIRED_C_SYMBOLS = {
    "UDM_SI": 0,
    "UDM_TOTAL_S": 1,
    "UDM_XMOM": 2,
    "UDM_YMOM": 3,
    "UDM_ZMOM": 4,
    "UDM_STRAIN_RATE": 5,
    "UDM_JW": 6,
    "UDM_CM": 7,
    "UDM_LMH": 8,
    "UDM_CP": 9,
    "UDM_SALT_FLUX": 10,
    "UDM_AREA": 11,
    "UDM_COUNT": 12,
}


def parse_udm_enum_from_c(source: str) -> dict[str, int]:
    """Parse `enum { NAME = N, ... }` UDM block from 260810_RO_UDF.c.

    Evaluates only the default dual-on branch: keeps lines under
    `#if RO_UDM_CELL_DIAGNOSTICS` and drops the `#else` ... `#endif` arm so
    UDM_AREA / UDM_COUNT=12 are visible.
    """
    enum_match = re.search(
        r"enum\s*\{(?P<body>.*?)\n\};",
        source,
        flags=re.DOTALL,
    )
    if enum_match is None:
        raise AssertionError("Could not find UDM enum { ... }; in 260810_RO_UDF.c")

    body = enum_match.group("body")
    # Keep CELL-diagnostics branch; drop the #else arm (COUNT=11).
    body = re.sub(
        r"#else.*?#endif",
        "",
        body,
        flags=re.DOTALL,
    )
    body = re.sub(r"#if[^\n]*\n", "", body)
    body = re.sub(r"#endif[^\n]*\n?", "", body)

    parsed: dict[str, int] = {}
    for match in re.finditer(
        r"\b(UDM_[A-Z0-9_]+)\s*=\s*(\d+)\b",
        body,
    ):
        parsed[match.group(1)] = int(match.group(2))
    if not parsed:
        raise AssertionError("UDM enum body contained no NAME = N entries")
    return parsed


@pytest.fixture(scope="module")
def c_udm_enum() -> dict[str, int]:
    text = UDF_PATH.read_text(encoding="utf-8")
    return parse_udm_enum_from_c(text)


def test_c_enum_matches_required_default_dual_layout(c_udm_enum):
    assert c_udm_enum == REQUIRED_C_SYMBOLS


def test_python_udm_layout_matches_c_enum(c_udm_enum):
    python_side = {
        "UDM_SI": UDM_SI,
        "UDM_TOTAL_S": UDM_TOTAL_S,
        "UDM_XMOM": UDM_XMOM,
        "UDM_YMOM": UDM_YMOM,
        "UDM_ZMOM": UDM_ZMOM,
        "UDM_STRAIN_RATE": UDM_STRAIN_RATE,
        "UDM_JW": UDM_JW,
        "UDM_CM": UDM_CM,
        "UDM_LMH": UDM_LMH,
        "UDM_CP": UDM_CP,
        "UDM_SALT_FLUX": UDM_SALT_FLUX,
        "UDM_AREA": UDM_AREA,
        "UDM_COUNT": UDM_COUNT,
    }
    assert python_side == c_udm_enum


def test_field_strings_use_parsed_indices(c_udm_enum):
    assert FIELD_UDM_SI == f"udm-{c_udm_enum['UDM_SI']}"
    assert FIELD_UDM_TOTAL_S == f"udm-{c_udm_enum['UDM_TOTAL_S']}"
    assert FIELD_UDM_JW == f"udm-{c_udm_enum['UDM_JW']}"
    assert FIELD_UDM_CM == f"udm-{c_udm_enum['UDM_CM']}"
    assert FIELD_UDM_LMH == f"udm-{c_udm_enum['UDM_LMH']}"
    assert FIELD_UDM_CP == f"udm-{c_udm_enum['UDM_CP']}"
    assert FIELD_UDM_CP_INLET == FIELD_UDM_CP
    assert FIELD_UDM_CELL_STRAIN_RATE == f"udm-{c_udm_enum['UDM_STRAIN_RATE']}"
    assert FIELD_UDM_SALT_FLUX == f"udm-{c_udm_enum['UDM_SALT_FLUX']}"
    assert FIELD_UDM_MEMBRANE_AREA_ACC == f"udm-{c_udm_enum['UDM_AREA']}"


def test_post_config_udm_indices_match_layout(c_udm_enum):
    post_cfg = load_post_config()
    assert post_cfg.udm_indices == UDM_INDEX_BY_ROLE
    assert post_cfg.udm_indices["salt_mass_source"] == c_udm_enum["UDM_SI"]
    assert post_cfg.udm_indices["total_mass_source"] == c_udm_enum["UDM_TOTAL_S"]
    assert post_cfg.udm_indices["cell_strain_rate"] == c_udm_enum["UDM_STRAIN_RATE"]
    assert post_cfg.udm_indices["salt_mass_flux"] == c_udm_enum["UDM_SALT_FLUX"]
    assert post_cfg.udm_indices["cp"] == c_udm_enum["UDM_CP"]
    assert "water_mass_source" not in post_cfg.udm_indices
    assert "cp_inlet" not in post_cfg.udm_indices


def test_expected_udm_fields_cover_valid_range_only():
    for field_name in EXPECTED_UDM_FIELDS:
        match = re.fullmatch(r"udm-(\d+)", field_name)
        assert match is not None, field_name
        index = int(match.group(1))
        assert 0 <= index < UDM_COUNT, field_name


def test_no_legacy_udm_12_in_active_post_scripts():
    """udm-12 is out of range when UDM_COUNT=12 (valid 0..11)."""
    active_roots = [
        SCRIPTS_DIR / "post_processing" / "01_pyfluent_report_extract.py",
        SCRIPTS_DIR / "post_processing" / "02_pyfluent_field_check.py",
        SCRIPTS_DIR / "post_processing" / "03_pyensight_contour_export.py",
        SCRIPTS_DIR / "post_processing" / "03b_pyfluent_shear_contour_export.py",
        SCRIPTS_DIR / "_udm_layout.py",
        SCRIPTS_DIR / "_fluent_report_helpers.py",
    ]
    banned = re.compile(r"udm-12|UDM_12|User Defined Memory 12")
    for path in active_roots:
        text = path.read_text(encoding="utf-8")
        # Allow comments that say the fallback was removed / out of range.
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "out of range" in line or "do not fall back" in line or "was 12" in line:
                continue
            if "was udm-12" in line or "was UDF-12" in line:
                continue
            assert banned.search(line) is None, f"{path}:{line_no}: {line}"
