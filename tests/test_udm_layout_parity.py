"""Lock Python UDM indices to the enum in 260822_RO_UDF.c.

Parses the C enum rather than duplicating numbers, so Python and UDF cannot
silently drift after a renumber.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from ro.udm_layout import (
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
    FIELD_UDM_Y1,
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
    UDM_Y1,
    UDM_YMOM,
    UDM_ZMOM,
    expected_udm_fields_from_case,
    expected_udm_fields_from_enum,
    find_case_udf_path,
    parse_udm_enum_from_c,
)
from helpers import REPO_ROOT, SCRIPTS_DIR, load_post_config, load_run_config

UDF_DIR = REPO_ROOT / "udfs"
UDF_PATH = UDF_DIR / "260822_RO_UDF.c"
UDF_260822_PATH = UDF_PATH
UDF_260816_PATH = UDF_DIR / "260816_RO_UDF.c"
UDF_260815_PATH = UDF_DIR / "260815_RO_UDF.c"
UDF_260814_PATH = UDF_DIR / "260814_RO_UDF.c"
UDF_260813_PATH = UDF_DIR / "260813_RO_UDF.c"
UDF_260810_PATH = UDF_DIR / "260810_RO_UDF.c"
UDF_260612_PATH = UDF_DIR / "260612_RO_UDF.c"

# Frozen regression references — do not edit those files in place.
MD5_260810 = "c88b7bbcc8f573a932f09e48445d66f5"
MD5_260612 = "078e66d8f3fb8b9398d267037c450c98"

# Frozen dated files retain their exact historical UTF-8 comment characters.
LEGACY_NON_ASCII_CHARACTERS = {
    "260810_RO_UDF.c": ((723, "—"),),
    "260813_RO_UDF.c": ((555, "·"),),
    "260814_RO_UDF.c": ((555, "·"),),
    "260815_RO_UDF.c": ((143, "—"), (633, "·")),
}

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
    "UDM_Y1": 12,
    "UDM_COUNT": 13,
}


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
        "UDM_Y1": UDM_Y1,
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
    assert FIELD_UDM_Y1 == f"udm-{c_udm_enum['UDM_Y1']}"


def test_post_config_udm_indices_match_layout(c_udm_enum):
    post_cfg = load_post_config()
    assert post_cfg.udm_indices == UDM_INDEX_BY_ROLE
    assert post_cfg.udm_indices["salt_mass_source"] == c_udm_enum["UDM_SI"]
    assert post_cfg.udm_indices["total_mass_source"] == c_udm_enum["UDM_TOTAL_S"]
    assert post_cfg.udm_indices["cell_strain_rate"] == c_udm_enum["UDM_STRAIN_RATE"]
    assert post_cfg.udm_indices["salt_mass_flux"] == c_udm_enum["UDM_SALT_FLUX"]
    assert post_cfg.udm_indices["cp"] == c_udm_enum["UDM_CP"]
    assert post_cfg.udm_indices["wall_centroid_distance"] == c_udm_enum["UDM_Y1"]
    assert "water_mass_source" not in post_cfg.udm_indices
    assert "cp_inlet" not in post_cfg.udm_indices


def test_expected_udm_fields_cover_valid_range_only():
    for field_name in EXPECTED_UDM_FIELDS:
        match = re.fullmatch(r"udm-(\d+)", field_name)
        assert match is not None, field_name
        index = int(match.group(1))
        assert 0 <= index < UDM_COUNT, field_name


def test_module_expected_fields_match_parsed_260822(c_udm_enum):
    assert EXPECTED_UDM_FIELDS == expected_udm_fields_from_enum(c_udm_enum)


def test_case_udf_fields_follow_dated_layouts():
    fields_813 = expected_udm_fields_from_enum(
        parse_udm_enum_from_c(UDF_260813_PATH.read_text(encoding="utf-8"))
    )
    fields_810 = expected_udm_fields_from_enum(
        parse_udm_enum_from_c(UDF_260810_PATH.read_text(encoding="utf-8"))
    )
    fields_612 = expected_udm_fields_from_enum(
        parse_udm_enum_from_c(UDF_260612_PATH.read_text(encoding="utf-8"))
    )

    assert fields_813["udm-12"] == "wall_centroid_distance"
    assert "udm-12" not in fields_810
    assert fields_810["udm-10"] == "salt_mass_flux"
    assert fields_612["udm-12"] == "salt_mass_flux"
    assert fields_612["udm-1"] == "water_mass_source"


def test_expected_udm_fields_from_case_uses_case_local_copy(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "260810_RO_UDF.c").write_text(
        UDF_260810_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fields = expected_udm_fields_from_case(case_dir)
    assert "udm-12" not in fields
    assert find_case_udf_path(case_dir).name == "260810_RO_UDF.c"


def test_expected_udm_fields_from_case_missing_copy_raises(tmp_path):
    case_dir = tmp_path / "empty_case"
    case_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="No case-local UDF"):
        expected_udm_fields_from_case(case_dir)


def test_expected_udm_fields_from_case_multiple_copies_raise(tmp_path):
    case_dir = tmp_path / "two_udfs"
    case_dir.mkdir()
    (case_dir / "260810_RO_UDF.c").write_text("enum { UDM_COUNT = 1\n};", encoding="utf-8")
    (case_dir / "260813_RO_UDF.c").write_text("enum { UDM_COUNT = 1\n};", encoding="utf-8")
    with pytest.raises(ValueError, match="Multiple case-local UDFs"):
        expected_udm_fields_from_case(case_dir)


def test_no_legacy_udm_13_in_active_post_scripts():
    """udm-13 is out of range when UDM_COUNT=13 (valid 0..12). udm-12 is Y1."""
    active_roots = [
        SCRIPTS_DIR / "pyfluent_report_extract.py",
        SCRIPTS_DIR / "pyfluent_field_check.py",
        SCRIPTS_DIR / "pyensight_contour_export.py",
        SCRIPTS_DIR / "pyfluent_shear_contour_export.py",
        REPO_ROOT / "src" / "ro" / "udm_layout.py",
        REPO_ROOT / "src" / "ro" / "fluent_report_helpers.py",
    ]
    banned = re.compile(r"udm-13|UDM_13|User Defined Memory 13")
    for path in active_roots:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "out of range" in line or "do not fall back" in line:
                continue
            assert banned.search(line) is None, f"{path}:{line_no}: {line}"


def test_d_salt_matches_run_config_mass_diffusivity():
    run_cfg = load_run_config()
    expected = float(run_cfg.mass_diffusivity)
    for label, path in (
        ("260813_RO_UDF.c", UDF_260813_PATH),
        ("260815_RO_UDF.c", UDF_260815_PATH),
        ("260816_RO_UDF.c", UDF_260816_PATH),
        ("260822_RO_UDF.c", UDF_260822_PATH),
    ):
        source = path.read_text(encoding="utf-8")
        match = re.search(
            r"^\s*#define\s+D_SALT\s+([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)",
            source,
            flags=re.MULTILINE,
        )
        assert match is not None, f"Could not find #define D_SALT in {label}"
        assert float(match.group(1)) == pytest.approx(expected)


def test_parse_ro_analytic_cwall_from_production_udf():
    from ro.udm_layout import (
        parse_ro_analytic_cwall_from_udf_path,
        parse_ro_analytic_cwall_from_udf_source,
    )

    assert parse_ro_analytic_cwall_from_udf_path(UDF_260822_PATH) == 1
    assert parse_ro_analytic_cwall_from_udf_path(UDF_260813_PATH) == 0
    source = UDF_260822_PATH.read_text(encoding="ascii")
    assert parse_ro_analytic_cwall_from_udf_source(source) == 1


def test_analytic_cwall_defaults_off():
    source = UDF_260813_PATH.read_text(encoding="utf-8")
    assert re.search(r"#define\s+RO_ANALYTIC_CWALL\s+0", source)


def test_260814_analytic_cwall_on():
    source = UDF_260814_PATH.read_text(encoding="utf-8")
    assert re.search(r"#define\s+RO_ANALYTIC_CWALL\s+1", source)


def test_260814_matches_260813_except_analytic_cwall_and_date():
    """260814 is 260813 with reconstruction ON; no other drift."""
    text_813 = UDF_260813_PATH.read_text(encoding="utf-8")
    text_814 = UDF_260814_PATH.read_text(encoding="utf-8")
    norm_813 = (
        text_813.replace("260813", "DATE").replace(
            "#define RO_ANALYTIC_CWALL 0",
            "#define RO_ANALYTIC_CWALL FLAG",
        )
    )
    norm_814 = (
        text_814.replace("260814", "DATE").replace(
            "#define RO_ANALYTIC_CWALL 1",
            "#define RO_ANALYTIC_CWALL FLAG",
        )
    )
    assert norm_813 == norm_814


def test_260816_production_flags_and_cell_y1():
    source = UDF_260816_PATH.read_text(encoding="ascii")
    assert re.search(r"#define\s+RO_ANALYTIC_CWALL\s+1", source)
    assert re.search(r"#define\s+RO_UDM_FACE_DIAGNOSTICS\s+0", source)
    assert re.search(r"#define\s+RO_UDM_CELL_DIAGNOSTICS\s+1", source)
    assert "C_UDMI(c, c_thread, UDM_Y1)        += y1 * dAm;" in source
    assert "C_UDMI(c, c_thread, UDM_Y1)        /= Aacc;" in source
    parsed = parse_udm_enum_from_c(source)
    assert parsed == REQUIRED_C_SYMBOLS
    assert parsed["UDM_COUNT"] == 13
    assert parsed["UDM_Y1"] == 12


def test_260816_matches_260815_except_ascii_comments_and_date():
    source_815 = UDF_260815_PATH.read_text(encoding="utf-8")
    source_816 = UDF_260816_PATH.read_text(encoding="ascii")
    normalized_815 = (
        source_815.replace("260815", "DATE").replace("—", "--").replace("·", "*")
    )
    normalized_816 = source_816.replace("260816", "DATE")
    assert normalized_816 == normalized_815


def _udf_macro(source: str, name: str) -> str:
    match = re.search(rf"^\s*#define\s+{name}\s+(.+)$", source, flags=re.MULTILINE)
    assert match is not None, f"missing #define {name}"
    return match.group(1).strip()


def test_260822_production_flags_and_cell_y1():
    source = UDF_260822_PATH.read_text(encoding="ascii")
    assert re.search(r"#define\s+RO_ANALYTIC_CWALL\s+1", source)
    assert re.search(r"#define\s+RO_UDM_FACE_DIAGNOSTICS\s+0", source)
    assert re.search(r"#define\s+RO_UDM_CELL_DIAGNOSTICS\s+1", source)
    assert "C_UDMI(c, c_thread, UDM_Y1)        += y1 * dAm;" in source
    assert "C_UDMI(c, c_thread, UDM_Y1)        /= Aacc;" in source
    parsed = parse_udm_enum_from_c(source)
    assert parsed == REQUIRED_C_SYMBOLS
    assert parsed["UDM_COUNT"] == 13
    assert parsed["UDM_Y1"] == 12


def test_260822_emits_profile_g_only_on_accepted_path():
    source = UDF_260822_PATH.read_text(encoding="ascii")
    assert 'Message0("RO_UDF_INLET_PROFILE_G=%.12g\\n", G);' in source
    assert "WARNING: RO_UDF_INLET_G_OUT_OF_RANGE" in source
    accepted = source.split("RO_UDF_INLET_G_OUT_OF_RANGE", 1)[1]
    assert "RO_UDF_INLET_PROFILE_G=" in accepted
    fallback = source.split("RO_UDF_INLET_G_OUT_OF_RANGE", 1)[0]
    assert "RO_UDF_INLET_PROFILE_G=" not in fallback


def test_260822_keeps_frozen_physics_from_260816():
    source_816 = UDF_260816_PATH.read_text(encoding="ascii")
    source_822 = UDF_260822_PATH.read_text(encoding="ascii")
    for name in (
        "D_SALT",
        "RO_ANALYTIC_CWALL",
        "INLET_Z_BOTTOM",
        "CHANNEL_HEIGHT",
        "INLET_AREA_EXPECTED_M2",
        "U_TARGET",
        "INLET_G_MIN",
        "INLET_G_MAX",
    ):
        assert _udf_macro(source_822, name) == _udf_macro(source_816, name), name
    assert parse_udm_enum_from_c(source_822) == parse_udm_enum_from_c(source_816)
    shape_816 = source_816.split("static real inlet_poiseuille_shape", 1)[1]
    shape_816 = shape_816.split("static void ensure_inlet_G", 1)[0]
    shape_822 = source_822.split("static real inlet_poiseuille_shape", 1)[1]
    shape_822 = shape_822.split("static void ensure_inlet_G", 1)[0]
    assert shape_822 == shape_816


def test_solver_udm_print_does_not_name_260813():
    solver = (SCRIPTS_DIR / "solver_code_260616.py").read_text(encoding="utf-8")
    assert "260813_RO_UDF.c" not in solver
    assert "run_inlet_profile_probe or use_inlet_velocity_profile" not in solver
    assert "execute-on-demand" in solver


def test_new_udf_sources_are_ascii_with_exact_frozen_legacy_exceptions():
    for path in sorted(UDF_DIR.glob("*.c")):
        source = path.read_text(encoding="utf-8")
        actual = tuple(
            (line_no, character)
            for line_no, line in enumerate(source.splitlines(), start=1)
            for character in line
            if not character.isascii()
        )
        expected = LEGACY_NON_ASCII_CHARACTERS.get(path.name, ())
        assert actual == expected, f"{path.name}: unexpected non-ASCII content {actual!r}"
        if path.name not in LEGACY_NON_ASCII_CHARACTERS:
            assert path.read_bytes().isascii(), f"{path.name} is not ASCII-only"


def test_run_config_selects_ascii_production_udf():
    run_cfg = load_run_config()
    assert run_cfg.udf_source_file_name == UDF_260822_PATH.name
    assert UDF_260822_PATH.read_bytes().isascii()


def test_y1_first_adjust_print_omits_cp_comparison():
    source = UDF_260813_PATH.read_text(encoding="utf-8")
    y1_header = source.find("=== RO_UDF membrane wall y1 ===")
    assert y1_header != -1
    probe_header = source.find("=== RO_UDF probe_cp_reconstruction ===")
    assert probe_header != -1
    y1_block = source[y1_header:probe_header]
    assert "CP raw (cell-centre)" not in y1_block
    assert "DEFINE_ON_DEMAND(probe_cp_reconstruction)" in source
    probe_block = source[probe_header:probe_header + 800]
    assert "CP raw (cell-centre)" in probe_block
    assert "CP reconstructed" in probe_block


def test_frozen_udf_regression_references_unchanged():
    digest_810 = hashlib.md5(UDF_260810_PATH.read_bytes()).hexdigest()
    digest_612 = hashlib.md5(UDF_260612_PATH.read_bytes()).hexdigest()
    assert digest_810 == MD5_260810
    assert digest_612 == MD5_260612
