"""2D pilot optimization. The CFD evaluator is injected; Fluent is not imported here."""

from ro_2d_pilot.optimization.experiment import run_optimization

__all__ = ["run_optimization"]
