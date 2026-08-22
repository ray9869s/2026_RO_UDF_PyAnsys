"""Parity tests for _solver_common convergence_pure helpers (phase 4)."""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from helpers import SCRIPTS_DIR, load_module, load_solver_common

TARGET_NAMES = {"continuity", "x-velocity", "y-velocity", "z-velocity", "nacl"}
STRICT_TARGETS = {
    "continuity": 1e-7,
    "x-velocity": 1e-7,
    "y-velocity": 1e-7,
    "z-velocity": 1e-7,
    "nacl": 1e-7,
}

TRANSCRIPT_CONVERGED = """
 iter  continuity  x-velocity  y-velocity  z-velocity  nacl
 100  1.0e-6      1.0e-6      1.0e-6      1.0e-6      1.0e-6
 200  5.0e-8      5.0e-8      5.0e-8      5.0e-8      5.0e-8
"""

TRANSCRIPT_PLATEAU = """
 iter  continuity  x-velocity  y-velocity  z-velocity  nacl
 300  1.6e-7      1.6e-7      1.6e-7      1.6e-7      1.6e-7
 400  1.6e-7      1.6e-7      1.6e-7      1.6e-7      1.6e-7
 500  1.6e-7      1.6e-7      1.6e-7      1.6e-7      1.6e-7
"""

TRANSCRIPT_DIVERGED = """
 iter  continuity  x-velocity  y-velocity  z-velocity  nacl
 100  1.0e-4      1.0e-4      1.0e-4      1.0e-4      1.0e-4
 200  1.0e-2      1.0e-2      1.0e-2      1.0e-2      1.0e-2
"""

TRANSCRIPT_GARBLED = """
This is not a residual table
bad line without numbers
"""


def legacy_blending_ramp_values(start: float, end: float, steps: int) -> list[float]:
    if steps <= 1:
        return [float(end)]
    delta = (float(end) - float(start)) / float(steps - 1)
    return [float(start) + delta * index for index in range(steps)]


def legacy_parse_transcript_residual_columns(header_line: str) -> list[str] | None:
    tokens = header_line.split()
    if not tokens:
        return None
    lowered = [token.lower() for token in tokens]
    if "continuity" not in lowered:
        return None
    return lowered


def legacy_parse_residuals_from_transcript_text(text: str, target_names: set[str]) -> dict[str, float]:
    latest: dict[str, float] = {}
    columns: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "continuity" in line.lower() and not line[0].isdigit():
            candidate_columns = legacy_parse_transcript_residual_columns(line)
            if candidate_columns:
                columns = candidate_columns
            continue
        if columns is None:
            continue
        tokens = line.split()
        if not tokens:
            continue
        try:
            int(tokens[0])
        except ValueError:
            continue
        row_values: dict[str, float] = {}
        for name, token in zip(columns[1:], tokens[1:]):
            if name not in target_names:
                continue
            try:
                row_values[name] = float(token)
            except ValueError:
                row_values = {}
                break
        if row_values:
            latest.update(row_values)
    return latest


def legacy_detect_residual_plateau(
    history: list[dict[str, Any]],
    strict_targets: dict[str, float],
    window_chunks: int,
    rel_change_tol: float,
    min_above_target_factor: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {"detected": False, "reason": "", "per_equation": {}}
    if not strict_targets:
        result["reason"] = "No residual targets available for plateau assessment."
        return result
    if len(history) < window_chunks:
        result["reason"] = f"Fewer than {window_chunks} chunks completed; plateau assessment deferred."
        return result

    recent = history[-window_chunks:]
    stuck_equations: list[str] = []
    per_equation: dict[str, Any] = {}
    for name, target in strict_targets.items():
        series = [snap.get("residual_numeric", {}).get(name) for snap in recent]
        series = [float(v) for v in series if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(series) < window_chunks:
            per_equation[name] = {"status": "insufficient_data", "series": series}
            continue

        above_target = series[-1] > target * min_above_target_factor
        mean_abs = sum(abs(v) for v in series) / len(series)
        rel_change = (max(series) - min(series)) / mean_abs if mean_abs > 0.0 else 0.0
        flat = rel_change <= rel_change_tol
        per_equation[name] = {
            "series": series,
            "latest": series[-1],
            "target": target,
            "above_target": above_target,
            "rel_change": rel_change,
            "flat": flat,
        }
        if above_target and flat:
            stuck_equations.append(name)

    result["per_equation"] = per_equation
    if stuck_equations:
        result["detected"] = True
        result["reason"] = (
            f"{stuck_equations} remained above {min_above_target_factor}x target with "
            f"<= {rel_change_tol} relative change over the last {window_chunks} chunks."
        )
    else:
        result["reason"] = "No plateau detected."
    return result


def legacy_assess_history(
    history: list[dict[str, Any]],
    args: argparse.Namespace,
    value_groups: tuple[str, ...] = ("residual_numeric", "report_values"),
    require_two_samples: bool = False,
) -> dict[str, Any]:
    assessment: dict[str, Any] = {
        "status": "MONITORS_UNAVAILABLE",
        "diverged": False,
        "stable": False,
        "bounded_not_converged": False,
        "details": "",
        "value_groups": list(value_groups),
    }
    series: dict[str, list[float]] = {}
    for snapshot in history:
        for group_name in value_groups:
            values = snapshot.get(group_name, {})
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    series.setdefault(f"{group_name}:{name}", []).append(float(value))

    if not series:
        assessment["details"] = (
            f"No numeric values were available for monitor groups {list(value_groups)}."
        )
        return assessment

    worst_growth = 0.0
    max_rel_variation = 0.0
    comparable_series_count = 0
    for name, values in series.items():
        if len(values) < 2:
            continue
        comparable_series_count += 1
        first = abs(values[0])
        last = abs(values[-1])
        if first > 0.0:
            worst_growth = max(worst_growth, last / first)
        window_values = values[-max(2, min(len(values), args.monitor_window)) :]
        mean_abs = sum(abs(v) for v in window_values) / len(window_values)
        if mean_abs > 0.0:
            rel_variation = (max(window_values) - min(window_values)) / mean_abs
            max_rel_variation = max(max_rel_variation, abs(rel_variation))
        print(f"Assessment series {name}: first={values[0]} last={values[-1]}")

    if require_two_samples and comparable_series_count == 0:
        assessment["details"] = (
            f"At least two numeric samples are required for monitor groups "
            f"{list(value_groups)}."
        )
        return assessment

    assessment["worst_growth"] = worst_growth
    assessment["max_rel_variation"] = max_rel_variation
    assessment["comparable_series_count"] = comparable_series_count

    if worst_growth > args.residual_growth_limit:
        assessment["status"] = "DIVERGED"
        assessment["diverged"] = True
        assessment["details"] = (
            f"Residual/report growth {worst_growth:.3g} exceeded limit "
            f"{args.residual_growth_limit:.3g}."
        )
    elif max_rel_variation <= args.monitor_rel_tol:
        assessment["status"] = "STABLE"
        assessment["stable"] = True
        assessment["details"] = (
            f"Last-window relative variation {max_rel_variation:.3g} is within "
            f"{args.monitor_rel_tol:.3g}."
        )
    else:
        assessment["status"] = "BOUNDED_NOT_CONVERGED"
        assessment["bounded_not_converged"] = True
        assessment["details"] = (
            f"Monitor variation {max_rel_variation:.3g} remains above "
            f"{args.monitor_rel_tol:.3g}; bounded but not converged."
        )
    return assessment


def legacy_assess_residual_convergence(
    latest_residuals: dict[str, float],
    strict_targets: dict[str, float],
) -> dict[str, Any]:
    per_equation: dict[str, Any] = {}
    if not strict_targets:
        return {
            "strict_met": False,
            "data_available": False,
            "reason": "No residual convergence targets were available (criteria could not be read).",
            "per_equation": per_equation,
        }

    strict_met = True
    data_available = False
    missing: list[str] = []
    for name, target in strict_targets.items():
        value = latest_residuals.get(name)
        per_equation[name] = {"latest": value, "target": target}
        if value is None:
            missing.append(name)
            strict_met = False
            continue
        data_available = True
        if value > target:
            strict_met = False

    reason = f"Residual current value unavailable for: {missing}." if missing else ""
    return {
        "strict_met": strict_met,
        "data_available": data_available,
        "reason": reason,
        "per_equation": per_equation,
    }


def legacy_classify_convergence(
    monitor_assessment: dict[str, Any],
    residual_assessment: dict[str, Any],
    plateau_assessment: dict[str, Any],
) -> dict[str, Any]:
    if monitor_assessment.get("diverged"):
        return {"status": "DIVERGED", "details": monitor_assessment.get("details", "")}

    strict_met = bool(residual_assessment.get("strict_met"))

    if strict_met and monitor_assessment.get("stable"):
        return {
            "status": "STRICT_CONVERGED_ATTEMPT",
            "details": "Residual strict target met and report monitors are stable.",
        }

    if plateau_assessment.get("detected"):
        return {
            "status": "NOT_CONVERGED_RESIDUAL_PLATEAU",
            "details": plateau_assessment.get("reason", "Residual plateau detected above target."),
        }

    if monitor_assessment.get("stable"):
        return {
            "status": "NOT_CONVERGED_STABLE_MONITORS",
            "details": "Monitors stable but residuals did not meet the strict target.",
        }

    if monitor_assessment.get("bounded_not_converged"):
        return {
            "status": "NEEDS_TRANSIENT_REVIEW",
            "details": "Monitors bounded but oscillating, and residual target not met.",
        }

    return {
        "status": "COMPLETED_NEEDS_REVIEW",
        "details": "Monitor stability could not be determined from available data.",
    }


def make_assessment_args() -> argparse.Namespace:
    return argparse.Namespace(
        monitor_window=200,
        monitor_rel_tol=0.005,
        residual_growth_limit=100.0,
    )


def plateau_history() -> list[dict[str, Any]]:
    residual = {"continuity": 1.6e-7, "nacl": 1.6e-7}
    return [
        {"residual_numeric": dict(residual)},
        {"residual_numeric": dict(residual)},
        {"residual_numeric": dict(residual)},
    ]


def stable_report_history() -> list[dict[str, Any]]:
    return [
        {"report_values": {"lmh": 1.0}},
        {"report_values": {"lmh": 1.001}},
        {"report_values": {"lmh": 1.0005}},
    ]


@pytest.fixture(scope="module")
def common():
    return load_solver_common()


@pytest.fixture(scope="module")
def rerun07():
    return load_module("batch_solver_rerun_under_test", SCRIPTS_DIR / "batch_solver_rerun.py")


class TestBlendingRampValuesParity:
    @pytest.mark.parametrize(
        ("start", "end", "steps"),
        [(0.2, 1.0, 5), (0.2, 1.0, 1), (0.5, 0.5, 3)],
    )
    def test_matches_legacy(self, common, start, end, steps):
        assert common.blending_ramp_values(start, end, steps) == legacy_blending_ramp_values(
            start, end, steps
        )


class TestTranscriptParsingParity:
    @pytest.mark.parametrize(
        ("text", "expected_continuity"),
        [
            (TRANSCRIPT_CONVERGED, 5.0e-8),
            (TRANSCRIPT_PLATEAU, 1.6e-7),
            (TRANSCRIPT_DIVERGED, 1.0e-2),
            (TRANSCRIPT_GARBLED, None),
        ],
    )
    def test_parse_residuals(self, common, text, expected_continuity):
        legacy = legacy_parse_residuals_from_transcript_text(text, TARGET_NAMES)
        actual = common.parse_residuals_from_transcript_text(text, TARGET_NAMES)
        assert actual == legacy
        if expected_continuity is None:
            assert actual == {}
        else:
            assert actual["continuity"] == expected_continuity

    def test_parse_columns_header(self, common):
        header = " iter  continuity  x-velocity  nacl"
        assert (
            common.parse_transcript_residual_columns(header)
            == legacy_parse_transcript_residual_columns(header)
        )


class TestDetectResidualPlateauParity:
    def test_plateau_detected(self, common):
        kwargs = dict(
            history=plateau_history(),
            strict_targets={"continuity": 1e-7},
            window_chunks=3,
            rel_change_tol=0.02,
            min_above_target_factor=1.2,
        )
        assert common.detect_residual_plateau(**kwargs) == legacy_detect_residual_plateau(**kwargs)
        assert common.detect_residual_plateau(**kwargs)["detected"] is True

    def test_converged_history_no_plateau(self, common):
        residuals = legacy_parse_residuals_from_transcript_text(TRANSCRIPT_CONVERGED, TARGET_NAMES)
        history = [{"residual_numeric": residuals} for _ in range(3)]
        kwargs = dict(
            history=history,
            strict_targets=STRICT_TARGETS,
            window_chunks=3,
            rel_change_tol=0.02,
            min_above_target_factor=1.2,
        )
        assert common.detect_residual_plateau(**kwargs) == legacy_detect_residual_plateau(**kwargs)
        assert common.detect_residual_plateau(**kwargs)["detected"] is False


class TestAssessResidualConvergenceParity:
    def test_converged(self, common):
        residuals = legacy_parse_residuals_from_transcript_text(TRANSCRIPT_CONVERGED, TARGET_NAMES)
        assert common.assess_residual_convergence(residuals, STRICT_TARGETS) == legacy_assess_residual_convergence(
            residuals, STRICT_TARGETS
        )
        assert common.assess_residual_convergence(residuals, STRICT_TARGETS)["strict_met"] is True

    def test_missing_residual_data(self, common):
        assert common.assess_residual_convergence({}, STRICT_TARGETS) == legacy_assess_residual_convergence(
            {}, STRICT_TARGETS
        )
        assert common.assess_residual_convergence({}, STRICT_TARGETS)["strict_met"] is False

    def test_diverged_residuals(self, common):
        residuals = legacy_parse_residuals_from_transcript_text(TRANSCRIPT_DIVERGED, TARGET_NAMES)
        assert common.assess_residual_convergence(residuals, STRICT_TARGETS) == legacy_assess_residual_convergence(
            residuals, STRICT_TARGETS
        )
        assert common.assess_residual_convergence(residuals, STRICT_TARGETS)["strict_met"] is False


class TestAssessHistoryParity:
    def test_stable_reports(self, common):
        args = make_assessment_args()
        history = stable_report_history()
        assert common.assess_history(history, args, value_groups=("report_values",)) == legacy_assess_history(
            history, args, value_groups=("report_values",)
        )

    def test_diverged_growth(self, common):
        args = make_assessment_args()
        history = [
            {"report_values": {"lmh": 1.0}},
            {"report_values": {"lmh": 200.0}},
        ]
        assert common.assess_history(history, args, value_groups=("report_values",)) == legacy_assess_history(
            history, args, value_groups=("report_values",)
        )
        assert common.assess_history(history, args, value_groups=("report_values",))["diverged"] is True


class TestClassifyConvergenceParity:
    def test_strict_converged_attempt(self, common):
        monitor = {"stable": True, "diverged": False}
        residual = {"strict_met": True}
        plateau = {"detected": False}
        assert common.classify_convergence(monitor, residual, plateau) == legacy_classify_convergence(
            monitor, residual, plateau
        )
        assert common.classify_convergence(monitor, residual, plateau)["status"] == "STRICT_CONVERGED_ATTEMPT"

    def test_plateau_label(self, common):
        monitor = {"stable": True, "diverged": False}
        residual = {"strict_met": False}
        plateau = {"detected": True, "reason": "stuck"}
        assert common.classify_convergence(monitor, residual, plateau) == legacy_classify_convergence(
            monitor, residual, plateau
        )
        assert common.classify_convergence(monitor, residual, plateau)["status"] == "NOT_CONVERGED_RESIDUAL_PLATEAU"

    def test_diverged_label(self, common):
        monitor = {"diverged": True, "details": "boom"}
        residual = {"strict_met": False}
        plateau = {"detected": False}
        assert common.classify_convergence(monitor, residual, plateau) == legacy_classify_convergence(
            monitor, residual, plateau
        )


class TestRerun07ImportsSharedConvergence:
    def test_rerun_module_binds_shared_helpers(self, rerun07):
        from ro import solver_common as _solver_common

        for name in (
            "parse_residuals_from_transcript_text",
            "detect_residual_plateau",
            "assess_residual_convergence",
            "assess_history",
            "classify_convergence",
            "blending_ramp_values",
        ):
            assert getattr(rerun07, name) is getattr(_solver_common, name)
