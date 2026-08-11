# ==========================================================
# _tmp_cell_profile.py
#
# Throwaway diagnostic: per-cell membrane flux + per-plane pressure /
# concentration profile for one solved case, to decide spacer cell count.
# Does NOT iterate, does NOT write .cas/.dat, does NOT touch post_config.
#
# Windows Fluent server only. Do not run from WSL.
# ==========================================================

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from pprint import pprint

import ansys.fluent.core as pyfluent

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _fluent_report_helpers import (  # noqa: E402
    create_x_normal_plane,
    list_named_object_names,
)
from _domain_layout import (  # noqa: E402
    CURRENT_EVALUATION_WINDOW,
    CURRENT_LAYOUT,
    GEOMETRY_LAYOUT_REGISTRY,
    mesh_case_name_candidates_from_dirname,
    resolve_layout,
)

# ---- Case under test (env-overridable throwaway paths) ----
# Override with CELL_PROFILE_GEO / CELL_PROFILE_CASE / CELL_PROFILE_MESH.
# Defaults keep the original D2450 throwaway target.
PROJECT_ROOT = SCRIPT_DIR.parent
GEO_NAME = os.environ.get("CELL_PROFILE_GEO", "D2450_a45_7c_brg110")
CASE_NAME = os.environ.get(
    "CELL_PROFILE_CASE",
    "u0p2_p6M__mesh_max085_min006_cpg5_bl4",
)
_DEFAULT_MESH_CASE_NAME = "mesh_max085_min006_cpg5_bl4"


def _resolve_cell_profile_mesh_case_name(geo_name: str, case_name: str) -> tuple[str, str]:
    """Return (mesh_case_name, source) for layout lookup.

    Priority: CELL_PROFILE_MESH env, then dirname candidates that hit the
    registry, else the D2450 default mesh token.
    """
    env_mesh = os.environ.get("CELL_PROFILE_MESH", "").strip()
    if env_mesh:
        return env_mesh, "env:CELL_PROFILE_MESH"
    for candidate in mesh_case_name_candidates_from_dirname(case_name):
        if (geo_name, candidate) in GEOMETRY_LAYOUT_REGISTRY:
            return candidate, "case_name"
    return _DEFAULT_MESH_CASE_NAME, "default"


MESH_CASE_NAME, MESH_CASE_NAME_SOURCE = _resolve_cell_profile_mesh_case_name(
    GEO_NAME, CASE_NAME
)
CASE_PATH = PROJECT_ROOT / "03_Results" / GEO_NAME / CASE_NAME
FINAL_CASE_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.cas.h5"
FINAL_DATA_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.dat.h5"
# Per-case CSV under the geo folder so concurrent runs do not overwrite.
CSV_PATH = CASE_PATH.parent / f"cell_profile_{CASE_NAME}.csv"

# Geometry [m] — prefer registry layout for (GEO_NAME, MESH_CASE_NAME).
# Fallback keeps the original D2450 1+7+2 / 0.003465 constants when the pair
# is unregistered so the throwaway script still runs offline.
_FALLBACK_CELL_LENGTH_M = 0.003465
_FALLBACK_DOMAIN_X_MAX_M = 0.03465
_FALLBACK_N_INLET_BUFFER_CELLS = 1
_FALLBACK_N_SPACER_CELLS = 7
_FALLBACK_N_OUTLET_BUFFER_CELLS = 2

DOMAIN_X_MIN_M = 0.0
LAYOUT_SOURCE = "fallback:D2450_1+7+2"
LAYOUT = CURRENT_LAYOUT
EVALUATION_WINDOW = CURRENT_EVALUATION_WINDOW
try:
    _layout_record = resolve_layout(GEO_NAME, MESH_CASE_NAME)
    LAYOUT = _layout_record.layout
    EVALUATION_WINDOW = _layout_record.evaluation_window
    CELL_LENGTH_M = float(LAYOUT.cell_length_x_m)
    DOMAIN_X_MAX_M = float(LAYOUT.total_length_m)
    N_INLET_BUFFER_CELLS = int(LAYOUT.n_buffer_in)
    N_SPACER_CELLS = int(LAYOUT.n_active)
    N_OUTLET_BUFFER_CELLS = int(LAYOUT.n_buffer_out)
    LAYOUT_SOURCE = f"resolve_layout({GEO_NAME!r}, {MESH_CASE_NAME!r})"
except KeyError:
    CELL_LENGTH_M = _FALLBACK_CELL_LENGTH_M
    DOMAIN_X_MAX_M = _FALLBACK_DOMAIN_X_MAX_M
    N_INLET_BUFFER_CELLS = _FALLBACK_N_INLET_BUFFER_CELLS
    N_SPACER_CELLS = _FALLBACK_N_SPACER_CELLS
    N_OUTLET_BUFFER_CELLS = _FALLBACK_N_OUTLET_BUFFER_CELLS

N_TOTAL_CELLS = N_INLET_BUFFER_CELLS + N_SPACER_CELLS + N_OUTLET_BUFFER_CELLS

MEMBRANE_BASE_NAMES = ["wall_top_mem", "wall_bottom_mem"]

# Hardcoded area_mem fallbacks from campaign reports (used only if live
# surface-area measurement is unavailable).
AREA_MEM_FALLBACK_BY_GEO = {
    "D0817_a45_21c_brg110": 4.5602e-05,
    "D2450_a45_7c_brg110": 1.5766e-04,
}

# UDM field names — copied from 01_pyfluent_report_extract.py / UDF enum.
# udm-6 = Jw [m/s] (water flux); udm-8 = Jw in LMH; udm-0 = salt mass source
# (volumetric sink) — NOT used for membrane flux averages.
FIELD_UDM_JW = "udm-6"
FIELD_UDM_LMH = "udm-8"
FIELD_UDM_CP_INLET = "udm-9"
FIELD_UDM_SI = "udm-0"  # salt sink — listed only to document exclusion
FIELD_SALT_MASS_FRACTION = "nacl"
FIELD_PRESSURE = "pressure"

# LMH conversion used by 01_pyfluent_report_extract.py for mass-balance LMH:
#   lmh_definition = f"abs(pp_m_in + pp_m_out) / ({rho} * pp_area_mem) * 3.6e6"
# UDF uses the same factor: MS_TO_LMH = 3600000.0 (= 3.6e6).
MS_TO_LMH = 3.6e6

SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
SURFACE_MASS_WEIGHTED_AVG = "surface-massavg"
SURFACE_AREA = "surface-area"

PRODUCT_VERSION = "25.1.0"
PROCESSOR_COUNT = 8
GRAPHICS_DRIVER = "dx11"
FLUENT_START_TIMEOUT = 300
FLUENT_HEALTH_TIMEOUT = 300


def as_fluent_path(path):
    """Convert a path to a Fluent-friendly absolute path."""
    return os.path.abspath(path).replace("\\", "/")


def zone_matches_base_name(zone_name, base_name):
    """Return True for base, base.1, base.2, ... zone naming."""
    # Copied from solver_code_260616.py / 01_pyfluent_report_extract.py.
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


def find_zones_by_base_names(zone_names, base_names):
    """Find zones matching any of the given base names (base or base.N)."""
    matched = []
    for base_name in base_names:
        matched.extend(find_zones_by_base_name(zone_names, base_name))
    return sorted(set(matched))


def collect_boundary_zones(setup):
    """Collect boundary zone names grouped by boundary type."""
    # Copied from 01_pyfluent_report_extract.py.
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
    """Create/update a surface report definition (01_pyfluent_report_extract style)."""
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
    """Compute one report definition and return its first numeric value.

    Raises if no numeric value can be extracted — failures must be loud.
    """
    result = solution.report_definitions.compute(report_defs=[report_name])
    if verbose:
        print(f"\nRaw compute result for {report_name}:")
        pprint(result)
    value = find_first_number(result)
    if value is None:
        raise RuntimeError(
            f"Could not extract numeric value from report {report_name!r}. "
            f"Raw result: {result!r}"
        )
    return value, result


def delete_surface_report(solution, report_name):
    """Delete a surface report definition if it exists."""
    group = solution.report_definitions.surface
    names = list_named_object_names(group, "solution.report_definitions.surface")
    if report_name in names:
        group.delete(report_name)
        print(f"Deleted surface report: {report_name}")


def delete_iso_clip(solver, clip_name):
    """Delete an iso-clip surface if it exists."""
    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
        print(f"Deleted iso-clip: {clip_name}")


def delete_iso_surface(solver, surface_name):
    """Delete an iso-surface if it exists."""
    iso_group = solver.settings.results.surfaces.iso_surface
    existing = list_named_object_names(iso_group, "results.surfaces.iso_surface")
    if surface_name in existing:
        iso_group.delete(surface_name)
        print(f"Deleted iso-surface: {surface_name}")


def create_x_range_iso_clip(solver, clip_name, surface_names, x_min_m, x_max_m):
    """Create an x-coordinate iso-clip of wall-zone surfaces (verified API).

    Live-verified attribute path (Fluent 25.1 / ansys-fluent-core 0.38.0):
      clip.field = "x-coordinate"
      clip.surfaces = [...]
      clip.range.minimum / clip.range.maximum
    Do NOT assign clip.range as a dict (CAR: wrong type [not a pair]).
    Do NOT use deprecated clip.minimum / clip.maximum.
    """
    if not surface_names:
        raise ValueError(f"Cannot create iso-clip {clip_name!r}: no surfaces.")

    iso_group = solver.settings.results.surfaces.iso_clip
    existing = list_named_object_names(iso_group, "results.surfaces.iso_clip")
    if clip_name in existing:
        iso_group.delete(clip_name)
        print(f"Deleted existing iso-clip: {clip_name}")

    try:
        iso_group.create(clip_name)
        clip = iso_group[clip_name]
        clip.field = "x-coordinate"
        clip.surfaces = list(surface_names)
        clip.range.minimum = float(x_min_m)
        clip.range.maximum = float(x_max_m)
    except Exception as exc:
        print(
            f"iso_clip FAILED for {clip_name!r} on surfaces={surface_names!r} "
            f"x=[{x_min_m}, {x_max_m}]: {type(exc).__name__}: {exc}"
        )
        raise

    print(
        f"Created iso-clip '{clip_name}' on {surface_names} "
        f"x in [{float(x_min_m):.6e}, {float(x_max_m):.6e}] m"
    )
    return clip_name


def iso_clip_segment_metrics(solver, solution, zone_names, x_min_m, x_max_m, tag):
    """Area-weighted membrane metrics on one x-clipped wall surface set.

    Creates one iso-clip, evaluates surface-area + areaavg(udm-6/8/9), then
    deletes the temporary reports and clip. Never falls back to sum_if.
    """
    if not zone_names:
        return {
            "area_m2": 0.0,
            "jw_m_s": None,
            "lmh_udm8": None,
            "cp_inlet": None,
            "lmh_from_jw": None,
            "active": False,
        }

    clip_name = f"tmp_clip_{tag}"
    report_area = f"tmp_clip_area_{tag}"
    report_jw = f"tmp_clip_jw_{tag}"
    report_lmh = f"tmp_clip_lmh_{tag}"
    report_cp = f"tmp_clip_cp_{tag}"
    created_reports = []

    create_x_range_iso_clip(solver, clip_name, zone_names, x_min_m, x_max_m)
    try:
        create_or_update_surface_report(
            solution, report_area, SURFACE_AREA, None, [clip_name]
        )
        created_reports.append(report_area)
        area_m2, _ = compute_one_report(solution, report_area)
        area_m2 = float(area_m2)

        # Empty clips (buffer x-ranges) have area 0; areaavg on them can fail.
        if area_m2 <= 0.0:
            return {
                "area_m2": area_m2,
                "jw_m_s": None,
                "lmh_udm8": None,
                "cp_inlet": None,
                "lmh_from_jw": None,
                "active": False,
            }

        create_or_update_surface_report(
            solution,
            report_jw,
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_UDM_JW,
            [clip_name],
        )
        created_reports.append(report_jw)
        create_or_update_surface_report(
            solution,
            report_lmh,
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_UDM_LMH,
            [clip_name],
        )
        created_reports.append(report_lmh)
        create_or_update_surface_report(
            solution,
            report_cp,
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_UDM_CP_INLET,
            [clip_name],
        )
        created_reports.append(report_cp)

        jw_m_s, _ = compute_one_report(solution, report_jw)
        lmh_udm8, _ = compute_one_report(solution, report_lmh)
        cp_inlet, _ = compute_one_report(solution, report_cp)
    finally:
        for report_name in created_reports:
            try:
                delete_surface_report(solution, report_name)
            except Exception as cleanup_exc:
                print(
                    f"WARN: could not delete report {report_name!r}: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
        try:
            delete_iso_clip(solver, clip_name)
        except Exception as cleanup_exc:
            print(
                f"WARN: could not delete iso-clip {clip_name!r}: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )

    return {
        "area_m2": area_m2,
        "jw_m_s": float(jw_m_s),
        "lmh_udm8": float(lmh_udm8),
        "cp_inlet": float(cp_inlet),
        "lmh_from_jw": float(jw_m_s) * MS_TO_LMH,
        "active": True,
    }


def resolve_total_membrane_area(solution, membrane_zones):
    """Return (area_m2, source_label) for the whole-membrane area check."""
    group = solution.report_definitions.surface
    existing = list_named_object_names(group, "solution.report_definitions.surface")

    for report_name in ("area_mem", "pp_area_mem"):
        if report_name in existing:
            value, _ = compute_one_report(solution, report_name)
            return float(value), f"existing report {report_name!r}"

    if membrane_zones:
        report_name = "tmp_area_mem_ref"
        create_or_update_surface_report(
            solution, report_name, SURFACE_AREA, None, membrane_zones
        )
        try:
            value, _ = compute_one_report(solution, report_name)
        finally:
            delete_surface_report(solution, report_name)
        return float(value), f"live surface-area on {membrane_zones}"

    if GEO_NAME in AREA_MEM_FALLBACK_BY_GEO:
        return (
            float(AREA_MEM_FALLBACK_BY_GEO[GEO_NAME]),
            f"hardcoded fallback AREA_MEM_FALLBACK_BY_GEO[{GEO_NAME!r}]",
        )
    raise RuntimeError(
        "Cannot resolve total membrane area: no area_mem / pp_area_mem report, "
        f"no membrane zones, and no hardcoded fallback for geo={GEO_NAME!r}."
    )


def area_self_check(spacer_rows, reference_area_m2, reference_source):
    """Sum spacer combined clip areas vs total membrane area; loud on mismatch."""
    areas = []
    for row in spacer_rows:
        area = row.get("comb_area_m2")
        if area is None:
            raise RuntimeError(
                f"Area self-check missing comb_area_m2 for span {row.get('span')!r}."
            )
        areas.append(float(area))
    area_sum = sum(areas)
    abs_diff = area_sum - float(reference_area_m2)
    rel_diff = (
        abs_diff / float(reference_area_m2)
        if float(reference_area_m2) != 0.0
        else float("inf")
    )
    abs_rel = abs(rel_diff)

    if abs_rel < 1e-3:
        interpretation = "clipping clean"
    elif area_sum > float(reference_area_m2):
        interpretation = "boundary facets double counted"
    else:
        interpretation = "boundary facets dropped"

    print("\nAREA SELF-CHECK (spacer combined clip areas vs total membrane):")
    print(f"  sum(comb_area_m2) = {area_sum:.8e} m2")
    print(f"  reference         = {float(reference_area_m2):.8e} m2")
    print(f"  reference source  = {reference_source}")
    print(f"  abs diff          = {abs_diff:.8e} m2")
    print(f"  rel diff          = {rel_diff:.8e}")
    print(f"  interpretation    = {interpretation}")

    if abs_rel >= 1e-2:
        raise RuntimeError(
            f"Area self-check FAILED: |rel|={abs_rel:.3e} >= 1e-2 "
            f"({interpretation}). sum={area_sum:.8e}, "
            f"ref={float(reference_area_m2):.8e}."
        )
    if abs_rel >= 1e-3:
        print(
            f"WARNING: area self-check |rel|={abs_rel:.3e} in [1e-3, 1e-2) "
            f"— {interpretation}"
        )
    return {
        "sum_m2": area_sum,
        "reference_m2": float(reference_area_m2),
        "reference_source": reference_source,
        "abs_diff_m2": abs_diff,
        "rel_diff": rel_diff,
        "interpretation": interpretation,
    }


def cell_spans_m():
    """Return ordered (label, x_min_m, x_max_m) for buffer + spacer spans."""
    spans = []
    x0 = DOMAIN_X_MIN_M
    for i in range(N_INLET_BUFFER_CELLS):
        x1 = x0 + CELL_LENGTH_M
        spans.append((f"buffer_in_{i + 1}", x0, x1))
        x0 = x1
    for i in range(N_SPACER_CELLS):
        x1 = x0 + CELL_LENGTH_M
        spans.append((f"spacer_{i + 1}", x0, x1))
        x0 = x1
    for i in range(N_OUTLET_BUFFER_CELLS):
        x1 = x0 + CELL_LENGTH_M
        spans.append((f"buffer_out_{i + 1}", x0, x1))
        x0 = x1
    return spans


def plane_x_positions_m():
    """Planes at every cell boundary from domain inlet to outlet."""
    return [
        DOMAIN_X_MIN_M + i * CELL_LENGTH_M for i in range(N_TOTAL_CELLS + 1)
    ]


def print_table(title, rows, columns):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    if not rows:
        print("(no rows)")
        return
    widths = {
        col: max(len(col), max(len(f"{row.get(col, '')}") for row in rows))
        for col in columns
    }
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            "  ".join(
                f"{row.get(col, '')}".ljust(widths[col]) for col in columns
            )
        )


def main():
    eval_local = EVALUATION_WINDOW.evaluation_local_indices(LAYOUT)
    eval_global = EVALUATION_WINDOW.evaluation_cell_numbers(LAYOUT)
    print("Resolved paths:")
    print(f"  CELL_PROFILE_GEO  -> GEO_NAME  = {GEO_NAME}")
    print(f"  CELL_PROFILE_CASE -> CASE_NAME = {CASE_NAME}")
    print(
        f"  CELL_PROFILE_MESH -> MESH_CASE_NAME = {MESH_CASE_NAME} "
        f"(source={MESH_CASE_NAME_SOURCE})"
    )
    print(f"  layout source     = {LAYOUT_SOURCE}")
    print(
        f"  layout            = n_buffer_in={LAYOUT.n_buffer_in}, "
        f"n_active={LAYOUT.n_active}, n_buffer_out={LAYOUT.n_buffer_out}, "
        f"cell_length_x_m={LAYOUT.cell_length_x_m}, "
        f"total_length_m={LAYOUT.total_length_m}"
    )
    print(
        f"  evaluation_window = lead={EVALUATION_WINDOW.n_lead_excluded}, "
        f"trail={EVALUATION_WINDOW.n_trail_excluded} "
        f"-> local spacer cells {eval_local}, "
        f"global unit cells {eval_global}"
    )
    print(
        f"  cells             = {N_INLET_BUFFER_CELLS}+{N_SPACER_CELLS}+"
        f"{N_OUTLET_BUFFER_CELLS} "
        f"(dx={CELL_LENGTH_M} m, Lx={DOMAIN_X_MAX_M} m)"
    )
    print(f"  CASE_PATH         = {CASE_PATH}")
    print(f"  FINAL_CASE_FILE   = {FINAL_CASE_FILE}")
    print(f"  FINAL_DATA_FILE   = {FINAL_DATA_FILE}")
    print(f"  CSV_PATH          = {CSV_PATH}")

    print("\nFIELD CHOICE:")
    print(
        f"  Water flux UDM = {FIELD_UDM_JW!r} (UDF UDM_JW = adjacent-face "
        "area-weighted water flux [m/s])"
    )
    print(
        f"  LMH UDM        = {FIELD_UDM_LMH!r} (UDF UDM_LMH = Jw * MS_TO_LMH; "
        f"MS_TO_LMH = {MS_TO_LMH} matches "
        "01_pyfluent_report_extract.py '* 3.6e6')"
    )
    print(
        f"  CP inlet UDM   = {FIELD_UDM_CP_INLET!r} "
        "(UDF UDM_CP_INLET = Cm / C_INLET_REF)"
    )
    print(
        f"  Salt sink UDM  = {FIELD_UDM_SI!r} (UDF UDM_SI) — NOT used for "
        "membrane flux averages"
    )
    print(
        "  Membrane clip method: iso_clip on wall zones by x-coordinate, "
        "then surface-area / surface-areaavg reports (NO sum_if)"
    )
    print(
        "  Plane nacl method: surface-massavg (primary); surface-areaavg "
        "printed alongside for bias comparison"
    )

    if not FINAL_CASE_FILE.is_file():
        raise FileNotFoundError(f"Final case file not found: {FINAL_CASE_FILE}")
    if not FINAL_DATA_FILE.is_file():
        raise FileNotFoundError(f"Final data file not found: {FINAL_DATA_FILE}")

    pyfluent.config.check_health_timeout = FLUENT_HEALTH_TIMEOUT

    meshing = None
    solver = None
    original_cwd = os.getcwd()
    csv_rows = []

    membrane_rows = []
    plane_rows = []
    convergence_rows = []

    try:
        os.chdir(CASE_PATH)

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

        # ---- Zone detection ----
        boundary_zones_by_type = collect_boundary_zones(setup)
        boundary_zone_names = sorted(
            zone_name
            for names in boundary_zones_by_type.values()
            for zone_name in names
        )
        print("Boundary zones by type:")
        for zone_type, names in boundary_zones_by_type.items():
            print(f"  {zone_type}: {names}")

        top_zones = find_zones_by_base_name(boundary_zone_names, "wall_top_mem")
        bottom_zones = find_zones_by_base_name(
            boundary_zone_names, "wall_bottom_mem"
        )
        print("wall_top_mem zones:", top_zones)
        print("wall_bottom_mem zones:", bottom_zones)
        if not top_zones and not bottom_zones:
            raise ValueError("No membrane wall zones found.")
        combined_zones = list(top_zones) + list(bottom_zones)

        # ---- Stage 1: per-cell membrane flux via iso_clip + surface reports ----
        spans = cell_spans_m()
        print("\nCell spans [m] (closed intervals [x_i, x_i+1]):")
        for label, x0, x1 in spans:
            print(f"  {label}: {x0:.6e} .. {x1:.6e}")

        for span_index, (label, x0, x1) in enumerate(spans):
            row = {
                "span": label,
                "x_min_m": x0,
                "x_max_m": x1,
            }
            for side, zones, prefix in (
                ("top", top_zones, "top"),
                ("bottom", bottom_zones, "bottom"),
                ("combined", combined_zones, "comb"),
            ):
                tag = f"{prefix}_{span_index:02d}"
                metrics = iso_clip_segment_metrics(
                    solver, solution, zones, x0, x1, tag
                )
                row[f"{prefix}_active"] = bool(metrics["active"])
                row[f"{prefix}_area_m2"] = metrics["area_m2"]
                row[f"{prefix}_jw_m_s"] = metrics["jw_m_s"]
                row[f"{prefix}_lmh_udm8"] = metrics["lmh_udm8"]
                row[f"{prefix}_lmh_from_jw"] = metrics["lmh_from_jw"]
                row[f"{prefix}_cp_inlet"] = metrics["cp_inlet"]
            membrane_rows.append(row)
            csv_rows.append({"table": "membrane_flux", **row})

        flux_cols = [
            "span",
            "x_min_m",
            "x_max_m",
            "top_active",
            "top_area_m2",
            "top_lmh_udm8",
            "top_cp_inlet",
            "bottom_active",
            "bottom_area_m2",
            "bottom_lmh_udm8",
            "bottom_cp_inlet",
            "comb_active",
            "comb_area_m2",
            "comb_lmh_udm8",
            "comb_lmh_from_jw",
            "comb_cp_inlet",
        ]
        print_table("1. PER-CELL MEMBRANE FLUX (iso_clip)", membrane_rows, flux_cols)

        buffer_rows = [
            r for r in membrane_rows if str(r["span"]).startswith("buffer_")
        ]
        print("\nMembrane activity on buffer spans (must be ~0 area):")
        for r in buffer_rows:
            print(
                f"  {r['span']}: active={r.get('comb_active')} "
                f"area_m2={r.get('comb_area_m2')} "
                f"lmh={r.get('comb_lmh_udm8')} "
                f"cp={r.get('comb_cp_inlet')}"
            )
            for prefix in ("top", "bottom", "comb"):
                area = r.get(f"{prefix}_area_m2")
                if area is not None and float(area) > 0.0:
                    print(
                        f"  WARNING: buffer span {r['span']} {prefix} area "
                        f"{area} > 0 — membrane zones extend into buffer x"
                    )

        spacer_rows = [
            r for r in membrane_rows if str(r["span"]).startswith("spacer_")
        ]
        reference_area_m2, reference_source = resolve_total_membrane_area(
            solution, combined_zones
        )
        area_check = area_self_check(
            spacer_rows, reference_area_m2, reference_source
        )
        csv_rows.append({"table": "area_self_check", **area_check})

        # Whole-membrane LMH vs evaluation-window area-weighted LMH
        whole_lmh_report = "tmp_whole_lmh"
        create_or_update_surface_report(
            solution,
            whole_lmh_report,
            SURFACE_AREA_WEIGHTED_AVG,
            FIELD_UDM_LMH,
            combined_zones,
        )
        try:
            whole_lmh, _ = compute_one_report(solution, whole_lmh_report)
        finally:
            delete_surface_report(solution, whole_lmh_report)
        whole_lmh = float(whole_lmh)

        whole_top_lmh = None
        if top_zones:
            whole_top_report = "tmp_whole_top_lmh"
            create_or_update_surface_report(
                solution,
                whole_top_report,
                SURFACE_AREA_WEIGHTED_AVG,
                FIELD_UDM_LMH,
                top_zones,
            )
            try:
                whole_top_lmh, _ = compute_one_report(solution, whole_top_report)
            finally:
                delete_surface_report(solution, whole_top_report)
            whole_top_lmh = float(whole_top_lmh)

        eval_labels = [f"spacer_{i}" for i in eval_local]
        eval_rows = [r for r in spacer_rows if r["span"] in eval_labels]
        missing = sorted(set(eval_labels) - {r["span"] for r in eval_rows})
        if missing:
            raise RuntimeError(
                f"Evaluation-window spans missing from membrane rows: {missing}"
            )

        def _area_weighted_lmh(rows, area_key, lmh_key):
            weighted = 0.0
            area_sum = 0.0
            for row in rows:
                area = row.get(area_key)
                lmh = row.get(lmh_key)
                if area is None or lmh is None:
                    raise RuntimeError(
                        f"Missing {area_key}/{lmh_key} for span {row.get('span')!r}."
                    )
                weighted += float(lmh) * float(area)
                area_sum += float(area)
            if area_sum <= 0.0:
                raise RuntimeError(
                    f"Evaluation-window area sum is {area_sum} "
                    f"(key={area_key}); cannot form window LMH."
                )
            return weighted / area_sum, area_sum

        window_lmh, window_area = _area_weighted_lmh(
            eval_rows, "comb_area_m2", "comb_lmh_udm8"
        )
        window_vs_whole_pct = (
            100.0 * (window_lmh - whole_lmh) / whole_lmh if whole_lmh != 0.0 else None
        )
        print("\nWINDOW vs WHOLE-MEMBRANE LMH (udm-8, area-weighted):")
        print(f"  evaluation spans     = {eval_labels}")
        print(f"  window area (comb)   = {window_area:.8e} m2")
        print(f"  window LMH (comb)    = {window_lmh:.8g}")
        print(f"  whole-membrane LMH   = {whole_lmh:.8g}")
        print(f"  window - whole [%]   = {window_vs_whole_pct}")

        window_top_lmh = None
        window_top_vs_whole_pct = None
        if top_zones and whole_top_lmh is not None:
            window_top_lmh, window_top_area = _area_weighted_lmh(
                eval_rows, "top_area_m2", "top_lmh_udm8"
            )
            window_top_vs_whole_pct = (
                100.0 * (window_top_lmh - whole_top_lmh) / whole_top_lmh
                if whole_top_lmh != 0.0
                else None
            )
            print(f"  window area (top)    = {window_top_area:.8e} m2")
            print(f"  window LMH (top)     = {window_top_lmh:.8g}")
            print(f"  whole top-wall LMH   = {whole_top_lmh:.8g}")
            print(f"  top window - whole [%] = {window_top_vs_whole_pct}")

        csv_rows.append(
            {
                "table": "window_lmh",
                "eval_spans": ",".join(eval_labels),
                "window_lmh_comb": window_lmh,
                "window_area_comb_m2": window_area,
                "whole_lmh_comb": whole_lmh,
                "window_minus_whole_pct": window_vs_whole_pct,
                "window_lmh_top": window_top_lmh,
                "whole_lmh_top": whole_top_lmh,
                "top_window_minus_whole_pct": window_top_vs_whole_pct,
            }
        )

        # ---- Stage 2: per-plane pressure + bulk nacl ----
        for i, x_m in enumerate(plane_x_positions_m()):
            plane_name = f"tmp_plane_x_{i:02d}"
            row = {"plane_index": i, "x_m": x_m, "plane_name": plane_name}
            p_report = f"tmp_p_{i:02d}"
            nacl_mass_report = f"tmp_nacl_mass_{i:02d}"
            nacl_area_report = f"tmp_nacl_area_{i:02d}"
            created_reports = []

            create_x_normal_plane(solver, plane_name, x_m)
            try:
                create_or_update_surface_report(
                    solution,
                    p_report,
                    SURFACE_AREA_WEIGHTED_AVG,
                    FIELD_PRESSURE,
                    [plane_name],
                )
                created_reports.append(p_report)
                row["pressure_Pa"], _ = compute_one_report(solution, p_report)

                create_or_update_surface_report(
                    solution,
                    nacl_mass_report,
                    SURFACE_MASS_WEIGHTED_AVG,
                    FIELD_SALT_MASS_FRACTION,
                    [plane_name],
                )
                created_reports.append(nacl_mass_report)
                row["nacl_massavg"], _ = compute_one_report(
                    solution, nacl_mass_report
                )

                create_or_update_surface_report(
                    solution,
                    nacl_area_report,
                    SURFACE_AREA_WEIGHTED_AVG,
                    FIELD_SALT_MASS_FRACTION,
                    [plane_name],
                )
                created_reports.append(nacl_area_report)
                row["nacl_areaavg"], _ = compute_one_report(
                    solution, nacl_area_report
                )

                row["nacl"] = row["nacl_massavg"]
                row["nacl_mass_minus_area"] = (
                    float(row["nacl_massavg"]) - float(row["nacl_areaavg"])
                )
            finally:
                for report_name in created_reports:
                    try:
                        delete_surface_report(solution, report_name)
                    except Exception as cleanup_exc:
                        print(
                            f"WARN: could not delete report {report_name!r}: "
                            f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                        )
                try:
                    delete_iso_surface(solver, plane_name)
                except Exception as cleanup_exc:
                    print(
                        f"WARN: could not delete iso-surface {plane_name!r}: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                    )

            if plane_rows and row["pressure_Pa"] is not None:
                prev = plane_rows[-1].get("pressure_Pa")
                row["dP_from_prev_Pa"] = (
                    None
                    if prev is None
                    else float(row["pressure_Pa"]) - float(prev)
                )
            else:
                row["dP_from_prev_Pa"] = None

            plane_rows.append(row)
            csv_rows.append({"table": "plane_profile", **row})

        print_table(
            "2. PER-PLANE PRESSURE AND BULK CONCENTRATION",
            plane_rows,
            [
                "plane_index",
                "x_m",
                "pressure_Pa",
                "dP_from_prev_Pa",
                "nacl_massavg",
                "nacl_areaavg",
                "nacl_mass_minus_area",
            ],
        )

        # ---- Stage 3: convergence vs mean of spacer cells 3..5 ----
        spacer_only = [
            r for r in membrane_rows if str(r["span"]).startswith("spacer_")
        ]
        ref = [
            r
            for r in spacer_only
            if r["span"] in ("spacer_3", "spacer_4", "spacer_5")
        ]
        ref_vals = [
            r["comb_lmh_udm8"]
            for r in ref
            if r.get("comb_lmh_udm8") is not None
        ]
        ref_mean = sum(ref_vals) / len(ref_vals) if ref_vals else None
        print(f"\nReference mean LMH (spacer_3..5, comb udm-8): {ref_mean}")
        for r in spacer_only:
            lmh = r.get("comb_lmh_udm8")
            if ref_mean in (None, 0.0) or lmh is None:
                pct = None
            else:
                pct = 100.0 * (float(lmh) - float(ref_mean)) / float(ref_mean)
            crow = {
                "span": r["span"],
                "comb_lmh_udm8": lmh,
                "ref_mean_lmh_cells_3_5": ref_mean,
                "pct_dev_from_ref": pct,
            }
            convergence_rows.append(crow)
            csv_rows.append({"table": "convergence", **crow})

        print_table(
            "3. CONVERGENCE SUMMARY (% vs mean of spacer cells 3-5)",
            convergence_rows,
            [
                "span",
                "comb_lmh_udm8",
                "ref_mean_lmh_cells_3_5",
                "pct_dev_from_ref",
            ],
        )

        # ---- Write CSV ----
        fieldnames = ["table"]
        for row in csv_rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\nWrote CSV: {CSV_PATH}")

        print("\nDone. No iterate and no case/data write were performed.")
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
        sys.exit(1)
