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
    wall_zone_reduction_locations,
)

# ---- Case under test (env-overridable throwaway paths) ----
# Override with CELL_PROFILE_GEO / CELL_PROFILE_CASE for a second case without
# editing this file. Defaults keep the original throwaway target.
PROJECT_ROOT = SCRIPT_DIR.parent
GEO_NAME = os.environ.get("CELL_PROFILE_GEO", "D2450_a45_7c_brg110")
CASE_NAME = os.environ.get(
    "CELL_PROFILE_CASE",
    "u0p2_p6M__mesh_max085_min006_cpg5_bl4",
)
CASE_PATH = PROJECT_ROOT / "03_Results" / GEO_NAME / CASE_NAME
FINAL_CASE_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.cas.h5"
FINAL_DATA_FILE = CASE_PATH / f"{GEO_NAME}_{CASE_NAME}_final.dat.h5"
# Per-case CSV under the geo folder so concurrent runs do not overwrite.
CSV_PATH = CASE_PATH.parent / f"cell_profile_{CASE_NAME}.csv"

# Geometry [m] — cell length 3.465 mm, asymmetric buffers.
CELL_LENGTH_M = 0.003465
DOMAIN_X_MIN_M = 0.0
DOMAIN_X_MAX_M = 0.03465
N_INLET_BUFFER_CELLS = 1
N_SPACER_CELLS = 7
N_OUTLET_BUFFER_CELLS = 2
N_TOTAL_CELLS = N_INLET_BUFFER_CELLS + N_SPACER_CELLS + N_OUTLET_BUFFER_CELLS

MEMBRANE_BASE_NAMES = ["wall_top_mem", "wall_bottom_mem"]

# UDM field names — copied from 01_pyfluent_report_extract.py / UDF enum.
# udm-6 = Jw [m/s] (water flux); udm-8 = Jw in LMH; udm-0 = salt mass source
# (volumetric sink) — NOT used for membrane flux averages.
FIELD_UDM_JW = "udm-6"
FIELD_UDM_LMH = "udm-8"
FIELD_UDM_SI = "udm-0"  # salt sink — listed only to document exclusion
FIELD_SALT_MASS_FRACTION = "nacl"
FIELD_PRESSURE = "pressure"

# LMH conversion used by 01_pyfluent_report_extract.py for mass-balance LMH:
#   lmh_definition = f"abs(pp_m_in + pp_m_out) / ({rho} * pp_area_mem) * 3.6e6"
# UDF uses the same factor: MS_TO_LMH = 3600000.0 (= 3.6e6).
MS_TO_LMH = 3.6e6

SURFACE_AREA_WEIGHTED_AVG = "surface-areaavg"
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
    """Compute one report definition and return its first numeric value."""
    result = solution.report_definitions.compute(report_defs=[report_name])
    if verbose:
        print(f"\nRaw compute result for {report_name}:")
        pprint(result)
    value = find_first_number(result)
    if value is None:
        print(f"Warning: could not extract numeric value from report {report_name}.")
    return value, result


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
    """11 planes at cell boundaries: 0, 3.465, ..., 34.65 mm."""
    return [
        DOMAIN_X_MIN_M + i * CELL_LENGTH_M for i in range(N_TOTAL_CELLS + 1)
    ]


def area_weighted_segment_metrics(reduction, wall_locations, x_min_m, x_max_m, field):
    """Area-weighted field average on membrane walls in [x_min, x_max].

    NOTE: There is NO iso_clip API anywhere in this repo. Per-cell membrane
    metrics in 01_pyfluent_report_extract.py / _fluent_report_helpers.py use
    solver.fields.reduction.sum_if with an x-range condition (see
    segmented_membrane_cp_metrics). This function copies that call shape.
    """
    condition = (
        f"AND(x >= {float(x_min_m)!r} [m], "
        f"x <= {float(x_max_m)!r} [m])"
    )

    def area_sum(expression):
        return reduction.sum_if(
            expression=expression,
            condition=condition,
            locations=list(wall_locations),
            weight="Area",
        )

    area_m2 = area_sum("1")
    if area_m2 is None or area_m2 <= 0.0:
        return {
            "area_m2": float(area_m2) if area_m2 is not None else 0.0,
            "avg": None,
            "active": False,
        }
    total = area_sum(field)
    avg = None if total is None else float(total) / float(area_m2)
    return {"area_m2": float(area_m2), "avg": avg, "active": True}


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
    print("Resolved paths:")
    print(f"  CELL_PROFILE_GEO  -> GEO_NAME  = {GEO_NAME}")
    print(f"  CELL_PROFILE_CASE -> CASE_NAME = {CASE_NAME}")
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
        f"  Salt sink UDM  = {FIELD_UDM_SI!r} (UDF UDM_SI) — NOT used for "
        "membrane flux averages"
    )
    print(
        "  Membrane clip method: reduction.sum_if x-range (repo has no "
        "iso_clip API; same shape as segmented_membrane_cp_metrics)"
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
        try:
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
        except Exception as exc:
            print(f"STAGE FAILED (zone detection): {type(exc).__name__}: {exc}")
            top_zones, bottom_zones = [], []

        # ---- Stage 1: per-cell membrane flux via sum_if ----
        try:
            reduction = solver.fields.reduction
            top_locations = (
                wall_zone_reduction_locations(setup, top_zones) if top_zones else []
            )
            bottom_locations = (
                wall_zone_reduction_locations(setup, bottom_zones)
                if bottom_zones
                else []
            )
            combined_locations = list(top_locations) + list(bottom_locations)

            spans = cell_spans_m()
            print("\nCell spans [m]:")
            for label, x0, x1 in spans:
                print(f"  {label}: {x0:.6e} .. {x1:.6e}")

            for label, x0, x1 in spans:
                row = {
                    "span": label,
                    "x_min_m": x0,
                    "x_max_m": x1,
                }
                for side, locs, prefix in (
                    ("top", top_locations, "top"),
                    ("bottom", bottom_locations, "bottom"),
                    ("combined", combined_locations, "comb"),
                ):
                    try:
                        if not locs:
                            row[f"{prefix}_active"] = False
                            row[f"{prefix}_area_m2"] = 0.0
                            row[f"{prefix}_jw_m_s"] = None
                            row[f"{prefix}_lmh_udm8"] = None
                            row[f"{prefix}_lmh_from_jw"] = None
                            row[f"{prefix}_nacl"] = None
                            continue
                        jw = area_weighted_segment_metrics(
                            reduction, locs, x0, x1, FIELD_UDM_JW
                        )
                        lmh = area_weighted_segment_metrics(
                            reduction, locs, x0, x1, FIELD_UDM_LMH
                        )
                        nacl = area_weighted_segment_metrics(
                            reduction, locs, x0, x1, FIELD_SALT_MASS_FRACTION
                        )
                        row[f"{prefix}_active"] = bool(jw["active"])
                        row[f"{prefix}_area_m2"] = jw["area_m2"]
                        row[f"{prefix}_jw_m_s"] = jw["avg"]
                        row[f"{prefix}_lmh_udm8"] = lmh["avg"]
                        row[f"{prefix}_lmh_from_jw"] = (
                            None
                            if jw["avg"] is None
                            else float(jw["avg"]) * MS_TO_LMH
                        )
                        row[f"{prefix}_nacl"] = nacl["avg"]
                    except Exception as side_exc:
                        print(
                            f"  WARN {label}/{side}: "
                            f"{type(side_exc).__name__}: {side_exc}"
                        )
                        row[f"{prefix}_active"] = False
                        row[f"{prefix}_area_m2"] = None
                        row[f"{prefix}_jw_m_s"] = None
                        row[f"{prefix}_lmh_udm8"] = None
                        row[f"{prefix}_lmh_from_jw"] = None
                        row[f"{prefix}_nacl"] = None
                membrane_rows.append(row)
                csv_rows.append({"table": "membrane_flux", **row})

            flux_cols = [
                "span",
                "x_min_m",
                "x_max_m",
                "top_active",
                "top_area_m2",
                "top_lmh_udm8",
                "top_nacl",
                "bottom_active",
                "bottom_area_m2",
                "bottom_lmh_udm8",
                "bottom_nacl",
                "comb_active",
                "comb_area_m2",
                "comb_lmh_udm8",
                "comb_lmh_from_jw",
                "comb_nacl",
            ]
            print_table("1. PER-CELL MEMBRANE FLUX", membrane_rows, flux_cols)

            # Buffer-span activity note
            buffer_rows = [
                r for r in membrane_rows if str(r["span"]).startswith("buffer_")
            ]
            print("\nMembrane activity on buffer spans (comb_active / comb_area):")
            for r in buffer_rows:
                print(
                    f"  {r['span']}: active={r.get('comb_active')} "
                    f"area_m2={r.get('comb_area_m2')} "
                    f"lmh={r.get('comb_lmh_udm8')}"
                )
        except Exception as exc:
            print(
                f"STAGE FAILED (per-cell membrane flux): "
                f"{type(exc).__name__}: {exc}"
            )

        # ---- Stage 2: per-plane pressure + bulk nacl ----
        try:
            for i, x_m in enumerate(plane_x_positions_m()):
                plane_name = f"tmp_plane_x_{i:02d}"
                row = {"plane_index": i, "x_m": x_m, "plane_name": plane_name}
                try:
                    create_x_normal_plane(solver, plane_name, x_m)
                except Exception as plane_exc:
                    print(
                        f"  WARN plane {plane_name}: "
                        f"{type(plane_exc).__name__}: {plane_exc}"
                    )
                    row["pressure_Pa"] = None
                    row["nacl"] = None
                    row["dP_from_prev_Pa"] = None
                    plane_rows.append(row)
                    csv_rows.append({"table": "plane_profile", **row})
                    continue

                try:
                    p_report = f"tmp_p_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        p_report,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_PRESSURE,
                        [plane_name],
                    )
                    row["pressure_Pa"], _ = compute_one_report(solution, p_report)
                except Exception as p_exc:
                    print(
                        f"  WARN pressure {plane_name}: "
                        f"{type(p_exc).__name__}: {p_exc}"
                    )
                    row["pressure_Pa"] = None

                try:
                    y_report = f"tmp_nacl_{i:02d}"
                    create_or_update_surface_report(
                        solution,
                        y_report,
                        SURFACE_AREA_WEIGHTED_AVG,
                        FIELD_SALT_MASS_FRACTION,
                        [plane_name],
                    )
                    row["nacl"], _ = compute_one_report(solution, y_report)
                except Exception as y_exc:
                    print(
                        f"  WARN nacl {plane_name}: "
                        f"{type(y_exc).__name__}: {y_exc}"
                    )
                    row["nacl"] = None

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
                    "nacl",
                ],
            )
        except Exception as exc:
            print(
                f"STAGE FAILED (plane profile): {type(exc).__name__}: {exc}"
            )

        # ---- Stage 3: convergence vs mean of spacer cells 3..5 ----
        try:
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
            ref_mean = (
                sum(ref_vals) / len(ref_vals) if ref_vals else None
            )
            print(
                f"\nReference mean LMH (spacer_3..5, comb udm-8): {ref_mean}"
            )
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
        except Exception as exc:
            print(
                f"STAGE FAILED (convergence summary): "
                f"{type(exc).__name__}: {exc}"
            )

        # ---- Write CSV ----
        try:
            # Union of keys for a flat CSV with a table column.
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
        except Exception as exc:
            print(f"STAGE FAILED (CSV write): {type(exc).__name__}: {exc}")

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
        sys.exit(1)
