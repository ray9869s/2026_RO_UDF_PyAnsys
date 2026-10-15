"""Short-domain LF fidelity. Does not launch Fluent or Discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.convergence_quality import evaluate_convergence_quality
from ro.evaluation_window_table import select_evaluation_window
from ro.geometry_registry import (
    DEFAULT_PILLAR_N_ACTIVE,
    format_mfbo_pillar_geo_id,
    mfbo_pillar_n_active,
    pillar_registry_entry,
    require_pillar_cad_geo_id,
)
from ro.mfbo_adapter import PillarDesign, evaluate, mesh_id_for_settings
from ro.mfbo_drivers import make_drivers
from ro.mfbo_fidelity import (
    extraction_cli_args,
    fidelity_settings_used,
    parse_fidelity_level,
    solver_cli_args,
)
from ro.mfbo_fidelity_screen import build_named_level_queue, load_fidelity_table
from ro.pillar_cad import _layout_from_config, _meta_inputs, _nodes
from ro.solver_common import sha256_file

TABLE_PATH = REPO_ROOT / "configs" / "mfbo_fidelity_table.lf_short.example.json"
MESH_ONLY = REPO_ROOT / "configs" / "mfbo_fidelity_table.example.json"
RUN_ID = "u0p2_p6M"


def _table():
    return load_fidelity_table(TABLE_PATH)


def test_default_domain_layout_matches_explicit_seven_and_omits_the_meta_key():
    filament_d_m = 0.0004
    implicit = _layout_from_config(filament_d_m)
    explicit = _layout_from_config(filament_d_m, n_active=DEFAULT_PILLAR_N_ACTIVE)
    assert implicit == explicit
    assert implicit["n_active"] == 7
    assert implicit["n_buffer_in"] == 1
    assert implicit["n_buffer_out"] == 2
    assert implicit["x_outlet"] == (1 + 7 + 2) * implicit["a_m"]
    assert _nodes(implicit) == _nodes(explicit)
    assert len(_nodes(implicit)) == 23
    inputs = _meta_inputs(
        d_p_mm=0.8,
        d_h_mm=0.15,
        d_f_mm=0.4,
        geo_id="MFP_d0800_h0150_f0400",
        n_active=7,
    )
    assert "n_active" not in inputs
    assert json.dumps(implicit, sort_keys=True) == json.dumps(explicit, sort_keys=True)


def test_short_domain_layout_suffix_and_campaign_refusal():
    short = _layout_from_config(0.0004, n_active=3)
    assert short["n_active"] == 3
    assert short["n_buffer_in"] == 1
    assert short["n_buffer_out"] == 2
    assert short["a_m"] == _layout_from_config(0.0004)["a_m"]
    assert short["x_outlet"] == (1 + 3 + 2) * short["a_m"]
    assert len(_nodes(short)) == 11
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=3)
    assert geo_id == "MFP_d0800_h0150_f0400_n3"
    assert mfbo_pillar_n_active(geo_id) == 3
    assert format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=7) == (
        "MFP_d0800_h0150_f0400"
    )
    inputs = _meta_inputs(
        d_p_mm=0.8,
        d_h_mm=0.15,
        d_f_mm=0.4,
        geo_id=geo_id,
        n_active=3,
    )
    assert inputs["n_active"] == 3
    with pytest.raises(ValueError, match="_n7"):
        mfbo_pillar_n_active("MFP_d0800_h0150_f0400_n7")
    with pytest.raises(ValueError, match="campaign pillar"):
        require_pillar_cad_geo_id("P_p100_h30", 1.0, 0.3, 0.4, n_active=3)


def test_lf_short_table_schema_and_mesh_only_table_still_parses():
    table = _table()
    hf = parse_fidelity_level(table["HF"])
    low_a = parse_fidelity_level(table["LF_a"])
    low_b = parse_fidelity_level(table["LF_b"])
    assert hf["n_active"] == 7
    assert hf["n_lead_excluded"] == 4
    assert hf["extract_profile"] == "mfbo"
    assert hf["solver_overrides"] == {}
    assert hf["convergence_gate"] == {}
    assert mesh_id_for_settings(table["HF"]) == "max085_min006_cpg5_bl12s4_peel2"
    assert mesh_id_for_settings(table["LF_a"]) == "max085_min006_cpg5_bl4_peel2"
    assert mesh_id_for_settings(table["LF_b"]) == "max120_min006_cpg5_bl4_peel2"
    assert low_a["n_active"] == 3
    assert low_a["n_lead_excluded"] == 1
    assert low_b["m_max"] == 0.12
    low_c = parse_fidelity_level(table["LF_c"])
    assert mesh_id_for_settings(table["LF_c"]) == mesh_id_for_settings(table["LF_b"])
    assert low_c["n_active"] == low_b["n_active"] == 3
    assert low_c["n_lead_excluded"] == 1
    assert low_c["run_id_suffix"] == "lfc"
    assert low_c["solver_overrides"]["max_iterations"] == 600
    assert low_c["solver_overrides"]["lf_monitor_stop"] == {
        "tol_lmh": 1.0e-3,
        "tol_dp": 5.0e-3,
        "n": 100,
    }
    assert low_c["convergence_gate"] == {"mode": "lf_monitor"}
    used = fidelity_settings_used(hf)
    assert used["window_source"] == "fidelity_table"
    assert used["solver"]["max_iterations"]["source"] == "production"
    assert "turbulence_residual_target" not in used["solver"]
    assert solver_cli_args(hf) == []
    assert extraction_cli_args(low_a) == [
        "--profile",
        "mfbo",
        "--fidelity-n-lead-excluded",
        "1",
    ]
    partial = dict(table["LF_a"])
    del partial["extract_profile"]
    with pytest.raises(KeyError, match="extract_profile"):
        parse_fidelity_level(partial)
    unknown = dict(table["LF_a"])
    unknown["mesh_id"] = "nope"
    with pytest.raises(ValueError, match="unknown keys"):
        parse_fidelity_level(unknown)
    mesh_only = load_fidelity_table(MESH_ONLY)
    parsed = parse_fidelity_level(mesh_only["LF"])
    assert parsed["extended"] is False
    assert fidelity_settings_used(parsed) is None
    assert mesh_only["HF"] == "TBD"


def test_fidelity_table_window_does_not_read_the_evaluation_table():
    selection = select_evaluation_window(
        geo_id="MFP_d0800_h0150_f0400_n3",
        mesh_id="max085_min006_cpg5_bl4_peel2",
        n_active=3,
        cell_length_x_m=0.003465,
        legacy_3_cell_window=False,
        fidelity_n_lead_excluded=1,
    )
    assert selection.window_source == "fidelity_table"
    assert selection.window_table_version == "fidelity_table"
    assert selection.n_lead_excluded == 1
    assert selection.excluded_length_m == 0.003465
    assert selection.window_length_m == 2 * 0.003465
    assert selection.window_table_mesh_id is None
    assert selection.run_mesh_id == "max085_min006_cpg5_bl4_peel2"
    with pytest.raises(ValueError, match="cannot both"):
        select_evaluation_window(
            geo_id="MFP_d0800_h0150_f0400_n3",
            mesh_id="max085_min006_cpg5_bl4_peel2",
            n_active=3,
            cell_length_x_m=0.003465,
            legacy_3_cell_window=True,
            fidelity_n_lead_excluded=1,
        )


def test_gate_override_is_used_and_the_default_still_reads_the_constants(monkeypatch):
    metrics = {
        "lmh_relative_difference": 5.0e-4,
        "mass_balance_relative_error": 0.0,
        "continuity_final": 0.0,
    }
    assert evaluate_convergence_quality(metrics)["convergence_quality"] == "PASS"
    tight = evaluate_convergence_quality(metrics, lmh_rel_abs_max=1.0e-4)
    assert tight["checks"]["lmh_relative_difference"]["passed"] is False
    monkeypatch.setattr("ro.convergence_quality.LMH_REL_ABS_MAX", 1.0e-4)
    patched = evaluate_convergence_quality(metrics)
    assert patched["checks"]["lmh_relative_difference"]["threshold_abs"] == 1.0e-4


def test_drivers_pass_short_domain_flags_and_only_specified_overrides(tmp_path):
    from types import SimpleNamespace

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        stdout = kwargs.get("stdout")
        if stdout is not None:
            stdout.write(b"ok\n")
        return SimpleNamespace(returncode=0)

    level = parse_fidelity_level(_table()["LF_a"])
    level["solver_overrides"] = {"max_iterations": 40}
    level["convergence_gate"] = {"lmh_rel_abs_max": 2.0e-3}
    drivers = make_drivers(tmp_path, runner=runner)
    geo_id = "MFP_d0800_h0150_f0400_n3"
    run_dir = tmp_path / "run"
    drivers.generate(
        design=PillarDesign(0.8, 0.15, 0.4),
        geo_id=geo_id,
        out_dir=tmp_path / "geo",
        n_active=3,
    )
    drivers.mesh(
        design=PillarDesign(0.8, 0.15, 0.4),
        geo_id=geo_id,
        mesh_id="max085_min006_cpg5_bl4_peel2",
        mesh_settings=level,
        mesh_dir=tmp_path / "mesh",
        n_lead_excluded=1,
    )
    drivers.solve(
        design=PillarDesign(0.8, 0.15, 0.4),
        geo_id=geo_id,
        mesh_id="max085_min006_cpg5_bl4_peel2",
        run_id=RUN_ID,
        run_dir=run_dir,
        level=level,
    )
    drivers.extract(
        design=PillarDesign(0.8, 0.15, 0.4),
        geo_id=geo_id,
        mesh_id="max085_min006_cpg5_bl4_peel2",
        run_id=RUN_ID,
        run_dir=run_dir,
        level=level,
    )
    generate_argv, mesh_argv, solve_argv, extract_argv = calls
    assert generate_argv[generate_argv.index("--n-active") + 1] == "3"
    assert mesh_argv[mesh_argv.index("--m-max") + 1] == "0.085"
    assert mesh_argv[mesh_argv.index("--bl-layers") + 1] == "4"
    assert mesh_argv[mesh_argv.index("--n-lead-excluded") + 1] == "1"
    assert solve_argv[solve_argv.index("--max-iterations") + 1] == "40"
    assert solve_argv[solve_argv.index("--profile") + 1] == "mfbo"
    assert solve_argv[solve_argv.index("--fidelity-n-lead-excluded") + 1] == "1"
    assert solve_argv[solve_argv.index("--lmh-rel-abs-max") + 1] == "0.002"
    assert "--residual-target" not in solve_argv
    assert "--qoi-stop-criterion" not in solve_argv
    assert extract_argv[-1] == str(run_dir)
    assert extract_argv[extract_argv.index("--profile") + 1] == "mfbo"
    assert "--max-iterations" not in extract_argv


def test_short_mesh_override_sets_active_cells_and_lead():
    driver = load_module(
        "mesh_mfbo_case_lf_short",
        SCRIPTS_DIR / "mfbo" / "mesh_mfbo_case.py",
    )
    geo_id = format_mfbo_pillar_geo_id(0.8, 0.15, 0.4, n_active=3)
    registry = pillar_registry_entry(geo_id, 0.0008, 0.00015, 0.0004, n_active_cells=3)
    template = {
        "geo_id": "P_p100_h30",
        "geo_name": "P_p100_h30",
        "spacing_code": "P_p100_h30",
        "filament_d_m": 4.0e-4,
        "bridge_radius_m": 1.1e-4,
        "wall_spacer_labels": ["wall_spacer"],
        "n_active_cells": 7,
        "n_lead_excluded": 3,
        "m_max": 0.085,
    }
    overrides = driver.apply_mfbo_overrides(
        template,
        geo_id,
        registry,
        n_lead_excluded=1,
    )
    assert overrides["n_active_cells"] == 3
    assert overrides["n_lead_excluded"] == 1
    rows = driver.assert_override_delta(template, overrides)
    differed = {row["key"] for row in rows if row["status"] == "differs"}
    assert {"n_active_cells", "n_lead_excluded"} <= differed
    assert differed <= driver.ALLOWED_OVERRIDE_DELTA
    assert driver.require_mfbo_geo_id(geo_id) == geo_id
    with pytest.raises(ValueError, match="_n7"):
        driver.require_mfbo_geo_id("MFP_d0800_h0150_f0400_n7")


def test_check_queue_has_six_jobs_and_emit_does_not_evaluate(tmp_path, monkeypatch):
    script = load_module(
        "run_lf_short_check_under_test",
        SCRIPTS_DIR / "mfbo" / "run_lf_short_check.py",
    )
    table = _table()
    jobs = build_named_level_queue(
        script.DESIGNS,
        script.CHECK_LEVELS,
        table,
        run_id=RUN_ID,
        data_root=tmp_path,
        fidelity_table=TABLE_PATH,
    )
    assert [job["id"] for job in jobs] == [
        "HF-MFP_d0800_h0150_f0400",
        "LF_a-MFP_d0800_h0150_f0400_n3",
        "LF_b-MFP_d0800_h0150_f0400_n3",
        "LF_c-MFP_d0800_h0150_f0400_n3",
        "HF-MFP_d1000_h0300_f0400",
        "LF_a-MFP_d1000_h0300_f0400_n3",
        "LF_b-MFP_d1000_h0300_f0400_n3",
        "LF_c-MFP_d1000_h0300_f0400_n3",
    ]
    assert all(job["argv"][1] == "scripts/mfbo/run_3d_evaluate.py" for job in jobs)
    assert jobs[0]["argv"][jobs[0]["argv"].index("--run-id") + 1] == RUN_ID

    def _refuse(*_args, **_kwargs):
        raise AssertionError("emit-queue evaluated a design")

    monkeypatch.setattr(script, "evaluate", _refuse)
    queue_path = tmp_path / "queue.json"
    code = script.main(
        [
            "--fidelity-table",
            str(TABLE_PATH),
            "--run-id",
            RUN_ID,
            "--data-root",
            str(tmp_path),
            "--emit-queue",
            str(queue_path),
        ]
    )
    assert code == 0
    written = json.loads(queue_path.read_text(encoding="utf-8"))
    assert [job["id"] for job in written] == [job["id"] for job in jobs]


def test_adapter_passes_the_level_into_every_driver(tmp_path, monkeypatch):
    written = {}

    def _capture(run_dir, updates):
        written["updates"] = updates
        return Path(run_dir) / "manifest.json"

    monkeypatch.setattr(
        "ro.mfbo_adapter.update_run_manifest_fields",
        _capture,
    )
    recorded = {}

    class _Drivers:
        def generate(self, *, design, geo_id, out_dir, n_active=None):
            recorded["n_active"] = n_active
            out_dir.mkdir(parents=True, exist_ok=True)
            pmdb = out_dir / f"{geo_id}.pmdb"
            pmdb.write_bytes(b"pmdb")
            meta = {
                "geo_id": geo_id,
                "inputs": {
                    "d_p_mm": design.d_p_mm,
                    "d_h_mm": design.d_h_mm,
                    "d_f_mm": design.d_f_mm,
                    "geo_id": geo_id,
                    "n_active": n_active,
                },
                "pmdb_sha256": sha256_file(pmdb),
            }
            (out_dir / f"{geo_id}_meta.json").write_text(
                json.dumps(meta),
                encoding="utf-8",
            )

        def mesh(self, *, design, geo_id, mesh_id, mesh_settings, mesh_dir, n_lead_excluded=None):
            recorded["n_lead_excluded"] = n_lead_excluded
            recorded["m_max"] = mesh_settings["m_max"]
            mesh_dir.mkdir(parents=True, exist_ok=True)
            (mesh_dir / "mesh_run_record.json").write_text(
                json.dumps({"status": "SUCCESS", "wall_time_seconds": 1.0}),
                encoding="utf-8",
            )
            (mesh_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "cell_count": 10,
                        "n_active_cells": 3,
                        "cell_length_x_m": 0.003465,
                        "periodic_shift_y_m": 0.003465,
                    }
                ),
                encoding="utf-8",
            )

        def solve(self, *, design, geo_id, mesh_id, run_id, run_dir, level=None):
            recorded["solve_level"] = level
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "stop_reason": "residual_converged",
                        "convergence_quality": "PASS",
                        "solver_wall_time_s": 4.0,
                        "extraction_wall_time_s": 1.0,
                    }
                ),
                encoding="utf-8",
            )

        def extract(self, *, design, geo_id, mesh_id, run_id, run_dir, level=None):
            recorded["extract_level"] = level
            path = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "lmh_mass_balance,lmh_window_module,area_mem,"
                "pressure_drop_spacer_per_m,cpc_window_avg_flux,"
                "cp_q999_window_flux,cp_canon_window_avg\n"
                "25,18,1.0e-5,30000,1.08,1.2,1.05\n",
                encoding="utf-8",
            )

    table = {"LF_a": _table()["LF_a"]}
    result = evaluate(
        PillarDesign(d_p_mm=0.8, d_h_mm=0.15, d_f_mm=0.4),
        "LF_a",
        run_id=RUN_ID,
        data_root=tmp_path,
        fidelity_table=table,
        drivers=_Drivers(),
    )
    assert recorded["n_active"] == 3
    assert recorded["n_lead_excluded"] == 1
    assert recorded["m_max"] == 0.085
    assert recorded["solve_level"]["extract_profile"] == "mfbo"
    assert recorded["extract_level"]["n_lead_excluded"] == 1
    assert result["geo_id"] == "MFP_d0800_h0150_f0400_n3"
    assert result["fidelity_settings"]["window_source"] == "fidelity_table"
    assert result["fidelity_settings"]["n_active"] == 3
    assert (
        written["updates"]["fidelity_settings"]["solver"]["max_iterations"]["source"]
        == "production"
    )


def test_lf_c_reuses_the_lf_b_geometry_and_mesh(tmp_path, monkeypatch):
    calls = []

    def _capture(run_dir, updates):
        return Path(run_dir) / "manifest.json"

    monkeypatch.setattr("ro.mfbo_adapter.update_run_manifest_fields", _capture)

    class _Drivers:
        def generate(self, *, design, geo_id, out_dir, n_active=None):
            calls.append("generate")
            out_dir.mkdir(parents=True, exist_ok=True)
            pmdb = out_dir / f"{geo_id}.pmdb"
            pmdb.write_bytes(b"pmdb")
            meta = {
                "geo_id": geo_id,
                "inputs": {
                    "d_p_mm": design.d_p_mm,
                    "d_h_mm": design.d_h_mm,
                    "d_f_mm": design.d_f_mm,
                    "geo_id": geo_id,
                    "n_active": n_active,
                },
                "pmdb_sha256": sha256_file(pmdb),
            }
            (out_dir / f"{geo_id}_meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )

        def mesh(self, *, design, geo_id, mesh_id, mesh_settings, mesh_dir, n_lead_excluded=None):
            calls.append("mesh")
            mesh_dir.mkdir(parents=True, exist_ok=True)
            (mesh_dir / "mesh_run_record.json").write_text(
                json.dumps({"status": "SUCCESS", "wall_time_seconds": 1.0}),
                encoding="utf-8",
            )
            (mesh_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "cell_count": 10,
                        "n_active_cells": 3,
                        "cell_length_x_m": 0.001,
                        "periodic_shift_y_m": 0.001,
                    }
                ),
                encoding="utf-8",
            )

        def solve(self, *, design, geo_id, mesh_id, run_id, run_dir, level=None):
            calls.append(("solve", run_id))
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "stop_reason": "lf_monitor_stable",
                        "convergence_quality": "PASS",
                        "convergence_gate": "lf_monitor",
                        "lf_monitor_ramp_end": 150,
                        "lf_monitor_tol_lmh": 1.0e-3,
                        "lf_monitor_tol_dp": 5.0e-3,
                        "lf_monitor_n": 100,
                    }
                ),
                encoding="utf-8",
            )

        def extract(self, *, design, geo_id, mesh_id, run_id, run_dir, level=None):
            calls.append("extract")
            path = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "lmh_mass_balance,lmh_window_module,area_mem,"
                "pressure_drop_spacer_per_m,cpc_window_avg_flux,"
                "cp_q999_window_flux,cp_canon_window_avg\n"
                "25,18,1.0e-5,30000,1.08,1.2,1.05\n",
                encoding="utf-8",
            )

    design = PillarDesign(d_p_mm=0.8, d_h_mm=0.15, d_f_mm=0.4)
    table = _table()
    evaluate(
        design,
        "LF_b",
        run_id=RUN_ID,
        data_root=tmp_path,
        fidelity_table=table,
        drivers=_Drivers(),
    )
    calls.clear()
    result = evaluate(
        design,
        "LF_c",
        run_id=RUN_ID,
        data_root=tmp_path,
        fidelity_table=table,
        drivers=_Drivers(),
    )
    assert calls == [("solve", "u0p2_p6M_lfc"), "extract"]
    assert result["run_id"] == "u0p2_p6M_lfc"
    assert result["mesh_id"] == mesh_id_for_settings(table["LF_b"])
    assert result["geo_id"].endswith("_n3")
    assert result["status"] == "valid"
    assert result["convergence_gate"] == "lf_monitor"
    assert result["lf_monitor_ramp_end"] == 150
    assert result["fidelity_settings"]["convergence_gate"]["mode"]["value"] == (
        "lf_monitor"
    )
