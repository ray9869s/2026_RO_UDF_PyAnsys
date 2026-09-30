"""Sequential HF-only BO and cost-aware MFBO.

``offline_replay`` reads the screening table and does not claim new CFD
savings. ``live`` calls the existing 2D case runner, one evaluation at a
time. A valid result that is already on disk is reused for the solve and
still charged to the benchmark budget.
"""

from __future__ import annotations

import csv
import json
import platform
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np

from ro_2d_pilot.optimization.acquisition import (
    constrained_ei,
    forrester_scores,
    stack_xy,
)
from ro_2d_pilot.optimization.budget import (
    MAX_SEQUENTIAL_EVALUATIONS,
    PILOT_HF_EQUIVALENT_BUDGET,
    Budget,
)
from ro_2d_pilot.optimization.cost import (
    lf_over_hf_cost_ratio,
    reference_hf_solver_s,
)
from ro_2d_pilot.optimization.data import Dataset, Outcome, outcome_from_result
from ro_2d_pilot.optimization.domain import (
    FIDELITY_HIGH,
    FIDELITY_LOW,
    Design,
    denormalize,
    geometry_allowed,
)
from ro_2d_pilot.optimization.gp import AutoregressiveModel
from ro_2d_pilot.optimization.initial import COMMON_HF_INITIAL, MFBO_LF_INITIAL
from ro_2d_pilot.optimization.objective import P_LIMIT_PA_PER_M, P_LIMIT_RATIONALE
from ro_2d_pilot.optimization.replay import ReplayMiss, lookup, replay_designs

METHOD_HF = "hf_bo"
METHOD_MF = "mfbo"
MODE_REPLAY = "offline_replay"
MODE_LIVE = "live"
HF_METHOD = "constrained_expected_improvement"
MF_METHOD = "kennedy_ohagan_forrester_constrained_ei"


@dataclass(frozen=True)
class ExperimentConfig:
    method: str
    mode: str
    experiment_id: str
    seed: int = 0
    pressure_limit_pa_per_m: float = P_LIMIT_PA_PER_M
    budget_hf_equivalent: float = PILOT_HF_EQUIVALENT_BUDGET
    max_sequential: int = MAX_SEQUENTIAL_EVALUATIONS


class ReplayEvaluator:
    def __call__(self, design: Design, fidelity: str) -> Outcome:
        try:
            found = lookup(design.d_m, design.L_m, fidelity)
        except ReplayMiss as exc:
            raise ReplayMiss(
                "offline_replay has no CFD value for "
                f"d={design.d_m} L={design.L_m} fidelity={fidelity}."
            ) from exc
        found.reused_existing = True
        return found


class LiveEvaluator:
    """One existing 2D case at a time. Does not open a second Fluent session."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __call__(self, design: Design, fidelity: str) -> Outcome:
        from ro_2d_pilot.config import OperatingPoint, PilotConfig
        from ro_2d_pilot.plan import build_plan
        from ro_2d_pilot.paths import run_dir

        if not geometry_allowed(design.d_m, design.L_m):
            raise ValueError(f"Geometry rejected d={design.d_m} L={design.L_m}.")
        config = PilotConfig(
            d_m=design.d_m,
            L_m=design.L_m,
            fidelity=fidelity,
            n_pitches=3,
            operating=OperatingPoint(),
        )
        plan = build_plan(config)
        path = run_dir(
            self.root,
            str(plan["geo_id"]),
            fidelity,
            str(plan["run_id"]),
        ) / "result.json"
        if path.is_file():
            record = json.loads(path.read_text(encoding="utf-8"))
            return outcome_from_result(
                design.d_m,
                design.L_m,
                fidelity,
                record,
                reused_existing=True,
            )
        from ro_2d_pilot.execute import run_case

        try:
            record = run_case(config, self.root)
        except Exception:
            return outcome_from_result(
                design.d_m,
                design.L_m,
                fidelity,
                None,
                reused_existing=False,
            )
        return outcome_from_result(
            design.d_m,
            design.L_m,
            fidelity,
            record,
            reused_existing=False,
        )


def run_optimization(
    config: ExperimentConfig,
    evaluator: ReplayEvaluator | LiveEvaluator,
    destination: Path,
) -> dict[str, object]:
    if config.method not in {METHOD_HF, METHOD_MF}:
        raise ValueError(f"Unknown method {config.method!r}.")
    if config.mode not in {MODE_REPLAY, MODE_LIVE}:
        raise ValueError(f"Unknown mode {config.mode!r}.")
    dataset = Dataset()
    budget = Budget(
        limit_hf_equivalent=config.budget_hf_equivalent,
        max_sequential=config.max_sequential,
    )
    history: list[dict[str, object]] = []
    for design in COMMON_HF_INITIAL:
        _absorb(dataset, budget, history, evaluator(design, FIDELITY_HIGH), sequential=False)
    if config.method == METHOD_MF:
        for design in MFBO_LF_INITIAL:
            _absorb(dataset, budget, history, evaluator(design, FIDELITY_LOW), sequential=False)
    while True:
        proposal = _propose(config, dataset)
        if proposal is None:
            break
        design, fidelity = proposal
        if not budget.can_afford(fidelity, sequential=True):
            break
        _absorb(dataset, budget, history, evaluator(design, fidelity), sequential=True)
    recommended = _recommend(config, dataset)
    if recommended is not None and not dataset.seen(
        recommended.d_m, recommended.L_m, FIDELITY_HIGH
    ):
        outcome = evaluator(recommended, FIDELITY_HIGH)
        _absorb(dataset, budget, history, outcome, sequential=True)
        recommended = dataset.best_hf_feasible(config.pressure_limit_pa_per_m)
        if recommended is None and outcome.trains_gp and outcome.dp_per_l is not None:
            from ro_2d_pilot.optimization.objective import pressure_feasible

            if pressure_feasible(outcome.dp_per_l, config.pressure_limit_pa_per_m):
                recommended = outcome
    summary = _summary(config, dataset, budget, recommended)
    _write(destination, config, history, summary)
    return summary


def _propose(config: ExperimentConfig, dataset: Dataset) -> tuple[Design, str] | None:
    objective, pressure = _fit(dataset)
    incumbent = dataset.best_hf_feasible_lmh(config.pressure_limit_pa_per_m)
    best: tuple[float, Design, str] | None = None
    for design in _candidates(config.mode):
        if not geometry_allowed(design.d_m, design.L_m):
            continue
        mean_l, var_l, mean_p, var_p, correlation = _predict(objective, pressure, design)
        score = constrained_ei(
            mean_l,
            var_l,
            mean_p,
            var_p,
            incumbent=incumbent,
            limit=config.pressure_limit_pa_per_m,
        )
        if config.method == METHOD_HF:
            choices = {FIDELITY_HIGH: score}
        else:
            choices = forrester_scores(score, correlation)
        for fidelity, value in choices.items():
            if dataset.seen(design.d_m, design.L_m, fidelity):
                continue
            if config.mode == MODE_REPLAY and not _replay_has(design, fidelity):
                continue
            if best is None or value > best[0]:
                best = (value, design, fidelity)
    if best is None:
        return None
    return best[1], best[2]


def _recommend(config: ExperimentConfig, dataset: Dataset) -> Design | None:
    objective, pressure = _fit(dataset)
    best_design: Design | None = None
    best_mean = -1.0e300
    for design in _candidates(config.mode):
        mean_l, _, mean_p, _, _ = _predict(objective, pressure, design)
        if mean_p > config.pressure_limit_pa_per_m:
            continue
        if mean_l > best_mean:
            best_mean = mean_l
            best_design = design
    if best_design is not None:
        return best_design
    incumbent = dataset.best_hf_feasible(config.pressure_limit_pa_per_m)
    if incumbent is None:
        return None
    return Design(incumbent.d_m, incumbent.L_m)


def _fit(dataset: Dataset) -> tuple[AutoregressiveModel, AutoregressiveModel]:
    low = dataset.training(FIDELITY_LOW)
    high = dataset.training(FIDELITY_HIGH)
    objective = AutoregressiveModel().fit(
        stack_xy([item.x for item in low]),
        np.asarray([item.lmh for item in low], dtype=float),
        stack_xy([item.x for item in high]),
        np.asarray([item.lmh for item in high], dtype=float),
    )
    pressure = AutoregressiveModel().fit(
        stack_xy([item.x for item in low]),
        np.asarray([item.dp_per_l for item in low], dtype=float),
        stack_xy([item.x for item in high]),
        np.asarray([item.dp_per_l for item in high], dtype=float),
    )
    return objective, pressure


def _predict(
    objective: AutoregressiveModel,
    pressure: AutoregressiveModel,
    design: Design,
) -> tuple[float, float, float, float, float]:
    x = np.asarray([design.x], dtype=float)
    mean_l, var_l = objective.predict_high(x)
    mean_p, var_p = pressure.predict_high(x)
    correlation = float(objective.correlation_with_low(x)[0])
    return float(mean_l[0]), float(var_l[0]), float(mean_p[0]), float(var_p[0]), correlation


def _replay_has(design: Design, fidelity: str) -> bool:
    try:
        lookup(design.d_m, design.L_m, fidelity)
    except ReplayMiss:
        return False
    return True


def _candidates(mode: str) -> list[Design]:
    if mode == MODE_REPLAY:
        return [Design(d_m, L_m) for d_m, L_m in replay_designs()]
    designs: list[Design] = []
    for x1 in np.linspace(0.0, 1.0, 9):
        for x2 in np.linspace(0.0, 1.0, 9):
            designs.append(denormalize(float(x1), float(x2)))
    return designs


def _absorb(
    dataset: Dataset,
    budget: Budget,
    history: list[dict[str, object]],
    outcome: Outcome,
    *,
    sequential: bool,
) -> None:
    dataset.add(outcome)
    budget.charge(
        outcome.fidelity,
        solver_s=outcome.solver_s,
        wall_s=outcome.wall_s,
        sequential=sequential,
        failed=outcome.failed,
        imputed=outcome.cost_imputed,
    )
    from ro_2d_pilot.optimization.objective import pressure_feasible

    feasible = (
        outcome.trains_gp
        and outcome.dp_per_l is not None
        and pressure_feasible(outcome.dp_per_l, P_LIMIT_PA_PER_M)
    )
    history.append(
        {
            "step": len(history) + 1,
            "sequential": sequential,
            "d_m": outcome.d_m,
            "L_m": outcome.L_m,
            "x1": outcome.x[0],
            "x2": outcome.x[1],
            "fidelity": outcome.fidelity,
            "state": outcome.state,
            "lmh": outcome.lmh,
            "dp_per_l": outcome.dp_per_l,
            "cp": outcome.cp,
            "feasible": feasible,
            "solver_s": outcome.solver_s,
            "wall_s": outcome.wall_s,
            "reused_existing": outcome.reused_existing,
            "cost_imputed": outcome.cost_imputed or outcome.solver_s is None,
            "cumulative_solver_s": budget.solver_s,
            "cumulative_hf_equivalent": budget.hf_equivalent,
            "cumulative_wall_s": budget.wall_s if budget.wall_known else None,
            "n_medium": budget.n_low,
            "n_very_fine": budget.n_high,
            "best_hf_feasible_lmh": dataset.best_hf_feasible_lmh(P_LIMIT_PA_PER_M),
        }
    )


def _summary(
    config: ExperimentConfig,
    dataset: Dataset,
    budget: Budget,
    recommended: Outcome | Design | None,
) -> dict[str, object]:
    incumbent = dataset.best_hf_feasible(config.pressure_limit_pa_per_m)
    if not isinstance(incumbent, Outcome):
        recommended_fields: dict[str, object] = {
            "d_m": None,
            "L_m": None,
            "hf_lmh": None,
            "hf_dp_per_l": None,
            "hf_cp": None,
        }
    else:
        recommended_fields = {
            "d_m": incumbent.d_m,
            "L_m": incumbent.L_m,
            "hf_lmh": incumbent.lmh,
            "hf_dp_per_l": incumbent.dp_per_l,
            "hf_cp": incumbent.cp,
        }
    failures = [
        {"d_m": item.d_m, "L_m": item.L_m, "fidelity": item.fidelity, "state": item.state}
        for item in dataset.outcomes
        if item.failed
    ]
    return {
        "method": config.method,
        "mode": config.mode,
        "experiment_id": config.experiment_id,
        "seed": config.seed,
        "acquisition": HF_METHOD if config.method == METHOD_HF else MF_METHOD,
        "pressure_limit_pa_per_m": config.pressure_limit_pa_per_m,
        "pressure_limit_rationale": P_LIMIT_RATIONALE,
        "reference_hf_solver_s": reference_hf_solver_s(),
        "lf_over_hf_cost_ratio": lf_over_hf_cost_ratio(),
        "budget_hf_equivalent": config.budget_hf_equivalent,
        "cumulative_solver_s": budget.solver_s,
        "cumulative_hf_equivalent": budget.hf_equivalent,
        "cumulative_wall_s": budget.wall_s if budget.wall_known else None,
        "n_medium": budget.n_low,
        "n_very_fine": budget.n_high,
        "n_sequential": budget.n_sequential,
        "n_failures": budget.n_failures,
        "failures": failures,
        "final_hf_validated": recommended_fields,
        "note": (
            "offline_replay does not measure new CFD savings."
            if config.mode == MODE_REPLAY
            else "Live costs count reused valid results as benchmark spend."
        ),
    }


def _write(
    destination: Path,
    config: ExperimentConfig,
    history: list[dict[str, object]],
    summary: dict[str, object],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": config.method,
        "mode": config.mode,
        "experiment_id": config.experiment_id,
        "seed": config.seed,
        "domain_d_m": [0.30e-3, 0.50e-3],
        "domain_L_m": [3.0e-3, 5.0e-3],
        "normalization": "[0, 1] on each bound",
        "objective": "maximize lmh",
        "constraint": f"pressure_drop_per_length_pa_per_m <= {config.pressure_limit_pa_per_m}",
        "pressure_limit_rationale": P_LIMIT_RATIONALE,
        "initial_hf": [{"d_m": item.d_m, "L_m": item.L_m} for item in COMMON_HF_INITIAL],
        "initial_lf": (
            [{"d_m": item.d_m, "L_m": item.L_m} for item in MFBO_LF_INITIAL]
            if config.method == METHOD_MF
            else []
        ),
        "acquisition": summary["acquisition"],
        "reference_hf_solver_s": reference_hf_solver_s(),
        "lf_over_hf_cost_ratio": lf_over_hf_cost_ratio(),
        "budget_hf_equivalent": config.budget_hf_equivalent,
        "hf_equivalent_definition": (
            "cumulative solver seconds / median screened very_fine solver seconds"
        ),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": version("numpy"),
        "botorch": None,
    }
    (destination / "config.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "history.json").write_text(
        json.dumps(history, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "final_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(destination / "history.csv", history)


def _write_csv(path: Path, history: list[dict[str, object]]) -> None:
    fields = [
        "step",
        "sequential",
        "d_m",
        "L_m",
        "x1",
        "x2",
        "fidelity",
        "state",
        "lmh",
        "dp_per_l",
        "cp",
        "feasible",
        "solver_s",
        "wall_s",
        "reused_existing",
        "cost_imputed",
        "cumulative_solver_s",
        "cumulative_hf_equivalent",
        "cumulative_wall_s",
        "n_medium",
        "n_very_fine",
        "best_hf_feasible_lmh",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in history:
            writer.writerow({name: row.get(name) for name in fields})


def experiment_dir(root: Path, method: str, experiment_id: str) -> Path:
    return root / "studies" / "optimization" / method / experiment_id
