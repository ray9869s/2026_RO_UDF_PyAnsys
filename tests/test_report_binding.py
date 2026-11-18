"""B2-01: reporting uses post/reports only when it describes the current final."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.report_binding import REPORT_BOUND, report_binding_reason, report_binding_status

REEXTRACT = load_module("campaign_v3_reextract_under_test", SCRIPTS_DIR / "campaign_v3_reextract.py")
CAMPAIGN = REEXTRACT.campaign
MESH_ID = "max035_min006_cpg5_bl4_peel2_y1h020_bms034"
CAS, DAT = b"case-bytes", b"data-bytes"
HASHES = {
    "final_case_sha256": hashlib.sha256(CAS).hexdigest(),
    "final_data_sha256": hashlib.sha256(DAT).hexdigest(),
}


def _leaf(root, geo="D2450_a60", family="diamond", *, gate="PASS", sidecar=HASHES, csv=True, final=True):
    leaf = root / "runs" / family / geo / MESH_ID / "u0p2_p6M"
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    if csv:
        (reports / "summary_metrics_wide.csv").write_text(
            "cp_meanfirst_live_window_avg\n1.2\n", encoding="utf-8"
        )
    if sidecar is not None:
        (reports / "extract_source.json").write_text(json.dumps(sidecar), encoding="utf-8")
    manifest = {"convergence_quality": gate, "geo_id": geo, "family": family, **HASHES}
    (leaf / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if final:
        (leaf / f"{geo}_u0p2_p6M_final.cas.h5").write_bytes(CAS)
        (leaf / f"{geo}_u0p2_p6M_final.dat.h5").write_bytes(DAT)
    return leaf


def test_bound_when_sidecar_matches_manifest(tmp_path):
    leaf = _leaf(tmp_path)
    assert report_binding_reason(leaf) is None
    assert report_binding_status(leaf) == REPORT_BOUND


def test_continued_leaf_is_unbound(tmp_path):
    old = dict(HASHES, final_data_sha256="0" * 64)
    leaf = _leaf(tmp_path, sidecar=old)
    reason = report_binding_reason(leaf)
    assert "different final" in reason and "final_data_sha256" in reason


@pytest.mark.parametrize(
    "kwargs, fragment",
    [({"sidecar": None}, "extract_source.json"), ({"csv": False}, "summary_metrics_wide.csv")],
)
def test_missing_evidence_is_unbound(tmp_path, kwargs, fragment):
    assert fragment in report_binding_reason(_leaf(tmp_path, **kwargs))


def test_manifest_without_hashes_is_unbound(tmp_path):
    leaf = _leaf(tmp_path)
    (leaf / "manifest.json").write_text(json.dumps({"convergence_quality": "PASS"}), encoding="utf-8")
    assert "no final" in report_binding_reason(leaf)


def test_leaf_metrics_blanks_unbound_row_with_reason(tmp_path):
    leaf = _leaf(tmp_path, sidecar=dict(HASHES, final_case_sha256="1" * 64))
    metrics = CAMPAIGN.leaf_metrics(leaf)
    assert metrics["cp"] is None
    assert metrics["gate"] == "PASS"
    assert "different final" in metrics["report_binding"]
    bound = CAMPAIGN.leaf_metrics(_leaf(tmp_path / "b"))
    assert bound["cp"] == pytest.approx(1.2)
    assert bound["report_binding"] == REPORT_BOUND


def test_emitter_includes_stale_non_pass_leaf_and_skips_bound_fail(tmp_path):
    _leaf(tmp_path, "D2450_a60", gate=None, sidecar=dict(HASHES, final_data_sha256="0" * 64))
    _leaf(tmp_path, "D2450_a45", gate="FAIL")
    _leaf(tmp_path, "D1225_a45", gate="PASS")
    _leaf(tmp_path, "D0817_a30", gate=None, sidecar=None, final=False)
    seen = []
    jobs = REEXTRACT.pass_final_jobs(tmp_path, report=seen.append)
    ids = sorted(job["id"] for job in jobs)
    assert ids == [
        f"reextract-D1225_a45-{MESH_ID}-u0p2_p6M",
        f"reextract-D2450_a60-{MESH_ID}-u0p2_p6M",
    ]
    assert any(line.startswith("STALE") and "D2450_a60" in line for line in seen)
    assert any(line.startswith("SKIP") and "D0817_a30" in line for line in seen)


def test_worker_verifies_final_hashes(tmp_path):
    leaf = _leaf(tmp_path, gate=None, sidecar=None)
    assert REEXTRACT.require_reextract_final(leaf, tmp_path) == leaf.resolve()
    (leaf / "D2450_a60_u0p2_p6M_final.dat.h5").write_bytes(b"half-written")
    with pytest.raises(ValueError, match="does not match manifest"):
        REEXTRACT.require_reextract_final(leaf, tmp_path)


def _snapshot(leaf, geo, iteration):
    for extension in ("cas", "dat"):
        (leaf / f"{geo}_u0p2_p6M_iter_{iteration:06d}.{extension}.h5").write_bytes(b"x")


def test_snapshot_jobs_target_the_single_leaf(tmp_path):
    leaf = _leaf(tmp_path, "S_a072_l3465", "sin")
    _snapshot(leaf, "S_a072_l3465", 2000)
    _snapshot(leaf, "S_a072_l3465", 2500)
    jobs = REEXTRACT.snapshot_jobs(
        tmp_path, [REEXTRACT.parse_snapshot("S_a072_l3465:2000"), ("S_a072_l3465", 2500)]
    )
    assert [job["argv"][-2:] for job in jobs] == [
        ["--snapshot-iteration", "2000"],
        ["--snapshot-iteration", "2500"],
    ]
    with pytest.raises(ValueError, match="exactly one"):
        REEXTRACT.snapshot_jobs(tmp_path, [("S_a072_l3465", 3000)])
    with pytest.raises(ValueError, match="GEO_ID:N"):
        REEXTRACT.parse_snapshot("S_a072_l3465")


def test_snapshot_worker_extracts_iter_pair_into_meanfirst_dir(tmp_path, monkeypatch):
    leaf = _leaf(tmp_path, "S_a072_l3465", "sin")
    _snapshot(leaf, "S_a072_l3465", 2000)
    seen = {}

    def fake_launch(data_root, run_directory, case, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(REEXTRACT.reextract_runs.mfbo_common, "launch_extract", fake_launch)
    monkeypatch.setattr(
        REEXTRACT.reextract_runs, "operating_point_case", lambda root, leaf: {}
    )
    REEXTRACT.reextract_snapshot(leaf, tmp_path, 2000)
    assert seen["report_dirname"] == "reports_iter2000_meanfirst"
    assert seen["snapshot_iteration"] == 2000
    assert Path(seen["final_data"]).name == "S_a072_l3465_u0p2_p6M_iter_002000.dat.h5"
    assert (leaf / "post" / "reports").is_dir()


def _extract_functions(*names):
    """Exec selected top-level functions; the worker imports ansys at load."""
    import ast
    import re

    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    namespace = {"re": re}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "extract", "exec"), namespace)
    return namespace


def test_extract_snapshot_dirname_needs_iteration():
    ns = _extract_functions("_report_dirname", "require_snapshot_layout", "report_binds_manifest")
    assert ns["_report_dirname"]("reports_iter2000_meanfirst") == "reports_iter2000_meanfirst"
    with pytest.raises(ValueError):
        ns["_report_dirname"]("reports_iter2000_other")
    good = SimpleNamespace(report_dirname="reports_iter2000_meanfirst", report_snapshot_iteration=2000)
    ns["require_snapshot_layout"](good)
    assert ns["report_binds_manifest"](good) is False
    assert ns["report_binds_manifest"](SimpleNamespace()) is True
    for bad in (
        SimpleNamespace(report_dirname="reports_iter2000_meanfirst"),
        SimpleNamespace(report_dirname="reports", report_snapshot_iteration=2000),
        SimpleNamespace(report_dirname="reports_iter2500_meanfirst", report_snapshot_iteration=2000),
    ):
        with pytest.raises(ValueError, match="snapshot extract"):
            ns["require_snapshot_layout"](bad)


def test_launch_extract_requires_snapshot_iteration_for_custom_pair(tmp_path):
    common = load_module("mfbo_common_snapshot", SCRIPTS_DIR / "mfbo" / "_common.py")
    with pytest.raises(ValueError, match="snapshot_iteration"):
        common.launch_extract(
            tmp_path, tmp_path, {}, geo_id="g", mesh_id="m", run_id="r",
            final_case=tmp_path / "a.cas.h5", final_data=tmp_path / "a.dat.h5",
        )
