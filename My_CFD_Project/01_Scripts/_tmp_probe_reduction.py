# ==========================================================
# _tmp_probe_reduction.py
#
# Throwaway diagnostic: isolate why reduction.sum_if raises
#   api-checks-before-command-or-query: command/query is not active
#   Error Object: setup/named-expressions/temp_expr_1/get-value
# while surface report definitions succeed in the same session.
#
# Discriminates UDM-as-expression vs session-state/cold-load hypotheses.
# STEP 0b enumerates live report_type.allowed_values.
# STEP F compares plane nacl under surface-areaavg vs surface-massavg.
# Does NOT initialize, iterate, or write .cas/.dat.
#
# Windows Fluent server only. Do not run from WSL.
# ==========================================================

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import ansys.fluent.core as pyfluent

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _fluent_report_helpers import (  # noqa: E402
    create_x_normal_plane,
    list_named_object_names,
    wall_zone_reduction_locations,
)

# ---- Case under test (same as _tmp_cell_profile.py) ----
PROJECT_ROOT = SCRIPT_DIR.parent
GEO_NAME = "D2450_a45_7c_brg110"
CASE_NAME = "u0p2_p6M__mesh_max085_min006_cpg5_bl4"
CASE_PATH = PROJECT_ROOT / "03_Results" / GEO_NAME / CASE_NAME
FINAL_CASE_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.cas.h5"
FINAL_DATA_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.dat.h5"

# Pinned in repo root requirements.txt (also printed at runtime).
PINNED_PYFLUENT_FILE = "requirements.txt"
PINNED_PYFLUENT_VERSION = "ansys-fluent-core==0.38.0"

# One spacer span + one buffer span (meters).
SPACER_4_LABEL = "spacer_4"
SPACER_4_X_MIN_M = 0.01386
SPACER_4_X_MAX_M = 0.017325
BUFFER_OUT_2_LABEL = "buffer_out_2"
BUFFER_OUT_2_X_MIN_M = 0.031185
BUFFER_OUT_2_X_MAX_M = 0.03465

# Plane grid — same as _tmp_cell_profile.plane_x_positions_m().
CELL_LENGTH_M = 0.003465
DOMAIN_X_MIN_M = 0.0
DOMAIN_X_MAX_M = 0.03465
N_INLET_BUFFER_CELLS = 1
N_SPACER_CELLS = 7
N_OUTLET_BUFFER_CELLS = 2
N_TOTAL_CELLS = N_INLET_BUFFER_CELLS + N_SPACER_CELLS + N_OUTLET_BUFFER_CELLS

EXPRESSIONS_STEP_A = (
    "1",
    "nacl",
    "udm-6",
    "udm-8",
    "udm-9",
    "x",
    "AbsolutePressure",
)
EXPRESSIONS_STEP_C = ("1", "udm-8")
REPORT_FIELDS_D1 = ("udm-8", "udm-6", "udm-9")

SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
SURFACE_MASS_WEIGHTED_AVG = "surface-massavg"
SURFACE_AREA = "surface-area"
FIELD_PRESSURE = "pressure"
FIELD_SALT_MASS_FRACTION = "nacl"

PRODUCT_VERSION = "25.1.0"
PROCESSOR_COUNT = 8
GRAPHICS_DRIVER = "dx11"
FLUENT_START_TIMEOUT = 300
FLUENT_HEALTH_TIMEOUT = 300

# Summary rows: (step, mode, expression, outcome) where outcome is OK|FAIL|None
SUMMARY_ROWS = []


def as_fluent_path(path):
    """Convert a path to a Fluent-friendly absolute path."""
    return os.path.abspath(path).replace("\\", "/")


def banner(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def zone_matches_base_name(zone_name, base_name):
    """Return True for base, base.1, base.2, ... zone naming."""
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def find_zones_by_base_name(zone_names, base_name):
    """Find zones whose names are base or base.N, sorted with base first."""
    matched = [name for name in zone_names if zone_matches_base_name(name, base_name)]

    def sort_key(name):
        if name == base_name:
            return (0, 0)
        suffix = name[len(base_name) + 1 :]
        if suffix.isdigit():
            return (1, int(suffix))
        return (2, suffix)

    return sorted(matched, key=sort_key)


def collect_boundary_zones(setup):
    """Collect boundary zone names grouped by boundary type."""
    boundary_type_names = [
        "velocity_inlet",
        "pressure_outlet",
        "pressure_inlet",
        "mass_flow_inlet",
        "mass_flow_outlet",
        "wall",
        "periodic",
        "shadow",
        "symmetry",
        "interface",
        "interior",
        "outflow",
    ]
    zones_by_type = {}
    for boundary_type in boundary_type_names:
        try:
            boundary_object = getattr(setup.boundary_conditions, boundary_type)
        except Exception:
            continue
        names = list_named_object_names(
            named_object=boundary_object,
            object_label=f"boundary_conditions.{boundary_type}",
        )
        if names:
            zones_by_type[boundary_type] = names
    return zones_by_type


def find_first_number(obj):
    """Recursively find the first numeric value inside PyFluent compute result."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    if isinstance(obj, dict):
        for value in obj.values():
            found = find_first_number(value)
            if found is not None:
                return found
    if isinstance(obj, (list, tuple)):
        for value in obj:
            found = find_first_number(value)
            if found is not None:
                return found
    return None


def create_or_update_surface_report(
    solution, report_name, report_type, field_name, surface_names
):
    """Create/update a surface report definition (_tmp_cell_profile style)."""
    if not surface_names:
        raise ValueError(f"Cannot create {report_name}: no surfaces.")

    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")

    if report_name in names:
        rd = group[report_name]
        print(f"Updating surface report: {report_name}")
    else:
        rd = group.create(report_name)
        print(f"Creating surface report: {report_name}")

    rd.report_type = report_type
    if field_name is not None:
        rd.field = field_name
    rd.surface_names = list(surface_names)
    rd.per_surface = False
    return report_name


def compute_one_report(solution, report_name, verbose=False):
    """Compute one report definition and return its first numeric value."""
    result = solution.report_definitions.compute(report_defs=[report_name])
    if verbose:
        print(f"Raw compute result for {report_name}: {result!r}")
    value = find_first_number(result)
    if value is None:
        print(f"Warning: could not extract numeric value from report {report_name}.")
    return value, result


def x_range_condition(x_min_m, x_max_m):
    """Same condition string shape as area_weighted_segment_metrics."""
    return (
        f"AND(x >= {float(x_min_m)!r} [m], "
        f"x <= {float(x_max_m)!r} [m])"
    )


def plane_x_positions_m():
    """11 planes at cell boundaries: 0, 3.465, ..., 34.65 mm."""
    return [
        DOMAIN_X_MIN_M + i * CELL_LENGTH_M for i in range(N_TOTAL_CELLS + 1)
    ]


def fmt_table_value(value):
    """Format a table cell; raw repr for None, never coerce to 0.0."""
    if value is None:
        return repr(None)
    return f"{value}"


def record_summary(step, mode, expression, outcome):
    SUMMARY_ROWS.append((step, mode, expression, outcome))


def report_exception(exc, expected_types=(RuntimeError,)):
    """Print exception type/message; traceback for unexpected types."""
    print(f"FAIL {type(exc).__name__}: {exc}")
    if not isinstance(exc, expected_types):
        traceback.print_exc()


def try_sum_if(
    reduction,
    *,
    step,
    mode,
    expression,
    locations,
    condition=None,
    omit_condition=False,
):
    """Call sum_if once; never coerces None to 0.0."""
    print(f"\nexpression={expression!r}  mode={mode!r}")
    try:
        kwargs = {
            "expression": expression,
            "locations": list(locations),
            "weight": "Area",
        }
        if not omit_condition:
            kwargs["condition"] = condition
        value = reduction.sum_if(**kwargs)
        if value is None:
            print(f"OK  value={value!r}   is_none=True")
            record_summary(step, mode, expression, "None")
        else:
            print(f"OK  value={value!r}   is_none=False")
            record_summary(step, mode, expression, "OK")
        return value
    except Exception as exc:
        report_exception(exc)
        record_summary(step, mode, expression, "FAIL")
        return None


def print_pyfluent_versions():
    banner("STEP 0 — setup / versions")
    installed = getattr(pyfluent, "__version__", None)
    if installed is None:
        try:
            from importlib.metadata import version

            installed = version("ansys-fluent-core")
        except Exception as exc:
            installed = f"<unavailable: {type(exc).__name__}: {exc}>"
    print(f"Pinned pyfluent ({PINNED_PYFLUENT_FILE}): {PINNED_PYFLUENT_VERSION}")
    print(f"Installed ansys-fluent-core / pyfluent: {installed}")
    print(f"Fluent product_version (launch arg): {PRODUCT_VERSION}")


def probe_report_type_allowed_values(solution):
    """STEP 0b: print live surface report_type allowed_values, then delete."""
    banner("STEP 0b — enumerate allowed surface report_type values")
    throwaway_name = "tmp_probe_rd_allowed_types"
    group = solution.report_definitions.surface
    try:
        names = list_named_object_names(group, "solution.report_definitions.surface")
        if throwaway_name in names:
            try:
                group.delete(throwaway_name)
            except Exception as exc:
                print(f"Could not delete pre-existing {throwaway_name}: {exc}")

        rd = group.create(throwaway_name)
        print(f"Created throwaway surface report: {throwaway_name}")
        report_type_obj = rd.report_type
        print(f"report_type object type: {type(report_type_obj)!r}")

        allowed = None
        # Try method form first, then property/attribute form.
        try:
            maybe = report_type_obj.allowed_values
            if callable(maybe):
                print("Using report_type.allowed_values() (method)")
                allowed = maybe()
            else:
                print("Using report_type.allowed_values (property/attribute)")
                allowed = maybe
        except Exception as exc:
            print(
                f"allowed_values access failed: {type(exc).__name__}: {exc}"
            )
            print(f"dir(report_type): {dir(report_type_obj)}")
            raise

        if allowed is None:
            print("allowed_values returned None")
            record_summary("0b", "report_type", "allowed_values", "None")
        else:
            print("allowed_values (one string per line):")
            try:
                for item in list(allowed):
                    print(f"  {item}")
            except TypeError:
                print(f"  (non-iterable) {allowed!r}")
            record_summary("0b", "report_type", "allowed_values", "OK")
    except Exception as exc:
        report_exception(exc, expected_types=())
        record_summary("0b", "report_type", "allowed_values", "FAIL")
    finally:
        try:
            names = list_named_object_names(
                group, "solution.report_definitions.surface"
            )
            if throwaway_name in names:
                group.delete(throwaway_name)
                print(f"Deleted throwaway surface report: {throwaway_name}")
        except Exception as cleanup_exc:
            print(
                f"Warning: could not delete {throwaway_name}: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )


def try_create_iso_clip_settings(solver, clip_name, zone_names, x_min_m, x_max_m):
    """Best-effort settings API for iso_clip (attribute names may vary)."""
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        try:
            iso_group.delete(clip_name)
            print(f"Deleted existing iso-clip: {clip_name}")
        except Exception as exc:
            print(f"Could not delete iso-clip {clip_name}: {exc}")

    iso_group.create(clip_name)
    clip = iso_group[clip_name]
    print(f"Created iso-clip named object: {clip_name}")
    try:
        print(f"iso_clip[{clip_name}] attrs sample: {[a for a in dir(clip) if not a.startswith('_')][:40]}")
    except Exception as exc:
        print(f"Could not list clip attrs: {exc}")

    # Probe common attribute names used across Fluent settings generations.
    set_attempts = [
        ("field", "x-coordinate"),
        ("surfaces", list(zone_names)),
        ("surface_names", list(zone_names)),
        ("zones", list(zone_names)),
        ("minimum", float(x_min_m)),
        ("maximum", float(x_max_m)),
        ("min", float(x_min_m)),
        ("max", float(x_max_m)),
        ("range", [float(x_min_m), float(x_max_m)]),
        ("iso_values", [float(x_min_m), float(x_max_m)]),
    ]
    for attr, value in set_attempts:
        if not hasattr(clip, attr):
            print(f"  skip unsettable attr {attr!r} (missing)")
            continue
        try:
            setattr(clip, attr, value)
            print(f"  set {attr}={value!r}")
        except Exception as exc:
            print(f"  set {attr} failed: {type(exc).__name__}: {exc}")

    try:
        print(f"iso_clip[{clip_name}] state: {clip.get_state()}")
    except Exception as exc:
        print(f"Could not get_state for {clip_name}: {exc}")
    return clip_name


def try_create_iso_clip_tui(solver, clip_name, zone_names, x_min_m, x_max_m):
    """TUI fallback via solver.execute_tui (repo pattern in solver_code / 07).

    NOTE: Could not verify the exact TUI argument order for Fluent 25.1
    iso-clip from this WSL repo (no Fluent here). The command below follows
    the common Fluent prompt order:
      /surface/iso-clip <name> <field> <surfaces...> () <min> <max>
    If it fails, try reordering on the Windows side against the live TUI help.
    """
    surfaces_token = " ".join(zone_names)
    command = (
        f"/surface/iso-clip {clip_name} x-coordinate "
        f"{surfaces_token} () {float(x_min_m)} {float(x_max_m)}"
    )
    print(f"Trying TUI iso-clip via execute_tui: {command}")
    solver.execute_tui(command)
    print(f"TUI iso-clip succeeded: {clip_name}")
    return clip_name


def main():
    print_pyfluent_versions()

    if not FINAL_CASE_FILE.is_file():
        raise FileNotFoundError(f"Final case file not found: {FINAL_CASE_FILE}")
    if not FINAL_DATA_FILE.is_file():
        raise FileNotFoundError(f"Final data file not found: {FINAL_DATA_FILE}")

    print(f"\nCase path: {CASE_PATH}")
    print(f"Final case: {FINAL_CASE_FILE}")
    print(
        f"Spans: {SPACER_4_LABEL}=[{SPACER_4_X_MIN_M}, {SPACER_4_X_MAX_M}], "
        f"{BUFFER_OUT_2_LABEL}=[{BUFFER_OUT_2_X_MIN_M}, {BUFFER_OUT_2_X_MAX_M}]"
    )
    print("No initialize / iterate / write will be performed.")

    pyfluent.config.check_health_timeout = FLUENT_HEALTH_TIMEOUT

    meshing = None
    solver = None
    original_cwd = os.getcwd()

    top_zones = []
    top_locations = []
    solution = None
    reduction = None

    try:
        os.chdir(CASE_PATH)

        try:
            print("\nLaunching Fluent in meshing mode, then switching to solver...")
            print(f"product_version = {PRODUCT_VERSION}")
            print(f"processor_count = {PROCESSOR_COUNT}")
            meshing = pyfluent.launch_fluent(
                product_version=PRODUCT_VERSION,
                mode="meshing",
                dimension=3,
                precision="double",
                processor_count=PROCESSOR_COUNT,
                ui_mode="gui",
                graphics_driver=GRAPHICS_DRIVER,
                start_timeout=FLUENT_START_TIMEOUT,
                cwd=as_fluent_path(CASE_PATH),
            )
            print("Meshing session launched successfully.")
            solver = meshing.switch_to_solver()
            meshing = None
            print("Switched to solver successfully.")

            setup = solver.settings.setup
            solution = solver.settings.solution

            print("Reading final case/data (read-only; no iterate / no write)...")
            solver.settings.file.read_case_data(
                file_name=as_fluent_path(FINAL_CASE_FILE)
            )
            print("Final case/data loaded.")
        except Exception as exc:
            report_exception(exc, expected_types=())
            print("SETUP FAILED: cannot continue probes without a live session.")
            return 1

        # Zone / reduction location resolution
        try:
            boundary_zones_by_type = collect_boundary_zones(setup)
            boundary_zone_names = sorted(
                zone_name
                for names in boundary_zones_by_type.values()
                for zone_name in names
            )
            top_zones = find_zones_by_base_name(boundary_zone_names, "wall_top_mem")
            print("wall_top_mem zones:", top_zones)
            if not top_zones:
                raise ValueError("No wall_top_mem zones found.")
            top_locations = wall_zone_reduction_locations(setup, top_zones)
            print(f"Resolved {len(top_locations)} wall_top_mem reduction location(s).")
            reduction = solver.fields.reduction
        except Exception as exc:
            report_exception(exc, expected_types=())
            print("ZONE RESOLUTION FAILED: sum_if probes will be skipped.")

        spacer_condition = x_range_condition(SPACER_4_X_MIN_M, SPACER_4_X_MAX_M)
        buffer_condition = x_range_condition(
            BUFFER_OUT_2_X_MIN_M, BUFFER_OUT_2_X_MAX_M
        )

        # ---- STEP 0b ----
        if solution is not None:
            probe_report_type_allowed_values(solution)
        else:
            banner("STEP 0b — enumerate allowed surface report_type values")
            print("SKIP STEP 0b: solution unavailable.")
            record_summary("0b", "report_type", "allowed_values", "FAIL")

        # ---- STEP A ----
        banner(
            f"STEP A — sum_if expressions on {SPACER_4_LABEL} "
            f"condition={spacer_condition!r}"
        )
        if reduction is not None and top_locations:
            for expr in EXPRESSIONS_STEP_A:
                try_sum_if(
                    reduction,
                    step="A",
                    mode=SPACER_4_LABEL,
                    expression=expr,
                    locations=top_locations,
                    condition=spacer_condition,
                )
        else:
            print("SKIP STEP A: reduction/locations unavailable.")
            for expr in EXPRESSIONS_STEP_A:
                record_summary("A", SPACER_4_LABEL, expr, "FAIL")

        # ---- STEP B ----
        banner(
            f"STEP B — sum_if expressions on {BUFFER_OUT_2_LABEL} "
            f"condition={buffer_condition!r}"
        )
        if reduction is not None and top_locations:
            for expr in EXPRESSIONS_STEP_A:
                try_sum_if(
                    reduction,
                    step="B",
                    mode=BUFFER_OUT_2_LABEL,
                    expression=expr,
                    locations=top_locations,
                    condition=buffer_condition,
                )
        else:
            print("SKIP STEP B: reduction/locations unavailable.")
            for expr in EXPRESSIONS_STEP_A:
                record_summary("B", BUFFER_OUT_2_LABEL, expr, "FAIL")

        # ---- STEP C ----
        banner("STEP C — bare sum_if with condition omitted")
        if reduction is not None and top_locations:
            for expr in EXPRESSIONS_STEP_C:
                try_sum_if(
                    reduction,
                    step="C",
                    mode="no_condition",
                    expression=expr,
                    locations=top_locations,
                    omit_condition=True,
                )
        else:
            print("SKIP STEP C: reduction/locations unavailable.")
            for expr in EXPRESSIONS_STEP_C:
                record_summary("C", "no_condition", expr, "FAIL")

        # ---- STEP D1 ----
        banner(
            "STEP D1 — surface report definitions with field=udm-N "
            "on whole wall_top_mem (no clipping)"
        )
        if solution is not None and top_zones:
            for field in REPORT_FIELDS_D1:
                report_name = f"tmp_probe_rd_{field.replace('-', '_')}"
                print(f"\nreport field={field!r} surfaces={top_zones!r}")
                try:
                    create_or_update_surface_report(
                        solution,
                        report_name,
                        SURFACE_AREA_WEIGHTED_AVG,
                        field,
                        top_zones,
                    )
                    value, raw = compute_one_report(solution, report_name)
                    print(f"OK  value={value!r}   is_none={value is None}")
                    print(f"raw compute repr={raw!r}")
                    record_summary(
                        "D1",
                        "wall_top_mem_report",
                        field,
                        "None" if value is None else "OK",
                    )
                except Exception as exc:
                    report_exception(exc)
                    record_summary("D1", "wall_top_mem_report", field, "FAIL")
        else:
            print("SKIP STEP D1: solution/top_zones unavailable.")
            for field in REPORT_FIELDS_D1:
                record_summary("D1", "wall_top_mem_report", field, "FAIL")

        # ---- STEP D2 ----
        banner(
            "STEP D2 — iso_clip on wall_top_mem x in spacer_4, then "
            "areaavg(udm-8) + surface-area"
        )
        print(
            f"Pinned package from {PINNED_PYFLUENT_FILE}: "
            f"{PINNED_PYFLUENT_VERSION}"
        )
        clip_name = "tmp_clip_c4_top"
        clip_ready = False
        if solver is not None and top_zones:
            try:
                surfaces = solver.settings.results.surfaces
                has_iso_clip = hasattr(surfaces, "iso_clip")
                print(f"solver.settings.results.surfaces.iso_clip exists: {has_iso_clip}")
                if has_iso_clip:
                    try:
                        try_create_iso_clip_settings(
                            solver,
                            clip_name,
                            top_zones,
                            SPACER_4_X_MIN_M,
                            SPACER_4_X_MAX_M,
                        )
                        clip_ready = True
                    except Exception as exc:
                        report_exception(exc)
                        print("Settings API iso_clip create failed; trying TUI.")
                        clip_ready = False
                else:
                    print(
                        "iso_clip settings API absent in this session "
                        f"(pinned {PINNED_PYFLUENT_VERSION}). Falling back to TUI."
                    )

                if not clip_ready:
                    try:
                        # Uses solver.execute_tui — same helper style as
                        # solver_code_260616.py / 07_batch_solver_rerun.py.
                        try_create_iso_clip_tui(
                            solver,
                            clip_name,
                            top_zones,
                            SPACER_4_X_MIN_M,
                            SPACER_4_X_MAX_M,
                        )
                        clip_ready = True
                    except Exception as exc:
                        report_exception(exc)
                        record_summary("D2", "iso_clip_create", clip_name, "FAIL")
            except Exception as exc:
                report_exception(exc)
                record_summary("D2", "iso_clip_create", clip_name, "FAIL")
        else:
            print("SKIP STEP D2 create: solver/top_zones unavailable.")
            record_summary("D2", "iso_clip_create", clip_name, "FAIL")

        if clip_ready and solution is not None:
            record_summary("D2", "iso_clip_create", clip_name, "OK")
            # area-weighted average of udm-8 on the clip
            try:
                print(f"\nCompute areaavg(udm-8) on {clip_name!r}")
                create_or_update_surface_report(
                    solution,
                    "tmp_probe_clip_udm8",
                    SURFACE_AREA_WEIGHTED_AVG,
                    "udm-8",
                    [clip_name],
                )
                value, raw = compute_one_report(solution, "tmp_probe_clip_udm8")
                print(f"OK  udm-8 value={value!r}   is_none={value is None}")
                print(f"raw compute repr={raw!r}")
                record_summary(
                    "D2",
                    "clip_areaavg",
                    "udm-8",
                    "None" if value is None else "OK",
                )
            except Exception as exc:
                report_exception(exc)
                record_summary("D2", "clip_areaavg", "udm-8", "FAIL")

            # surface-area on the clip (field=None) for segment area cross-check
            try:
                print(f"\nCompute surface-area on {clip_name!r}")
                create_or_update_surface_report(
                    solution,
                    "tmp_probe_clip_area",
                    SURFACE_AREA,
                    None,
                    [clip_name],
                )
                value, raw = compute_one_report(solution, "tmp_probe_clip_area")
                print(f"OK  area value={value!r}   is_none={value is None}")
                print(f"raw compute repr={raw!r}")
                record_summary(
                    "D2",
                    "clip_surface_area",
                    "surface-area",
                    "None" if value is None else "OK",
                )
            except Exception as exc:
                report_exception(exc)
                record_summary("D2", "clip_surface_area", "surface-area", "FAIL")
        elif not clip_ready:
            print("SKIP STEP D2 reports: iso-clip was not created.")
            record_summary("D2", "clip_areaavg", "udm-8", "FAIL")
            record_summary("D2", "clip_surface_area", "surface-area", "FAIL")

        # ---- STEP E ----
        banner(
            f"STEP E — re-run sum_if(udm-8) on {SPACER_4_LABEL} after STEP D"
        )
        if reduction is not None and top_locations:
            try_sum_if(
                reduction,
                step="E",
                mode=SPACER_4_LABEL,
                expression="udm-8",
                locations=top_locations,
                condition=spacer_condition,
            )
        else:
            print("SKIP STEP E: reduction/locations unavailable.")
            record_summary("E", SPACER_4_LABEL, "udm-8", "FAIL")

        # ---- STEP F ----
        # Rationale: in the previous run, area-weighted nacl DECREASED across
        # the outlet buffer (planes 8 -> 9 -> 10: 0.03512255 -> 0.03510113 ->
        # 0.03508671) where there is no membrane and no species source, which
        # is impossible for a conserved bulk quantity. This step measures both
        # weightings side by side on identical planes to confirm that
        # mass-weighted (mixing-cup) removes the artificial drift.
        #
        # Note: Fluent's surface Mass-Weighted Average weights by rho*|v.dA|
        # (absolute value), so reverse flow through a plane still carries
        # positive weight and exact conservation is not guaranteed. Do NOT
        # try to correct for this here — just record the numbers.
        banner(
            "STEP F — plane bulk concentration: "
            "surface-areaavg vs surface-massavg"
        )
        plane_rows = []
        if solver is not None and solution is not None:
            for i, x_m in enumerate(plane_x_positions_m()):
                plane_name = f"tmp_fplane_x_{i:02d}"
                row = {
                    "plane_index": i,
                    "x_m": x_m,
                    "area_m2": None,
                    "pressure_Pa": None,
                    "dP_from_prev_Pa": None,
                    "nacl_areaavg": None,
                    "nacl_massavg": None,
                    "massavg_minus_areaavg": None,
                }
                try:
                    create_x_normal_plane(solver, plane_name, x_m)
                except Exception as plane_exc:
                    report_exception(plane_exc)
                    record_summary("F", plane_name, "create_plane", "FAIL")
                    plane_rows.append(row)
                    continue
                record_summary("F", plane_name, "create_plane", "OK")

                # pressure, surface-areaavg
                try:
                    p_report = f"tmp_fp_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        p_report,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_PRESSURE,
                        [plane_name],
                    )
                    row["pressure_Pa"], _ = compute_one_report(solution, p_report)
                    record_summary(
                        "F",
                        plane_name,
                        "pressure_areaavg",
                        "None" if row["pressure_Pa"] is None else "OK",
                    )
                except Exception as exc:
                    report_exception(exc)
                    record_summary("F", plane_name, "pressure_areaavg", "FAIL")

                # nacl, surface-areaavg
                try:
                    ya_report = f"tmp_fnacl_a_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        ya_report,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_SALT_MASS_FRACTION,
                        [plane_name],
                    )
                    row["nacl_areaavg"], _ = compute_one_report(
                        solution, ya_report
                    )
                    record_summary(
                        "F",
                        plane_name,
                        "nacl_areaavg",
                        "None" if row["nacl_areaavg"] is None else "OK",
                    )
                except Exception as exc:
                    report_exception(exc)
                    record_summary("F", plane_name, "nacl_areaavg", "FAIL")

                # nacl, surface-massavg
                try:
                    ym_report = f"tmp_fnacl_m_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        ym_report,
                        SURFACE_MASS_WEIGHTED_AVG,
                        FIELD_SALT_MASS_FRACTION,
                        [plane_name],
                    )
                    row["nacl_massavg"], _ = compute_one_report(
                        solution, ym_report
                    )
                    record_summary(
                        "F",
                        plane_name,
                        "nacl_massavg",
                        "None" if row["nacl_massavg"] is None else "OK",
                    )
                except Exception as exc:
                    report_exception(exc)
                    record_summary("F", plane_name, "nacl_massavg", "FAIL")

                # plane area, surface-area
                try:
                    area_report = f"tmp_farea_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        area_report,
                        SURFACE_AREA,
                        None,
                        [plane_name],
                    )
                    row["area_m2"], _ = compute_one_report(solution, area_report)
                    record_summary(
                        "F",
                        plane_name,
                        "surface-area",
                        "None" if row["area_m2"] is None else "OK",
                    )
                except Exception as exc:
                    report_exception(exc)
                    record_summary("F", plane_name, "surface-area", "FAIL")

                if (
                    row["nacl_massavg"] is not None
                    and row["nacl_areaavg"] is not None
                ):
                    row["massavg_minus_areaavg"] = (
                        float(row["nacl_massavg"]) - float(row["nacl_areaavg"])
                    )

                if plane_rows and row["pressure_Pa"] is not None:
                    prev = plane_rows[-1].get("pressure_Pa")
                    row["dP_from_prev_Pa"] = (
                        None
                        if prev is None
                        else float(row["pressure_Pa"]) - float(prev)
                    )

                plane_rows.append(row)

            # Aligned table
            cols = [
                "plane_index",
                "x_m",
                "area_m2",
                "pressure_Pa",
                "dP_from_prev_Pa",
                "nacl_areaavg",
                "nacl_massavg",
                "massavg_minus_areaavg",
            ]
            print("\nSTEP F TABLE")
            widths = {
                col: max(
                    len(col),
                    max(
                        (len(fmt_table_value(r.get(col))) for r in plane_rows),
                        default=0,
                    ),
                )
                for col in cols
            }
            header = "  ".join(col.ljust(widths[col]) for col in cols)
            print(header)
            print("-" * len(header))
            for row in plane_rows:
                print(
                    "  ".join(
                        fmt_table_value(row.get(col)).ljust(widths[col])
                        for col in cols
                    )
                )
        else:
            print("SKIP STEP F: solver/solution unavailable.")
            record_summary("F", "planes", "all", "FAIL")

        # ---- Summary ----
        banner("SUMMARY (expression -> OK/FAIL/None)")
        print("step  mode                      expression            outcome")
        print("-" * 72)
        for step, mode, expression, outcome in SUMMARY_ROWS:
            print(
                f"{step:<5} {mode:<25} {expression:<20} {outcome}"
            )
        # One-line compact form requested by the task.
        compact = "; ".join(
            f"{step}/{mode}/{expression}->{outcome}"
            for step, mode, expression, outcome in SUMMARY_ROWS
        )
        print("\nONE_LINE_SUMMARY: " + compact)

        print(
            "\nDone. No iterate and no case/data write were performed."
        )
        return 0
    finally:
        if meshing is not None:
            try:
                meshing.exit()
            except Exception as cleanup_error:
                print(f"Warning: meshing.exit() failed: {cleanup_error}")
        if solver is not None:
            try:
                solver.exit()
            except Exception as cleanup_error:
                print(f"Warning: solver.exit() failed: {cleanup_error}")
        try:
            os.chdir(original_cwd)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"SCRIPT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
