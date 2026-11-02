"""Intel MPI is the default Fluent launch flag on this host."""

from __future__ import annotations

import pytest

from helpers import (
    REPO_ROOT,
    load_run_config,
    load_solver_code,
    populate_valid_meshing_config,
    populate_valid_solver_config,
)
from ro.fluent_launch import (
    fluent_mpi_argument,
    resolve_fluent_mpi,
)

_LAUNCH_ROOTS = (REPO_ROOT / "scripts", REPO_ROOT / "src")


def _call_span(text, start):
    depth = 0
    index = text.find("(", start)
    for cursor in range(index, len(text)):
        if text[cursor] == "(":
            depth += 1
        elif text[cursor] == ")":
            depth -= 1
            if depth == 0:
                return text[start : cursor + 1]
    raise AssertionError("unclosed launch_fluent call")


def test_default_mpi_is_intel_and_empty_env_omits_the_flag(monkeypatch):
    monkeypatch.delenv("RO_FLUENT_MPI", raising=False)
    assert resolve_fluent_mpi(None) == "intel"
    assert resolve_fluent_mpi("openmpi") == "openmpi"
    assert fluent_mpi_argument("intel") == "-mpi=intel"
    monkeypatch.setenv("RO_FLUENT_MPI", "")
    assert resolve_fluent_mpi("intel") == ""
    assert fluent_mpi_argument("intel") == ""
    monkeypatch.setenv("RO_FLUENT_MPI", " msmpi ")
    assert resolve_fluent_mpi("intel") == "msmpi"
    assert fluent_mpi_argument(None) == "-mpi=msmpi"
    monkeypatch.delenv("RO_FLUENT_MPI", raising=False)
    with pytest.raises(ValueError, match="fluent_mpi"):
        resolve_fluent_mpi("intel mpi")


def test_solver_manifest_records_the_resolved_mpi(monkeypatch):
    monkeypatch.delenv("RO_FLUENT_MPI", raising=False)
    solver = load_solver_code("solver_fluent_mpi_under_test")
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    populate_valid_solver_config(cfg)
    cfg.fluent_mpi = "openmpi"
    payload = solver.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-11-02T00:00:00Z",
    )
    assert payload["fluent_mpi"] == "openmpi"
    monkeypatch.setenv("RO_FLUENT_MPI", "")
    payload = solver.build_run_manifest_payload(
        cfg,
        {"mesh_sha256": "a" * 64},
        created_utc="2026-11-02T00:00:00Z",
    )
    assert payload["fluent_mpi"] == ""


def test_every_launch_fluent_call_passes_the_mpi_argument():
    offenders = []
    for root in _LAUNCH_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            cursor = 0
            while True:
                found = text.find("launch_fluent(", cursor)
                if found < 0:
                    break
                span = _call_span(text, found)
                covered = (
                    "fluent_mpi_argument" in span
                    or "additional_arguments" in span
                    or "**" in span
                )
                if not covered:
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())
                cursor = found + 1
            if "launch_fluent(" in text and "fluent_mpi_argument" not in text:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []
