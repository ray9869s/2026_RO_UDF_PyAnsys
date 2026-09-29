"""Mesh one 2D case and, unless ``dry_run``, solve it.

``dry_run`` writes the mesh and the JSON records and leaves every metric
null, so validity stays ``not_run``. A Fluent failure after the mesh
exists still writes an invalid result, then re-raises.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ro.solver_common import STOP_REASON_DETERMINATION_FAILED
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.fluent_session import (
    open_solver_session,
    resolve_max_iterations,
    solve_case,
)
from ro_2d_pilot.mesh_build import build_quad_mesh, write_fluent_msh
from ro_2d_pilot.physics import UDF_FILE_NAME
from ro_2d_pilot.plan import build_plan, materialize
from ro_2d_pilot.record import build_result_record
from ro_2d_pilot.udf_case import write_case_udf


def run_case(
    config: PilotConfig,
    root: Path,
    *,
    dry_run: bool = False,
    max_iterations: int | None = None,
    launcher=None,
) -> dict[str, object]:
    started = time.perf_counter()
    plan = build_plan(config)
    mesh = build_quad_mesh(config)
    plan_mesh = plan["mesh"]
    if not isinstance(plan_mesh, dict):
        raise TypeError("plan mesh must be a dict.")
    plan_mesh["cell_count"] = mesh.n_cells
    plan_mesh["node_count"] = mesh.n_nodes
    plan_mesh["actual_max_edge_m"] = mesh.actual_max_edge_m
    plan_mesh["actual_min_edge_m"] = mesh.actual_min_edge_m
    plan_mesh["n_side"] = mesh.n_side
    plan_mesh["n_radial"] = mesh.n_radial
    plan_mesh["boundary_edge_counts"] = dict(mesh.counts)
    plan["result"] = build_result_record(plan)
    written = materialize(plan, root)
    mesh_path = written["mesh"].with_name(
        f"{plan['geo_id']}_{plan['fidelity']}.msh"
    )
    write_fluent_msh(mesh, mesh_path)
    if dry_run:
        return dict(plan["result"])

    iterations = resolve_max_iterations(max_iterations)
    udf_path = written["result"].parent / UDF_FILE_NAME
    session = None
    try:
        write_case_udf(config, udf_path)
        session = open_solver_session(
            cwd=udf_path.parent,
            mesh_path=mesh_path,
            launcher=launcher,
        )
        metrics = solve_case(
            session,
            config=config,
            udf_path=udf_path,
            max_iterations=iterations,
        )
        metrics["cell_count"] = mesh.n_cells
        metrics["total_wall_time_s"] = time.perf_counter() - started
        record = build_result_record(plan, metrics)
        _write_result(written["result"], record)
        return record
    except Exception:
        _write_result(
            written["result"],
            build_result_record(
                plan,
                {
                    "cell_count": mesh.n_cells,
                    "convergence_status": STOP_REASON_DETERMINATION_FAILED,
                    "total_wall_time_s": time.perf_counter() - started,
                },
            ),
        )
        raise
    finally:
        if session is not None:
            try:
                session.exit()
            except Exception:
                pass


def _write_result(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
