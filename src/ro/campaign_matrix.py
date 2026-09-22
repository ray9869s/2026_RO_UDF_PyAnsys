"""Production 31-mesh / 279-run matrix. Exploratory batch lists stay separate.

Two constraints:

1. Per-geometry layout is read from the registry
   (``geometry_parameters_for_geo_id``), never from ``_COMMON_MESH``.
   ``_COMMON_MESH`` carries ``n_active_cells = 7`` and
   ``cell_length_x_m = 0.003465``. Config wins over registry on the merge
   path, so copying that dict would stamp 7 onto every Diamond case.
   Diamond's real ``n_active_cells`` spans 5 to 27.

2. ``mesh_batch_cases`` and ``solver_sweep_cases`` are the exploratory
   lists (22 meshes, no Diamond; currently 4 u0p3-from-u0p2 restart
   solver cases). Production is
   ``production_mesh_batch_cases`` / ``production_solver_sweep_cases``,
   reached only via ``--case-set production``. Default CLI remains
   exploratory so existing batch scripts cannot launch 279 solves.

This generator selects the 31 campaign mesh leaves and attaches the 9
operating points. It does not remesh. ``mesh_sha256`` is not invented
here; skip/preflight attach it when ``RO_DATA_ROOT`` is present.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ro.campaign_geo_ids import CAMPAIGN_GEO_IDS, family_for_geo_id
from ro.campaign_geometry import geometry_parameters_for_geo_id
from ro.solver_common import make_base_case_name

# Keys that must never be inherited from a common-mesh template.
# _COMMON_MESH values are the D2450_a45 7-cell / 3.465 mm pitch layout.
LAYOUT_KEYS_NOT_FROM_COMMON = frozenset(
    {
        "n_active_cells",
        "cell_length_x_m",
        "periodic_shift_y",
    }
)

CASE_SET_EXPLORATORY = "exploratory"
CASE_SET_PRODUCTION = "production"
CASE_SET_CHOICES = (CASE_SET_EXPLORATORY, CASE_SET_PRODUCTION)

EXPECTED_PRODUCTION_MESH_COUNT = 31
EXPECTED_PRODUCTION_SOLVER_COUNT = 279
EXPECTED_OPERATING_POINTS_PER_GEO = 9

PRODUCTION_INLET_VELOCITY_MS = (0.1, 0.2, 0.3)
PRODUCTION_OUTLET_GAUGE_PRESSURE_PA = (4.0e6, 6.0e6, 8.0e6)

PRODUCTION_M_MAX_DEFAULT = 0.085
PRODUCTION_M_MAX_BY_GEO_ID = {"D0817_a60": 0.060}

PRODUCTION_MESH_ID_DEFAULT = "max085_min006_cpg5_bl4_peel2"
PRODUCTION_MESH_ID_D0817_A60 = "max060_min006_cpg5_bl4_peel2"


def production_m_max_for_geo_id(geo_id: str) -> float:
    return PRODUCTION_M_MAX_BY_GEO_ID.get(geo_id, PRODUCTION_M_MAX_DEFAULT)


def format_production_mesh_id(
    *,
    m_max: float,
    m_min: float,
    m_cpg: int,
    bl_layers: int,
    peel_layers: int,
) -> str:
    return (
        f"max{int(round(float(m_max) * 1000.0)):03d}"
        f"_min{int(round(float(m_min) * 1000.0)):03d}"
        f"_cpg{int(m_cpg)}"
        f"_bl{int(bl_layers)}"
        f"_peel{int(peel_layers)}"
    )


def production_mesh_id_for_geo_id(
    geo_id: str,
    *,
    m_min: float,
    m_cpg: int,
    bl_layers: int,
    peel_layers: int,
) -> str:
    return format_production_mesh_id(
        m_max=production_m_max_for_geo_id(geo_id),
        m_min=m_min,
        m_cpg=m_cpg,
        bl_layers=bl_layers,
        peel_layers=peel_layers,
    )


def mesh_settings_without_layout(common_mesh: Mapping[str, Any]) -> dict[str, Any]:
    """Copy common mesh knobs, dropping layout keys that belong to the registry."""
    return {
        key: value
        for key, value in common_mesh.items()
        if key not in LAYOUT_KEYS_NOT_FROM_COMMON
    }


def _layout_from_registry(geo_id: str) -> dict[str, Any]:
    geometry = geometry_parameters_for_geo_id(geo_id)
    return {
        "n_active_cells": geometry["n_active_cells"],
        "cell_length_x_m": geometry["cell_length_x_m"],
        # Fluent meshing consumes periodic_shift_y in millimetres.
        "periodic_shift_y": float(geometry["periodic_shift_y_m"]) * 1.0e3,
        "spacing_code": geometry["spacing_code"],
        "filament_d_m": geometry["filament_d_m"],
        "bridge_radius_m": geometry["bridge_radius_m"],
        "overlap_m": geometry["overlap_m"],
        "wall_spacer_labels": list(geometry["spacer_wall_zones"]),
        "attack_angle_deg": geometry["attack_angle_deg"],
    }


def build_production_mesh_batch_cases(
    common_mesh: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One meshing dict per campaign geo_id. Layout always from the registry."""
    stripped = mesh_settings_without_layout(common_mesh)
    cases: list[dict[str, Any]] = []
    for geo_id in sorted(CAMPAIGN_GEO_IDS):
        family = family_for_geo_id(geo_id)
        layout = _layout_from_registry(geo_id)
        case = dict(stripped)
        case["family"] = family
        case["geo_id"] = geo_id
        case["n_active_cells"] = layout["n_active_cells"]
        case["cell_length_x_m"] = layout["cell_length_x_m"]
        case["periodic_shift_y"] = layout["periodic_shift_y"]
        case["spacing_code"] = layout["spacing_code"]
        case["filament_d_m"] = layout["filament_d_m"]
        case["bridge_radius_m"] = layout["bridge_radius_m"]
        case["overlap_m"] = layout["overlap_m"]
        case["wall_spacer_labels"] = layout["wall_spacer_labels"]
        # Pillar registry angle is 0; exploratory meshes used _COMMON_MESH 45
        # (Astra A-02). Do not change that here. ML exploratory meshes also
        # used 45 from the common template; leave it. Diamond / sin / empty
        # take the registry angle (Diamond 30/45/60; sin/empty 0).
        if family not in ("ml", "pillar"):
            case["attack_angle_deg"] = layout["attack_angle_deg"]
        case["m_max"] = production_m_max_for_geo_id(geo_id)
        case["mesh_id"] = production_mesh_id_for_geo_id(
            geo_id,
            m_min=case["m_min"],
            m_cpg=case["m_cpg"],
            bl_layers=case["bl_layers"],
            peel_layers=case["peel_layers"],
        )
        cases.append(case)
    _assert_production_mesh_cases(cases)
    return cases


def build_production_solver_sweep_cases(
    mesh_cases: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the 9 (u, p) operating points to each selected mesh leaf."""
    solver_cases: list[dict[str, Any]] = []
    for mesh in mesh_cases:
        geo_id = mesh["geo_id"]
        mesh_id = mesh["mesh_id"]
        family = mesh["family"]
        for inlet_velocity_value in PRODUCTION_INLET_VELOCITY_MS:
            for outlet_gauge_pressure in PRODUCTION_OUTLET_GAUGE_PRESSURE_PA:
                run_id = make_base_case_name(
                    inlet_velocity_value,
                    outlet_gauge_pressure,
                )
                solver_cases.append(
                    {
                        "family": family,
                        "geo_id": geo_id,
                        "mesh_id": mesh_id,
                        "run_id": run_id,
                        "geo_name": geo_id,
                        "case_name": run_id,
                        "inlet_velocity_value": inlet_velocity_value,
                        "outlet_gauge_pressure": outlet_gauge_pressure,
                    }
                )
    _assert_production_solver_cases(mesh_cases, solver_cases)
    return solver_cases


def cases_for_case_set(
    batchcfg,
    case_set: str,
    *,
    exploratory_attr: str,
    production_attr: str,
) -> list[Any]:
    """Return exploratory or production case list. Default callers pass exploratory."""
    if case_set == CASE_SET_PRODUCTION:
        if not hasattr(batchcfg, production_attr):
            raise ValueError(
                f"{production_attr} is missing from batch_config; "
                "the production matrix was not generated."
            )
        return list(getattr(batchcfg, production_attr))
    if case_set == CASE_SET_EXPLORATORY:
        return list(getattr(batchcfg, exploratory_attr, []))
    raise ValueError(
        f"Unknown case-set {case_set!r}. Expected one of {CASE_SET_CHOICES}."
    )


def filter_cases_by_geo_id(cases, geo_ids):
    """Keep case-set order. Empty/None geo_ids leaves the list unchanged."""
    if not geo_ids:
        return list(cases)
    requested = list(dict.fromkeys(geo_ids))
    wanted = set(requested)
    selected = [case for case in cases if case["geo_id"] in wanted]
    found = {case["geo_id"] for case in selected}
    missing = [geo_id for geo_id in requested if geo_id not in found]
    if missing:
        available = sorted({case["geo_id"] for case in cases})
        raise ValueError(
            "--geo-id not in the selected case-set: "
            f"{missing}. available={available}."
        )
    return selected


def filter_cases_by_outlet_gauge_pressure(cases, pressures):
    """Keep case-set order. Empty/None pressures leaves the list unchanged."""
    if not pressures:
        return list(cases)
    requested = list(dict.fromkeys(pressures))
    wanted = set(requested)
    selected = [
        case for case in cases if case["outlet_gauge_pressure"] in wanted
    ]
    found = {case["outlet_gauge_pressure"] for case in selected}
    missing = [pressure for pressure in requested if pressure not in found]
    if missing:
        available = sorted(
            {case["outlet_gauge_pressure"] for case in cases}
        )
        raise ValueError(
            "--outlet-gauge-pressure not in the selected case-set: "
            f"{missing}. available={available}."
        )
    return selected


def _assert_production_mesh_cases(cases: Sequence[Mapping[str, Any]]) -> None:
    assert len(cases) == EXPECTED_PRODUCTION_MESH_COUNT, (
        f"Expected {EXPECTED_PRODUCTION_MESH_COUNT} production meshes, "
        f"got {len(cases)}."
    )
    geo_ids = [case["geo_id"] for case in cases]
    assert set(geo_ids) == set(CAMPAIGN_GEO_IDS)
    assert len(geo_ids) == len(set(geo_ids))
    mesh_ids = {case["mesh_id"] for case in cases}
    assert mesh_ids == {
        PRODUCTION_MESH_ID_DEFAULT,
        PRODUCTION_MESH_ID_D0817_A60,
    }
    for case in cases:
        geo_id = case["geo_id"]
        expected_m_max = production_m_max_for_geo_id(geo_id)
        assert case["m_max"] == expected_m_max
        if geo_id == "D0817_a60":
            assert case["mesh_id"] == PRODUCTION_MESH_ID_D0817_A60
        else:
            assert case["mesh_id"] == PRODUCTION_MESH_ID_DEFAULT


def _assert_production_solver_cases(
    mesh_cases: Sequence[Mapping[str, Any]],
    solver_cases: Sequence[Mapping[str, Any]],
) -> None:
    assert len(solver_cases) == EXPECTED_PRODUCTION_SOLVER_COUNT, (
        f"Expected {EXPECTED_PRODUCTION_SOLVER_COUNT} production solver cases, "
        f"got {len(solver_cases)}."
    )
    keys = [(case["geo_id"], case["mesh_id"], case["run_id"]) for case in solver_cases]
    assert len(keys) == len(set(keys))
    by_geo: dict[str, int] = {}
    mesh_id_by_geo = {case["geo_id"]: case["mesh_id"] for case in mesh_cases}
    for case in solver_cases:
        geo_id = case["geo_id"]
        by_geo[geo_id] = by_geo.get(geo_id, 0) + 1
        assert case["mesh_id"] == mesh_id_by_geo[geo_id]
        assert case["run_id"] == make_base_case_name(
            case["inlet_velocity_value"],
            case["outlet_gauge_pressure"],
        )
        assert case["case_name"] == case["run_id"]
    assert set(by_geo) == set(CAMPAIGN_GEO_IDS)
    assert all(count == EXPECTED_OPERATING_POINTS_PER_GEO for count in by_geo.values())
