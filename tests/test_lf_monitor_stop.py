"""LF monitor stop and gate. Does not launch Fluent."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import REPO_ROOT
from ro.convergence_quality import (
    evaluate_convergence_quality,
    manifest_quality_payload,
)
from ro.lf_monitor_stop import (
    evaluate_lf_monitor_gate,
    lf_monitor_decision,
    ramp_end_from_udf_source,
    run_lf_monitor_iterations,
)
from ro.mfbo_fidelity import (
    fidelity_settings_used,
    parse_fidelity_level,
    production_gate_thresholds,
    production_solver_convergence,
    run_id_for_level,
    solver_cli_args,
)
from ro.mfbo_fidelity_screen import load_fidelity_table
from ro.solver_common import (
    STOP_REASON_LF_MONITOR_STABLE,
    STOP_REASON_MAX_ITER_REACHED,
)

TABLE = REPO_ROOT / "configs" / "mfbo_fidelity_table.lf_short.example.json"
UDF = REPO_ROOT / "udfs" / "260822_RO_UDF.c"
RAMP = 150
N = 100


def _flat(start: int, end: int, value: float = 26.48):
    return [(iteration, value) for iteration in range(start, end + 1)]


def _decision(lmh, dp, **overrides):
    kwargs = {
        "ramp_end": RAMP,
        "n": N,
        "tol_lmh": 1.0e-3,
        "tol_dp": 5.0e-3,
    }
    kwargs.update(overrides)
    return lf_monitor_decision(lmh, dp, **kwargs)


def test_ramp_end_is_read_from_the_compiled_udf_source():
    source = UDF.read_text(encoding="utf-8")
    assert "if (iter < RAMP_ITER_3) return RAMP_FACTOR_3;" in source
    assert ramp_end_from_udf_source(source) == 150
    parser = (REPO_ROOT / "src" / "ro" / "lf_monitor_stop.py").read_text(
        encoding="utf-8"
    )
    assert "return 150" not in parser
    with pytest.raises(ValueError, match="RAMP_ITER_3"):
        ramp_end_from_udf_source("#define RAMP_ITER_1 50\n")


def test_plateau_after_the_ramp_stops_and_an_overlapping_window_does_not():
    flat = _flat(1, 250)
    early = _decision(flat[:200], flat[:200])
    assert early["stable"] is False
    assert early["reason"] == "window_overlaps_ramp"
    assert early["window_start"] == 101
    assert early["lmh_range"] == pytest.approx(0.0)
    ready = _decision(flat, flat)
    assert ready["stable"] is True
    assert ready["reason"] == "lf_monitor_stable"
    assert ready["window_start"] == 151
    assert ready["window_end"] == 250
    assert ready["window_start"] == RAMP + 1
    assert ready["window_end"] == RAMP + N


def test_ramp_steps_then_a_plateau_waits_until_the_window_clears_the_ramp():
    def value(iteration: int) -> float:
        if iteration < 50:
            return 10.0
        if iteration < 100:
            return 15.0
        if iteration < 150:
            return 20.0
        if iteration == 150:
            return 26.55
        return 26.48

    rows = [(iteration, value(iteration)) for iteration in range(1, 251)]
    before = _decision(rows[:249], rows[:249])
    assert before["stable"] is False
    assert before["window_start"] == 150
    assert before["reason"] == "window_overlaps_ramp"
    after = _decision(rows, rows)
    assert after["stable"] is True
    assert after["lmh_range"] == pytest.approx(0.0)
    assert after["dp_range"] == pytest.approx(0.0)


def test_a_series_that_never_stabilizes_hits_max_iterations():
    calls = []

    def iterate(count):
        calls.append(count)

    def read_decision():
        return {
            "stable": False,
            "reason": "not_yet",
            "ramp_end": 3,
            "n": 2,
            "tol_lmh": 1.0e-3,
            "tol_dp": 5.0e-3,
            "window_start": 1,
            "window_end": 2,
            "lmh_range": 0.2,
            "dp_range": 0.2,
        }

    decision = run_lf_monitor_iterations(
        iterate=iterate,
        read_decision=read_decision,
        ramp_end=3,
        n=2,
        max_iterations=6,
    )
    assert calls == [5, 1]
    assert decision["stop_reason"] == STOP_REASON_MAX_ITER_REACHED
    assert decision["iterations_run"] == 6
    assert decision["stable"] is False


def test_runner_stops_at_ramp_end_plus_n_when_the_first_legal_window_is_stable():
    calls = []

    def iterate(count):
        calls.append(count)

    def read_decision():
        return _decision(_flat(1, 250), _flat(1, 250))

    decision = run_lf_monitor_iterations(
        iterate=iterate,
        read_decision=read_decision,
        ramp_end=RAMP,
        n=N,
        max_iterations=600,
    )
    assert calls == [RAMP + N]
    assert decision["stop_reason"] == STOP_REASON_LF_MONITOR_STABLE
    assert decision["iterations_run"] == 250


def _write_case(tmp_path: Path, rows, *, continuity_udf: str | None = None):
    source = UDF.read_text(encoding="utf-8") if continuity_udf is None else continuity_udf
    (tmp_path / "260822_RO_UDF.c").write_text(source, encoding="utf-8")
    for name in ("lmh_udm_avg.out", "pressure_drop_spacer.out"):
        lines = ['"report"', '"Iteration" "value"']
        lines.extend(f"{iteration} {value}" for iteration, value in rows)
        (tmp_path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_lf_gate_ignores_mass_balance_lmh_and_requires_monitors_and_continuity(tmp_path):
    rows = _flat(1, 250)
    _write_case(tmp_path, rows)
    metrics = {
        "lmh_relative_difference": 0.5,
        "mass_balance_relative_error": 0.5,
    }
    production = evaluate_convergence_quality(metrics, continuity_final=1.0e-2)
    assert production["convergence_quality"] == "FAIL"
    assert "lmh_relative_difference" in production["failures"]
    assert "mass_balance_relative_error" in production["failures"]
    gated = evaluate_lf_monitor_gate(
        tmp_path,
        continuity_final=1.0e-2,
        tol_lmh=1.0e-3,
        tol_dp=5.0e-3,
        n=100,
        metrics=metrics,
    )
    assert gated["convergence_quality"] == "PASS"
    assert gated["convergence_gate"] == "lf_monitor"
    assert gated["lf_monitor_ramp_end"] == 150
    assert gated["lf_monitor_window"] == {"start": 151, "end": 250, "n": 100}
    assert gated["checks"]["lf_monitor_stable"]["passed"] is True
    payload = manifest_quality_payload(gated)
    assert payload["convergence_gate"] == "lf_monitor"
    assert payload["lf_monitor_tol_lmh"] == pytest.approx(1.0e-3)
    assert payload["lf_monitor_tol_dp"] == pytest.approx(5.0e-3)
    assert payload["lf_monitor_n"] == 100
    assert "convergence_gate" not in manifest_quality_payload(production)
    missing = evaluate_lf_monitor_gate(
        tmp_path,
        continuity_final=None,
        tol_lmh=1.0e-3,
        tol_dp=5.0e-3,
        n=100,
    )
    assert missing["convergence_quality"] == "FAIL"
    assert missing["failures"] == ["continuity_final"]
    _write_case(tmp_path, _flat(1, 200))
    overlapping = evaluate_lf_monitor_gate(
        tmp_path,
        continuity_final=1.0e-8,
        tol_lmh=1.0e-3,
        tol_dp=5.0e-3,
        n=100,
    )
    assert overlapping["failures"] == ["lf_monitor_stable"]


def test_production_and_hf_settings_stay_the_production_stop(tmp_path):
    del tmp_path
    table = load_fidelity_table(TABLE)
    hf = parse_fidelity_level(table["HF"])
    low_b = parse_fidelity_level(table["LF_b"])
    low_c = parse_fidelity_level(table["LF_c"])
    assert solver_cli_args(hf) == []
    assert solver_cli_args(low_b) == []
    used = fidelity_settings_used(hf)
    production_solver = production_solver_convergence()
    production_gate = production_gate_thresholds()
    assert set(used["solver"]) == set(production_solver)
    for key, item in used["solver"].items():
        assert item == {"value": production_solver[key], "source": "production"}
    assert set(used["convergence_gate"]) == set(production_gate)
    for key, item in used["convergence_gate"].items():
        assert item == {"value": production_gate[key], "source": "production"}
    low_used = fidelity_settings_used(low_b)
    assert "lf_monitor_stop" not in low_used["solver"]
    assert set(low_used["convergence_gate"]) == set(production_gate)
    c_used = fidelity_settings_used(low_c)
    assert c_used["solver"]["lf_monitor_stop"]["source"] == "fidelity_table"
    assert c_used["solver"]["max_iterations"] == {
        "value": 600,
        "source": "fidelity_table",
    }
    assert c_used["solver"]["qoi_stop_criterion"]["source"] == "production"
    assert c_used["convergence_gate"]["mode"]["value"] == "lf_monitor"
    assert run_id_for_level("u0p2_p6M", hf) == "u0p2_p6M"
    assert run_id_for_level("u0p2_p6M", low_c) == "u0p2_p6M_lfc"
    flags = solver_cli_args(low_c)
    assert "--qoi-stop-criterion" not in flags
    assert "--lf-monitor-stop" in flags
    solver = (REPO_ROOT / "scripts" / "solver_code_260616.py").read_text(
        encoding="utf-8"
    )
    assert 'QOI_CONVERGENCE_CONDITION = "all-conditions-are-met"' in solver
    config = (REPO_ROOT / "configs" / "run_config.py").read_text(encoding="utf-8")
    assert "qoi_stop_criterion = 1e-3" in config
    assert "qoi_previous_values_to_consider = 100" in config
    assert "qoi_initial_values_to_ignore = 200" in config
    assert "max_iterations = 2000" in config
