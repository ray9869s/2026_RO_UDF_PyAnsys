"""Unit tests for 07_batch_solver_rerun.select_candidates matrix filter (F-05)."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import pytest

from helpers import SCRIPTS_DIR, load_module

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
F05_FIXTURE_CSV = FIXTURES_DIR / "active_solver_rerun_candidates_f05.csv"

# Pre-F-05 matrix gate (full-string match only).
LEGACY_MATRIX_CASE_RE = re.compile(r"^u\d+p\d+_p\d+M$")

SIN_MESH_CPG5_BL4 = "mesh_max100_min006_cpg5_bl4"


@pytest.fixture(scope="module")
def rerun07():
    return load_module("batch_solver_rerun_under_test", SCRIPTS_DIR / "batch_solver_rerun.py")


def make_select_args(**overrides) -> argparse.Namespace:
    defaults = {
        "family": [],
        "geo_id": [],
        "mesh_id": [],
        "run_id": [],
        "convergence_status": [],
        "start_index": 0,
        "limit": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def candidate_row(
    geo_name: str,
    case_name: str,
    convergence_status: str = "MAX_ITER_REACHED",
) -> dict[str, str]:
    return {
        "_source_row_number": "2",
        "geo_name": geo_name,
        "case_name": case_name,
        "convergence_status": convergence_status,
        "case_status": "NEEDS_SOLVER_RERUN",
    }


def load_fixture_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row_number, row in enumerate(reader, start=2):
            cleaned = {
                str(key).strip(): "" if value is None else str(value).strip()
                for key, value in row.items()
                if key is not None
            }
            cleaned["_source_row_number"] = str(row_number)
            rows.append(cleaned)
        return rows


class TestSelectCandidatesMatrixFilter:
    def test_geo_id_filter_selects_matching_row(self, rerun07):
        rows = [
            candidate_row("D2450_a45", "u0p2_p6M"),
            candidate_row("D1225_a45", "u0p2_p6M"),
        ]
        selected, stats = rerun07.select_candidates(
            rows,
            make_select_args(geo_id=["D2450_a45"]),
        )
        assert stats["selected"] == 1
        assert stats["filtered_geo_id"] == 1
        assert selected[0]["geo_name"] == "D2450_a45"

    def test_plain_matrix_run_id_is_selected(self, rerun07):
        selected, stats = rerun07.select_candidates(
            [candidate_row("Sin_ST", "u0p2_p4M")],
            make_select_args(),
        )
        assert stats["selected"] == 1
        assert stats["non_matrix_case_name"] == 0
        assert selected[0]["case_name"] == "u0p2_p4M"

    @pytest.mark.parametrize(
        "case_name",
        [
            "not_a_case",
            "__mesh_only",
            "u0p2_p4",
            "attempt_20260705_161718",
            f"u0p2_p4M__{SIN_MESH_CPG5_BL4}",
            f"u0p3_p8M__{SIN_MESH_CPG5_BL4}",
        ],
    )
    def test_non_matrix_names_are_rejected(self, rerun07, case_name):
        selected, stats = rerun07.select_candidates(
            [candidate_row("Sin_ST", case_name)],
            make_select_args(),
        )
        assert selected == []
        assert stats["selected"] == 0
        assert stats["non_matrix_case_name"] == 1


class TestSelectCandidatesFixtureCsv:
    def test_fixture_csv_header_is_pre_id_f05_schema(self):
        with F05_FIXTURE_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            fieldnames = csv.DictReader(handle).fieldnames
        assert fieldnames == [
            "geo_name",
            "case_name",
            "max_iteration_detected",
            "convergence_status",
            "hard_solver_failure_detected",
            "max_iter_only",
            "failure_evidence_short",
            "latest_log_file",
            "suggested_next_action",
        ]

    def test_fixture_rows_reject_mesh_qualified_names(self, rerun07):
        rows = load_fixture_rows(F05_FIXTURE_CSV)
        assert len(rows) == 4

        mesh_qualified = [
            row["case_name"] for row in rows if "__" in row["case_name"]
        ]
        assert len(mesh_qualified) == 3
        for case_name in mesh_qualified:
            assert LEGACY_MATRIX_CASE_RE.match(case_name) is None

        selected, stats = rerun07.select_candidates(rows, make_select_args())
        assert stats["input_rows"] == 4
        assert stats["non_matrix_case_name"] == 3
        assert stats["selected"] == 1
        assert {row["latest_log_file"] for row in selected} == {"/tmp/fake.log"}
        assert selected[0]["case_name"] == "u0p2_p4M"

    def test_legacy_regex_would_drop_mesh_qualified_fixture_rows(self):
        rows = load_fixture_rows(F05_FIXTURE_CSV)
        legacy_selected = [
            row for row in rows if LEGACY_MATRIX_CASE_RE.match(row["case_name"])
        ]
        mesh_qualified_rows = [row for row in rows if "__" in row["case_name"]]
        assert len(mesh_qualified_rows) == 3
        assert len(legacy_selected) == 1
        assert legacy_selected[0]["case_name"] == "u0p2_p4M"
