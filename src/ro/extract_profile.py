"""Opt-in report-extract profiles.

``full`` (the default) is the existing extract. ``mfbo`` keeps the summary
columns the pillar adapter and the convergence gate read, and skips the
per-cell Fluent reports those columns do not use.

Measured on REF_empty (``docs/POSTPROCESSING_MAP.md``, 38.7 min) the phase
shares in ``report_extract_timing.json`` are:

- ``segmented_membrane_cp`` about 62 percent
- ``cell_7_report_creation`` about 16 percent
- ``cell_8_4_midplane_cb`` about 7 percent
- ``cell_8_compute`` about 7 percent
- ``read_case_data`` and ``fluent_launch`` about 3 percent each

``cell_8_25_salt_reduction`` is inside that budget and is not an MFBO input.
Importing this module does not launch Fluent.
"""

from __future__ import annotations

import argparse
import math
import re
from typing import Any, Mapping, Sequence

from ro.convergence_quality import PRESSURE_DROP_CELLS
from ro.fluent_report_helpers import (
    unit_cell_mixing_cup_report_name,
    unit_cell_pressure_report_name,
)

PROFILE_FULL = "full"
PROFILE_MFBO = "mfbo"
PROFILES = frozenset({PROFILE_FULL, PROFILE_MFBO})

SHARED_COLUMN_REL_TOL = 1e-9

# Written on the mfbo wide summary only, so a lightweight extract cannot be
# read as a full campaign extract.
PROFILE_COLUMN = "profile"

# Fluent report definitions the mfbo profile still creates. Inlet/outlet
# pressure, the domain pressure drop, the signed LMH expression, and the
# UDM area sum are not inputs to the adapter or the convergence gate.
MFBO_BASE_REPORTS = (
    "pp_m_in",
    "pp_m_out",
    "pp_area_mem",
    "pp_lmh_mass_balance",
    "pp_lmh_udm_avg",
    "pp_volint_salt_mass_source",
    "pp_volint_total_mass_source",
    "pp_p_spacer_in_avg",
    "pp_p_spacer_out_avg",
    "pp_pressure_drop_spacer",
)

# Wide-summary names the mfbo profile must leave non-blank. Canonical CP
# window columns are checked by require_canonical_cp_summary_columns.
MFBO_REQUIRED_SUMMARY_COLUMNS = (
    "lmh_mass_balance",
    "area_mem",
    "pressure_drop_spacer_per_m",
    "cpc_window_avg_flux",
    "cp_q999_window_flux",
    "cp_canon_window_avg",
    "active_window_fluid_volume_m3",
    "active_window_membrane_area_m2",
    "active_window_spacer_area_m2",
    "active_window_box_volume_m3",
    "active_window_porosity",
    "lmh_udm_avg",
    "lmh_relative_difference",
    "m_in",
    "m_out",
    "mass_balance_relative_error",
    PROFILE_COLUMN,
)

# Same-formula columns. A name absent from this set and from _CELL_METRIC
# is not written, so it is not a shared parity column.
_MFBO_SUMMARY_METRICS = frozenset(
    {
        PROFILE_COLUMN,
        "geo_name",
        "case_name",
        "m_in",
        "m_out",
        "m_in_with_sources",
        "m_out_with_sources",
        "m_in_mass_source",
        "m_out_mass_source",
        "boundary_permeate_mass_flow",
        "boundary_permeate_mass_flow_abs_m_in_plus_m_out",
        "area_mem",
        "active_window_fluid_volume_m3",
        "active_window_membrane_area_m2",
        "active_window_spacer_area_m2",
        "active_window_box_volume_m3",
        "active_window_porosity",
        "lmh_mass_balance",
        "lmh_mass_balance_signed_python",
        "lmh_udm_avg",
        "lmh_difference_mass_balance_minus_udm",
        "lmh_relative_difference",
        "domain_length_m",
        "spacer_x_in_m",
        "spacer_x_out_m",
        "spacer_length_m",
        "p_spacer_in_avg",
        "p_spacer_out_avg",
        "pressure_drop_spacer",
        "pressure_drop_spacer_per_m",
        "water_sink_volume_integral_UDM1",
        "salt_sink_volume_integral_UDM0",
        "total_sink_volume_integral_UDM2",
        "mass_balance_error_boundary_minus_total_sink",
        "mass_balance_relative_error",
        "c_b_window_mol_m3",
        "c_b_window_salt_field",
        "c_b_window_is_mass_fraction_field",
        "cp_canon_window_avg",
        "cp_canon_window_max",
        "cp_L1_window_avg",
        "cp_L1_window_max",
        "cp_L2_window_avg",
        "cp_L2_window_max",
        "cp_canon_rescale_delta_max",
        "cp_canon_rescale_delta_status",
        "cp_scalar_rescale_guard_threshold",
        "compute_cp_spread",
        "c_inlet_ref_mol_m3",
        "pp_pressure_drop_periodic_per_m",
        "mu",
        "expected_outlet_gauge_pressure",
        "cm_area_mean_window",
        "cm_q999_window",
        "cm_q99_window",
        "cp_ref_area",
        "cp_ref_flux",
        "n_faces_jw_nonpositive",
        "area_jw_nonpositive",
        "cpc_window_avg_area",
        "cpc_window_avg_flux",
        "cp_q999_window_area",
        "cp_q999_window_flux",
        "cp_q99_window_area",
        "cp_q99_window_flux",
        "report_definition_errors_json",
        "report_compute_errors_json",
        "segmented_cp_diagnostic_error",
        "segmented_cp_diagnostic_error_type",
        "segmented_cp_diagnostic_error_message",
    }
)

# Per-cell names produced by the same formula on the cells the profile
# still clips. all-active aggregates and per-wall clips are not in this list.
_CELL_METRIC = re.compile(
    r"^(?:"
    r"pp_pressure_drop_cell_\d+"
    r"|pp_p_unit_cell_boundary_\d+_avg"
    r"|pp_unit_cell_boundary_\d+_x_m"
    r"|pp_salt_mass_fraction_unit_cell_boundary_\d+_massavg"
    r"|pp_salt_molar_concentration_unit_cell_boundary_\d+_massavg_mol_m3"
    r"|pp_c_b_midplane_cell_\d+_mol_m3"
    r"|pp_membrane_area_cell_\d+_m2"
    r"|pp_cm_mol_m3_cell_\d+"
    r"|pp_jw_m_per_s_cell_\d+"
    r"|pp_cp_inlet_unit_cell_boundary_\d+"
    r"|pp_cp_bulk_unit_cell_boundary_\d+"
    r"|pp_cp_perm_mol_m3_cell_\d+"
    r"|pp_cp_canon_cell_\d+"
    r"|pp_cp_canon_max_cell_\d+"
    r"|pp_cp_L1_cell_\d+"
    r"|pp_cp_L1_max_cell_\d+"
    r"|pp_cp_L2_cell_\d+"
    r"|pp_cp_L2_max_cell_\d+"
    r"|pp_cp_canon_rescale_k_cell_\d+"
    r"|pp_cp_canon_rescale_delta_cell_\d+"
    r")$"
)

# Present on a successful extract even when the value is null.
_NULLABLE_SUMMARY_METRICS = frozenset({"cp_canon_rescale_delta_max"})

# Explained in the parity report. These are not shared columns.
PARITY_EXCEPTIONS = (
    "profile is written only by --profile mfbo.",
    "Columns present only on the full extract were not computed: "
    "per-boundary salt area-average, plane area, and mixing-cup outside "
    "the evaluation-cell flanks; pressure on planes that do not bound "
    "cells 4-7 or the evaluation window; membrane segments outside the "
    "evaluation window; per-wall iso-clips; all-active CP aggregates; "
    "per-cell concentration quantiles; y1; turbulence reductions; "
    "salt mass-fraction range diagnostics; the legacy whole-domain "
    "center-plane average; whole-membrane jw/cm/salt-flux/shear "
    "max and min; inlet and outlet pressure; the signed LMH expression; "
    "and the UDM area sum.",
    "cp_membrane_segment_fluent_computes counts Fluent surface "
    "evaluations. The mfbo profile evaluates fewer segments, so the "
    "count is omitted rather than compared.",
    "Shared numeric columns use relative tolerance 1e-9 against the "
    "full-extract value. A full-extract value of 0 must match exactly.",
)


def parse_extract_profile(argv: Sequence[str] | None = None) -> str:
    """Return ``full`` or ``mfbo``. Unknown flags and profiles raise."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", default=PROFILE_FULL)
    args, unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    if unknown:
        raise ValueError(f"Unrecognized extract arguments: {unknown!r}.")
    profile = args.profile
    if profile not in PROFILES:
        raise ValueError(
            f"Unknown extract profile {profile!r}. "
            f"Expected one of {sorted(PROFILES)}."
        )
    return profile


def report_extract_argv(python: str, worker: str, profile: str | None = None) -> list[str]:
    """Worker argv. Omitting ``profile`` (or ``full``) leaves the command unchanged."""
    cmd = [str(python), str(worker)]
    if profile is None or profile == PROFILE_FULL:
        return cmd
    if profile != PROFILE_MFBO:
        raise ValueError(
            f"Unknown extract profile {profile!r}. "
            f"Expected one of {sorted(PROFILES)}."
        )
    cmd.extend(["--profile", profile])
    return cmd


def mfbo_pressure_cells(evaluation_cells: Sequence[int]) -> set[int]:
    """Cells whose boundary pressures the convergence warning reads."""
    cells = set(PRESSURE_DROP_CELLS)
    for cell in evaluation_cells:
        if isinstance(cell, bool) or not isinstance(cell, int):
            raise TypeError(
                f"evaluation cell must be an int, got {cell!r}."
            )
        if cell < 1:
            raise ValueError(f"evaluation cell must be >= 1, got {cell!r}.")
        cells.add(cell)
    return cells


def mfbo_pressure_boundary_indices(evaluation_cells: Sequence[int]) -> set[int]:
    """Unit-cell planes that bound the convergence pressure cells."""
    indices: set[int] = set()
    for cell in mfbo_pressure_cells(evaluation_cells):
        indices.add(cell - 1)
        indices.add(cell)
    return indices


def mfbo_mixing_cup_boundary_indices(evaluation_cells: Sequence[int]) -> set[int]:
    """Flanking planes for the mid-plane c_b mixing-cup guard."""
    indices: set[int] = set()
    for cell in evaluation_cells:
        if isinstance(cell, bool) or not isinstance(cell, int):
            raise TypeError(
                f"evaluation cell must be an int, got {cell!r}."
            )
        if cell < 1:
            raise ValueError(f"evaluation cell must be >= 1, got {cell!r}.")
        indices.add(cell - 1)
        indices.add(cell)
    return indices


def mfbo_required_report_names(evaluation_cells: Sequence[int]) -> list[str]:
    """Report definitions whose failure must abort an mfbo extract."""
    names = list(MFBO_BASE_REPORTS)
    for index in sorted(mfbo_pressure_boundary_indices(evaluation_cells)):
        names.append(unit_cell_pressure_report_name(index))
    for index in sorted(mfbo_mixing_cup_boundary_indices(evaluation_cells)):
        names.append(unit_cell_mixing_cup_report_name(index))
    return names


def mfbo_keeps_summary_metric(name: str) -> bool:
    return name in _MFBO_SUMMARY_METRICS or _CELL_METRIC.fullmatch(name) is not None


def filter_mfbo_summary_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop metrics the mfbo profile did not compute.

    A null ``cp_canon_rescale_delta_max`` is kept: spread-off extracts
    store that null with status ``not_evaluated``.
    """
    kept: list[dict[str, Any]] = []
    for row in rows:
        name = row["metric"]
        if not mfbo_keeps_summary_metric(name):
            continue
        if row.get("value") is None and name not in _NULLABLE_SUMMARY_METRICS:
            continue
        kept.append(dict(row))
    return kept


def _summary_blank(value: Any) -> bool:
    if value is None or value == "":
        return True
    return isinstance(value, float) and not math.isfinite(value)


def require_mfbo_summary_columns(wide_record: Mapping[str, Any]) -> None:
    """Raise if an mfbo extract is missing an adapter or gate column."""
    if wide_record.get(PROFILE_COLUMN) != PROFILE_MFBO:
        raise RuntimeError(
            "MFBO extract summary must set profile="
            f"{PROFILE_MFBO!r}, got {wide_record.get(PROFILE_COLUMN)!r}."
        )
    missing = [
        column
        for column in MFBO_REQUIRED_SUMMARY_COLUMNS
        if column not in wide_record or _summary_blank(wide_record[column])
    ]
    if missing:
        raise RuntimeError(
            "MFBO extract summary is missing required columns "
            f"{missing!r}."
        )


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text == "":
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return number


def shared_values_match(full: Any, mfbo: Any, *, rel_tol: float = SHARED_COLUMN_REL_TOL) -> bool:
    """True when a shared column agrees.

    Numbers use relative tolerance against the full-extract value. A full
    value of 0 matches only an mfbo value of 0. Other values compare as text.
    """
    if _blank(full) and _blank(mfbo):
        return True
    full_number = _number(full)
    mfbo_number = _number(mfbo)
    if full_number is None and mfbo_number is None:
        return str(full).strip() == str(mfbo).strip()
    if full_number is None or mfbo_number is None:
        return False
    if full_number == 0.0:
        return mfbo_number == 0.0
    return abs(mfbo_number - full_number) <= rel_tol * abs(full_number)


def compare_shared_summary_columns(
    full_record: Mapping[str, Any],
    mfbo_record: Mapping[str, Any],
    *,
    rel_tol: float = SHARED_COLUMN_REL_TOL,
) -> dict[str, Any]:
    """Compare columns present on both summaries.

    The mfbo summary must carry ``profile=mfbo``. The reference must be a
    full extract (no profile column, or ``profile=full``).
    """
    if mfbo_record.get(PROFILE_COLUMN) != PROFILE_MFBO:
        raise ValueError(
            "MFBO summary must set profile="
            f"{PROFILE_MFBO!r}, got {mfbo_record.get(PROFILE_COLUMN)!r}."
        )
    full_profile = full_record.get(PROFILE_COLUMN)
    if full_profile not in (None, "", PROFILE_FULL):
        raise ValueError(
            "Reference summary is not a full extract "
            f"(profile={full_profile!r})."
        )
    shared = sorted(
        (set(full_record) & set(mfbo_record)) - {PROFILE_COLUMN}
    )
    mismatches = []
    for column in shared:
        if shared_values_match(
            full_record[column],
            mfbo_record[column],
            rel_tol=rel_tol,
        ):
            continue
        mismatches.append(
            {
                "column": column,
                "full": full_record[column],
                "mfbo": mfbo_record[column],
            }
        )
    only_full = sorted(set(full_record) - set(mfbo_record))
    only_mfbo = sorted(set(mfbo_record) - set(full_record) - {PROFILE_COLUMN})
    return {
        "ok": not mismatches,
        "rel_tol": rel_tol,
        "shared_columns": shared,
        "mismatches": mismatches,
        "only_full": only_full,
        "only_mfbo": only_mfbo,
        "exceptions": list(PARITY_EXCEPTIONS),
    }
