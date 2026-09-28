"""Assemble and, on request, write a 2D case plan.

Writing creates JSON specifications only. It does not create a mesh, copy
a UDF, or start a Fluent session. Relative paths inside the plan stay
portable; absolute paths appear only as the destination root passed to
``materialize``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ro.campaign_geometry import CAMPAIGN_H_M
from ro_2d_pilot.config import FIDELITIES, OperatingPoint, PilotConfig
from ro_2d_pilot.geometry import describe_geometry
from ro_2d_pilot.mesh import mesh_spec
from ro_2d_pilot.paths import (
    assert_data_root_allowed,
    campaign_root_from_env,
    geometry_dir,
    mesh_dir,
    relative_geometry_dir,
    relative_mesh_dir,
    relative_run_dir,
    run_dir,
)
from ro_2d_pilot.record import build_result_record
from ro_2d_pilot.udf_gate import physics_record

SCHEMA_VERSION = 1

_STAGES = {
    "specify_geometry": "ready",
    "specify_mesh": "ready",
    "allocate_result": "ready",
    "generate_mesh": "ready",
    "setup_solver": "fluent_only",
    "compile_udf": "fluent_only",
    "solve": "fluent_only",
    "extract_metrics": "fluent_only",
}


def _shared_geometry_fields(geometry: Mapping[str, object]) -> dict[str, object]:
    """Fields that must match across fidelities of one design."""
    keys = (
        "geo_id",
        "d_m",
        "L_m",
        "channel_height_m",
        "n_pitches",
        "length_m",
        "obstacles",
        "boundaries",
        "membrane_area_m2",
        "membrane_blocked_area_frac",
    )
    return {key: geometry[key] for key in keys}


def build_plan(config: PilotConfig) -> dict[str, object]:
    geometry = describe_geometry(config)
    mesh = mesh_spec(config)
    operating = config.operating
    assert operating is not None
    physics = physics_record()
    geo_id = str(geometry["geo_id"])
    fidelity = config.fidelity
    run_id = operating.run_id
    plan: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "geo_id": geo_id,
        "fidelity": fidelity,
        "run_id": run_id,
        "operating": operating.to_dict(),
        "geometry": geometry,
        "mesh": mesh,
        "physics": physics,
        "stages": dict(_STAGES),
        "relative_paths": {
            "geometry_dir": relative_geometry_dir(geo_id),
            "mesh_dir": relative_mesh_dir(geo_id, fidelity),
            "run_dir": relative_run_dir(geo_id, fidelity, run_id),
        },
    }
    plan["result"] = build_result_record(plan)
    return plan


def shared_design_payload(plan: Mapping[str, object]) -> dict[str, object]:
    geometry = plan["geometry"]
    operating = plan["operating"]
    physics = plan["physics"]
    if not isinstance(geometry, Mapping):
        raise TypeError("plan geometry must be a mapping.")
    if not isinstance(operating, Mapping):
        raise TypeError("plan operating must be a mapping.")
    if not isinstance(physics, Mapping):
        raise TypeError("plan physics must be a mapping.")
    return {
        "geometry": _shared_geometry_fields(geometry),
        "operating": dict(operating),
        "physics": {
            "flow": physics["flow"],
            "species": physics["species"],
            "membrane_model": physics["membrane_model"],
            "production_udf": physics["production_udf"],
        },
    }


def plans_for_fidelities(
    *,
    d_m: float,
    L_m: float,
    operating: OperatingPoint | None = None,
    channel_height_m: float | None = None,
    n_pitches: int = 1,
) -> dict[str, dict[str, object]]:
    height = CAMPAIGN_H_M if channel_height_m is None else channel_height_m
    plans: dict[str, dict[str, object]] = {}
    for fidelity in FIDELITIES:
        config = PilotConfig(
            d_m=d_m,
            L_m=L_m,
            fidelity=fidelity,
            operating=operating,
            channel_height_m=height,
            n_pitches=n_pitches,
        )
        plans[fidelity] = build_plan(config)
    return plans


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def materialize(
    plan: Mapping[str, object],
    root: Path,
    *,
    host_os_name: str | None = None,
) -> dict[str, Path]:
    """Write geometry, mesh spec, and an empty result record under ``root``."""
    destination = assert_data_root_allowed(
        root,
        host_os_name=host_os_name,
        campaign_root=campaign_root_from_env(),
    )
    geometry = plan["geometry"]
    mesh = plan["mesh"]
    result = plan["result"]
    if not isinstance(geometry, Mapping) or not isinstance(mesh, Mapping):
        raise TypeError("plan geometry and mesh must be mappings.")
    if not isinstance(result, Mapping):
        raise TypeError("plan result must be a mapping.")
    geo_id = str(plan["geo_id"])
    fidelity = str(plan["fidelity"])
    run_id = str(plan["run_id"])
    written = {
        "geometry": geometry_dir(destination, geo_id) / "geometry.json",
        "mesh": mesh_dir(destination, geo_id, fidelity) / "mesh_spec.json",
        "result": run_dir(destination, geo_id, fidelity, run_id) / "result.json",
    }
    _dump(written["geometry"], dict(geometry))
    _dump(written["mesh"], dict(mesh))
    _dump(written["result"], dict(result))
    return written
