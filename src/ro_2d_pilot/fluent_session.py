"""One 2D Fluent session for a single pilot case.

The Windows host times out when Fluent is launched directly in solver
mode. The production 3D workflow therefore launches meshing mode and
calls ``switch_to_solver()``. This module does the same for one 2D
case, then reads the algebraic ``.msh`` that Python already wrote.
``PURE_MESHING`` is not used: that session cannot switch to the solver.

PyFluent is imported only while launching. Launch, mesh-read, and
switch failures are separate exceptions. Species setup fails by step
name when the live settings tree does not match this session.

The solved inlet is the 2D Poiseuille profile in ``260929_RO_UDF.c``.
A magnitude plug is written only so the boundary exists before the
library is loaded, and the profile replace is required before iterate.
"""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path
from typing import Mapping

from ro.solver_common import (
    STOP_REASON_QOI_CONVERGED,
    STOP_REASON_RESIDUAL_CONVERGED,
    classify_solver_stop_reason,
    parse_fluent_convergence_marker,
    parse_last_residual_iteration_from_transcript_text,
    path_to_fluent_str,
)
from ro_2d_pilot.config import PilotConfig
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
_CELL_COUNT_RE = re.compile(r"(\d+)\s+cells\b", re.IGNORECASE)
_DEFAULT_START_TIMEOUT_S = 300


class FluentUnavailable(RuntimeError):
    """Fluent could not be imported or launched on this machine."""


class FluentSetupError(RuntimeError):
    """One named setup or extraction step failed. The case was not treated as valid."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        super().__init__(f"{step}: {message}")


class FluentMeshReadError(FluentSetupError):
    """The meshing session could not read or verify the 2D ``.msh``."""

    def __init__(self, message: str) -> None:
        super().__init__("read_mesh", message)


class FluentSwitchToSolverError(FluentSetupError):
    """``switch_to_solver()`` failed after the mesh was read."""

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


def classify_transcript(text: str, *, max_iterations: int) -> dict[str, object]:
    """Map a Fluent transcript to the campaign stop-reason vocabulary."""
    lowered = text.lower()
    diverged = any(marker in lowered for marker in _DIVERGENCE_MARKERS)
    marker_reason, marker_iteration = parse_fluent_convergence_marker(text)
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
    }


def meshing_launch_kwargs(
    *,
    cwd: Path,
    processor_count: int | None = None,
    product_version: str | None = None,
    start_timeout: int | None = None,
) -> dict[str, object]:
    """Arguments for a regular 2D meshing session.

    ``mode`` is ``"meshing"``, which ``FluentMode.MESHING`` uses. It is
    not ``"solver"`` and not ``"pure_meshing"``. The UI settings match
    the production launcher on this Windows host: ``gui`` and ``dx11``.
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
    """Launch Fluent in regular meshing mode. ``launcher`` is for tests."""
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
            "Fluent meshing launch failed. On the Windows host, "
            "AWP_ROOT251 must point at the Ansys 2025 R1 installation. "
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
    """Launch meshing, read the ``.msh``, switch, and mesh-check.

    The returned object is a solver session. The meshing object is not
    usable after a successful switch, and a second Fluent process is
    not started.
    """
    meshing = None
    solver = None
    handed_off = False
    try:
        meshing = launch_meshing_session(
            cwd=cwd,
            processor_count=processor_count,
            product_version=product_version,
            start_timeout=start_timeout,
            launcher=launcher,
        )
        _read_and_verify_mesh(meshing, mesh_path)
        try:
            solver = meshing.switch_to_solver()
        except FluentSwitchToSolverError:
            raise
        except Exception as exc:
            raise FluentSwitchToSolverError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        _solver_mesh_check(solver)
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
    started = time.perf_counter()
    _step(
        "iterate",
        lambda: solution.run_calculation.iterate(iter_count=max_iterations),
    )
    solver_wall_time_s = time.perf_counter() - started
    transcript = collect_transcript(udf_path.parent, solver)
    classified = classify_transcript(transcript, max_iterations=max_iterations)
    metrics = _step(
        "extract_reports",
        lambda: _extract_reports(solver, config),
    )
    metrics.update(classified)
    metrics["solver_wall_time_s"] = solver_wall_time_s
    return metrics


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
    if mode in {"pure_meshing", pyfluent.FluentMode.PURE_MESHING}:
        raise FluentUnavailable(
            "PURE_MESHING cannot switch_to_solver(). "
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


def _read_and_verify_mesh(meshing, mesh_path: Path) -> None:
    fluent_path = path_to_fluent_str(mesh_path)
    try:
        meshing.tui.file.read_mesh(fluent_path)
    except FluentMeshReadError:
        raise
    except Exception as exc:
        raise FluentMeshReadError(
            f"Could not read {fluent_path}. {type(exc).__name__}: {exc}"
        ) from exc
    try:
        dimension = _as_int(meshing.scheme.eval("(rpgetvar 'dimension)"))
        report = meshing.tui.report.mesh_size()
    except FluentMeshReadError:
        raise
    except Exception as exc:
        raise FluentMeshReadError(
            "Mesh read returned, but dimension or mesh size could not "
            f"be read. {type(exc).__name__}: {exc}"
        ) from exc
    if dimension != 2:
        raise FluentMeshReadError(
            f"Fluent dimension is {dimension}, expected 2."
        )
    cells = _cell_count_from_report(report)
    if cells is None:
        cells = _cell_count_from_scheme(meshing)
    if cells is None or cells < 1:
        raise FluentMeshReadError(
            "Mesh size did not report a positive cell count. "
            f"Report={report!r}."
        )


def _cell_count_from_scheme(meshing) -> int | None:
    for expression in ("(mesh-size)",):
        try:
            value = meshing.scheme.eval(expression)
        except Exception:
            continue
        cells = _cell_count_from_report(value)
        if cells is not None and cells > 0:
            return cells
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Expected an integer, got {value!r}.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Expected an integer, got {value!r}.")
        return int(value)
    return int(float(str(value).strip()))


def _cell_count_from_report(payload: object) -> int | None:
    if isinstance(payload, bool) or payload is None:
        return None
    if isinstance(payload, int):
        return payload
    if isinstance(payload, float) and payload.is_integer():
        return int(payload)
    match = _CELL_COUNT_RE.search(str(payload))
    if match is None:
        return None
    return int(match.group(1))


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
    _set_constant(mixture.density, RHO_KG_M3, "mixture_density")
    _set_constant(mixture.viscosity, MU_KG_M_S, "mixture_viscosity")
    _set_first(
        mixture.mass_diffusivity.option,
        ("constant-dilute-appx",),
        "mass_diffusivity",
    )
    mixture.mass_diffusivity.value.set_state(MASS_DIFFUSIVITY_M2_S)
    species = mixture.species.volumetric_species
    names = list(species.get_object_names())
    for name in ("water", "nacl"):
        if name not in names:
            species.create(name)
    try:
        mixture.species.last_species.set_state("water")
    except Exception as exc:
        raise FluentSetupError(
            "last_species",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    try:
        species["nacl"].molecular_weight.set_state(_NACL_MW_KG_KMOL)
    except Exception as exc:
        raise FluentSetupError(
            "nacl_molecular_weight",
            f"{type(exc).__name__}: {exc}",
        ) from exc
    names = list(species.get_object_names())
    if not names or names[0] != SPECIES_NAME:
        raise FluentSetupError(
            "species_order",
            "SALT_YI_INDEX is 0, so nacl must be the first volumetric "
            f"species. Got {names}.",
        )


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
    _set_first(item.report_type, ("mass-flow-rate",), f"{name}_type")
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
