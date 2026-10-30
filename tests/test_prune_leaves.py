"""Disk report and dry-run prune of passed run leaves."""

from __future__ import annotations

import json

from helpers import SCRIPTS_DIR, load_module

PRUNE = load_module("prune_leaves_under_test", SCRIPTS_DIR / "prune_leaves.py")

GEO = "D2450_a45"
RUN = "u0p2_p6M"


def _validator(path):
    if path.is_file() and path.read_text(encoding="utf-8") == "ok\n":
        return True, "OK"
    return False, "bad"


def _leaf(root, name, *, gate, csv, source=True, finals=True):
    leaf = root / "runs" / "diamond" / name / "mesh" / RUN
    leaf.mkdir(parents=True)
    payload = {}
    if gate is not None:
        payload["convergence_quality"] = gate
    (leaf / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (leaf / "solve.log").write_bytes(b"log")
    (leaf / f"{name}_{RUN}_checkpoint_000200.cas.h5").write_bytes(b"cas200")
    (leaf / f"{name}_{RUN}_checkpoint_000200.dat.h5").write_bytes(b"dat200")
    (leaf / f"{name}_{RUN}_setup.cas.h5").write_bytes(b"setup!")
    if finals:
        (leaf / f"{name}_{RUN}_final.cas.h5").write_bytes(b"finalcas")
        (leaf / f"{name}_{RUN}_final.dat.h5").write_bytes(b"finaldat")
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text(csv, encoding="utf-8")
    (reports / f"{name}_{RUN}_checkpoint_000200.cas.h5").write_bytes(b"nested")
    if source:
        (reports / "extract_source.json").write_text("{}\n", encoding="utf-8")
    return leaf


def test_dry_run_reports_each_root_and_deletes_nothing(tmp_path, capsys, monkeypatch):
    passed = tmp_path / "passed"
    held = tmp_path / "held"
    good = _leaf(passed, "D2450_a45", gate="PASS", csv="ok\n")
    failed = _leaf(held, "D2450_a45", gate="FAIL", csv="ok\n")
    _leaf(held, "D0817_a30", gate="PASS", csv="ok\n", source=False)
    _leaf(held, "D0817_a45", gate="PASS", csv="no\n")
    real_validator = PRUNE.batch_report_extract.validate_summary_wide_csv
    monkeypatch.setattr(
        PRUNE.batch_report_extract,
        "validate_summary_wide_csv",
        _validator,
    )
    code = PRUNE.main(
        ["--data-root", str(passed), "--data-root", str(held)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("dry-run") == 2
    assert f"data_root: {passed}" in out
    assert f"data_root: {held}" in out
    assert "eligible: 1 leaf, 18 bytes (0.00 GiB)" in out
    assert (good / f"{GEO}_{RUN}_checkpoint_000200.cas.h5").is_file()
    assert (good / f"{GEO}_{RUN}_setup.cas.h5").is_file()
    assert (failed / f"{GEO}_{RUN}_checkpoint_000200.dat.h5").is_file()
    assert PRUNE.leaf_is_eligible(good, GEO, RUN, real_validator) is False


def test_apply_prunes_only_pass_leaves_with_a_finished_extract(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(
        PRUNE.batch_report_extract,
        "validate_summary_wide_csv",
        _validator,
    )
    root = tmp_path / "root"
    good = _leaf(root, "D2450_a45", gate="PASS", csv="ok\n")
    failed = _leaf(root, "D0817_a30", gate="FAIL", csv="ok\n")
    incomplete = _leaf(root, "D0817_a45", gate="PASS", csv="ok\n", source=False)
    no_final = _leaf(root, "D0817_a60", gate="PASS", csv="ok\n", finals=False)
    code = PRUNE.main(
        ["--data-root", str(root), "--apply"],
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "dry-run" not in out
    assert "removed: 3 files, 18 bytes (0.00 GiB)" in out
    assert "eligible: 1 leaf, 18 bytes (0.00 GiB)" in out
    assert not (good / f"{GEO}_{RUN}_checkpoint_000200.cas.h5").exists()
    assert not (good / f"{GEO}_{RUN}_checkpoint_000200.dat.h5").exists()
    assert not (good / f"{GEO}_{RUN}_setup.cas.h5").exists()
    assert (good / f"{GEO}_{RUN}_final.cas.h5").read_bytes() == b"finalcas"
    assert (good / f"{GEO}_{RUN}_final.dat.h5").read_bytes() == b"finaldat"
    assert (good / "solve.log").read_bytes() == b"log"
    assert (good / "manifest.json").is_file()
    nested = good / "post" / "reports" / f"{GEO}_{RUN}_checkpoint_000200.cas.h5"
    assert nested.read_bytes() == b"nested"
    for leaf, geo in (
        (failed, "D0817_a30"),
        (incomplete, "D0817_a45"),
        (no_final, "D0817_a60"),
    ):
        assert (leaf / f"{geo}_{RUN}_checkpoint_000200.cas.h5").is_file()
        assert (leaf / f"{geo}_{RUN}_setup.cas.h5").is_file()


def test_missing_runs_directory_is_an_error(tmp_path, capsys):
    code = PRUNE.main(["--data-root", str(tmp_path / "empty")])
    assert code == 1
    assert "No runs directory" in capsys.readouterr().err
