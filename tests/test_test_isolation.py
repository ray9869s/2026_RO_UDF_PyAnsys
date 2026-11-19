"""The suite cannot launch Fluent or see the host's production data root."""

from __future__ import annotations

import os

import pytest


def test_launch_fluent_is_blocked_in_tests():
    pyfluent = pytest.importorskip("ansys.fluent.core")
    with pytest.raises(RuntimeError, match="pytest must never launch Fluent"):
        pyfluent.launch_fluent(mode="meshing")


def test_host_ro_data_root_is_not_inherited():
    assert "RO_DATA_ROOT" not in os.environ
