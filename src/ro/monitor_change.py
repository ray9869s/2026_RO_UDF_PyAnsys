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
  last sample at or before ``L-N``. A converged monitor that oscillates
  reads its drift, not its oscillation amplitude.
- ``monitor_trend_last<N>_<q>``: least-squares slope over the samples in
  ``[L-N, L]`` times ``N``, relative to their mean.
- ``monitor_change_last<N>_<q>_legacy``: the previous statistic, the
  UG 37.18 maximum deviation ``max|m(L)-m(k)|/|m(L)|`` over the window.

``q`` is ``lmh`` (``lmh_udm_avg.out``), ``dp`` (``pressure_drop_spacer.out``)
and ``cp``. The new ``cp`` columns read the window-live ``cp_live.out``
(dead-zone faces excluded) and are blank without it; ``cp_window``
includes the dead zone and is only used for the legacy column. Series are
sorted by iteration and the last value per iteration wins.
"""

from __future__ import annotations

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


def end_to_end_relative_change(series, n: int) -> float | None:
    """``|m(L) - m(L-n)| / |m(L)|``; ``m(L-n)`` is the last sample <= L-n.

    None without a sample that old, or when ``m(L)`` is zero.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"iteration window must be an integer >= 1, got {n!r}.")
    points = clean_series(series)
    if len(points) < 2:
        return None
    last_iteration, last_value = points[-1]
    earlier = [value for iteration, value in points if iteration <= last_iteration - n]
    if not earlier or last_value == 0.0:
        return None
    return abs(last_value - earlier[-1]) / abs(last_value)


def trend_relative_change(series, n: int) -> float | None:
    """``|slope| * n / |mean|`` of a least-squares line over ``[L-n, L]``.

    None with fewer than three samples in the window or a zero mean.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"iteration window must be an integer >= 1, got {n!r}.")
    points = clean_series(series)
    if not points:
        return None
    last_iteration = points[-1][0]
    window = [(i, v) for i, v in points if i >= last_iteration - n]
    if len(window) < 3:
        return None
    count = len(window)
    mean_x = sum(i for i, _ in window) / count
    mean_y = sum(v for _, v in window) / count
    sxx = sum((i - mean_x) ** 2 for i, _ in window)
    if sxx == 0.0 or mean_y == 0.0:
        return None
    slope = sum((i - mean_x) * (v - mean_y) for i, v in window) / sxx
    return abs(slope) * n / abs(mean_y)


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
    """End-to-end change, trend, and legacy max deviation over ``n`` iterations.

    See the module docstring. Missing report files leave that entry None.
    The new CP columns read ``cp_live.out`` only. The legacy CP column
    prefers ``cp_window.out`` and otherwise reads ``cp_full_membrane.out``.
    """
    directory = Path(case_dir)
    prefix = "monitor_change_last100" if int(n) == 100 else f"monitor_change_last{int(n)}"
    trend = "monitor_trend_last100" if int(n) == 100 else f"monitor_trend_last{int(n)}"
    lmh = read_report_series(directory / LMH_REPORT_FILE)
    dp = read_report_series(directory / DP_REPORT_FILE)
    live_path = directory / cp_report_file_name(CP_MONITOR_LIVE)
    live = read_report_series(live_path)
    cp_path = cp_report_path(directory)
    legacy_cp = [] if cp_path is None else read_report_series(cp_path)
    changes = {
        f"{prefix}_lmh": end_to_end_relative_change(lmh, n),
        f"{prefix}_dp": end_to_end_relative_change(dp, n),
        f"{prefix}_cp": end_to_end_relative_change(live, n),
        f"{trend}_lmh": trend_relative_change(lmh, n),
        f"{trend}_dp": trend_relative_change(dp, n),
        f"{trend}_cp": trend_relative_change(live, n),
        f"{prefix}_lmh_legacy": relative_change_last_iterations(lmh, n),
        f"{prefix}_dp_legacy": relative_change_last_iterations(dp, n),
        f"{prefix}_cp_legacy": relative_change_last_iterations(legacy_cp, n),
    }
    if live_path.is_file():
        # Kept from earlier extracts: the legacy statistic on cp_live.
        changes[f"{prefix}_cp_live"] = relative_change_last_iterations(live, n)
    return changes


def last100_monitor_changes(case_dir) -> dict[str, float | None]:
    """Monitor-change columns over 100 iterations. Does not change PASS/FAIL."""
    return monitor_changes(case_dir, 100)


# Scheme forms that print or return Fluent's expression check. The first
# one is the formatted invalid-input table (Expression / Error / Used In).
EXPRESSION_CHECK_FORMS = (
    "(get-invalid-inputs-formated)",
    "(get-invalid-inputs)",
    "(expr-check)",
    "(validate-named-expr-def)",
    "(validate-expr-report-def)",
)


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


_CHECK_DID_NOT_RUN_RE = re.compile(
    r"unbound variable|invalid function|^\s*error\b|^[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def expression_check_ran(text) -> bool:
    """True when a check form returned output that is not an error.

    ``None`` (no scheme interface), an exception text from the wrapper,
    or Fluent's ``unbound variable`` / ``invalid function`` mean the form
    did not run and prove nothing.
    """
    if text is None:
        return False
    return _CHECK_DID_NOT_RUN_RE.search(str(text)) is None


def named_expression_listed_valid(text) -> bool:
    """True only when the listing positively says ``Valid : Yes``."""
    if not isinstance(text, str):
        return False
    return re.search(r"valid\s*[:=]\s*yes\b", text.casefold()) is not None


def expression_check_positive(form_texts, named_listings) -> tuple[bool, str]:
    """(passed, reason). Passing needs positive evidence, not silence.

    Every named expression must list ``Valid : Yes``. With no named
    expression, at least one check form must have run. Any invalid marker
    fails.
    """
    texts = [text for text in form_texts if text is not None]
    texts += [text for text in named_listings.values() if text is not None]
    if any(fluent_expression_report_is_invalid(str(text)) for text in texts):
        return False, "Fluent reported an invalid expression"
    not_valid = [name for name, text in named_listings.items() if not named_expression_listed_valid(text)]
    if not_valid:
        return False, "named expression(s) not listed as Valid : Yes: " + ", ".join(not_valid)
    if named_listings:
        return True, "every named expression is Valid : Yes"
    if any(expression_check_ran(text) for text in form_texts):
        return True, "a check form ran and reported nothing invalid"
    return False, "no expression check could run"


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
