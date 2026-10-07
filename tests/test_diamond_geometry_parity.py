"""Path guards and volume comparison for the Diamond geometry parity driver."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module


def load_driver():
    return load_module(
        "run_diamond_geometry_parity_under_test",
        SCRIPTS_DIR / "mfbo" / "run_diamond_geometry_parity.py",
    )


def test_out_root_refuses_production_tree_and_existing_cases(tmp_path):
    driver = load_driver()
    for value in (
        "C:/ro_data",
        "C:/ro_data/geometries",
        r"C:\ro_data\meshes",
        "/mnt/c/ro_data/geom_smoke",
    ):
        with pytest.raises(ValueError, match="production data root"):
            driver.resolve_out_root(value)
    resolved = driver.resolve_out_root(str(tmp_path))
    assert resolved == tmp_path.resolve()

    geo_id = "D2450_a45"
    existing = tmp_path / "gen" / geo_id
    existing.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="case subfolders"):
        driver.refuse_existing_case_dirs(tmp_path, [geo_id])
    empty = tmp_path / "fresh"
    driver.refuse_existing_case_dirs(empty, [geo_id])


def test_production_cases_are_the_nine_manual_files():
    driver = load_driver()
    cases = driver.production_cases()
    assert [case["geo_id"] for case in cases] == [
        "D2450_a30",
        "D2450_a45",
        "D2450_a60",
        "D1225_a30",
        "D1225_a45",
        "D1225_a60",
        "D0817_a30",
        "D0817_a45",
        "D0817_a60",
    ]
    for case in cases:
        assert case["cad_path"] == (
            f"C:/ro_data/geometries/diamond/{case['geo_id']}/{case['geo_id']}.dsco"
        )


def test_unread_volume_is_not_zero_and_a_real_difference_fails():
    driver = load_driver()
    unread = driver.compare_body_volumes(
        [{"name": "a", "regions": None, "volumes": None, "error": "TypeError: none"}],
        [{"name": "b", "regions": ["fluid"], "volumes": [10.0], "error": None}],
    )
    assert unread["status"] == "unread"
    assert unread["current_mm3"] is None
    assert unread["reference_mm3"] == 10.0
    assert driver.summed_body_volume_mm3(
        [{"error": "TypeError", "volumes": None}]
    ) is None

    matched = driver.compare_body_volumes(
        [{"volumes": [1.0, 2.0], "error": None}],
        [{"volumes": [3.0], "error": None}],
    )
    assert matched["status"] == "compared"
    assert matched["rel_diff"] == 0.0

    steps_ok = {
        "reference": {"return_code": 0, "log": "a"},
        "generate": {"return_code": 0, "log": "b"},
        "compare": {"return_code": 0, "log": "c"},
        "orientation": {"return_code": 0, "log": "d"},
    }
    summary = driver.aggregate_case(
        geo_id="D2450_a45",
        steps=steps_ok,
        comparison=None,
        volume=unread,
        face_counts=None,
        orientation_match=True,
    )
    assert summary["volume_status"] == "unread"
    assert driver.case_failed(summary) is False

    differed = driver.aggregate_case(
        geo_id="D2450_a45",
        steps=steps_ok,
        comparison=None,
        volume={"status": "compared", "rel_diff": 0.01, "current_mm3": 1.01, "reference_mm3": 1.0},
        face_counts=None,
        orientation_match=True,
    )
    assert driver.case_failed(differed) is True

    swapped = dict(steps_ok)
    swapped["orientation"] = {"return_code": 1, "log": "d"}
    mismatch = driver.aggregate_case(
        geo_id="D2450_a45",
        steps=swapped,
        comparison=None,
        volume=unread,
        face_counts=None,
        orientation_match=False,
    )
    assert mismatch["orientation_match"] is False
    assert driver.case_failed(mismatch) is True


def test_unknown_only_id_raises():
    driver = load_driver()
    with pytest.raises(ValueError, match="Unknown geo_id"):
        driver.select_cases(driver.production_cases(), ["P_p100_h30"])
