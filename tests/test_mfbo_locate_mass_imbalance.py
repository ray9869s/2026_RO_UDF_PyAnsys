"""Mass-imbalance location without Fluent."""

from __future__ import annotations

import csv
import json

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.domain_layout import DomainLayout

locate = load_module(
    "locate_mass_imbalance_under_test",
    SCRIPTS_DIR / "mfbo" / "locate_mass_imbalance.py",
)
job_queue = load_module(
    "job_queue_for_mass_imbalance",
    SCRIPTS_DIR / "mfbo" / "job_queue.py",
)

MESH = "max085_min006_cpg5_bl4_peel2"
FINE = "max045_min006_cpg5_bl12s4_peel2"


def _layout():
    return DomainLayout(
        n_buffer_in=1,
        n_active=2,
        n_buffer_out=2,
        cell_length_x_m=0.01,
        buffer_length_in_m=0.01,
        buffer_length_out_m=0.02,
    )


def _cell(mass, x, volume=1.0e-12):
    return {
        "imbalance": mass,
        "abs_imbalance": abs(mass),
        "volume_m3": volume,
        "x_m": x,
        "y_m": 0.001,
        "z_m": 0.0004,
    }


def test_unit_cell_labels_follow_the_layout_spans():
    spans = _layout().spans(0.0)
    assert locate.unit_cell_label(0.0, spans, 0.0) == "buffer_in_1"
    assert locate.unit_cell_label(0.01, spans, 0.0) == "active_1"
    assert locate.unit_cell_label(0.025, spans, 0.0) == "active_2"
    assert locate.unit_cell_label(0.035, spans, 0.0) == "buffer_out_1"
    assert locate.unit_cell_label(0.05, spans, 0.0) == "buffer_out_2"
    assert locate.unit_cell_label(-1.0e-4, spans, 0.0) == "outside"
    assert locate.unit_cell_label(-1.0e-7, spans, 1.0e-6) == "buffer_in_1"


def test_top_cells_histogram_and_concentration():
    spans = _layout().spans(0.0)
    cells = [
        _cell(4.0, 0.015),
        _cell(-3.0, 0.016),
        _cell(1.0, 0.045),
        _cell(10.0, -1.0),
    ]
    analysis = locate.analyze_cells(cells, spans, top_n=2, tolerance_m=0.0)
    assert [row["unit_cell"] for row in analysis["top"]] == ["outside", "active_1"]
    assert analysis["top"][0]["abs_imbalance"] == 10.0
    assert analysis["top"][0]["imbalance"] == 10.0
    by_label = {row["unit_cell"]: row for row in analysis["histogram"]}
    assert by_label["active_1"]["cell_count"] == 2
    assert by_label["active_1"]["sum_abs_imbalance"] == pytest.approx(7.0)
    assert by_label["active_1"]["max_abs_imbalance"] == pytest.approx(4.0)
    assert by_label["active_2"]["cell_count"] == 0
    assert by_label["buffer_out_2"]["sum_abs_imbalance"] == pytest.approx(1.0)
    assert by_label["outside"]["sum_abs_imbalance"] == pytest.approx(10.0)
    assert analysis["total_abs_imbalance"] == pytest.approx(18.0)


def test_concentration_shares_match_a_hand_count():
    cells = [_cell(900.0, 0.01)]
    cells.extend(_cell(10.0, 0.01) for _ in range(9))
    cells.extend(_cell(0.0, 0.01) for _ in range(990))
    top_tenth = locate.concentration_fraction(cells, 0.001)
    top_percent = locate.concentration_fraction(cells, 0.01)
    assert top_tenth["cell_count"] == 1
    assert top_tenth["share"] == pytest.approx(900.0 / 990.0)
    assert top_percent["cell_count"] == 10
    assert top_percent["share"] == pytest.approx(1.0)
    zeros = [_cell(0.0, 0.01) for _ in range(20)]
    assert locate.concentration_fraction(zeros, 0.01)["share"] == 0.0


def test_missing_field_name_is_refused():
    with pytest.raises(RuntimeError, match="mass-imbalance"):
        locate.require_scalar_field(["pressure", "cell-volume"], "mass-imbalance")
    assert (
        locate.require_scalar_field(
            ["mass-imbalance", "cell-volume"],
            "mass-imbalance",
        )
        == "mass-imbalance"
    )


def test_field_lengths_must_match():
    with pytest.raises(ValueError, match="Field lengths differ"):
        locate.assemble_cells([1.0], [1.0], [0.0, 1.0], [0.0], [0.0])


def test_report_files_quote_the_field_and_the_shares(tmp_path):
    spans = _layout().spans(0.0)
    cells = [_cell(2.0, 0.015), _cell(-8.0, 0.005)]
    analysis = locate.analyze_cells(cells, spans, top_n=1, tolerance_m=0.0)
    meta = {
        "role": "stalled",
        "source": "C:/ro_data/runs/pillar/P_p100_h00/" + MESH + "/u0p3_p6M",
        "run_leaf": tmp_path / "copy",
    }
    markdown, top_path, histogram_path, summary_path = locate.write_analysis(
        tmp_path / "reports",
        meta,
        analysis,
    )
    text = markdown.read_text(encoding="utf-8")
    assert "mass-imbalance" in text
    assert "Table 42.17" in text
    assert "buffer_in_1" in text
    with top_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["unit_cell"] == "buffer_in_1"
    assert rows[0]["rank"] == "1"
    assert float(rows[0]["abs_imbalance"]) == pytest.approx(8.0)
    with histogram_path.open(encoding="utf-8", newline="") as handle:
        histogram = list(csv.DictReader(handle))
    assert histogram[0]["unit_cell"] == "buffer_in_1"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["field"] == "mass-imbalance"
    assert payload["fractions"][0]["share"] == pytest.approx(0.8)
    with pytest.raises(ValueError, match="C:/ro_data"):
        locate.write_analysis("C:/ro_data/reports", meta, analysis)


def test_production_root_is_refused_and_mfbo_is_not():
    with pytest.raises(ValueError, match="C:/ro_data"):
        locate.resolve_data_root("C:/ro_data")
    with pytest.raises(ValueError, match="C:/ro_data"):
        locate.resolve_data_root("C:/ro_data/runs")
    assert locate.resolve_data_root("C:/ro_data_mfbo/mass_imbalance") == (
        "C:/ro_data_mfbo/mass_imbalance"
    )


def test_copy_reuses_a_match_and_refuses_production(tmp_path):
    source = (
        tmp_path
        / "meshstudy"
        / "runs"
        / "pillar"
        / "P_p100_h00"
        / FINE
        / "u0p3_p6M"
    )
    source.mkdir(parents=True)
    (source / "P_p100_h00_u0p3_p6M_final.cas.h5").write_bytes(b"case")
    (source / "P_p100_h00_u0p3_p6M_final.dat.h5").write_bytes(b"data")
    mesh = tmp_path / "meshstudy" / "meshes" / "pillar" / "P_p100_h00" / FINE
    mesh.mkdir(parents=True)
    (mesh / "manifest.json").write_text("{}\n", encoding="utf-8")
    prepared = locate.prepare_copy(source, tmp_path / "work")
    assert prepared["case_file"].is_file()
    assert prepared["mesh_manifest"].is_file()
    assert not (tmp_path / "work" / "meshes").joinpath(
        "pillar", "P_p100_h00", FINE, "mesh.msh.h5"
    ).exists()
    again = locate.prepare_copy(source, tmp_path / "work")
    assert again["run_leaf"] == prepared["run_leaf"]
    (prepared["run_leaf"] / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="sha256"):
        locate.prepare_copy(source, tmp_path / "work")
    with pytest.raises(ValueError, match="C:/ro_data"):
        locate.copy_or_reuse_tree(source, "C:/ro_data/runs/copied", label="run leaf")


def test_emit_queue_lists_the_study_and_the_control(tmp_path):
    path = tmp_path / "mass_imbalance_jobs.json"
    assert locate.main(["--emit-queue", str(path), "--top", "50"]) == 0
    jobs = job_queue.load_queue(path)
    assert [job["id"] for job in jobs] == [
        f"stalled-P_p100_h00-{FINE}-u0p3_p6M",
        f"stalled-P_p100_h00-{MESH}-u0p2_p6M",
        f"stalled-D0817_a30-{MESH}-u0p3_p6M",
        f"control-P_p100_h30-{MESH}-u0p3_p6M",
    ]
    sources = [job["argv"][job["argv"].index("--source") + 1] for job in jobs]
    assert sources[0].startswith("C:/ro_data_mfbo/meshstudy/runs/pillar/P_p100_h00/")
    assert sources[1:] == [
        f"C:/ro_data/runs/pillar/P_p100_h00/{MESH}/u0p2_p6M",
        f"C:/ro_data/runs/diamond/D0817_a30/{MESH}/u0p3_p6M",
        f"C:/ro_data/runs/pillar/P_p100_h30/{MESH}/u0p3_p6M",
    ]
    for job in jobs:
        root = job["argv"][job["argv"].index("--data-root") + 1]
        assert root == "C:/ro_data_mfbo/mass_imbalance"
        assert job["env"]["RO_DATA_ROOT"] == root
        assert "C:/ro_data/" not in root
        assert "--source" in job["argv"]
    assert jobs[-1]["argv"][jobs[-1]["argv"].index("--role") + 1] == "control"
