"""Offline continuity-quality metadata migration: 95-leaf scope and fail-closed apply."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from helpers import CONFIGS_DIR, load_module
from ro.campaign_geometry import (
    merge_geometry_into_mesh_manifest,
    merge_geometry_into_run_manifest,
)
from ro.continuity_quality_migration import (
    EXPECTED_ORIGINAL_COUNT,
    EXPECTED_TOTAL_COUNT,
    GROUP_GTS_PILOT,
    GROUP_ORIGINAL,
    GROUP_REPLACEMENT,
    GTS_PILOT_EXPECTED_CONTINUITY,
    GTS_PILOT_TARGET,
    ORIGINAL_P6M_RUN_IDS,
    REPLACEMENT_EXPECTED_CONTINUITY,
    REPLACEMENT_EXPECTED_LMH_REL,
    REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    REPLACEMENT_TARGET,
    LeafAssessment,
    MigrationTarget,
    apply_leaf,
    expected_transition_block_reason,
    inspect_leaf,
    main,
    migration_targets,
    raw_report_quality_payload,
    run_migration,
)
from ro.extract_skip import EXTRACT_SOURCE_FILENAME, write_extract_source_record
from ro.manifest import (
    MANIFEST_SCHEMA_VERSION,
    read_run_manifest,
    update_run_manifest_fields,
    write_mesh_manifest,
    write_run_manifest,
)
from ro.paths import mesh_dir, run_dir
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    SOLVER_ATTEMPT_ID_FIELD,
)
from test_manifest import mesh_payload, ref_empty_mesh_payload, run_payload

ATTEMPT_ID = "attempt-1"
CASE_BYTES = b"cas-bytes"
DATA_BYTES = b"dat-bytes"

RESIDUAL_HEADER = (
    "iter  continuity  x-velocity  y-velocity  z-velocity  nacl  "
    "lmh  m_out  m_in  area_mem  time/iter"
)


def _log_text(continuity: float, iteration: int = 2000) -> str:
    return (
        RESIDUAL_HEADER
        + "\n"
        f"{iteration}  {continuity:.4e}  1.0000e-03  2.0000e-03  3.0000e-03  "
        "4.0000e-03  1.0000e+02  -1.9090e-04  2.6629e-04  6.2423e-05  "
        "0:13:30  0\n"
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_leaf(
    monkeypatch,
    tmp_path,
    target: MigrationTarget,
    *,
    saved_quality: str = "FAIL",
    saved_continuity: float = 0.03,
    log_continuity: float = 1.3233e-07,
    lmh: float = REPLACEMENT_EXPECTED_LMH_REL,
    mb: float = REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    write_sidecar: bool = True,
    write_canonical_log: bool = True,
    extra_logs: tuple[tuple[str, str], ...] = (),
    extra_derived: dict | None = None,
):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    mesh_directory = mesh_dir(target.family, target.geo_id, target.mesh_id)
    mesh_directory.mkdir(parents=True, exist_ok=True)
    mesh = (
        ref_empty_mesh_payload()
        if target.family == "empty"
        else mesh_payload()
    )
    mesh["family"] = target.family
    mesh["geo_id"] = target.geo_id
    mesh["mesh_id"] = target.mesh_id
    mesh = merge_geometry_into_mesh_manifest(mesh, target.geo_id)
    mesh["schema_version"] = MANIFEST_SCHEMA_VERSION
    if target.family == "empty":
        mesh["porosity_eps"] = 1.0
    else:
        mesh["porosity_eps"] = 0.908849
    write_mesh_manifest(mesh_directory, mesh)

    directory = run_dir(target.family, target.geo_id, target.mesh_id, target.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    cas = directory / f"{target.geo_id}_{target.run_id}_final.cas.h5"
    dat = directory / f"{target.geo_id}_{target.run_id}_final.dat.h5"
    cas.write_bytes(CASE_BYTES)
    dat.write_bytes(DATA_BYTES)

    payload = run_payload()
    payload["family"] = target.family
    payload["geo_id"] = target.geo_id
    payload["mesh_id"] = target.mesh_id
    payload["run_id"] = target.run_id
    payload = merge_geometry_into_run_manifest(
        payload, target.geo_id, mesh_id=target.mesh_id
    )
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION
    payload[SOLVER_ATTEMPT_ID_FIELD] = ATTEMPT_ID
    payload[FINAL_CASE_SHA256_FIELD] = hashlib.sha256(CASE_BYTES).hexdigest()
    payload[FINAL_DATA_SHA256_FIELD] = hashlib.sha256(DATA_BYTES).hexdigest()
    payload["convergence_quality"] = saved_quality
    payload["needs_longer_solve"] = saved_quality == "FAIL"
    payload["continuity_final"] = saved_continuity
    write_run_manifest(directory, payload)

    if write_canonical_log:
        (directory / f"solver_log_{target.run_id}.txt").write_text(
            _log_text(log_continuity),
            encoding="utf-8",
        )
    for name, text in extra_logs:
        (directory / name).write_text(text, encoding="utf-8")

    reports = directory / "post" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "summary_metrics_wide.csv").write_text(
        (
            "lmh_relative_difference,mass_balance_relative_error\n"
            f"{lmh!r},{mb!r}\n"
        ),
        encoding="utf-8-sig",
    )
    derived = {
        "convergence_quality": saved_quality,
        "needs_longer_solve": saved_quality == "FAIL",
        "convergence_quality_failures": (
            ["continuity_final"] if saved_quality == "FAIL" else []
        ),
        "convergence_quality_warnings": [],
        "continuity_final": saved_continuity,
        "pp_pressure_drop_rel_spread_window": None,
        "pp_pressure_drop_rel_spread_cells_4_7": None,
        "pp_pressure_drop_rel_spread_note": None,
        "keep_me": True,
    }
    if extra_derived:
        derived.update(extra_derived)
    (reports / "raw_report_values.json").write_text(
        json.dumps({"derived_values": derived}, indent=2),
        encoding="utf-8",
    )
    if write_sidecar:
        write_extract_source_record(
            reports,
            {
                FINAL_CASE_SHA256_FIELD: hashlib.sha256(CASE_BYTES).hexdigest(),
                FINAL_DATA_SHA256_FIELD: hashlib.sha256(DATA_BYTES).hexdigest(),
                SOLVER_ATTEMPT_ID_FIELD: ATTEMPT_ID,
            },
        )
    return directory


def _synthetic_originals():
    dummy = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p1_p6M",
    )
    rows: list[LeafAssessment] = []
    for saved, corrected, count in (
        ("PASS", "PASS", 69),
        ("FAIL", "PASS", 17),
        ("FAIL", "FAIL", 7),
    ):
        for _ in range(count):
            rows.append(
                LeafAssessment(
                    dummy,
                    saved_quality=saved,
                    corrected_quality=corrected,
                    continuity_final=1e-7 if corrected == "PASS" else 0.02,
                )
            )
    return rows


def _synthetic_replacement(**updates) -> LeafAssessment:
    fields = dict(
        saved_quality="FAIL",
        corrected_quality="PASS",
        continuity_final=REPLACEMENT_EXPECTED_CONTINUITY,
        lmh_relative_difference=REPLACEMENT_EXPECTED_LMH_REL,
        mass_balance_relative_error=REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    )
    fields.update(updates)
    return LeafAssessment(REPLACEMENT_TARGET, **fields)


def _synthetic_pilot(**updates) -> LeafAssessment:
    fields = dict(
        saved_quality="FAIL",
        corrected_quality="FAIL",
        continuity_final=GTS_PILOT_EXPECTED_CONTINUITY,
    )
    fields.update(updates)
    return LeafAssessment(GTS_PILOT_TARGET, **fields)


def test_selects_exactly_95_leaves_and_excludes_other_columns():
    targets = migration_targets()
    assert len(targets) == EXPECTED_TOTAL_COUNT
    original = [item for item in targets if item.group == GROUP_ORIGINAL]
    replacement = [item for item in targets if item.group == GROUP_REPLACEMENT]
    pilot = [item for item in targets if item.group == GROUP_GTS_PILOT]
    assert len(original) == EXPECTED_ORIGINAL_COUNT
    assert len(replacement) == 1
    assert len(pilot) == 1
    assert len({item.four_id for item in targets}) == EXPECTED_TOTAL_COUNT

    assert {item.run_id for item in original} == ORIGINAL_P6M_RUN_IDS
    assert all("p4M" not in item.run_id for item in targets)
    assert all("p8M" not in item.run_id for item in targets)
    assert all("_restart" not in item.run_id for item in original)
    assert all("ptgts" not in item.run_id for item in original)
    assert all("p4M" not in item.run_id and "p8M" not in item.run_id for item in original)

    assert replacement == [REPLACEMENT_TARGET]
    assert replacement[0].four_id == (
        "sin/S_a144_l1733/max085_min006_cpg5_bl4_peel2/u0p3_p6M_restart"
    )
    assert pilot == [GTS_PILOT_TARGET]
    assert pilot[0].four_id == (
        "diamond/D0817_a30/max085_min006_cpg5_bl4_peel2/u0p3_p6M_ptgts3"
    )
    assert any(
        item.four_id
        == "sin/S_a144_l1733/max085_min006_cpg5_bl4_peel2/u0p3_p6M"
        for item in original
    )
    a60 = [
        item
        for item in original
        if item.geo_id == "D0817_a60"
    ]
    assert a60
    assert {item.mesh_id for item in a60} == {"max060_min006_cpg5_bl4_peel2"}


def test_expected_transitions_match_workstation_audit():
    assessments = (
        _synthetic_originals() + [_synthetic_replacement(), _synthetic_pilot()]
    )
    assert expected_transition_block_reason(assessments) is None


def test_expected_transitions_reject_wrong_original_counts():
    assessments = _synthetic_originals()
    assessments[0] = LeafAssessment(
        assessments[0].target,
        saved_quality="FAIL",
        corrected_quality="PASS",
        continuity_final=1e-7,
    )
    assessments.append(_synthetic_replacement())
    assessments.append(_synthetic_pilot())
    reason = expected_transition_block_reason(assessments)
    assert reason is not None
    assert "neither the pre-migration audit" in reason
    assert "fully corrected post-migration state" in reason
    assert "Partial or unexpected quality metadata" in reason


def test_expected_transitions_accept_fully_corrected_state():
    dummy = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p1_p6M",
    )
    originals = [
        LeafAssessment(
            dummy,
            saved_quality="PASS",
            corrected_quality="PASS",
            continuity_final=1e-7,
        )
        for _ in range(86)
    ] + [
        LeafAssessment(
            dummy,
            saved_quality="FAIL",
            corrected_quality="FAIL",
            continuity_final=0.02,
        )
        for _ in range(7)
    ]
    assessments = originals + [
        _synthetic_replacement(saved_quality="PASS", corrected_quality="PASS"),
        _synthetic_pilot(),
    ]
    assert expected_transition_block_reason(assessments) is None


def test_expected_transitions_reject_wrong_replacement_or_pilot_values():
    base = _synthetic_originals()
    bad_replacement = expected_transition_block_reason(
        base + [_synthetic_replacement(continuity_final=2e-7), _synthetic_pilot()]
    )
    assert bad_replacement is not None
    assert "replacement corrected continuity" in bad_replacement

    bad_pilot = expected_transition_block_reason(
        base + [_synthetic_replacement(), _synthetic_pilot(corrected_quality="PASS")]
    )
    assert bad_pilot is not None
    assert "gts_pilot" in bad_pilot


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    directory = _write_leaf(monkeypatch, tmp_path, target)
    before = _tree_bytes(tmp_path)
    stream = io.StringIO()
    code = run_migration(
        apply=False,
        targets=[target],
        check_expected_transitions=False,
        stream=stream,
    )
    assert code == 0
    assert "DRY_RUN no files written" in stream.getvalue()
    assert _tree_bytes(tmp_path) == before
    sidecar = directory / "post" / "reports" / EXTRACT_SOURCE_FILENAME
    assert sidecar.is_file()


def test_transition_mismatch_aborts_before_apply(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(monkeypatch, tmp_path, target)
    before = _tree_bytes(tmp_path)
    stream = io.StringIO()
    code = run_migration(apply=True, targets=[target], stream=stream)
    assert code == 1
    assert "ABORT" in stream.getvalue()
    assert "no files written" in stream.getvalue()
    assert _tree_bytes(tmp_path) == before


def test_fail_closed_missing_skip_sidecar(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(monkeypatch, tmp_path, target, write_sidecar=False)
    before = _tree_bytes(tmp_path)
    assessment = inspect_leaf(target)
    assert assessment.error is not None
    assert "extract skip evidence missing" in assessment.error
    stream = io.StringIO()
    code = run_migration(
        apply=True,
        targets=[target],
        check_expected_transitions=False,
        stream=stream,
    )
    assert code == 1
    assert _tree_bytes(tmp_path) == before


def test_fail_closed_competing_solver_log(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(
        monkeypatch,
        tmp_path,
        target,
        extra_logs=(
            ("solver_log_u0p2_p6M__attempt1.txt", _log_text(2e-7, iteration=300)),
        ),
    )
    assessment = inspect_leaf(target)
    assert assessment.error is not None
    assert "competing residual-table log" in assessment.error


def test_fluent_transcript_with_residuals_is_not_competing(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(
        monkeypatch,
        tmp_path,
        target,
        extra_logs=(("fluent-00001.trn", _log_text(2e-7, iteration=2000)),),
    )
    assessment = inspect_leaf(target)
    assert assessment.error is None
    assert assessment.log_name == "solver_log_u0p2_p6M.txt"


def test_attempt_log_without_table_is_not_competing(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(
        monkeypatch,
        tmp_path,
        target,
        extra_logs=(("solver_log_u0p2_p6M__attempt1.txt", "no residual table\n"),),
    )
    assessment = inspect_leaf(target)
    assert assessment.error is None
    assert assessment.log_name == "solver_log_u0p2_p6M.txt"


def test_fail_closed_missing_canonical_log(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    _write_leaf(monkeypatch, tmp_path, target, write_canonical_log=False)
    assessment = inspect_leaf(target)
    assert assessment.error is not None
    assert "canonical solver log not found" in assessment.error


def test_fail_closed_nonfinite_lmh(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    directory = _write_leaf(monkeypatch, tmp_path, target)
    wide = directory / "post" / "reports" / "summary_metrics_wide.csv"
    wide.write_text(
        "lmh_relative_difference,mass_balance_relative_error\n,1e-6\n",
        encoding="utf-8-sig",
    )
    assessment = inspect_leaf(target)
    assert assessment.error is not None
    assert "lmh_relative_difference" in assessment.error


def test_pair_write_consistency_and_json_failure_restores(
    monkeypatch, tmp_path
):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    directory = _write_leaf(monkeypatch, tmp_path, target)
    sidecar_before = (
        directory / "post" / "reports" / EXTRACT_SOURCE_FILENAME
    ).read_bytes()
    wide_before = (
        directory / "post" / "reports" / "summary_metrics_wide.csv"
    ).read_bytes()
    log_before = (directory / "solver_log_u0p2_p6M.txt").read_bytes()
    cas_before = (
        directory / f"{target.geo_id}_{target.run_id}_final.cas.h5"
    ).read_bytes()

    assessment = inspect_leaf(target)
    assert assessment.error is None
    assert assessment.saved_quality == "FAIL"
    assert assessment.corrected_quality == "PASS"

    import ro.continuity_quality_migration as mig

    original_write = mig._atomic_write_json

    def boom(path, payload):
        raise OSError("injected json write failure")

    monkeypatch.setattr(mig, "_atomic_write_json", boom)
    with pytest.raises(OSError, match="injected json write failure"):
        apply_leaf(assessment)

    restored = read_run_manifest(directory)
    assert restored["convergence_quality"] == "FAIL"
    assert restored["continuity_final"] == 0.03
    raw = json.loads(
        (directory / "post" / "reports" / "raw_report_values.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["derived_values"]["convergence_quality"] == "FAIL"
    assert raw["derived_values"]["continuity_final"] == 0.03
    assert (
        directory / "post" / "reports" / EXTRACT_SOURCE_FILENAME
    ).read_bytes() == sidecar_before

    monkeypatch.setattr(mig, "_atomic_write_json", original_write)
    written = apply_leaf(inspect_leaf(target))
    assert written == "wrote"
    updated_manifest = read_run_manifest(directory)
    updated_raw = json.loads(
        (directory / "post" / "reports" / "raw_report_values.json").read_text(
            encoding="utf-8"
        )
    )
    assert updated_manifest["convergence_quality"] == "PASS"
    assert updated_manifest["needs_longer_solve"] is False
    assert "convergence_quality_unavailable" in updated_manifest
    derived = updated_raw["derived_values"]
    assert derived["convergence_quality"] == "PASS"
    assert derived["keep_me"] is True
    assert "convergence_quality_unavailable" not in derived
    json_quality = raw_report_quality_payload(inspect_leaf(target).quality_result)
    for key, value in json_quality.items():
        assert derived[key] == value
    assert (
        directory / "post" / "reports" / EXTRACT_SOURCE_FILENAME
    ).read_bytes() == sidecar_before
    assert (
        directory / "post" / "reports" / "summary_metrics_wide.csv"
    ).read_bytes() == wide_before
    assert (directory / "solver_log_u0p2_p6M.txt").read_bytes() == log_before
    assert (
        directory / f"{target.geo_id}_{target.run_id}_final.cas.h5"
    ).read_bytes() == cas_before


def test_apply_is_idempotent(monkeypatch, tmp_path):
    target = MigrationTarget(
        GROUP_ORIGINAL,
        "diamond",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2",
        "u0p2_p6M",
    )
    directory = _write_leaf(monkeypatch, tmp_path, target)
    first = apply_leaf(inspect_leaf(target))
    assert first == "wrote"
    after_first = _tree_bytes(tmp_path)
    backups = [
        path
        for path in directory.rglob("*")
        if ".bak." in path.name
    ]
    assert len(backups) == 2
    second = apply_leaf(inspect_leaf(target))
    assert second == "unchanged"
    assert _tree_bytes(tmp_path) == after_first


def test_replacement_and_pilot_inspect_values(monkeypatch, tmp_path):
    replacement_dir = _write_leaf(
        monkeypatch,
        tmp_path,
        REPLACEMENT_TARGET,
        log_continuity=REPLACEMENT_EXPECTED_CONTINUITY,
        lmh=REPLACEMENT_EXPECTED_LMH_REL,
        mb=REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    )
    replacement = inspect_leaf(REPLACEMENT_TARGET)
    assert replacement.error is None
    assert replacement.saved_quality == "FAIL"
    assert replacement.corrected_quality == "PASS"
    assert replacement.continuity_final == pytest.approx(
        REPLACEMENT_EXPECTED_CONTINUITY
    )
    assert replacement.lmh_relative_difference == pytest.approx(
        REPLACEMENT_EXPECTED_LMH_REL
    )
    assert replacement.mass_balance_relative_error == pytest.approx(
        REPLACEMENT_EXPECTED_MASS_BALANCE_REL
    )
    assert (replacement_dir / "solver_log_u0p3_p6M_restart.txt").is_file()

    _write_leaf(
        monkeypatch,
        tmp_path,
        GTS_PILOT_TARGET,
        log_continuity=GTS_PILOT_EXPECTED_CONTINUITY,
        lmh=REPLACEMENT_EXPECTED_LMH_REL,
        mb=REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    )
    pilot = inspect_leaf(GTS_PILOT_TARGET)
    assert pilot.error is None
    assert pilot.saved_quality == "FAIL"
    assert pilot.corrected_quality == "FAIL"
    assert pilot.continuity_final == pytest.approx(GTS_PILOT_EXPECTED_CONTINUITY)


def test_cli_dry_run_is_default():
    script = load_module(
        "migrate_continuity_quality_under_test",
        CONFIGS_DIR.parent / "scripts" / "migrate_continuity_quality.py",
    )
    assert script.main is main
    from ro.continuity_quality_migration import parse_args

    assert parse_args([]).apply is False
    assert parse_args(["--apply"]).apply is True


def _backup_relpaths(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".bak." in path.name
    }


def _write_first_run_scope(monkeypatch, tmp_path) -> list[MigrationTarget]:
    targets = migration_targets()
    originals = [item for item in targets if item.group == GROUP_ORIGINAL]
    assert len(originals) == EXPECTED_ORIGINAL_COUNT
    for index, target in enumerate(originals):
        if index < 69:
            _write_leaf(
                monkeypatch,
                tmp_path,
                target,
                saved_quality="PASS",
                log_continuity=1.0e-7,
            )
        elif index < 86:
            _write_leaf(
                monkeypatch,
                tmp_path,
                target,
                saved_quality="FAIL",
                log_continuity=1.0e-7,
            )
        else:
            _write_leaf(
                monkeypatch,
                tmp_path,
                target,
                saved_quality="FAIL",
                log_continuity=0.02,
            )
    _write_leaf(
        monkeypatch,
        tmp_path,
        REPLACEMENT_TARGET,
        log_continuity=REPLACEMENT_EXPECTED_CONTINUITY,
        lmh=REPLACEMENT_EXPECTED_LMH_REL,
        mb=REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    )
    _write_leaf(
        monkeypatch,
        tmp_path,
        GTS_PILOT_TARGET,
        log_continuity=GTS_PILOT_EXPECTED_CONTINUITY,
        lmh=REPLACEMENT_EXPECTED_LMH_REL,
        mb=REPLACEMENT_EXPECTED_MASS_BALANCE_REL,
    )
    return targets


def test_run_migration_first_apply_then_complete_rerun(monkeypatch, tmp_path):
    targets = _write_first_run_scope(monkeypatch, tmp_path)
    first = io.StringIO()
    code = run_migration(apply=True, targets=targets, stream=first)
    assert code == 0
    assert "APPLY wrote=95 unchanged=0" in first.getvalue()
    backups_after_first = _backup_relpaths(tmp_path)
    assert len(backups_after_first) == 190
    after_first = _tree_bytes(tmp_path)

    rerun = io.StringIO()
    code = run_migration(apply=True, targets=targets, stream=rerun)
    assert code == 0
    assert "APPLY wrote=0 unchanged=95" in rerun.getvalue()
    assert _backup_relpaths(tmp_path) == backups_after_first
    assert _tree_bytes(tmp_path) == after_first


def test_run_migration_partial_state_fail_closed(monkeypatch, tmp_path):
    targets = _write_first_run_scope(monkeypatch, tmp_path)
    originals = [item for item in targets if item.group == GROUP_ORIGINAL]
    already = originals[69]
    update_run_manifest_fields(
        run_dir(already.family, already.geo_id, already.mesh_id, already.run_id),
        {"convergence_quality": "PASS", "needs_longer_solve": False},
    )
    before = _tree_bytes(tmp_path)
    stream = io.StringIO()
    code = run_migration(apply=True, targets=targets, stream=stream)
    output = stream.getvalue()
    assert code == 1
    assert "ABORT" in output
    assert "neither the pre-migration audit" in output
    assert "Partial or unexpected quality metadata" in output
    assert "APPLY aborted; no files written" in output
    assert _tree_bytes(tmp_path) == before
