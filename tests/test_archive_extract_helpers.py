"""Tests for manifest-less archive extract helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ro.domain_layout import normalize_post_layout_overrides
from ro.fluent_report_helpers import (
    assert_layout_spans_match_cell_profile,
    resolve_scoring_layout_from_config,
    scoring_geometry_from_layout,
)
from ro.domain_layout import DomainLayout
from ro.manifest import ManifestError, resolve_analytic_cwall_for_extract


def _write_cell_profile_csv(path: Path) -> None:
    layout = DomainLayout(
        n_buffer_in=1,
        n_active=7,
        n_buffer_out=2,
        cell_length_x_m=0.003465,
        buffer_length_in_m=0.003465,
        buffer_length_out_m=0.00693,
    )
    spans = layout.spans(0.0)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["span", "x_min_m", "x_max_m"])
        writer.writeheader()
        for label, x_min, x_max in spans:
            profile_label = label
            if label.startswith("active_"):
                profile_label = "spacer_" + label.split("_", 1)[1]
            writer.writerow(
                {
                    "span": profile_label,
                    "x_min_m": x_min,
                    "x_max_m": x_max,
                }
            )


def test_normalize_post_layout_overrides_accepts_n_active_cells_alias():
    overrides = normalize_post_layout_overrides(
        {
            "n_buffer_in": 1,
            "n_active_cells": 7,
            "n_buffer_out": 2,
            "cell_length_x_m": 0.003465,
            "n_lead_excluded": 3,
            "n_trail_excluded": 0,
        }
    )
    assert overrides["n_active"] == 7
    assert overrides["buffer_length_in_m"] == 0.003465
    assert overrides["buffer_length_out_m"] == 0.00693
    assert overrides["domain_length_m"] == 0.03465


def test_assert_layout_spans_match_cell_profile(tmp_path: Path):
    profile = tmp_path / "cell_profile_recon.csv"
    _write_cell_profile_csv(profile)
    layout = DomainLayout(
        n_buffer_in=1,
        n_active=7,
        n_buffer_out=2,
        cell_length_x_m=0.003465,
        buffer_length_in_m=0.003465,
        buffer_length_out_m=0.00693,
    )
    scoring_layout = scoring_geometry_from_layout(layout, 0.0)
    result = assert_layout_spans_match_cell_profile(scoring_layout, tmp_path)
    assert result["spacer_1_x_min_m"] == pytest.approx(0.003465)
    assert result["spacer_1_x_max_m"] == pytest.approx(0.006930)
    assert result["domain_end_m"] == pytest.approx(0.034650)


def test_resolve_analytic_cwall_without_manifest(tmp_path: Path):
    udf = tmp_path / "260814_RO_UDF.c"
    udf.write_text(
        "#ifndef RO_ANALYTIC_CWALL\n#define RO_ANALYTIC_CWALL 1\n#endif\n",
        encoding="utf-8",
    )
    value, source = resolve_analytic_cwall_for_extract(tmp_path)
    assert value == 1
    assert source == "case_local_udf"
    assert not (tmp_path / "manifest.json").exists()


def test_resolve_analytic_cwall_without_flag_raises(tmp_path: Path):
    udf = tmp_path / "260810_RO_UDF.c"
    udf.write_text("// no analytic cwall flag\n", encoding="utf-8")
    with pytest.raises(ValueError, match="RO_ANALYTIC_CWALL not found"):
        resolve_analytic_cwall_for_extract(tmp_path)


def test_resolve_analytic_cwall_zero_raises(tmp_path: Path):
    udf = tmp_path / "260813_RO_UDF.c"
    udf.write_text("#define RO_ANALYTIC_CWALL 0\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="must be 1"):
        resolve_analytic_cwall_for_extract(tmp_path)


def test_normalize_overrides_enables_scoring_layout_resolution():
    from types import SimpleNamespace

    overrides = normalize_post_layout_overrides(
        {
            "n_buffer_in": 1,
            "n_active_cells": 7,
            "n_buffer_out": 2,
            "cell_length_x_m": 0.003465,
        }
    )
    cfg = SimpleNamespace(domain_x_min_m=0.0, **overrides)
    geo = resolve_scoring_layout_from_config(cfg)
    assert geo.layout.n_active == 7
    assert geo.unit_cell_boundary_x_m[-1] == pytest.approx(0.03465)
