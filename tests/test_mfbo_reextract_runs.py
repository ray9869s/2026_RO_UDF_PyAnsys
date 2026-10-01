"""Guards for scripts/mfbo/reextract_runs.py. Does not launch Fluent."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from helpers import SCRIPTS_DIR, load_module

reextract_runs = load_module(
    "reextract_runs_under_test",
    SCRIPTS_DIR / "mfbo" / "reextract_runs.py",
)


def _leaf(root):
    return (
        root
        / "runs"
        / "pillar"
        / "P_p100_h30"
        / "max085_min006_cpg5_bl4_peel2"
        / "u0p2_p6M_rke"
    )


def test_refuses_production_roots():
    samples = [
        "C:/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
        "c:/RO_DATA/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
        "/mnt/c/ro_data/runs/pillar/P_p100_h30/mesh/u0p2_p6M",
    ]
    for sample in samples:
        with pytest.raises(ValueError, match="production"):
            reextract_runs.parse_run_leaf(sample)


def test_archive_renames_and_keeps_previous_reports(tmp_path):
    leaf = _leaf(tmp_path)
    reports = leaf / "post" / "reports"
    reports.mkdir(parents=True)
    (reports / "summary_metrics_wide.csv").write_text("lmh_mass_balance\n24.5\n")
    moment = datetime(2026, 10, 1, 11, 2, 3, tzinfo=timezone.utc)
    dest = reextract_runs.archive_reports(leaf, now=moment)
    assert dest == leaf / "post" / "reports_prev_20261001T110203Z"
    assert dest.is_dir()
    assert (dest / "summary_metrics_wide.csv").read_text().startswith("lmh")
    assert not reports.exists()


def test_reextract_uses_leaf_data_root_and_does_not_keep_parent_env(
    monkeypatch, tmp_path
):
    leaf = _leaf(tmp_path)
    (leaf / "post" / "reports").mkdir(parents=True)
    (leaf / "post" / "reports" / "summary_metrics_wide.csv").write_text("x\n")
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    seen = {}

    def fake_launch(data_root, run_directory, case, *, geo_id, mesh_id, run_id):
        seen["data_root"] = data_root
        seen["run_directory"] = run_directory
        seen["case"] = case
        seen["geo_id"] = geo_id
        seen["mesh_id"] = mesh_id
        seen["run_id"] = run_id
        seen["parent_env"] = os.environ.get("RO_DATA_ROOT")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reextract_runs.mfbo_common, "launch_extract", fake_launch)
    monkeypatch.setattr(
        reextract_runs,
        "operating_point_case",
        lambda data_root, run_leaf: {
            "inlet_velocity_value": 0.2,
            "outlet_gauge_pressure": 6.0e6,
        },
    )
    assert reextract_runs.main([str(leaf)]) == 0
    assert seen["data_root"] == tmp_path.resolve()
    assert seen["run_directory"] == leaf.resolve()
    assert seen["geo_id"] == "P_p100_h30"
    assert seen["mesh_id"] == "max085_min006_cpg5_bl4_peel2"
    assert seen["run_id"] == "u0p2_p6M_rke"
    assert seen["case"]["inlet_velocity_value"] == 0.2
    assert seen["parent_env"] is None
    assert os.environ.get("RO_DATA_ROOT") is None
    assert not (leaf / "post" / "reports").exists()
    archives = list((leaf / "post").glob("reports_prev_*"))
    assert len(archives) == 1
    assert (archives[0] / "summary_metrics_wide.csv").is_file()
