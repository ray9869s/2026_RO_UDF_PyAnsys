"""Cost-aware multi-fidelity BO using Kennedy-O'Hagan and Forrester's rule.

Low fidelity is medium. The target fidelity is very_fine. very_fine is the
pilot reference, not a grid-independent truth.
"""

from ro_2d_pilot.optimization.experiment import METHOD_MF

__all__ = ["METHOD_MF"]
