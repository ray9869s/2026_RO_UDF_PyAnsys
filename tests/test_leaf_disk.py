"""Checkpoint and setup cleanup on a finished run leaf."""

from __future__ import annotations

import json

from ro.leaf_disk import (
    checkpoint_files,
    cleanup_line,
    directory_bytes,
    release_after_pass,
)


def _plant(leaf):
    leaf.mkdir(parents=True)
    (leaf / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5").write_bytes(b"cas")
    (leaf / "D2450_a45_u0p2_p6M_checkpoint_000200.dat.h5").write_bytes(b"dat")
    (leaf / "D2450_a45_u0p2_p6M_checkpoint_000100.dat.h5").write_bytes(b"orphan")
    (leaf / "D2450_a45_u0p2_p6M_setup.cas.h5").write_bytes(b"setup")
    (leaf / "D2450_a45_u0p2_p6M_final.cas.h5").write_bytes(b"fcas")
    (leaf / "D2450_a45_u0p2_p6M_final.dat.h5").write_bytes(b"fdat")
    (leaf / "solve.log").write_bytes(b"log")
    (leaf / "manifest.json").write_text(
        json.dumps({"convergence_quality": "PASS"}),
        encoding="utf-8",
    )
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "keep.txt").write_bytes(b"post")
    (reports / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5").write_bytes(b"nested")
    (reports / "D2450_a45_u0p2_p6M_setup.cas.h5").write_bytes(b"nested-setup")


def test_pass_drops_checkpoints_and_setup_only(tmp_path):
    leaf = tmp_path / "run"
    _plant(leaf)
    outcome = release_after_pass(leaf, "D2450_a45", "u0p2_p6M", keep=False)
    assert outcome["kept_reason"] is None
    assert len(outcome["removed"]) == 4
    assert checkpoint_files(leaf, "D2450_a45", "u0p2_p6M") == []
    assert not (leaf / "D2450_a45_u0p2_p6M_setup.cas.h5").exists()
    assert (leaf / "D2450_a45_u0p2_p6M_final.cas.h5").read_bytes() == b"fcas"
    assert (leaf / "D2450_a45_u0p2_p6M_final.dat.h5").read_bytes() == b"fdat"
    assert (leaf / "solve.log").read_bytes() == b"log"
    assert (leaf / "manifest.json").is_file()
    assert (leaf / "post" / "reports" / "keep.txt").read_bytes() == b"post"
    nested = leaf / "post" / "reports" / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5"
    assert nested.read_bytes() == b"nested"
    assert (
        leaf / "post" / "reports" / "D2450_a45_u0p2_p6M_setup.cas.h5"
    ).read_bytes() == b"nested-setup"
    assert "Removed 4 checkpoint and setup files." in cleanup_line(outcome)
    assert outcome["leaf_bytes"] == directory_bytes(leaf)


def test_failure_and_opt_out_keep_the_files(tmp_path):
    leaf = tmp_path / "run"
    _plant(leaf)
    (leaf / "manifest.json").write_text(
        json.dumps({"convergence_quality": "FAIL"}),
        encoding="utf-8",
    )
    failed = release_after_pass(leaf, "D2450_a45", "u0p2_p6M", keep=False)
    assert failed["kept_reason"] == "gate FAIL"
    assert (leaf / "D2450_a45_u0p2_p6M_checkpoint_000200.cas.h5").is_file()
    assert (leaf / "D2450_a45_u0p2_p6M_setup.cas.h5").is_file()

    (leaf / "manifest.json").write_text(
        json.dumps({"convergence_quality": "PASS"}),
        encoding="utf-8",
    )
    (leaf / "D2450_a45_u0p2_p6M_final.cas.h5").unlink()
    missing = release_after_pass(leaf, "D2450_a45", "u0p2_p6M", keep=False)
    assert missing["kept_reason"] == "final pair missing"
    assert (leaf / "D2450_a45_u0p2_p6M_setup.cas.h5").is_file()

    (leaf / "D2450_a45_u0p2_p6M_final.cas.h5").write_bytes(b"fcas")
    kept = release_after_pass(leaf, "D2450_a45", "u0p2_p6M", keep=True)
    assert kept["kept_reason"] == "--keep-checkpoints"
    assert (leaf / "D2450_a45_u0p2_p6M_checkpoint_000200.dat.h5").is_file()
