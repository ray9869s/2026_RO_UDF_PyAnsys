"""Copy the 2D UDF into a case folder and patch geometry-dependent macros.

The master file under ``udfs/`` is never modified. ``U_TARGET``,
``CHANNEL_HEIGHT``, and ``INLET_Y_BOTTOM`` are rewritten on the copy so a
later design point does not keep the campaign placeholder.
"""

from __future__ import annotations

import re
from pathlib import Path

from ro.paths import udfs_dir
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.physics import UDF_FILE_NAME

_DEFINE_VALUE = re.compile(
    r"(#define\s+(?P<name>U_TARGET|CHANNEL_HEIGHT|INLET_Y_BOTTOM)\s+)"
    r"(?:\([^)\n]*\)|[^\s/]+)"
)


def master_udf_path() -> Path:
    return udfs_dir() / UDF_FILE_NAME


def render_define_value(name: str, value: float) -> str:
    text = f"{float(value):.12g}"
    if name == "INLET_Y_BOTTOM":
        return f"({text})"
    return text


def patch_udf_text(source: str, config: PilotConfig) -> str:
    operating = config.operating
    if operating is None:
        raise ValueError("PilotConfig.operating is missing.")
    replacements = {
        "U_TARGET": operating.inlet_velocity_m_s,
        "CHANNEL_HEIGHT": config.channel_height_m,
        "INLET_Y_BOTTOM": -0.5 * config.channel_height_m,
    }
    seen: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        seen.add(name)
        return match.group(1) + render_define_value(name, replacements[name])

    patched = _DEFINE_VALUE.sub(replace, source)
    missing = sorted(set(replacements) - seen)
    if missing:
        raise ValueError(
            "2D UDF is missing macros required for a case copy: "
            + ", ".join(missing)
        )
    return patched


def write_case_udf(config: PilotConfig, destination: Path) -> Path:
    """Write the patched UDF. Refuses to overwrite the master file."""
    master = master_udf_path()
    target = Path(destination)
    if target.resolve() == master.resolve():
        raise ValueError(f"Refusing to patch the master UDF at {master}.")
    patched = patch_udf_text(master.read_text(encoding="ascii"), config)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patched, encoding="ascii", newline="\n")
    return target
