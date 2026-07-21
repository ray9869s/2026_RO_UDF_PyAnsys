"""Tests for 03b shear export exit-code resolver (F-03 #2)."""

from __future__ import annotations

import sys
import types

from helpers import POST_DIR, load_module


def load_shear_export():
    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
        "numpy": types.ModuleType("numpy"),
        "matplotlib": types.ModuleType("matplotlib"),
        "matplotlib.pyplot": types.ModuleType("matplotlib.pyplot"),
        "matplotlib.tri": types.ModuleType("matplotlib.tri"),
    }
    stubs["matplotlib"].use = lambda *args, **kwargs: None

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module(
            "shear_export_under_test",
            POST_DIR / "03b_pyfluent_shear_contour_export.py",
        )
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_success_exits_zero():
    mod = load_shear_export()
    assert mod.resolve_shear_export_exit_code(mod.STATUS_OK) == 0


def test_warn_exits_one():
    mod = load_shear_export()
    assert mod.resolve_shear_export_exit_code(mod.STATUS_WARN) == 1


def test_failed_exits_two():
    mod = load_shear_export()
    assert mod.resolve_shear_export_exit_code(mod.STATUS_FAIL) == 2
