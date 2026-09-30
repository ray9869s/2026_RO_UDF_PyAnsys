"""Equal-budget accounting in HF-equivalent solver time."""

from __future__ import annotations

from dataclasses import dataclass

from ro_2d_pilot.optimization.cost import hf_equivalent, normalized_fidelity_cost
from ro_2d_pilot.optimization.domain import FIDELITY_HIGH, FIDELITY_LOW

# About four initial HF runs plus room for several LF/HF decisions.
PILOT_HF_EQUIVALENT_BUDGET = 8.0
# Caps a live session even if every extra query is a cheap LF run.
MAX_SEQUENTIAL_EVALUATIONS = 6


@dataclass
class Budget:
    limit_hf_equivalent: float = PILOT_HF_EQUIVALENT_BUDGET
    max_sequential: int = MAX_SEQUENTIAL_EVALUATIONS
    solver_s: float = 0.0
    wall_s: float = 0.0
    wall_known: bool = True
    n_low: int = 0
    n_high: int = 0
    n_sequential: int = 0
    n_failures: int = 0
    imputed_costs: int = 0

    @property
    def hf_equivalent(self) -> float:
        return hf_equivalent(self.solver_s)

    def expected_hf_equivalent(self, fidelity: str) -> float:
        return normalized_fidelity_cost(fidelity)

    def can_afford(self, fidelity: str, *, sequential: bool) -> bool:
        if sequential and self.n_sequential >= self.max_sequential:
            return False
        return self.hf_equivalent + self.expected_hf_equivalent(fidelity) <= (
            self.limit_hf_equivalent + 1.0e-9
        )

    def charge(
        self,
        fidelity: str,
        *,
        solver_s: float | None,
        wall_s: float | None,
        sequential: bool,
        failed: bool,
        imputed: bool,
    ) -> None:
        if fidelity == FIDELITY_LOW:
            self.n_low += 1
        elif fidelity == FIDELITY_HIGH:
            self.n_high += 1
        else:
            raise ValueError(f"Unknown fidelity {fidelity!r}.")
        if sequential:
            self.n_sequential += 1
        if failed:
            self.n_failures += 1
        if solver_s is None:
            from ro_2d_pilot.optimization.cost import reference_hf_solver_s

            solver_s = self.expected_hf_equivalent(fidelity) * reference_hf_solver_s()
            imputed = True
        if imputed:
            self.imputed_costs += 1
        self.solver_s += float(solver_s)
        if wall_s is None:
            self.wall_known = False
        else:
            self.wall_s += float(wall_s)

