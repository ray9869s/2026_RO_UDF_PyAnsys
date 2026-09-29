"""Mesh, UDF, and dry-run checks for the 2D case path. No Fluent process."""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import pytest

from helpers import REPO_ROOT, SCRIPTS_DIR, load_module
from ro.udf_constants import parse_udf_membrane_constants
from ro.udm_layout import parse_udm_enum_from_c
from ro_2d_pilot.config import OperatingPoint, PilotConfig
from ro_2d_pilot.execute import run_case
from ro_2d_pilot.fluent_session import (
    MESH_READ_BACKEND,
    SWITCH_BACKEND,
    FluentMeshReadError,
    FluentSetupError,
    FluentSwitchToSolverError,
    FluentUnavailable,
    _configure_mixture,
    _keep_nacl_water_mixture,
    _set_constant,
    _set_mixture_density,
    classify_transcript,
    first_report_number,
    meshing_launch_kwargs,
    open_solver_session,
)
from ro_2d_pilot.geometry import describe_geometry
from ro_2d_pilot.mesh_build import build_quad_mesh, write_fluent_msh
from ro_2d_pilot.paths import DATA_ROOT_ENV
from ro_2d_pilot.record import mass_balance_relative_error
from ro_2d_pilot.udf_case import master_udf_path, patch_udf_text, write_case_udf
from ro_2d_pilot.udf_gate import production_udf_path

D_M = 4.0e-4
L_M = 4.0e-3


def _config(fidelity: str = "low", n_pitches: int = 1) -> PilotConfig:
    return PilotConfig(
        d_m=D_M,
        L_m=L_M,
        fidelity=fidelity,
        n_pitches=n_pitches,
    )


def _centroid(mesh, quad_index: int) -> tuple[float, float]:
    quad = mesh.quads[quad_index]
    xs = [mesh.nodes[index][0] for index in quad]
    ys = [mesh.nodes[index][1] for index in quad]
    return sum(xs) / 4.0, sum(ys) / 4.0


def _left_of(mesh, n0: int, n1: int, quad_index: int) -> float:
    x0, y0 = mesh.nodes[n0]
    x1, y1 = mesh.nodes[n1]
    cx, cy = _centroid(mesh, quad_index)
    return (x1 - x0) * (cy - y0) - (y1 - y0) * (cx - x0)


def test_low_mesh_is_closed_and_resolves_the_filament() -> None:
    config = _config()
    mesh = build_quad_mesh(config)
    geometry = describe_geometry(config)
    assert mesh.n_cells > 0
    assert mesh.actual_min_edge_m > 0.0
    boundary = sum(len(edges) for edges in mesh.boundaries.values())
    assert 4 * mesh.n_cells == 2 * len(mesh.interior) + boundary
    assert set(mesh.boundaries) == {
        "inlet",
        "outlet",
        "wall_top_mem",
        "wall_bottom_mem",
        "wall_spacer",
    }
    assert mesh.counts["inlet"] == mesh.counts["outlet"]
    assert mesh.counts["wall_spacer"] > 0
    for n0, n1, c0, _c1 in mesh.interior:
        assert _left_of(mesh, n0, n1, c0) > 0.0
    for name, edges in mesh.boundaries.items():
        for n0, n1 in edges:
            owner = None
            for quad_index, quad in enumerate(mesh.quads):
                for corner in range(4):
                    if (quad[corner], quad[(corner + 1) % 4]) == (n0, n1):
                        owner = quad_index
            assert owner is not None
            assert _left_of(mesh, n0, n1, owner) > 0.0, name
    obstacles = geometry["obstacles"]
    for x_m, y_m in mesh.nodes:
        for obstacle in obstacles:
            radius = 0.5 * obstacle["diameter_m"]
            distance = math.hypot(
                x_m - obstacle["center_x_m"],
                y_m - obstacle["center_y_m"],
            )
            assert distance >= radius - 1.0e-9
    spacer_nodes = {
        index for edge in mesh.boundaries["wall_spacer"] for index in edge
    }
    for index in spacer_nodes:
        x_m, y_m = mesh.nodes[index]
        on_circle = False
        for obstacle in obstacles:
            radius = 0.5 * obstacle["diameter_m"]
            distance = math.hypot(
                x_m - obstacle["center_x_m"],
                y_m - obstacle["center_y_m"],
            )
            on_circle = on_circle or abs(distance - radius) <= 1.0e-8
        assert on_circle
    again = build_quad_mesh(config)
    assert again.nodes == mesh.nodes
    assert again.quads == mesh.quads


def test_three_pitches_keep_one_inlet_and_high_mesh_is_finer() -> None:
    one = build_quad_mesh(_config(n_pitches=1))
    three = build_quad_mesh(_config(n_pitches=3))
    high = build_quad_mesh(_config(fidelity="high"))
    assert three.n_cells > one.n_cells
    assert three.counts["inlet"] == one.counts["inlet"]
    assert three.counts["outlet"] == one.counts["outlet"]
    assert three.counts["wall_spacer"] == 3 * one.counts["wall_spacer"]
    assert high.n_cells > one.n_cells
    assert high.actual_max_edge_m < one.actual_max_edge_m


def test_fluent_mesh_is_a_2d_ascii_file(tmp_path: Path) -> None:
    mesh = build_quad_mesh(_config())
    path = tmp_path / "case.msh"
    write_fluent_msh(mesh, path)
    text = path.read_text(encoding="ascii")
    assert "(2 2)" in text
    assert "velocity-inlet inlet" in text
    assert "pressure-outlet outlet" in text
    assert "wall wall_top_mem" in text
    assert "wall wall_bottom_mem" in text
    assert "wall wall_spacer" in text
    assert "fluid fluid" in text
    assert str(tmp_path) not in text
    assert "\\" not in text
    assert "C:" not in text


def test_2d_udf_keeps_physics_and_uses_y() -> None:
    production = production_udf_path().read_text(encoding="ascii")
    source = master_udf_path().read_text(encoding="ascii")
    assert master_udf_path().name == "260929_RO_UDF.c"
    assert source.isascii()
    assert '#if RP_3D' in source
    assert "2D membrane source" in source
    assert "xc[2]" not in source
    assert "x[2]" not in source
    assert "Ar[2]" not in source
    assert "centroid[1]" in source
    assert "wall_top_mem" in source
    assert "wall_bottom_mem" in source
    assert "y_mom_source" in source
    assert "z_mom_source" not in source
    assert "rp_3d_compile_fence" not in source
    assert '#error "260822 inlet profile' in production
    assert parse_udf_membrane_constants(source) == parse_udf_membrane_constants(
        production
    )
    parsed = parse_udm_enum_from_c(source)
    assert parsed["UDM_TOTAL_S"] == 1
    assert parsed["UDM_Y1"] == 12
    assert parsed["UDM_COUNT"] == 13
    assert parsed["UDM_ZMOM"] == parse_udm_enum_from_c(production)["UDM_ZMOM"]


def test_case_udf_patch_does_not_touch_the_master(tmp_path: Path) -> None:
    master = master_udf_path()
    before = master.read_bytes()
    config = PilotConfig(
        d_m=D_M,
        L_m=L_M,
        fidelity="low",
        operating=OperatingPoint(
            inlet_velocity_m_s=0.15,
            outlet_gauge_pressure_pa=5.0e6,
        ),
    )
    patched = patch_udf_text(before.decode("ascii"), config)
    assert "0.15" in patched
    assert "0.00077" in patched
    destination = tmp_path / master.name
    write_case_udf(config, destination)
    assert master.read_bytes() == before
    assert destination.read_bytes() != before
    with pytest.raises(ValueError, match="master"):
        write_case_udf(config, master)
    assert master.read_bytes() == before


def test_mass_balance_matches_the_campaign_definition() -> None:
    assert mass_balance_relative_error(1.0, 1.0) == pytest.approx(0.0)
    assert mass_balance_relative_error(1.001, 1.0) == pytest.approx(1.0e-3)


def test_transcript_classification_reuses_campaign_stop_reasons() -> None:
    text = "\n".join(
        [
            "iter continuity x-velocity y-velocity z-velocity nacl",
            "12 1e-8 1e-8 1e-8 1e-8 1e-8",
            "solution is converged",
        ]
    )
    classified = classify_transcript(text, max_iterations=2000)
    assert classified["convergence_status"] == "residual_converged"
    assert classified["solver_iterations"] == 12
    diverged = classify_transcript(
        "divergence detected\n" + text,
        max_iterations=2000,
    )
    assert diverged["convergence_status"] == "diverged"
    unknown = classify_transcript("", max_iterations=2000)
    assert unknown["convergence_status"] == "iteration_unknown"
    assert first_report_number({"mass_in": ["name", 3.5]}) == pytest.approx(3.5)


def test_dry_run_writes_mesh_without_launching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    root = tmp_path / "ro2d"
    launched = {"called": False}

    def launcher(**_kwargs):
        launched["called"] = True
        raise AssertionError("dry-run launched Fluent")

    record = run_case(_config(), root, dry_run=True, launcher=launcher)
    assert launched["called"] is False
    assert record["validity"] == "not_run"
    assert record["cell_count"] is None
    assert record["lmh"] is None
    mesh_spec = json.loads(
        next(root.rglob("mesh_spec.json")).read_text(encoding="utf-8")
    )
    msh = next(root.rglob("*.msh"))
    assert mesh_spec["cell_count"] == 2868
    assert mesh_spec["fidelity_study_status"] == "unvalidated_execution_floor"
    assert msh.stat().st_size > 0
    assert REPO_ROOT not in msh.parents


def test_missing_fluent_writes_an_invalid_result(tmp_path: Path) -> None:
    seen = {}

    def launcher(**kwargs):
        seen.update(kwargs)
        raise KeyError("AWP_ROOT251")

    with pytest.raises(FluentUnavailable, match="AWP_ROOT251"):
        run_case(_config(), tmp_path / "ro2d", launcher=launcher)
    assert seen["mode"] == "meshing"
    assert seen["dimension"] == 2
    assert seen["precision"] == "double"
    assert seen["ui_mode"] == "gui"
    assert seen["graphics_driver"] == "dx11"
    assert seen["mode"] != "solver"
    assert seen["mode"] != "pure_meshing"
    result = json.loads(
        next((tmp_path / "ro2d").rglob("result.json")).read_text(encoding="utf-8")
    )
    assert result["validity"] == "invalid"
    assert result["cell_count"] == 2868
    assert result["convergence_status"] == "stop_reason_determination_failed"
    assert result["lmh"] is None
    udf = next((tmp_path / "ro2d").rglob("*_RO_UDF.c"))
    assert udf.name == "260929_RO_UDF.c"
    assert "2D membrane source" in udf.read_text(encoding="ascii")


def test_run_script_dry_run_parses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RO_DATA_ROOT", raising=False)
    monkeypatch.setenv(DATA_ROOT_ENV, str(tmp_path / "ro2d"))
    script = load_module("run_ro_2d_case_under_test", SCRIPTS_DIR / "run_ro_2d_case.py")
    assert script.main(
        [
            "--d-m",
            "4e-4",
            "--l-m",
            "4e-3",
            "--u-ms",
            "0.2",
            "--pressure-pa",
            "6e6",
            "--fidelity",
            "low",
            "--n-pitches",
            "1",
            "--dry-run",
        ]
    ) == 0
    captured = capsys.readouterr()
    assert "validity=not_run" in captured.out
    assert "d0p400000mm_L4p000000mm_h0p770000mm_n1" in captured.out


class _Scheme:
    def __init__(self, events: list[str], dimension: int = 2) -> None:
        self._events = events
        self._dimension = dimension

    def eval(self, expression: str, suppress_prompts: bool = True):
        self._events.append(f"scheme:{expression}")
        if "dimension" in expression:
            return self._dimension
        raise AssertionError(expression)


class _Report:
    def __init__(self, events: list[str], text: str = "2868 cells") -> None:
        self._events = events
        self._text = text

    def mesh_size(self) -> str:
        self._events.append("mesh_size")
        return self._text


class _Tui:
    def __init__(self, events: list[str]) -> None:
        self.report = _Report(events)


class _SolverFile:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    def read_mesh(self, *, file_name: str) -> None:
        self._events.append(f"read_mesh:{file_name}")
        if self._error is not None:
            raise self._error


class _TwoDimSpace:
    def __init__(self, events: list[str], space: str) -> None:
        self._events = events
        self._space = space

    def get_state(self) -> str:
        self._events.append(f"two_dim_space:{self._space}")
        return self._space


class _SizeInfo:
    def __init__(self, events: list[str], text: str | None = "2868 cells") -> None:
        self._events = events
        self._text = text

    def __call__(self) -> str | None:
        self._events.append("size_info")
        return self._text


class _MeshSettings:
    def __init__(self, events: list[str], text: str | None) -> None:
        self.size_info = _SizeInfo(events, text)


class _GeneralSolver:
    def __init__(self, events: list[str], space: str) -> None:
        self.two_dim_space = _TwoDimSpace(events, space)


class _General:
    def __init__(self, events: list[str], space: str) -> None:
        self.solver = _GeneralSolver(events, space)


class _Setup:
    def __init__(self, events: list[str], space: str) -> None:
        self.general = _General(events, space)


class _SolverSettings:
    def __init__(
        self,
        events: list[str],
        error: Exception | None = None,
        *,
        space: str = "planar",
        size_report: str | None = "2868 cells",
    ) -> None:
        self.file = _SolverFile(events, error)
        self.setup = _Setup(events, space)
        self.mesh = _MeshSettings(events, size_report)


class _Solver:
    def __init__(
        self,
        events: list[str],
        *,
        dimension: int = 2,
        read_error: Exception | None = None,
        size_report: str | None = "2868 cells",
    ) -> None:
        self.events = events
        self.settings = _SolverSettings(
            events,
            read_error,
            space="planar" if dimension == 2 else "3d",
            size_report=size_report,
        )
        self.scheme = _Scheme(events, dimension)
        self.tui = _Tui(events)
        self.exited = False

    def execute_tui(self, command: str) -> None:
        self.events.append(f"tui:{command}")

    def exit(self) -> None:
        self.exited = True
        self.events.append("exit_solver")


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.finalizer_cbs = [lambda: events.append("meshing_finalizer")]


class _MeshingScheme:
    def __init__(
        self,
        events: list[str],
        error: Exception | None = None,
        result: object = True,
    ) -> None:
        self._events = events
        self._error = error
        self._result = result

    def eval(self, expression: str, suppress_prompts: bool = True):
        self._events.append(f"scheme:{expression}")
        if self._error is not None:
            raise self._error
        if "/switch-to-solution-mode yes" not in expression:
            raise AssertionError(expression)
        return self._result


class _Meshing:
    def __init__(
        self,
        events: list[str],
        *,
        dimension: int = 2,
        read_error: Exception | None = None,
        switch_error: Exception | None = None,
        switch_result: object = True,
        size_report: str | None = "2868 cells",
    ) -> None:
        self.events = events
        self.dimension = dimension
        self.read_error = read_error
        self.size_report = size_report
        self._fluent_connection = _Connection(events)
        self._file_transfer_service = None
        self.scheme = _MeshingScheme(events, switch_error, switch_result)
        self.exited = False
        self.solver: _Solver | None = None

    def build_solver(self, _connection) -> _Solver:
        self.events.append("attach_solver")
        self.solver = _Solver(
            self.events,
            dimension=self.dimension,
            read_error=self.read_error,
            size_report=self.size_report,
        )
        return self.solver

    def exit(self) -> None:
        self.exited = True
        self.events.append("exit_meshing")


def test_launch_kwargs_are_2d_meshing_with_gui(tmp_path: Path) -> None:
    kwargs = meshing_launch_kwargs(cwd=tmp_path)
    assert kwargs["mode"] == "meshing"
    assert kwargs["dimension"] == 2
    assert kwargs["precision"] == "double"
    assert kwargs["processor_count"] == 1
    assert kwargs["ui_mode"] == "gui"
    assert kwargs["graphics_driver"] == "dx11"
    assert kwargs["start_timeout"] == 300
    assert kwargs["mode"] != "pure_meshing"
    assert kwargs["mode"] != "solver"


def test_case_launch_switches_by_text_menu_then_reads_mesh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    meshing = _Meshing(events)

    def launcher(**kwargs):
        events.append(f"launch:{kwargs['mode']}:{kwargs['dimension']}")
        assert kwargs["ui_mode"] == "gui"
        assert kwargs["graphics_driver"] == "dx11"
        return meshing

    def solve(solver, **_kwargs):
        events.append("solver_setup")
        assert isinstance(solver, _Solver)
        return {
            "cell_count": 2868,
            "lmh": 1.0,
            "pressure_drop_pa": 10.0,
            "mass_balance_rel": 0.0,
            "convergence_status": "residual_converged",
            "solver_iterations": 3,
            "solver_wall_time_s": 1.0,
        }

    monkeypatch.setattr("ro_2d_pilot.execute.solve_case", solve)
    record = run_case(_config(), tmp_path / "ro2d", launcher=launcher)
    mesh_read = "read_mesh:" + str(next((tmp_path / "ro2d").rglob("*.msh")))
    assert events == [
        "launch:meshing:2",
        "meshing_finalizer",
        'scheme:(ti-menu-load-string "/switch-to-solution-mode yes")',
        "attach_solver",
        mesh_read,
        "two_dim_space:planar",
        "size_info",
        "tui:/mesh/check",
        "solver_setup",
        "exit_solver",
    ]
    assert meshing._fluent_connection is None
    assert record["validity"] == "valid"
    assert "exit_meshing" not in events


def test_mesh_read_failure_stops_before_mesh_check(tmp_path: Path) -> None:
    events: list[str] = []
    meshing = _Meshing(events, read_error=RuntimeError("bad msh"))

    def launcher(**_kwargs):
        return meshing

    with pytest.raises(FluentMeshReadError, match="read_mesh"):
        run_case(_config(), tmp_path / "ro2d", launcher=launcher)
    assert "attach_solver" in events
    assert "tui:/mesh/check" not in events
    assert "solver_setup" not in events
    assert meshing.solver is not None and meshing.solver.exited is True
    assert meshing.exited is False
    result = json.loads(
        next((tmp_path / "ro2d").rglob("result.json")).read_text(encoding="utf-8")
    )
    assert result["validity"] == "invalid"


def test_text_menu_switch_failure_does_not_read_mesh(tmp_path: Path) -> None:
    events: list[str] = []
    meshing = _Meshing(events, switch_error=RuntimeError("menu not found"))

    with pytest.raises(FluentSwitchToSolverError, match="menu not found") as caught:
        run_case(_config(), tmp_path / "ro2d", launcher=lambda **_kwargs: meshing)
    message = str(caught.value)
    assert f"backend={SWITCH_BACKEND}" in message
    assert "attach_solver" not in events
    assert not any(event.startswith("read_mesh:") for event in events)
    assert meshing.exited is True
    assert meshing._fluent_connection is not None
    assert SWITCH_BACKEND.startswith("scheme ti-menu-load-string")


def test_non_2d_session_is_rejected_before_mesh_check(tmp_path: Path) -> None:
    events: list[str] = []
    meshing = _Meshing(events, dimension=3)
    mesh_path = tmp_path / "case.msh"
    mesh_path.write_text("(2 2)\n", encoding="ascii")

    with pytest.raises(FluentMeshReadError, match="planar"):
        open_solver_session(
            cwd=tmp_path,
            mesh_path=mesh_path,
            launcher=lambda **_kwargs: meshing,
        )
    assert any(event.startswith("read_mesh:") for event in events)
    assert "tui:/mesh/check" not in events
    assert meshing.solver is not None and meshing.solver.exited is True


def test_mesh_read_failure_names_the_solver_backend(tmp_path: Path) -> None:
    events: list[str] = []
    meshing = _Meshing(events, read_error=RuntimeError("Error: read mesh failed"))

    with pytest.raises(FluentMeshReadError, match="read mesh failed") as caught:
        run_case(_config(), tmp_path / "ro2d", launcher=lambda **_kwargs: meshing)
    message = str(caught.value)
    assert f"backend={MESH_READ_BACKEND}" in message
    assert "pyfluent=" in message
    assert "fluent=" in message
    assert "exists=True" in message
    assert "size_bytes=" in message
    assert any(
        "/switch-to-solution-mode yes" in event for event in events
    )
    assert events.index("attach_solver") < next(
        index for index, event in enumerate(events) if event.startswith("read_mesh:")
    )
    assert "tui:/mesh/check" not in events
    assert MESH_READ_BACKEND == "solver.settings.file.read_mesh"


def test_none_size_info_uses_the_mesh_size_table(tmp_path: Path) -> None:
    events: list[str] = []
    meshing = _Meshing(events, size_report=None)
    mesh_path = tmp_path / "case.msh"
    mesh_path.write_text("(2 2)\n", encoding="ascii")
    (tmp_path / "fluent.trn").write_text(
        "Mesh Size\n"
        "\n"
        "Level    Cells    Faces    Nodes   Partitions\n"
        "    0     2868     5920     3052            1\n",
        encoding="utf-8",
    )

    solver = open_solver_session(
        cwd=tmp_path,
        mesh_path=mesh_path,
        launcher=lambda **_kwargs: meshing,
    )
    assert "size_info" in events
    assert "tui:/mesh/check" in events
    assert solver.exited is False


def test_mixture_density_is_the_incompressible_mixing_law() -> None:
    class _State:
        def __init__(self) -> None:
            self.state = None

        def set_state(self, value: str) -> None:
            if value == "constant":
                raise RuntimeError(
                    "constant is_not_in "
                    "(volume-weighted-mixing-law ideal-gas)"
                )
            self.state = value

    option = _State()
    value = _State()

    class _Density:
        def __init__(self, option: _State, value: _State) -> None:
            self.option = option
            self.value = value

    class _Mixture:
        def __init__(self, density: _Density) -> None:
            self.density = density

    _set_mixture_density(_Mixture(_Density(option, value)))
    assert option.state == "volume-weighted-mixing-law"
    assert value.state is None

    class _RejectingOption:
        def set_state(self, _value: str) -> None:
            raise RuntimeError("rejected")

    with pytest.raises(FluentSetupError, match="mixture_density"):
        _set_mixture_density(_Mixture(_Density(_RejectingOption(), value)))


def test_nacl_molecular_weight_uses_the_constant_property() -> None:
    class _Child:
        def __init__(self) -> None:
            self.state = None

        def set_state(self, value: object) -> None:
            self.state = value

    class _Group:
        def __init__(self) -> None:
            self.option = _Child()
            self.value = _Child()

    group = _Group()
    _set_constant(group, 58.44, "nacl_molecular_weight")
    assert group.option.state == "constant"
    assert group.value.state == 58.44
    assert "molecular_weight.set_state" not in inspect.getsource(_configure_mixture)


def test_default_air_species_are_removed_so_nacl_is_first() -> None:
    class _Species:
        def __init__(self) -> None:
            self.names = ["h2o", "o2", "n2"]

        def get_object_names(self) -> list[str]:
            return list(self.names)

        def create(self, name: str) -> None:
            self.names.append(name)

        def __delitem__(self, name: str) -> None:
            self.names.remove(name)

    class _Last:
        def __init__(self, species: _Species) -> None:
            self._species = species

        def set_state(self, name: str) -> None:
            self._species.names.remove(name)
            self._species.names.append(name)

    species = _Species()

    class _SpeciesMenu:
        def __init__(self) -> None:
            self.volumetric_species = species
            self.last_species = _Last(species)

    class _Mixture:
        def __init__(self) -> None:
            self.species = _SpeciesMenu()

    _keep_nacl_water_mixture(_Mixture())
    assert species.names == ["nacl", "water"]


def test_production_solver_launch_is_unchanged() -> None:
    source = (REPO_ROOT / "scripts" / "solver_code_260616.py").read_text(
        encoding="utf-8"
    )
    assert 'mode="meshing"' in source
    assert "switch_to_solver()" in source
    assert "ro_2d_pilot" not in source
    assert "File.ReadMesh" not in source
    assert "SwitchToSolution" not in source
    pilot = (REPO_ROOT / "src" / "ro_2d_pilot" / "fluent_session.py").read_text(
        encoding="utf-8"
    )
    assert "ti-menu-load-string" in pilot
    assert "tui.switch_to_solution_mode(" not in pilot
    assert "SwitchToSolution()" not in pilot
    assert "hwtree" in pilot


