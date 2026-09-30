"""Path guards and summary aggregation for the Pillar geometry parity driver."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module


def load_driver():
    return load_module(
        "run_geometry_parity_all_under_test",
        SCRIPTS_DIR / "mfbo" / "run_geometry_parity_all.py",
    )


def load_probe():
    return load_module(
        "probe_reference_geometry_area_under_test",
        SCRIPTS_DIR / "_probe_reference_geometry.py",
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

    geo_id = "P_p100_h30"
    existing = tmp_path / "ref" / geo_id
    existing.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="case subfolders"):
        driver.refuse_existing_case_dirs(tmp_path, [geo_id])
    empty = tmp_path / "fresh"
    driver.refuse_existing_case_dirs(empty, [geo_id])


def test_design_points_use_batch_config_helpers():
    driver = load_driver()
    batch = driver.load_batch_config()
    points = driver.design_points(batch)
    expected = [
        {
            "d_p_mm": d_mm,
            "d_h_mm": h_mm,
            "geo_id": batch._pillar_geo_id(d_mm, h_mm),
        }
        for d_mm in batch._PILLAR_D_MM
        for h_mm in batch._PILLAR_H_MM
    ]
    assert points == expected
    assert len(points) == 9


def test_summary_aggregation_reads_comparison_and_face_counts():
    driver = load_driver()
    comparison = {
        "labels_only_in_current": [],
        "labels_only_in_reference": ["wall_spacer_hole"],
        "labels": [
            {
                "label": "wall_spacer_pillar",
                "max_abs_diff_mm": 0.01,
                "zone_count": "PASS",
                "area_rel_diff": 0.0002,
            },
            {
                "label": "wall_inlet",
                "max_abs_diff_mm": 0.4,
                "zone_count": "FAIL",
                "area_rel_diff": 0.02,
            },
        ],
        "overall": {"max_abs_diff_mm": 0.05},
        "area_max_rel_diff": 0.02,
    }
    steps = {
        "reference": {"return_code": 0, "log": "ref.log"},
        "generate": {"return_code": 0, "log": "gen.log"},
        "compare": {"return_code": 1, "log": "cmp.log"},
    }
    summary = driver.aggregate_case(
        geo_id="P_p100_h30",
        d_p_mm=1.0,
        d_h_mm=0.3,
        steps=steps,
        comparison=comparison,
        face_counts={"wall_spacer_pillar": 4, "wall_spacer_hole": 0},
    )
    assert summary["label_set_match"] is False
    assert summary["max_bbox_abs_diff_mm"] == 0.4
    assert summary["zone_count_match"] is False
    assert summary["area_max_rel_diff"] == 0.02
    assert summary["generated_face_counts"]["wall_spacer_hole"] == 0
    assert driver.case_failed(summary) is True

    empty = driver.aggregate_case(
        geo_id="P_p60_h00",
        d_p_mm=0.6,
        d_h_mm=0.0,
        steps={
            "reference": {"return_code": 0, "log": "a"},
            "generate": {"return_code": 0, "log": "b"},
            "compare": {"return_code": 0, "log": "c"},
        },
        comparison=None,
        face_counts=None,
    )
    assert empty["label_set_match"] is None
    assert empty["max_bbox_abs_diff_mm"] is None
    assert empty["zone_count_match"] is None
    assert empty["area_max_rel_diff"] is None
    assert driver.case_failed(empty) is False


def _cad_payload(wall_area):
    box = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    return {
        "labels": {
            "body": {
                "face_zone_ids": [1, 2],
                "bounding_box_mm": box,
                "face_zone_area": 3.0,
            },
            "wall": {
                "face_zone_ids": [1],
                "bounding_box_mm": box,
                "face_zone_area": wall_area,
            },
        },
        "all_face_zone_ids": [1, 2],
        "overall_bounding_box_mm": box,
    }


def test_d_h_mm_is_required_with_cad_path_and_defaults_without_it():
    probe = load_probe()
    no_args = probe.build_parser().parse_args([])
    assert no_args.cad_path is None
    assert no_args.d_h_mm is None
    assert probe.resolve_d_h_mm(no_args.cad_path, no_args.d_h_mm) == 0.30

    with_cad = probe.build_parser().parse_args(["--cad-path", "C:/tmp/part.pmdb"])
    assert with_cad.d_h_mm is None
    with pytest.raises(ValueError, match="--d-h-mm is required"):
        probe.resolve_d_h_mm(with_cad.cad_path, with_cad.d_h_mm)

    explicit = probe.build_parser().parse_args(
        ["--cad-path", "C:/tmp/part.pmdb", "--d-h-mm", "0"]
    )
    assert probe.resolve_d_h_mm(explicit.cad_path, explicit.d_h_mm) == 0.0

    batch = probe.load_batch_config()
    h00 = probe.expected_config_labels(batch, 0.0)
    h30 = probe.expected_config_labels(batch, 0.30)
    assert "wall_spacer_hole" not in h00
    assert "wall_spacer_hole" in h30
    assert h30 == probe.expected_config_labels(batch, probe.DEFAULT_D_H_MM)


def test_only_rejects_ids_outside_the_nine_design_points():
    driver = load_driver()
    points = driver.design_points(driver.load_batch_config())
    chosen = points[0]["geo_id"]
    assert driver.select_points(points, None) == points
    assert driver.select_points(points, [chosen]) == [points[0]]
    with pytest.raises(ValueError, match="not-a-pillar"):
        driver.select_points(points, [chosen, "not-a-pillar"])

    reference = driver._probe_cmd("C:/cad.dsco", "C:/work/ref", 0.0)
    compare = driver._probe_cmd(
        "C:/cad.pmdb",
        "C:/work/cmp",
        0.15,
        compare_to="C:/work/ref/reference_geometry.json",
    )
    assert reference[reference.index("--d-h-mm") + 1] == "0.0"
    assert compare[compare.index("--d-h-mm") + 1] == "0.15"
    assert "--compare-to" in compare


def test_probe_area_comparison_uses_rtol_and_skips_old_json():
    probe = load_probe()
    reference = _cad_payload(10.0)
    close = probe.compare_reference(_cad_payload(10.005), reference, 1e-3, area_rtol=1e-3)
    assert close["passed"] is True
    assert close["area_max_rel_diff"] == pytest.approx(0.0005)
    far = probe.compare_reference(_cad_payload(10.02), reference, 1e-3, area_rtol=1e-3)
    assert far["passed"] is False
    wall = next(row for row in far["labels"] if row["label"] == "wall")
    assert wall["area"] == "FAIL"

    legacy = _cad_payload(10.0)
    for record in legacy["labels"].values():
        record.pop("face_zone_area")
    current = _cad_payload(10.0)
    for record in current["labels"].values():
        record.pop("face_zone_area")
    unchanged = probe.compare_reference(current, legacy, 1e-3)
    assert "area_max_rel_diff" not in unchanged
    assert unchanged["passed"] is True
