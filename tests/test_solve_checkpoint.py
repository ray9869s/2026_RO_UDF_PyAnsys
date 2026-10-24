"""Checkpoint scheduling. Does not launch Fluent."""

from __future__ import annotations

import pytest

from ro.solve_checkpoint import (
    checkpoint_case_path,
    checkpoint_data_path,
    latest_checkpoint,
    list_checkpoints,
    prune_checkpoints,
    require_checkpoint_every,
    require_latest_checkpoint,
    resume_iteration_plan,
    run_checkpointed_iterations,
)


def test_checkpoint_every_defaults_off_and_rejects_non_positive():
    assert require_checkpoint_every(None) is None
    assert require_checkpoint_every(200) == 200
    for value in (0, -1, True, 1.5, "200"):
        with pytest.raises(ValueError, match="checkpoint_every"):
            require_checkpoint_every(value)


def test_chunks_write_every_n_and_stop_when_the_counter_does_not_finish(tmp_path):
    calls = []
    written = []
    # before/after each chunk. The second chunk stops inside the request.
    reported = iter((200, 400, 400, 550))

    def iterate(step):
        calls.append(step)

    def read_iteration():
        return next(reported)

    outcome = run_checkpointed_iterations(
        iterate,
        count=1800,
        every=200,
        start_iteration=200,
        read_iteration=read_iteration,
        on_checkpoint=written.append,
    )
    assert calls == [200, 200]
    assert written == [400, 550]
    assert outcome["end_iteration"] == 550
    assert outcome["stopped_early"] is True
    assert outcome["iterations_completed"] == 350


def test_a_stale_read_does_not_skip_the_rest_of_the_solve():
    calls = []
    written = []

    def read_iteration():
        return 781

    outcome = run_checkpointed_iterations(
        lambda step: calls.append(step),
        count=400,
        every=200,
        start_iteration=600,
        read_iteration=read_iteration,
        on_checkpoint=written.append,
    )
    assert calls == [200, 200]
    assert written == [800, 1000]
    assert outcome["stopped_early"] is False
    assert outcome["end_iteration"] == 1000


def test_checkpointing_off_is_one_iterate_and_writes_nothing():
    calls = []
    outcome = run_checkpointed_iterations(
        lambda step: calls.append(step),
        count=2000,
        every=None,
        start_iteration=0,
        on_checkpoint=lambda iteration: calls.append(("write", iteration)),
    )
    assert calls == [2000]
    assert outcome["checkpoint_iterations"] == []
    assert outcome["end_iteration"] == 2000


def test_one_step_monitor_writes_only_on_the_interval():
    written = []
    cursor = {"n": 0}

    def iterate(step):
        cursor["n"] += step

    for _ in range(3):
        outcome = run_checkpointed_iterations(
            iterate,
            count=1,
            every=2,
            start_iteration=cursor["n"],
            on_checkpoint=written.append,
        )
        cursor["n"] = outcome["end_iteration"]
    assert written == [2]
    assert cursor["n"] == 3


def test_prune_keeps_the_latest_two_and_leaves_other_case_files(tmp_path):
    geo = "D2450_a45"
    run = "u0p2_p6M"
    for iteration in (200, 400, 600):
        case_path = checkpoint_case_path(tmp_path, geo, run, iteration)
        case_path.write_bytes(b"cas")
        checkpoint_data_path(case_path).write_bytes(b"dat")
    setup = tmp_path / f"{geo}_{run}_setup.cas.h5"
    final = tmp_path / f"{geo}_{run}_final.cas.h5"
    final_data = tmp_path / f"{geo}_{run}_final.dat.h5"
    orphan = checkpoint_case_path(tmp_path, geo, run, 100)
    for path in (setup, final, final_data, orphan):
        path.write_bytes(b"keep")

    removed = prune_checkpoints(tmp_path, geo, run)
    assert [path.name for path in removed] == [
        f"{geo}_{run}_checkpoint_000200.cas.h5",
        f"{geo}_{run}_checkpoint_000200.dat.h5",
    ]
    kept = [item.iteration for item in list_checkpoints(tmp_path, geo, run)]
    assert kept == [400, 600]
    assert latest_checkpoint(tmp_path, geo, run).iteration == 600
    assert setup.is_file() and final.is_file() and final_data.is_file()
    assert orphan.is_file()


def test_latest_checkpoint_ignores_a_case_without_data(tmp_path):
    geo = "D2450_a45"
    run = "u0p2_p6M"
    older = checkpoint_case_path(tmp_path, geo, run, 200)
    older.write_bytes(b"cas")
    checkpoint_data_path(older).write_bytes(b"dat")
    newer = checkpoint_case_path(tmp_path, geo, run, 400)
    newer.write_bytes(b"cas-only")
    found = require_latest_checkpoint(tmp_path, geo, run)
    assert found.iteration == 200
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="setup case is not a solution checkpoint"):
        require_latest_checkpoint(empty, geo, run)


def test_resume_plan_continues_the_counter_past_the_ramp():
    past_ramp = resume_iteration_plan(
        checkpoint_iteration=600,
        max_iterations=2000,
        pre_convergence_iterations=200,
        use_ramp=True,
    )
    assert past_ramp == {
        "checkpoint_iteration": 600,
        "phase1_remaining": 0,
        "phase2_remaining": 1400,
        "phase2_start": 600,
    }
    inside_ramp = resume_iteration_plan(
        checkpoint_iteration=100,
        max_iterations=2000,
        pre_convergence_iterations=200,
        use_ramp=True,
    )
    assert inside_ramp["phase1_remaining"] == 100
    assert inside_ramp["phase2_remaining"] == 1800
    assert inside_ramp["phase2_start"] == 200
    at_window = resume_iteration_plan(
        checkpoint_iteration=200,
        max_iterations=2000,
        pre_convergence_iterations=200,
        use_ramp=True,
    )
    assert at_window["phase1_remaining"] == 0
    assert at_window["phase2_start"] == 200
    with pytest.raises(ValueError, match="already at max_iterations"):
        resume_iteration_plan(
            checkpoint_iteration=2000,
            max_iterations=2000,
            pre_convergence_iterations=200,
            use_ramp=True,
        )
