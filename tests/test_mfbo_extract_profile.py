"""MFBO extract profile and parity comparison. Does not launch Fluent."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.extract_profile import (
    PROFILE_FULL,
    PROFILE_MFBO,
    compare_shared_summary_columns,
    filter_mfbo_summary_rows,
    mfbo_mixing_cup_boundary_indices,
    mfbo_pressure_boundary_indices,
    parse_extract_profile,
    report_extract_argv,
    require_mfbo_summary_columns,
)

parity = load_module(
    "parity_mfbo_extract_under_test",
    SCRIPTS_DIR / "mfbo" / "parity_mfbo_extract.py",
)


def _write_wide(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _leaf(root):
    return (
        root
        / "runs"
        / "pillar"
        / "P_p100_h30"
        / "max085_min006_cpg5_bl4_peel2"
        / "u0p2_p6M_rke"
    )


def test_parse_extract_profile_defaults_to_full():
    assert parse_extract_profile([]) == PROFILE_FULL
    assert parse_extract_profile(["--profile", "mfbo"]) == PROFILE_MFBO
    assert parse_extract_profile(["--profile=full"]) == PROFILE_FULL


def test_parse_extract_profile_rejects_unknown_profile_and_args():
    with pytest.raises(ValueError, match="Unknown extract profile"):
        parse_extract_profile(["--profile", "fast"])
    with pytest.raises(ValueError, match="Unrecognized"):
        parse_extract_profile(["--verbose"])


def test_report_extract_argv_leaves_the_full_command_unchanged():
    assert report_extract_argv("python", "worker.py") == ["python", "worker.py"]
    assert report_extract_argv("python", "worker.py", PROFILE_FULL) == [
        "python",
        "worker.py",
    ]
    assert report_extract_argv("python", "worker.py", PROFILE_MFBO) == [
        "python",
        "worker.py",
        "--profile",
        "mfbo",
    ]


def test_mfbo_boundaries_cover_gate_cells_and_evaluation_flanks():
    evaluation = [5, 6, 7, 8]
    assert mfbo_pressure_boundary_indices(evaluation) == set(range(3, 9))
    assert mfbo_mixing_cup_boundary_indices(evaluation) == set(range(4, 9))


def test_filter_drops_reports_the_mfbo_fields_do_not_need():
    rows = [
        {"metric": "lmh_mass_balance", "value": 12.0, "unit": "LMH"},
        {"metric": "wall_shear_avg", "value": 1.0, "unit": "Pa"},
        {"metric": "cp_canon_all_active_avg", "value": 1.2, "unit": "-"},
        {"metric": "cp_membrane_segment_fluent_computes", "value": 40, "unit": "-"},
        {"metric": "cp_q999_cell_5", "value": 1.1, "unit": "-"},
        {"metric": "cp_canon_window_avg", "value": 1.05, "unit": "-"},
        {"metric": "cp_canon_rescale_delta_max", "value": None, "unit": "-"},
        {"metric": "pp_pressure_drop_cell_5", "value": 100.0, "unit": "Pa"},
        {"metric": "pp_pressure_drop_cell_2", "value": None, "unit": "Pa"},
        {"metric": "profile", "value": "mfbo", "unit": "-"},
    ]
    kept = {row["metric"]: row["value"] for row in filter_mfbo_summary_rows(rows)}
    assert kept["lmh_mass_balance"] == 12.0
    assert kept["cp_canon_window_avg"] == 1.05
    assert kept["cp_canon_rescale_delta_max"] is None
    assert kept["pp_pressure_drop_cell_5"] == 100.0
    assert kept["profile"] == "mfbo"
    assert "wall_shear_avg" not in kept
    assert "cp_canon_all_active_avg" not in kept
    assert "cp_membrane_segment_fluent_computes" not in kept
    assert "cp_q999_cell_5" not in kept
    assert "pp_pressure_drop_cell_2" not in kept


def test_require_mfbo_summary_columns_rejects_a_full_looking_row():
    with pytest.raises(RuntimeError, match="profile"):
        require_mfbo_summary_columns({"lmh_mass_balance": 1.0})


def test_shared_columns_match_within_relative_tolerance():
    full = {
        "lmh_mass_balance": "10.0",
        "area_mem": "0.0",
        "cp_canon_rescale_delta_status": "not_evaluated",
        "wall_shear_avg": "3.0",
    }
    mfbo = {
        "lmh_mass_balance": "10.000000001",
        "area_mem": "0.0",
        "cp_canon_rescale_delta_status": "not_evaluated",
        "profile": "mfbo",
    }
    report = compare_shared_summary_columns(full, mfbo)
    assert report["ok"]
    assert report["shared_columns"] == [
        "area_mem",
        "cp_canon_rescale_delta_status",
        "lmh_mass_balance",
    ]
    assert "wall_shear_avg" in report["only_full"]
    assert report["only_mfbo"] == []
    assert any("profile" in item for item in report["exceptions"])
    assert any("cp_membrane_segment_fluent_computes" in item for item in report["exceptions"])


def test_shared_columns_fail_when_a_value_moves():
    full = {"lmh_mass_balance": "10.0"}
    mfbo = {"lmh_mass_balance": "10.1", "profile": "mfbo"}
    report = compare_shared_summary_columns(full, mfbo)
    assert not report["ok"]
    assert report["mismatches"][0]["column"] == "lmh_mass_balance"


def test_compare_refuses_an_mfbo_reference():
    with pytest.raises(ValueError, match="not a full extract"):
        compare_shared_summary_columns(
            {"profile": "mfbo", "lmh_mass_balance": "1"},
            {"profile": "mfbo", "lmh_mass_balance": "1"},
        )


def test_zero_reference_must_match_exactly():
    report = compare_shared_summary_columns(
        {"area_mem": "0.0"},
        {"area_mem": "1e-15", "profile": "mfbo"},
    )
    assert not report["ok"]


def test_emit_queue_does_not_copy(tmp_path):
    leaf = _leaf(tmp_path)
    queue_path = tmp_path / "queue.json"
    code = parity.main(
        [
            "--run-leaf",
            str(leaf),
            "--data-root",
            "D:/ro_data",
            "--emit-queue",
            str(queue_path),
        ]
    )
    assert code == 0
    jobs = json.loads(queue_path.read_text(encoding="utf-8"))
    assert jobs[0]["id"].startswith("mfbo-profile-parity-")
    assert jobs[0]["argv"][1] == "scripts/mfbo/parity_mfbo_extract.py"
    assert "D:/ro_data" in jobs[0]["argv"]
    assert "--profile" not in jobs[0]["argv"]
    assert jobs[0]["env"]["RO_DATA_ROOT"] == "D:/ro_data"
    assert not (tmp_path / "mfbo_profile_parity").exists()


def test_run_parity_compares_the_copy(tmp_path):
    root = tmp_path / "data"
    leaf = _leaf(root)
    full = {
        "lmh_mass_balance": "12.5",
        "area_mem": "0.001",
        "wall_shear_avg": "4",
    }
    _write_wide(parity.summary_csv(leaf), full)
    (leaf / "marker.txt").write_text("keep", encoding="utf-8")
    seen = {}

    def runner(data_root, copy_dir):
        seen["data_root"] = data_root
        seen["copy"] = copy_dir
        assert (copy_dir / "marker.txt").read_text(encoding="utf-8") == "keep"
        assert not parity.summary_csv(copy_dir).exists()
        _write_wide(
            parity.summary_csv(copy_dir),
            {
                "lmh_mass_balance": "12.5",
                "area_mem": "0.001",
                "profile": "mfbo",
            },
        )
        return 0

    report = parity.run_parity(leaf, root, runner=runner)
    assert report["ok"]
    assert seen["copy"] == root / "mfbo_profile_parity" / leaf.relative_to(root)
    written = json.loads(
        (seen["copy"] / "post" / "reports" / "mfbo_profile_parity.json").read_text(
            encoding="utf-8"
        )
    )
    assert written["ok"]
    assert parity.summary_csv(leaf).is_file()


def test_run_parity_raises_on_extract_failure(tmp_path):
    root = tmp_path / "data"
    leaf = _leaf(root)
    _write_wide(parity.summary_csv(leaf), {"lmh_mass_balance": "1"})

    def runner(data_root, copy_dir):
        log = copy_dir / "geo__mesh__run__extract_attempt1.log"
        log.write_text("boom", encoding="utf-8")
        return 1

    with pytest.raises(RuntimeError, match="Child log:") as exc:
        parity.run_parity(leaf, root, runner=runner)
    assert "extract_attempt1.log" in str(exc.value)


def test_prepare_refuses_a_missing_or_mfbo_summary(tmp_path):
    root = tmp_path / "data"
    leaf = _leaf(root)
    with pytest.raises(FileNotFoundError, match="Full extract summary"):
        parity.prepare_mfbo_profile_copy(leaf, root)
    _write_wide(parity.summary_csv(leaf), {"profile": "mfbo", "lmh_mass_balance": "1"})
    with pytest.raises(ValueError, match="not a full extract"):
        parity.prepare_mfbo_profile_copy(leaf, root)
