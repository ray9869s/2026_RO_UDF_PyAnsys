"""Periodicity of the steady-solver monitors on one run leaf.

No Fluent. The series are the files the production solver already writes:

* ``lmh_udm_avg.out`` and ``pressure_drop_spacer.out`` in the case
  directory (``configs/run_config.py`` ``lmh_udm_avg_report_file_name`` and
  ``pressure_drop_spacer_report_file_name``).
  ``scripts/solver_code_260616.py`` ``ensure_lmh_udm_avg_report_file``
  writes them through ``solution.monitor.report_files``, one row per
  iteration, relative to the case directory.
* The residual table in the solve transcript selected by
  ``ro.residual_transcript.select_solve_transcript`` (prefer
  ``solver_log_*.txt``). Continuity is the column used here.

Over the last ``N`` iterations (default 500) each series reports the
mean, the sample standard deviation, the relative amplitude
``(max-min)/|mean|``, the least-squares slope per iteration, the
relative drift ``slope * (iter_last - iter_first) / |mean|``, and the
dominant period of the detrended series. The period is the
periodogram peak of the linear residual, in iterations. Its strength
is that peak divided by the median power of the other bins
(peak-to-background).

Classification, periodic first:

* **periodic** — strength >= 8, period between 4 iterations and half
  the window, at least two cycles in the window, and relative
  amplitude >= 0.01.
* **drifting** — not periodic, |relative drift| >= 0.02, and the
  linear fit has R^2 >= 0.8.
* **irregular** — neither. A flat series is irregular. A zero mean
  leaves the relative metrics undefined and is irregular.

Importing this module does not launch Fluent.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.residual_transcript import parse_residual_table, select_solve_transcript
from ro.solver_common import parse_fluent_report_file_series

DEFAULT_LAST = 500
MIN_POINTS = 16
PERIOD_STRENGTH_MIN = 8.0
RELATIVE_AMPLITUDE_MIN = 0.01
MIN_PERIOD_ITERATIONS = 4.0
MIN_CYCLES = 2.0
RELATIVE_DRIFT_MIN = 0.02
DRIFT_R2_MIN = 0.8

LMH_REPORT_FILE = "lmh_udm_avg.out"
PRESSURE_REPORT_FILE = "pressure_drop_spacer.out"
REPORT_FILES = (
    ("lmh_udm_avg", LMH_REPORT_FILE),
    ("pressure_drop_spacer", PRESSURE_REPORT_FILE),
)
CLASS_PERIODIC = "periodic"
CLASS_DRIFTING = "drifting"
CLASS_IRREGULAR = "irregular"

_MARKDOWN_NAME = "periodicity.md"
_CSV_NAME = "periodicity_series.csv"


def _mean(values):
    return sum(values) / len(values)


def _sample_std(values, mean):
    if len(values) < 2:
        raise ValueError("standard deviation needs at least two points.")
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _linear_fit(iterations, values):
    count = len(values)
    x_mean = _mean(iterations)
    y_mean = _mean(values)
    sxx = sum((x - x_mean) ** 2 for x in iterations)
    sxy = sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(iterations, values)
    )
    if sxx == 0.0:
        slope = 0.0
    else:
        slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in values)
    ss_res = sum(
        (y - (intercept + slope * x)) ** 2
        for x, y in zip(iterations, values)
    )
    if ss_tot == 0.0:
        r2 = 1.0
    else:
        r2 = 1.0 - ss_res / ss_tot
    return slope, intercept, r2


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _periodogram_peak(residuals):
    """Return (period_in_samples, peak_to_background) of the detrended series.

    Bins are the DFT frequencies ``k = 2 .. n/4``, so the period
    ``n/k`` lies in ``[4, n/2]`` and the window holds at least two cycles.
    """
    count = len(residuals)
    k_max = count // 4
    if k_max < 2:
        return None, 0.0
    powers = []
    for k in range(2, k_max + 1):
        real = 0.0
        imag = 0.0
        for index, value in enumerate(residuals):
            angle = 2.0 * math.pi * k * index / count
            real += value * math.cos(angle)
            imag -= value * math.sin(angle)
        powers.append(real * real + imag * imag)
    peak = max(powers)
    if peak <= 0.0:
        return None, 0.0
    peak_index = powers.index(peak)
    k = peak_index + 2
    others = [power for index, power in enumerate(powers) if index != peak_index]
    background = _median(others) if others else 0.0
    if background <= 0.0:
        strength = math.inf
    else:
        strength = peak / background
    return count / k, strength


def _unique_sorted(pairs):
    by_iteration = {}
    for iteration, value in pairs:
        by_iteration[int(iteration)] = float(value)
    return [(iteration, by_iteration[iteration]) for iteration in sorted(by_iteration)]


def last_window(pairs, last_n):
    """Last ``last_n`` unique iterations, in iteration order."""
    series = _unique_sorted(pairs)
    if last_n < MIN_POINTS:
        raise ValueError(
            f"last N must be at least {MIN_POINTS}, got {last_n}."
        )
    if len(series) > last_n:
        return series[-last_n:]
    return series


def analyze_series(pairs):
    """Statistics and class for one ``(iteration, value)`` window."""
    if len(pairs) < MIN_POINTS:
        raise ValueError(
            f"A monitor window needs at least {MIN_POINTS} iterations, "
            f"got {len(pairs)}."
        )
    iterations = [float(iteration) for iteration, _value in pairs]
    values = [value for _iteration, value in pairs]
    mean = _mean(values)
    std = _sample_std(values, mean)
    slope, intercept, r2 = _linear_fit(iterations, values)
    span = iterations[-1] - iterations[0]
    residuals = [
        value - (intercept + slope * iteration)
        for iteration, value in zip(iterations, values)
    ]
    period_samples, strength = _periodogram_peak(residuals)
    if period_samples is None or span == 0.0:
        period_iterations = None
    else:
        step = span / (len(pairs) - 1)
        period_iterations = period_samples * step
    if mean == 0.0:
        relative_amplitude = None
        relative_drift = None
    else:
        relative_amplitude = (max(values) - min(values)) / abs(mean)
        relative_drift = slope * span / abs(mean)
    cycles = None
    if period_iterations not in (None, 0.0) and span > 0.0:
        cycles = span / period_iterations
    return {
        "n": len(pairs),
        "iter_first": int(pairs[0][0]),
        "iter_last": int(pairs[-1][0]),
        "mean": mean,
        "std": std,
        "relative_amplitude": relative_amplitude,
        "slope_per_iteration": slope,
        "relative_drift": relative_drift,
        "linear_r2": r2,
        "dominant_period_iterations": period_iterations,
        "period_strength": strength,
        "cycles": cycles,
        "classification": _classify(
            relative_amplitude,
            relative_drift,
            r2,
            period_iterations,
            strength,
            cycles,
        ),
    }


def _classify(
    relative_amplitude,
    relative_drift,
    linear_r2,
    period_iterations,
    strength,
    cycles,
):
    periodic = (
        relative_amplitude is not None
        and relative_amplitude >= RELATIVE_AMPLITUDE_MIN
        and period_iterations is not None
        and period_iterations >= MIN_PERIOD_ITERATIONS
        and cycles is not None
        and cycles >= MIN_CYCLES
        and strength >= PERIOD_STRENGTH_MIN
    )
    if periodic:
        return CLASS_PERIODIC
    drifting = (
        relative_drift is not None
        and abs(relative_drift) >= RELATIVE_DRIFT_MIN
        and linear_r2 >= DRIFT_R2_MIN
    )
    if drifting:
        return CLASS_DRIFTING
    return CLASS_IRREGULAR


def read_report_file(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_fluent_report_file_series(text)


def read_continuity(leaf):
    """Continuity column from the selected solve transcript, or None."""
    path, status, detail = select_solve_transcript(Path(leaf))
    if path is None:
        return None, None, f"{status}: {detail}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, path, f"{type(exc).__name__}: {exc}"
    rows, table_detail = parse_residual_table(text)
    if not rows:
        return None, path, table_detail
    series = [(int(row["iter"]), float(row["continuity"])) for row in rows]
    return series, path, table_detail


def load_leaf_series(leaf):
    """Named series present on the run leaf. Raises when none are readable."""
    directory = Path(leaf)
    if not directory.is_dir():
        raise FileNotFoundError(f"Run leaf is not a directory: {directory}")
    series = {}
    notes = []
    for name, filename in REPORT_FILES:
        path = directory / filename
        if not path.is_file():
            notes.append(f"{filename} is not in the run leaf.")
            continue
        parsed = read_report_file(path)
        if not parsed:
            raise ValueError(f"{path} has no iteration/value rows.")
        series[name] = parsed
    continuity, transcript, detail = read_continuity(directory)
    if continuity is None:
        notes.append(f"continuity: {detail}")
    else:
        series["continuity"] = continuity
        notes.append(f"continuity transcript: {transcript} ({detail})")
    if not series:
        raise FileNotFoundError(
            f"Run leaf {directory} has no QoI report file and no residual table."
        )
    return series, notes


def _format_number(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return format(float(value), ".12g")


def render_markdown(leaf, last_n, analyses, notes):
    lines = [
        "# Monitor periodicity",
        "",
        f"Run leaf: `{leaf}`",
        "",
        f"Window: last {last_n} iterations of each series "
        f"(fewer when the file is shorter, and at least {MIN_POINTS}).",
        "",
        "Thresholds, applied in this order:",
        "",
        f"- **periodic**: peak-to-background >= {PERIOD_STRENGTH_MIN:g}, "
        f"period >= {MIN_PERIOD_ITERATIONS:g} iterations and at most half "
        f"the window, at least {MIN_CYCLES:g} cycles, relative amplitude "
        f">= {RELATIVE_AMPLITUDE_MIN:g}.",
        f"- **drifting**: not periodic, |relative drift| >= "
        f"{RELATIVE_DRIFT_MIN:g}, linear R^2 >= {DRIFT_R2_MIN:g}.",
        "- **irregular**: neither. A flat series is irregular.",
        "",
        "Relative amplitude is `(max - min) / |mean|`. "
        "Relative drift is `slope_per_iteration * (iter_last - iter_first) / |mean|`. "
        "Standard deviation is the sample standard deviation. "
        "The period is the peak of the periodogram of the linear residual, "
        "converted to iterations with the mean spacing of the window. "
        "Strength is that peak divided by the median of the other bins.",
        "",
        "Sources:",
        "",
        f"- `{LMH_REPORT_FILE}` and `{PRESSURE_REPORT_FILE}` in the case "
        "directory (`configs/run_config.py` report-file names; "
        "`scripts/solver_code_260616.py` `ensure_lmh_udm_avg_report_file`).",
        "- `continuity` from the residual table in the solve transcript "
        "(`src/ro/residual_transcript.py` `select_solve_transcript`).",
        "",
    ]
    if notes:
        lines.append("Notes:")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(
        [
            "| series | class | n | mean | std | relative amplitude | "
            "relative drift | R^2 | period (iterations) | strength |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for name, stats in analyses:
        lines.append(
            "| {name} | {classification} | {n} | {mean} | {std} | "
            "{relative_amplitude} | {relative_drift} | {linear_r2} | "
            "{period} | {strength} |".format(
                name=name,
                classification=stats["classification"],
                n=stats["n"],
                mean=_format_number(stats["mean"]),
                std=_format_number(stats["std"]),
                relative_amplitude=_format_number(stats["relative_amplitude"]),
                relative_drift=_format_number(stats["relative_drift"]),
                linear_r2=_format_number(stats["linear_r2"]),
                period=_format_number(stats["dominant_period_iterations"]),
                strength=_format_number(stats["period_strength"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(leaf, out_dir, last_n=DEFAULT_LAST):
    """Write ``periodicity.md`` and ``periodicity_series.csv``. Return both paths."""
    directory = Path(leaf)
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    loaded, notes = load_leaf_series(directory)
    analyses = []
    csv_rows = []
    for name, pairs in loaded.items():
        window = last_window(pairs, last_n)
        if len(window) < len(_unique_sorted(pairs)):
            notes.append(
                f"{name}: last {len(window)} of "
                f"{len(_unique_sorted(pairs))} iterations."
            )
        elif len(window) < last_n:
            notes.append(f"{name}: file has {len(window)} iterations.")
        stats = analyze_series(window)
        analyses.append((name, stats))
        for iteration, value in window:
            csv_rows.append(
                {"series": name, "iteration": iteration, "value": _format_number(value)}
            )
    markdown_path = destination / _MARKDOWN_NAME
    csv_path = destination / _CSV_NAME
    markdown_path.write_text(
        render_markdown(directory, last_n, analyses, notes),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("series", "iteration", "value"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    return markdown_path, csv_path


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Classify steady-solver monitor history on one run leaf "
            "as periodic, drifting, or irregular."
        ),
    )
    parser.add_argument("leaf", help="Run leaf directory.")
    parser.add_argument(
        "--last",
        type=int,
        default=DEFAULT_LAST,
        help=f"Iterations at the end of each series (default {DEFAULT_LAST}).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory for periodicity.md and periodicity_series.csv. "
        "Default: <leaf>/post/reports.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    leaf = Path(args.leaf)
    out_dir = Path(args.out) if args.out else leaf / "post" / "reports"
    markdown_path, csv_path = write_outputs(leaf, out_dir, args.last)
    print(markdown_path.read_text(encoding="utf-8"))
    print(f"Wrote {markdown_path}")
    print(f"Wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
