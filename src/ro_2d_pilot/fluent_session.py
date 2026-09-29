"""One 2D Fluent session for a single pilot case.

Direct 2D solver launch reaches the welcome banner and then aborts
with ``Failed to construct hwtree for collect command``. PyFluent
keeps waiting on that dead process. This pilot therefore starts in
meshing mode with ``gui`` and ``dx11``, which does start on this host.
The gRPC attribute ``tui.switch_to_solution_mode`` is not a live 2D
menu, and datamodel ``SwitchToSolution`` calls ``S_SwitchToSolution``,
which needs the workflow binding ``%tg-get-thread-of-class``. The
switch uses the console text command ``/switch-to-solution-mode yes``
through scheme ``ti-menu-load-string``, then attaches a solver session
to the same process. The algebraic ``.msh`` is read after that with
``settings.file.read_mesh``. Meshing ``File.ReadMesh`` is not used.

PyFluent is imported only while launching. Launch, switch, and
mesh-read failures are separate exceptions. Species setup fails by
step name when the live settings tree does not match this session.

The solved inlet is the 2D Poiseuille profile in ``260929_RO_UDF.c``.
A magnitude plug is written only so the boundary exists before the
library is loaded, and the profile replace is required before iterate.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Mapping

from ro.solver_common import (
    STOP_REASON_DIVERGED,
    STOP_REASON_QOI_CONVERGED,
    STOP_REASON_RESIDUAL_CONVERGED,
    classify_solver_stop_reason,
    parse_fluent_convergence_marker,
    parse_last_residual_iteration_from_transcript_text,
    path_to_fluent_str,
)
from ro_2d_pilot.config import PilotConfig
from ro_2d_pilot.membrane_diag import divergence_phase
from ro_2d_pilot.source_schedule import (
    CONVERGED_BEFORE_FULL_SOURCE,
    IterateRequest,
    ScheduleResult,
    observation_from_transcript,
    run_source_schedule,
)
from ro_2d_pilot.geometry import membrane_area_m2
from ro_2d_pilot.physics import (
    ADJUST_UDF,
    INIT_UDF,
    INLET_PROFILE_UDF,
    MASS_DIFFUSIVITY_M2_S,
    MAX_ITERATIONS,
    MU_KG_M_S,
    OPERATING_PRESSURE_PA,
    PROBE_UDF,
    RESIDUAL_TARGET,
    RHO_KG_M3,
    SALT_MASS_FRACTION,
    SOURCE_UDFS,
    SPECIES_NAME,
    UDF_LIBRARY,
    UDM_COUNT,
)
from ro_2d_pilot.record import (
    lmh_from_mass_balance,
    mass_balance_relative_error,
    permeate_mass_flow_kg_s,
)

# Fluent material molecular weight is kg/kmol, which is 1000 * kg/mol.
_NACL_MW_KG_KMOL = 58.44
_DIVERGENCE_MARKERS = (
    "divergence detected",
    "amg divergence",
    "floating point exception",
)
_RESIDUAL_EQUATIONS = (
    "continuity",
    "x-velocity",
    "y-velocity",
    "z-velocity",
    SPECIES_NAME,
)
_REQUIRED_RESIDUALS = ("continuity", "x-velocity", "y-velocity")
_CELL_COUNT_RES = (
    re.compile(
        r"Level\s+Cells\s+Faces\s+Nodes\s+Partitions\s+"
        r"0\s+(\d[\d,]*)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(\d[\d,]*)\s+cells\b", re.IGNORECASE),
    re.compile(r"\bcells\s*[:=]\s*(\d[\d,]*)", re.IGNORECASE),
)
_DEFAULT_START_TIMEOUT_S = 300
# Classic solver file/read-mesh. Meshing File.ReadMesh is S_FileReadMesh
# and requires the size function %tg-size-func-bgrid, which this pilot
# never creates.
MESH_READ_BACKEND = "solver.settings.file.read_mesh"
# Console text menu, not the gRPC TUI attribute and not S_SwitchToSolution.
SWITCH_COMMAND = "/switch-to-solution-mode yes"
SWITCH_BACKEND = "scheme ti-menu-load-string /switch-to-solution-mode yes"


class FluentUnavailable(RuntimeError):
    """Fluent could not be imported or launched on this machine."""


class FluentSetupError(RuntimeError):
    """One named setup or extraction step failed. The case was not treated as valid."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        super().__init__(f"{step}: {message}")


class FluentMeshReadError(FluentSetupError):
    """The solver session could not read or verify the 2D ``.msh``."""

    def __init__(self, message: str) -> None:
        super().__init__("read_mesh", message)


class FluentSwitchToSolverError(FluentSetupError):
    """The text-menu switch out of 2D meshing mode failed."""

    def __init__(self, message: str) -> None:
        super().__init__("switch_to_solver", message)


def resolve_max_iterations(explicit: int | None = None) -> int:
    if explicit is not None:
        if explicit < 1:
            raise ValueError(f"max_iterations must be >= 1, got {explicit!r}.")
        return int(explicit)
    raw = os.environ.get("RO_2D_MAX_ITERATIONS")
    if raw:
        return resolve_max_iterations(int(raw))
    return MAX_ITERATIONS


def first_report_number(payload: object) -> float:
    """Return the first finite number in a PyFluent ``compute()`` payload."""
    found = _walk_number(payload)
    if found is None:
        raise ValueError(f"No numeric report value in {payload!r}.")
    return found


def classify_transcript(
    text: str,
    *,
    max_iterations: int,
    accept_convergence_after: int | None = None,
) -> dict[str, object]:
    """Map a Fluent transcript to the campaign stop-reason vocabulary.

    ``accept_convergence_after`` drops residual and QoI stop lines at or
    before that iteration. The 2D ramp schedule sets it to the last
    settling iteration so an early residual stop cannot classify the case.
    """
    lowered = text.lower()
    diverged = any(marker in lowered for marker in _DIVERGENCE_MARKERS)
    marker_reason, marker_iteration = parse_fluent_convergence_marker(
        text,
        after_iteration=accept_convergence_after,
    )
    iteration = parse_last_residual_iteration_from_transcript_text(text)
    if iteration is None:
        iteration = marker_iteration
    status = classify_solver_stop_reason(
        diverged=diverged,
        residuals_met=marker_reason == STOP_REASON_RESIDUAL_CONVERGED,
        qoi_met=marker_reason == STOP_REASON_QOI_CONVERGED,
        final_iteration=iteration,
        max_iterations=max_iterations,
        calculation_ran=True,
    )
    return {
        "convergence_status": status,
        "solver_iterations": iteration,
        "convergence_iteration": (
            marker_iteration if marker_reason is not None else None
        ),
    }


def meshing_launch_kwargs(
    *,
    cwd: Path,
    processor_count: int | None = None,
    product_version: str | None = None,
    start_timeout: int | None = None,
) -> dict[str, object]:
    """Arguments for a 2D meshing session that can switch by text TUI.

    ``mode`` is ``"meshing"``. Direct ``"solver"`` launch aborts on this
    host while constructing ``hwtree``. ``ui_mode`` is ``gui`` and
    ``graphics_driver`` is ``dx11``.
    """
    version = product_version or os.environ.get(
        "RO_2D_FLUENT_PRODUCT_VERSION",
        "25.1.0",
    )
    if processor_count is None:
        processor_count = int(os.environ.get("RO_2D_PROCESSOR_COUNT", "1"))
    if start_timeout is None:
        raw_timeout = os.environ.get("RO_2D_FLUENT_START_TIMEOUT")
        start_timeout = (
            int(raw_timeout) if raw_timeout else _DEFAULT_START_TIMEOUT_S
        )
    if start_timeout < 1:
        raise ValueError(f"start_timeout must be >= 1, got {start_timeout!r}.")
    return {
        "product_version": version,
        "dimension": 2,
        "mode": "meshing",
        "precision": "double",
        "processor_count": processor_count,
        "ui_mode": "gui",
        "graphics_driver": os.environ.get(
            "RO_2D_FLUENT_GRAPHICS_DRIVER",
            "dx11",
        ),
        "start_timeout": start_timeout,
        "cwd": str(cwd),
    }


def launch_meshing_session(
    *,
    cwd: Path,
    processor_count: int | None = None,
    product_version: str | None = None,
    start_timeout: int | None = None,
    launcher=None,
):
    """Launch Fluent in 2D meshing mode. ``launcher`` is for tests."""
    kwargs = meshing_launch_kwargs(
        cwd=cwd,
        processor_count=processor_count,
        product_version=product_version,
        start_timeout=start_timeout,
    )
    if kwargs["mode"] != "meshing":
        raise FluentUnavailable(
            "2D pilot must launch FluentMode.MESHING, "
            f"got {kwargs['mode']!r}."
        )
    start = _default_launcher if launcher is None else launcher
    try:
        return start(**kwargs)
    except FluentUnavailable:
        raise
    except Exception as exc:
        raise FluentUnavailable(
            "Fluent 2D meshing launch failed. "
            f"mode={kwargs['mode']} dimension={kwargs['dimension']} "
            f"ui_mode={kwargs['ui_mode']} "
            f"graphics_driver={kwargs['graphics_driver']} "
            f"start_timeout={kwargs['start_timeout']}. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc


def open_solver_session(
    *,
    cwd: Path,
    mesh_path: Path,
    processor_count: int | None = None,
    product_version: str | None = None,
    start_timeout: int | None = None,
    launcher=None,
):
    """Launch 2D meshing, switch with the text menu, read, and mesh-check.

    Direct solver launch is not used. On this host it aborts with
    ``Failed to construct hwtree for collect command`` and the PyFluent
    client waits until the health timeout.
    """
    meshing = None
    solver = None
    handed_off = False
    try:
        launch_started = time.perf_counter()
        meshing = launch_meshing_session(
            cwd=cwd,
            processor_count=processor_count,
            product_version=product_version,
            start_timeout=start_timeout,
            launcher=launcher,
        )
        launch_time_s = time.perf_counter() - launch_started
        read_started = time.perf_counter()
        solver = _switch_to_solver(meshing)
        _read_and_verify_mesh(solver, mesh_path, transcript_dir=cwd)
        _solver_mesh_check(solver)
        solver.ro2d_timing = {
            "launch_time_s": launch_time_s,
            "mesh_read_time_s": time.perf_counter() - read_started,
        }
        handed_off = True
        return solver
    finally:
        if not handed_off:
            if solver is not None:
                _exit_quietly(solver)
            elif meshing is not None:
                _exit_quietly(meshing)


def solve_case(
    solver,
    *,
    config: PilotConfig,
    udf_path: Path,
    max_iterations: int,
) -> dict[str, object]:
    """Set up and solve a mesh already loaded by ``open_solver_session``."""
    setup_started = time.perf_counter()
    setup = solver.settings.setup
    solution = solver.settings.solution
    _require_zones(setup)
    _step("steady", lambda: _set(setup.general.solver.time, "steady"))
    _step("laminar", lambda: _set(setup.models.viscous.model, "laminar"))
    _step("energy_off", lambda: _disable_energy(setup))
    _step("mixture", lambda: _configure_mixture(setup))
    _step(
        "operating_pressure",
        lambda: _set(
            setup.general.operating_conditions.operating_pressure,
            OPERATING_PRESSURE_PA,
        ),
    )
    operating = config.operating
    assert operating is not None
    _step(
        "boundaries",
        lambda: _set_boundaries(
            setup,
            inlet_velocity_m_s=operating.inlet_velocity_m_s,
            outlet_gauge_pressure_pa=operating.outlet_gauge_pressure_pa,
        ),
    )
    _step(
        "user_defined_memory",
        lambda: solver.execute_tui(
            f"/define/user-defined/user-defined-memory {UDM_COUNT}"
        ),
    )
    _step(
        "compile_udf",
        lambda: solver.tui.define.user_defined.compiled_functions(
            "compile",
            UDF_LIBRARY,
            "yes",
            path_to_fluent_str(udf_path),
            "",
            "",
        ),
    )
    _step(
        "load_udf",
        lambda: solver.tui.define.user_defined.compiled_functions(
            "load",
            UDF_LIBRARY,
        ),
    )
    _step(
        "inlet_probe",
        lambda: solver.execute_tui(
            f'/define/user-defined/execute-on-demand "{PROBE_UDF}"'
        ),
    )
    _step(
        "inlet_profile",
        lambda: _attach_inlet_profile(setup),
    )
    _step(
        "udf_hooks",
        lambda: _hook_functions(solver),
    )
    _step("source_terms", lambda: _hook_sources(setup))
    _step(
        "initialize",
        lambda: solution.initialization.standard_initialize(),
    )
    _step("species_patch", lambda: _patch_species(solution))
    _step(
        "residuals",
        lambda: _set_residuals(solution, RESIDUAL_TARGET),
    )
    setup_time_s = time.perf_counter() - setup_started
    started = time.perf_counter()
    schedule = _step(
        "iterate",
        lambda: _run_source_schedule(
            solution,
            solver,
            udf_path.parent,
            max_iterations,
        ),
    )
    solver_wall_time_s = time.perf_counter() - started
    extract_started = time.perf_counter()
    transcript = collect_transcript(udf_path.parent, solver)
    classified = _classify_scheduled(
        transcript,
        max_iterations=max_iterations,
        schedule=schedule,
    )
    if classified["convergence_status"] == STOP_REASON_DIVERGED:
        return _session_metrics(
            _with_divergence_note(classified, transcript),
            setup_time_s=setup_time_s,
            solver_wall_time_s=solver_wall_time_s,
            extraction_time_s=None,
        )
    try:
        metrics = _step(
            "extract_reports",
            lambda: _extract_reports(solver, config),
        )
    except Exception:
        transcript = collect_transcript(udf_path.parent, solver)
        classified = _classify_scheduled(
            transcript,
            max_iterations=max_iterations,
            schedule=schedule,
        )
        if classified["convergence_status"] == STOP_REASON_DIVERGED:
            return _session_metrics(
                _with_divergence_note(classified, transcript),
                setup_time_s=setup_time_s,
                solver_wall_time_s=solver_wall_time_s,
                extraction_time_s=time.perf_counter() - extract_started,
            )
        raise
    metrics.update(classified)
    return _session_metrics(
        metrics,
        setup_time_s=setup_time_s,
        solver_wall_time_s=solver_wall_time_s,
        extraction_time_s=time.perf_counter() - extract_started,
    )


def _run_source_schedule(solution, solver, run_dir: Path, max_iterations: int):
    def iterate(request: IterateRequest) -> None:
        _set_convergence_checks(solution, request.check_convergence)
        solution.run_calculation.iterate(iter_count=request.count)

    def observe():
        text = collect_transcript(run_dir, solver)
        return observation_from_transcript(
            text,
            diverged=_transcript_diverged(text),
        )

    return run_source_schedule(iterate, observe, max_iterations)


def _classify_scheduled(
    transcript: str,
    *,
    max_iterations: int,
    schedule: ScheduleResult,
) -> dict[str, object]:
    classified = classify_transcript(
        transcript,
        max_iterations=max_iterations,
        accept_convergence_after=schedule.accept_convergence_after,
    )
    classified.update(_schedule_fields(schedule))
    if schedule.stalled and not schedule.convergence_checked_after_full_source:
        classified["validity_reason"] = CONVERGED_BEFORE_FULL_SOURCE
    return classified


def _schedule_fields(schedule: ScheduleResult) -> dict[str, object]:
    return {
        "source_ramp_final": schedule.source_ramp_final,
        "full_source_reached": schedule.full_source_reached,
        "full_source_start_iteration": schedule.full_source_start_iteration,
        "full_source_iterations": schedule.full_source_iterations,
        "convergence_checked_after_full_source": (
            schedule.convergence_checked_after_full_source
        ),
        "total_iterations": schedule.completed_iterations,
    }


def _transcript_diverged(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _DIVERGENCE_MARKERS)


def _set_convergence_checks(solution, enabled: bool) -> None:
    equations = solution.monitor.residual.equations
    available = list(equations.get_state().keys())
    for name in _RESIDUAL_EQUATIONS:
        if name not in available:
            continue
        equations[name].check_convergence = enabled


def _with_divergence_note(
    classified: dict[str, object],
    transcript: str,
) -> dict[str, object]:
    payload = dict(classified)
    payload["membrane_solution"] = {
        "divergence_phase": divergence_phase(transcript),
    }
    return payload


def _session_metrics(
    metrics: dict[str, object],
    *,
    setup_time_s: float,
    solver_wall_time_s: float,
    extraction_time_s: float | None,
) -> dict[str, object]:
    finished = dict(metrics)
    finished["setup_time_s"] = setup_time_s
    finished["solver_wall_time_s"] = solver_wall_time_s
    finished["extraction_time_s"] = extraction_time_s
    return finished


def collect_transcript(run_dir: Path, solver) -> str:
    parts: list[str] = []
    seen: set[Path] = set()
    transcript = getattr(solver, "transcript", None)
    for attribute in ("filepath", "path", "filename"):
        raw = getattr(transcript, attribute, None)
        if not raw:
            continue
        path = Path(str(raw))
        if path.is_file() and path not in seen:
            seen.add(path)
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    for path in sorted(run_dir.glob("*.trn")):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _default_launcher(**kwargs):
    try:
        import ansys.fluent.core as pyfluent
    except ImportError as exc:
        raise FluentUnavailable(
            "ansys-fluent-core is not importable in this interpreter."
        ) from exc
    mode = kwargs.get("mode")
    if mode in {"solver", pyfluent.FluentMode.SOLVER}:
        raise FluentUnavailable(
            "2D pilot must not launch FluentMode.SOLVER directly. "
            "This host aborts with: Failed to construct hwtree for "
            "collect command. Launch FluentMode.MESHING."
        )
    if mode in {"pure_meshing", pyfluent.FluentMode.PURE_MESHING}:
        raise FluentUnavailable(
            "PURE_MESHING cannot enter solution mode. "
            "Launch FluentMode.MESHING."
        )
    if mode not in {"meshing", pyfluent.FluentMode.MESHING}:
        raise FluentUnavailable(
            "2D pilot must launch FluentMode.MESHING, "
            f"got {mode!r}."
        )
    kwargs["mode"] = pyfluent.FluentMode.MESHING
    kwargs["precision"] = pyfluent.Precision.DOUBLE
    pyfluent.config.check_health_timeout = int(kwargs["start_timeout"])
    return pyfluent.launch_fluent(**kwargs)


def _exit_quietly(session) -> None:
    try:
        session.exit()
    except Exception:
        return


def _switch_context(session) -> str:
    return (
        f"backend={SWITCH_BACKEND} "
        f"pyfluent={_pyfluent_version()} "
        f"fluent={_fluent_version(session)}"
    )


def _scheme_result_is_error(value: object) -> bool:
    text = str(value).lower()
    return (
        "error:" in text
        or "menu not found" in text
        or "null pointer" in text
    )


def _switch_to_solver(meshing):
    """Enter solution mode with the console command, then wrap Solver.

    ``tui.switch_to_solution_mode`` asks the gRPC menu tree and is
    missing in 2D. ``SwitchToSolution`` is the workflow command and
    needs ``%tg-get-thread-of-class``. ``ti-menu-load-string`` sends
    the same text a console user would type. Test doubles may set
    ``build_solver``.
    """
    context = _switch_context(meshing)
    connection = getattr(meshing, "_fluent_connection", None)
    if connection is None:
        raise FluentSwitchToSolverError(
            f"{context} meshing session has no Fluent connection."
        )
    expression = f"(ti-menu-load-string {json.dumps(SWITCH_COMMAND)})"
    try:
        for callback in list(getattr(connection, "finalizer_cbs", ())):
            callback()
        result = meshing.scheme.eval(expression)
        if _scheme_result_is_error(result):
            raise FluentSwitchToSolverError(
                f"{context} scheme result={result!r}"
            )
        builder = getattr(meshing, "build_solver", None)
        if builder is not None:
            solver = builder(connection)
        else:
            solver = _make_solver_session(
                connection,
                meshing.scheme,
                getattr(meshing, "_file_transfer_service", None),
            )
    except FluentSwitchToSolverError:
        raise
    except Exception as exc:
        raise FluentSwitchToSolverError(
            f"{context} {type(exc).__name__}: {exc}"
        ) from exc
    meshing._fluent_connection = None
    print(f"2D switch to solver succeeded: {context}", flush=True)
    return solver


def _make_solver_session(connection, scheme_eval, file_transfer_service):
    from ansys.fluent.core.session_solver import Solver

    return Solver(
        fluent_connection=connection,
        scheme_eval=scheme_eval,
        file_transfer_service=file_transfer_service,
    )


def _pyfluent_version() -> str:
    try:
        from importlib.metadata import version

        return version("ansys-fluent-core")
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"


def _fluent_version(session) -> str:
    try:
        return str(session.get_fluent_version())
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"


def _mesh_file_facts(mesh_path: Path) -> str:
    path = Path(mesh_path)
    if path.is_file():
        return f"exists=True size_bytes={path.stat().st_size}"
    return "exists=False size_bytes=None"


def _mesh_read_context(session, mesh_path: Path) -> str:
    return (
        f"backend={MESH_READ_BACKEND} "
        f"pyfluent={_pyfluent_version()} "
        f"fluent={_fluent_version(session)} "
        f"path={path_to_fluent_str(mesh_path)} "
        f"{_mesh_file_facts(mesh_path)}"
    )


def _read_and_verify_mesh(solver, mesh_path: Path, transcript_dir: Path) -> None:
    """Read the Python-written ``.msh`` with the 25.1 solver file menu."""
    fluent_path = path_to_fluent_str(mesh_path)
    context = _mesh_read_context(solver, mesh_path)
    try:
        solver.settings.file.read_mesh(file_name=fluent_path)
    except FluentMeshReadError:
        raise
    except Exception as exc:
        raise FluentMeshReadError(
            f"{context} {type(exc).__name__}: {exc}"
        ) from exc
    # (rpgetvar 'dimension) is undefined in Fluent 2025 R1. The solver
    # settings value two-dim-space is 'planar' for this channel. Cell
    # count comes from mesh/size-info; report/mesh-size is not a solver
    # TUI command in this version.
    space = _planar_space(solver, context)
    cells = _cell_count(solver, context, transcript_dir)
    print(
        "2D mesh read succeeded: "
        f"{context} two_dim_space={space} cells={cells}",
        flush=True,
    )


def _planar_space(solver, context: str) -> str:
    try:
        space = solver.settings.setup.general.solver.two_dim_space.get_state()
    except FluentMeshReadError:
        raise
    except Exception as exc:
        raise FluentMeshReadError(
            "Mesh read succeeded, but two_dim_space could not be read. "
            f"{context} {type(exc).__name__}: {exc}"
        ) from exc
    text = str(space).strip().lower()
    if text != "planar":
        raise FluentMeshReadError(
            f"Fluent two_dim_space is {space!r}, expected 'planar'. {context}"
        )
    return text


def _cell_count(solver, context: str, transcript_dir: Path) -> int:
    try:
        report = solver.settings.mesh.size_info()
    except FluentMeshReadError:
        raise
    except Exception as exc:
        raise FluentMeshReadError(
            "Mesh read succeeded, but mesh.size_info could not be read. "
            f"{context} {type(exc).__name__}: {exc}"
        ) from exc
    cells = _cell_count_from_report(report)
    transcript = ""
    if cells is None:
        # size_info prints the Mesh Size table and returns None.
        transcript = collect_transcript(transcript_dir, solver)
        cells = _cell_count_from_report(transcript)
    if cells is None or cells < 1:
        excerpt = _mesh_size_excerpt(transcript)
        raise FluentMeshReadError(
            "mesh.size_info did not report a positive cell count. "
            f"Report={report!r}. transcript_excerpt={excerpt!r}. {context}"
        )
    return cells


def _mesh_size_excerpt(transcript: str) -> str:
    index = transcript.lower().rfind("mesh size")
    if index < 0:
        return ""
    return transcript[index:index + 240]


def _cell_count_from_report(payload: object) -> int | None:
    if isinstance(payload, bool) or payload is None:
        return None
    if isinstance(payload, int):
        return payload
    if isinstance(payload, float) and payload.is_integer():
        return int(payload)
    if isinstance(payload, Mapping):
        for key in ("cells", "cell_count", "ncells"):
            if key in payload:
                return _cell_count_from_report(payload[key])
        return None
    text = str(payload)
    for pattern in _CELL_COUNT_RES:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1).replace(",", ""))
    return None


def _solver_mesh_check(solver) -> None:
    try:
        solver.execute_tui("/mesh/check")
    except Exception as exc:
        raise FluentSetupError(
            "mesh_check",
            f"{type(exc).__name__}: {exc}",
        ) from exc


def _step(name: str, action):
    try:
        return action()
    except FluentSetupError:
        raise
    except Exception as exc:
        raise FluentSetupError(name, f"{type(exc).__name__}: {exc}") from exc


def _set(node, value) -> None:
    node.set_state(value)


def _set_first(node, candidates: tuple[str, ...], step: str) -> None:
    errors: list[str] = []
    for candidate in candidates:
        try:
            node.set_state(candidate)
            return
        except Exception as exc:
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
    raise FluentSetupError(step, "; ".join(errors))


def _walk_number(payload: object) -> float | None:
    if isinstance(payload, bool) or payload is None:
        return None
    if isinstance(payload, (int, float)):
        value = float(payload)
        return value if math.isfinite(value) else None
    if isinstance(payload, str):
        try:
            value = float(payload)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    if isinstance(payload, Mapping):
        for key in ("value", "mean", "net"):
            if key in payload:
                found = _walk_number(payload[key])
                if found is not None:
                    return found
        for value in payload.values():
            found = _walk_number(value)
            if found is not None:
                return found
        return None
    if isinstance(payload, (list, tuple)):
        for item in payload:
            found = _walk_number(item)
            if found is not None:
                return found
    return None


def _require_zones(setup) -> None:
    boundary = setup.boundary_conditions
    inlet = list(boundary.velocity_inlet.get_object_names())
    outlet = list(boundary.pressure_outlet.get_object_names())
    walls = list(boundary.wall.get_object_names())
    fluids = list(setup.cell_zone_conditions.fluid.get_object_names())
    missing = []
    if "inlet" not in inlet:
        missing.append(f"velocity_inlet inlet (have {inlet})")
    if "outlet" not in outlet:
        missing.append(f"pressure_outlet outlet (have {outlet})")
    for name in ("wall_top_mem", "wall_bottom_mem", "wall_spacer"):
        if name not in walls:
            missing.append(f"wall {name} (have {walls})")
    if "fluid" not in fluids:
        missing.append(f"cell zone fluid (have {fluids})")
    if missing:
        raise FluentSetupError("zones", "; ".join(missing))


def _disable_energy(setup) -> None:
    energy = setup.models.energy
    enabled = getattr(energy, "enabled", None)
    if enabled is not None:
        enabled.set_state(False)
        return
    _set(energy.option, "off")


def _set_constant(group, value: float, step: str) -> None:
    try:
        group.option.set_state("constant")
        group.value.set_state(value)
    except Exception as exc:
        raise FluentSetupError(step, f"{type(exc).__name__}: {exc}") from exc


def _ensure_fluid(materials, name: str, density: float, viscosity: float) -> None:
    fluid = materials.fluid
    names = list(fluid.get_object_names())
    if name not in names:
        fluid.create(name)
    _set_constant(fluid[name].density, density, f"{name}_density")
    _set_constant(fluid[name].viscosity, viscosity, f"{name}_viscosity")


def _configure_mixture(setup) -> None:
    _set_first(
        setup.models.species.model.option,
        ("species-transport", "Species Transport"),
        "species_model",
    )
    materials = setup.materials
    _ensure_fluid(materials, "water", RHO_KG_M3, MU_KG_M_S)
    _ensure_fluid(materials, "nacl", RHO_KG_M3, MU_KG_M_S)
    mixture = materials.mixture["mixture-template"]
    _set_constant(mixture.viscosity, MU_KG_M_S, "mixture_viscosity")
    _set_first(
        mixture.mass_diffusivity.option,
        ("constant-dilute-appx",),
        "mass_diffusivity",
    )
    mixture.mass_diffusivity.value.set_state(MASS_DIFFUSIVITY_M2_S)
    _keep_nacl_water_mixture(mixture)
    # molecular-weight is an option/value property. A bare number is
    # rejected: "value" is not an allowed option, only "constant" is.
    _set_constant(
        mixture.species.volumetric_species[SPECIES_NAME].molecular_weight,
        _NACL_MW_KG_KMOL,
        "nacl_molecular_weight",
    )
    _set_mixture_density(mixture)
    _disable_energy(setup)


def _keep_nacl_water_mixture(mixture) -> None:
    """Leave volumetric species as ``nacl``, then ``water``.

    Fluent 2025 R1 mixture-template already contains ``h2o``, ``o2``,
    and ``n2``. Setting the last species to water does not remove
    them, so ``nacl`` is not index 0. ``SALT_YI_INDEX`` in the 2D UDF
    is 0, so those default species are deleted after water is last.
    """
    species = mixture.species.volumetric_species
    names = list(species.get_object_names())
    for name in ("water", SPECIES_NAME):
        if name not in names:
            species.create(name)
            names.append(name)
    try:
        mixture.species.last_species.set_state("water")
    except Exception as exc:
        raise FluentSetupError(
            "last_species",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    for name in list(species.get_object_names()):
        if name in {SPECIES_NAME, "water"}:
            continue
        try:
            del species[name]
        except Exception as exc:
            raise FluentSetupError(
                "species_order",
                f"Could not remove default species {name}. "
                f"{type(exc).__name__}: {exc}",
            ) from exc
    try:
        mixture.species.last_species.set_state("water")
    except Exception as exc:
        raise FluentSetupError(
            "last_species",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    names = list(species.get_object_names())
    if names != [SPECIES_NAME, "water"]:
        raise FluentSetupError(
            "species_order",
            "SALT_YI_INDEX is 0, so volumetric species must be "
            f"[{SPECIES_NAME!r}, 'water']. Got {names}.",
        )


def _set_mixture_density(mixture) -> None:
    """Use the incompressible mixing law. ``constant`` is not allowed.

    Fluent 2025 R1 rejects mixture density option ``constant``. The
    allowed value that does not require the energy equation is
    ``volume-weighted-mixing-law``. Water and nacl are both given the
    same constant density, so the mixture density stays at that value
    for every mass fraction.
    """
    try:
        mixture.density.option.set_state("volume-weighted-mixing-law")
    except Exception as exc:
        raise FluentSetupError(
            "mixture_density",
            f"{type(exc).__name__}: {exc}",
        ) from exc


def _set_boundaries(
    setup,
    *,
    inlet_velocity_m_s: float,
    outlet_gauge_pressure_pa: float,
) -> None:
    """Write the pre-UDF plug. The profile replaces it after libudf loads."""
    inlet = setup.boundary_conditions.velocity_inlet["inlet"]
    _set_first(
        inlet.momentum.velocity_specification_method,
        ("Magnitude, Normal to Boundary",),
        "inlet_plug",
    )
    inlet.momentum.velocity_magnitude.value.set_state(inlet_velocity_m_s)
    species_names = list(inlet.species.species_mass_fraction.get_object_names())
    if not species_names or species_names[0] != SPECIES_NAME:
        raise FluentSetupError(
            "species_order",
            "SALT_YI_INDEX is 0, so the inlet species list must start "
            f"with {SPECIES_NAME}. Got {species_names}.",
        )
    inlet.species.species_mass_fraction[SPECIES_NAME].value.set_state(
        SALT_MASS_FRACTION
    )
    outlet = setup.boundary_conditions.pressure_outlet["outlet"]
    outlet.momentum.gauge_pressure.value.set_state(outlet_gauge_pressure_pa)
    backflow = list(
        outlet.species.backflow_species_mass_fraction.get_object_names()
    )
    if SPECIES_NAME not in backflow:
        raise FluentSetupError(
            "outlet_backflow",
            f"{SPECIES_NAME} is not a backflow species. Have {backflow}.",
        )
    outlet.species.backflow_species_mass_fraction[SPECIES_NAME].value.set_state(
        SALT_MASS_FRACTION
    )


def _attach_inlet_profile(setup) -> None:
    inlet = setup.boundary_conditions.velocity_inlet["inlet"]
    _set_first(
        inlet.momentum.velocity_specification_method,
        ("Components",),
        "inlet_profile",
    )
    components = inlet.momentum.velocity_components
    if len(components) != 2:
        raise FluentSetupError(
            "inlet_profile",
            "2D velocity_components length must be 2, "
            f"got {len(components)}.",
        )
    components[0].option.set_state("udf")
    components[0].udf.set_state(INLET_PROFILE_UDF)
    components[1].option.set_state("value")
    components[1].value.set_state(0.0)
    state = components[0].get_state()
    if INLET_PROFILE_UDF not in str(state):
        raise FluentSetupError(
            "inlet_profile",
            f"x-velocity UDF was not attached. State={state!r}.",
        )


def _hook_functions(solver) -> None:
    solver.execute_tui(
        f'/define/user-defined/function-hooks/adjust "{ADJUST_UDF}"'
    )
    solver.execute_tui(
        '/define/user-defined/function-hooks/initialization '
        f'"{INIT_UDF}"'
    )


def _hook_sources(setup) -> None:
    zone = setup.cell_zone_conditions.fluid["fluid"]
    zone.sources.enable.set_state(True)
    available = list(zone.sources.terms.get_state().keys())
    missing = [key for key in SOURCE_UDFS if key not in available]
    if missing:
        raise FluentSetupError(
            "source_terms",
            f"Missing {missing}. Available source terms: {available}.",
        )
    for key, udf_name in SOURCE_UDFS.items():
        term = zone.sources.terms[key]
        term.resize(1)
        term[0].option.set_state("udf")
        term[0].udf.set_state(udf_name)


def _patch_species(solution) -> None:
    solution.initialization.patch.calculate_patch(
        domain="mixture",
        cell_zones=["fluid"],
        registers=[],
        variable="species-0",
        reference_frame="Relative to Cell Zone",
        use_custom_field_function=False,
        custom_field_function_name="",
        value=SALT_MASS_FRACTION,
    )


def _set_residuals(solution, target: float) -> None:
    equations = solution.monitor.residual.equations
    available = list(equations.get_state().keys())
    missing = [name for name in _REQUIRED_RESIDUALS if name not in available]
    if missing:
        raise FluentSetupError(
            "residuals",
            f"Missing {missing}. Available equations: {available}.",
        )
    for name in _RESIDUAL_EQUATIONS:
        if name not in available:
            continue
        equation = equations[name]
        equation.monitor = True
        equation.check_convergence = True
        state = equation.get_state()
        if "absolute_criteria" in state:
            equation.absolute_criteria = target
        elif "relative_criteria" in state:
            equation.relative_criteria = target
        else:
            raise FluentSetupError(
                "residuals",
                f"No criteria field for {name}. State={state!r}.",
            )


def _extract_reports(solver, config: PilotConfig) -> dict[str, object]:
    reports = solver.settings.solution.report_definitions
    _make_surface(reports, "p_inlet", "pressure", ["inlet"])
    _make_surface(reports, "p_outlet", "pressure", ["outlet"])
    _make_flux(reports, "m_inlet", ["inlet"])
    _make_flux(reports, "m_outlet", ["outlet"])
    _make_volume(reports, "sink_total", "udm-1", ["fluid"])
    p_inlet = _compute(reports, "p_inlet")
    p_outlet = _compute(reports, "p_outlet")
    mass_in = _compute(reports, "m_inlet")
    mass_out = _compute(reports, "m_outlet")
    sink = _compute(reports, "sink_total")
    permeate = permeate_mass_flow_kg_s(mass_in, mass_out)
    return {
        "lmh": lmh_from_mass_balance(
            mass_in_kg_s=mass_in,
            mass_out_kg_s=mass_out,
            density_kg_m3=RHO_KG_M3,
            membrane_area_m2=membrane_area_m2(config),
        ),
        "pressure_drop_pa": p_inlet - p_outlet,
        "mass_balance_rel": mass_balance_relative_error(permeate, sink),
        "cp_average": _optional_cp(reports),
        "membrane_solution": _membrane_solution(reports, mass_in, mass_out, sink),
    }


def _make_surface(reports, name: str, field: str, surfaces: list[str]) -> None:
    reports.surface.create(name)
    item = reports.surface[name]
    _set_first(
        item.report_type,
        ("surface-areaavg", "area-weighted-avg"),
        f"{name}_type",
    )
    item.field.set_state(field)
    item.surface_names.set_state(surfaces)


def _make_flux(reports, name: str, boundaries: list[str]) -> None:
    reports.flux.create(name)
    item = reports.flux[name]
    # Fluent 2025 R1 flux reports allow only flux-massflow.
    _set_first(item.report_type, ("flux-massflow",), f"{name}_type")
    item.boundaries.set_state(boundaries)


def _make_volume(reports, name: str, field: str, zones: list[str]) -> None:
    reports.volume.create(name)
    item = reports.volume[name]
    _set_first(item.report_type, ("volume-integral",), f"{name}_type")
    item.field.set_state(field)
    item.cell_zones.set_state(zones)


def _compute(reports, name: str) -> float:
    payload = reports.compute(report_defs=[name])
    try:
        return first_report_number(payload)
    except ValueError as exc:
        raise FluentSetupError(name, str(exc)) from exc


_BOTH_MEMBRANES = ["wall_top_mem", "wall_bottom_mem"]
_AVERAGE_TYPES = ("surface-areaavg", "area-weighted-avg")
_MIN_TYPES = ("surface-facetmin", "facet-min")
_MAX_TYPES = ("surface-facetmax", "facet-max")


def _membrane_solution(
    reports,
    mass_in: float,
    mass_out: float,
    sink: float,
) -> dict[str, float | None]:
    """Fail-soft membrane state. A rejected report type stays null."""
    both = _BOTH_MEMBRANES
    return {
        "mass_in_kg_s": mass_in,
        "mass_out_kg_s": mass_out,
        "source_integral_kg_s": sink,
        "jw_avg_m_s": _try_surface(reports, "jw_avg", "udm-6", both, _AVERAGE_TYPES),
        "jw_min_m_s": _try_surface(reports, "jw_min", "udm-6", both, _MIN_TYPES),
        "jw_max_m_s": _try_surface(reports, "jw_max", "udm-6", both, _MAX_TYPES),
        "jw_top_avg_m_s": _try_surface(
            reports, "jw_top", "udm-6", ["wall_top_mem"], _AVERAGE_TYPES
        ),
        "jw_bottom_avg_m_s": _try_surface(
            reports, "jw_bot", "udm-6", ["wall_bottom_mem"], _AVERAGE_TYPES
        ),
        "salt_flux_avg_kg_m2_s": _try_surface(
            reports, "js_avg", "udm-10", both, _AVERAGE_TYPES
        ),
        "membrane_pressure_avg_pa": _try_surface(
            reports, "p_mem", "pressure", both, _AVERAGE_TYPES
        ),
        "membrane_pressure_top_avg_pa": _try_surface(
            reports, "p_top", "pressure", ["wall_top_mem"], _AVERAGE_TYPES
        ),
        "membrane_pressure_bottom_avg_pa": _try_surface(
            reports, "p_bot", "pressure", ["wall_bottom_mem"], _AVERAGE_TYPES
        ),
        "membrane_concentration_avg_mol_m3": _try_surface(
            reports, "cm_avg", "udm-7", both, _AVERAGE_TYPES
        ),
        "membrane_concentration_top_avg_mol_m3": _try_surface(
            reports, "cm_top", "udm-7", ["wall_top_mem"], _AVERAGE_TYPES
        ),
        "membrane_concentration_bottom_avg_mol_m3": _try_surface(
            reports, "cm_bot", "udm-7", ["wall_bottom_mem"], _AVERAGE_TYPES
        ),
        "cp_min": _try_surface(reports, "cp_min", "udm-9", both, _MIN_TYPES),
        "cp_max": _try_surface(reports, "cp_max", "udm-9", both, _MAX_TYPES),
        "udm_y1_avg_m": _try_surface(reports, "y1_avg", "udm-12", both, _AVERAGE_TYPES),
        "udm_y1_min_m": _try_surface(reports, "y1_min", "udm-12", both, _MIN_TYPES),
        "udm_y1_max_m": _try_surface(reports, "y1_max", "udm-12", both, _MAX_TYPES),
        "divergence_phase": None,
    }


def _try_surface(
    reports,
    name: str,
    field: str,
    surfaces: list[str],
    kinds: tuple[str, ...],
) -> float | None:
    try:
        reports.surface.create(name)
        item = reports.surface[name]
        _set_first(item.report_type, kinds, f"{name}_type")
        item.field.set_state(field)
        item.surface_names.set_state(surfaces)
        return _compute(reports, name)
    except Exception:
        return None


def _optional_cp(reports) -> float | None:
    try:
        _make_surface(
            reports,
            "cp_mem",
            "udm-9",
            ["wall_top_mem", "wall_bottom_mem"],
        )
        return _compute(reports, "cp_mem")
    except Exception:
        return None
