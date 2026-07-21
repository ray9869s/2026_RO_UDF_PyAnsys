"""Pytest path setup for characterization tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `from helpers import ...` when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Scripts import sibling modules such as `_solver_common`.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "My_CFD_Project" / "01_Scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
