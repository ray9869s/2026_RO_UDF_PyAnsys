"""Pytest path setup for characterization tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `from helpers import ...` when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
