"""Pure helpers: parse Fluent solve residual tables and measure window stats.

SAFE — no PyFluent / Fluent. Used by 09_residual_measurement_report.py.
Does not change convergence_status or case_status.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any, Iterable, Optional

RESIDUAL_EQS = (
    "continuity",
    "x-velocity",
    "y-velocity",
    "z-velocity",
    "nacl",
)
RESIDUAL_EQ_KEYS = tuple(eq.replace("-", "_") for eq in RESIDUAL_EQS)

QOI_MONITORS = ("lmh", "m_out")
CONSTANT_MONITORS = ("m_in", "area_mem")

# summary_metrics_wide.csv columns carried through unchanged (not recomputed).
SUMMARY_WIDE_CARRY_COLUMNS = (
    "mass_balance_relative_error",
    "lmh_mass_balance",
    "boundary_permeate_mass_flow",
    "mass_balance_error_boundary_minus_total_sink",
)

TRANSCRIPT_SUFFIXES = {".txt", ".trn", ".log", ".out"}
CLOCK_TOKEN_RE = re.compile(r"^\d+:\d{2}(:\d{2})?$")

SLOPE_MAG = 2.3e-4
REL_SPREAD_FLAT = 0.05
ACF1_COHERENT = 0.5
SIGN_FLIP_COHERENT_MAX = 0.15
SIGN_FLIP_NOISY_MIN = 0.35
MIN_USABLE_POINTS = 20
MIN_POSITIVE_FOR_LOG = 10

PARSE_OK = "ok"
PARSE_NO_TRANSCRIPT = "no_transcript"
PARSE_NO_RESIDUAL_TABLE = "no_residual_table"
PARSE_UNREADABLE = "unreadable"
PARSE_MALFORMED = "malformed_table"

TREND_UNKNOWN = "unknown"
TREND_STILL_DESCENDING = "still_descending"
TREND_OSCILLATING_COHERENT = "oscillating_coherent"
TREND_NOISY_PLATEAU = "noisy_plateau"
TREND_FLAT_PLATEAU = "flat_plateau"


def read_text_replace(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read text with errors='replace'. Returns (text, error_message)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def is_residual_header_line(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    if tokens[0].isdigit():
        return False
    return "continuity" in {t.lower() for t in tokens}


def parse_residual_data_row(line: str) -> Optional[dict[str, Any]]:
    """Positional parse of a 12-field Fluent residual/monitor row.

    Layout: iter, 5 residuals, 4 monitors, clock, optional trailing int.
    Rejects rows that do not match (including naive 11-token zip layouts).
    """
    tokens = line.split()
    if len(tokens) not in (11, 12):
        return None
    try:
        iteration = int(tokens[0])
    except ValueError:
        return None
    try:
        residuals = [float(tokens[i]) for i in range(1, 6)]
        monitors = [float(tokens[i]) for i in range(6, 10)]
    except ValueError:
        return None
    if not CLOCK_TOKEN_RE.match(tokens[10]):
        return None
    remaining: Optional[int] = None
    if len(tokens) == 12:
        try:
            remaining = int(tokens[11])
        except ValueError:
            return None
    return {
        "iter": iteration,
        "continuity": residuals[0],
        "x-velocity": residuals[1],
        "y-velocity": residuals[2],
        "z-velocity": residuals[3],
        "nacl": residuals[4],
        "lmh": monitors[0],
        "m_out": monitors[1],
        "m_in": monitors[2],
        "area_mem": monitors[3],
        "clock": tokens[10],
        "remaining_iters": remaining,
    }


def parse_residual_table(text: str) -> tuple[list[dict[str, Any]], str]:
    """Parse all valid residual rows from transcript text.

    Skips repeated header pages. Last occurrence wins per iteration.
    Returns (rows_sorted_by_iter, detail).
    """
    by_iter: dict[int, dict[str, Any]] = {}
    saw_header = False
    skipped_malformed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if is_residual_header_line(line):
            saw_header = True
            continue
        if not saw_header:
            continue
        if not line[0].isdigit():
            continue
        row = parse_residual_data_row(line)
        if row is None:
            skipped_malformed += 1
            continue
        by_iter[int(row["iter"])] = row
    rows = [by_iter[k] for k in sorted(by_iter)]
    detail_parts: list[str] = []
    if not saw_header:
        return [], "no residual header found"
    if not rows:
        return [], "residual header found but no valid data rows"
    if skipped_malformed:
        detail_parts.append(f"skipped_malformed_rows={skipped_malformed}")
    detail_parts.append(f"parsed_iters={len(rows)}")
    return rows, "; ".join(detail_parts)


def transcript_max_iteration(text: str) -> Optional[int]:
    rows, _ = parse_residual_table(text)
    if not rows:
        return None
    return int(rows[-1]["iter"])


def list_transcript_candidates(case_dir: Path) -> list[Path]:
    if not case_dir.is_dir():
        return []
    out: list[Path] = []
    try:
        for path in case_dir.iterdir():
            if path.is_file() and path.suffix.lower() in TRANSCRIPT_SUFFIXES:
                out.append(path)
    except OSError:
        return []
    return sorted(out, key=lambda p: p.name.lower())


def select_solve_transcript(case_dir: Path) -> tuple[Optional[Path], str, str]:
    """Select solve transcript. Prefer solver_log_*.txt, else largest max-iter.

    Returns (path_or_None, parse_status, detail).
    """
    candidates = list_transcript_candidates(case_dir)
    if not candidates:
        return None, PARSE_NO_TRANSCRIPT, "no .txt/.trn/.log/.out files in case dir"

    scored: list[tuple[Path, int, int, str]] = []
    unreadable: list[str] = []
    no_table: list[str] = []
    for path in candidates:
        text, err = read_text_replace(path)
        if text is None:
            unreadable.append(f"{path.name}:{err}")
            continue
        rows, detail = parse_residual_table(text)
        if not rows:
            no_table.append(f"{path.name}:{detail}")
            continue
        max_iter = int(rows[-1]["iter"])
        scored.append((path, max_iter, len(rows), detail))

    if not scored:
        if unreadable and not no_table:
            return None, PARSE_UNREADABLE, "; ".join(unreadable[:5])
        if no_table:
            return None, PARSE_NO_RESIDUAL_TABLE, "; ".join(no_table[:5])
        return None, PARSE_UNREADABLE, "no readable residual table"

    solver_logs = [s for s in scored if s[0].name.lower().startswith("solver_log_")]
    pool = solver_logs if solver_logs else scored
    pool.sort(key=lambda s: (s[1], s[2], s[0].name.lower()), reverse=True)
    chosen, max_iter, nrows, detail = pool[0]
    return (
        chosen,
        PARSE_OK,
        f"chosen={chosen.name}; max_iter={max_iter}; rows={nrows}; {detail}",
    )


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def relative_spread(values: list[float]) -> Optional[float]:
    mean = _mean(values)
    if mean is None or mean == 0.0:
        return None
    return (max(values) - min(values)) / mean


def relative_peak_to_peak(values: list[float]) -> Optional[float]:
    mean = _mean(values)
    if mean is None or mean == 0.0:
        return None
    return (max(values) - min(values)) / abs(mean)


def log10_slope(iters: list[int], values: list[float]) -> Optional[float]:
    """OLS slope of log10(r) vs iteration for positive samples."""
    pts = [(float(i), math.log10(v)) for i, v in zip(iters, values) if v > 0.0]
    if len(pts) < MIN_POSITIVE_FOR_LOG:
        return None
    n = len(pts)
    mean_x = sum(p[0] for p in pts) / n
    mean_y = sum(p[1] for p in pts) / n
    var_x = sum((p[0] - mean_x) ** 2 for p in pts)
    if var_x == 0.0:
        return 0.0
    cov = sum((p[0] - mean_x) * (p[1] - mean_y) for p in pts)
    return cov / var_x


def sign_flip_fraction(values: list[float]) -> Optional[float]:
    if len(values) < 3:
        return None
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    nonzero = [d for d in diffs if d != 0.0]
    if len(nonzero) < 2:
        return 0.0
    flips = 0
    for i in range(len(nonzero) - 1):
        if nonzero[i] * nonzero[i + 1] < 0.0:
            flips += 1
    return flips / (len(nonzero) - 1)


def lag1_acf(values: list[float]) -> Optional[float]:
    """Lag-1 autocorrelation of the mean-detrended series."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    detrended = [v - mean for v in values]
    denom = sum(v * v for v in detrended)
    if denom == 0.0:
        return 1.0
    num = sum(detrended[i] * detrended[i + 1] for i in range(len(detrended) - 1))
    return num / denom


def mean_crossing_count(values: list[float]) -> int:
    """Count times the series crosses its mean (sign change of v - mean)."""
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    signs = [1 if v >= mean else -1 for v in values]
    return sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])


def classify_residual_trend(
    iters: list[int],
    values: list[float],
) -> dict[str, Any]:
    """Classify residual window trend. Diagnostics only — not a status rewrite."""
    result: dict[str, Any] = {
        "trend": TREND_UNKNOWN,
        "rel_spread": None,
        "log10_slope": None,
        "sign_flip_frac": None,
        "acf1": None,
    }
    if len(values) < MIN_USABLE_POINTS:
        return result
    mean = _mean(values)
    if mean is None or mean <= 0.0:
        return result
    positive = [v for v in values if v > 0.0]
    if len(positive) < MIN_POSITIVE_FOR_LOG:
        return result

    rel = relative_spread(values)
    slope = log10_slope(iters, values)
    flips = sign_flip_fraction(values)
    acf1 = lag1_acf(values)
    crossings = mean_crossing_count(values)
    result.update(
        {
            "rel_spread": rel,
            "log10_slope": slope,
            "sign_flip_frac": flips,
            "acf1": acf1,
        }
    )
    if slope is None or rel is None or flips is None or acf1 is None:
        return result

    # Coherent oscillation needs repeated mean crossings. Smooth monotonic
    # descent also has high acf1 and low sign_flip_frac, so crossings separate
    # limit cycles from still_descending. OLS log10 slope alone is not enough:
    # a sinusoid is not orthogonal to a linear trend over a finite window.
    coherent_osc_evidence = (
        rel > REL_SPREAD_FLAT
        and acf1 > ACF1_COHERENT
        and flips < SIGN_FLIP_COHERENT_MAX
        and crossings >= 4
    )

    # First match wins (approved order).
    if slope <= -SLOPE_MAG and not coherent_osc_evidence:
        result["trend"] = TREND_STILL_DESCENDING
    elif coherent_osc_evidence:
        result["trend"] = TREND_OSCILLATING_COHERENT
    elif (
        rel > REL_SPREAD_FLAT
        and (flips >= SIGN_FLIP_NOISY_MIN or acf1 <= ACF1_COHERENT)
        and abs(slope) <= SLOPE_MAG
    ):
        result["trend"] = TREND_NOISY_PLATEAU
    elif rel <= REL_SPREAD_FLAT and abs(slope) <= SLOPE_MAG:
        result["trend"] = TREND_FLAT_PLATEAU
    else:
        result["trend"] = TREND_UNKNOWN
    return result


def window_rows(rows: list[dict[str, Any]], window_n: int) -> list[dict[str, Any]]:
    if window_n <= 0:
        return list(rows)
    return rows[-window_n:]


def measure_case_from_rows(
    rows: list[dict[str, Any]],
    *,
    window_n: int,
    residual_target: float,
    iteration_cap: int,
) -> dict[str, Any]:
    """Build measurement fields from parsed residual rows."""
    out: dict[str, Any] = {
        "window_n_requested": window_n,
        "window_iters_used": 0,
        "iterations_completed": None,
        "iteration_cap": iteration_cap,
        "residual_target": residual_target,
        "hit_iteration_cap": False,
    }
    if not rows:
        return out

    final = rows[-1]
    out["iterations_completed"] = int(final["iter"])
    out["hit_iteration_cap"] = bool(
        out["iterations_completed"] is not None
        and int(out["iterations_completed"]) >= int(iteration_cap)
    )
    win = window_rows(rows, window_n)
    out["window_iters_used"] = len(win)
    iters = [int(r["iter"]) for r in win]

    detail_notes: list[str] = []

    for eq, key in zip(RESIDUAL_EQS, RESIDUAL_EQ_KEYS):
        series = [float(r[eq]) for r in win]
        final_val = float(final[eq])
        mean = _mean(series)
        wmax = max(series) if series else None
        rel = relative_spread(series)
        shortfall = None
        if residual_target > 0.0 and final_val is not None:
            shortfall = final_val / residual_target
        target_met = bool(final_val > 0.0 and residual_target > 0.0 and final_val <= residual_target)
        trend = classify_residual_trend(iters, series)
        out[f"{key}_final"] = final_val
        out[f"{key}_window_max"] = wmax
        out[f"{key}_window_mean"] = mean
        out[f"{key}_window_rel_spread"] = rel
        out[f"{key}_shortfall_factor"] = shortfall
        out[f"{key}_target_met"] = target_met
        out[f"{key}_trend"] = trend["trend"]
        out[f"{key}_log10_slope"] = trend["log10_slope"]
        out[f"{key}_sign_flip_frac"] = trend["sign_flip_frac"]
        out[f"{key}_acf1"] = trend["acf1"]
        out[f"{key}_rel_spread"] = trend["rel_spread"]
        if eq == "nacl" and series and all(v == 0.0 for v in series):
            detail_notes.append(
                "nacl residual is 0.0 for the entire measurement window "
                "(species possibly not solved in this run)"
            )

    for name in QOI_MONITORS:
        series = [float(r[name]) for r in win]
        out[f"{name}_final"] = float(final[name])
        out[f"{name}_window_mean"] = _mean(series)
        out[f"{name}_window_rel_ptp"] = relative_peak_to_peak(series)

    for name in CONSTANT_MONITORS:
        out[f"{name}_final"] = float(final[name])
        out[f"{name}_is_constant_monitor"] = True

    if detail_notes:
        out["_detail_notes"] = detail_notes
    return out


def read_summary_wide_carry(reports_dir: Path) -> dict[str, Any]:
    """Carry selected columns from post/reports/summary_metrics_wide.csv unchanged."""
    path = reports_dir / "summary_metrics_wide.csv"
    empty = {col: None for col in SUMMARY_WIDE_CARRY_COLUMNS}
    empty["summary_metrics_wide_file"] = ""
    empty["summary_metrics_wide_status"] = "missing"
    if not path.is_file():
        return empty
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            row = next(reader, None)
    except (OSError, csv.Error) as exc:
        empty["summary_metrics_wide_status"] = f"unreadable:{type(exc).__name__}"
        return empty
    if not row:
        empty["summary_metrics_wide_status"] = "empty"
        return empty
    out: dict[str, Any] = {
        "summary_metrics_wide_file": str(path),
        "summary_metrics_wide_status": "ok",
    }
    for col in SUMMARY_WIDE_CARRY_COLUMNS:
        raw = row.get(col, "")
        if raw is None or raw == "":
            out[col] = None
            continue
        try:
            out[col] = float(raw)
        except (TypeError, ValueError):
            out[col] = raw
    return out


def null_measurement_record(
    geo_name: str,
    case_name: str,
    case_dir: Path,
    *,
    parse_status: str,
    parse_detail: str,
    window_n: int,
    residual_target: float,
    iteration_cap: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "geo_name": geo_name,
        "case_name": case_name,
        "case_dir": str(case_dir),
        "solve_transcript": "",
        "parse_status": parse_status,
        "parse_detail": parse_detail,
        "window_n_requested": window_n,
        "window_iters_used": None,
        "iterations_completed": None,
        "iteration_cap": iteration_cap,
        "residual_target": residual_target,
        "hit_iteration_cap": None,
    }
    for key in RESIDUAL_EQ_KEYS:
        for suffix in (
            "final",
            "window_max",
            "window_mean",
            "window_rel_spread",
            "shortfall_factor",
            "target_met",
            "trend",
            "log10_slope",
            "sign_flip_frac",
            "acf1",
            "rel_spread",
        ):
            record[f"{key}_{suffix}"] = None
    for name in QOI_MONITORS:
        record[f"{name}_final"] = None
        record[f"{name}_window_mean"] = None
        record[f"{name}_window_rel_ptp"] = None
    for name in CONSTANT_MONITORS:
        record[f"{name}_final"] = None
        record[f"{name}_is_constant_monitor"] = True
    record.update(read_summary_wide_carry(case_dir / "post" / "reports"))
    return record


def measure_case_dir(
    case_dir: Path,
    geo_name: str,
    case_name: str,
    *,
    window_n: int,
    residual_target: float,
    iteration_cap: int,
) -> dict[str, Any]:
    """Full per-case measurement; never raises for parse failures."""
    try:
        path, status, detail = select_solve_transcript(case_dir)
        if path is None or status != PARSE_OK:
            return null_measurement_record(
                geo_name,
                case_name,
                case_dir,
                parse_status=status,
                parse_detail=detail,
                window_n=window_n,
                residual_target=residual_target,
                iteration_cap=iteration_cap,
            )
        text, err = read_text_replace(path)
        if text is None:
            return null_measurement_record(
                geo_name,
                case_name,
                case_dir,
                parse_status=PARSE_UNREADABLE,
                parse_detail=err or "read failed",
                window_n=window_n,
                residual_target=residual_target,
                iteration_cap=iteration_cap,
            )
        rows, table_detail = parse_residual_table(text)
        if not rows:
            return null_measurement_record(
                geo_name,
                case_name,
                case_dir,
                parse_status=PARSE_MALFORMED if "header" in table_detail else PARSE_NO_RESIDUAL_TABLE,
                parse_detail=table_detail,
                window_n=window_n,
                residual_target=residual_target,
                iteration_cap=iteration_cap,
            )
        measured = measure_case_from_rows(
            rows,
            window_n=window_n,
            residual_target=residual_target,
            iteration_cap=iteration_cap,
        )
        notes = list(measured.pop("_detail_notes", []))
        parse_detail = detail
        if table_detail:
            parse_detail = f"{parse_detail}; {table_detail}"
        if notes:
            parse_detail = f"{parse_detail}; " + "; ".join(notes)
        record: dict[str, Any] = {
            "geo_name": geo_name,
            "case_name": case_name,
            "case_dir": str(case_dir),
            "solve_transcript": str(path),
            "parse_status": PARSE_OK,
            "parse_detail": parse_detail,
        }
        record.update(measured)
        record.update(read_summary_wide_carry(case_dir / "post" / "reports"))
        return record
    except Exception as exc:  # noqa: BLE001 — report row must never crash the batch
        return null_measurement_record(
            geo_name,
            case_name,
            case_dir,
            parse_status=PARSE_UNREADABLE,
            parse_detail=f"unexpected:{type(exc).__name__}: {exc}",
            window_n=window_n,
            residual_target=residual_target,
            iteration_cap=iteration_cap,
        )


def report_fieldnames() -> list[str]:
    fields = [
        "geo_name",
        "case_name",
        "case_dir",
        "solve_transcript",
        "parse_status",
        "parse_detail",
        "window_n_requested",
        "window_iters_used",
        "iterations_completed",
        "iteration_cap",
        "residual_target",
        "hit_iteration_cap",
    ]
    for key in RESIDUAL_EQ_KEYS:
        fields.extend(
            [
                f"{key}_final",
                f"{key}_window_max",
                f"{key}_window_mean",
                f"{key}_window_rel_spread",
                f"{key}_shortfall_factor",
                f"{key}_target_met",
                f"{key}_trend",
                f"{key}_log10_slope",
                f"{key}_sign_flip_frac",
                f"{key}_acf1",
                f"{key}_rel_spread",
            ]
        )
    for name in QOI_MONITORS:
        fields.extend(
            [
                f"{name}_final",
                f"{name}_window_mean",
                f"{name}_window_rel_ptp",
            ]
        )
    for name in CONSTANT_MONITORS:
        fields.extend([f"{name}_final", f"{name}_is_constant_monitor"])
    fields.extend(
        [
            "summary_metrics_wide_file",
            "summary_metrics_wide_status",
            *SUMMARY_WIDE_CARRY_COLUMNS,
        ]
    )
    return fields


def flatten_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return repr(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_measurement_csv(path: Path, records: Iterable[dict[str, Any]]) -> None:
    fieldnames = report_fieldnames()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({k: flatten_csv_value(record.get(k)) for k in fieldnames})


def build_summary_text(records: list[dict[str, Any]]) -> str:
    from collections import Counter

    lines = [
        "Residual measurement report (diagnostics only).",
        "Does NOT update convergence_status or case_status.",
        "Trend labels are report diagnostics only, not classification inputs.",
        "oscillating_coherent is a CANDIDATE for unsteady physics requiring separate confirmation, not a conclusion.",
        "m_in_final / m_out_final are raw monitor values; mass balance is carried from summary_metrics_wide.csv when present.",
        "",
        f"cases: {len(records)}",
    ]
    status_counts = Counter(str(r.get("parse_status")) for r in records)
    lines.append("parse_status counts:")
    for key, count in sorted(status_counts.items()):
        lines.append(f"  {key}: {count}")

    ok = [r for r in records if r.get("parse_status") == PARSE_OK]
    lines.append(f"ok_cases: {len(ok)}")
    if ok:
        for eq_key in ("continuity", "nacl"):
            shortfalls = [
                float(r[f"{eq_key}_shortfall_factor"])
                for r in ok
                if isinstance(r.get(f"{eq_key}_shortfall_factor"), (int, float))
            ]
            if shortfalls:
                lines.append(
                    f"{eq_key}_shortfall_factor: min={min(shortfalls):.4g} "
                    f"median={sorted(shortfalls)[len(shortfalls)//2]:.4g} "
                    f"max={max(shortfalls):.4g}"
                )
            trends = Counter(str(r.get(f"{eq_key}_trend")) for r in ok)
            lines.append(f"{eq_key}_trend counts: {dict(sorted(trends.items()))}")
        met_counts = Counter(
            tuple(bool(r.get(f"{k}_target_met")) for k in RESIDUAL_EQ_KEYS) for r in ok
        )
        lines.append(f"target_met tuple counts (cont,x,y,z,nacl): {len(met_counts)} distinct patterns")
    lines.append("")
    return "\n".join(lines) + "\n"
