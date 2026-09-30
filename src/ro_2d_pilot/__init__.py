"""2D RO-membrane spacer pilot, separate from the 3D campaign package.

This package plans and, on a Fluent host, solves one parameterized 2D
channel (diameter ``d``, pitch ``L``). The optimization subpackage can
drive that solver, or a future evaluator with the same design/fidelity
interface. Case files belong under ``RO_2D_DATA_ROOT``, never in the
git tree and never in the 3D ``RO_DATA_ROOT`` campaign folders.
"""

__version__ = "0.1.0"
