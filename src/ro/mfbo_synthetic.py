"""Synthetic 3D pillar benchmark on the live HF-only and MFBO loop.

The high-fidelity truth is a Gaussian process of LMH, CP excess, and dP/L
fit to valid ``HF_m`` rows. The low-fidelity truth is that surface plus a
second Gaussian process of the LF−HF discrepancy at co-located valid pairs.
An HF gate failure is a Gaussian-process classifier of valid against
gate-fail rows. Crashes are not part of that region.

``hf_bo`` and ``mfbo`` then run through ``run_optimization`` in replay mode
on the 17×17 opening-gap grid. Seed 0 keeps the v2 initial design. Any
other seed uses the live maximin Latin hypercube. A valid HF query costs
one reference wall time. A valid LF query costs ``ratio`` times that
wall time. A gate failure costs the median recorded HF gate-fail cost
from the CSV, not one reference HF.

``failure_aware`` is the live acquisition option: constrained EI times the
probability of a valid HF run, fit only on that run's own HF outcomes.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ro.mfbo_3d import (
    BUDGET_STOP_EXPECTED,
    D_F_MM,
    FIDELITIES,
    GEOMETRY_PREGENERATED,
    HF,
    LF,
    METHOD_HF,
    METHOD_MF,
    MODE_REPLAY,
    STATUS_INVALID,
    STATUS_VALID,
    TARGET_BUDGET_WALL_H,
    Experiment3D,
    PillarOutcome,
    PillarPoint,
    ReplayTable,
    _load_fidelity_settings,
    draw_maximin_lhs,
    geometry_block_reason,
    probit_validity,
    run_optimization,
)
from ro.mfbo_dataset import HF_M, LF_D, STATUS_GATE_FAIL, STATUS_VALID_NAME, Observation
from ro_2d_pilot.optimization.acquisition import stack_xy
from ro_2d_pilot.optimization.gp import GaussianProcess

LIVE_D_P_MM = 0.75
LIVE_D_H_MM = 0.05625
LIVE_HF_LMH = 24.414
LIVE_FIRST_FEASIBLE = {METHOD_HF: 8.0, METHOD_MF: 9.1}
LIVE_NEAR_OPTIMUM = {METHOD_HF: 10.6, METHOD_MF: 14.6}
LIVE_FAILURE_FRACTION = (0.30, 0.37)
DEFAULT_RATIOS = (0.54, 0.4, 0.25, 0.1)
DEFAULT_SEEDS = 20
NEAR_OPTIMUM_FRACTION = 0.999
_QOI = ("lmh", "cp_excess", "dp_per_l")
_METHODS = (METHOD_HF, METHOD_MF)


@dataclass(frozen=True)
class Surrogate:
    box: tuple[float, float, float, float]
    grid: int
    d_f_mm: float
    m_min_mm: float
    points: tuple[PillarPoint, ...]
    hf: dict[str, GaussianProcess]
    delta: dict[str, GaussianProcess | None]
    failure: GaussianProcess | None
    reference_hf_wall_s: float
    failure_hf_wall_s: float
    n_hf_valid: int
    n_hf_gate_fail: int
    n_pairs: int
    cp_excess_max: float
    dp_max_pa_per_m: float
    seed0: tuple[PillarPoint, ...]

    def predict(self, d_p_mm: float, d_h_mm: float, fidelity: str) -> tuple[float, float, float]:
        high = tuple(self._mean(self.hf[name], d_p_mm, d_h_mm) for name in _QOI)
        if fidelity == HF:
            return high[0], high[1], high[2]
        if fidelity != LF:
            raise ValueError(f"Unknown fidelity {fidelity!r}.")
        low = tuple(
            high[index]
            + (0.0 if self.delta[name] is None else self._mean(self.delta[name], d_p_mm, d_h_mm))
            for index, name in enumerate(_QOI)
        )
        return low[0], low[1], low[2]

    def hf_fails(self, d_p_mm: float, d_h_mm: float) -> bool:
        if self.failure is None:
            return False
        mean, variance = self._dist(self.failure, d_p_mm, d_h_mm)
        return probit_validity(mean, variance) < 0.5

    def outcome(self, d_p_mm: float, d_h_mm: float, fidelity: str, *, cost_ratio: float) -> PillarOutcome:
        if fidelity == HF and self.hf_fails(d_p_mm, d_h_mm):
            return PillarOutcome(
                d_p_mm=d_p_mm,
                d_h_mm=d_h_mm,
                fidelity=HF,
                status=STATUS_INVALID,
                solver_wall_time_s=self.failure_hf_wall_s,
                mesh_wall_time_s=0.0,
                extraction_wall_time_s=0.0,
                failure_reason="convergence_quality",
            )
        lmh, cp_excess, dp_per_l = self.predict(d_p_mm, d_h_mm, fidelity)
        wall = self.reference_hf_wall_s if fidelity == HF else cost_ratio * self.reference_hf_wall_s
        return PillarOutcome(
            d_p_mm=d_p_mm,
            d_h_mm=d_h_mm,
            fidelity=fidelity,
            status=STATUS_VALID,
            lmh=lmh,
            cp_average=1.0 + cp_excess,
            dp_per_l=dp_per_l,
            mesh_wall_time_s=0.0,
            solver_wall_time_s=wall,
            extraction_wall_time_s=0.0,
        )

    def replay_table(self, cost_ratio: float) -> ReplayTable:
        rows: dict[tuple[float, float, str], PillarOutcome] = {}
        for point in self.points:
            for fidelity in FIDELITIES:
                outcome = self.outcome(point.d_p_mm, point.d_h_mm, fidelity, cost_ratio=cost_ratio)
                rows[outcome.key] = outcome
        return ReplayTable(rows)

    def best_feasible(self) -> dict[str, float] | None:
        best: dict[str, float] | None = None
        for point in self.points:
            if self.hf_fails(point.d_p_mm, point.d_h_mm):
                continue
            lmh, cp_excess, dp_per_l = self.predict(point.d_p_mm, point.d_h_mm, HF)
            if cp_excess > self.cp_excess_max or dp_per_l > self.dp_max_pa_per_m:
                continue
            if best is None or lmh > best["lmh"]:
                best = {
                    "d_p_mm": point.d_p_mm,
                    "d_h_mm": point.d_h_mm,
                    "lmh": lmh,
                    "cp_excess": cp_excess,
                    "dp_per_l": dp_per_l,
                }
        return best

    def initials(self, seed: int) -> tuple[PillarPoint, ...]:
        if seed == 0:
            return self.seed0
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 1:
            raise ValueError(f"seed must be an integer >= 0, got {seed!r}.")
        drawn = draw_maximin_lhs(
            d_p_min_mm=self.box[0],
            d_p_max_mm=self.box[1],
            d_h_min_mm=self.box[2],
            d_h_max_mm=self.box[3],
            d_f_mm=self.d_f_mm,
            candidate_grid=self.grid,
            n=len(self.seed0),
            seed=seed,
            m_min_mm=self.m_min_mm,
        )
        return _snap(drawn, self.points)

    def _mean(self, model: GaussianProcess, d_p_mm: float, d_h_mm: float) -> float:
        mean, _variance = self._dist(model, d_p_mm, d_h_mm)
        return mean

    def _dist(self, model: GaussianProcess, d_p_mm: float, d_h_mm: float) -> tuple[float, float]:
        x = np.asarray([_scale(d_p_mm, d_h_mm, self.box)], dtype=float)
        mean, variance = model.predict(x)
        return float(mean[0]), float(variance[0])


def fit_surrogate(
    rows: Sequence[Observation],
    *,
    repo_root: Path,
    candidate_grid: int | None = None,
) -> Surrogate:
    """Fit the truths. ``candidate_grid`` defaults to the v2 experiment."""
    payload = _v2_payload(repo_root)
    box = (
        float(payload["design_box"]["d_p_mm"][0]),
        float(payload["design_box"]["d_p_mm"][1]),
        float(payload["design_box"]["d_h_mm"][0]),
        float(payload["design_box"]["d_h_mm"][1]),
    )
    grid = int(payload["candidate_grid"] if candidate_grid is None else candidate_grid)
    if grid < 2:
        raise ValueError(f"candidate_grid must be >= 2, got {grid}.")
    settings = _load_fidelity_settings(repo_root / payload["fidelity_table"], FIDELITIES)
    m_min = max(float(settings[name]["m_min"]) for name in FIDELITIES)
    d_f = float(payload["d_f_mm"])
    hf_rows = _collapse(rows, HF_M, box, d_f)
    lf_rows = _collapse(rows, LF_D, box, d_f)
    valid_hf = [row for row in hf_rows if row.status == STATUS_VALID_NAME and _complete(row)]
    if not valid_hf:
        raise ValueError("No valid HF_m rows with LMH, CP excess, and dP/L.")
    hf_models = {
        name: _fit_values(valid_hf, name, box)
        for name in _QOI
    }
    paired = _pairs(valid_hf, [row for row in lf_rows if row.status == STATUS_VALID_NAME and _complete(row)])
    delta = {name: _fit_delta(paired, name, box) for name in _QOI}
    gate = [row for row in hf_rows if row.status == STATUS_GATE_FAIL]
    labels_x: list[tuple[float, float]] = []
    labels: list[float] = []
    for row in valid_hf:
        labels_x.append((row.d_p_mm, row.d_h_mm))
        labels.append(1.0)
    for row in gate:
        labels_x.append((row.d_p_mm, row.d_h_mm))
        labels.append(0.0)
    failure = None
    if labels and min(labels) != max(labels):
        failure = _fit_labels(labels_x, labels, box)
    reference = _median_cost(rows, HF_M, STATUS_VALID_NAME, kind="wall")
    failure_cost = _median_cost(rows, HF_M, STATUS_GATE_FAIL, kind="recorded")
    if reference is None:
        raise ValueError("Valid HF_m rows have no positive wall_s to use as the reference HF cost.")
    if failure_cost is None:
        raise ValueError(
            "HF_m gate-fail rows have no positive recorded cost. "
            "Failures are charged that median, not one reference HF."
        )
    points = tuple(_grid(box, grid, d_f, m_min))
    seed0 = _snap(
        tuple(PillarPoint(float(item["d_p_mm"]), float(item["d_h_mm"])) for item in payload["initial_hf"]),
        points,
    )
    return Surrogate(
        box=box,
        grid=grid,
        d_f_mm=d_f,
        m_min_mm=m_min,
        points=points,
        hf=hf_models,
        delta=delta,
        failure=failure,
        reference_hf_wall_s=reference,
        failure_hf_wall_s=failure_cost,
        n_hf_valid=len(valid_hf),
        n_hf_gate_fail=len(gate),
        n_pairs=len(paired),
        cp_excess_max=float(payload["cp_excess_max"]),
        dp_max_pa_per_m=float(payload["dp_max_pa_per_m"]),
        seed0=seed0,
    )


def run_benchmark(
    rows: Sequence[Observation],
    out_dir: Path,
    *,
    repo_root: Path,
    seeds: Sequence[int] = tuple(range(DEFAULT_SEEDS)),
    ratios: Sequence[float] = DEFAULT_RATIOS,
    budget_hf_equivalent: float | None = None,
    max_sequential: int | None = None,
    candidate_grid: int | None = None,
) -> dict[str, Any]:
    """Run both methods, with and without failure-aware EI, and write the tables."""
    surrogate = fit_surrogate(rows, repo_root=repo_root, candidate_grid=candidate_grid)
    payload = _v2_payload(repo_root)
    settings = _load_fidelity_settings(repo_root / payload["fidelity_table"], FIDELITIES)
    budget = (
        TARGET_BUDGET_WALL_H * 3600.0 / surrogate.reference_hf_wall_s
        if budget_hf_equivalent is None
        else float(budget_hf_equivalent)
    )
    sequential = int(payload["max_sequential"] if max_sequential is None else max_sequential)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for ratio in ratios:
        ratio = float(ratio)
        if ratio <= 0.0:
            raise ValueError(f"cost ratio must be positive, got {ratio}.")
        table = surrogate.replay_table(ratio)
        for seed in seeds:
            initials = surrogate.initials(int(seed))
            for method in _METHODS:
                for failure_aware in (False, True):
                    runs.append(
                        _run_one(
                            surrogate,
                            table,
                            settings,
                            out_dir / "runs" / _run_name(method, failure_aware, ratio, int(seed)),
                            method=method,
                            seed=int(seed),
                            cost_ratio=ratio,
                            initials=initials,
                            failure_aware=failure_aware,
                            budget=budget,
                            max_sequential=sequential,
                        )
                    )
    summary = _summarize(runs)
    validation = _validate(surrogate, runs)
    _write_runs(out_dir / "runs.csv", runs)
    _write_summary(out_dir / "summary.csv", summary)
    _write_json(out_dir / "validation.json", validation)
    text = format_validation(validation)
    (out_dir / "validation.md").write_text(text, encoding="utf-8")
    _write_json(
        out_dir / "truth.json",
        {
            "n_hf_valid": surrogate.n_hf_valid,
            "n_hf_gate_fail": surrogate.n_hf_gate_fail,
            "n_pairs": surrogate.n_pairs,
            "reference_hf_wall_s": surrogate.reference_hf_wall_s,
            "failure_hf_wall_s": surrogate.failure_hf_wall_s,
            "failure_hf_equivalent": surrogate.failure_hf_wall_s / surrogate.reference_hf_wall_s,
            "budget_hf_equivalent": budget,
            "best_feasible": surrogate.best_feasible(),
            "grid": surrogate.grid,
            "n_candidates": len(surrogate.points),
        },
    )
    plot = out_dir / "cost_ratio.png"
    write_cost_plot(plot, summary, validation["break_even"])
    return {
        "runs": runs,
        "summary": summary,
        "validation": validation,
        "validation_text": text,
        "out_dir": out_dir,
        "plot": plot,
    }


def break_even(hf_by_ratio: Mapping[float, float], mf_by_ratio: Mapping[float, float]) -> float | None:
    """Cost ratio where the MFBO median crosses the HF-only median.

    Ratios are interpolated in ratio space. None when the two curves do
    not cross inside the supplied ratios.
    """
    shared = sorted(set(hf_by_ratio) & set(mf_by_ratio))
    diffs = [(ratio, float(mf_by_ratio[ratio]) - float(hf_by_ratio[ratio])) for ratio in shared]
    for ratio, diff in diffs:
        if diff == 0.0:
            return float(ratio)
    for (left_ratio, left), (right_ratio, right) in zip(diffs, diffs[1:]):
        if left * right < 0.0:
            span = left - right
            return float(left_ratio + (left / span) * (right_ratio - left_ratio))
    return None


def in_live_optimum_region(
    d_p_mm: float | None,
    d_h_mm: float | None,
    *,
    box: tuple[float, float, float, float],
    grid: int,
) -> bool:
    if d_p_mm is None or d_h_mm is None:
        return False
    step_p = (box[1] - box[0]) / (grid - 1)
    step_h = (box[3] - box[2]) / (grid - 1)
    return (
        abs(float(d_p_mm) - LIVE_D_P_MM) <= step_p + 1.0e-9
        and abs(float(d_h_mm) - LIVE_D_H_MM) <= step_h + 1.0e-9
    )


def format_validation(report: Mapping[str, Any]) -> str:
    truth = report.get("truth_best") or {}
    lines = [
        "# Synthetic seed 0 at LF/HF cost ratio 0.54",
        "",
        "Live v2, host report 2026-10-19: both methods reached "
        f"D_p {LIVE_D_P_MM} mm, d_h {LIVE_D_H_MM} mm, HF LMH {LIVE_HF_LMH}. "
        f"First feasible HF-equivalent {LIVE_FIRST_FEASIBLE[METHOD_HF]} (hf_bo) "
        f"and {LIVE_FIRST_FEASIBLE[METHOD_MF]} (mfbo). "
        f"Cost to the optimum {LIVE_NEAR_OPTIMUM[METHOD_HF]} and {LIVE_NEAR_OPTIMUM[METHOD_MF]}. "
        f"Both spent {LIVE_FAILURE_FRACTION[0]:.0%}–{LIVE_FAILURE_FRACTION[1]:.0%} "
        "of the charged cost on gate-failing HF runs.",
        "",
        "Same optimum region means the truth's best feasible grid point and "
        "both seed-0 recommendations sit within one candidate-grid step of that live point.",
        "",
        f"Truth best feasible: D_p {truth.get('d_p_mm')} mm, d_h {truth.get('d_h_mm')} mm, "
        f"LMH {truth.get('lmh')}.",
        f"Same optimum region: {report.get('same_optimum_region')}.",
        "",
        "| | live hf_bo | live mfbo | synthetic hf_bo | synthetic mfbo |",
        "|---|---:|---:|---:|---:|",
    ]
    synthetic = report.get("synthetic") or {}
    lines.append(
        "| first feasible | "
        f"{LIVE_FIRST_FEASIBLE[METHOD_HF]} | {LIVE_FIRST_FEASIBLE[METHOD_MF]} | "
        f"{_cell(synthetic, METHOD_HF, 'cost_to_first_feasible')} | "
        f"{_cell(synthetic, METHOD_MF, 'cost_to_first_feasible')} |"
    )
    lines.append(
        "| near optimum (0.1%) | "
        f"{LIVE_NEAR_OPTIMUM[METHOD_HF]} | {LIVE_NEAR_OPTIMUM[METHOD_MF]} | "
        f"{_cell(synthetic, METHOD_HF, 'cost_to_near_optimum')} | "
        f"{_cell(synthetic, METHOD_MF, 'cost_to_near_optimum')} |"
    )
    lines.append(
        "| failure fraction | "
        f"{LIVE_FAILURE_FRACTION[0]}–{LIVE_FAILURE_FRACTION[1]} | "
        f"{LIVE_FAILURE_FRACTION[0]}–{LIVE_FAILURE_FRACTION[1]} | "
        f"{_cell(synthetic, METHOD_HF, 'failure_fraction')} | "
        f"{_cell(synthetic, METHOD_MF, 'failure_fraction')} |"
    )
    lines.append("")
    lines.append(f"Break-even cost ratio, plain acquisition: {report.get('break_even', {}).get('plain')}.")
    lines.append(
        "Break-even cost ratio, failure-aware acquisition: "
        f"{report.get('break_even', {}).get('failure_aware')}."
    )
    lines.append("")
    return "\n".join(lines)


def write_cost_plot(path: Path, summary: Sequence[Mapping[str, Any]], break_evens: Mapping[str, float | None]) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7.2, 4.6))
    styles = {
        (METHOD_HF, False): ("C0", "-"),
        (METHOD_MF, False): ("C1", "-"),
        (METHOD_HF, True): ("C0", "--"),
        (METHOD_MF, True): ("C1", "--"),
    }
    for method, aware in styles:
        rows = [
            row
            for row in summary
            if row["metric"] == "cost_to_near_optimum"
            and row["method"] == method
            and bool(row["failure_aware"]) is aware
            and row["median"] is not None
        ]
        rows.sort(key=lambda item: float(item["cost_ratio"]))
        if not rows:
            continue
        xs = [float(row["cost_ratio"]) for row in rows]
        ys = [float(row["median"]) for row in rows]
        low = [float(row["median"]) - float(row["q25"]) for row in rows]
        high = [float(row["q75"]) - float(row["median"]) for row in rows]
        color, linestyle = styles[(method, aware)]
        label = method if not aware else f"{method} failure-aware"
        axis.errorbar(xs, ys, yerr=[low, high], color=color, linestyle=linestyle, marker="o", capsize=3, label=label)
    marked = False
    for name, ratio in break_evens.items():
        if ratio is None:
            continue
        axis.axvline(
            float(ratio),
            color="0.2" if name == "plain" else "0.45",
            linestyle=":" if name == "plain" else "-.",
            linewidth=1.0,
            label=f"break-even {name} {float(ratio):.3g}",
        )
        marked = True
    if not marked:
        axis.text(0.02, 0.95, "no break-even in range", transform=axis.transAxes, va="top")
    axis.set_xlabel("LF/HF cost ratio")
    axis.set_ylabel("Median cost to within 0.1% of the best feasible truth")
    axis.grid(True, color="0.9", linewidth=0.8)
    _handles, labels = axis.get_legend_handles_labels()
    if labels:
        axis.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def _run_one(
    surrogate: Surrogate,
    table: ReplayTable,
    settings: Mapping[str, dict[str, Any]],
    destination: Path,
    *,
    method: str,
    seed: int,
    cost_ratio: float,
    initials: tuple[PillarPoint, ...],
    failure_aware: bool,
    budget: float,
    max_sequential: int,
) -> dict[str, Any]:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Refusing to reuse a non-empty run directory: {destination}.")
    config = _config(
        surrogate,
        settings,
        table,
        method=method,
        seed=seed,
        cost_ratio=cost_ratio,
        initials=initials,
        failure_aware=failure_aware,
        budget=budget,
        max_sequential=max_sequential,
    )

    def evaluate(design: PillarPoint, fidelity: str, lookup: ReplayTable = table) -> PillarOutcome:
        return replace(lookup.lookup(design.d_p_mm, design.d_h_mm, fidelity))

    summary = run_optimization(config, evaluate, destination, failure_aware=failure_aware)
    history = json.loads((destination / "history.json").read_text(encoding="utf-8"))
    measured = _measure(history, surrogate)
    final = summary.get("final_hf_validated") or {}
    return {
        "method": method,
        "failure_aware": failure_aware,
        "cost_ratio": cost_ratio,
        "seed": seed,
        "d_p_mm": final.get("d_p_mm"),
        "d_h_mm": final.get("d_h_mm"),
        "hf_lmh": final.get("hf_lmh"),
        "cumulative_hf_equivalent": summary.get("cumulative_hf_equivalent"),
        "n_failures": summary.get("n_failures"),
        "directory": str(destination),
        **measured,
    }


def _config(
    surrogate: Surrogate,
    settings: Mapping[str, dict[str, Any]],
    table: ReplayTable,
    *,
    method: str,
    seed: int,
    cost_ratio: float,
    initials: tuple[PillarPoint, ...],
    failure_aware: bool,
    budget: float,
    max_sequential: int,
) -> Experiment3D:
    experiment_id = f"synthetic-s{seed}"
    problem = {
        "experiment_id": experiment_id,
        "method": method,
        "mode": MODE_REPLAY,
        "seed": seed,
        "failure_aware": failure_aware,
        "lf_over_hf_cost_ratio": cost_ratio,
        "candidate_grid": surrogate.grid,
        "initial": [{"d_p_mm": point.d_p_mm, "d_h_mm": point.d_h_mm} for point in initials],
    }
    return Experiment3D(
        method=method,
        mode=MODE_REPLAY,
        experiment_id=experiment_id,
        seed=seed,
        run_id="u0p2_p6M",
        d_f_mm=surrogate.d_f_mm,
        d_p_min_mm=surrogate.box[0],
        d_p_max_mm=surrogate.box[1],
        d_h_min_mm=surrogate.box[2],
        d_h_max_mm=surrogate.box[3],
        cp_excess_max=surrogate.cp_excess_max,
        dp_max_pa_per_m=surrogate.dp_max_pa_per_m,
        budget_hf_equivalent=budget,
        max_sequential=max_sequential,
        reference_hf_wall_s=surrogate.reference_hf_wall_s,
        lf_over_hf_cost_ratio=cost_ratio,
        candidate_grid=surrogate.grid,
        initial_hf=initials,
        initial_lf=initials,
        fidelity_settings=dict(settings),
        replay=table,
        problem=problem,
        geometry=GEOMETRY_PREGENERATED,
        budget_stop=BUDGET_STOP_EXPECTED,
        note=(
            "Synthetic replay. HF truth, LF discrepancy, and the HF gate are "
            "Gaussian processes fit to exported leaves. A gate failure is charged "
            "the median recorded HF gate-fail cost."
        ),
    )


def _measure(history: Sequence[Mapping[str, Any]], surrogate: Surrogate) -> dict[str, Any]:
    best = surrogate.best_feasible()
    target = None if best is None else float(best["lmh"]) * NEAR_OPTIMUM_FRACTION
    first: float | None = None
    near: float | None = None
    fail_wall = 0.0
    for row in history:
        cost = float(row["cumulative_hf_equivalent"])
        charged = row.get("charged_wall_s")
        if row.get("status") != STATUS_VALID and charged is not None:
            fail_wall += float(charged)
        feasible_hf = row.get("fidelity") == HF and row.get("feasible") is True
        if first is None and feasible_hf:
            first = cost
        lmh = row.get("lmh")
        if near is None and feasible_hf and target is not None and lmh is not None and float(lmh) >= target:
            near = cost
    total = float(history[-1]["cumulative_wall_s"]) if history else 0.0
    return {
        "cost_to_first_feasible": first,
        "cost_to_near_optimum": near,
        "failure_hf_equivalent": fail_wall / surrogate.reference_hf_wall_s,
        "failure_fraction": (fail_wall / total) if total > 0.0 else 0.0,
    }


def _summarize(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    metrics = (
        "cost_to_first_feasible",
        "cost_to_near_optimum",
        "failure_hf_equivalent",
        "failure_fraction",
    )
    groups: dict[tuple[str, bool, float], list[Mapping[str, Any]]] = {}
    for run in runs:
        key = (str(run["method"]), bool(run["failure_aware"]), float(run["cost_ratio"]))
        groups.setdefault(key, []).append(run)
    rows = []
    for (method, aware, ratio), items in sorted(groups.items(), key=lambda item: (item[0][2], item[0][0], item[0][1])):
        for metric in metrics:
            stats = _iqr([item.get(metric) for item in items])
            rows.append(
                {
                    "method": method,
                    "failure_aware": aware,
                    "cost_ratio": ratio,
                    "metric": metric,
                    **stats,
                }
            )
    return rows


def _validate(surrogate: Surrogate, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    truth = surrogate.best_feasible()
    synthetic: dict[str, dict[str, Any]] = {}
    region = in_live_optimum_region(
        None if truth is None else truth["d_p_mm"],
        None if truth is None else truth["d_h_mm"],
        box=surrogate.box,
        grid=surrogate.grid,
    )
    for method in _METHODS:
        match = [
            run
            for run in runs
            if run["method"] == method
            and run["failure_aware"] is False
            and int(run["seed"]) == 0
            and abs(float(run["cost_ratio"]) - 0.54) <= 1.0e-12
        ]
        if not match:
            region = False
            continue
        run = match[0]
        synthetic[method] = {
            "d_p_mm": run.get("d_p_mm"),
            "d_h_mm": run.get("d_h_mm"),
            "hf_lmh": run.get("hf_lmh"),
            "cost_to_first_feasible": run.get("cost_to_first_feasible"),
            "cost_to_near_optimum": run.get("cost_to_near_optimum"),
            "failure_fraction": run.get("failure_fraction"),
            "in_live_region": in_live_optimum_region(
                run.get("d_p_mm"),
                run.get("d_h_mm"),
                box=surrogate.box,
                grid=surrogate.grid,
            ),
        }
        region = region and bool(synthetic[method]["in_live_region"])
    return {
        "truth_best": truth,
        "synthetic": synthetic,
        "same_optimum_region": region and len(synthetic) == len(_METHODS),
        "live": {
            "d_p_mm": LIVE_D_P_MM,
            "d_h_mm": LIVE_D_H_MM,
            "hf_lmh": LIVE_HF_LMH,
            "first_feasible": LIVE_FIRST_FEASIBLE,
            "near_optimum": LIVE_NEAR_OPTIMUM,
            "failure_fraction": list(LIVE_FAILURE_FRACTION),
        },
        "break_even": {
            "plain": _break_even_of(runs, False),
            "failure_aware": _break_even_of(runs, True),
        },
    }


def _break_even_of(runs: Sequence[Mapping[str, Any]], failure_aware: bool) -> float | None:
    summary = [
        row
        for row in _summarize(
            [run for run in runs if bool(run["failure_aware"]) is failure_aware]
        )
        if row["metric"] == "cost_to_near_optimum" and row["median"] is not None
    ]
    hf = {float(row["cost_ratio"]): float(row["median"]) for row in summary if row["method"] == METHOD_HF}
    mf = {float(row["cost_ratio"]): float(row["median"]) for row in summary if row["method"] == METHOD_MF}
    return break_even(hf, mf)


def _iqr(values: Sequence[Any]) -> dict[str, Any]:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return {"n_runs": len(values), "n": 0, "median": None, "q25": None, "q75": None, "iqr": None}
    q25, median, q75 = (float(item) for item in np.percentile(finite, [25, 50, 75]))
    return {
        "n_runs": len(values),
        "n": len(finite),
        "median": median,
        "q25": q25,
        "q75": q75,
        "iqr": q75 - q25,
    }


def _collapse(
    rows: Sequence[Observation],
    level: str,
    box: tuple[float, float, float, float],
    d_f_mm: float,
) -> list[Observation]:
    groups: dict[tuple[float, float], list[Observation]] = {}
    for row in rows:
        if row.fidelity != level or abs(row.d_f_mm - d_f_mm) > 1.0e-9:
            continue
        if not _in_box(row.d_p_mm, row.d_h_mm, box):
            continue
        groups.setdefault((round(row.d_p_mm, 6), round(row.d_h_mm, 6)), []).append(row)
    chosen = []
    rank = {STATUS_VALID_NAME: 2, STATUS_GATE_FAIL: 1, "crash": 0}
    for items in groups.values():
        status = max(items, key=lambda item: rank[item.status]).status
        pool = [item for item in items if item.status == status]
        chosen.append(_median_row(pool, status))
    return chosen


def _median_row(items: Sequence[Observation], status: str) -> Observation:
    first = items[0]

    def median(name: str) -> float | None:
        values = [getattr(item, name) for item in items if getattr(item, name) is not None]
        if not values:
            return None
        return float(np.median(np.asarray(values, dtype=float)))

    return Observation(
        d_p_mm=first.d_p_mm,
        d_h_mm=first.d_h_mm,
        d_f_mm=first.d_f_mm,
        fidelity=first.fidelity,
        status=status,
        lmh=median("lmh"),
        cp_excess=median("cp_excess"),
        dp_per_l=median("dp_per_l"),
        wall_s=median("wall_s"),
        recorded_cost_s=median("recorded_cost_s"),
        geo_id=first.geo_id,
        mesh_id=first.mesh_id,
        run_id=first.run_id,
        leaf_path=first.leaf_path,
    )


def _pairs(hf_rows: Sequence[Observation], lf_rows: Sequence[Observation]) -> list[tuple[Observation, Observation]]:
    low = {(round(row.d_p_mm, 6), round(row.d_h_mm, 6)): row for row in lf_rows}
    paired = []
    for row in hf_rows:
        match = low.get((round(row.d_p_mm, 6), round(row.d_h_mm, 6)))
        if match is not None:
            paired.append((row, match))
    return paired


def _fit_values(rows: Sequence[Observation], name: str, box: tuple[float, float, float, float]) -> GaussianProcess:
    return _fit_labels(
        [(row.d_p_mm, row.d_h_mm) for row in rows],
        [float(getattr(row, name)) for row in rows],
        box,
    )


def _fit_delta(
    paired: Sequence[tuple[Observation, Observation]],
    name: str,
    box: tuple[float, float, float, float],
) -> GaussianProcess | None:
    if not paired:
        return None
    return _fit_labels(
        [(high.d_p_mm, high.d_h_mm) for high, _low in paired],
        [float(getattr(low, name)) - float(getattr(high, name)) for high, low in paired],
        box,
    )


def _fit_labels(
    points: Sequence[tuple[float, float]],
    values: Sequence[float],
    box: tuple[float, float, float, float],
) -> GaussianProcess:
    x = stack_xy([_scale(d_p, d_h, box) for d_p, d_h in points])
    return GaussianProcess().fit(x, np.asarray(values, dtype=float))


def _median_cost(rows: Sequence[Observation], level: str, status: str, *, kind: str) -> float | None:
    values = []
    for row in rows:
        if row.fidelity != level or row.status != status or abs(row.d_f_mm - D_F_MM) > 1.0e-9:
            continue
        value = row.wall_s if kind == "wall" else row.recorded_cost_s
        if value is None and kind == "recorded":
            value = row.wall_s
        if value is not None and float(value) > 0.0:
            values.append(float(value))
    if not values:
        return None
    return float(np.median(np.asarray(values, dtype=float)))


def _complete(row: Observation) -> bool:
    return row.lmh is not None and row.cp_excess is not None and row.dp_per_l is not None


def _grid(
    box: tuple[float, float, float, float],
    grid: int,
    d_f_mm: float,
    m_min_mm: float,
) -> list[PillarPoint]:
    span = grid - 1
    points = []
    for index_p in range(grid):
        for index_h in range(grid):
            d_p = box[0] + (box[1] - box[0]) * index_p / span
            d_h = box[2] + (box[3] - box[2]) * index_h / span
            if geometry_block_reason(d_p, d_h, d_f_mm, m_min_mm) is not None:
                continue
            points.append(PillarPoint(d_p, d_h))
    if not points:
        raise ValueError("The opening-gap check removed every candidate.")
    return points


def _snap(points: tuple[PillarPoint, ...], grid: Sequence[PillarPoint]) -> tuple[PillarPoint, ...]:
    index = {point.key(): point for point in grid}
    snapped = []
    for point in points:
        found = index.get(point.key())
        if found is None:
            raise ValueError(
                f"Design d_p_mm={point.d_p_mm} d_h_mm={point.d_h_mm} is not on the replay grid."
            )
        snapped.append(found)
    return tuple(snapped)


def _scale(
    d_p_mm: float,
    d_h_mm: float,
    box: tuple[float, float, float, float],
) -> tuple[float, float]:
    return (
        (float(d_p_mm) - box[0]) / (box[1] - box[0]),
        (float(d_h_mm) - box[2]) / (box[3] - box[2]),
    )


def _in_box(d_p_mm: float, d_h_mm: float, box: tuple[float, float, float, float]) -> bool:
    return box[0] - 1.0e-9 <= d_p_mm <= box[1] + 1.0e-9 and box[2] - 1.0e-9 <= d_h_mm <= box[3] + 1.0e-9


def _v2_payload(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "configs" / "mfbo_3d_experiment.v2.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"v2 experiment must be an object: {path}")
    return payload


def _run_name(method: str, failure_aware: bool, ratio: float, seed: int) -> str:
    aware = "aware" if failure_aware else "plain"
    token = f"{ratio:.2f}".replace(".", "p")
    return f"{method}_{aware}_r{token}_s{seed}"


def _cell(synthetic: Mapping[str, Any], method: str, name: str) -> str:
    row = synthetic.get(method) or {}
    value = row.get(name)
    if value is None:
        return ""
    return f"{float(value):.4g}"


def _write_runs(path: Path, runs: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "method",
        "failure_aware",
        "cost_ratio",
        "seed",
        "d_p_mm",
        "d_h_mm",
        "hf_lmh",
        "cost_to_first_feasible",
        "cost_to_near_optimum",
        "failure_hf_equivalent",
        "failure_fraction",
        "cumulative_hf_equivalent",
        "n_failures",
        "directory",
    )
    _write_csv(path, fields, runs)


def _write_summary(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "method",
        "failure_aware",
        "cost_ratio",
        "metric",
        "n_runs",
        "n",
        "median",
        "q25",
        "q75",
        "iqr",
    )
    _write_csv(path, fields, rows)


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fields})


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
