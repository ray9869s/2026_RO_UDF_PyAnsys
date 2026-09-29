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
from ro_2d_pilot.membrane_diag import (
    flux_consistency,
    membrane_geometry_diagnostics,
    source_ramp_factor,
)
from ro_2d_pilot.mesh_build import build_quad_mesh, write_fluent_msh
from ro_2d_pilot.physics import RHO_KG_M3, UDF_FILE_NAME
from ro_2d_pilot.plan import build_plan, materialize
from ro_2d_pilot.record import build_result_record
from ro_2d_pilot.udf_case import write_case_udf


def phase_times(
    *,
    python_prep_s: float | None,
    launch_time_s: float | None,
    mesh_read_time_s: float | None,
    setup_time_s: float | None,
    solve_time_s: float | None,
    extraction_time_s: float | None,
    total_wall_time_s: float | None,
) -> dict[str, float | None]:
    """Split one 2D run. Startup is everything except iterate and reports."""
    measured = (
        python_prep_s,
        launch_time_s,
        mesh_read_time_s,
        setup_time_s,
    )
    if all(part is not None for part in measured):
        startup = float(sum(measured))
    elif (
        total_wall_time_s is not None
        and solve_time_s is not None
        and extraction_time_s is not None
    ):
        startup = float(total_wall_time_s - solve_time_s - extraction_time_s)
    else:
        startup = None
    return {
        "launch_time_s": launch_time_s,
        "mesh_read_time_s": mesh_read_time_s,
        "setup_time_s": setup_time_s,
        "extraction_time_s": extraction_time_s,
        "startup_overhead_s": startup,
        "solver_wall_time_s": solve_time_s,
        "total_wall_time_s": total_wall_time_s,
    }


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
    geometry_diagnostics = membrane_geometry_diagnostics(mesh, config)
    plan["result"] = build_result_record(
        plan,
        _with_membrane_diagnostics(geometry_diagnostics, None),
    )
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
    python_prep_s = None
    try:
        write_case_udf(config, udf_path)
        python_prep_s = time.perf_counter() - started
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
        total_wall_time_s = time.perf_counter() - started
        session_timing = getattr(session, "ro2d_timing", {})
        metrics["cell_count"] = mesh.n_cells
        metrics.update(
            phase_times(
                python_prep_s=python_prep_s,
                launch_time_s=_optional_time(session_timing, "launch_time_s"),
                mesh_read_time_s=_optional_time(session_timing, "mesh_read_time_s"),
                setup_time_s=_optional_time(metrics, "setup_time_s"),
                solve_time_s=_optional_time(metrics, "solver_wall_time_s"),
                extraction_time_s=_optional_time(metrics, "extraction_time_s"),
                total_wall_time_s=total_wall_time_s,
            )
        )
        record = build_result_record(
            plan,
            _with_membrane_diagnostics(geometry_diagnostics, metrics),
        )
        _write_result(written["result"], record)
        return record
    except Exception:
        failed = {
            "cell_count": mesh.n_cells,
            "convergence_status": STOP_REASON_DETERMINATION_FAILED,
            "total_wall_time_s": time.perf_counter() - started,
        }
        if session is not None:
            session_timing = getattr(session, "ro2d_timing", {})
            failed["launch_time_s"] = _optional_time(session_timing, "launch_time_s")
            failed["mesh_read_time_s"] = _optional_time(
                session_timing,
                "mesh_read_time_s",
            )
        _write_result(
            written["result"],
            build_result_record(
                plan,
                _with_membrane_diagnostics(geometry_diagnostics, failed),
            ),
        )
        raise
    finally:
        if session is not None:
            try:
                session.exit()
            except Exception:
                pass


def _with_membrane_diagnostics(
    geometry: dict[str, object],
    metrics: dict[str, object] | None,
) -> dict[str, object]:
    payload = dict(metrics or {})
    solution = payload.pop("membrane_solution", None)
    diagnostics = dict(geometry)
    recorded_ramp = payload.get("source_ramp_final")
    if isinstance(recorded_ramp, bool) or not isinstance(recorded_ramp, (int, float)):
        recorded_ramp = source_ramp_factor(
            payload.get("total_iterations", payload.get("solver_iterations"))
        )
    diagnostics["source_ramp_factor"] = recorded_ramp
    diagnostics["full_source_iterations"] = payload.get("full_source_iterations")
    if isinstance(solution, dict):
        diagnostics.update(solution)
        length = geometry.get("membrane_length_total_m")
        diagnostics.update(
            flux_consistency(
                mass_in_kg_s=_optional_float(solution.get("mass_in_kg_s")),
                mass_out_kg_s=_optional_float(solution.get("mass_out_kg_s")),
                source_integral_kg_s=_optional_float(
                    solution.get("source_integral_kg_s")
                ),
                water_flux_avg_m_s=_optional_float(solution.get("jw_avg_m_s")),
                salt_flux_avg_kg_m2_s=_optional_float(
                    solution.get("salt_flux_avg_kg_m2_s")
                ),
                membrane_length_m=_optional_float(length),
                density_kg_m3=RHO_KG_M3,
            )
        )
    payload["membrane_diagnostics"] = diagnostics
    return payload


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_time(payload: object, key: str) -> float | None:
    if not isinstance(payload, dict) or key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _write_result(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
