"""Solve-time CP monitor definition and report-file relative changes.

The CP monitor is the area-weighted mean of film-corrected wall
concentration (``udm-7``, mol/m3) divided by the window bulk
concentration. ``c_b`` is the mid-plane mixing-cup used at extraction:
``surface-massavg`` of the salt mass fraction on the evaluation-window
mid-plane, times density over molecular weight. Mixture density is
uniform, so one window clip matches the area-weighted aggregate of the
per-cell mixing cups.

Report files are the source of record. Fluent's transcript columns use a
fixed print width of about three decimal places.
"""

from __future__ import annotations

from pathlib import Path

from ro.solver_common import (
    fluent_report_relative_change,
    parse_fluent_report_file_series,
)

# At least 8 significant digits. 12 leaves margin for the 1e-4 stop.
REPORT_FILE_SIGNIFICANT_DIGITS = 12

CP_MONITOR_WINDOW = "cp_window"
CP_MONITOR_FULL_MEMBRANE = "cp_full_membrane"
# Area-mean udm-7 on faces where udm-9 > 0, over the same window c_b.
# udm-9 is 0 on the dead zone. cp_window averages every face on the clip.
CP_MONITOR_LIVE = "cp_live"
CP_CM_REPORT = "cp_monitor_cm_avg"
CP_CB_REPORT = "cp_monitor_cb_yi"
CP_MEMBRANE_CLIP = "cp_monitor_membrane_window"
CP_MIDPLANE_CLIP = "cp_monitor_midplane_window"

LMH_REPORT_FILE = "lmh_udm_avg.out"
DP_REPORT_FILE = "pressure_drop_spacer.out"

MONITOR_CHANGE_LAST100 = (
    "monitor_change_last100_lmh",
    "monitor_change_last100_dp",
    "monitor_change_last100_cp",
)

# Salt mass-fraction field names extraction tries, excluding udm-7.
# udm-7 is the wall concentration, not the bulk.
CB_SALT_FIELD_CANDIDATES = (
    "nacl",
    "mass-fraction-of-nacl",
    "yi-0",
    "species-0",
)


def molecular_weight_kg_per_mol(salt_molecular_weight_g_per_mol: float) -> float:
    """``run_config.salt_molecular_weight`` is g/mol (58.44)."""
    grams = float(salt_molecular_weight_g_per_mol)
    if grams <= 0.0:
        raise ValueError(
            f"salt molecular weight must be positive g/mol, got {grams!r}."
        )
    return grams / 1000.0


def cp_monitor_definition(
    cm_report: str,
    cb_report: str,
    density_kg_per_m3: float,
    molecular_weight_kg_per_mol: float,
) -> str:
    """Fluent expression: area-mean udm-7 / window c_b [mol/m3]."""
    density = float(density_kg_per_m3)
    molar_mass = float(molecular_weight_kg_per_mol)
    if density <= 0.0 or molar_mass <= 0.0:
        raise ValueError(
            "density and molecular weight must be positive, "
            f"got density={density!r}, molar_mass={molar_mass!r}."
        )
    return (
        f"{cm_report} / ({cb_report} * {density:.16g} / {molar_mass:.16g})"
    )


def cp_live_cm_definition(clip: str) -> str:
    """Area-mean udm-7 where udm-9 is positive, on one surface clip.

    The UDF stores udm-9 as 0 when ``c_p >= c_0``. Those dead-zone faces
    stay in the ``cp_window`` area average and are left out of this one.
    """
    if not isinstance(clip, str) or not clip or any(
        token in clip for token in ("'", '"', "[", "]", ",", " ")
    ):
        raise ValueError(f"clip name must be one token, got {clip!r}.")
    return (
        f"AreaInt(IF(udm-9>0, udm-7, 0), ['{clip}'])"
        f" / AreaInt(IF(udm-9>0, 1, 0), ['{clip}'])"
    )


def cp_live_monitor_definition(
    clip: str,
    cb_report: str,
    density_kg_per_m3: float,
    molecular_weight_kg_per_mol: float,
) -> str:
    """Live-face udm-7 mean divided by the window bulk concentration."""
    density = float(density_kg_per_m3)
    molar_mass = float(molecular_weight_kg_per_mol)
    if density <= 0.0 or molar_mass <= 0.0:
        raise ValueError(
            "density and molecular weight must be positive, "
            f"got density={density!r}, molar_mass={molar_mass!r}."
        )
    if not isinstance(cb_report, str) or not cb_report:
        raise ValueError(f"cb report name is required, got {cb_report!r}.")
    cm = cp_live_cm_definition(clip)
    return (
        f"({cm}) / ({cb_report} * {density:.16g} / {molar_mass:.16g})"
    )


def cp_report_file_name(report_name: str) -> str:
    return f"{report_name}.out"


def cb_salt_field_candidates(species_name: str) -> tuple[str, ...]:
    """Species name first, then the extraction mass-fraction candidates."""
    ordered = []
    for name in (species_name, *CB_SALT_FIELD_CANDIDATES):
        if name and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def relative_change_last_iterations(series, n: int) -> float | None:
    """Relative change over the last ``n`` iterations of ``(iteration, value)``.

    The window is every sample with iteration >= last - n, so a per-iteration
    file contributes n+1 points. The statistic is the UG 37.18 change of
    that window. None when the file has fewer than two samples in it.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"iteration window must be an integer >= 1, got {n!r}.")
    if not series:
        return None
    last_iteration = int(series[-1][0])
    start = last_iteration - n
    window = [float(value) for iteration, value in series if int(iteration) >= start]
    return fluent_report_relative_change(window)


def read_report_series(path) -> list[tuple[int, float]]:
    file_path = Path(path)
    if not file_path.is_file():
        return []
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_fluent_report_file_series(text)


def cp_report_path(case_dir) -> Path | None:
    directory = Path(case_dir)
    window = directory / cp_report_file_name(CP_MONITOR_WINDOW)
    if window.is_file():
        return window
    full = directory / cp_report_file_name(CP_MONITOR_FULL_MEMBRANE)
    if full.is_file():
        return full
    return None


def _change_at(case_dir, filename: str, n: int) -> float | None:
    return relative_change_last_iterations(
        read_report_series(Path(case_dir) / filename),
        n,
    )


def monitor_changes(case_dir, n: int = 100) -> dict[str, float | None]:
    """Relative changes of LMH, spacer dP, and CP over the last ``n`` iterations.

    Missing report files leave that entry None. CP prefers ``cp_window.out``
    and otherwise reads ``cp_full_membrane.out``.
    """
    directory = Path(case_dir)
    cp_path = cp_report_path(directory)
    cp_series = [] if cp_path is None else read_report_series(cp_path)
    prefix = "monitor_change_last100" if int(n) == 100 else f"monitor_change_last{int(n)}"
    changes = {
        f"{prefix}_lmh": _change_at(directory, LMH_REPORT_FILE, n),
        f"{prefix}_dp": _change_at(directory, DP_REPORT_FILE, n),
        f"{prefix}_cp": relative_change_last_iterations(cp_series, n),
    }
    live_path = directory / cp_report_file_name(CP_MONITOR_LIVE)
    if live_path.is_file():
        changes[f"{prefix}_cp_live"] = relative_change_last_iterations(
            read_report_series(live_path),
            n,
        )
    return changes


def last100_monitor_changes(case_dir) -> dict[str, float | None]:
    """The three acceptance-gate columns. Does not change PASS/FAIL."""
    return monitor_changes(case_dir, 100)
