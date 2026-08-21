"""Import-safety checks for executable scripts with no configured data root."""

from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

import pytest
import ro

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module


def _is_main_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _executable_scripts() -> tuple[Path, ...]:
    scripts = []
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(_is_main_guard(node) for node in ast.walk(tree)):
            scripts.append(path)
    return tuple(scripts)


EXECUTABLE_SCRIPTS = _executable_scripts()


def _script_id(path: Path) -> str:
    return path.relative_to(SCRIPTS_DIR).as_posix()


@pytest.mark.parametrize("script_path", EXECUTABLE_SCRIPTS, ids=_script_id)
def test_executable_script_imports_without_data_root(
    script_path,
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))

    stubs = {
        "ansys": types.ModuleType("ansys"),
        "ansys.fluent": types.ModuleType("ansys.fluent"),
        "ansys.fluent.core": types.ModuleType("ansys.fluent.core"),
        "matplotlib": types.ModuleType("matplotlib"),
        "matplotlib.pyplot": types.ModuleType("matplotlib.pyplot"),
        "matplotlib.ticker": types.ModuleType("matplotlib.ticker"),
        "matplotlib.tri": types.ModuleType("matplotlib.tri"),
    }
    stubs["matplotlib"].use = lambda *_args, **_kwargs: None
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    relative = script_path.relative_to(SCRIPTS_DIR).with_suffix("").as_posix()
    module_name = "_import_safety_" + re.sub(r"[^A-Za-z0-9_]", "_", relative)
    try:
        load_module(module_name, script_path)
    finally:
        sys.modules.pop(module_name, None)


def test_pytest_pythonpath_exposes_source_package():
    assert Path(ro.__file__).resolve().parent == REPO_ROOT / "src" / "ro"
