"""Hard global AR studies keep distinct identities and all other quality gates."""

import json

import pytest

from helpers import SCRIPTS_DIR, load_module, load_run_config, populate_valid_meshing_config
from ro.aspect_ceiling import aspect_ceiling_suffix
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER
from ro.manifest import ManifestError, read_mesh_manifest, write_mesh_manifest
from ro.mesh_common import evaluate_quality_gate
from ro.mesh_manifest_payload import build_mesh_manifest_payload
from ro.mesh_spec import changes_for_settings, default_spec_path, family_settings, load_mesh_spec
from ro.paths import MESH_ID_RE, mesh_dir
from test_manifest import mesh_payload
from test_mesh_ledger import load_meshing_code


CAMPAIGN = load_module("campaign_ar_study", SCRIPTS_DIR / "campaign_v3.py")
DRIVER = CAMPAIGN.mesh_study
STANDARD_ID = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"


@pytest.mark.parametrize("limit", [0, -1, True, 250.1, float("nan"), float("inf"), "250"])
def test_invalid_ceiling_cannot_alias_another_mesh(limit):
    with pytest.raises(ValueError, match="positive integer"):
        aspect_ceiling_suffix(limit)


def test_sensitivity_mesh_ids_do_not_mix_with_standard_or_finer_meshes():
    spec = load_mesh_spec(default_spec_path())
    standard = CAMPAIGN.mesh_ids_for_spec(spec)
    sensitivity = CAMPAIGN.mesh_ids_for_spec(spec, max_aspect_ratio=250)
    spec["geometries"] = {"D1225_a30": 0.025}
    fine = CAMPAIGN.mesh_ids_for_spec(spec)
    fine_sensitivity = CAMPAIGN.mesh_ids_for_spec(spec, max_aspect_ratio=250)
    for geo_id in CAMPAIGN_GEO_ID_ORDER:
        assert standard[geo_id] == STANDARD_ID
        assert sensitivity[geo_id] == STANDARD_ID + "_ar250"
        assert MESH_ID_RE.fullmatch(sensitivity[geo_id])
    assert "_fs025_" in fine["D1225_a30"]
    assert len({standard["D1225_a30"], sensitivity["D1225_a30"],
                fine["D1225_a30"], fine_sensitivity["D1225_a30"]}) == 4


@pytest.mark.parametrize("mesh_only", [False, True])
def test_emit_ar250_queue_uses_standard_sizing_and_global_gate(tmp_path, mesh_only):
    path = tmp_path / "ar250.json"
    argv = ["--emit-queue", str(path), "--geo-id", "D1225_a30",
            "--geo-id", "S_a193_l1733", "--max-aspect-ratio", "250"]
    argv += ["--mesh-only"] if mesh_only else ["--run-id", "u0p2_p6M", "--no-cp-live"]
    assert CAMPAIGN.main(argv) == 0
    jobs = json.loads(path.read_text(encoding="utf-8"))
    assert len(jobs) == 2
    for job in jobs:
        assert job["id"].endswith("_ar250")
        args = DRIVER.build_parser().parse_args(job["argv"][2:])
        assert args.max_aspect_ratio == 250
        assert args.max_prism_aspect_ratio is None
        assert args.m_max == 0.035
        assert args.local_face_size is None
        assert args.mesh_only == mesh_only
        changes = DRIVER.collect_overrides(
            m_max=args.m_max, m_min=args.m_min, m_cpg=args.m_cpg,
            bl_layers=args.bl_layers, peel_layers=args.peel_layers,
            membrane_bl_y1_um=args.membrane_bl_y1_um,
            bulk_max_size_mm=args.bulk_max_size, max_aspect_ratio=args.max_aspect_ratio,
        )
        _, template, _ = DRIVER.load_mesh_template(args.geo_id)
        overrides = DRIVER.apply_study_overrides(template, changes)
        assert overrides["mesh_id"] == STANDARD_ID + "_ar250"
        assert overrides["max_aspect_ratio_threshold"] == 250
        assert overrides["max_prism_aspect_ratio_threshold"] is None
        assert overrides["fail_if_quality_not_parsed"] is True
        cfg = load_run_config()
        for key, value in overrides.items():
            setattr(cfg, key, value)
        cfg.validate_for_meshing()


def test_driver_rejects_combining_global_and_prism_overrides(tmp_path):
    with pytest.raises(ValueError, match="cannot combine"):
        DRIVER.main(["--data-root", str(tmp_path), "--geo-id", "D1225_a30",
                     "--max-aspect-ratio", "250", "--max-prism-aspect-ratio", "250"])


def test_config_requires_the_encoded_global_ceiling_and_parsed_quality():
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.mesh_id += "_ar250"
    with pytest.raises(ValueError, match="suffix"):
        cfg.validate_for_meshing()
    cfg.max_aspect_ratio_threshold = 250.0
    with pytest.raises(ValueError, match="fail_if_quality_not_parsed"):
        cfg.validate_for_meshing()
    cfg.fail_if_quality_not_parsed = True
    cfg.validate_for_meshing()
    cfg.max_prism_aspect_ratio_threshold = 300.0
    with pytest.raises(ValueError, match="global ceiling"):
        cfg.validate_for_meshing()


@pytest.mark.parametrize("aspect,ortho,passes", [
    (212.0, 0.08, True), (228.0, 0.08, True), (250.0, 0.08, True),
    (250.01, 0.08, False), (228.0, 0.049, False), (None, 0.08, False),
])
def test_real_worker_gate_and_ledger_keep_hard_limits(tmp_path, aspect, ortho, passes):
    # Synthetic parser input; these values are not host measurements.
    log = tmp_path / "synthetic_mesh.txt"
    text = f"Minimum Orthogonal Quality = {ortho}\n"
    if aspect is not None:
        text += f"Maximum Aspect Ratio = {aspect}\n"
    log.write_text(text, encoding="utf-8")
    worker = load_meshing_code()
    kwargs = dict(log_path=log, min_orthogonal_quality_limit=0.05,
                  max_aspect_ratio_limit=250, max_skewness_limit=None,
                  skewed_face_fraction_limit=None, fail_if_not_parsed=True)
    if passes:
        metrics = worker.apply_mesh_quality_gate(**kwargs)
        assert metrics["max_aspect_ratio"] == aspect
    else:
        with pytest.raises(RuntimeError):
            worker.apply_mesh_quality_gate(**kwargs)
    assert evaluate_quality_gate({
        "min_orthogonal_quality_threshold": 0.05,
        "max_aspect_ratio_threshold": 250, "fail_if_quality_not_parsed": True,
    }, {"min_orthogonal_quality": ortho, "max_aspect_ratio": aspect}) is passes


def test_manifest_records_hard_gate_ceiling_and_measured_maximum(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    cfg = load_run_config()
    populate_valid_meshing_config(cfg)
    cfg.mesh_id += "_ar250"
    cfg.max_aspect_ratio_threshold = 250.0
    metrics = {"min_orthogonal_quality": 0.08, "max_aspect_ratio": 228.0,
               "max_skewness": 0.6, "skewed_face_fraction": 1e-6, "cell_count": 100}
    payload = build_mesh_manifest_payload(cfg, metrics, "c" * 64)
    assert payload["aspect_ratio_gate"] == "hard"
    assert payload["max_aspect_ratio_threshold"] == 250.0
    assert payload["AR_max"] == 228.0
    directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    directory.mkdir(parents=True)
    write_mesh_manifest(directory, payload)
    assert read_mesh_manifest(directory)["AR_max"] == 228.0


@pytest.mark.parametrize("change", [
    {"max_aspect_ratio_threshold": 200}, {"max_aspect_ratio_threshold": None},
    {"aspect_ratio_gate": "warn"}, {"AR_max": 251}, {"AR_max": None},
    {"max_prism_aspect_ratio_threshold": 300},
])
def test_manifest_rejects_mislabeled_or_failed_sensitivity_mesh(monkeypatch, tmp_path, change):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    payload = mesh_payload()
    payload.update(mesh_id=payload["mesh_id"] + "_ar250", aspect_ratio_gate="hard",
                   max_aspect_ratio_threshold=250, AR_max=228)
    payload.update(change)
    directory = mesh_dir(payload["family"], payload["geo_id"], payload["mesh_id"])
    directory.mkdir(parents=True)
    with pytest.raises(ManifestError):
        write_mesh_manifest(directory, payload)


def test_reuse_checks_the_recorded_ceiling_and_measured_ar(tmp_path):
    geometry = tmp_path / "geometry.pmdb"
    geometry.write_bytes(b"synthetic geometry")
    digest = DRIVER.sha256_file(geometry)
    spec = load_mesh_spec(default_spec_path())
    _, template, _ = DRIVER.load_mesh_template("D1225_a30")
    changes = changes_for_settings(family_settings(spec, "diamond"))
    changes["max_aspect_ratio_threshold"] = 250.0
    overrides = DRIVER.apply_study_overrides(template, changes)
    leaf = tmp_path / overrides["mesh_id"]
    leaf.mkdir()
    payload = {"max_size_mm": 0.035, "min_size_mm": 0.006, "cpg": 5,
               "bl": 4, "peel": 2, "membrane_bl_y1": overrides["membrane_bl_y1"],
               "bulk_max_size_mm": 0.034, "max_aspect_ratio_threshold": 250.0,
               "aspect_ratio_gate": "hard", "AR_max": 228.0}
    manifest = leaf / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    (leaf / "mesh_run_record.json").write_text(json.dumps({"status": "SUCCESS"}), encoding="utf-8")
    DRIVER.require_reusable_mesh(leaf, overrides, geometry, digest)
    for changes in ({"max_aspect_ratio_threshold": 150}, {"AR_max": 251}, {"AR_max": None},
                    {"aspect_ratio_gate": "warn"}, {"max_prism_aspect_ratio_threshold": 300}):
        manifest.write_text(json.dumps({**payload, **changes}), encoding="utf-8")
        with pytest.raises(ValueError, match="Refusing to reuse"):
            DRIVER.require_reusable_mesh(leaf, overrides, geometry, digest)


def test_sensitivity_mesh_table_uses_its_own_ceiling(tmp_path):
    ids = CAMPAIGN.mesh_ids_for_spec(load_mesh_spec(default_spec_path()), max_aspect_ratio=250)
    for geo_id, aspect, ortho in (("D1225_a30", 212, 0.08), ("S_a193_l1733", 228, 0.08),
                                 ("D2450_a30", 251, 0.08), ("D0817_a30", 200, 0.04)):
        path = CAMPAIGN.mesh_manifest_path(tmp_path, geo_id, ids[geo_id])
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"AR_max": aspect, "ortho_min": ortho,
                                    "aspect_ratio_gate": "hard", "max_aspect_ratio_threshold": 250}),
                        encoding="utf-8")
    gates = {row["geo_id"]: row["gate"] for row in CAMPAIGN.mesh_table_rows(tmp_path, ids)}
    assert gates["D1225_a30"] == gates["S_a193_l1733"] == "PASS"
    assert gates["D2450_a30"] == gates["D0817_a30"] == "FAIL"
    path = CAMPAIGN.mesh_manifest_path(tmp_path, "D1225_a30", ids["D1225_a30"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["max_aspect_ratio_threshold"] = 150
    path.write_text(json.dumps(payload), encoding="utf-8")
    row = next(row for row in CAMPAIGN.mesh_table_rows(tmp_path, ids)
               if row["geo_id"] == "D1225_a30")
    assert row["gate"] == "FAIL"
    assert "encoded hard global AR ceiling" in row["error"]
