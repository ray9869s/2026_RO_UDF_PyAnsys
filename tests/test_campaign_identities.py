"""R-11 campaign/solver identities. Validators over existing fields. No Fluent."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from helpers import (
    SCRIPTS_DIR,
    load_module,
    load_run_config,
    load_solver_code,
    populate_valid_solver_config,
)
from ro.manifest import (
    MESH_MANIFEST_REQUIRED_FIELDS,
    read_mesh_manifest,
    write_mesh_manifest,
    write_run_manifest,
)
from ro.manifest_validation import (
    ManifestValidationError,
    campaign_blocked_frac_block_reason,
    mesh_run_blocked_frac_block_reason,
    require_campaign_membrane_blocked_area_frac,
    require_mesh_run_blocked_frac_agree,
    validate_mesh_geometry_fields,
)
from ro.paths import mesh_dir, run_dir
from ro.solver_common import (
    make_base_case_name,
    max_iterations_from_common_solver_settings,
    mesh_sha256_file_block_reason,
    require_mesh_sha256_matches_file,
    require_run_id_matches_operating_point,
    require_u_mean_profile_identity,
    residual_target_from_common_solver_settings,
    run_id_operating_point_block_reason,
    u_mean_profile_identity_block_reason,
)
from test_manifest import FAMILY, GEO_ID, MESH_ID, mesh_payload, run_payload


D2450_U_MEAN_MS = 0.1992807169514518
D2450_G = 1.00360939613
D2450_U_TARGET_MS = 0.2


def _load_report():
    return load_module(
        "report_campaign_identities_under_test",
        SCRIPTS_DIR / "report_campaign_identities.py",
    )


def test_schema_range_still_allows_historical_nonzero_blocked_frac(monkeypatch, tmp_path):
    """0.08 must remain readable. Campaign 0.0 is not a schema required value."""
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    payload = mesh_payload()
    payload["membrane_blocked_area_frac"] = 0.08
    validate_mesh_geometry_fields(payload)
    directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    directory.mkdir(parents=True)
    write_mesh_manifest(directory, payload)
    loaded = read_mesh_manifest(directory)
    assert loaded["membrane_blocked_area_frac"] == 0.08
    assert "membrane_blocked_area_frac" in MESH_MANIFEST_REQUIRED_FIELDS
    reason = campaign_blocked_frac_block_reason(loaded)
    assert reason is not None
    assert "exactly 0.0" in reason


@pytest.mark.parametrize("blocked", [0.08, 0.06, 0.05])
def test_campaign_blocked_frac_rejects_old_pillar_values(blocked):
    payload = {"membrane_blocked_area_frac": blocked}
    reason = campaign_blocked_frac_block_reason(payload)
    assert reason is not None
    with pytest.raises(ManifestValidationError, match="exactly 0.0"):
        require_campaign_membrane_blocked_area_frac(payload, kind="Mesh")


def test_campaign_blocked_frac_accepts_exact_zero():
    payload = {"membrane_blocked_area_frac": 0.0}
    assert campaign_blocked_frac_block_reason(payload) is None
    require_campaign_membrane_blocked_area_frac(payload, kind="Mesh")


def test_campaign_blocked_frac_missing_key_is_not_a_reject():
    assert campaign_blocked_frac_block_reason({}) is None


def test_mesh_run_blocked_frac_must_agree():
    mesh = {"membrane_blocked_area_frac": 0.0}
    run = {"membrane_blocked_area_frac": 0.08}
    reason = mesh_run_blocked_frac_block_reason(mesh, run)
    assert reason is not None
    assert "disagree" in reason
    with pytest.raises(ManifestValidationError, match="disagree"):
        require_mesh_run_blocked_frac_agree(mesh, run)
    require_mesh_run_blocked_frac_agree(mesh, {"membrane_blocked_area_frac": 0.0})


def test_u_mean_profile_identity_d2450_fixture_passes():
    assert (
        u_mean_profile_identity_block_reason(
            D2450_U_MEAN_MS,
            D2450_G,
            D2450_U_TARGET_MS,
        )
        is None
    )
    require_u_mean_profile_identity(
        D2450_U_MEAN_MS,
        D2450_G,
        D2450_U_TARGET_MS,
    )


def test_u_mean_profile_identity_rejects_mismatch():
    reason = u_mean_profile_identity_block_reason(0.199281, D2450_G, D2450_U_TARGET_MS)
    assert reason is not None
    with pytest.raises(ValueError, match="does not equal u_target_ms"):
        require_u_mean_profile_identity(0.199281, D2450_G, D2450_U_TARGET_MS)


@pytest.mark.parametrize(
    ("u_mean", "g", "inlet_bc_type"),
    [
        (None, D2450_G, "parabolic"),
        (D2450_U_MEAN_MS, None, "parabolic"),
        (D2450_U_MEAN_MS, D2450_G, "plug"),
    ],
)
def test_u_mean_profile_identity_unfilled_or_plug_is_not_a_reject(
    u_mean, g, inlet_bc_type
):
    assert (
        u_mean_profile_identity_block_reason(
            u_mean,
            g,
            D2450_U_TARGET_MS,
            inlet_bc_type=inlet_bc_type,
        )
        is None
    )


def test_run_id_must_match_make_base_case_name():
    assert make_base_case_name(0.2, 6.0e6) == "u0p2_p6M"
    reason = run_id_operating_point_block_reason("u0p1_p6M", 0.2, 6.0e6)
    assert reason is not None
    with pytest.raises(ValueError, match="does not match make_base_case_name"):
        require_run_id_matches_operating_point("u0p1_p6M", 0.2, 6.0e6)


def test_run_id_letter_suffix_uses_the_same_operating_point_token():
    assert (
        run_id_operating_point_block_reason("u0p2_p6M_plug", 0.2, 6.0e6) is None
    )
    require_run_id_matches_operating_point("u0p2_p6M_plug", 0.2, 6.0e6)


def test_mesh_sha256_rejects_mismatch_and_missing_file(tmp_path: Path):
    mesh_file = tmp_path / "D2450_a45_max085_min006_cpg5_bl4_peel2.msh.h5"
    mesh_file.write_bytes(b"mesh-bytes")
    actual = hashlib.sha256(b"mesh-bytes").hexdigest()
    assert mesh_sha256_file_block_reason(actual, mesh_file) is None
    require_mesh_sha256_matches_file(actual, mesh_file)
    reason = mesh_sha256_file_block_reason("a" * 64, mesh_file)
    assert reason is not None
    assert "does not match" in reason
    with pytest.raises(ValueError, match="does not match"):
        require_mesh_sha256_matches_file("a" * 64, mesh_file)
    missing = tmp_path / "absent.msh.h5"
    missing_reason = mesh_sha256_file_block_reason(actual, missing)
    assert missing_reason is not None
    assert "was not found" in missing_reason


@pytest.mark.parametrize(
    "settings",
    [
        None,
        "not-a-dict",
        {},
        {"max_iterations": True},
        {"max_iterations": "2000"},
        {"max_iterations": 0},
    ],
)
def test_malformed_max_iterations_raises_without_default(settings):
    with pytest.raises(ValueError, match="max_iterations|must be a dict"):
        max_iterations_from_common_solver_settings(settings)


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {"residual_target": True},
        {"residual_target": "not-a-float"},
        {"residual_target": 0.0},
        {"residual_target": float("nan")},
    ],
)
def test_malformed_residual_target_raises_without_default(settings):
    with pytest.raises(ValueError, match="residual_target|must be a dict"):
        residual_target_from_common_solver_settings(settings)


def test_write_worker_rejects_nonzero_campaign_blocked_frac(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    solver = load_solver_code("r11_blocked_frac")
    cfg = load_run_config()
    populate_valid_solver_config(cfg)
    cfg.family = FAMILY
    cfg.geo_id = GEO_ID
    cfg.mesh_id = MESH_ID
    cfg.run_id = "u0p1_p4M"
    mesh_directory = mesh_dir(cfg.family, cfg.geo_id, cfg.mesh_id)
    mesh_directory.mkdir(parents=True)
    mesh = mesh_payload()
    mesh["membrane_blocked_area_frac"] = 0.08
    write_mesh_manifest(mesh_directory, mesh)
    run_directory = run_dir(cfg.family, cfg.geo_id, cfg.mesh_id, cfg.run_id)
    run_directory.mkdir(parents=True)
    with pytest.raises(ManifestValidationError, match="exactly 0.0"):
        solver.write_worker_run_manifest(cfg, mesh_directory, run_directory)


def test_report_distinguishes_na_from_reject(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    (tmp_path / "runs").mkdir()
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    mesh_bytes = b"r11-mesh"
    mesh_file = mesh_directory / f"{GEO_ID}_{MESH_ID}.msh.h5"
    mesh_file.write_bytes(mesh_bytes)
    mesh = mesh_payload()
    mesh["mesh_sha256"] = hashlib.sha256(mesh_bytes).hexdigest()
    mesh["inlet_profile_G"] = None
    write_mesh_manifest(mesh_directory, mesh)
    report = _load_report()
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "blocked=PASS" in out
    assert "sha=PASS" in out
    assert "blocked_reject=0" in out
    assert "sha_reject=0" in out

    (mesh_directory / "manifest.json").unlink()
    mesh["membrane_blocked_area_frac"] = 0.08
    write_mesh_manifest(mesh_directory, mesh)
    report.main()
    rejected = capsys.readouterr().out
    assert "blocked=REJECT" in rejected
    assert "blocked_reject=1" in rejected


def test_report_marks_missing_g_as_na_not_reject(monkeypatch, tmp_path):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    (tmp_path / "runs").mkdir()
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    mesh_directory.mkdir(parents=True)
    write_mesh_manifest(mesh_directory, mesh_payload())
    run = run_payload()
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, run["run_id"])
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, run)
    report = _load_report()
    buf = io.StringIO()
    report.report_run_leaf(run_directory / "manifest.json", run, file=buf)
    line = buf.getvalue()
    assert "uG=N/A" in line
    assert "uG=REJECT" not in line


def test_report_requires_ro_data_root(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    report = _load_report()
    with pytest.raises(ValueError, match="RO_DATA_ROOT"):
        report.main()
