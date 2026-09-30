"""Offline checks for the 2D optimization pilot. No Fluent session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.optimization.acquisition import constrained_ei, expected_improvement
from ro_2d_pilot.optimization.budget import Budget
from ro_2d_pilot.optimization.cost import (
    hf_equivalent,
    lf_over_hf_cost_ratio,
    normalized_fidelity_cost,
    reference_hf_solver_s,
)
from ro_2d_pilot.optimization.data import Dataset, Outcome
from ro_2d_pilot.optimization.domain import (
    FIDELITY_HIGH,
    FIDELITY_LOW,
    denormalize,
    geometry_allowed,
    normalize,
)
from ro_2d_pilot.optimization.experiment import (
    METHOD_HF,
    METHOD_MF,
    MODE_REPLAY,
    ExperimentConfig,
    ReplayEvaluator,
    run_optimization,
)
from ro_2d_pilot.optimization.gp import AutoregressiveModel, GaussianProcess
from ro_2d_pilot.optimization.initial import COMMON_HF_INITIAL
from ro_2d_pilot.optimization.objective import P_LIMIT_PA_PER_M, pressure_feasible
from ro_2d_pilot.optimization.replay import ReplayMiss, lookup
import numpy as np


def test_normalization_round_trip() -> None:
    assert normalize(0.30e-3, 3.0e-3) == (0.0, 0.0)
    assert normalize(0.50e-3, 5.0e-3) == (1.0, 1.0)
    design = denormalize(0.5, 0.5)
    assert design.d_m == pytest.approx(0.40e-3)
    assert design.L_m == pytest.approx(4.0e-3)
    x1, x2 = normalize(design.d_m, design.L_m)
    assert (x1, x2) == pytest.approx((0.5, 0.5))


def test_geometry_validator_rejects_overlap_and_blockage() -> None:
    assert geometry_allowed(0.40e-3, 4.0e-3)
    assert not geometry_allowed(0.80e-3, 4.0e-3)
    with pytest.raises(ValueError):
        PilotConfig(d_m=0.40e-3, L_m=0.30e-3, fidelity="very_fine", n_pitches=3)


def test_pressure_limit_splits_the_screen() -> None:
    feasible = lookup(0.40e-3, 4.0e-3, FIDELITY_HIGH)
    blocked = lookup(0.50e-3, 3.0e-3, FIDELITY_HIGH)
    assert feasible.dp_per_l is not None and feasible.dp_per_l <= P_LIMIT_PA_PER_M
    assert blocked.dp_per_l is not None and blocked.dp_per_l > P_LIMIT_PA_PER_M
    assert pressure_feasible(feasible.dp_per_l)
    assert not pressure_feasible(blocked.dp_per_l)


def test_cost_model_uses_screened_solver_medians() -> None:
    assert reference_hf_solver_s() == pytest.approx(758.1605)
    assert lf_over_hf_cost_ratio() == pytest.approx(0.1058853, rel=1e-4)
    assert normalized_fidelity_cost(FIDELITY_HIGH) == 1.0
    assert normalized_fidelity_cost(FIDELITY_LOW) == pytest.approx(lf_over_hf_cost_ratio())
    assert hf_equivalent(reference_hf_solver_s()) == pytest.approx(1.0)


def test_budget_stops_before_an_unaffordable_query() -> None:
    budget = Budget(limit_hf_equivalent=1.2, max_sequential=6)
    budget.charge(
        FIDELITY_HIGH,
        solver_s=reference_hf_solver_s(),
        wall_s=1.0,
        sequential=False,
        failed=False,
        imputed=False,
    )
    assert budget.can_afford(FIDELITY_HIGH, sequential=True) is False
    assert budget.can_afford(FIDELITY_LOW, sequential=True) is True


def test_diverged_replay_is_not_an_objective() -> None:
    failed = lookup(0.50e-3, 4.0e-3, FIDELITY_HIGH)
    assert failed.state == "diverged"
    assert failed.lmh is None
    dataset = Dataset()
    dataset.add(failed)
    assert dataset.training(FIDELITY_HIGH) == []
    with pytest.raises(ValueError, match="Refusing to repeat"):
        dataset.add(failed)


def test_replay_does_not_invent_values() -> None:
    with pytest.raises(ReplayMiss):
        lookup(0.35e-3, 3.5e-3, FIDELITY_HIGH)
    with pytest.raises(ReplayMiss):
        lookup(0.50e-3, 4.0e-3, FIDELITY_LOW)


def test_gp_and_autoregressive_use_both_fidelities() -> None:
    x = np.asarray([[0.0], [1.0]])
    low = GaussianProcess().fit(x, np.asarray([0.0, 1.0]))
    mean, _ = low.predict(np.asarray([[0.0]]))
    assert mean[0] == pytest.approx(0.0, abs=0.15)
    model = AutoregressiveModel().fit(
        x,
        np.asarray([0.0, 2.0]),
        x,
        np.asarray([0.0, 2.0]),
    )
    high, _ = model.predict_high(np.asarray([[1.0]]))
    assert high[0] == pytest.approx(2.0, abs=0.35)
    assert model.correlation_with_low(np.asarray([[0.5]]))[0] > 0.0


def test_constrained_ei_is_zero_when_the_constraint_is_violated() -> None:
    assert expected_improvement(2.0, 1.0, 1.0) > 0.0
    blocked = constrained_ei(2.0, 1.0, 100.0, 1.0e-12, incumbent=1.0, limit=30.0)
    assert blocked == pytest.approx(0.0)
    open_score = constrained_ei(2.0, 1.0, 10.0, 1.0e-12, incumbent=1.0, limit=30.0)
    assert open_score > 0.0


def test_hf_and_mfbo_replay_are_deterministic_and_budgeted(tmp_path: Path) -> None:
    first = _run(tmp_path / "a", METHOD_HF)
    second = _run(tmp_path / "b", METHOD_HF)
    assert first["final_hf_validated"] == second["final_hf_validated"]
    assert first["cumulative_hf_equivalent"] == second["cumulative_hf_equivalent"]
    history = json.loads((tmp_path / "a" / "history.json").read_text(encoding="utf-8"))
    assert all(row["fidelity"] == FIDELITY_HIGH for row in history)
    assert first["cumulative_hf_equivalent"] <= 8.0 + 1.0e-6
    mf = _run(tmp_path / "mf", METHOD_MF)
    mf_history = json.loads((tmp_path / "mf" / "history.json").read_text(encoding="utf-8"))
    assert any(row["fidelity"] == FIDELITY_LOW for row in mf_history)
    assert mf["n_medium"] >= 4
    assert mf["cumulative_hf_equivalent"] <= 8.0 + 1.5
    initial = {(item.d_m, item.L_m) for item in COMMON_HF_INITIAL}
    assert (0.30e-3, 3.0e-3) not in initial
    loaded_hf = [
        (row["d_m"], row["L_m"])
        for row in history
        if not row["sequential"]
    ]
    assert len(loaded_hf) == 4


def test_reused_replay_rows_still_count_as_cost(tmp_path: Path) -> None:
    summary = _run(tmp_path, METHOD_HF)
    history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert all(row["reused_existing"] for row in history)
    assert summary["cumulative_solver_s"] > 0.0
    assert history[-1]["cumulative_hf_equivalent"] > 0.0


def test_final_recommendation_is_hf_feasible(tmp_path: Path) -> None:
    summary = _run(tmp_path, METHOD_HF)
    final = summary["final_hf_validated"]
    assert final["hf_lmh"] is not None
    assert final["hf_dp_per_l"] <= P_LIMIT_PA_PER_M
    assert summary["mode"] == MODE_REPLAY


def _run(destination: Path, method: str) -> dict[str, object]:
    summary = run_optimization(
        ExperimentConfig(method=method, mode=MODE_REPLAY, experiment_id="pilot", seed=0),
        ReplayEvaluator(),
        destination,
    )
    assert (destination / "config.json").is_file()
    assert (destination / "history.csv").is_file()
    return summary
