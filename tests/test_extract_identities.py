"""R-12 extract identities over wide CSV columns. No Fluent, no required schema."""

from __future__ import annotations

import csv
import io

import pytest

from helpers import SCRIPTS_DIR, load_module
from ro.fluent_report_helpers import (
    DIAGNOSTIC_FLUX_DECOMPOSITION_SUMMARY_METRICS,
    LOAD_BEARING_SUMMARY_METRICS,
    active_cell_numbers_from_counts,
    area_mem_udm_identity_applies,
    area_mem_udm_identity_block_reason,
    csv_flux_three_key_applies,
    csv_flux_three_key_block_reason,
    require_extract_identities,
    spacer_dp_active_cell_sum_applies,
    spacer_dp_active_cell_sum_block_reason,
)
from ro.manifest import write_run_manifest
from ro.paths import run_dir
from test_manifest import FAMILY, GEO_ID, MESH_ID, RUN_ID, run_payload


# REF_empty u0p2_p6M (this session).
REF_AREA_MEM = 1.680871416727065e-4
REF_UDM_AREA_SUM = 1.680871416727068e-4
REF_M_IN = 5.326494756117325e-4
REF_M_IN_MASS_SOURCE = -1.120921058109091e-6
REF_M_IN_WITH_SOURCES = 5.315285545536234e-4

ACTIVE_CELLS_1_PLUS_7 = list(range(2, 9))


def _load_report():
    return load_module(
        "report_extract_identities_under_test",
        SCRIPTS_DIR / "report_extract_identities.py",
    )


def test_flux_and_area_keys_are_not_new_required_schema_fields():
    assert "m_in_mass_source" not in LOAD_BEARING_SUMMARY_METRICS
    assert "m_out_mass_source" not in LOAD_BEARING_SUMMARY_METRICS
    assert "m_in_with_sources" not in LOAD_BEARING_SUMMARY_METRICS
    assert DIAGNOSTIC_FLUX_DECOMPOSITION_SUMMARY_METRICS == (
        "m_in_with_sources",
        "m_out_with_sources",
        "m_in_mass_source",
        "m_out_mass_source",
    )
    assert "area_mem" in LOAD_BEARING_SUMMARY_METRICS
    assert "pp_udm_area_sum" in LOAD_BEARING_SUMMARY_METRICS


def test_area_mem_udm_ref_empty_fixture_passes():
    record = {"area_mem": REF_AREA_MEM, "pp_udm_area_sum": REF_UDM_AREA_SUM}
    assert area_mem_udm_identity_applies(record)
    assert area_mem_udm_identity_block_reason(record) is None


def test_area_mem_udm_measured_1e15_is_below_1e9():
    relative = abs(REF_AREA_MEM - REF_UDM_AREA_SUM) / REF_AREA_MEM
    assert relative == pytest.approx(1.8e-15, rel=0.2)
    assert relative < 1e-9


def test_area_mem_udm_rejects_mismatch():
    record = {"area_mem": REF_AREA_MEM, "pp_udm_area_sum": REF_AREA_MEM * 1.001}
    reason = area_mem_udm_identity_block_reason(record)
    assert reason is not None
    with pytest.raises(RuntimeError, match="area_mem"):
        require_extract_identities(record, active_cell_numbers=ACTIVE_CELLS_1_PLUS_7)


def test_area_mem_udm_missing_key_is_not_a_reject():
    record = {"area_mem": REF_AREA_MEM}
    assert not area_mem_udm_identity_applies(record)
    assert area_mem_udm_identity_block_reason(record) is None


def test_flux_three_key_ref_empty_fixture_passes():
    record = {
        "m_in": REF_M_IN,
        "m_in_mass_source": REF_M_IN_MASS_SOURCE,
        "m_in_with_sources": REF_M_IN_WITH_SOURCES,
        "m_out": REF_M_IN,
        "m_out_mass_source": REF_M_IN_MASS_SOURCE,
        "m_out_with_sources": REF_M_IN_WITH_SOURCES,
    }
    assert csv_flux_three_key_applies(record, side="in")
    assert csv_flux_three_key_block_reason(record, side="in") is None
    assert csv_flux_three_key_block_reason(record, side="out") is None


def test_flux_three_key_missing_mass_source_is_na_not_zero():
    record = {
        "m_in": REF_M_IN,
        "m_in_with_sources": REF_M_IN_WITH_SOURCES,
    }
    assert not csv_flux_three_key_applies(record, side="in")
    assert csv_flux_three_key_block_reason(record, side="in") is None
    require_extract_identities(record, active_cell_numbers=ACTIVE_CELLS_1_PLUS_7)


def test_flux_three_key_rejects_mismatch():
    record = {
        "m_in": REF_M_IN,
        "m_in_mass_source": REF_M_IN_MASS_SOURCE,
        "m_in_with_sources": REF_M_IN,
    }
    reason = csv_flux_three_key_block_reason(record, side="in")
    assert reason is not None
    with pytest.raises(RuntimeError, match="m_in_with_sources"):
        require_extract_identities(record, active_cell_numbers=[])


def test_spacer_dp_exact_sum_passes():
    record = {
        "pressure_drop_spacer": 700.0,
        "pp_pressure_drop_cell_2": 100.0,
        "pp_pressure_drop_cell_3": 100.0,
        "pp_pressure_drop_cell_4": 100.0,
        "pp_pressure_drop_cell_5": 100.0,
        "pp_pressure_drop_cell_6": 100.0,
        "pp_pressure_drop_cell_7": 100.0,
        "pp_pressure_drop_cell_8": 100.0,
    }
    assert spacer_dp_active_cell_sum_applies(record, ACTIVE_CELLS_1_PLUS_7)
    assert (
        spacer_dp_active_cell_sum_block_reason(record, ACTIVE_CELLS_1_PLUS_7)
        is None
    )


def test_spacer_dp_discretisation_gap_within_tol_passes():
    record = {
        "pressure_drop_spacer": 700.00005,
        "pp_pressure_drop_cell_2": 100.0,
        "pp_pressure_drop_cell_3": 100.0,
        "pp_pressure_drop_cell_4": 100.0,
        "pp_pressure_drop_cell_5": 100.0,
        "pp_pressure_drop_cell_6": 100.0,
        "pp_pressure_drop_cell_7": 100.0,
        "pp_pressure_drop_cell_8": 100.0,
    }
    assert (
        spacer_dp_active_cell_sum_block_reason(record, ACTIVE_CELLS_1_PLUS_7)
        is None
    )


def test_spacer_dp_rejects_large_gap():
    record = {
        "pressure_drop_spacer": 650.0,
        "pp_pressure_drop_cell_2": 100.0,
        "pp_pressure_drop_cell_3": 100.0,
        "pp_pressure_drop_cell_4": 100.0,
        "pp_pressure_drop_cell_5": 100.0,
        "pp_pressure_drop_cell_6": 100.0,
        "pp_pressure_drop_cell_7": 100.0,
        "pp_pressure_drop_cell_8": 100.0,
    }
    reason = spacer_dp_active_cell_sum_block_reason(
        record, ACTIVE_CELLS_1_PLUS_7
    )
    assert reason is not None


def test_spacer_dp_missing_cell_column_is_na():
    record = {
        "pressure_drop_spacer": 700.0,
        "pp_pressure_drop_cell_2": 100.0,
    }
    assert not spacer_dp_active_cell_sum_applies(record, ACTIVE_CELLS_1_PLUS_7)
    assert (
        spacer_dp_active_cell_sum_block_reason(record, ACTIVE_CELLS_1_PLUS_7)
        is None
    )


def test_active_cell_numbers_from_counts_match_layout():
    assert active_cell_numbers_from_counts(1, 7) == ACTIVE_CELLS_1_PLUS_7


def test_extract_identities_precede_is_after_csv_write():
    source = (SCRIPTS_DIR / "pyfluent_report_extract.py").read_text(
        encoding="utf-8"
    )
    csv_write = source.index("summary_wide_df.to_csv(")
    identities = source.index("require_extract_identities(")
    load_bearing = source.index("require_load_bearing_summary_columns(")
    assert csv_write < load_bearing < identities


def test_report_marks_missing_flux_as_na(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    payload = run_payload()
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    reports = run_directory / "post" / "reports"
    reports.mkdir(parents=True)
    write_run_manifest(run_directory, payload)
    csv_path = reports / "summary_metrics_wide.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["area_mem", "pp_udm_area_sum", "pressure_drop_spacer"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "area_mem": REF_AREA_MEM,
                "pp_udm_area_sum": REF_UDM_AREA_SUM,
                "pressure_drop_spacer": "",
            }
        )
    report = _load_report()
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "area=PASS" in out
    assert "flux_in=N/A" in out
    assert "flux_out=N/A" in out
    assert "spacer_dp=N/A" in out
    assert "flux_in_reject=0" in out


def test_report_requires_ro_data_root(monkeypatch):
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    report = _load_report()
    with pytest.raises(ValueError, match="RO_DATA_ROOT"):
        report.main()


def test_report_no_csv_is_not_a_reject(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RO_DATA_ROOT", str(tmp_path))
    payload = run_payload()
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    run_directory.mkdir(parents=True)
    write_run_manifest(run_directory, payload)
    report = _load_report()
    buf = io.StringIO()
    report.report_run_leaf(run_directory / "manifest.json", payload, file=buf)
    assert "NO_CSV" in buf.getvalue()
