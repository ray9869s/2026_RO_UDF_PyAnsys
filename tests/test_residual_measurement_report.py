"""Tests for residual measurement report (SAFE, no Fluent)."""

from __future__ import annotations

import math
import os
from pathlib import Path, PureWindowsPath

import pytest

from helpers import POST_DIR, load_module

HEADER = (
    "iter  continuity  x-velocity  y-velocity  z-velocity  nacl  "
    "lmh  m_out  m_in  area_mem  time/iter"
)


@pytest.fixture(scope="module")
def residual_mod():
    return load_module(
        "residual_transcript_under_test",
        POST_DIR / "_residual_transcript.py",
    )


@pytest.fixture(scope="module")
def report_mod():
    return load_module(
        "residual_measurement_report_under_test",
        POST_DIR / "09_residual_measurement_report.py",
    )


def make_row(
    it: int,
    continuity: float,
    *,
    x: float = 1e-10,
    y: float = 1e-10,
    z: float = 1e-10,
    nacl: float = 1e-10,
    lmh: float = 100.0,
    m_out: float = -1e-4,
    m_in: float = 2e-4,
    area_mem: float = 6e-5,
    clock: str = "0:00:01",
    remaining: int | None = 100,
) -> str:
    parts = [
        f"{it}",
        f"{continuity:.4e}",
        f"{x:.4e}",
        f"{y:.4e}",
        f"{z:.4e}",
        f"{nacl:.4e}",
        f"{lmh:.4e}",
        f"{m_out:.4e}",
        f"{m_in:.4e}",
        f"{area_mem:.4e}",
        clock,
    ]
    if remaining is not None:
        parts.append(str(remaining))
    return "  " + "  ".join(parts)


def build_transcript(rows: list[str], *, page_every: int | None = None) -> str:
    lines = [HEADER]
    for i, row in enumerate(rows, start=1):
        if page_every and i > 1 and (i - 1) % page_every == 0:
            lines.append(HEADER)
        lines.append(row)
    return "\n".join(lines) + "\n"


def series_to_rows(values: list[float], start_iter: int = 1) -> list[str]:
    return [
        make_row(start_iter + i, v, nacl=v, lmh=50.0 + 0.001 * i, m_out=-1.0e-4)
        for i, v in enumerate(values)
    ]


class TestPositionalParse:
    def test_twelve_field_row(self, residual_mod):
        line = make_row(
            1,
            1.0,
            x=3.1351e-03,
            y=7.4514e-04,
            z=4.0441e-04,
            nacl=0.0,
            lmh=4.3594e03,
            m_out=-1.9090e-04,
            m_in=2.6629e-04,
            area_mem=6.2423e-05,
            clock="0:13:30",
            remaining=199,
        )
        row = residual_mod.parse_residual_data_row(line)
        assert row is not None
        assert row["iter"] == 1
        assert row["continuity"] == pytest.approx(1.0)
        assert row["nacl"] == pytest.approx(0.0)
        assert row["lmh"] == pytest.approx(4359.4)
        assert row["remaining_iters"] == 199

    def test_reject_wrong_field_count(self, residual_mod):
        assert residual_mod.parse_residual_data_row("1 1.0 2.0") is None

    def test_reject_non_clock_token(self, residual_mod):
        bad = make_row(1, 1e-6).replace("0:00:01", "not_a_clock")
        assert residual_mod.parse_residual_data_row(bad) is None

    def test_iteration_one_zeros_not_target_met(self, residual_mod):
        rows_text = [
            make_row(1, 1.0, nacl=0.0),
            make_row(2, 3.15e-7, nacl=2.82e-7),
        ]
        text = build_transcript(rows_text)
        rows, _ = residual_mod.parse_residual_table(text)
        measured = residual_mod.measure_case_from_rows(
            rows, window_n=200, residual_target=1e-7, iteration_cap=1000
        )
        assert measured["continuity_target_met"] is False  # final 3.15e-7 > 1e-7
        # Force final nacl=0
        rows[-1]["nacl"] = 0.0
        measured0 = residual_mod.measure_case_from_rows(
            rows, window_n=200, residual_target=1e-7, iteration_cap=1000
        )
        assert measured0["nacl_target_met"] is False
        assert measured0["nacl_shortfall_factor"] == pytest.approx(0.0)


class TestHeaderPagingAndMalformed:
    def test_repeated_headers_skipped(self, residual_mod):
        values = [1e-6 - i * 1e-9 for i in range(40)]
        text = build_transcript(series_to_rows(values), page_every=13)
        rows, detail = residual_mod.parse_residual_table(text)
        assert len(rows) == 40
        assert "parsed_iters=40" in detail

    def test_malformed_mid_table_skipped(self, residual_mod):
        rows = series_to_rows([1e-6] * 5)
        # Digit-leading but wrong arity / no clock → positional reject.
        bad = "  99  1.0000e-06  1.0000e-06"
        text = HEADER + "\n" + rows[0] + "\n" + bad + "\n" + "\n".join(rows[1:]) + "\n"
        parsed, detail = residual_mod.parse_residual_table(text)
        assert len(parsed) == 5
        assert "skipped_malformed_rows=" in detail


class TestTrendClassification:
    def test_flat_plateau(self, residual_mod):
        values = [3.15e-7] * 200
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values)
        assert trend["trend"] == residual_mod.TREND_FLAT_PLATEAU
        assert trend["trend_reason"] == residual_mod.REASON_FLAT_ENDPOINT

    def test_still_descending(self, residual_mod):
        # Exact log-linear in float log-space; after detrend acf1 << old ~0.99.
        values = [10 ** (-5 - 0.005 * i) for i in range(200)]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert trend["trend"] == residual_mod.TREND_STILL_DESCENDING
        assert trend["trend_reason"] == residual_mod.REASON_DOMINANT_DESCENT
        assert abs(trend["acf1"]) < 0.35

    def test_pure_log_linear_detrended_acf1_near_zero(self, residual_mod):
        values = [10 ** (-4 - 0.002 * i) for i in range(200)]
        iters = list(range(1, 201))
        fit = residual_mod.fit_log10_line(iters, values)
        assert fit is not None
        _a, _b, e, _ui, _uv = fit
        # Contrast with pre-fix mean-detrended raw series (~0.99).
        raw_acf = residual_mod.lag1_acf([math.log10(v) for v in values])
        assert raw_acf is not None and raw_acf > 0.9
        assert abs(residual_mod.lag1_acf(e)) < 0.35
        assert residual_mod.lag1_acf([0.0] * 200) == pytest.approx(0.0, abs=1e-12)
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert abs(trend["acf1"]) < 0.35
        assert trend["trend"] == residual_mod.TREND_STILL_DESCENDING

    def test_dominant_descent_not_oscillating_despite_raw_crossings(self, residual_mod):
        # Large descent (|slope|*n >> 0.5) must win even if raw series crosses mean often.
        values = [10 ** (-3 - 0.008 * i) for i in range(200)]
        iters = list(range(1, 201))
        assert residual_mod.mean_crossing_count(values) >= 1
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        decades = abs(trend["log10_slope"]) * 200
        assert decades >= 0.5
        assert trend["trend"] == residual_mod.TREND_STILL_DESCENDING
        assert trend["trend_reason"] == residual_mod.REASON_DOMINANT_DESCENT

    def test_oscillating_coherent_sinusoid(self, residual_mod):
        period = 40
        base = 3e-7
        amp = 1e-7
        values = [
            base + amp * math.sin(2 * math.pi * i / period) for i in range(200)
        ]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert trend["trend"] == residual_mod.TREND_OSCILLATING_COHERENT
        assert trend["trend_reason"] == residual_mod.REASON_COHERENT_OSCILLATION
        assert trend["acf1"] > 0.5
        assert trend["sign_flip_frac"] < 0.15
        assert trend["rel_spread"] > 0.05
        assert trend["mean_crossings"] >= 4

    def test_oscillating_precedes_weak_spurious_slope(self, residual_mod):
        # Non-integer periods can yield |slope| > 2.3e-4 but << dominant; osc must win.
        period = 37
        base = 3e-7
        amp = 1.2e-7
        values = [
            base + amp * math.sin(2 * math.pi * i / period) for i in range(200)
        ]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert abs(trend["log10_slope"]) * 200 < 0.5
        assert trend["trend"] == residual_mod.TREND_OSCILLATING_COHERENT

    def test_noisy_plateau_shuffled_sinusoid(self, residual_mod):
        period = 40
        base = 3e-7
        amp = 1e-7
        values = [
            base + amp * math.sin(2 * math.pi * i / period) for i in range(200)
        ]
        shuffled = [values[(i * 47) % 200] for i in range(200)]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, shuffled, window_iters_used=200)
        assert trend["trend"] == residual_mod.TREND_NOISY_PLATEAU
        assert trend["rel_spread"] > 0.05

    def test_small_amplitude_jitter_is_flat(self, residual_mod):
        values = [3.15e-7 * (1.0 + 1e-4 * ((-1) ** i)) for i in range(200)]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert trend["trend"] == residual_mod.TREND_FLAT_PLATEAU
        assert trend["rel_spread"] <= 0.05

    def test_weak_rising(self, residual_mod):
        # ~0.06 decades over 200 iters: above SLOPE_MAG, below dominant.
        values = [10 ** (-6 + 0.0003 * i) for i in range(200)]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert trend["log10_slope"] >= residual_mod.SLOPE_MAG
        assert abs(trend["log10_slope"]) * 200 < 0.5
        assert trend["trend"] == residual_mod.TREND_RISING
        assert trend["trend_reason"] == residual_mod.REASON_SLOPE_RISING

    def test_dead_zone_not_unknown(self, residual_mod):
        # Mild spread / mid flip / weak slope must not fall through to unknown.
        values = [
            3.15e-7 * (1.0 + 0.04 * math.sin(2 * math.pi * i / 17.0) + 0.02 * ((-1) ** i))
            for i in range(200)
        ]
        iters = list(range(1, 201))
        trend = residual_mod.classify_residual_trend(iters, values, window_iters_used=200)
        assert trend["trend"] != residual_mod.TREND_UNKNOWN
        assert trend["trend"] in {
            residual_mod.TREND_NOISY_PLATEAU,
            residual_mod.TREND_FLAT_PLATEAU,
            residual_mod.TREND_OSCILLATING_COHERENT,
            residual_mod.TREND_RISING,
            residual_mod.TREND_STILL_DESCENDING,
        }

    def test_short_series_unknown(self, residual_mod):
        trend = residual_mod.classify_residual_trend(list(range(10)), [1e-6] * 10)
        assert trend["trend"] == residual_mod.TREND_UNKNOWN
        assert trend["trend_reason"] == residual_mod.REASON_INSUFFICIENT_POINTS

    def test_history_bottom_then_rise(self, residual_mod):
        # Descend for iters 1..100, then rise — min at iter 100; endpoint rising.
        values = []
        for i in range(200):
            if i < 100:
                values.append(10 ** (-4 - 0.02 * i))
            else:
                values.append(10 ** (-4 - 0.02 * 99 + 0.01 * (i - 99)))
        rows = []
        for i, v in enumerate(values, start=1):
            rows.append(
                {
                    "iter": i,
                    "continuity": v,
                    "x-velocity": 1e-10,
                    "y-velocity": 1e-10,
                    "z-velocity": 1e-10,
                    "nacl": v,
                    "lmh": 50.0,
                    "m_out": -1e-4,
                    "m_in": 2e-4,
                    "area_mem": 6e-5,
                }
            )
        measured = residual_mod.measure_case_from_rows(
            rows, window_n=50, residual_target=1e-7, iteration_cap=1000
        )
        assert measured["continuity_iter_of_min"] == 100
        assert measured["continuity_min_value"] == pytest.approx(values[99])
        assert measured["continuity_decades_dropped_total"] is not None
        assert measured["continuity_trend"] == residual_mod.TREND_RISING
        assert measured["continuity_mean_crossings"] is not None
        assert measured["continuity_trend_reason"] in {
            residual_mod.REASON_SLOPE_RISING,
            residual_mod.REASON_DOMINANT_RISE,
        }


class TestSelectionAndRobustness:
    def test_prefer_solver_log_over_newer_post_trn(self, residual_mod, tmp_path):
        case_dir = tmp_path / "Sin_ST" / "u0p1_p4M__mesh"
        case_dir.mkdir(parents=True)
        solve_rows = series_to_rows([3e-7] * 50, start_iter=1)
        (case_dir / "solver_log_u0p1_p4M__mesh.txt").write_text(
            build_transcript(solve_rows), encoding="utf-8"
        )
        # Older solve .trn with fewer iters
        (case_dir / "fluent-20260716-231050-40396.trn").write_text(
            build_transcript(series_to_rows([3e-7] * 30)), encoding="utf-8"
        )
        # Newest post-process .trn with no residual table
        post = case_dir / "fluent-20260720-120000-99999.trn"
        post.write_text("Loading CFF\nNo residuals here\n", encoding="utf-8")
        # Make post newest
        os.utime(post, (9_999_999_999, 9_999_999_999))

        chosen, status, detail = residual_mod.select_solve_transcript(case_dir)
        assert status == residual_mod.PARSE_OK
        assert chosen is not None
        assert chosen.name.startswith("solver_log_")

    def test_fallback_largest_max_iter(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        case_dir.mkdir(parents=True)
        (case_dir / "fluent-old.trn").write_text(
            build_transcript(series_to_rows([1e-6] * 20, start_iter=1)),
            encoding="utf-8",
        )
        (case_dir / "fluent-new.trn").write_text(
            build_transcript(series_to_rows([1e-6] * 80, start_iter=1)),
            encoding="utf-8",
        )
        chosen, status, _ = residual_mod.select_solve_transcript(case_dir)
        assert status == residual_mod.PARSE_OK
        assert chosen is not None
        assert chosen.name == "fluent-new.trn"

    def test_no_residual_table(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        case_dir.mkdir(parents=True)
        (case_dir / "fluent-post.trn").write_text("post only\n", encoding="utf-8")
        record = residual_mod.measure_case_dir(
            case_dir,
            "geo",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record["parse_status"] == residual_mod.PARSE_NO_RESIDUAL_TABLE
        assert record["continuity_final"] is None

    def test_missing_transcript(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        case_dir.mkdir(parents=True)
        record = residual_mod.measure_case_dir(
            case_dir,
            "geo",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record["parse_status"] == residual_mod.PARSE_NO_TRANSCRIPT

    def test_encoding_garbage_does_not_raise(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        case_dir.mkdir(parents=True)
        payload = (
            HEADER.encode("utf-8")
            + b"\n"
            + make_row(1, 1e-6).encode("utf-8")
            + b"\n"
            + b"\xff\xfe bad bytes \n"
            + make_row(2, 2e-7).encode("utf-8")
            + b"\n"
        )
        (case_dir / "solver_log_case.txt").write_bytes(payload)
        record = residual_mod.measure_case_dir(
            case_dir,
            "geo",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record["parse_status"] == residual_mod.PARSE_OK
        assert record["iterations_completed"] == 2

    def test_nacl_all_zero_note(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        case_dir.mkdir(parents=True)
        rows = [
            make_row(i, 3e-7, nacl=0.0) for i in range(1, 50)
        ]
        (case_dir / "solver_log_case.txt").write_text(
            build_transcript(rows), encoding="utf-8"
        )
        record = residual_mod.measure_case_dir(
            case_dir,
            "geo",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert "nacl residual is 0.0" in record["parse_detail"]
        assert record["nacl_target_met"] is False

    def test_summary_wide_carry(self, residual_mod, tmp_path):
        case_dir = tmp_path / "geo" / "case"
        reports = case_dir / "post" / "reports"
        reports.mkdir(parents=True)
        rows = [make_row(i, 3e-7) for i in range(1, 30)]
        (case_dir / "solver_log_case.txt").write_text(
            build_transcript(rows), encoding="utf-8"
        )
        (reports / "summary_metrics_wide.csv").write_text(
            "geo_name,case_name,mass_balance_relative_error,lmh_mass_balance,"
            "boundary_permeate_mass_flow,mass_balance_error_boundary_minus_total_sink\n"
            "geo,case,0.012,40.5,1.2e-6,1.1e-8\n",
            encoding="utf-8-sig",
        )
        record = residual_mod.measure_case_dir(
            case_dir,
            "geo",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record["summary_metrics_wide_status"] == "ok"
        assert record["mass_balance_relative_error"] == pytest.approx(0.012)
        assert record["lmh_mass_balance"] == pytest.approx(40.5)
        assert "mass_imbalance" not in record

    def test_cross_platform_paths(self, residual_mod, monkeypatch, tmp_path):
        # Must not instantiate PosixPath; PureWindowsPath string shapes work on any OS.
        win_like = PureWindowsPath("C:/PyFluent/My_CFD_Project/03_Results/Sin_ST/case")
        assert "Sin_ST" in win_like.as_posix()
        monkeypatch.setattr(os, "name", "nt")
        case_dir = tmp_path / "Sin_ST" / "case"
        case_dir.mkdir(parents=True)
        (case_dir / "solver_log_case.txt").write_text(
            build_transcript([make_row(i, 3e-7) for i in range(1, 25)]),
            encoding="utf-8",
        )
        record = residual_mod.measure_case_dir(
            case_dir,
            "Sin_ST",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record["parse_status"] == residual_mod.PARSE_OK
        monkeypatch.setattr(os, "name", "posix")
        record2 = residual_mod.measure_case_dir(
            case_dir,
            "Sin_ST",
            "case",
            window_n=200,
            residual_target=1e-7,
            iteration_cap=1000,
        )
        assert record2["parse_status"] == residual_mod.PARSE_OK


class TestCliEndToEnd:
    def test_cli_writes_report(self, report_mod, residual_mod, tmp_path):
        results = tmp_path / "03_Results"
        case_dir = results / "Sin_ST" / "u0p1_p4M__mesh"
        case_dir.mkdir(parents=True)
        values = [3.15e-7] * 60
        (case_dir / "solver_log_u0p1.txt").write_text(
            build_transcript(series_to_rows(values)), encoding="utf-8"
        )
        out = tmp_path / "_inventory"
        rc = report_mod.main(
            [
                "--results-root",
                str(results),
                "--output-dir",
                str(out),
                "--window",
                "50",
                "--max-iter",
                "1000",
                "--residual-target",
                "1e-7",
            ]
        )
        assert rc == 0
        csv_path = out / "residual_measurement.csv"
        summary_path = out / "residual_measurement_summary.txt"
        assert csv_path.is_file()
        text = summary_path.read_text(encoding="utf-8")
        assert "oscillating_coherent is a CANDIDATE" in text
        assert "Does NOT update convergence_status" in text
        content = csv_path.read_text(encoding="utf-8")
        assert "continuity_shortfall_factor" in content
        assert "continuity_trend_reason" in content
        assert "continuity_mean_crossings" in content
        assert "continuity_decades_dropped_total" in content
        assert "continuity_iter_of_min" in content
        assert "mass_imbalance" not in content
        assert "m_in_is_constant_monitor" in content
        cli_src = (POST_DIR / "09_residual_measurement_report.py").read_text(encoding="utf-8")
        assert "ENDPOINT window" in cli_src
        assert ">=500" in cli_src
