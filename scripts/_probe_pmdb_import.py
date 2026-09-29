# ==========================================================
# _probe_pmdb_import.py
#
# Import-only probe: launch Fluent Meshing, import one .pmdb
# through the Watertight "Import Geometry" task, and check that
# named selections arrive as face labels at the expected length
# scale.
#
# Does NOT execute any meshing task after Import Geometry.
#
# Windows Fluent server only. Do not run from WSL.
# ==========================================================

from __future__ import annotations

import importlib.util
import math
import os
import sys
import traceback
from collections.abc import Mapping

import ansys.fluent.core as pyfluent

from ro.paths import project_root

PMDB_PATH = "C:/ro_data/geom_smoke/smoke_box_ns.pmdb"
WORK_DIR = "C:/ro_data/geom_smoke/probe_pmdb"
EXPECTED_LABELS = {
    "inlet",
    "outlet",
    "side_ymin",
    "side_ymax",
    "membrane_bottom",
    "membrane_top",
}
EXPECTED_EXTENT_MM = (1.0, 1.0, 0.5)  # x, y, z
EXTENT_RTOL = 1e-6

TRANSCRIPT_NAME = "probe_pmdb_import_transcript.txt"
# Flat 6-real bounding boxes are min point then max point, each (x, y, z).
# Same (x y z) order as get_average_bounding_box_center and as Fluent's
# join-region bounding box "(min x y z) (max x y z)".
_BBOX_AXIS_KEYS = ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")


def _load_meshing_worker():
    """Load meshing_code_260616 for helpers. Import does not launch Fluent."""
    worker_path = project_root() / "scripts" / "meshing_code_260616.py"
    if not worker_path.is_file():
        raise FileNotFoundError(f"Meshing worker not found: {worker_path}")
    spec = importlib.util.spec_from_file_location(
        "probe_pmdb_meshing_worker", worker_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load meshing worker: {worker_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_meshing_worker = _load_meshing_worker()
# scripts/meshing_code_260616.py as_fluent_path. Not copied.
as_fluent_path = _meshing_worker.as_fluent_path
# scripts/meshing_code_260616.py teardown_meshing_session. Not copied.
teardown_meshing_session = _meshing_worker.teardown_meshing_session


def load_run_config():
    """Load configs/run_config.py for launch_fluent kwargs only."""
    config_path = project_root() / "configs" / "run_config.py"
    if not config_path.is_file():
        raise FileNotFoundError(f"Run config file not found: {config_path}")

    spec = importlib.util.spec_from_file_location(
        "probe_pmdb_import_run_config", config_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load run config: {config_path}")
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


def read_imported_face_labels(meshing):
    """Read face-zone labels with meshing_utilities.get_labels(object_name=...).

    PyFluent 0.38.0 datamodel_251: get_labels(object_name, filter=None,
    label_name_pattern=None) -> list[str]. Called once per object from
    get_all_objects(), with object_name only.
    """
    utilities = meshing.meshing_utilities
    object_names = utilities.get_all_objects()
    if object_names is None:
        raise RuntimeError("meshing_utilities.get_all_objects() returned None.")
    try:
        names = [str(name) for name in object_names]
    except TypeError as exc:
        raise RuntimeError(
            "meshing_utilities.get_all_objects() did not return a name list: "
            f"{object_names!r}"
        ) from exc

    labels = []
    for name in names:
        raw_labels = utilities.get_labels(object_name=name)
        print(f"RAW labels type: {type(raw_labels).__name__} object={name!r}")
        print(f"RAW labels repr: {raw_labels!r}")
        if raw_labels is None:
            raise RuntimeError(
                f"meshing_utilities.get_labels(object_name={name!r}) returned None."
            )
        if isinstance(raw_labels, (str, bytes)) or not isinstance(
            raw_labels, (list, tuple)
        ):
            raise TypeError(
                f"meshing_utilities.get_labels(object_name={name!r}) "
                "must return a list of strings, "
                f"got {type(raw_labels).__name__}: {raw_labels!r}"
            )
        for label in raw_labels:
            if not isinstance(label, str):
                raise TypeError(
                    f"meshing_utilities.get_labels(object_name={name!r}) "
                    f"contains a non-string label: {label!r}"
                )
            labels.append(label)
    return labels


def check_face_labels(meshing):
    """Print labels, require EXPECTED_LABELS, and report extras."""
    labels = read_imported_face_labels(meshing)
    found = sorted(labels)
    print(f"Face labels: {found}")
    found_set = set(labels)
    extra = sorted(found_set - EXPECTED_LABELS)
    print(f"EXTRA labels: {extra}")
    missing = sorted(EXPECTED_LABELS - found_set)
    if missing:
        raise RuntimeError(
            f"Missing face labels: {missing}. Found: {found}"
        )


def _corner_pair(values):
    """Split six axis values into min and max corners."""
    if len(values) != 6:
        raise RuntimeError(
            f"Bounding box needs 6 coordinates, got {len(values)}: {values!r}"
        )
    try:
        nums = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Bounding box coordinates are not numeric: {values!r}"
        ) from exc
    if not all(math.isfinite(value) for value in nums):
        raise RuntimeError(f"Bounding box coordinates are not finite: {nums!r}")
    return nums[:3], nums[3:]


def bbox_corners_mm(raw):
    """Parse get_bounding_box_of_zone_list into min and max corners.

    PyFluent 0.38.0 (datamodel_251) annotates this query as returning None.
    The query RPC still returns Fluent's result. Accepted shapes:
    a mapping with xmin/xmax/ymin/ymax/zmin/zmax, a pair of xyz triplets,
    or a length-6 sequence (xmin, ymin, zmin, xmax, ymax, zmax).
    """
    if raw is None:
        raise RuntimeError(
            "meshing_utilities.get_bounding_box_of_zone_list returned None; "
            "bounding box extents were not obtained."
        )

    if isinstance(raw, Mapping):
        lowered = {str(key).casefold(): value for key, value in raw.items()}
        missing = [key for key in _BBOX_AXIS_KEYS if key not in lowered]
        if missing:
            raise RuntimeError(
                "Bounding box mapping is missing "
                f"{missing}. Raw result: {raw!r}"
            )
        return _corner_pair(tuple(lowered[key] for key in _BBOX_AXIS_KEYS))

    if isinstance(raw, (str, bytes)):
        raise RuntimeError(
            "Bounding box result is text, not coordinates. "
            f"Raw result: {raw!r}"
        )

    try:
        items = list(raw)
    except TypeError as exc:
        raise RuntimeError(
            "Bounding box result is not a mapping or sequence. "
            f"Raw result: {raw!r}"
        ) from exc

    if len(items) == 2:
        try:
            min_pt = list(items[0])
            max_pt = list(items[1])
        except TypeError as exc:
            raise RuntimeError(
                "Bounding box pair items are not sequences. "
                f"Raw result: {raw!r}"
            ) from exc
        if len(min_pt) != 3 or len(max_pt) != 3:
            raise RuntimeError(
                "Bounding box pair must be two xyz points. "
                f"Raw result: {raw!r}"
            )
        return _corner_pair(tuple(min_pt) + tuple(max_pt))

    if len(items) == 6:
        return _corner_pair(tuple(items))

    raise RuntimeError(
        "Unrecognized bounding box result from "
        "meshing_utilities.get_bounding_box_of_zone_list. "
        f"Raw result: {raw!r}"
    )


def imported_face_zone_ids(meshing):
    """Face-zone ids of every imported object.

    get_bounding_box_of_zone_list needs zone ids. get_face_zones(filter=...)
    keeps zones whose names contain the filter string, so it is not a list-all.
    Objects come from get_all_objects; their faces from get_face_zones_of_object.
    """
    object_names = meshing.meshing_utilities.get_all_objects()
    if object_names is None:
        raise RuntimeError("meshing_utilities.get_all_objects() returned None.")
    try:
        names = [str(name) for name in object_names]
    except TypeError as exc:
        raise RuntimeError(
            "meshing_utilities.get_all_objects() did not return a name list: "
            f"{object_names!r}"
        ) from exc
    if not names:
        raise RuntimeError(
            "meshing_utilities.get_all_objects() returned no objects; "
            "cannot obtain a bounding box."
        )
    print(f"Imported objects: {names}")

    ids = []
    seen = set()
    for name in names:
        zone_ids = meshing.meshing_utilities.get_face_zones_of_object(
            object_name=name
        )
        if zone_ids is None:
            raise RuntimeError(
                "meshing_utilities.get_face_zones_of_object"
                f"(object_name={name!r}) returned None."
            )
        try:
            zone_ints = [int(zone_id) for zone_id in zone_ids]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "meshing_utilities.get_face_zones_of_object"
                f"(object_name={name!r}) did not return integer zone ids: "
                f"{zone_ids!r}"
            ) from exc
        for zone_id in zone_ints:
            if zone_id not in seen:
                seen.add(zone_id)
                ids.append(zone_id)
    if not ids:
        raise RuntimeError(
            "Imported objects have no face zones; cannot obtain a bounding box. "
            f"Objects: {names}"
        )
    return ids


def check_length_scale(meshing):
    """Require imported extents to match EXPECTED_EXTENT_MM."""
    zone_ids = imported_face_zone_ids(meshing)
    print(f"Face zone ids for bounding box: {zone_ids}")
    zone_names = meshing.meshing_utilities.convert_zone_ids_to_name_strings(
        zone_id_list=zone_ids
    )
    if isinstance(zone_names, (list, tuple)):
        print(f"Face zone names: {list(zone_names)}")
    else:
        print(f"Face zone names: {zone_names!r}")
    raw_bbox = meshing.meshing_utilities.get_bounding_box_of_zone_list(
        zone_id_list=zone_ids
    )
    print(f"zone_id_list: {zone_ids}")
    print(f"RAW bbox type: {type(raw_bbox).__name__}")
    print(f"RAW bbox repr: {raw_bbox!r}")
    print(f"Bounding box raw: {raw_bbox!r}")
    min_corner, max_corner = bbox_corners_mm(raw_bbox)
    extents = tuple(max_corner[i] - min_corner[i] for i in range(3))
    print(f"Bounding box min (mm): {min_corner}")
    print(f"Bounding box max (mm): {max_corner}")
    print(f"Extents (mm): {extents}")
    if any(extent <= 0.0 for extent in extents):
        raise RuntimeError(
            f"Bounding box extents must be positive, got {extents!r} mm "
            f"(min={min_corner}, max={max_corner})."
        )
    mismatches = []
    for axis, actual, expected in zip("xyz", extents, EXPECTED_EXTENT_MM):
        if not math.isclose(actual, expected, rel_tol=EXTENT_RTOL):
            mismatches.append(
                f"{axis}: actual={actual!r} expected={expected!r} "
                f"rel_tol={EXTENT_RTOL!r}"
            )
    if mismatches:
        raise RuntimeError(
            "Imported extents differ from EXPECTED_EXTENT_MM. "
            + "; ".join(mismatches)
        )


def main():
    if not os.path.isfile(PMDB_PATH):
        raise FileNotFoundError(f"Geometry file not found: {PMDB_PATH}")

    cfg = load_run_config()
    product_version = cfg.product_version
    graphics_driver = cfg.graphics_driver
    # Same launch keywords as meshing_code_260616.py. Only processor_count changes.
    processor_count = 2

    print(f"PMDB: {PMDB_PATH}")
    print(f"Work dir: {WORK_DIR}")
    print(
        "launch_fluent args: "
        f"product_version={product_version!r}, mode='meshing', dimension=3, "
        f"precision='double', processor_count={processor_count!r}, "
        f"ui_mode='gui', graphics_driver={graphics_driver!r}"
    )

    meshing = None
    transcript_is_running = False
    changed_directory = False
    original_working_directory = os.getcwd()
    try:
        os.makedirs(WORK_DIR, exist_ok=True)
        os.chdir(WORK_DIR)
        changed_directory = True

        transcript_path = os.path.join(WORK_DIR, TRANSCRIPT_NAME)
        meshing = pyfluent.launch_fluent(
            product_version=product_version,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=processor_count,
            ui_mode="gui",
            graphics_driver=graphics_driver,
        )
        workflow = meshing.workflow

        meshing.transcript.start(file_name=as_fluent_path(transcript_path))
        transcript_is_running = True
        print(f"Transcript: {as_fluent_path(transcript_path)}")

        workflow.InitializeWorkflow(WorkflowType=r"Watertight Geometry")

        workflow.TaskObject["Import Geometry"].Arguments.set_state({
            r"FileName": as_fluent_path(PMDB_PATH),
            r"ImportCadPreferences": {
                r"MaxFacetLength": 0,
            },
            r"LengthUnit": r"mm",
        })
        workflow.TaskObject["Import Geometry"].Execute()
        print("Import Geometry executed.")

        check_face_labels(meshing)
        check_length_scale(meshing)
        print(
            "Probe finished after Import Geometry. "
            "No meshing task was executed."
        )
        return 0
    finally:
        cleanup_error = None
        if meshing is not None and transcript_is_running:
            try:
                meshing.transcript.stop()
                transcript_is_running = False
            except Exception as stop_error:
                cleanup_error = stop_error

        exit_timeout_s = float(getattr(cfg, "fluent_exit_timeout_s", 60.0))
        teardown_meshing_session(meshing, exit_timeout_s=exit_timeout_s)

        if changed_directory:
            try:
                os.chdir(original_working_directory)
            except OSError as chdir_error:
                if cleanup_error is None:
                    cleanup_error = chdir_error
                else:
                    print(
                        "Warning: could not restore working directory: "
                        f"{chdir_error}"
                    )

        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error
        if cleanup_error is not None:
            print(
                "Warning: cleanup error while probe was already failing: "
                f"{cleanup_error}"
            )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"PROBE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
