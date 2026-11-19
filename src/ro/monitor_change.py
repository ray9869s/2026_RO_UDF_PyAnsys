"""Solve-time CP monitor definition and report-file monitor changes.

The CP monitor is the area-weighted mean of film-corrected wall
concentration (``udm-7``, mol/m3) divided by the window bulk
concentration. ``c_b`` is the evaluation-window mid-plane bulk value:
Fluent ``surface-massavg`` of the salt mass fraction on a z-normal
mid-plane clip, times density over molecular weight. It is not an
x-normal mixing cup. On a z-normal plane the massavg weight is the
through-plane mass flux, and the extract aggregates per-cell values by
area, so one solve-time window clip is close to, but not identical to,
the extracted ``c_b_window_mol_m3``. Host checks put the mid-plane value
0.01-0.2 % below the x-normal cups, always the same sign.

Report files are the source of record. Fluent's transcript columns use a
fixed print width of about three decimal places.

Monitor-change columns (recorded only; they do not set PASS/FAIL):

- ``monitor_change_last<N>_<q>``: end-to-end relative change
  ``|m(L) - m(L-N)| / |m(L)|`` between the last iteration ``L`` and the
  last sample at or before ``L-N``. It is phase-sensitive: on a monitor
  that oscillates it reads anywhere from 0 (``N`` a multiple of the
  period) up to the peak-to-peak amplitude.
- ``monitor_trend_last<N>_<q>``: least-squares slope over the samples in
  ``[L-N, L]`` times ``N``, relative to their mean. This is the drift
  measure: a stationary oscillation fits a slope near 0.
- ``monitor_change_last<N>_<q>_legacy``: the previous statistic, the
  UG 37.18 maximum deviation ``max|m(L)-m(k)|/|m(L)|`` over the window.
- ``monitor_amplitude_last<N>_<q>``: peak-to-peak of the residuals about
  that fitted line, relative to the mean: the oscillation, reported
  separately from the drift.
- ``monitor_window_status_last<N>_<q>``: ``ok`` or why the window is
  unavailable. The change, trend and amplitude are blank unless the
  window lies in the last attempt segment (iterations strictly
  increasing), every value is finite, the segment reaches back to
  ``L-N`` and the window holds at least ``max(3, ceil(0.8 N))`` samples.

``N`` defaults to 500 (``MONITOR_DEFAULT_WINDOW``); the extract writes the
N=100 set (kept) and the N=500 set. These are recorded only: the stop rule
and the 2000-iteration cap are unchanged.

``q`` is ``lmh`` (``lmh_udm_avg.out``), ``dp`` (``pressure_drop_spacer.out``)
and ``cp``. The new ``cp`` columns read the window-live ``cp_live.out``
(dead-zone faces excluded) and are blank without it; ``cp_window``
includes the dead zone and is only used for the legacy column. The legacy
column still sorts by iteration (last row per iteration wins).
"""

from __future__ import annotations

import math
import re
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
# The area-mean lives in a named expression. The report definition stays
# a ratio of names, which is the form cp_window already uses.
CP_LIVE_MEAN = "cp_live_mean"
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
MONITOR_TREND_LAST100 = (
    "monitor_trend_last100_lmh",
    "monitor_trend_last100_dp",
    "monitor_trend_last100_cp",
)
MONITOR_CHANGE_LAST100_LEGACY = tuple(f"{name}_legacy" for name in MONITOR_CHANGE_LAST100)

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


def cp_live_cm_definition(
    clip: str,
    cb_report: str,
    density_kg_per_m3: float,
    molecular_weight_kg_per_mol: float,
    b_perm_m_per_s: float,
) -> str:
    """Area-mean udm-7 on faces that stay in the live canon.

    udm-9 is 0 when ``c_p >= c_0``. The modulus also needs ``c_p < c_b``,
    and ``c_b`` can sit below ``c_0``. The predicate is udm-9 > 0 and
    ``B*udm-7/(udm-6+B) < c_b``, which is ``c_p < min(c_0, c_b)`` when the
    UDF zero and this comparison are both applied.
    """
    if not isinstance(clip, str) or not clip or any(
        token in clip for token in ("'", '"', "[", "]", ",", " ")
    ):
        raise ValueError(f"clip name must be one token, got {clip!r}.")
    density = float(density_kg_per_m3)
    molar_mass = float(molecular_weight_kg_per_mol)
    permeability = float(b_perm_m_per_s)
    if density <= 0.0 or molar_mass <= 0.0 or permeability <= 0.0:
        raise ValueError(
            "density, molecular weight, and B must be positive, "
            f"got density={density!r}, molar_mass={molar_mass!r}, "
            f"B={permeability!r}."
        )
    if not isinstance(cb_report, str) or not cb_report:
        raise ValueError(f"cb report name is required, got {cb_report!r}.")
    bulk = f"({cb_report}*{density:.16g}/{molar_mass:.16g})"
    # A bare udm-7 is the subtraction ``udm - 7``. Fluent then reports
    # "Setup contains invalid expression" and iterate() does not run.
    cm = "{udm-7}"
    flux = "{udm-6}"
    cp = "{udm-9}"
    permeate = f"({permeability:.16g}*{cm}/({flux}+{permeability:.16g}))"
    live = f"AND({cp}>0,{permeate}<{bulk})"
    # At initialization every udm is 0, so the live area is 0. The floor
    # is a fraction of the clip area, which keeps the denominator an area
    # without a unit literal.
    live_area = f"AreaInt(IF({live}, 1, 0), ['{clip}'])"
    floor = f"1e-30*Area(['{clip}'])"
    return (
        f"AreaInt(IF({live}, {cm}, 0), ['{clip}'])"
        f" / max({live_area}, {floor})"
    )


def cp_live_monitor_definition(
    clip: str,
    cb_report: str,
    density_kg_per_m3: float,
    molecular_weight_kg_per_mol: float,
    *,
    b_perm_m_per_s: float,
) -> str:
    """Live-face udm-7 mean divided by the window bulk concentration.

    The face test is ``c_p < min(c_0, c_b)``: udm-9 drops ``c_p >= c_0``,
    and the expression also drops ``c_p >= c_b``.
    """
    density = float(density_kg_per_m3)
    molar_mass = float(molecular_weight_kg_per_mol)
    if density <= 0.0 or molar_mass <= 0.0:
        raise ValueError(
            "density and molecular weight must be positive, "
            f"got density={density!r}, molar_mass={molar_mass!r}."
        )
    if not isinstance(cb_report, str) or not cb_report:
        raise ValueError(f"cb report name is required, got {cb_report!r}.")
    # The body is installed as the named expression CP_LIVE_MEAN. The
    # report definition only names it, matching cp_window.
    cp_live_cm_definition(
        clip,
        cb_report,
        density,
        molar_mass,
        b_perm_m_per_s,
    )
    return (
        f"{CP_LIVE_MEAN} / ({cb_report} * {density:.16g} / {molar_mass:.16g})"
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


def clean_series(series) -> list[tuple[int, float]]:
    """Sorted by iteration; for a repeated iteration the last row wins."""
    by_iteration = {}
    for iteration, value in series:
        by_iteration[int(iteration)] = float(value)
    return sorted(by_iteration.items())


# Default window for the monitor-change columns. last100 columns are kept.
MONITOR_DEFAULT_WINDOW = 500
# A window needs at least this fraction of n samples (and never fewer than 3).
MONITOR_MIN_SAMPLE_FRACTION = 0.8
MONITOR_WINDOW_OK = "ok"


def validated_window(series, n: int):
    """``(points, None)`` or ``(None, reason)`` for the last ``n`` iterations.

    ``series`` is in file order. The window must lie in the last attempt
    segment (a row whose iteration is not above the previous row starts a
    new segment: a restart or a re-written iteration), so its iterations
    are strictly increasing. Every value in it must be finite, the segment
    must reach back to ``L - n`` (full coverage), and the samples in
    ``[L-n, L]`` must number at least ``max(3, ceil(0.8 n))``. ``points``
    is the anchor (last sample at or before ``L-n``) followed by the
    samples after it.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"iteration window must be an integer >= 1, got {n!r}.")
    rows = [(int(iteration), float(value)) for iteration, value in series]
    if not rows:
        return None, "no samples"
    start = 0
    for index in range(1, len(rows)):
        if rows[index][0] <= rows[index - 1][0]:
            start = index
    segment = rows[start:]
    last_iteration = segment[-1][0]
    first_needed = last_iteration - n
    if segment[0][0] > first_needed:
        if start > 0:
            return None, (
                f"window [{first_needed}, {last_iteration}] spans more than one attempt "
                f"segment (last segment starts at {segment[0][0]})"
            )
        return None, f"window not covered: first sample {segment[0][0]} > {first_needed}"
    anchor_index = max(i for i, (iteration, _v) in enumerate(segment) if iteration <= first_needed)
    points = segment[anchor_index:]
    if any(not math.isfinite(value) for _i, value in points):
        return None, "non-finite value in window"
    inside = sum(1 for iteration, _v in points if iteration >= first_needed)
    required = max(3, math.ceil(MONITOR_MIN_SAMPLE_FRACTION * n))
    if inside < required:
        return None, f"{inside} samples in window, need {required}"
    return points, None


def end_to_end_relative_change(series, n: int) -> float | None:
    """``|m(L) - m(L-n)| / |m(L)|``; ``m(L-n)`` is the last sample <= L-n.

    None when ``validated_window`` rejects the series or ``m(L)`` is zero.
    """
    points, _reason = validated_window(series, n)
    if points is None:
        return None
    last_value = points[-1][1]
    if last_value == 0.0:
        return None
    return abs(last_value - points[0][1]) / abs(last_value)


def _line_fit(window):
    count = len(window)
    mean_x = sum(i for i, _ in window) / count
    mean_y = sum(v for _, v in window) / count
    sxx = sum((i - mean_x) ** 2 for i, _ in window)
    if sxx == 0.0:
        return None
    slope = sum((i - mean_x) * (v - mean_y) for i, v in window) / sxx
    return slope, mean_x, mean_y


def _fit_window(series, n):
    points, _reason = validated_window(series, n)
    if points is None:
        return None, None
    window = [(i, v) for i, v in points if i >= points[-1][0] - n]
    fit = _line_fit(window)
    if fit is None or fit[2] == 0.0:
        return None, None
    return window, fit


def trend_relative_change(series, n: int) -> float | None:
    """``|slope| * n / |mean|`` of a least-squares line over ``[L-n, L]``.

    None when ``validated_window`` rejects the series or the mean is zero.
    """
    window, fit = _fit_window(series, n)
    if window is None:
        return None
    slope, _mean_x, mean_y = fit
    return abs(slope) * n / abs(mean_y)


def oscillation_amplitude(series, n: int) -> float | None:
    """Peak-to-peak of the residuals about the fitted line, over ``|mean|``.

    The oscillation, separated from the drift that the trend measures.
    """
    window, fit = _fit_window(series, n)
    if window is None:
        return None
    slope, mean_x, mean_y = fit
    residuals = [v - (mean_y + slope * (i - mean_x)) for i, v in window]
    return (max(residuals) - min(residuals)) / abs(mean_y)


def window_status(series, n: int) -> str:
    """``"ok"`` or why the window is unavailable."""
    _points, reason = validated_window(series, n)
    return MONITOR_WINDOW_OK if reason is None else reason


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


def monitor_changes(case_dir, n: int = MONITOR_DEFAULT_WINDOW) -> dict[str, float | None]:
    """End-to-end change, trend, amplitude, status and legacy over ``n`` iterations.

    See the module docstring. Missing report files leave that entry None
    (status ``no samples``). The new CP columns read ``cp_live.out`` only.
    The legacy CP column prefers ``cp_window.out`` and otherwise reads
    ``cp_full_membrane.out``.
    """
    directory = Path(case_dir)
    n = int(n)
    prefix = f"monitor_change_last{n}"
    trend = f"monitor_trend_last{n}"
    amplitude = f"monitor_amplitude_last{n}"
    status = f"monitor_window_status_last{n}"
    lmh = read_report_series(directory / LMH_REPORT_FILE)
    dp = read_report_series(directory / DP_REPORT_FILE)
    live_path = directory / cp_report_file_name(CP_MONITOR_LIVE)
    live = read_report_series(live_path)
    cp_path = cp_report_path(directory)
    legacy_cp = [] if cp_path is None else read_report_series(cp_path)
    changes = {}
    for label, series in (("lmh", lmh), ("dp", dp), ("cp", live)):
        changes[f"{prefix}_{label}"] = end_to_end_relative_change(series, n)
        changes[f"{trend}_{label}"] = trend_relative_change(series, n)
        changes[f"{amplitude}_{label}"] = oscillation_amplitude(series, n)
        changes[f"{status}_{label}"] = window_status(series, n)
    changes.update({
        f"{prefix}_lmh_legacy": relative_change_last_iterations(lmh, n),
        f"{prefix}_dp_legacy": relative_change_last_iterations(dp, n),
        f"{prefix}_cp_legacy": relative_change_last_iterations(legacy_cp, n),
    })
    if live_path.is_file():
        # Kept from earlier extracts: the legacy statistic on cp_live.
        changes[f"{prefix}_cp_live"] = relative_change_last_iterations(live, n)
    return changes


def last100_monitor_changes(case_dir) -> dict[str, float | None]:
    """Monitor-change columns over 100 iterations. Does not change PASS/FAIL."""
    return monitor_changes(case_dir, 100)


def extract_monitor_changes(case_dir) -> dict[str, float | None]:
    """The extract's columns: the kept last100 set plus the default last500 set."""
    return {**monitor_changes(case_dir, 100), **monitor_changes(case_dir, MONITOR_DEFAULT_WINDOW)}


def format_expression_error_table(rows) -> str:
    """The Expression / Error / Used In table Fluent prints for a bad setup."""
    lines = [
        f"{'Expression':<32} {'Error':<20} Used In",
        f"{'-' * 32} {'-' * 20} {'-' * 12}",
    ]
    for expression, error, used_in in rows:
        name = str(expression).replace("\n", " ")
        reason = str(error).replace("\n", " ")
        where = str(used_in).replace("\n", " ")
        lines.append(f"{name[:32]:<32} {reason[:20]:<20} {where}")
    return "\n".join(lines)


def named_expression_listed_valid(text) -> bool:
    """True only when the listing positively says ``Valid : Yes``."""
    if not isinstance(text, str):
        return False
    return re.search(r"valid\s*[:=]\s*yes\b", text.casefold()) is not None


def fluent_expression_report_is_invalid(text: str) -> bool:
    """True when Fluent's own check text reports an invalid expression."""
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.casefold()
    if "setup contains invalid expression" in lowered:
        return True
    if "invalid expression:" in lowered:
        return True
    return re.search(r"valid\s*[:=]\s*no\b", lowered) is not None
