"""Campaign geo_id whitelist and family mapping.

Geometry parameters live in manifests and ``campaign_geometry``; geo_id is an
identifier only — never a parameter source.
"""

from __future__ import annotations

import re
from pathlib import Path

# Shape grammar: numeric token immediately after the family letter is optional
# (e.g. M_c160, S_a144_l3465). Membership is enforced by the whitelist below.
_CAMPAIGN_GEO_ID_SHAPE_RE = re.compile(
    r"^(?:"
    r"D(?:\d{4})?_a(?:30|45|60)|"
    r"M_c(?:160|267|400)|"
    r"P_p(?:60|80|100)_h(?:00|15|30)|"
    r"S_a\d{3}_l\d{4}|"
    r"REF_empty"
    r")$"
)

LEGACY_ML_GEO_ID_PREFIX = "M_r"

_DIAMOND_GEO_IDS = tuple(
    f"D{spacing}_a{angle}"
    for spacing in ("2450", "1225", "0817")
    for angle in ("30", "45", "60")
)

_MULTI_LAYER_GEO_IDS = ("M_c160", "M_c267", "M_c400")

_PILLAR_GEO_IDS = tuple(
    f"P_p{pitch}_h{hole}"
    for pitch in ("60", "80", "100")
    for hole in ("00", "15", "30")
)

_SINUSOIDAL_GEO_IDS = tuple(
    f"S_{amplitude}_l{wavelength}"
    for amplitude in ("a072", "a144", "a193")
    for wavelength in ("1733", "3465", "6930")
)

_REFERENCE_GEO_IDS = ("REF_empty",)

CAMPAIGN_GEO_IDS: frozenset[str] = frozenset(
    _DIAMOND_GEO_IDS
    + _MULTI_LAYER_GEO_IDS
    + _PILLAR_GEO_IDS
    + _SINUSOIDAL_GEO_IDS
    + _REFERENCE_GEO_IDS
)

assert len(CAMPAIGN_GEO_IDS) == 31, (
    f"Expected 31 campaign geo_ids, got {len(CAMPAIGN_GEO_IDS)}."
)


def campaign_geo_id_shape_re() -> re.Pattern[str]:
    """Return the campaign geo_id shape regex (whitelist is authoritative)."""
    return _CAMPAIGN_GEO_ID_SHAPE_RE


def family_for_geo_id(geo_id: str) -> str:
    """Map a whitelisted geo_id to its path family directory name."""
    if geo_id not in CAMPAIGN_GEO_IDS:
        raise ValueError(f"geo_id {geo_id!r} is not in the campaign whitelist.")
    if geo_id == "REF_empty":
        return "empty"
    if geo_id.startswith("D"):
        return "diamond"
    if geo_id.startswith("M_"):
        return "ml"
    if geo_id.startswith("P_"):
        return "pillar"
    if geo_id.startswith("S"):
        return "sin"
    raise ValueError(f"Cannot resolve family for geo_id {geo_id!r}.")


def assert_no_legacy_ml_geo_paths(data_root: Path) -> None:
    """Raise if any legacy M_r* geometry/mesh/run paths exist under data_root."""
    root = Path(data_root)
    if not root.is_dir():
        return
    legacy: list[str] = []
    for tree_name in ("geometries", "meshes", "runs"):
        tree = root / tree_name / "ml"
        if not tree.is_dir():
            continue
        for child in tree.iterdir():
            if child.name.startswith(LEGACY_ML_GEO_ID_PREFIX):
                legacy.append(child.as_posix())
    if legacy:
        raise ValueError(
            "Legacy ML geo_id paths (M_r*) found under RO_DATA_ROOT; "
            f"rename to M_c* before continuing: {legacy!r}."
        )


def validate_campaign_geo_id(geo_id: str) -> None:
    """Raise ValueError when geo_id is outside the 31-case whitelist."""
    if geo_id not in CAMPAIGN_GEO_IDS:
        raise ValueError(
            f"geo_id {geo_id!r} is not a campaign case. "
            f"Expected one of {len(CAMPAIGN_GEO_IDS)} whitelisted ids."
        )
    if _CAMPAIGN_GEO_ID_SHAPE_RE.fullmatch(geo_id) is None:
        raise ValueError(
            f"geo_id {geo_id!r} is whitelisted but fails the campaign shape regex."
        )
