"""Pytest path setup for characterization tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `from helpers import ...` when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_host_lock(tmp_path_factory, monkeypatch):
    """Queue runners in tests never touch the real ~/.pyfluent host lock."""
    monkeypatch.setenv("RO_HOST_LOCK", str(tmp_path_factory.mktemp("hostlock") / "fluent_host.lock"))
    monkeypatch.setenv("RO_ORPHAN_GRACE_S", "0")
