"""Guards for the CP max hotspot diagnostic. Does not launch Fluent."""

import math
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.domain_layout import (
    BUFFER_LENGTH_IN_M,
    BUFFER_LENGTH_OUT_M,
    CELL_LENGTH_X_M,
    CURRENT_EVALUATION_WINDOW,
    DomainLayout,
)

diagnose = load_module(
    "diagnose_cp_max_hotspots_under_test",
    SCRIPTS_DIR / "mfbo" / "diagnose_cp_max_hotspots.py",
)


def _production_layout():
    return DomainLayout(
        1,
        7,
        2,
        CELL_LENGTH_X_M,
        BUFFER_LENGTH_IN_M,
        BUFFER_LENGTH_OUT_M,
    )


def test_data_root_and_source_run_guards():
    with pytest.raises(ValueError, match="production"):
        diagnose.resolve_data_root("C:/ro_data")
    with pytest.raises(ValueError, match="production"):
        diagnose.resolve_data_root("c:/RO_DATA/campaign")
    with pytest.raises(ValueError, match="production"):
        diagnose.resolve_data_root("/mnt/c/ro_data/work")
    with pytest.raises(ValueError, match="production"):
        diagnose.parse_production_run_leaf("/tmp/runs/pillar/geo/mesh/u0p2_p6M")
    identity = diagnose.parse_production_run_leaf(
        "C:/ro_data/runs/sinusoidal/Sin_h10/meshA/u0p2_p6M"
    )
    assert identity["geo_id"] == "Sin_h10"
    assert identity["run_id"] == "u0p2_p6M"
    assert identity["mesh_leaf"].as_posix().endswith(
        "C:/ro_data/meshes/sinusoidal/Sin_h10/meshA"
    )


def test_production_evaluation_window_is_cells_5_to_8():
    cells = diagnose.evaluation_window_cells(
        _production_layout(),
        CURRENT_EVALUATION_WINDOW,
    )
    assert cells == [5, 6, 7, 8]
    spans = diagnose.cell_x_bounds(_production_layout(), cells, 0.0)
    assert spans[5][0] < spans[5][1]
    assert spans[8][1] > spans[5][0]


def test_copy_reuses_identical_tree_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "a.txt").write_text("alpha", encoding="utf-8")
    dest = tmp_path / "dest"
    first = diagnose.copy_or_reuse_tree(source, dest, label="run leaf")
    assert (dest / "nested" / "a.txt").read_text(encoding="utf-8") == "alpha"
    second = diagnose.copy_or_reuse_tree(source, dest, label="run leaf")
    assert second == first
    (source / "nested" / "a.txt").write_text("beta", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not identical"):
        diagnose.copy_or_reuse_tree(source, dest, label="run leaf")
    assert (dest / "nested" / "a.txt").read_text(encoding="utf-8") == "alpha"


def test_fluent_targets_must_be_copies(tmp_path):
    data_root = tmp_path / "data"
    run_leaf = data_root / "runs" / "sinusoidal" / "Sin_h10" / "meshA" / "u0p2_p6M"
    run_leaf.mkdir(parents=True)
    case_file = run_leaf / "Sin_h10_u0p2_p6M_final.cas.h5"
    data_file = run_leaf / "Sin_h10_u0p2_p6M_final.dat.h5"
    case_file.write_bytes(b"cas")
    data_file.write_bytes(b"dat")
    diagnose.assert_fluent_opens_copies(data_root, run_leaf, case_file, data_file)
    with pytest.raises(ValueError, match="C:/ro_data"):
        diagnose.assert_fluent_opens_copies(
            data_root,
            Path("C:/ro_data/runs/sinusoidal/Sin_h10/meshA/u0p2_p6M"),
            case_file,
            data_file,
        )


def test_launch_kwargs_match_post_path_with_two_processors(tmp_path):
    run_config = diagnose.load_run_config()
    kwargs = diagnose.production_post_launch_kwargs(tmp_path, run_config)
    assert kwargs["mode"] == "meshing"
    assert kwargs["dimension"] == 3
    assert kwargs["precision"] == "double"
    assert kwargs["processor_count"] == 2
    assert kwargs["ui_mode"] == "gui"
    assert kwargs["product_version"] == run_config.product_version
    assert kwargs["graphics_driver"] == run_config.graphics_driver
    assert "C:/ro_data" not in kwargs["cwd"]
    with pytest.raises(ValueError, match="C:/ro_data"):
        diagnose.production_post_launch_kwargs(
            Path("C:/ro_data/runs/sinusoidal/Sin_h10/meshA/u0p2_p6M"),
            run_config,
        )


def test_area_quantile_top_faces_and_film_terms():
    values = [10.0, 1800.0, 1.0]
    areas = [1.0, 0.0, 1.0]
    assert diagnose.area_quantile(values, areas, 0.999) == 10.0
    faces = [
        {"cm": 1.0, "area": 1.0},
        {"cm": 1800.0, "area": 0.0},
        {"cm": 9.0, "area": 1.0},
    ]
    top = diagnose.top_faces_by_cm(faces, count=2)
    assert [face["cm"] for face in top] == [1800.0, 9.0]
    argument, exponential = diagnose.film_terms(2.0e-9, 1.0, 2.0e-9)
    assert argument == pytest.approx(1.0)
    assert exponential == pytest.approx(math.e)
    square = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    assert diagnose.polygon_area(square) == pytest.approx(1.0)
    areas_out = diagnose.face_areas(
        square,
        [(0, 1, 2, 3)],
    )
    assert areas_out == pytest.approx([1.0])


def test_cp_definition_metrics_exclude_singular_and_negative_faces():
    b_perm = diagnose.B_PERM_M_PER_S
    c_inlet = diagnose.C_INLET_REF_MOL_PER_M3
    singular = {
        "x": 0.01,
        "y": 0.0,
        "z": 0.0,
        "area": 0.01,
        "cm": 3.0 * c_inlet,
        "jw": 2.0 * b_perm,
        "udm9": 100.0,
    }
    bulk = {
        "x": 0.02,
        "y": 0.0,
        "z": 0.0,
        "area": 1.0,
        "cm": 650.0,
        "jw": 1.0e-5,
        "udm9": 1.05,
    }
    negative = {
        "x": 0.03,
        "y": 0.0,
        "z": 0.0,
        "area": 1.0,
        "cm": 400.0,
        "jw": 1.0e-5,
        "udm9": -2.0,
    }
    zero_area_spike = {
        "x": 0.04,
        "y": 0.0,
        "z": 0.0,
        "area": 0.0,
        "cm": 100.0,
        "jw": 1.0e-5,
        "udm9": 9000.0,
    }
    faces = [bulk, singular, negative, zero_area_spike]
    perm = diagnose.cp_perm_mol_per_m3(singular["cm"], singular["jw"])
    assert perm == pytest.approx(c_inlet)
    metrics = diagnose.cp_definition_metrics(faces, c_b=610.0)
    assert metrics["udm9_facet_max"] == 9000.0
    assert metrics["near_singular_count"] == 1
    assert metrics["near_singular_area"] == pytest.approx(0.01)
    assert metrics["negative_udm9_count"] == 1
    assert metrics["negative_udm9_area"] == pytest.approx(1.0)
    assert metrics["udm9_area_avg_excluding"] == pytest.approx(1.05)
    assert metrics["udm9_area_avg_change"] == pytest.approx(
        metrics["udm9_area_avg_excluding"] - metrics["udm9_area_avg"]
    )
    assert metrics["robust_q99"] is not None
    assert metrics["robust_q999"] is not None
    ranked = diagnose.top_faces_by_udm9(metrics["annotated_faces"], count=2)
    assert ranked[0]["udm9"] == 9000.0
    assert ranked[0]["cm"] == 100.0
    singular_face = next(
        face for face in metrics["annotated_faces"] if face["udm9"] == 100.0
    )
    assert singular_face["jw_over_b_perm"] == pytest.approx(2.0)
    assert singular_face["denominator"] == pytest.approx(0.0)
    text = diagnose.format_definition_summary(
        [
            {
                "cell": 5,
                "surface": "combined",
                **{
                    key: metrics[key]
                    for key in (
                        "udm9_area_avg",
                        "udm9_facet_max",
                        "near_singular_count",
                        "near_singular_area",
                        "negative_udm9_count",
                        "udm9_area_avg_excluding",
                        "udm9_area_avg_change",
                        "robust_q99",
                        "robust_q999",
                        "c_b_midplane_mol_m3",
                    )
                },
            }
        ]
    )
    assert "cell 5 combined" in text
    assert "udm9_max=9000" in text


def test_definition_check_uses_existing_copy_only(monkeypatch, tmp_path):
    identity = diagnose.parse_production_run_leaf(
        "C:/ro_data/runs/sinusoidal/Sin_h10/meshA/u0p2_p6M"
    )
    data_root = tmp_path / "data"
    run_leaf, mesh_leaf = diagnose.destination_leaves(data_root, identity)
    mesh_leaf.mkdir(parents=True)
    (mesh_leaf / "mesh.msh.h5").write_bytes(b"mesh")
    run_leaf.mkdir(parents=True)
    (run_leaf / "Sin_h10_u0p2_p6M_final.cas.h5").write_bytes(b"cas")
    (run_leaf / "Sin_h10_u0p2_p6M_final.dat.h5").write_bytes(b"dat")

    def fail_copy(*_args, **_kwargs):
        raise AssertionError("definition check must not copy C:/ro_data")

    monkeypatch.setattr(diagnose, "copy_source_leaves", fail_copy)
    monkeypatch.setattr(
        diagnose,
        "launch_definition_check",
        lambda *args, **kwargs: "checked",
    )
    assert (
        diagnose.main(
            [
                "--source-run",
                "C:/ro_data/runs/sinusoidal/Sin_h10/meshA/u0p2_p6M",
                "--data-root",
                str(data_root),
                "--cp-definition-check",
            ]
        )
        == 0
    )
    with pytest.raises(FileNotFoundError, match="does not copy"):
        diagnose.require_existing_copies(tmp_path / "empty", identity)


def test_report_records_facetmax_path_and_null_quantile(tmp_path):
    payload = {
        "cp_canon_max_path": diagnose.CP_CANON_MAX_PATH,
        "evaluation_cells": [5, 6, 7, 8],
    }
    rows = [
        {
            "surface": "wall_top_mem",
            "cell": 5,
            "rank": 1,
            "x": 0.01,
            "y": 0.0,
            "z": 0.0,
            "area": 1.0e-8,
            "cm": 12.0,
            "concentration_nacl": 0.04,
            "jw": 1.0e-5,
            "y1": 1.0e-5,
            "jw_y1_over_d_salt": 0.05,
            "exp_jw_y1_over_d_salt": math.exp(0.05),
            "spacer_distance_m": None,
            "spacer_distance_note": "not_computable: no wall_spacer zones",
            "cm_area_quantile_999": 11.0,
            "concentration_nacl_area_quantile_999": 0.035,
        }
    ]
    directory = diagnose.output_directory(tmp_path, "Sin_h10", "u0p2_p6M")
    json_path, csv_path = diagnose.write_hotspot_report(directory, payload, rows)
    text = json_path.read_text(encoding="utf-8")
    assert "udm-9" in text
    assert "surface-facetmax" in text
    assert '"quantile": null' in text
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "cm" in header.split(",")
    assert "exp_jw_y1_over_d_salt" in header.split(",")
    with pytest.raises(ValueError, match="C:/ro_data"):
        diagnose.output_directory(Path("C:/ro_data"), "Sin_h10", "u0p2_p6M")
