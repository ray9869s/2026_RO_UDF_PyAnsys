"""Deterministic initial designs shared by HF-only BO and MFBO.

Four high-fidelity points: the solved center and three corners. The
low-d, short-pitch corner is left out so it can still be discovered.
That corner is feasible under the pilot pressure limit and has the
highest screened feasible LMH, so putting it in the initial set would
end the comparison before any sequential decision.

MFBO also evaluates those same four designs at medium. Their solver
cost is charged. They are not part of the common HF initialization.
The other screened very_fine results stay out of the initial GP.
"""

from __future__ import annotations

from ro_2d_pilot.optimization.domain import Design

COMMON_HF_INITIAL = (
    Design(0.40e-3, 4.0e-3),
    Design(0.50e-3, 3.0e-3),
    Design(0.50e-3, 5.0e-3),
    Design(0.30e-3, 5.0e-3),
)
MFBO_LF_INITIAL = COMMON_HF_INITIAL
