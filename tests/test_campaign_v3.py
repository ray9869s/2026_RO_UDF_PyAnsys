"""Campaign v3 queue, mesh table, and v2/v3 comparison."""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    load_solver_code,
    populate_valid_meshing_config,
)
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.manifest import ManifestError
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.mesh_spec import (
    changes_for_settings,
    default_spec_path,
    family_settings,
    load_mesh_spec,
    provenance_fields,
    require_changes_match,
    spec_file_sha256,
)

CAMPAIGN = load_module(
    "campaign_v3_under_test",
    SCRIPTS_DIR / "campaign_v3.py",
)
MESH_STUDY = load_module(
    "mesh_study_case_campaign_v3",
    SCRIPTS_DIR / "mfbo" / "mesh_study_case.py",
)
JOB_QUEUE = load_module(
    "job_queue_campaign_v3",
    SCRIPTS_DIR / "mfbo" / "job_queue.py",
)

MESH_ID = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
_HEAD = "a" * 40
_SPEC_HASH = "b" * 64


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def _changes(argv):
    return {
        "m_max": float(_flag(argv, "--m-max")),
        "m_min": float(_flag(argv, "--m-min")),
        "m_cpg": int(_flag(argv, "--m-cpg")),
        "bl_layers": int(_flag(argv, "--bl-layers")),
        "membrane_bl_y1_um": float(_flag(argv, "--membrane-bl-y1-um")),
        "bulk_max_size_mm": float(_flag(argv, "--bulk-max-size")),
        "peel_layers": int(_flag(argv, "--peel-layers")),
    }


def _spec():
    return load_mesh_spec(default_spec_path())


def _write_leaf(root, geo_id, mesh_id, run_id, *, cp, q999, dp, exposed, installed, gate, spec_hash="", git_head=""):
    family = family_for_geo_id(geo_id)
    leaf = root / "runs" / family / geo_id / mesh_id / run_id
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    with (reports / "summary_metrics_wide.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cpc_window_avg_flux",
                "cp_q999_window_flux",
                "pressure_drop_spacer_per_m",
                "lmh_window_exposed",
                "lmh_window_module",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "cpc_window_avg_flux": cp,
                "cp_q999_window_flux": q999,
                "pressure_drop_spacer_per_m": dp,
                "lmh_window_exposed": exposed,
                "lmh_window_module": installed,
            }
        )
    payload = {"convergence_quality": gate}
    if spec_hash:
        payload["mesh_spec_sha256"] = spec_hash
    if git_head:
        payload["git_head"] = git_head
    (leaf / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return leaf


def test_default_spec_is_the_passed_bulk_mesh():
    spec = _spec()
    for family in ("diamond", "empty", "ml", "pillar", "sin"):
        settings = family_settings(spec, family)
        assert settings["m_max"] == pytest.approx(0.035)
        assert settings["m_min"] == pytest.approx(0.006)
        assert settings["cpg"] == 5
        assert settings["membrane_bl_y1_um"] == pytest.approx(2.0)
        assert settings["membrane_bl_offset_method"] == "last-ratio"
        assert settings["bulk_max_size_mm"] == pytest.approx(0.034)
        assert settings["peel"] == 2
    digest = spec_file_sha256(default_spec_path())
    assert len(digest) == 64
    assert spec_file_sha256(default_spec_path()) == digest
    templates = CAMPAIGN.production_templates()
    overrides = MESH_STUDY.apply_study_overrides(
        templates["D2450_a45"],
        changes_for_settings(family_settings(spec, "diamond")),
    )
    assert overrides["mesh_id"] == MESH_ID
    assert overrides["membrane_bl_y1"]["layers"] == 5
    assert overrides["membrane_bl_y1"]["offset_method"] == "last-ratio"
    mesh_ids = CAMPAIGN.mesh_ids_for_spec(spec, templates)
    assert {mesh_ids[geo_id] for geo_id in CAMPAIGN_GEO_ID_ORDER} == {MESH_ID}


def test_family_override_changes_only_that_family(tmp_path):
    raw = json.loads(default_spec_path().read_text(encoding="utf-8"))
    raw["families"]["pillar"] = {"m_max": 0.045}
    path = tmp_path / "mesh_spec.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    spec = load_mesh_spec(path)
    assert family_settings(spec, "pillar")["m_max"] == pytest.approx(0.045)
    assert family_settings(spec, "pillar")["bulk_max_size_mm"] == pytest.approx(0.034)
    assert family_settings(spec, "diamond")["m_max"] == pytest.approx(0.035)
    jobs = CAMPAIGN.mesh_jobs(spec, path)
    by_geo = {_flag(job["argv"], "--geo-id"): job["argv"] for job in jobs}
    assert _flag(by_geo["P_p80_h15"], "--m-max") == "0.045"
    assert _flag(by_geo["D2450_a45"], "--m-max") == "0.035"
    assert _flag(by_geo["M_c160"], "--m-max") == "0.035"
    assert _flag(by_geo["S_a144_l3465"], "--m-max") == "0.035"


def test_unknown_family_field_is_refused(tmp_path):
    raw = json.loads(default_spec_path().read_text(encoding="utf-8"))
    raw["families"]["ml"] = {"bl_layers": 10}
    path = tmp_path / "mesh_spec.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        load_mesh_spec(path)


def test_mesh_only_queue_lists_every_geometry():
    spec = _spec()
    jobs = CAMPAIGN.mesh_jobs(spec, default_spec_path())
    assert [job["id"] for job in jobs] == [f"mesh-{geo_id}" for geo_id in CAMPAIGN_GEO_ID_ORDER]
    assert len(jobs) == 31
    for job in jobs:
        argv = job["argv"]
        assert argv[1] == "scripts/mfbo/mesh_study_case.py"
        assert _flag(argv, "--data-root") == "C:/ro_data_mfbo/campaign_v3"
        assert _flag(argv, "--geometry-root") == "C:/ro_data_mfbo/geometries_pmdb"
        assert _flag(argv, "--geometry-suffix") == ".pmdb"
        assert _flag(argv, "--mesh-spec") == "configs/mesh_spec_v3.json"
        assert argv[-1] == "--mesh-only"
        assert "--reuse-mesh" not in argv
        assert "--checkpoint-every" not in argv
        assert "--resume" not in argv
        geo_id = _flag(argv, "--geo-id")
        require_changes_match(
            _changes(argv),
            family_settings(spec, family_for_geo_id(geo_id)),
        )
    resumed = JOB_QUEUE.argv_with_checkpoint_resume(jobs[0]["argv"])
    assert resumed == jobs[0]["argv"]


def test_solve_queue_is_u0p2_first_and_reuses_the_mesh():
    spec = _spec()
    jobs = CAMPAIGN.solve_jobs(spec, default_spec_path())
    assert len(jobs) == 93
    ids = [job["id"] for job in jobs]
    assert ids[:31] == [f"u0p2_p6M-{geo_id}" for geo_id in CAMPAIGN_GEO_ID_ORDER]
    assert ids[31:62] == [f"u0p1_p6M-{geo_id}" for geo_id in CAMPAIGN_GEO_ID_ORDER]
    assert ids[62:] == [f"u0p3_p6M-{geo_id}" for geo_id in CAMPAIGN_GEO_ID_ORDER]
    for job in jobs:
        argv = job["argv"]
        assert "--reuse-mesh" in argv
        assert _flag(argv, "--checkpoint-every") == "200"
        assert "--keep-checkpoints" not in argv
        assert "--mesh-only" not in argv
        assert "--resume" not in argv
        assert "scripts/mfbo/mesh_study_case.py" in argv
    resumed = JOB_QUEUE.argv_with_checkpoint_resume(jobs[0]["argv"])
    assert resumed[-1] == "--resume"
    u0p2 = CAMPAIGN.solve_jobs(spec, default_spec_path(), run_id_filter=["u0p2_p6M"])
    assert len(u0p2) == 31
    assert {_flag(job["argv"], "--case") for job in u0p2} == {"u0p2_p6M"}
    with pytest.raises(ValueError, match="run id"):
        CAMPAIGN.solve_jobs(spec, default_spec_path(), run_id_filter=["u0p2_p4M"])


def test_mesh_table_lists_failures_and_estimates_time(tmp_path):
    mesh_ids = {geo_id: MESH_ID for geo_id in CAMPAIGN_GEO_ID_ORDER}
    passed = CAMPAIGN.mesh_manifest_path(tmp_path, "D2450_a45", MESH_ID)
    passed.parent.mkdir(parents=True)
    passed.write_text(
        json.dumps(
            {
                "cell_count": 4_340_000,
                "ortho_min": 0.12,
                "AR_max": 40.0,
                "membrane_bl_y1": {"layers": 5},
            }
        ),
        encoding="utf-8",
    )
    failed = CAMPAIGN.mesh_manifest_path(tmp_path, "D0817_a30", MESH_ID)
    failed.parent.mkdir(parents=True)
    failed.write_text(
        json.dumps(
            {
                "cell_count": 1_000_000,
                "ortho_min": 0.2,
                "AR_max": 178.6,
                "membrane_bl_y1": {"layers": 5},
            }
        ),
        encoding="utf-8",
    )
    rows = CAMPAIGN.mesh_table_rows(tmp_path, mesh_ids)
    assert len(rows) == 31
    by_geo = {row["geo_id"]: row for row in rows}
    assert by_geo["D2450_a45"]["gate"] == "PASS"
    assert by_geo["D2450_a45"]["membrane_layers"] == 5
    assert by_geo["D2450_a45"]["solve_h"] == pytest.approx(6.5)
    assert by_geo["D0817_a30"]["gate"] == "FAIL"
    assert by_geo["REF_empty"]["gate"] == "MISSING"
    text = CAMPAIGN.format_mesh_table(rows)
    assert "D0817_a30" in text
    assert "quality gate FAIL (1): D0817_a30" in text
    assert "REF_empty" in text
    assert "6.50" in text
    assert CAMPAIGN.estimate_solve_hours(8_680_000) == pytest.approx(13.0)


def test_mesh_table_lists_a_gate_failure_that_has_no_manifest(tmp_path):
    mesh_ids = {geo_id: MESH_ID for geo_id in CAMPAIGN_GEO_ID_ORDER}
    log = CAMPAIGN.mesh_log_path(tmp_path, "P_p100_h00", MESH_ID)
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            [
                "Minimum Orthogonal Quality = 0.04",
                "Maximum Aspect Ratio = 178.6",
                "Number of Cells = 5300000",
                "Membrane BL number of layers [-]: 5",
            ]
        ),
        encoding="utf-8",
    )
    rows = CAMPAIGN.mesh_table_rows(tmp_path, mesh_ids)
    failed = next(row for row in rows if row["geo_id"] == "P_p100_h00")
    assert failed["gate"] == "FAIL"
    assert failed["cell_count"] == 5_300_000
    assert failed["membrane_layers"] == 5
    assert failed["solve_h"] == pytest.approx(5_300_000 / 4_340_000 * 6.5)
    text = CAMPAIGN.format_mesh_table(rows)
    assert "quality gate FAIL (1): P_p100_h00" in text
    assert sum(row["gate"] == "MISSING" for row in rows) == 30


def test_gate_limits_match_run_config():
    cfg = load_run_config()
    assert CAMPAIGN.ORTHO_MIN_LIMIT == cfg.min_orthogonal_quality_threshold
    assert CAMPAIGN.AR_MAX_LIMIT == cfg.max_aspect_ratio_threshold
    assert CAMPAIGN.SKEW_MAX_LIMIT == cfg.max_skewness_threshold
    assert CAMPAIGN.SKEWED_FRACTION_LIMIT == cfg.skewed_face_fraction_threshold


def test_comparison_step_uses_cp_itself(tmp_path):
    v2 = tmp_path / "v2"
    v3 = tmp_path / "v3"
    mesh_ids = {geo_id: MESH_ID for geo_id in CAMPAIGN_GEO_ID_ORDER}
    production = CAMPAIGN.production_mesh_id("REF_empty")
    _write_leaf(
        v2,
        "REF_empty",
        production,
        "u0p2_p6M",
        cp=1.10,
        q999=1.20,
        dp=3707,
        exposed=23.32,
        installed=23.32,
        gate="PASS",
    )
    _write_leaf(
        v3,
        "REF_empty",
        MESH_ID,
        "u0p2_p6M",
        cp=1.21,
        q999=1.40,
        dp=3800,
        exposed=23.80,
        installed=23.10,
        gate="FAIL",
        spec_hash=_SPEC_HASH,
        git_head=_HEAD,
    )
    rows = CAMPAIGN.comparison_rows(v2, v3, mesh_ids)
    assert len(rows) == 93
    row = rows[0]
    assert row["geo_id"] == "REF_empty"
    assert row["run_id"] == "u0p2_p6M"
    assert row["v2_cp"] == pytest.approx(1.10)
    assert row["v3_cp"] == pytest.approx(1.21)
    assert row["v2_cp_excess"] == pytest.approx(0.10)
    assert row["v3_cp_excess"] == pytest.approx(0.21)
    assert row["cp_rel_change"] == pytest.approx(0.1)
    assert row["v2_q999"] == pytest.approx(1.20)
    assert row["v3_q999"] == pytest.approx(1.40)
    assert row["v2_gate"] == "PASS"
    assert row["v3_gate"] == "FAIL"
    assert row["v3_lmh_exposed"] == pytest.approx(23.80)
    assert row["v3_lmh_installed"] == pytest.approx(23.10)
    missing = next(item for item in rows if item["geo_id"] == "D2450_a45")
    assert missing["v3_cp"] is None
    assert missing["v3_gate"] == "MISSING"


def test_seminar_csv_schema(tmp_path):
    mesh_ids = {geo_id: MESH_ID for geo_id in CAMPAIGN_GEO_ID_ORDER}
    _write_leaf(
        tmp_path,
        "D2450_a45",
        MESH_ID,
        "u0p2_p6M",
        cp=1.0854,
        q999=1.848,
        dp=35423,
        exposed=24.10,
        installed=23.831,
        gate="PASS",
        spec_hash=_SPEC_HASH,
        git_head=_HEAD,
    )
    mesh_manifest = CAMPAIGN.mesh_manifest_path(tmp_path, "D2450_a45", MESH_ID)
    mesh_manifest.parent.mkdir(parents=True)
    mesh_manifest.write_text(
        json.dumps({"mesh_spec_sha256": _SPEC_HASH, "git_head": _HEAD}),
        encoding="utf-8",
    )
    destination = tmp_path / "seminar_cases.csv"
    CAMPAIGN.write_csv(
        destination,
        CAMPAIGN.SEMINAR_CASES_COLUMNS,
        CAMPAIGN.seminar_rows(tmp_path, mesh_ids),
    )
    with destination.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(CAMPAIGN.SEMINAR_CASES_COLUMNS)
        rows = list(reader)
    assert len(rows) == 93
    filled = next(row for row in rows if row["geo_id"] == "D2450_a45" and row["run_id"] == "u0p2_p6M")
    assert float(filled["cpc_window_avg_flux"]) == pytest.approx(1.0854)
    assert float(filled["cp_excess"]) == pytest.approx(0.0854)
    assert "cpc_window_avg_flux_minus_1" not in reader.fieldnames
    assert filled["mesh_spec_sha256"] == _SPEC_HASH
    assert filled["git_head"] == _HEAD
    assert filled["convergence_quality"] == "PASS"


def test_mesh_spec_flag_refuses_a_different_knob():
    with pytest.raises(ValueError, match="m_max"):
        MESH_STUDY.main(
            [
                "--data-root",
                "C:/ro_data_mfbo/campaign_v3",
                "--geo-id",
                "D2450_a45",
                "--m-max",
                "0.045",
                "--m-min",
                "0.006",
                "--m-cpg",
                "5",
                "--bl-layers",
                "4",
                "--membrane-bl-y1-um",
                "2",
                "--bulk-max-size",
                "0.034",
                "--peel-layers",
                "2",
                "--mesh-spec",
                "configs/mesh_spec_v3.json",
                "--mesh-only",
            ]
        )


def test_provenance_is_stamped_and_must_match():
    stamped = provenance_fields(default_spec_path(), git_head=_HEAD)
    assert stamped["mesh_spec_sha256"] == spec_file_sha256(default_spec_path())
    assert stamped["git_head"] == _HEAD

    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.m_min = 0.006
    cfg.mesh_id = "max085_min006_cpg5_bl4_peel2"
    cfg.mesh_spec_sha256 = _SPEC_HASH
    cfg.git_head = _HEAD
    payload = build_mesh_manifest_payload(
        cfg,
        {
            "min_orthogonal_quality": 0.1,
            "max_aspect_ratio": 40.0,
            "max_skewness": 0.5,
            "skewed_face_fraction": 0.0,
            "cell_count": 100,
        },
        "c" * 64,
        created_utc="2026-10-30T00:00:00Z",
    )
    assert payload["mesh_spec_sha256"] == _SPEC_HASH
    assert payload["git_head"] == _HEAD

    from ro.manifest import _optional_provenance

    _optional_provenance(
        {"mesh_spec_sha256": _SPEC_HASH, "git_head": _HEAD},
        "Mesh",
    )
    with pytest.raises(ManifestError, match="git_head"):
        _optional_provenance({"git_head": "abc"}, "Run")

    solver = load_solver_code()
    base = {}
    with pytest.raises(ValueError, match="mesh_spec_sha256"):
        solver._stamp_campaign_provenance(
            base,
            SimpleNamespace(mesh_spec_sha256=_SPEC_HASH, git_head=_HEAD),
            {"mesh_spec_sha256": "d" * 64},
        )
    solver._stamp_campaign_provenance(
        base,
        SimpleNamespace(mesh_spec_sha256=_SPEC_HASH, git_head=_HEAD),
        {"mesh_spec_sha256": _SPEC_HASH},
    )
    assert base["mesh_spec_sha256"] == _SPEC_HASH
    assert base["git_head"] == _HEAD


def test_keep_checkpoints_is_only_on_solve_jobs(tmp_path):
    spec = _spec()
    jobs = CAMPAIGN.solve_jobs(
        spec, default_spec_path(), keep_checkpoints=True
    )
    assert jobs
    for job in jobs:
        argv = job["argv"]
        assert argv[-1] == "--keep-checkpoints"
        assert _flag(argv, "--checkpoint-every") == "200"
        assert "--mesh-only" not in argv
    with pytest.raises(ValueError, match="keep-checkpoints"):
        CAMPAIGN.main(
            [
                "--mesh-only",
                "--keep-checkpoints",
                "--emit-queue",
                str(tmp_path / "queue.json"),
            ]
        )
