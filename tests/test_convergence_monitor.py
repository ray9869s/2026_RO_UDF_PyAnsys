"""CP monitor change, campaign_v3 stop, and the convergence audit."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.convergence_quality import evaluate_convergence_quality
from ro.convergence_stop import (
    campaign_v3_stop_overrides,
    convergence_stop_manifest_fields,
    launch_limit_overrides,
)
from ro.monitor_change import (
    MONITOR_CHANGE_LAST100,
    cp_live_monitor_definition,
    cp_monitor_definition,
    last100_monitor_changes,
    relative_change_last_iterations,
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
    assert tuple(changes) == MONITOR_CHANGE_LAST100
    assert changes["monitor_change_last100_lmh"] == pytest.approx(
        abs(24.001 - 20.0) / 24.001
    )
    assert changes["monitor_change_last100_dp"] == pytest.approx(0.0)
    assert changes["monitor_change_last100_cp"] == pytest.approx(0.0)

    (tmp_path / "cp_window.out").unlink()
    fallback = last100_monitor_changes(tmp_path)
    assert fallback["monitor_change_last100_cp"] == pytest.approx(abs(9.0 - 1.0) / 9.0)


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
    assert float(by_run["capped"]["monitor_change_last100_cp"]) == pytest.approx(0.0)
    assert by_run["transcript_only"]["group"] == "stopped_early"
    assert by_run["transcript_only"]["stop_iteration"] == "30"
    assert by_run["transcript_only"]["continuity_final"] == "2.5e-07"
    assert by_run["transcript_only"]["lmh_source"] == "transcript"
    assert by_run["transcript_only"]["residual_source"] == "transcript"
    assert float(by_run["transcript_only"]["monitor_change_last100_lmh"]) == pytest.approx(
        abs(11.0 - 10.0) / 11.0
    )

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
    )
    assert "udm-9" not in window
    assert "IF(udm-9>0, udm-7, 0)" in live
    assert "cp_monitor_cb_yi" in live
    assert "cp_monitor_membrane_window" in live
