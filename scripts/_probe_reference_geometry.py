"""Import-only extraction of one production reference CAD file.

Launches Fluent Meshing, imports ``P_p100_h30.dsco`` through Watertight
Import Geometry, and records objects, face labels, per-label zones, and
bounding boxes. Does not execute any meshing task after Import Geometry.

Windows Fluent server only. Do not run from WSL.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback

import ansys.fluent.core as pyfluent

from ro.paths import geometry_dir, project_root

FAMILY = "pillar"
GEO_ID = "P_p100_h30"
WORK_DIR = "C:/ro_data/geom_smoke/probe_ref_P_p100_h30"
SUMMARY_NAME = "reference_geometry.json"
TRANSCRIPT_NAME = "probe_reference_geometry_transcript.txt"


def _load_module(module_name, path):
    if not path.is_file():
        raise FileNotFoundError(f"Module file not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_pmdb_probe = _load_module(
    "probe_reference_geometry_pmdb_probe",
    project_root() / "scripts" / "_probe_pmdb_import.py",
)
# scripts/_probe_pmdb_import.py, which binds meshing_code_260616.as_fluent_path.
as_fluent_path = _pmdb_probe.as_fluent_path
# scripts/_probe_pmdb_import.py, which binds teardown_meshing_session.
teardown_meshing_session = _pmdb_probe.teardown_meshing_session
load_run_config = _pmdb_probe.load_run_config
_string_sequence = _pmdb_probe._string_sequence
bbox_corners_mm = _pmdb_probe.bbox_corners_mm


def load_batch_config():
    """Load configs/batch_config.py. Import does not launch Fluent."""
    return _load_module(
        "probe_reference_geometry_batch_config",
        project_root() / "configs" / "batch_config.py",
    )


def _print_raw(kind, value):
    print(f"RAW {kind} type: {type(value).__name__}")
    print(f"RAW {kind} repr: {value!r}")


def _int_sequence(value, source):
    """Require a sequence of int, including a protobuf repeated container.

    None and str/bytes are rejected. Any other value is converted with
    list(); an iteration error propagates unchanged.
    """
    if value is None:
        raise RuntimeError(f"{source} returned None.")
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"{source} must return a sequence of integers, "
            f"got {type(value).__name__}: {value!r}"
        )
    items = list(value)
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(
                f"{source} contains a non-integer element: {item!r}"
            )
    return items


def _validated_strings(raw, source):
    _print_raw(source, raw)
    return _string_sequence(raw, source)


def _validated_ints(raw, source):
    _print_raw(source, raw)
    return _int_sequence(raw, source)


def _bbox_pair_mm(raw, source):
    """Parse the verified [[min xyz], [max xyz]] millimetre box."""
    _print_raw(source, raw)
    print(f"{source} raw: {raw!r}")
    min_corner, max_corner = bbox_corners_mm(raw)
    pair = [list(min_corner), list(max_corner)]
    print(f"{source} mm: {pair}")
    return pair


def expected_config_labels(batch):
    """Labels this pillar case declares. Strings come from the config module."""
    active_membrane_wall_labels = list(
        batch._COMMON_MESH["active_membrane_wall_labels"]
    )
    buffer_wall_labels = list(batch._COMMON_MESH["buffer_wall_labels"])
    return (
        active_membrane_wall_labels
        + buffer_wall_labels
        + list(batch._pillar_spacer_labels(0.30))
    )


def _unique(items):
    seen = set()
    unique = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def extract_reference(meshing):
    """Read objects, labels, per-label zones, and bounding boxes."""
    utilities = meshing.meshing_utilities
    raw_objects = utilities.get_all_objects()
    object_names = _validated_strings(
        raw_objects, "meshing_utilities.get_all_objects()"
    )
    if not object_names:
        raise RuntimeError("meshing_utilities.get_all_objects() returned no objects.")
    print(f"Objects: {object_names}")

    objects = []
    cad_labels = []
    all_face_zone_ids = []
    for name in object_names:
        raw_labels = utilities.get_labels(object_name=name)
        labels = _validated_strings(
            raw_labels,
            f"meshing_utilities.get_labels(object_name={name!r})",
        )
        print(f"Object {name!r} labels: {labels}")
        objects.append({"name": name, "labels": labels})
        cad_labels.extend(labels)

        raw_zones = utilities.get_face_zones_of_object(object_name=name)
        zone_ids = _validated_ints(
            raw_zones,
            f"meshing_utilities.get_face_zones_of_object(object_name={name!r})",
        )
        print(f"Object {name!r} face zone ids: {zone_ids}")
        all_face_zone_ids.extend(zone_ids)

    cad_labels = _unique(cad_labels)
    all_face_zone_ids = _unique(all_face_zone_ids)
    if not all_face_zone_ids:
        raise RuntimeError(
            "Imported objects have no face zones. "
            f"Objects: {object_names}"
        )
    print(f"CAD labels: {cad_labels}")
    print(f"All face zone ids: {all_face_zone_ids}")

    # get_face_zone_id_list_with_labels returns face-zone ids that contain
    # the given labels. Every installed example also passes a zone list, so
    # the candidate set is every imported face zone.
    labels_report = {}
    for label in cad_labels:
        source = (
            "meshing_utilities.get_face_zone_id_list_with_labels"
            f"(label_name_list={[label]!r})"
        )
        raw_ids = utilities.get_face_zone_id_list_with_labels(
            face_zone_id_list=all_face_zone_ids,
            label_name_list=[label],
        )
        zone_ids = _validated_ints(raw_ids, source)
        print(f"Label {label!r} face zone ids: {zone_ids}")
        if not zone_ids:
            raise RuntimeError(
                f"Label {label!r} matched no face zones. "
                f"Candidate zone ids: {all_face_zone_ids}"
            )

        name_source = (
            "meshing_utilities.convert_zone_ids_to_name_strings"
            f"(zone_id_list={zone_ids!r})"
        )
        raw_names = utilities.convert_zone_ids_to_name_strings(
            zone_id_list=zone_ids
        )
        zone_names = _validated_strings(raw_names, name_source)
        print(f"Label {label!r} zone names: {zone_names}")
        if len(zone_names) != len(zone_ids):
            raise RuntimeError(
                f"Label {label!r} zone name count {len(zone_names)} "
                f"does not match zone id count {len(zone_ids)}."
            )

        print(f"Label {label!r} zone_id_list: {zone_ids}")
        bbox = _bbox_pair_mm(
            utilities.get_bounding_box_of_zone_list(zone_id_list=zone_ids),
            f"meshing_utilities.get_bounding_box_of_zone_list label={label!r}",
        )
        labels_report[label] = {
            "face_zone_ids": zone_ids,
            "zone_names": zone_names,
            "bounding_box_mm": bbox,
        }

    print(f"Overall zone_id_list: {all_face_zone_ids}")
    overall_bbox = _bbox_pair_mm(
        utilities.get_bounding_box_of_zone_list(zone_id_list=all_face_zone_ids),
        "meshing_utilities.get_bounding_box_of_zone_list overall",
    )
    return {
        "objects": objects,
        "cad_labels": cad_labels,
        "labels": labels_report,
        "all_face_zone_ids": all_face_zone_ids,
        "overall_bounding_box_mm": overall_bbox,
    }


def reconcile_labels(expected, cad_labels):
    expected_set = set(expected)
    cad_set = set(cad_labels)
    missing = sorted(expected_set - cad_set)
    extra = sorted(cad_set - expected_set)
    print(f"CONFIG labels missing from CAD: {missing}")
    print(f"CAD labels not in config: {extra}")
    return missing, extra


def write_summary(payload):
    output_path = os.path.join(WORK_DIR, SUMMARY_NAME)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"Wrote {output_path}")
    return output_path


def main():
    geometry_file = geometry_dir(FAMILY, GEO_ID) / f"{GEO_ID}.dsco"
    if not geometry_file.is_file():
        raise FileNotFoundError(f"Geometry file not found: {geometry_file}")

    batch = load_batch_config()
    expected = expected_config_labels(batch)
    cfg = load_run_config()
    product_version = cfg.product_version
    graphics_driver = cfg.graphics_driver
    # Same launch keywords as meshing_code_260616.py. Only processor_count changes.
    processor_count = 2

    print(f"Family: {FAMILY}")
    print(f"Geo id: {GEO_ID}")
    print(f"DSCO: {geometry_file}")
    print(f"Work dir: {WORK_DIR}")
    print(f"Config labels: {expected}")
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
            r"FileName": as_fluent_path(geometry_file),
            r"ImportCadPreferences": {
                r"MaxFacetLength": 0,
            },
            r"LengthUnit": r"mm",
        })
        workflow.TaskObject["Import Geometry"].Execute()
        print("Import Geometry executed.")

        extracted = extract_reference(meshing)
        missing, extra = reconcile_labels(expected, extracted["cad_labels"])
        write_summary(
            {
                "family": FAMILY,
                "geo_id": GEO_ID,
                "geometry_file": str(geometry_file),
                "objects": extracted["objects"],
                "labels": extracted["labels"],
                "all_face_zone_ids": extracted["all_face_zone_ids"],
                "overall_bounding_box_mm": extracted["overall_bounding_box_mm"],
                "expected_config_labels": expected,
                "config_labels_missing_from_cad": missing,
                "cad_labels_not_in_config": extra,
            }
        )
        if missing:
            print(
                "Probe finished after Import Geometry. "
                "Config labels are missing from the CAD."
            )
            return 1
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
