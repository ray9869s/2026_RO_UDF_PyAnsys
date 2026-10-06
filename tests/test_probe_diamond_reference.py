"""Diamond reference-CAD dump. Does not launch Fluent or Discovery."""

from __future__ import annotations

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.campaign_geometry import geometry_parameters_for_geo_id


def load_probe():
    return load_module(
        "probe_diamond_reference_under_test",
        SCRIPTS_DIR / "_probe_reference_geometry.py",
    )


def _extracted():
    box = [[0.0, -1.0, -0.385], [10.0, 1.0, 0.385]]
    return {
        "objects": [{"name": "fluid", "labels": ["inlet", "wall_spacer"]}],
        "bodies": [
            {
                "name": "fluid",
                "regions": ["fluid"],
                "volumes": [12.5],
                "error": None,
            }
        ],
        "labels": {
            "inlet": {
                "face_zone_ids": [1],
                "bounding_box_mm": box,
                "face_zone_area": 2.5,
                "face_count": 4,
            },
            "wall_spacer": {
                "face_zone_ids": [2, 3],
                "bounding_box_mm": box,
                "face_zone_area": 8.0,
                "face_count": 40,
            },
        },
        "overall_bounding_box_mm": box,
    }


def test_production_diamond_cases_are_the_nine_campaign_files():
    probe = load_probe()
    cases = probe.production_diamond_cases()
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
        geometry_parameters_for_geo_id(case["geo_id"])


def test_reference_dump_reports_face_counts_areas_and_volume():
    probe = load_probe()
    dump = probe.reference_dump(_extracted())
    assert dump["body_count"] == 1
    assert dump["bodies"][0]["volumes"] == [12.5]
    assert dump["labels"]["wall_spacer"]["face_zone_count"] == 2
    assert dump["labels"]["wall_spacer"]["face_count"] == 40
    assert dump["labels"]["inlet"]["area"] == 2.5
    text = probe.format_diamond_case_report("D2450_a45", dump)
    assert "body_count: 1" in text
    assert "volume_mm3=12.5" in text
    assert "wall_spacer" in text
    assert "40" in text


def test_unread_volume_is_marked_and_not_invented():
    probe = load_probe()
    extracted = _extracted()
    extracted["bodies"] = [
        {
            "name": "fluid",
            "regions": None,
            "volumes": None,
            "error": "AttributeError: get_region_volume",
        }
    ]
    dump = probe.reference_dump(extracted)
    text = probe.format_diamond_case_report("D2450_a45", dump)
    assert "VOLUME_UNREAD" in text
    assert "12.5" not in text
    with pytest.raises(TypeError, match="volume"):
        probe._require_volume("12.5", "fluid")


def test_diamond_cli_does_not_launch_and_refuses_the_cad_tree(tmp_path):
    probe = load_probe()
    mixed = probe.build_parser().parse_args(
        ["--family", "diamond", "--cad-path", "C:/tmp/part.dsco"]
    )
    with pytest.raises(ValueError, match="does not take"):
        probe.run_diamond_reference(mixed)

    reserved = probe.build_parser().parse_args(
        ["--family", "diamond", "--work-dir", r"C:\ro_data\geometries\diamond"]
    )
    with pytest.raises(ValueError, match="reserved for the manual CAD"):
        probe.run_diamond_reference(reserved)

    missing = probe.build_parser().parse_args(
        ["--family", "diamond", "--work-dir", str(tmp_path)]
    )
    with pytest.raises(FileNotFoundError, match="Diamond reference CAD is missing"):
        probe.run_diamond_reference(missing)
