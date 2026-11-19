"""CP monitor change, campaign_v3 stop, and the convergence audit."""

from __future__ import annotations

import csv
import json

import pytest

from types import SimpleNamespace

from helpers import SCRIPTS_DIR, load_module, load_solver_code
from ro.convergence_quality import evaluate_convergence_quality
from ro.convergence_stop import (
    campaign_v3_stop_overrides,
    convergence_stop_manifest_fields,
    launch_limit_overrides,
)
from ro.monitor_change import (
    CP_LIVE_MEAN,
    MONITOR_CHANGE_LAST100,
    cp_live_cm_definition,
    cp_live_monitor_definition,
    cp_monitor_definition,
    fluent_expression_report_is_invalid,
    format_expression_error_table,
    MONITOR_CHANGE_LAST100_LEGACY,
    MONITOR_TREND_LAST100,
    end_to_end_relative_change,
    last100_monitor_changes,
    relative_change_last_iterations,
    trend_relative_change,
)
from ro.solver_common import fluent_report_relative_change

audit = load_module(
    "convergence_audit_under_test",
    SCRIPTS_DIR / "convergence_audit.py",
)

_HEADER = (
    "iter continuity x-velocity y-velocity z-velocity nacl "
    "lmh m_out m_in area_mem lmh_udm_avg pressure_drop_spacer "
    "cp_window time/iter"
)


def _report(rows):
    lines = ['"rfile"', '"Iteration" "value"']
    for iteration, value in rows:
        lines.append(f"{iteration} {value:.16g}")
    return "\n".join(lines) + "\n"


def _tail(start, stop, value_at):
    return [(iteration, value_at(iteration)) for iteration in range(start, stop + 1)]


def test_relative_change_matches_the_fluent_window_statistic():
    series = [(1, 20.0), (2, 24.0), (3, 24.0)]
    change = relative_change_last_iterations(series, 1)
    assert change == pytest.approx(0.0)
    wider = relative_change_last_iterations(series, 100)
    assert wider == pytest.approx(fluent_report_relative_change([20.0, 24.0, 24.0]))
    assert wider == pytest.approx(4.0 / 24.0)
    assert relative_change_last_iterations([(8, 1.0)], 100) is None


def test_last100_reads_report_files_and_prefers_the_window_cp_file(tmp_path):
    lmh = _tail(100, 250, lambda i: 20.0 if i < 200 else 24.001)
    (tmp_path / "lmh_udm_avg.out").write_text(_report(lmh), encoding="utf-8")
    (tmp_path / "pressure_drop_spacer.out").write_text(
        _report([(250, 1200.0), (249, 1200.0)]),
        encoding="utf-8",
    )
    (tmp_path / "cp_full_membrane.out").write_text(
        _report([(150, 1.0), (250, 9.0)]),
        encoding="utf-8",
    )
    (tmp_path / "cp_window.out").write_text(
        _report([(250, 1.05), (150, 1.05)]),
        encoding="utf-8",
    )
    changes = last100_monitor_changes(tmp_path)
    assert set(MONITOR_CHANGE_LAST100) <= set(changes)
    assert set(MONITOR_TREND_LAST100) <= set(changes)
    assert set(MONITOR_CHANGE_LAST100_LEGACY) <= set(changes)
    assert changes["monitor_change_last100_lmh"] == pytest.approx(
        abs(24.001 - 20.0) / 24.001
    )
    assert changes["monitor_change_last100_lmh_legacy"] == pytest.approx(
        abs(24.001 - 20.0) / 24.001
    )
    assert changes["monitor_change_last100_dp"] is None  # no sample at <= 150
    assert changes["monitor_change_last100_dp_legacy"] == pytest.approx(0.0)
    # New CP columns read cp_live.out only; cp_window is legacy.
    assert changes["monitor_change_last100_cp"] is None
    assert changes["monitor_change_last100_cp_legacy"] == pytest.approx(0.0)

    (tmp_path / "cp_window.out").unlink()
    fallback = last100_monitor_changes(tmp_path)
    assert fallback["monitor_change_last100_cp_legacy"] == pytest.approx(abs(9.0 - 1.0) / 9.0)
    assert fallback["monitor_change_last100_cp"] is None


def test_oscillation_reads_as_drift_not_amplitude(tmp_path):
    """Host evidence: 101 distinct values but no drift 2000 -> 4020."""
    wobble = _tail(3900, 4020, lambda i: 1.2 * (1 + 1e-4 * (-1) ** i))
    (tmp_path / "cp_live.out").write_text(_report(wobble), encoding="utf-8")
    (tmp_path / "cp_window.out").write_text(_report(wobble), encoding="utf-8")
    changes = last100_monitor_changes(tmp_path)
    assert changes["monitor_change_last100_cp_legacy"] == pytest.approx(2e-4, rel=1e-3)
    assert changes["monitor_change_last100_cp"] == pytest.approx(0.0, abs=1e-12)
    assert changes["monitor_trend_last100_cp"] < 1e-5
    assert changes["monitor_change_last100_cp_live"] == pytest.approx(2e-4, rel=1e-3)


def test_drift_is_seen_by_change_and_trend():
    drift = _tail(1900, 2000, lambda i: 10.0 + 1e-3 * (i - 1900))
    assert end_to_end_relative_change(drift, 100) == pytest.approx(0.1 / 10.1)
    assert trend_relative_change(drift, 100) == pytest.approx(0.1 / 10.05)


def test_window_spanning_two_attempt_segments_is_unavailable():
    """Rows from a restart are not stitched into one window any more."""
    from ro.monitor_change import window_status

    appended = _tail(1800, 2000, lambda i: 5.0) + _tail(1950, 2000, lambda i: 5.5)
    assert end_to_end_relative_change(appended, 100) is None
    assert "attempt segment" in window_status(appended, 100)
    assert trend_relative_change(appended, 100) is None
    assert end_to_end_relative_change([(5, 1.0)], 100) is None
    assert trend_relative_change([(1, 1.0), (2, 2.0)], 100) is None


def test_campaign_v3_stop_bumps_the_ignore_prefix_past_the_ramp():
    overrides = campaign_v3_stop_overrides(
        stop_criterion=1e-4,
        window=100,
        initial_values_to_ignore=100,
        ramp_full_iteration=150,
        residual_target=1e-7,
        max_iterations=2000,
    )
    assert overrides["qoi_initial_values_to_ignore"] == 150
    assert overrides["qoi_stop_include_cp"] is True
    assert overrides["qoi_window_after_ramp"] is True
    assert overrides["max_iterations"] == 2000
    fields = convergence_stop_manifest_fields(
        stop_criterion=overrides["qoi_stop_criterion"],
        previous_values=overrides["qoi_previous_values_to_consider"],
        initial_values_to_ignore=overrides["qoi_initial_values_to_ignore"],
        include_cp=True,
        window_after_ramp=True,
        ramp_full_iteration=150,
        stop_reports=["lmh_udm_avg", "pressure_drop_spacer", "cp_window"],
        cp_monitor_name="cp_window",
        cp_monitor_scope="window",
    )
    assert fields["qoi_stop_reports"][-1] == "cp_window"
    assert "solver_settings" not in fields


def test_launch_limits_only_override_the_keys_that_were_set():
    assert launch_limit_overrides() == {}
    limits = launch_limit_overrides(
        processor_count=1,
        max_iterations=5,
        disable_ramp_convergence_safety=True,
    )
    assert limits == {
        "processor_count": 1,
        "max_iterations": 5,
        "use_ramp_convergence_safety": False,
    }
    with pytest.raises(ValueError, match="processor_count"):
        launch_limit_overrides(processor_count=0)


def test_monitor_columns_do_not_change_pass_fail():
    metrics = {
        "lmh_relative_difference": 1.0e-6,
        "mass_balance_relative_error": 1.0e-6,
        "continuity_final": 1.0e-8,
    }
    base = evaluate_convergence_quality(metrics)
    extra = dict(metrics)
    extra.update({name: 0.4 for name in MONITOR_CHANGE_LAST100})
    recorded = evaluate_convergence_quality(extra)
    assert base["convergence_quality"] == "PASS"
    assert recorded["convergence_quality"] == "PASS"
    assert recorded["failures"] == base["failures"] == []
    assert "monitor_change_last100_lmh" not in recorded


def _manifest(run_id, max_iterations, quality="PASS"):
    return {
        "convergence_quality": quality,
        "family": "diamond",
        "geo_id": "D2450_a45",
        "mesh_id": "max085_min006_cpg5_bl4_peel2",
        "run_id": run_id,
        "solver_settings": {"max_iterations": max_iterations},
    }


def _scientific_row(iteration, continuity, nacl, lmh, dp, cp):
    return (
        f"{iteration} {continuity} 1.0e-08 1.0e-08 1.0e-08 {nacl} "
        f"24.065 1.0e-04 -1.0e-04 1.0e-03 {lmh} {dp} {cp} 0:00:01"
    )


def test_audit_groups_report_files_and_scientific_transcript_fallback(tmp_path, capsys):
    early = tmp_path / "diamond" / "D2450_a45" / "mesh" / "early"
    cap = tmp_path / "diamond" / "D2450_a45" / "mesh" / "capped"
    transcript = tmp_path / "diamond" / "D2450_a45" / "mesh" / "transcript_only"
    failed = tmp_path / "diamond" / "D2450_a45" / "mesh" / "failed"
    posted = early / "post"
    for leaf in (early, cap, transcript, failed, posted):
        leaf.mkdir(parents=True)

    def write_leaf(leaf, run_id, iterations, quality="PASS"):
        (leaf / "manifest.json").write_text(
            json.dumps(_manifest(run_id, iterations, quality)),
            encoding="utf-8",
        )

    write_leaf(early, "early", 2000)
    write_leaf(cap, "capped", 2000)
    write_leaf(transcript, "transcript_only", 2000)
    write_leaf(failed, "failed", 2000, quality="FAIL")
    (posted / "manifest.json").write_text(
        json.dumps(_manifest("posted", 2000)),
        encoding="utf-8",
    )

    early_lmh = _tail(140, 250, lambda i: 20.0 if i < 200 else (24.0 if i < 230 else 24.001))
    (early / "lmh_udm_avg.out").write_text(_report(early_lmh), encoding="utf-8")
    (early / "pressure_drop_spacer.out").write_text(
        _report(_tail(140, 250, lambda _i: 1000.0)),
        encoding="utf-8",
    )
    (early / "cp_window.out").write_text(
        _report(_tail(140, 250, lambda _i: 1.1)),
        encoding="utf-8",
    )
    (early / "solve.txt").write_text(
        _HEADER
        + "\n"
        + _scientific_row(250, "1.0e-07", "2.0e-08", "24.065", "1000", "1.1")
        + "\n",
        encoding="utf-8",
    )

    (cap / "lmh_udm_avg.out").write_text(
        _report(_tail(1900, 2000, lambda _i: 24.065)),
        encoding="utf-8",
    )
    (cap / "pressure_drop_spacer.out").write_text(
        _report(_tail(1900, 2000, lambda _i: 1100.0)),
        encoding="utf-8",
    )
    (cap / "cp_full_membrane.out").write_text(
        _report(_tail(1900, 2000, lambda _i: 1.2)),
        encoding="utf-8",
    )
    (cap / "solve.txt").write_text(
        _HEADER
        + "\n"
        + _scientific_row(2000, "3.0e-06", "4.0e-08", "24.065", "1100", "1.2")
        + "\n",
        encoding="utf-8",
    )

    (transcript / "solve.log").write_text(
        "\n".join(
            [
                _HEADER,
                _scientific_row(10, "1.0e-04", "1.0e-05", "10.0", "1.0e3", "1.0"),
                "40 0.000 0.000 0.000 0.000 0.000 24.065 0.000 0.000 0.000 99 99 99 0:00:02",
                _scientific_row(30, "2.5e-07", "4.0e-08", "11.0", "1.1e3", "1.01"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = audit.audit_data_root(tmp_path)
    by_run = {row["run_id"]: row for row in rows}
    assert set(by_run) == {"early", "capped", "transcript_only"}
    assert by_run["early"]["group"] == "stopped_early"
    assert by_run["early"]["stop_iteration"] == "250"
    assert by_run["early"]["continuity_final"] == "1e-07"
    assert by_run["early"]["lmh_source"] == "report_file"
    assert by_run["early"]["cp_source"] == "report_file"
    assert float(by_run["early"]["monitor_change_last20_lmh"]) == pytest.approx(0.0)
    assert float(by_run["early"]["monitor_change_last100_lmh"]) == pytest.approx(
        abs(24.001 - 20.0) / 24.001
    )
    assert by_run["capped"]["group"] == "ran_to_cap"
    assert by_run["capped"]["stop_iteration"] == "2000"
    assert by_run["capped"]["nacl_final"] == "4e-08"
    assert by_run["capped"]["cp_source"] == "report_file"
    assert by_run["transcript_only"]["group"] == "stopped_early"
    assert by_run["transcript_only"]["stop_iteration"] == "30"
    assert by_run["transcript_only"]["continuity_final"] == "2.5e-07"
    assert by_run["transcript_only"]["lmh_source"] == "transcript"
    assert by_run["transcript_only"]["residual_source"] == "transcript"
    assert by_run["transcript_only"]["monitor_change_last100_lmh"] == ""
    assert float(by_run["transcript_only"]["monitor_change_last100_lmh_legacy"]) == pytest.approx(
        abs(11.0 - 10.0) / 11.0
    )
    assert float(by_run["capped"]["monitor_change_last100_cp_legacy"]) == pytest.approx(0.0)
    assert by_run["capped"]["monitor_change_last100_cp"] == ""  # no cp_live.out

    output = tmp_path / "out" / "convergence_audit.csv"
    assert audit.main([str(tmp_path), "-o", str(output)]) == 0
    with output.open(encoding="utf-8", newline="") as handle:
        written = list(csv.DictReader(handle))
    assert {row["run_id"] for row in written} == set(by_run)
    assert "monitor_change_last100_cp" in written[0]
    captured = capsys.readouterr().out
    assert "stopped_early: 2" in captured
    assert "ran_to_cap: 1" in captured
    assert audit.main([str(tmp_path / "missing")]) == 2


def test_live_cp_monitor_drops_faces_where_udm9_is_zero():
    window = cp_monitor_definition("cp_monitor_cm_avg", "cp_monitor_cb_yi", 998.2, 0.05844)
    live = cp_live_monitor_definition(
        "cp_monitor_membrane_window",
        "cp_monitor_cb_yi",
        998.2,
        0.05844,
        b_perm_m_per_s=2.5e-8,
    )
    body = cp_live_cm_definition(
        "cp_monitor_membrane_window",
        "cp_monitor_cb_yi",
        998.2,
        0.05844,
        2.5e-8,
    )
    assert "udm-9" not in window
    assert "{udm-9}>0" in body
    assert "{udm-6}" in body
    assert "{udm-7}" in body
    assert "udm-7" not in body.replace("{udm-7}", "")
    assert "AND(" in body
    assert "max(" in body
    assert "1e-30*Area(" in body
    assert "1e-30 [m^2]" not in body
    assert live.startswith(f"{CP_LIVE_MEAN} /")
    assert "cp_monitor_cb_yi" in live
    assert "AreaInt" not in live


def test_an_invalid_expression_stops_before_iterate(capsys):
    solver = load_solver_code("expr_check_invalid")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            command = commands[0]
            if "list-properties" in command:
                return "Valid            : No\nUsed in          : 1 places"
            return (
                "Setup contains invalid expression\n"
                "Expression                       Error                Used In\n"
                "cp_live_mean                     udm - 7              cp_live"
            )

    class Named:
        def get_object_names(self):
            return ["cp_live_mean"]

    session = SimpleNamespace(
        scheme=Scheme(),
        setup=SimpleNamespace(named_expressions=Named()),
    )
    with pytest.raises(RuntimeError, match="Refusing to iterate"):
        solver.require_setup_expressions_valid(session)
    printed = capsys.readouterr().out
    assert "Expression" in printed
    assert "Error" in printed
    assert "Used In" in printed
    assert "Setup contains invalid expression" in printed


def test_a_valid_expression_report_does_not_block_iterate():
    solver = load_solver_code("expr_check_valid")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return "Valid            : Yes"

    class Named:
        def get_object_names(self):
            return []

    session = SimpleNamespace(
        scheme=Scheme(),
        setup=SimpleNamespace(named_expressions=Named()),
    )
    solver.require_setup_expressions_valid(session)


def test_expression_report_text_is_the_setup_failure():
    table = format_expression_error_table(
        [("cp_live_mean", "udm - 7", "report definition cp_live")]
    )
    header = table.splitlines()[0]
    assert header.startswith("Expression")
    assert "Error" in header
    assert header.endswith("Used In")
    assert fluent_expression_report_is_invalid(
        "Setup contains invalid expression"
    )
    assert fluent_expression_report_is_invalid("Valid            : No")
    assert not fluent_expression_report_is_invalid("Valid            : Yes")
    assert not fluent_expression_report_is_invalid("Input parameter  : No")


def _session(scheme, names=(), listing="Valid            : Yes"):
    class Named:
        def get_object_names(self):
            return list(names)

    return SimpleNamespace(scheme=scheme, setup=SimpleNamespace(named_expressions=Named()))


def test_expression_check_fails_closed_when_no_check_can_run():
    """Audit B1-05: every scheme call raising is not a pass."""
    solver = load_solver_code("expr_check_no_scheme")

    class Broken:
        def exec(self, commands, wait=True, silent=False):
            raise ConnectionError("scheme unavailable")

    with pytest.raises(RuntimeError, match="no expression check could run"):
        solver.require_setup_expressions_valid(_session(Broken()))
    with pytest.raises(RuntimeError, match="no expression check could run"):
        solver.require_setup_expressions_valid(_session(None))


def test_expression_check_fails_closed_on_unbound_forms():
    solver = load_solver_code("expr_check_unbound")

    class Unbound:
        def exec(self, commands, wait=True, silent=False):
            return "Error: eval: unbound variable"

    with pytest.raises(RuntimeError, match="did not positively pass"):
        solver.require_setup_expressions_valid(_session(Unbound()))


def test_named_expression_must_list_valid_yes():
    solver = load_solver_code("expr_check_named_silent")

    class Silent:
        def exec(self, commands, wait=True, silent=False):
            return ""  # listing printed nothing about validity

    with pytest.raises(RuntimeError, match="Valid : Yes"):
        solver.require_setup_expressions_valid(_session(Silent(), names=["cp_live_mean"]))

    class Good:
        def exec(self, commands, wait=True, silent=False):
            return "Valid            : Yes\nUsed in          : 1 places"

    solver.require_setup_expressions_valid(_session(Good(), names=["cp_live_mean"]))


def test_incommensurate_oscillation_end_to_end_is_phase_sensitive_trend_is_not():
    import math

    wobble = _tail(1900, 2000, lambda i: 1.2 * (1 + 1e-4 * math.sin(2 * math.pi * i / 7)))
    change = end_to_end_relative_change(wobble, 100)
    assert change > 5e-5  # reads part of the amplitude, not drift
    assert trend_relative_change(wobble, 100) < 2e-5


@pytest.mark.parametrize("output", ["", "   ", "None", "#f", "some text never seen before", "0"])
def test_expression_check_unrecognised_output_fails(output):
    solver = load_solver_code(f"expr_check_unrecognised_{abs(hash(output))}")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return output

    with pytest.raises(RuntimeError, match="did not positively pass"):
        solver.require_setup_expressions_valid(_session(Scheme()))


@pytest.mark.parametrize("output", ["()", "#t", "No invalid inputs"])
def test_expression_check_recognised_pass_outputs(output):
    solver = load_solver_code(f"expr_check_recognised_{abs(hash(output))}")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return output

    solver.require_setup_expressions_valid(_session(Scheme()))


def test_expression_check_enumeration_failure_fails(tmp_path):
    solver = load_solver_code("expr_check_enumeration")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return "()"

    class Broken:
        def get_object_names(self):
            raise RuntimeError("settings API unavailable")

        def get_state(self):
            raise RuntimeError("settings API unavailable")

    session = SimpleNamespace(scheme=Scheme(), setup=SimpleNamespace(named_expressions=Broken()))
    log = tmp_path / "solver_log_case.txt"
    with pytest.raises(RuntimeError, match="Could not enumerate"):
        solver.require_setup_expressions_valid(session, solver_log_path=log)
    assert "FAIL (enumeration)" in log.read_text(encoding="utf-8")


def test_expression_check_raw_output_goes_to_solver_log(tmp_path):
    solver = load_solver_code("expr_check_log")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return "#t"

    log = tmp_path / "solver_log_case.txt"
    log.write_text("earlier lines\n", encoding="utf-8")
    solver.require_setup_expressions_valid(_session(Scheme()), solver_log_path=log)
    text = log.read_text(encoding="utf-8")
    assert text.startswith("earlier lines")
    assert "EXPRESSION CHECK (raw output)" in text
    assert "(get-invalid-inputs-formated)" in text
    assert "RESULT: PASS" in text


def test_expression_check_warn_override_is_explicit_and_logged(monkeypatch, tmp_path, capsys):
    solver = load_solver_code("expr_check_warn")

    class Scheme:
        def exec(self, commands, wait=True, silent=False):
            return "#f"

    log = tmp_path / "solver_log_case.txt"
    monkeypatch.setenv("RO_EXPRESSION_CHECK", "warn")
    solver.require_setup_expressions_valid(_session(Scheme()), solver_log_path=log)
    assert "RO_EXPRESSION_CHECK=warn" in capsys.readouterr().out
    assert "OVERRIDDEN" in log.read_text(encoding="utf-8")

    class Invalid:
        def exec(self, commands, wait=True, silent=False):
            return "Setup contains invalid expression"

    # The override never lets an explicit invalid expression through.
    with pytest.raises(RuntimeError, match="Setup contains invalid expressions"):
        solver.require_setup_expressions_valid(_session(Invalid()), solver_log_path=log)


def test_monitor_window_requires_coverage_samples_and_finite_values():
    from ro.monitor_change import oscillation_amplitude, validated_window, window_status

    good = _tail(1500, 2000, lambda i: 10.0 + 1e-6 * i)
    assert window_status(good, 500) == "ok"
    assert end_to_end_relative_change(good, 500) == pytest.approx(5e-4 / (10.0 + 2e-3))
    short = _tail(1600, 2000, lambda i: 10.0)
    assert "not covered" in window_status(short, 500)
    sparse = [(i, 10.0) for i in range(1500, 2001, 10)]
    assert "samples in window" in window_status(sparse, 500)
    nan = good[:-10] + [(1991, float("nan"))] + good[-9:]
    assert window_status(nan, 500) == "non-finite value in window"
    assert trend_relative_change(nan, 500) is None
    assert oscillation_amplitude(nan, 500) is None
    # A later segment that itself covers the window is fine (continue appended
    # after a re-written checkpoint row).
    resumed = _tail(1000, 2000, lambda i: 10.0) + _tail(2000, 2500, lambda i: 10.0)
    points, reason = validated_window(resumed, 500)
    assert reason is None and points[0][0] == 2000


def test_amplitude_is_separate_from_trend():
    import math

    from ro.monitor_change import oscillation_amplitude

    wobble = _tail(1500, 2000, lambda i: 2.0 * (1 + 1e-3 * math.sin(2 * math.pi * i / 37)))
    assert oscillation_amplitude(wobble, 500) == pytest.approx(2e-3, rel=0.05)
    assert trend_relative_change(wobble, 500) < 1e-4
    drift = _tail(1500, 2000, lambda i: 2.0 + 1e-5 * i)
    assert oscillation_amplitude(drift, 500) == pytest.approx(0.0, abs=1e-12)


def test_monitor_changes_default_window_is_500_and_extract_keeps_last100(tmp_path):
    from ro.monitor_change import (
        DP_REPORT_FILE,
        LMH_REPORT_FILE,
        MONITOR_DEFAULT_WINDOW,
        extract_monitor_changes,
        monitor_changes,
    )

    assert MONITOR_DEFAULT_WINDOW == 500
    body = "\n".join(f"{i} {20.0 + 1e-6 * i:.12g}" for i in range(1, 2001))
    (tmp_path / LMH_REPORT_FILE).write_text('"lmh"\n' + body + "\n", encoding="utf-8")
    (tmp_path / DP_REPORT_FILE).write_text('"dp"\n' + body + "\n", encoding="utf-8")
    default = monitor_changes(tmp_path)
    assert default["monitor_window_status_last500_lmh"] == "ok"
    assert default["monitor_trend_last500_lmh"] is not None
    assert "monitor_amplitude_last500_lmh" in default
    assert default["monitor_window_status_last500_cp"] == "no samples"
    merged = extract_monitor_changes(tmp_path)
    assert "monitor_change_last100_lmh" in merged and "monitor_change_last500_lmh" in merged
