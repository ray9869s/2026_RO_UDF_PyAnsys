"""Compact machine-readable record for one 2D case.

Metrics stay null until a solve exists. Validity is ``not_run`` until the
required numbers are present. A finished case is ``valid`` when those
numbers are finite, the cell count is positive, and the mass-balance
error is strictly inside the campaign limit. ``diverged`` is ``invalid``.
``max_iter_reached`` can still be ``valid``. ``diverged`` and an
unreadable stop are ``invalid``. Windowed CP and PyEnSight gates from
the 3D campaign are not applied.
"""

from __future__ import annotations

import math
from typing import Mapping

from ro.convergence_quality import MASS_BALANCE_REL_ABS_MAX
from ro.lmh_metrics import lmh_from_mass_imbalance_kg_s
from ro.solver_common import STOP_REASON_NOT_RUN, STOP_REASON_VALUES

SCHEMA_VERSION = 1

VALIDITY_NOT_RUN = "not_run"
VALIDITY_VALID = "valid"
VALIDITY_INVALID = "invalid"
VALIDITY_VALUES = (VALIDITY_NOT_RUN, VALIDITY_VALID, VALIDITY_INVALID)

_REQUIRED_METRICS = (
    "cell_count",
    "lmh",
    "pressure_drop_pa",
    "mass_balance_rel",
)

RESULT_FIELDS = (
    "schema_version",
    "geo_id",
    "fidelity",
    "run_id",
    "d_m",
    "L_m",
    "channel_height_m",
    "n_pitches",
    "length_m",
    "inlet_velocity_m_s",
    "outlet_gauge_pressure_pa",
    "mesh_max_size_m",
    "mesh_min_size_m",
    "boundary_layers",
    "cell_count",
    "lmh",
    "cp_average",
    "pressure_drop_pa",
    "pressure_drop_per_length_pa_per_m",
    "solver_iterations",
    "solver_wall_time_s",
    "total_wall_time_s",
    "convergence_status",
    "mass_balance_rel",
    "validity",
    "udf_2d_status",
    "mesh_level",
    "height_divisions",
    "cells_across_channel_height",
    "launch_time_s",
    "mesh_read_time_s",
    "setup_time_s",
    "extraction_time_s",
    "startup_overhead_s",
)


def _optional_finite(name: str, value: object) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null, got {value!r}.")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    if name in {
        "cell_count",
        "solver_iterations",
        "height_divisions",
        "cells_across_channel_height",
    }:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{name} must be an integer, got {value!r}.")
        if int(value) < 0:
            raise ValueError(f"{name} must be >= 0, got {value!r}.")
        return int(value)
    return float(value)


def lmh_from_mass_balance(
    *,
    mass_in_kg_s: float,
    mass_out_kg_s: float,
    density_kg_m3: float,
    membrane_area_m2: float,
) -> float:
    """Campaign LMH definition on the 2D membrane area (no blocked fraction)."""
    return lmh_from_mass_imbalance_kg_s(
        mass_in_kg_s,
        mass_out_kg_s,
        density_kg_m3=density_kg_m3,
        nominal_area_m2=membrane_area_m2,
        membrane_blocked_area_frac=0.0,
    )


def permeate_mass_flow_kg_s(mass_in_kg_s: float, mass_out_kg_s: float) -> float:
    """Boundary permeate rate.

    Fluent reports mass flow into the domain as positive, so the outlet
    value is negative and ``mass_in + mass_out`` is the mass that left
    through the membrane.
    """
    return abs(float(mass_in_kg_s) + float(mass_out_kg_s))


def mass_balance_relative_error(
    permeate_kg_s: float,
    total_sink_kg_s: float,
) -> float:
    """Campaign balance: boundary permeate versus the UDF total-sink integral.

    Matches ``pyfluent_report_extract``: ``(permeate - |sink|) / |sink|``.
    """
    denom = abs(float(total_sink_kg_s))
    if denom == 0.0:
        raise ValueError("total_sink_kg_s must be non-zero.")
    return (float(permeate_kg_s) - denom) / denom


def pressure_drop_per_length(pressure_drop_pa: float, length_m: float) -> float:
    if length_m <= 0.0:
        raise ValueError(f"length_m must be positive, got {length_m!r}.")
    return float(pressure_drop_pa) / float(length_m)


def judge_validity(
    *,
    convergence_status: str,
    cell_count: int | None,
    lmh: float | None,
    pressure_drop_pa: float | None,
    mass_balance_rel: float | None,
) -> str:
    present = {
        "cell_count": cell_count,
        "lmh": lmh,
        "pressure_drop_pa": pressure_drop_pa,
        "mass_balance_rel": mass_balance_rel,
    }
    if all(value is None for value in present.values()):
        if convergence_status == STOP_REASON_NOT_RUN:
            return VALIDITY_NOT_RUN
        return VALIDITY_INVALID
    if any(present[key] is None for key in _REQUIRED_METRICS):
        return VALIDITY_INVALID
    if cell_count is not None and cell_count < 1:
        return VALIDITY_INVALID
    assert mass_balance_rel is not None
    # Same strict cut as ro.convergence_quality: equality fails.
    if abs(mass_balance_rel) >= MASS_BALANCE_REL_ABS_MAX:
        return VALIDITY_INVALID
    # A finished Fluent session is not enough. Only a classified stop with
    # a mass balance inside the campaign limit can be valid.
    if convergence_status not in {
        "residual_converged",
        "qoi_converged",
        "max_iter_reached",
    }:
        return VALIDITY_INVALID
    return VALIDITY_VALID


def build_result_record(
    plan: Mapping[str, object],
    metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Flatten one case plan plus optional measured metrics."""
    geometry = plan["geometry"]
    mesh = plan["mesh"]
    operating = plan["operating"]
    physics = plan["physics"]
    if not isinstance(geometry, Mapping):
        raise TypeError("plan['geometry'] must be a mapping.")
    if not isinstance(mesh, Mapping):
        raise TypeError("plan['mesh'] must be a mapping.")
    if not isinstance(operating, Mapping):
        raise TypeError("plan['operating'] must be a mapping.")
    if not isinstance(physics, Mapping):
        raise TypeError("plan['physics'] must be a mapping.")

    supplied = dict(metrics or {})
    convergence_status = str(
        supplied.get("convergence_status", STOP_REASON_NOT_RUN)
    )
    if convergence_status not in STOP_REASON_VALUES:
        raise ValueError(
            f"Unknown convergence_status: {convergence_status!r}."
        )

    length_m = float(geometry["length_m"])
    pressure_drop = _optional_finite(
        "pressure_drop_pa",
        supplied.get("pressure_drop_pa"),
    )
    per_length = supplied.get("pressure_drop_per_length_pa_per_m")
    if per_length is None and isinstance(pressure_drop, float):
        per_length = pressure_drop_per_length(pressure_drop, length_m)

    cell_count = _optional_finite("cell_count", supplied.get("cell_count"))
    lmh = _optional_finite("lmh", supplied.get("lmh"))
    cp_average = _optional_finite("cp_average", supplied.get("cp_average"))
    mass_balance = _optional_finite(
        "mass_balance_rel",
        supplied.get("mass_balance_rel"),
    )
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "geo_id": geometry["geo_id"],
        "fidelity": mesh["fidelity"],
        "run_id": plan["run_id"],
        "d_m": geometry["d_m"],
        "L_m": geometry["L_m"],
        "channel_height_m": geometry["channel_height_m"],
        "n_pitches": geometry["n_pitches"],
        "length_m": length_m,
        "inlet_velocity_m_s": operating["inlet_velocity_m_s"],
        "outlet_gauge_pressure_pa": operating["outlet_gauge_pressure_pa"],
        "mesh_max_size_m": mesh.get("actual_max_edge_m", mesh["max_size_m"]),
        "mesh_min_size_m": mesh.get("actual_min_edge_m", mesh["min_size_m"]),
        "boundary_layers": mesh["boundary_layers"],
        "cell_count": cell_count,
        "lmh": lmh,
        "cp_average": cp_average,
        "pressure_drop_pa": pressure_drop,
        "pressure_drop_per_length_pa_per_m": _optional_finite(
            "pressure_drop_per_length_pa_per_m",
            per_length,
        ),
        "solver_iterations": _optional_finite(
            "solver_iterations",
            supplied.get("solver_iterations"),
        ),
        "solver_wall_time_s": _optional_finite(
            "solver_wall_time_s",
            supplied.get("solver_wall_time_s"),
        ),
        "total_wall_time_s": _optional_finite(
            "total_wall_time_s",
            supplied.get("total_wall_time_s"),
        ),
        "convergence_status": convergence_status,
        "mass_balance_rel": mass_balance,
        "validity": judge_validity(
            convergence_status=convergence_status,
            cell_count=cell_count if isinstance(cell_count, int) else None,
            lmh=lmh if isinstance(lmh, float) else None,
            pressure_drop_pa=(
                pressure_drop if isinstance(pressure_drop, float) else None
            ),
            mass_balance_rel=(
                mass_balance if isinstance(mass_balance, float) else None
            ),
        ),
        "udf_2d_status": physics["udf_2d_status"],
        "mesh_level": mesh.get("mesh_level"),
        "height_divisions": _optional_finite(
            "height_divisions",
            mesh.get("height_divisions"),
        ),
        "cells_across_channel_height": _optional_finite(
            "cells_across_channel_height",
            mesh.get("cells_across_channel_height"),
        ),
        "launch_time_s": _optional_finite(
            "launch_time_s",
            supplied.get("launch_time_s"),
        ),
        "mesh_read_time_s": _optional_finite(
            "mesh_read_time_s",
            supplied.get("mesh_read_time_s"),
        ),
        "setup_time_s": _optional_finite(
            "setup_time_s",
            supplied.get("setup_time_s"),
        ),
        "extraction_time_s": _optional_finite(
            "extraction_time_s",
            supplied.get("extraction_time_s"),
        ),
        "startup_overhead_s": _optional_finite(
            "startup_overhead_s",
            supplied.get("startup_overhead_s"),
        ),
    }
    missing = [key for key in RESULT_FIELDS if key not in record]
    if missing:
        raise RuntimeError(f"result record missing fields: {missing}")
    return record
