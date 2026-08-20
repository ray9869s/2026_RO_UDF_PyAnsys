"""Live probe: species-numerics context vs species-0 URF writability.

Reproduces the production meshing-to-solver launch path, then runs a 2x2
matrix over {template, after replace_mesh} x {expert off, expert on} without
saving case/data files or running iterations.

Use --dry-run to print the planned launch and phases without starting Fluent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ro.solver_common import path_to_fluent_str


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_TEMPLATE_CASE = (
    PROJECT_ROOT / "01_Templates" / "template_RO_setup.cas.h5"
)
DEFAULT_MESH_FILE = (
    PROJECT_ROOT
    / "03_Results"
    / "Sin_ST"
    / "mesh_max100_min006_cpg3_bl3"
    / "Sin_ST_mesh_max100_min006_cpg3_bl3.msh.h5"
)
DEFAULT_PRODUCT_VERSION = "25.1.0"
# Avoid processor_count=1 (slow replace_mesh) and 50 (equals stored partition
# count on typical production meshes).
DEFAULT_PROCESSOR_COUNT = 8
DEFAULT_GRAPHICS_DRIVER = "dx11"
DEFAULT_START_TIMEOUT = 300

MATRIX_COLUMNS = (
    "mesh_state",
    "expert_requested",
    "expert_readback",
    "requested",
    "before",
    "after",
    "status",
    "error",
)


def alternate_relaxation(value):
    value = float(value)
    return 0.5 if abs(value - 0.5) > 1.0e-9 else 0.6


def leaf_value(parent, name):
    state = parent.get_state()
    if not isinstance(state, dict) or name not in state:
        raise RuntimeError(f"{name!r} absent from state: {state!r}")
    return state[name]


class BoolLeafAdapter:
    """Expose a bool setting numerically to set_and_verify_leaf."""

    def __init__(self, parent, attr_name):
        object.__setattr__(self, "_parent", parent)
        object.__setattr__(self, "_attr_name", attr_name)

    def get_state(self):
        actual = leaf_value(self._parent, self._attr_name)
        return {"value": 1.0 if actual else 0.0}

    def __setattr__(self, name, value):
        if name != "value":
            object.__setattr__(self, name, value)
            return
        setattr(self._parent, self._attr_name, bool(value))


class DictKeyLeafAdapter:
    """Expose a dict-like settings key to set_and_verify_leaf."""

    def __init__(self, container, key):
        object.__setattr__(self, "_container", container)
        object.__setattr__(self, "_key", key)

    def get_state(self):
        state = self._container.get_state()
        if not isinstance(state, dict) or self._key not in state:
            raise RuntimeError(f"{self._key!r} absent from state: {state!r}")
        return {"value": state[self._key]}

    def __setattr__(self, name, value):
        if name != "value":
            object.__setattr__(self, name, value)
            return
        try:
            self._container[self._key] = value
        except Exception:
            self._container.set_state({self._key: value})


def explicit_species_adapter(solution):
    container = (
        solution.controls
        .pseudo_time_explicit_relaxation_factor
        .global_dt_pseudo_relax
    )
    return DictKeyLeafAdapter(container, "species-0")


def read_expert(adapter):
    return bool(adapter.get_state()["value"])


def show(label, getter):
    try:
        value = getter()
        if hasattr(value, "get_state"):
            value = value.get_state()
        print(f"{label} = {value!r}")
        return value
    except Exception as exc:
        print(f"{label} = UNAVAILABLE: {type(exc).__name__}: {exc}")
        return None


def matrix_row_from_outcome(
    *,
    mesh_state,
    expert_requested,
    expert_readback,
    requested,
    outcome,
):
    """Build one 2x2 summary row, including any set_and_verify error text."""
    return {
        "mesh_state": mesh_state,
        "expert_requested": expert_requested,
        "expert_readback": expert_readback,
        "requested": requested,
        "before": outcome.get("before"),
        "after": outcome.get("after"),
        "status": outcome.get("status"),
        "error": outcome.get("error") or "",
    }


def format_matrix_table(rows):
    """Return the labelled 2x2 summary as printable lines."""
    headers = (
        ("mesh state", 28),
        ("expert", 8),
        ("expert readback", 16),
        ("requested", 12),
        ("before", 12),
        ("after", 12),
        ("status", 22),
        ("error", 48),
    )
    header_line = " ".join(
        f"{title:<{width}}" for title, width in headers
    ).rstrip()
    divider = "-" * max(len(header_line), 160)
    lines = [
        "=== EXPLICIT SPECIES-0 2x2 RESULTS ===",
        header_line,
        divider,
    ]
    for row in rows:
        lines.append(
            f"{str(row['mesh_state']):<28} "
            f"{str(row['expert_requested']):<8} "
            f"{str(row['expert_readback']):<16} "
            f"{str(row['requested']):<12} "
            f"{str(row['before']):<12} "
            f"{str(row['after']):<12} "
            f"{str(row['status']):<22} "
            f"{str(row['error'])}"
        )
    return lines


def build_dry_run_plan(args):
    """Describe the live probe without launching Fluent."""
    return {
        "action": "launch Fluent meshing-to-solver probe (no save, no iterate)",
        "product_version": args.product_version,
        "processor_count": args.processor_count,
        "graphics_driver": args.graphics_driver,
        "ui_mode": args.ui_mode,
        "start_timeout": args.start_timeout,
        "template_case": str(args.template_case),
        "mesh_file": str(args.mesh_file),
        "template_case_exists": Path(args.template_case).is_file(),
        "mesh_file_exists": Path(args.mesh_file).is_file(),
        "phases": [
            {
                "name": "TEMPLATE_BEFORE_REPLACE",
                "actions": [
                    "read_case(template)",
                    "explicit species-0 write with expert OFF",
                    "explicit species-0 write with expert ON",
                    "restore expert and explicit relaxation",
                ],
            },
            {
                "name": "AFTER_REPLACE_MESH",
                "actions": [
                    "replace_mesh(mesh_file)",
                    "report p-v coupling and pseudo-time method state",
                    "explicit species-0 write with expert OFF",
                    "explicit species-0 write with expert ON",
                    "implicit species-0 path with expert ON",
                    "restore expert, explicit, and implicit leaves",
                ],
            },
        ],
        "summary": (
            "Print a labelled 2x2 table with mesh_state, expert, "
            "requested/before/after, status, and error."
        ),
    }


def run_explicit_matrix_phase(
    *,
    phase,
    setup,
    solution,
    set_and_verify_leaf,
    probe_implicit,
    matrix_rows,
    implicit_results,
):
    """Run expert-off and expert-on explicit writes, restoring all state."""
    species = setup.models.species
    species_options = species.options
    expert_adapter = BoolLeafAdapter(
        species_options,
        "species_transport_expert",
    )

    expert_before = read_expert(expert_adapter)
    explicit_adapter = explicit_species_adapter(solution)
    explicit_before = explicit_adapter.get_state()["value"]
    requested = alternate_relaxation(explicit_before)

    implicit_parent = None
    implicit_before = None

    print(f"\n=== {phase}: INITIAL STATE ===")
    show(f"{phase}.species_options", lambda: species_options)
    show(
        f"{phase}.species_transport_expert_options",
        lambda: species.species_transport_expert_options,
    )
    print(f"{phase}.explicit_species_0_before = {explicit_before!r}")

    try:
        print(f"\n=== {phase}: EXPERT OFF ===")
        set_and_verify_leaf(
            expert_adapter,
            "value",
            0.0,
            f"{phase}.set_expert_off",
        )
        expert_off_actual = read_expert(expert_adapter)

        try:
            explicit_adapter = explicit_species_adapter(solution)
            off_outcome = set_and_verify_leaf(
                explicit_adapter,
                "value",
                requested,
                f"{phase}.explicit_write_expert_off",
            )
        except Exception as exc:
            off_outcome = {
                "status": "ERROR",
                "before": None,
                "after": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

        matrix_rows.append(
            matrix_row_from_outcome(
                mesh_state=phase,
                expert_requested="OFF",
                expert_readback=expert_off_actual,
                requested=requested,
                outcome=off_outcome,
            )
        )

        explicit_adapter = explicit_species_adapter(solution)
        set_and_verify_leaf(
            explicit_adapter,
            "value",
            explicit_before,
            f"{phase}.restore_explicit_after_expert_off",
        )

        print(f"\n=== {phase}: EXPERT ON ===")
        set_and_verify_leaf(
            expert_adapter,
            "value",
            1.0,
            f"{phase}.set_expert_on",
        )
        expert_on_actual = read_expert(expert_adapter)

        try:
            explicit_adapter = explicit_species_adapter(solution)
            on_outcome = set_and_verify_leaf(
                explicit_adapter,
                "value",
                requested,
                f"{phase}.explicit_write_expert_on",
            )
        except Exception as exc:
            on_outcome = {
                "status": "ERROR",
                "before": None,
                "after": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

        matrix_rows.append(
            matrix_row_from_outcome(
                mesh_state=phase,
                expert_requested="ON",
                expert_readback=expert_on_actual,
                requested=requested,
                outcome=on_outcome,
            )
        )

        if probe_implicit:
            print(f"\n=== {phase}: IMPLICIT PATH WITH EXPERT ON ===")
            try:
                implicit_parent = (
                    solution.controls.advanced.expert
                    .pseudo_time_method_usage.global_dt["species-0"]
                )
                implicit_before = leaf_value(
                    implicit_parent,
                    "implicit_under_relaxation_factor",
                )
                implicit_outcome = set_and_verify_leaf(
                    implicit_parent,
                    "implicit_under_relaxation_factor",
                    alternate_relaxation(implicit_before),
                    f"{phase}.implicit_write_expert_on",
                )
                implicit_results.append(implicit_outcome)
            except Exception as exc:
                implicit_results.append({
                    "label": f"{phase}.implicit_write_expert_on",
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    finally:
        print(f"\n=== {phase}: RESTORE ===")

        if implicit_parent is not None and implicit_before is not None:
            set_and_verify_leaf(
                implicit_parent,
                "implicit_under_relaxation_factor",
                implicit_before,
                f"{phase}.restore_implicit",
            )

        try:
            explicit_adapter = explicit_species_adapter(solution)
            set_and_verify_leaf(
                explicit_adapter,
                "value",
                explicit_before,
                f"{phase}.restore_explicit",
            )
        finally:
            set_and_verify_leaf(
                expert_adapter,
                "value",
                1.0 if expert_before else 0.0,
                f"{phase}.restore_expert",
            )

        show(f"{phase}.final_species_options", lambda: species_options)


def run_live_probe(args):
    """Launch Fluent and execute the two-phase species-numerics probe."""
    import ansys.fluent.core as pyfluent
    from solver_code_260616 import set_and_verify_leaf

    template_case = Path(args.template_case)
    mesh_file = Path(args.mesh_file)
    if not template_case.is_file():
        raise FileNotFoundError(f"Template case not found: {template_case}")
    if not mesh_file.is_file():
        raise FileNotFoundError(f"Mesh file not found: {mesh_file}")

    matrix_rows = []
    implicit_results = []
    meshing = None
    solver = None

    try:
        meshing = pyfluent.launch_fluent(
            product_version=args.product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=args.processor_count,
            ui_mode=args.ui_mode,
            graphics_driver=args.graphics_driver,
            start_timeout=args.start_timeout,
        )
        solver = meshing.switch_to_solver()
        meshing = None

        solver.settings.file.read_case(
            file_name=path_to_fluent_str(template_case)
        )
        run_explicit_matrix_phase(
            phase="TEMPLATE_BEFORE_REPLACE",
            setup=solver.settings.setup,
            solution=solver.settings.solution,
            set_and_verify_leaf=set_and_verify_leaf,
            probe_implicit=False,
            matrix_rows=matrix_rows,
            implicit_results=implicit_results,
        )

        solver.settings.file.replace_mesh(
            file_name=path_to_fluent_str(mesh_file)
        )
        setup = solver.settings.setup
        solution = solver.settings.solution

        print("\n=== ACTUAL SOLVER STATE AFTER REPLACE_MESH ===")
        show("P_V_COUPLING", lambda: solution.methods.p_v_coupling)
        show(
            "P_V_FLOW_SCHEME",
            lambda: solution.methods.p_v_coupling.flow_scheme,
        )
        show(
            "PRESSURE_VELOCITY_COUPLING_FALLBACK",
            lambda: solution.methods.pressure_velocity_coupling,
        )
        show(
            "PSEUDO_TIME_METHOD",
            lambda: solution.methods.pseudo_time_method,
        )
        show(
            "PSEUDO_TIME_METHOD_ENABLED",
            lambda: solution.methods.pseudo_time_method.enabled,
        )
        show(
            "PSEUDO_TRANSIENT_FALLBACK",
            lambda: solution.methods.pseudo_transient,
        )
        show(
            "RUN_CALCULATION_PSEUDO_TIME_SETTINGS",
            lambda: solution.run_calculation.pseudo_time_settings,
        )

        run_explicit_matrix_phase(
            phase="AFTER_REPLACE_MESH",
            setup=setup,
            solution=solution,
            set_and_verify_leaf=set_and_verify_leaf,
            probe_implicit=True,
            matrix_rows=matrix_rows,
            implicit_results=implicit_results,
        )

        print()
        for line in format_matrix_table(matrix_rows):
            print(line)

        print("\n=== IMPLICIT PATH RESULT ===")
        for outcome in implicit_results:
            print(outcome)

        return {
            "matrix_rows": matrix_rows,
            "implicit_results": implicit_results,
        }
    finally:
        if solver is not None:
            solver.exit()
        elif meshing is not None:
            meshing.exit()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template-case",
        type=Path,
        default=DEFAULT_TEMPLATE_CASE,
        help="Template .cas.h5 used for the pre-replace_mesh phase.",
    )
    parser.add_argument(
        "--mesh-file",
        type=Path,
        default=DEFAULT_MESH_FILE,
        help="Mesh .msh.h5 used for replace_mesh (production path).",
    )
    parser.add_argument(
        "--product-version",
        default=DEFAULT_PRODUCT_VERSION,
    )
    parser.add_argument(
        "--processor-count",
        type=int,
        default=DEFAULT_PROCESSOR_COUNT,
        help=(
            "Fluent processor count. Default 8. Avoid 50 when that matches "
            "the stored mesh partition count."
        ),
    )
    parser.add_argument(
        "--graphics-driver",
        default=DEFAULT_GRAPHICS_DRIVER,
    )
    parser.add_argument(
        "--ui-mode",
        default="gui",
    )
    parser.add_argument(
        "--start-timeout",
        type=int,
        default=DEFAULT_START_TIMEOUT,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned probe without launching Fluent.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.dry_run:
        plan = build_dry_run_plan(args)
        print(json.dumps(plan, indent=2, default=str))
        return 0
    run_live_probe(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
