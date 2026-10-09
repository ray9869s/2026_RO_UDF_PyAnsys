"""Synthetic monitor series for monitor_periodicity. No Fluent."""

from __future__ import annotations

import csv
import math

import pytest

from helpers import SCRIPTS_DIR, load_module

monitor = load_module(
    "monitor_periodicity_under_test",
    SCRIPTS_DIR / "mfbo" / "monitor_periodicity.py",
)


def _sine(period, count, mean=1.0, amplitude=0.2, start=1):
    return [
        (
            start + index,
            mean + amplitude * math.sin(2.0 * math.pi * index / period),
        )
        for index in range(count)
    ]


def _ramp(count):
    return [(index, index / (count - 1)) for index in range(count)]


def test_sine_is_periodic_with_the_planted_period():
    stats = monitor.analyze_series(_sine(25, 200))
    assert stats["classification"] == monitor.CLASS_PERIODIC
    assert stats["dominant_period_iterations"] == pytest.approx(25.0, rel=0.02)
    assert stats["period_strength"] >= monitor.PERIOD_STRENGTH_MIN
    assert stats["relative_amplitude"] == pytest.approx(0.4, rel=0.02)
    assert stats["mean"] == pytest.approx(1.0, abs=1e-9)


def test_ramp_is_drifting():
    count = 80
    stats = monitor.analyze_series(_ramp(count))
    assert stats["classification"] == monitor.CLASS_DRIFTING
    assert stats["linear_r2"] == pytest.approx(1.0)
    assert stats["relative_drift"] == pytest.approx(2.0)
    assert stats["slope_per_iteration"] == pytest.approx(1.0 / (count - 1))


def test_flat_series_is_irregular():
    stats = monitor.analyze_series([(index, 0.54) for index in range(1, 41)])
    assert stats["classification"] == monitor.CLASS_IRREGULAR
    assert stats["std"] == pytest.approx(0.0)
    assert stats["relative_amplitude"] == pytest.approx(0.0)
    assert stats["relative_drift"] == pytest.approx(0.0)


def test_last_window_keeps_the_tail():
    pairs = [(index, float(index)) for index in range(1, 21)]
    window = monitor.last_window(pairs, 16)
    assert [iteration for iteration, _value in window] == list(range(5, 21))
    with pytest.raises(ValueError, match="at least 16"):
        monitor.last_window(pairs, 4)


def _residual_transcript(rows):
    header = (
        "iter continuity x-velocity y-velocity z-velocity nacl "
        "lmh m_out m_in area_mem time/iter"
    )
    lines = [header]
    for iteration, continuity in rows:
        lines.append(
            f"{iteration} {continuity:.8g} 1e-4 1e-4 1e-4 1e-4 "
            "10 0.1 0.2 0.001 0:00:01"
        )
    return "\n".join(lines) + "\n"


def test_run_leaf_reads_the_report_file_and_the_transcript(tmp_path):
    leaf = tmp_path / "runs" / "pillar" / "P_p100_h00" / "mesh" / "u0p3_p6M"
    leaf.mkdir(parents=True)
    continuity = _sine(10, 40, mean=0.5, amplitude=0.05)
    (leaf / "solver_log_u0p3.txt").write_text(
        _residual_transcript(continuity),
        encoding="utf-8",
    )
    lmh = _sine(20, 40, mean=25.0, amplitude=0.0)
    body = ['"lmh_udm_avg"']
    body.extend(f"{iteration} {value:.8g}" for iteration, value in lmh)
    (leaf / monitor.LMH_REPORT_FILE).write_text("\n".join(body) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    markdown, table = monitor.write_outputs(leaf, out, last_n=500)
    text = markdown.read_text(encoding="utf-8")
    assert "periodic" in text
    assert "irregular" in text
    assert "lmh_udm_avg.out" in text
    assert "select_solve_transcript" in text
    assert "peak-to-background >= 8" in text
    with table.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_series = {}
    for row in rows:
        by_series.setdefault(row["series"], []).append(int(row["iteration"]))
    assert by_series["continuity"] == [iteration for iteration, _value in continuity]
    assert by_series["lmh_udm_avg"] == [iteration for iteration, _value in lmh]
    assert "pressure_drop_spacer" not in by_series
    assert "pressure_drop_spacer.out is not in the run leaf." in text


def test_missing_monitors_raise(tmp_path):
    leaf = tmp_path / "empty-leaf"
    leaf.mkdir()
    with pytest.raises(FileNotFoundError, match="no QoI report file"):
        monitor.write_outputs(leaf, tmp_path / "out")
