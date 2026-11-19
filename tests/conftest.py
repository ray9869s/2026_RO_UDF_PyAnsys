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
    # The Windows host would otherwise list real processes (PowerShell) for
    # every queue test, and a live Fluent there would refuse the test job.
    monkeypatch.setattr("ro.host_lock.windows_processes", lambda: [])


@pytest.fixture(autouse=True)
def _no_production_data_root(monkeypatch):
    """Tests never inherit the host shell's RO_DATA_ROOT (C:\\ro_data there).

    A test that needs a data root sets one under tmp_path itself.
    """
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)


def _refuse_fluent_launch(*_args, **_kwargs):
    raise RuntimeError("pytest must never launch Fluent (blocked in tests/conftest.py).")


@pytest.fixture(autouse=True, scope="session")
def _no_fluent_launch():
    """Block pyfluent.launch_fluent for the whole session.

    On the Windows host a test once reached a real meshing launch because
    the production CAD it expected to be missing exists there.
    """
    patcher = pytest.MonkeyPatch()
    try:
        import ansys.fluent.core as pyfluent  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - not installed: nothing to block
        pyfluent = None
    if pyfluent is not None:
        patcher.setattr(pyfluent, "launch_fluent", _refuse_fluent_launch, raising=False)
        launcher = getattr(getattr(pyfluent, "launcher", None), "launcher", None)
        if launcher is not None and hasattr(launcher, "launch_fluent"):
            patcher.setattr(launcher, "launch_fluent", _refuse_fluent_launch)
    yield
    patcher.undo()
