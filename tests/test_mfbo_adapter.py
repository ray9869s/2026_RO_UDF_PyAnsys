"""3D pillar adapter. Drivers are fakes; nothing here launches Fluent."""

from __future__ import annotations

import ast
import csv
import json
import math
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.mfbo_adapter import (
    EXAMPLE_FIDELITY_TABLE,
    LF_MESH_ID,
    PillarDesign,
    evaluate,
    lmh_module_area,
    mesh_id_for_settings,
    opening_gap_mm,
    resolve_data_root,
)

RUN_ID = "u0p2_p6M"
WIDE = PillarDesign(d_p_mm=0.8, d_h_mm=0.0, d_f_mm=0.4)
SLIVER = PillarDesign(d_p_mm=0.8, d_h_mm=0.2, d_f_mm=0.4)


class RecordingDrivers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, *, design, geo_id, out_dir):
        self.calls.append("generate")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{geo_id}.pmdb").write_bytes(b"pmdb")
        _write_json(out_dir / f"{geo_id}_meta.json", {"status": "success"})

    def mesh(self, *, design, geo_id, mesh_id, mesh_settings, mesh_dir):
        self.calls.append(f"mesh:{mesh_id}")
        mesh_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            mesh_dir / "mesh_run_record.json",
            {"status": "SUCCESS", "wall_time_seconds": 3.0},
        )
        _write_json(mesh_dir / "manifest.json", _mesh_manifest())

    def solve(self, *, design, geo_id, mesh_id, run_id, run_dir):
        self.calls.append(f"solve:{run_id}")
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "manifest.json", _run_manifest())

    def extract(self, *, design, geo_id, mesh_id, run_id, run_dir):
        self.calls.append("extract")
        _write_summary(run_dir)


def _drivers() -> RecordingDrivers:
    return RecordingDrivers()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mesh_manifest() -> dict:
    return {
        "cell_count": 1200,
        "n_active_cells": 7,
        "cell_length_x_m": 0.003465,
        "periodic_shift_y_m": 0.003465,
    }


def _run_manifest(**overrides) -> dict:
    payload = {
        "stop_reason": "qoi_converged",
        "convergence_quality": "PASS",
        "solver_wall_time_s": 10.0,
        "extraction_wall_time_s": 2.0,
        "solver_time_s": 9.5,
    }
    payload.update(overrides)
    return payload


def _write_summary(run_dir: Path, **overrides) -> None:
    row = {
        "lmh_mass_balance": "25.0",
        "area_mem": "1.0e-5",
        "pressure_drop_spacer_per_m": "30000",
        "cpc_window_avg_flux": "1.08",
        "cp_q999_window_flux": "1.20",
        "cp_canon_window_avg": "1.05",
    }
    row.update(overrides)
    path = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _write_success_tree(root: Path, design: PillarDesign = WIDE) -> tuple[str, Path]:
    from ro.geometry_registry import format_mfbo_pillar_geo_id

    geo_id = format_mfbo_pillar_geo_id(design.d_p_mm, design.d_h_mm, design.d_f_mm)
    geo_dir = root / "geometries" / "pillar" / geo_id
    mesh_dir = root / "meshes" / "pillar" / geo_id / LF_MESH_ID
    run_dir = root / "runs" / "pillar" / geo_id / LF_MESH_ID / RUN_ID
    geo_dir.mkdir(parents=True)
    (geo_dir / f"{geo_id}.pmdb").write_bytes(b"pmdb")
    _write_json(geo_dir / f"{geo_id}_meta.json", {"status": "success"})
    _write_json(
        mesh_dir / "mesh_run_record.json",
        {"status": "SUCCESS", "wall_time_seconds": 3.0},
    )
    _write_json(mesh_dir / "manifest.json", _mesh_manifest())
    _write_json(run_dir / "manifest.json", _run_manifest())
    _write_summary(run_dir)
    return geo_id, run_dir


def _evaluate(root: Path, design, fidelity, drivers, table=None):
    return evaluate(
        design,
        fidelity,
        run_id=RUN_ID,
        data_root=root,
        fidelity_table=EXAMPLE_FIDELITY_TABLE if table is None else table,
        drivers=drivers,
    )


def test_example_table_is_lf_only():
    assert list(EXAMPLE_FIDELITY_TABLE) == ["LF"]
    assert mesh_id_for_settings(EXAMPLE_FIDELITY_TABLE["LF"]) == LF_MESH_ID


def test_missing_fidelity_raises_and_launches_nothing(tmp_path):
    drivers = _drivers()
    with pytest.raises(KeyError, match="HF"):
        _evaluate(tmp_path, WIDE, "HF", drivers)
    assert drivers.calls == []


def test_data_root_refuses_production(tmp_path):
    for value in (
        "C:/ro_data",
        "C:/ro_data/studies",
        r"C:\ro_data\geometries",
        "c:/RO_DATA/meshes/pillar",
        "/mnt/c/ro_data",
        "/mnt/c/ro_data/runs",
    ):
        with pytest.raises(ValueError, match="production data root"):
            resolve_data_root(value)
    assert resolve_data_root(tmp_path) == tmp_path.resolve()


def test_opening_gap_matches_the_surface_formula():
    radius = 0.4
    expected = radius * (
        math.pi / 4.0 - math.asin(0.2 / radius) - math.asin(0.1 / radius)
    )
    assert opening_gap_mm(0.8, 0.2, 0.4) == pytest.approx(expected)
    assert expected * 1.0e3 == pytest.approx(3.65, abs=0.01)
    no_bore = radius * (math.pi / 4.0 - math.asin(0.2 / radius))
    assert opening_gap_mm(0.8, 0.0, 0.4) == pytest.approx(no_bore)


def test_opening_gap_sliver_launches_nothing(tmp_path):
    drivers = _drivers()
    record = _evaluate(tmp_path, SLIVER, "LF", drivers)
    assert drivers.calls == []
    assert record["status"] == "invalid"
    assert record["failure_reason"] == "opening_gap_sliver"
    assert record["lmh"] is None
    assert record["leaf_path"] is None
    assert not (tmp_path / "geometries").exists()


def test_call_order_when_leaves_are_absent(tmp_path):
    drivers = _drivers()
    record = _evaluate(tmp_path, WIDE, "LF", drivers)
    assert drivers.calls == [
        "generate",
        f"mesh:{LF_MESH_ID}",
        f"solve:{RUN_ID}",
        "extract",
    ]
    assert record["status"] == "valid"
    assert record["geo_id"] == "MFP_d0800_h0000_f0400"
    assert record["mesh_id"] == LF_MESH_ID


def test_reuse_skips_drivers_when_manifests_record_success(tmp_path):
    _write_success_tree(tmp_path)
    drivers = _drivers()
    record = _evaluate(tmp_path, WIDE, "LF", drivers)
    assert drivers.calls == []
    assert record["status"] == "valid"
    assert record["lmh"] == pytest.approx(25.0)


def test_existing_failed_leaf_is_not_rerun(tmp_path):
    from ro.geometry_registry import format_mfbo_pillar_geo_id

    geo_id = format_mfbo_pillar_geo_id(WIDE.d_p_mm, WIDE.d_h_mm, WIDE.d_f_mm)
    geo_dir = tmp_path / "geometries" / "pillar" / geo_id
    geo_dir.mkdir(parents=True)
    (geo_dir / f"{geo_id}.pmdb").write_bytes(b"pmdb")
    _write_json(geo_dir / f"{geo_id}_meta.json", {"status": "success"})
    mesh_dir = tmp_path / "meshes" / "pillar" / geo_id / LF_MESH_ID
    mesh_dir.mkdir(parents=True)
    _write_json(mesh_dir / "mesh_run_record.json", {"status": "FAILED"})
    drivers = _drivers()
    record = _evaluate(tmp_path, WIDE, "LF", drivers)
    assert drivers.calls == []
    assert record["status"] == "execution_failed"
    assert record["failure_reason"] == "leaf_not_successful"
    assert record["leaf_path"] == str(mesh_dir)
    assert record["lmh"] is None


def test_geometry_without_manifest_is_execution_failed(tmp_path):
    from ro.geometry_registry import format_mfbo_pillar_geo_id

    geo_id = format_mfbo_pillar_geo_id(WIDE.d_p_mm, WIDE.d_h_mm, WIDE.d_f_mm)
    geo_dir = tmp_path / "geometries" / "pillar" / geo_id
    geo_dir.mkdir(parents=True)
    drivers = _drivers()
    record = _evaluate(tmp_path, WIDE, "LF", drivers)
    assert drivers.calls == []
    assert record["status"] == "execution_failed"
    assert record["leaf_path"] == str(geo_dir)


def test_field_mapping_uses_summary_columns_and_module_area(tmp_path):
    _write_success_tree(tmp_path)
    record = _evaluate(tmp_path, WIDE, "LF", _drivers())
    expected = lmh_module_area(25.0, 1.0e-5, 7, 0.003465, 0.003465)
    assert record["lmh"] == pytest.approx(25.0)
    assert record["lmh_module_area"] == pytest.approx(expected)
    assert record["pressure_drop_per_length_pa_per_m"] == pytest.approx(30000.0)
    assert record["cp_average"] == pytest.approx(1.08)
    assert record["cp_q999"] == pytest.approx(1.20)
    assert record["cp_canon_window_avg"] == pytest.approx(1.05)
    assert record["cp_average"] != pytest.approx(record["cp_canon_window_avg"])
    assert record["cell_count"] == pytest.approx(1200)
    assert record["mesh_wall_time_s"] == pytest.approx(3.0)
    assert record["solver_wall_time_s"] == pytest.approx(10.0)
    assert record["extraction_wall_time_s"] == pytest.approx(2.0)
    assert record["solver_time_s"] == pytest.approx(9.5)
    assert record["failure_reason"] is None


def test_module_area_matches_summarize_results():
    summary = load_module(
        "summarize_results_for_adapter",
        SCRIPTS_DIR / "mfbo" / "summarize_results.py",
    )
    assert lmh_module_area(25.0, 1.0e-5, 7, 0.003465, 0.003465) == pytest.approx(
        summary.lmh_per_module_area(25.0, 1.0e-5, 7, 0.003465, 0.003465)
    )


def test_convergence_fail_diverged_keeps_measured_lmh(tmp_path):
    _write_success_tree(tmp_path)
    geo_id = "MFP_d0800_h0000_f0400"
    run_dir = tmp_path / "runs" / "pillar" / geo_id / LF_MESH_ID / RUN_ID
    _write_json(
        run_dir / "manifest.json",
        _run_manifest(stop_reason="diverged", convergence_quality="FAIL"),
    )
    _write_summary(run_dir, lmh_mass_balance="12.5")
    record = _evaluate(tmp_path, WIDE, "LF", _drivers())
    assert record["status"] == "diverged"
    assert record["failure_reason"] == "diverged"
    assert record["lmh"] == pytest.approx(12.5)
    assert record["lmh"] != 0.0


def test_convergence_fail_without_divergence_is_invalid(tmp_path):
    _write_success_tree(tmp_path)
    geo_id = "MFP_d0800_h0000_f0400"
    run_dir = tmp_path / "runs" / "pillar" / geo_id / LF_MESH_ID / RUN_ID
    _write_json(
        run_dir / "manifest.json",
        _run_manifest(
            convergence_quality="FAIL",
            convergence_quality_failures=["continuity_final"],
        ),
    )
    _write_summary(run_dir, lmh_mass_balance="")
    record = _evaluate(tmp_path, WIDE, "LF", _drivers())
    assert record["status"] == "invalid"
    assert record["failure_reason"] == "continuity_final"
    assert record["lmh"] is None


def test_adapter_source_does_not_import_fluent():
    source = (REPO_ROOT / "src" / "ro" / "mfbo_adapter.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported == [
        "__future__",
        "csv",
        "json",
        "math",
        "collections.abc",
        "dataclasses",
        "pathlib",
        "typing",
        "ro.campaign_matrix",
        "ro.geometry_registry",
        "ro.paths",
    ]
    joined = " ".join(imported).casefold()
    assert "fluent" not in joined
    assert "ansys" not in joined
