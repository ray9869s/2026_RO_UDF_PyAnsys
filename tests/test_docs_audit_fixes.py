"""A4-01..A4-04 and the c_b wording stay fixed in the docs and docstrings."""

from __future__ import annotations

from pathlib import Path

from ro import cp_metrics

ROOT = Path(__file__).resolve().parents[1]
DOSSIER = (ROOT / "docs" / "PAPER_METHODS_DOSSIER.md").read_text(encoding="utf-8")
EXTRACT = (ROOT / "scripts" / "pyfluent_report_extract.py").read_text(encoding="utf-8")


def test_gate_denominator_matches_the_extract():
    assert "lmh_relative_difference = lmh_difference / lmh_mass_balance" in EXTRACT
    assert "/ abs(lmh_mass_balance)` (`lmh_relative_difference`)" in DOSSIER
    assert "/ abs(lmh_udm_avg)`" not in DOSSIER


def test_salt_flux_is_not_labelled_permeate_concentration():
    assert "cp = B * cm * Jw / (Jw + B)" not in DOSSIER
    assert "Js = B * cm * Jw / (Jw + B)" in DOSSIER


def test_v3_stop_is_documented():
    assert "Campaign v3 stop (`--convergence-profile campaign_v3`)" in DOSSIER
    assert "cp_live" in DOSSIER


def test_c_b_is_not_called_a_mixing_cup():
    for path in ("docs/AGENTS.md", "docs/metrics_conventions.md", "src/ro/monitor_change.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "mixing-cup \\(c_b\\)" not in text, path
        assert "(mixing-cup) salt" not in text, path
    assert "It is a mid-plane bulk concentration, not an x-normal mixing cup." in DOSSIER
    assert "Error-table method" in DOSSIER
    assert "6.7 Dead zone and agreement of CP definitions" in DOSSIER


def test_cp_metrics_docstrings_do_not_contradict_mean_first():
    assert "Wrong order" not in (cp_metrics.ratio_of_averages_cp_bae_approx.__doc__ or "")
    assert "Correct order" not in (cp_metrics.average_of_ratios_cp_bae_approx.__doc__ or "")


def test_metrics_conventions_documents_mean_first_order_and_dead_zone():
    text = (ROOT / "docs" / "metrics_conventions.md").read_text(encoding="utf-8")
    assert "**Superseded for the benchmark (2026-11-18).**" in text
    assert "cp_meanfirst_live_window_avg" in text
    assert "This live\nmask is not the UDM-9 zeroing." in text
    assert "filters only\n\\(J_w > 0\\)" in text
    assert "There is no\n   \\(J_w > 0\\) mask." in text
    dossier = (ROOT / "docs" / "PAPER_METHODS_DOSSIER.md").read_text(encoding="utf-8")
    assert "This live mask is not the UDM-9 zeroing" in dossier
    assert "excluded from the flux-weighted `c_p`" not in dossier


def test_c_b_is_not_called_a_mixing_cup():
    """Only the x-normal boundary cups (the guard) may be called mixing cups."""
    import re

    paths = [ROOT / "docs" / "POSTPROCESSING_MAP.md", ROOT / "docs" / "MESH_STUDY_RESULTS.md",
             ROOT / "src" / "ro" / "cp_concentration_stats.py", ROOT / "src" / "ro" / "fluent_report_helpers.py",
             ROOT / "scripts" / "diagnose_cp_quantile_spread.py", ROOT / "scripts" / "diagnose_cp_face_filters.py",
             ROOT / "scripts" / "solver_code_260616.py", ROOT / "scripts" / "pyfluent_report_extract.py"]
    bad = re.compile(r"mid-plane mixing[- ]cup|window mixing-cup|Mixing-cup c_b|Mixing-cup `c_b|"
                     r"mixing-cup salt concentration per|Mixing-cup \(surface-massavg\)")
    for path in paths:
        assert not bad.search(path.read_text(encoding="utf-8")), path
