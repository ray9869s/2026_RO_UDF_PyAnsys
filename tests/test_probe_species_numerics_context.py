"""Tests for the species-numerics live probe script (no Fluent launch)."""

from __future__ import annotations

import json

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module


def _load_probe():
    return load_module(
        "probe_species_numerics_context_under_test",
        SCRIPTS_DIR / "probe_species_numerics_context.py",
    )


def test_processor_count_defaults_to_eight_not_partition_count():
    probe = _load_probe()
    args = probe.parse_args([])
    assert args.processor_count == 8
    assert probe.DEFAULT_PROCESSOR_COUNT == 8


def test_path_defaults_resolve_after_parse(monkeypatch, tmp_path):
    probe = _load_probe()
    args = probe.parse_args([])
    assert args.template_case is None
    assert args.mesh_file is None

    monkeypatch.delenv("PYFLUENT_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    probe.resolve_path_defaults(args)

    assert args.template_case == (
        REPO_ROOT
        / "templates"
        / "template_RO_setup.cas.h5"
    )
    assert args.mesh_file == (
        tmp_path
        / "meshes"
        / "diamond"
        / "D2450_a45"
        / "max100_min006_cpg3_bl3_peel2"
        / "D2450_a45_max100_min006_cpg3_bl3_peel2.msh.h5"
    )


def test_matrix_row_preserves_error_to_distinguish_refusal_from_missing_key():
    probe = _load_probe()
    refused = probe.matrix_row_from_outcome(
        mesh_state="AFTER_REPLACE_MESH",
        expert_requested="OFF",
        expert_readback=False,
        requested=0.5,
        outcome={
            "status": "WARN_APPLY_URF_FAILED",
            "before": 0.75,
            "after": 0.75,
        },
    )
    missing = probe.matrix_row_from_outcome(
        mesh_state="AFTER_REPLACE_MESH",
        expert_requested="ON",
        expert_readback=True,
        requested=0.5,
        outcome={
            "status": "WARN_APPLY_URF_FAILED",
            "before": None,
            "after": None,
            "error": "value not present in state keys ['other']",
        },
    )
    assert refused["error"] == ""
    assert "not present" in missing["error"]
    assert refused["status"] == missing["status"]

    table = "\n".join(probe.format_matrix_table([refused, missing]))
    assert "error" in table.splitlines()[1]
    assert "not present in state keys" in table
    assert "WARN_APPLY_URF_FAILED" in table


def test_dry_run_prints_plan_without_launching_fluent(capsys, monkeypatch, tmp_path):
    probe = _load_probe()
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))

    def _fail_live(*_args, **_kwargs):
        raise AssertionError("dry-run must not call run_live_probe")

    monkeypatch.setattr(probe, "run_live_probe", _fail_live)
    exit_code = probe.main(["--dry-run", "--processor-count", "8"])
    assert exit_code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["processor_count"] == 8
    assert plan["action"].startswith("launch Fluent")
    assert [phase["name"] for phase in plan["phases"]] == [
        "TEMPLATE_BEFORE_REPLACE",
        "AFTER_REPLACE_MESH",
    ]
    assert "error" in plan["summary"]
