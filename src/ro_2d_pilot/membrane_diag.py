"""Membrane geometry and flux-consistency diagnostics for the 2D pilot.

The source formula is not changed here. Geometry uses the quad mesh.
Flux ratios use numbers the solver reports.
"""

from __future__ import annotations

import math
from typing import Mapping

from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.mesh_build import QuadMesh
from ro_2d_pilot.source_schedule import (
    display_ramp,
    ramp_telemetry_warning,
    source_ramp_factor,
)

_MEMBRANE_ZONES = ("wall_bottom_mem", "wall_top_mem")


def membrane_geometry_diagnostics(
    mesh: QuadMesh,
    config: PilotConfig,
) -> dict[str, object]:
    """Length, face counts, and flux-to-source factors on membrane edges."""
    owners = _edge_owners(mesh.quads)
    areas = [_polygon_area(mesh.nodes, quad) for quad in mesh.quads]
    per_zone: dict[str, dict[str, float | int]] = {}
    cell_faces: dict[int, int] = {}
    paired: list[tuple[float, float]] = []
    for zone in _MEMBRANE_ZONES:
        length = 0.0
        count = 0
        for first, second in mesh.boundaries.get(zone, ()):
            length += _edge_length(mesh.nodes, first, second)
            count += 1
            cell = owners.get((first, second))
            if cell is None:
                continue
            cell_faces[cell] = cell_faces.get(cell, 0) + 1
            cell_area = areas[cell]
            edge_length = _edge_length(mesh.nodes, first, second)
            if cell_area <= 0.0 or edge_length <= 0.0:
                continue
            # Unit depth is on both the face area and the cell volume.
            factor = edge_length / cell_area
            distance = _wall_distance(mesh.nodes, first, second, mesh.quads[cell])
            if distance > 0.0:
                paired.append((factor, distance))
        per_zone[zone] = {"length_m": length, "face_count": count}
    area_over_volume = [factor for factor, _distance in paired]
    wall_distance = [distance for _factor, distance in paired]
    inverse_distance = [1.0 / distance for distance in wall_distance]
    top = per_zone["wall_top_mem"]
    bottom = per_zone["wall_bottom_mem"]
    total_length = float(top["length_m"]) + float(bottom["length_m"])
    expected = 2.0 * config.n_pitches * config.L_m * config.unit_depth_m
    return {
        "membrane_length_top_m": float(top["length_m"]),
        "membrane_length_bottom_m": float(bottom["length_m"]),
        "membrane_length_total_m": total_length,
        "expected_membrane_length_m": expected,
        "membrane_length_minus_expected_m": total_length - expected,
        "membrane_face_count_top": int(top["face_count"]),
        "membrane_face_count_bottom": int(bottom["face_count"]),
        "membrane_face_count": int(top["face_count"]) + int(bottom["face_count"]),
        "membrane_adjacent_cell_count": len(cell_faces),
        "membrane_cells_with_multiple_faces": sum(
            1 for count in cell_faces.values() if count > 1
        ),
        "area_over_volume_min": _min(area_over_volume),
        "area_over_volume_mean": _mean(area_over_volume),
        "area_over_volume_max": _max(area_over_volume),
        "wall_distance_min_m": _min(wall_distance),
        "wall_distance_mean_m": _mean(wall_distance),
        "wall_distance_max_m": _max(wall_distance),
        "inv_wall_distance_min": _min(inverse_distance),
        "inv_wall_distance_mean": _mean(inverse_distance),
        "inv_wall_distance_max": _max(inverse_distance),
        "area_over_volume_times_y1_mean": _mean(
            [
                factor * distance
                for factor, distance in zip(area_over_volume, wall_distance)
            ]
        ),
    }


def flux_consistency(
    *,
    mass_in_kg_s: float | None,
    mass_out_kg_s: float | None,
    source_integral_kg_s: float | None,
    water_flux_avg_m_s: float | None,
    salt_flux_avg_kg_m2_s: float | None,
    membrane_length_m: float | None,
    density_kg_m3: float,
) -> dict[str, float | None]:
    """Signed permeate rates and relative mismatches.

    ``source_integral_kg_s`` is the volume integral of ``UDM_TOTAL_S``.
    The surface rate is ``rho * Jw * L + salt_flux * L`` with unit depth.
    Balance is ``mass_in + mass_out`` and is positive when mass leaves
    through the membrane.
    """
    balance = _add(mass_in_kg_s, mass_out_kg_s)
    surface = _surface_mass_flow(
        water_flux_avg_m_s,
        salt_flux_avg_kg_m2_s,
        membrane_length_m,
        density_kg_m3,
    )
    return {
        "mass_in_kg_s": mass_in_kg_s,
        "mass_out_kg_s": mass_out_kg_s,
        "mdot_perm_balance_kg_s": balance,
        "mdot_perm_source_kg_s": source_integral_kg_s,
        "mdot_perm_surface_kg_s": surface,
        "source_vs_balance_rel": _relative(source_integral_kg_s, balance),
        "surface_vs_balance_rel": _relative(surface, balance),
        "surface_vs_source_rel": _relative(surface, source_integral_kg_s),
        "abs_source_vs_balance_rel": _abs_relative(source_integral_kg_s, balance),
        "abs_surface_vs_balance_rel": _abs_relative(surface, balance),
        "source_over_surface": _ratio(source_integral_kg_s, surface),
        "inferred_ramp_from_flux_ratio": _ratio(source_integral_kg_s, surface),
    }


def merge_diagnostics(
    geometry: Mapping[str, object],
    solution: Mapping[str, object] | None = None,
) -> dict[str, object]:
    merged = dict(geometry)
    if solution:
        merged.update(solution)
    return merged


def _surface_mass_flow(
    water_flux_m_s: float | None,
    salt_flux_kg_m2_s: float | None,
    length_m: float | None,
    density_kg_m3: float,
) -> float | None:
    if (
        water_flux_m_s is None
        or salt_flux_kg_m2_s is None
        or length_m is None
    ):
        return None
    return (density_kg_m3 * water_flux_m_s + salt_flux_kg_m2_s) * length_m


def _abs_relative(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0.0:
        return None
    return abs(abs(value) - abs(reference)) / abs(reference)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0.0:
        return None
    return numerator / denominator


def _relative(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0.0:
        return None
    return (value - reference) / reference


def _add(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left + right


def _edge_owners(
    quads: list[tuple[int, int, int, int]],
) -> dict[tuple[int, int], int]:
    owners: dict[tuple[int, int], int] = {}
    for index, quad in enumerate(quads):
        for corner in range(4):
            owners[(quad[corner], quad[(corner + 1) % 4])] = index
    return owners


def _edge_length(nodes: list[tuple[float, float]], first: int, second: int) -> float:
    x0, y0 = nodes[first]
    x1, y1 = nodes[second]
    return math.hypot(x1 - x0, y1 - y0)


def _polygon_area(
    nodes: list[tuple[float, float]],
    quad: tuple[int, int, int, int],
) -> float:
    area = 0.0
    for index in range(4):
        x0, y0 = nodes[quad[index]]
        x1, y1 = nodes[quad[(index + 1) % 4]]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def _wall_distance(
    nodes: list[tuple[float, float]],
    first: int,
    second: int,
    quad: tuple[int, int, int, int],
) -> float:
    length = _edge_length(nodes, first, second)
    if length <= 0.0:
        return 0.0
    x0, y0 = nodes[first]
    x1, y1 = nodes[second]
    centroid = _centroid(nodes, quad)
    return abs((centroid[0] - x0) * (y1 - y0) - (centroid[1] - y0) * (x1 - x0)) / length


def _centroid(
    nodes: list[tuple[float, float]],
    quad: tuple[int, int, int, int],
) -> tuple[float, float]:
    signed = _polygon_area(nodes, quad)
    if signed == 0.0:
        xs = [nodes[index][0] for index in quad]
        ys = [nodes[index][1] for index in quad]
        return (sum(xs) / 4.0, sum(ys) / 4.0)
    cx = 0.0
    cy = 0.0
    for index in range(4):
        x0, y0 = nodes[quad[index]]
        x1, y1 = nodes[quad[(index + 1) % 4]]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return (cx / (6.0 * signed), cy / (6.0 * signed))


def format_membrane_comparison(records: list[Mapping[str, object]]) -> str:
    """Print diagnostic columns. Missing values stay ``undefined``."""
    lines = [
        "2D membrane diagnostics",
        "No acceptance threshold is applied.",
        "",
        _full_source_lines(records),
        "",
        _section(
            records,
            (
                ("cells", "cell_count", False),
                ("L_mem_m", "membrane_length_total_m", True),
                ("faces", "membrane_face_count", True),
                ("adj_cells", "membrane_adjacent_cell_count", True),
                ("multi_face_cells", "membrane_cells_with_multiple_faces", True),
            ),
        ),
        "",
        _section(
            records,
            (
                ("mdot_balance", "mdot_perm_balance_kg_s", True),
                ("mdot_source", "mdot_perm_source_kg_s", True),
                ("mdot_surface", "mdot_perm_surface_kg_s", True),
                ("source_vs_balance", "source_vs_balance_rel", True),
                ("surface_vs_balance", "surface_vs_balance_rel", True),
                ("surface_vs_source", "surface_vs_source_rel", True),
                ("abs_src_bal", "abs_source_vs_balance_rel", True),
                ("abs_srf_bal", "abs_surface_vs_balance_rel", True),
                ("src_over_srf", "source_over_surface", True),
                ("inferred_ramp", "inferred_ramp_from_flux_ratio", True),
            ),
        ),
        "",
        _section(
            records,
            (
                ("Jw_avg", "jw_avg_m_s", True),
                ("Jw_min", "jw_min_m_s", True),
                ("Jw_max", "jw_max_m_s", True),
                ("Jw_top", "jw_top_avg_m_s", True),
                ("Jw_bottom", "jw_bottom_avg_m_s", True),
                ("p_avg", "membrane_pressure_avg_pa", True),
                ("p_top", "membrane_pressure_top_avg_pa", True),
                ("p_bottom", "membrane_pressure_bottom_avg_pa", True),
                ("c_avg", "membrane_concentration_avg_mol_m3", True),
                ("c_top", "membrane_concentration_top_avg_mol_m3", True),
                ("c_bottom", "membrane_concentration_bottom_avg_mol_m3", True),
                ("CP_avg", "cp_average", False),
                ("CP_min", "cp_min", True),
                ("CP_max", "cp_max", True),
            ),
        ),
        "",
        _section(
            records,
            (
                ("AV_min", "area_over_volume_min", True),
                ("AV_mean", "area_over_volume_mean", True),
                ("AV_max", "area_over_volume_max", True),
                ("inv_y_min", "inv_wall_distance_min", True),
                ("inv_y_mean", "inv_wall_distance_mean", True),
                ("inv_y_max", "inv_wall_distance_max", True),
                ("AV_times_y1", "area_over_volume_times_y1_mean", True),
                ("y1_avg", "udm_y1_avg_m", True),
                ("ramp", "source_ramp_factor", True),
                ("LMH", "lmh", False),
                ("dP/L", "pressure_drop_per_length_pa_per_m", False),
            ),
        ),
        "",
        _length_note(records),
    ]
    for record in records:
        phase = _diag_value(record, "divergence_phase")
        if phase is not None:
            lines.append(
                f"{_level_label(record)} divergence_phase={phase}"
            )
        warning = ramp_telemetry_warning(
            _shown_ramp(record),
            _diag_value(record, "inferred_ramp_from_flux_ratio"),
        )
        if warning is not None:
            lines.append(f"{_level_label(record)}: {warning}")
    return "\n".join(lines)


def _shown_ramp(record: Mapping[str, object]) -> float | None:
    recorded = record.get("source_ramp_final")
    if recorded is None:
        recorded = _diag_value(record, "source_ramp_factor")
    iteration = record.get("total_iterations")
    if iteration is None:
        iteration = record.get("solver_iterations")
    return display_ramp(recorded, iteration)


def _full_source_lines(records: list[Mapping[str, object]]) -> str:
    lines = []
    for record in records:
        ramp = _shown_ramp(record)
        lines.append(
            f"{_level_label(record)}: final source ramp={_format_value(ramp)}; "
            "iterations at ramp 1.0="
            f"{_format_value(record.get('full_source_iterations'))}"
        )
    return "\n".join(lines)


def _section(
    records: list[Mapping[str, object]],
    columns: tuple[tuple[str, str, bool], ...],
) -> str:
    header = "mesh".ljust(12) + "".join(name.rjust(16) for name, _key, _nested in columns)
    body = [header]
    for record in records:
        cells = [_level_label(record).ljust(12)]
        for _name, key, nested in columns:
            value = _diag_value(record, key) if nested else record.get(key)
            if key == "source_ramp_factor" and value is None:
                value = _shown_ramp(record)
            cells.append(_format_value(value).rjust(16))
        body.append("".join(cells))
    return "\n".join(body)


def _length_note(records: list[Mapping[str, object]]) -> str:
    lengths = [
        _diag_value(record, "membrane_length_total_m")
        for record in records
    ]
    finite = [value for value in lengths if isinstance(value, (int, float))]
    if len(finite) < 2:
        return "Membrane length comparison needs at least two diagnostic records."
    spread = max(finite) - min(finite)
    if spread > 1.0e-12:
        return (
            "Membrane length differs across mesh levels by "
            f"{spread:.6e} m. This is a geometry or mesh-generation discrepancy."
        )
    return (
        "Membrane length is the same across these mesh levels "
        f"({finite[0]:.12g} m)."
    )


def _diag_value(record: Mapping[str, object], key: str) -> object:
    diagnostics = record.get("membrane_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    return diagnostics.get(key)


def _level_label(record: Mapping[str, object]) -> str:
    level = record.get("mesh_level")
    if isinstance(level, str) and level:
        return level
    fidelity = record.get("fidelity")
    if isinstance(fidelity, str) and fidelity:
        return fidelity
    return "unknown"


def _format_value(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "undefined"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def divergence_phase(transcript: str) -> str | None:
    """Name the divergence markers that are actually in the transcript."""
    lowered = transcript.lower()
    notes: list[str] = []
    if "species-0" in lowered and "amg" in lowered:
        notes.append("species-0 AMG divergence")
    elif "amg divergence" in lowered or "divergence detected" in lowered:
        notes.append("AMG divergence")
    if "floating point exception" in lowered:
        notes.append("floating point exception")
    if not notes:
        return None
    return "; ".join(notes)


def _min(values: list[float]) -> float | None:
    if not values:
        return None
    return min(values)


def _max(values: list[float]) -> float | None:
    if not values:
        return None
    return max(values)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
