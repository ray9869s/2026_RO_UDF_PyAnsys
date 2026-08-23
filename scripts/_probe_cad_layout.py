# ==========================================================
# _probe_cad_layout.py
#
# Measure Named Selections and face bounding boxes from CAD.
# Writes nothing under meshes/ or runs/. Stops before Set Up
# Periodic Boundaries, so it does not need periodic_shift_y.
#
# After Import Geometry: dump labels and try extents.
# --calibrate always also runs a coarse surface mesh and, for
# D2450_a45, compares both stages to the known 24.255 mm
# membrane span and 3.465 mm periodic |Δy|. Without
# --calibrate, the surface mesh is skipped when import extents
# already look populated.
#
# Windows Fluent server only. Do not run from WSL.
#
#   python scripts/_probe_cad_layout.py --calibrate D2450_a45:3.465
#   python scripts/_probe_cad_layout.py D0817_a45:1.155
# ==========================================================

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile

import ansys.fluent.core as pyfluent

from ro.paths import geometry_dir, project_root

FAMILY_DEFAULT = "diamond"
MEMBRANE_LABEL = "wall_top_mem"
PERIODIC_L = "periodic_l"
PERIODIC_R = "periodic_r"
SPACER_LABEL = "wall_spacer"
SPLIT_BUFFER_LABELS = (
    "wall_top_buffer_in",
    "wall_top_buffer_out",
    "wall_bottom_buffer_in",
    "wall_bottom_buffer_out",
)
UNSPLIT_BUFFER_LABELS = ("wall_top_buffer", "wall_bottom_buffer")

# Same cheap surface sizes as _probe_periodic_after_surface.py.
PROBE_M_MAX = 0.085
PROBE_M_MIN = 0.006
PROBE_M_CPG = 3.0

CALIBRATE_GEO_ID = "D2450_a45"
CALIBRATE_MEMBRANE_DX_MM = 24.255
CALIBRATE_PERIODIC_DY_MM = 3.465
CALIBRATE_TOL_MM = 0.05

_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")

# First Scheme expression that yields six numbers wins.
# UNCERTAIN: this repo has never queried per-label extents.
_BOUND_SCHEME_TEMPLATES = (
    '(inquire-bounds (get-zone-id "{label}"))',
    '(tg-zone-box (get-zone-id "{label}"))',
    '(query-zone-extents (get-zone-id "{label}"))',
    '(get-label-bounding-box "{label}")',
    '(query-label-bounding-box "{label}")',
    '(tgapi-util-get-label-bounding-box "{label}")',
)


def as_fluent_path(path):
    """Convert an absolute file path to a Fluent-friendly path string."""
    return os.path.abspath(path).replace("\\", "/")


def load_run_config():
    """Load configs/run_config.py for launch_fluent kwargs only."""
    config_path = project_root() / "configs" / "run_config.py"
    if not config_path.is_file():
        raise FileNotFoundError(f"Run config file not found: {config_path}")
    spec = importlib.util.spec_from_file_location(
        "probe_cad_layout_run_config", config_path
    )
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


def parse_geo_pitch(token):
    """Parse one CLI token 'geo_id:pitch_mm' into (geo_id, pitch_mm)."""
    if ":" not in token:
        raise ValueError(
            f"Expected geo_id:pitch_mm, got {token!r}. "
            "Example: D2450_a45:3.465"
        )
    geo_id, pitch_text = token.rsplit(":", 1)
    geo_id = geo_id.strip()
    if not geo_id:
        raise ValueError(f"Missing geo_id in {token!r}.")
    try:
        pitch_mm = float(pitch_text)
    except ValueError as exc:
        raise ValueError(f"Pitch must be a number in {token!r}.") from exc
    if pitch_mm <= 0.0:
        raise ValueError(f"Pitch must be positive in {token!r}.")
    return geo_id, pitch_mm


def classify_labels(labels):
    """Summarise Named Selection names for mesh_batch_cases."""
    present = set(labels)
    return {
        "buffers_split": all(name in present for name in SPLIT_BUFFER_LABELS),
        "buffers_unsplit": any(name in present for name in UNSPLIT_BUFFER_LABELS),
        "has_wall_spacer": SPACER_LABEL in present,
        "has_periodic_l": PERIODIC_L in present,
        "has_periodic_r": PERIODIC_R in present,
        "has_wall_top_mem": MEMBRANE_LABEL in present,
        "split_buffer_labels_found": [
            name for name in SPLIT_BUFFER_LABELS if name in present
        ],
        "unsplit_buffer_labels_found": [
            name for name in UNSPLIT_BUFFER_LABELS if name in present
        ],
    }


def _flatten_numbers(value):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return [float(match.group(0)) for match in _NUMBER_RE.finditer(value)]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten_numbers(item))
        return out
    return []


def bbox_from_raw(raw):
    """Turn a Scheme/TUI payload into xmin/xmax/... or None."""
    nums = _flatten_numbers(raw)
    if len(nums) < 6:
        return None
    six = nums[:6]
    xmin, xmax, ymin, ymax, zmin, zmax = six
    if not (xmin <= xmax and ymin <= ymax and zmin <= zmax):
        xmin, ymin, zmin, xmax, ymax, zmax = six
        if not (xmin <= xmax and ymin <= ymax and zmin <= zmax):
            return None
    return {
        "xmin": xmin,
        "xmax": xmax,
        "ymin": ymin,
        "ymax": ymax,
        "zmin": zmin,
        "zmax": zmax,
        "cx": 0.5 * (xmin + xmax),
        "cy": 0.5 * (ymin + ymax),
        "cz": 0.5 * (zmin + zmax),
        "dx": xmax - xmin,
        "dy": ymax - ymin,
        "dz": zmax - zmin,
    }


def membrane_dx_mm(extents_by_label):
    box = (extents_by_label or {}).get(MEMBRANE_LABEL)
    if not box:
        return None
    return box["dx"]


def periodic_dy_abs_mm(extents_by_label):
    left = (extents_by_label or {}).get(PERIODIC_L)
    right = (extents_by_label or {}).get(PERIODIC_R)
    if not left or not right:
        return None
    return abs(left["cy"] - right["cy"])


def n_active_from_dx(dx_mm, pitch_mm):
    if dx_mm is None or pitch_mm <= 0.0:
        return None
    return dx_mm / pitch_mm


def extents_populated(extents_by_label):
    """True when membrane Δx > 0 and both periodic centroids exist."""
    dx = membrane_dx_mm(extents_by_label)
    if dx is None or dx <= 0.0:
        return False
    return periodic_dy_abs_mm(extents_by_label) is not None


def _close(measured, expected, tol):
    if measured is None:
        return False
    return abs(measured - expected) <= tol


def calibration_report(import_ext, surface_ext, labels_summary):
    """Compare both stages to known D2450_a45 CAD numbers."""

    def _stage(ext):
        dx = membrane_dx_mm(ext)
        dy = periodic_dy_abs_mm(ext)
        return {
            "populated": extents_populated(ext),
            "buffers_split_ok": labels_summary["buffers_split"],
            "membrane_dx_mm": dx,
            "membrane_dx_ok": _close(dx, CALIBRATE_MEMBRANE_DX_MM, CALIBRATE_TOL_MM),
            "periodic_dy_abs_mm": dy,
            "periodic_dy_ok": _close(dy, CALIBRATE_PERIODIC_DY_MM, CALIBRATE_TOL_MM),
        }

    import_stage = _stage(import_ext)
    surface_stage = _stage(surface_ext)
    import_ok = (
        import_stage["populated"]
        and import_stage["buffers_split_ok"]
        and import_stage["membrane_dx_ok"]
        and import_stage["periodic_dy_ok"]
    )
    surface_ok = (
        surface_stage["populated"]
        and surface_stage["buffers_split_ok"]
        and surface_stage["membrane_dx_ok"]
        and surface_stage["periodic_dy_ok"]
    )
    agree = False
    if import_stage["populated"] and surface_stage["populated"]:
        agree = _close(
            import_stage["membrane_dx_mm"],
            surface_stage["membrane_dx_mm"],
            CALIBRATE_TOL_MM,
        ) and _close(
            import_stage["periodic_dy_abs_mm"],
            surface_stage["periodic_dy_abs_mm"],
            CALIBRATE_TOL_MM,
        )
    return {
        "geo_id": CALIBRATE_GEO_ID,
        "expected": {
            "buffers_split": True,
            "wall_top_mem_dx_mm": CALIBRATE_MEMBRANE_DX_MM,
            "periodic_dy_abs_mm": CALIBRATE_PERIODIC_DY_MM,
            "tolerance_mm": CALIBRATE_TOL_MM,
        },
        "after_import": import_stage,
        "after_surface_mesh": surface_stage,
        "import_matches_known": import_ok,
        "surface_mesh_matches_known": surface_ok,
        "import_agrees_with_surface_mesh": agree,
        "import_only_sufficient": import_ok,
    }


def get_available_labels(task, complete_label_key):
    """Read available face labels from the current workflow task."""
    try:
        state = task.Arguments.get_state()
    except Exception as exc:
        sys.stderr.write(
            f"Warning: could not read labels from {complete_label_key}: {exc}\n"
        )
        return []
    labels = state.get(complete_label_key, [])
    return [label for label in labels if isinstance(label, str)]


def _scheme_eval(meshing, expr):
    scheme = getattr(meshing, "scheme_eval", None)
    if scheme is None:
        raise RuntimeError("meshing session has no scheme_eval")
    errors = []
    for method_name in ("scheme_eval", "string_eval", "eval"):
        fn = getattr(scheme, method_name, None)
        if not callable(fn):
            continue
        try:
            return fn(expr)
        except Exception as exc:
            errors.append(f"{method_name}: {exc}")
    raise RuntimeError("; ".join(errors) or "no scheme_eval method worked")


def read_label_bounds(meshing, label):
    """Return (bbox_or_None, method, attempts) for one Named Selection."""
    attempts = []
    for template in _BOUND_SCHEME_TEMPLATES:
        expr = template.format(label=label)
        try:
            raw = _scheme_eval(meshing, expr)
            box = bbox_from_raw(raw)
            attempts.append({
                "method": expr,
                "ok": box is not None,
                "raw_type": type(raw).__name__,
            })
            if box is not None:
                return box, expr, attempts
        except Exception as exc:
            attempts.append({
                "method": expr,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return None, None, attempts


def read_extents(meshing, labels):
    present = set(labels)
    wanted = [MEMBRANE_LABEL, PERIODIC_L, PERIODIC_R]
    wanted.extend(name for name in labels if name not in wanted)
    extents = {}
    methods = {}
    attempts_by_label = {}
    for label in wanted:
        if label not in present and label not in (MEMBRANE_LABEL, PERIODIC_L, PERIODIC_R):
            continue
        box, method, attempts = read_label_bounds(meshing, label)
        attempts_by_label[label] = attempts
        if box is not None:
            extents[label] = box
            methods[label] = method
    return {
        "populated": extents_populated(extents),
        "method_by_label": methods,
        "labels": extents,
        "wall_top_mem_dx_mm": membrane_dx_mm(extents),
        "periodic_dy_abs_mm": periodic_dy_abs_mm(extents),
        "attempts_by_label": {
            label: attempts_by_label[label]
            for label in (MEMBRANE_LABEL, PERIODIC_L, PERIODIC_R)
            if label in attempts_by_label
        },
    }


def import_geometry(workflow, geo_path):
    workflow.InitializeWorkflow(WorkflowType=r"Watertight Geometry")
    workflow.TaskObject["Import Geometry"].Arguments.set_state({
        r"FileName": as_fluent_path(str(geo_path)),
        r"ImportCadPreferences": {
            r"MaxFacetLength": 0,
        },
        r"LengthUnit": r"mm",
    })
    workflow.TaskObject["Import Geometry"].Execute()


def generate_probe_surface_mesh(workflow):
    workflow.TaskObject["Generate the Surface Mesh"].Arguments.set_state({
        r"CFDSurfaceMeshControls": {
            r"CellsPerGap": PROBE_M_CPG,
            r"MaxSize": PROBE_M_MAX,
            r"MinSize": PROBE_M_MIN,
            r"ScopeProximityTo": r"faces",
        },
    })
    workflow.TaskObject["Generate the Surface Mesh"].Execute()


def probe_one_geometry(meshing, family, geo_id, pitch_mm, *, calibrate):
    geo_path = geometry_dir(family, geo_id) / f"{geo_id}.dsco"
    record = {
        "family": family,
        "geo_id": geo_id,
        "pitch_mm": pitch_mm,
        "cad": str(geo_path),
        "cad_exists": geo_path.is_file(),
        "named_selections": [],
        "label_flags": None,
        "after_import": None,
        "after_surface_mesh": None,
        "surface_mesh_ran": False,
        "n_active_cells_from_dx": None,
        "error": None,
    }
    if not geo_path.is_file():
        record["error"] = f"Geometry file not found: {geo_path}"
        return record

    workflow = meshing.workflow
    import_geometry(workflow, geo_path)
    labels = get_available_labels(
        workflow.TaskObject["Add Local Sizing"],
        r"CompleteFaceLabelList",
    )
    record["named_selections"] = list(labels)
    record["label_flags"] = classify_labels(labels)

    import_ext = read_extents(meshing, labels)
    import_ext["n_active_cells_from_dx"] = n_active_from_dx(
        import_ext["wall_top_mem_dx_mm"], pitch_mm
    )
    record["after_import"] = import_ext

    run_surface = calibrate or not import_ext["populated"]
    surface_ext = None
    if run_surface:
        generate_probe_surface_mesh(workflow)
        record["surface_mesh_ran"] = True
        surface_ext = read_extents(meshing, labels)
        surface_ext["n_active_cells_from_dx"] = n_active_from_dx(
            surface_ext["wall_top_mem_dx_mm"], pitch_mm
        )
        record["after_surface_mesh"] = surface_ext

    chosen = surface_ext if surface_ext and surface_ext["populated"] else import_ext
    record["n_active_cells_from_dx"] = chosen.get("n_active_cells_from_dx")
    record["wall_top_mem_dx_mm"] = chosen.get("wall_top_mem_dx_mm")
    record["periodic_dy_abs_mm"] = chosen.get("periodic_dy_abs_mm")
    record["periodic_shift_y"] = chosen.get("periodic_dy_abs_mm")

    if calibrate and geo_id == CALIBRATE_GEO_ID:
        record["calibration"] = calibration_report(
            import_ext.get("labels"),
            (surface_ext or {}).get("labels"),
            record["label_flags"],
        )
    return record


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Probe CAD Named Selections and face extents without writing a mesh."
        )
    )
    parser.add_argument(
        "geos",
        nargs="+",
        help="One or more geo_id:pitch_mm tokens (pitch in millimetres).",
    )
    parser.add_argument("--family", default=FAMILY_DEFAULT)
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help=(
            "Always run import + coarse surface mesh and, for D2450_a45, "
            "compare both stages to the known 24.255 / 3.465 mm values."
        ),
    )
    parser.add_argument(
        "--processors",
        type=int,
        default=None,
        help="Override run_config processor_count (default: value in run_config).",
    )
    return parser.parse_args(argv)


def launch_meshing(cfg, processor_count):
    return pyfluent.launch_fluent(
        product_version=cfg.product_version,
        mode="meshing",
        dimension=3,
        precision="double",
        processor_count=processor_count,
        ui_mode="gui",
        graphics_driver=cfg.graphics_driver,
    )


def _exit_meshing(meshing):
    if meshing is None:
        return
    try:
        meshing.exit()
    except Exception as exc:
        sys.stderr.write(f"Warning: meshing.exit() failed: {exc}\n")


def main(argv=None):
    args = parse_args(argv)
    specs = [parse_geo_pitch(token) for token in args.geos]
    family = args.family

    for geo_id, _pitch in specs:
        geometry_dir(family, geo_id)

    cfg = load_run_config()
    processor_count = (
        args.processors if args.processors is not None else cfg.processor_count
    )
    sys.stderr.write(
        "launch_fluent args: "
        f"product_version={cfg.product_version!r}, mode='meshing', "
        f"processor_count={processor_count!r}, ui_mode='gui'\n"
    )
    sys.stderr.write(
        "Probe surface mesh sizes: "
        f"m_max={PROBE_M_MAX}, m_min={PROBE_M_MIN}, m_cpg={PROBE_M_CPG}\n"
    )
    sys.stderr.write(
        "This probe does not write under meshes/ or runs/; "
        "cwd is a throwaway temp directory.\n"
    )

    original_cwd = os.getcwd()
    results = []
    meshing = None
    with tempfile.TemporaryDirectory(prefix="probe_cad_layout_") as tmp:
        os.chdir(tmp)
        try:
            meshing = launch_meshing(cfg, processor_count)
            for index, (geo_id, pitch_mm) in enumerate(specs):
                sys.stderr.write(f"Probing {family}/{geo_id} (pitch {pitch_mm} mm)\n")
                try:
                    record = probe_one_geometry(
                        meshing, family, geo_id, pitch_mm, calibrate=args.calibrate
                    )
                except Exception as exc:
                    sys.stderr.write(
                        f"Session failed on {geo_id}: {type(exc).__name__}: {exc}\n"
                    )
                    if index == 0:
                        raise
                    sys.stderr.write(
                        "Relaunching Fluent and retrying this geometry.\n"
                    )
                    _exit_meshing(meshing)
                    meshing = launch_meshing(cfg, processor_count)
                    record = probe_one_geometry(
                        meshing, family, geo_id, pitch_mm, calibrate=args.calibrate
                    )
                    record["session_relaunched"] = True
                results.append(record)
        finally:
            _exit_meshing(meshing)
            os.chdir(original_cwd)

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"PROBE FAILED: {type(exc).__name__}: {exc}\n")
        sys.exit(1)
