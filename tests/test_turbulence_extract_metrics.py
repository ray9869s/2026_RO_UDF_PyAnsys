"""RANS extraction columns. Laminar values of existing metrics stay put."""

import pytest

from ro.fluent_report_helpers import (
    TURBULENCE_METRIC_NAMES,
    null_turbulence_metrics,
    require_turbulence_scalar_fields,
    scalar_field_names,
    summary_rows_to_wide_record,
    turbulence_extract_plan,
    turbulence_metrics_from_reductions,
    turbulence_summary_rows,
)
from ro.manifest import ManifestError

MASS_DIFFUSIVITY = 2.0e-9


def _laminar_existing_rows():
    return [
        {"metric": "geo_name", "value": "P_p100_h30", "unit": "-"},
        {"metric": "case_name", "value": "u0p2_p6M", "unit": "-"},
        {"metric": "lmh_mass_balance", "value": 24.5, "unit": "LMH"},
        {"metric": "pressure_drop_spacer_per_m", "value": 1.25e5, "unit": "Pa/m"},
        {"metric": "cp_canon_window_avg", "value": 1.08, "unit": "-"},
        {"metric": "mass_balance_relative_error", "value": 1.0e-6, "unit": "-"},
    ]


def test_laminar_wide_record_keeps_existing_column_values():
    existing = _laminar_existing_rows()
    before = summary_rows_to_wide_record(existing)
    after = summary_rows_to_wide_record(
        existing + turbulence_summary_rows(null_turbulence_metrics("laminar"))
    )
    for column, value in before.items():
        assert after[column] == value
    assert after["turbulence_metrics_status"] == "laminar"
    for column in TURBULENCE_METRIC_NAMES:
        if column == "turbulence_metrics_status":
            continue
        assert after[column] is None


def test_legacy_manifest_does_not_compute():
    plan = turbulence_extract_plan({"solver_settings": {"max_iterations": 100}})
    assert plan == "legacy_manifest_no_viscous_model"
    metrics = null_turbulence_metrics(plan)
    assert metrics["turbulence_metrics_status"] == "legacy_manifest_no_viscous_model"
    assert metrics["viscosity_ratio_max"] is None
    assert metrics["diff_ratio_max"] is None


def test_laminar_plan_and_non_laminar_compute_plan():
    assert turbulence_extract_plan(
        {"solver_settings": {"viscous_model": "laminar"}}
    ) == "laminar"
    assert turbulence_extract_plan(
        {"solver_settings": {"viscous_model": "k-epsilon-realizable-ewt"}}
    ) == "compute"


def test_invalid_viscous_model_still_raises():
    with pytest.raises(ManifestError):
        turbulence_extract_plan({"solver_settings": {"viscous_model": "spalart"}})


def test_missing_scalar_field_raises():
    present = scalar_field_names(
        {
            "fields": [
                {"name": "diff-nacl"},
                {"name": "diffl-nacl"},
                {"name": "viscosity-turb"},
            ]
        }
    )
    with pytest.raises(RuntimeError, match="viscosity-ratio"):
        require_turbulence_scalar_fields(present)


def test_diffl_mismatch_raises():
    with pytest.raises(RuntimeError, match="diffl_nacl_min"):
        turbulence_metrics_from_reductions(
            viscosity_ratio_max=12.0,
            viscosity_ratio_volavg=3.0,
            diff_nacl_max=4.0e-9,
            diff_nacl_volavg=3.0e-9,
            diffl_nacl_min=2.1e-9,
            diffl_nacl_max=MASS_DIFFUSIVITY,
            mass_diffusivity=MASS_DIFFUSIVITY,
        )


def test_computed_diff_ratios():
    metrics = turbulence_metrics_from_reductions(
        viscosity_ratio_max=12.0,
        viscosity_ratio_volavg=3.5,
        diff_nacl_max=4.0e-9,
        diff_nacl_volavg=3.0e-9,
        diffl_nacl_min=MASS_DIFFUSIVITY,
        diffl_nacl_max=MASS_DIFFUSIVITY,
        mass_diffusivity=MASS_DIFFUSIVITY,
    )
    assert metrics["turbulence_metrics_status"] == "computed"
    assert metrics["viscosity_ratio_max"] == 12.0
    assert metrics["diff_ratio_max"] == pytest.approx(2.0)
    assert metrics["diff_ratio_volavg"] == pytest.approx(1.5)
