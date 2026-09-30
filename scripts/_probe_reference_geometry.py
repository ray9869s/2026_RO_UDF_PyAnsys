"""Import-only extraction of one production reference CAD file.

Launches Fluent Meshing, imports ``P_p100_h30.dsco`` through Watertight
Import Geometry, and records objects, face labels, per-label zones, and
bounding boxes. Does not execute any meshing task after Import Geometry.

Windows Fluent server only. Do not run from WSL.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
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
_MANUAL_CAD_ROOT = "c:/ro_data/geometries"
DEFAULT_BBOX_TOL_MM = 1e-3


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


def _under_manual_cad(path):
    candidates = [str(path).replace("\\", "/"), os.path.abspath(path).replace("\\", "/")]
    for text in candidates:
        folded = text.lower()
        if folded == _MANUAL_CAD_ROOT or folded.startswith(_MANUAL_CAD_ROOT + "/"):
            return True
    return False


def _require_bbox_mm(value, source):
    """Require [[xmin, ymin, zmin], [xmax, ymax, zmax]] in millimetres."""
    if value is None:
        raise RuntimeError(f"{source} bounding box is None.")
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{source} bounding box is text: {value!r}")
    corners = list(value)
    if len(corners) != 2:
        raise RuntimeError(
            f"{source} bounding box needs 2 corners, got {len(corners)}: {value!r}"
        )
    points = []
    for index, corner in enumerate(corners):
        if isinstance(corner, (str, bytes)):
            raise TypeError(f"{source} corner {index} is text: {corner!r}")
        coords = list(corner)
        if len(coords) != 3:
            raise RuntimeError(
                f"{source} corner {index} needs 3 coordinates, got {coords!r}"
            )
        numbers = []
        for coord in coords:
            if isinstance(coord, bool) or not isinstance(coord, (int, float)):
                raise TypeError(f"{source} coordinate is not a number: {coord!r}")
            number = float(coord)
            if not math.isfinite(number):
                raise RuntimeError(f"{source} coordinate is not finite: {coord!r}")
            numbers.append(number)
        points.append(numbers)
    return points


def _require_zone_ids(value, source):
    if value is None:
        raise RuntimeError(f"{source} face zone ids are None.")
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{source} face zone ids are text: {value!r}")
    ids = list(value)
    for zone_id in ids:
        if isinstance(zone_id, bool) or not isinstance(zone_id, int):
            raise TypeError(f"{source} face zone id is not an int: {zone_id!r}")
    return ids


def _label_record(payload, label, source):
    labels = payload.get("labels")
    if not isinstance(labels, dict):
        raise TypeError(f"{source} 'labels' must be an object, got {type(labels).__name__}.")
    record = labels.get(label)
    if not isinstance(record, dict):
        raise TypeError(f"{source} label {label!r} is not an object.")
    return record


def _body_label(payload, source):
    """Label whose face zones are exactly every face zone. Exactly one is required."""
    all_ids = set(_require_zone_ids(payload.get("all_face_zone_ids"), f"{source} all zones"))
    labels = payload.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise RuntimeError(f"{source} has no labels to identify a body label.")
    matches = []
    for label in labels:
        record = _label_record(payload, label, source)
        zone_ids = set(_require_zone_ids(record.get("face_zone_ids"), f"{source} {label}"))
        if zone_ids == all_ids:
            matches.append(label)
    if len(matches) != 1:
        raise RuntimeError(
            f"{source} body label must be the one label covering every face zone, "
            f"found {matches}."
        )
    return matches[0]


def _max_abs_corner_diff_mm(current, reference):
    diffs = [
        abs(current[corner][axis] - reference[corner][axis])
        for corner in range(2)
        for axis in range(3)
    ]
    return max(diffs)


def _bbox_status(max_abs_diff_mm, tolerance_mm):
    if max_abs_diff_mm <= tolerance_mm:
        return "PASS"
    return "FAIL"


def compare_reference(current, reference, tolerance_mm):
    """Compare label sets, per-label boxes, and the overall box.

    The body label (face zones equal to all face zones) is printed and then
    excluded from the label-set comparison.
    """
    current_body = _body_label(current, "current")
    reference_body = _body_label(reference, "reference")
    print(f"Body label current: {current_body}")
    print(f"Body label reference: {reference_body}")

    current_labels = set(current["labels"]) - {current_body}
    reference_labels = set(reference["labels"]) - {reference_body}
    only_current = sorted(current_labels - reference_labels)
    only_reference = sorted(reference_labels - current_labels)
    print(f"Labels only in current CAD: {only_current}")
    print(f"Labels only in reference: {only_reference}")

    rows = []
    for label in sorted(current_labels & reference_labels):
        current_record = _label_record(current, label, "current")
        reference_record = _label_record(reference, label, "reference")
        current_ids = _require_zone_ids(
            current_record.get("face_zone_ids"), f"current {label}"
        )
        reference_ids = _require_zone_ids(
            reference_record.get("face_zone_ids"), f"reference {label}"
        )
        current_bbox = _require_bbox_mm(
            current_record.get("bounding_box_mm"), f"current {label}"
        )
        reference_bbox = _require_bbox_mm(
            reference_record.get("bounding_box_mm"), f"reference {label}"
        )
        max_abs_diff_mm = _max_abs_corner_diff_mm(current_bbox, reference_bbox)
        zone_status = "PASS" if len(current_ids) == len(reference_ids) else "FAIL"
        rows.append(
            {
                "label": label,
                "max_abs_diff_mm": max_abs_diff_mm,
                "bbox": _bbox_status(max_abs_diff_mm, tolerance_mm),
                "face_zone_count_current": len(current_ids),
                "face_zone_count_reference": len(reference_ids),
                "zone_count": zone_status,
            }
        )

    overall_diff = _max_abs_corner_diff_mm(
        _require_bbox_mm(current.get("overall_bounding_box_mm"), "current overall"),
        _require_bbox_mm(reference.get("overall_bounding_box_mm"), "reference overall"),
    )
    overall = {
        "label": "overall",
        "max_abs_diff_mm": overall_diff,
        "bbox": _bbox_status(overall_diff, tolerance_mm),
    }
    label_sets_differ = bool(only_current or only_reference)
    bbox_failed = any(row["bbox"] == "FAIL" for row in rows) or overall["bbox"] == "FAIL"
    zones_failed = any(row["zone_count"] == "FAIL" for row in rows)
    print(
        f"{'label':<32} {'max_|diff|_mm':>14} {'bbox':>6} "
        f"{'zones_current':>14} {'zones_reference':>16} {'zones':>6}"
    )
    for row in rows:
        print(
            f"{row['label']:<32} {row['max_abs_diff_mm']:14.6g} {row['bbox']:>6} "
            f"{row['face_zone_count_current']:14d} {row['face_zone_count_reference']:16d} "
            f"{row['zone_count']:>6}"
        )
    print(
        f"{overall['label']:<32} {overall['max_abs_diff_mm']:14.6g} {overall['bbox']:>6}"
    )
    return {
        "bbox_tol_mm": tolerance_mm,
        "body_label_current": current_body,
        "body_label_reference": reference_body,
        "labels_only_in_current": only_current,
        "labels_only_in_reference": only_reference,
        "labels": rows,
        "overall": overall,
        "passed": not (label_sets_differ or bbox_failed or zones_failed),
    }


def _load_comparison_reference(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Comparison reference not found: {path}")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(
            f"Comparison reference must be a JSON object, got {type(payload).__name__}."
        )
    return payload


def write_summary(payload, work_dir=WORK_DIR):
    output_path = os.path.join(work_dir, SUMMARY_NAME)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"Wrote {output_path}")
    return output_path


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Import one CAD file and record face labels and bounding boxes. "
            "With no arguments, import the pillar reference .dsco into WORK_DIR."
        ),
    )
    parser.add_argument(
        "--cad-path",
        default=None,
        help="Import this file instead of the geometry_dir() .dsco.",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Override WORK_DIR. Must not be under C:/ro_data/geometries.",
    )
    parser.add_argument(
        "--compare-to",
        default=None,
        help="Previously written reference_geometry.json to compare against.",
    )
    parser.add_argument(
        "--bbox-tol-mm",
        type=float,
        default=DEFAULT_BBOX_TOL_MM,
        help="Maximum absolute bounding-box corner difference, in mm.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if isinstance(args.bbox_tol_mm, bool) or not math.isfinite(args.bbox_tol_mm):
        raise ValueError(f"--bbox-tol-mm must be finite, got {args.bbox_tol_mm!r}.")
    if args.bbox_tol_mm < 0.0:
        raise ValueError(f"--bbox-tol-mm must be >= 0, got {args.bbox_tol_mm}.")

    if args.cad_path is None:
        geometry_file = geometry_dir(FAMILY, GEO_ID) / f"{GEO_ID}.dsco"
    else:
        geometry_file = args.cad_path
    if not os.path.isfile(geometry_file):
        raise FileNotFoundError(f"Geometry file not found: {geometry_file}")

    work_dir = WORK_DIR if args.work_dir is None else args.work_dir
    if _under_manual_cad(work_dir):
        raise ValueError(
            f"work dir {work_dir} is under C:/ro_data/geometries. "
            "That tree is reserved for the manual CAD."
        )
    reference = None
    if args.compare_to is not None:
        reference = _load_comparison_reference(args.compare_to)

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
    print(f"Work dir: {work_dir}")
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
        os.makedirs(work_dir, exist_ok=True)
        os.chdir(work_dir)
        changed_directory = True

        transcript_path = os.path.join(work_dir, TRANSCRIPT_NAME)
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
        comparison = None
        if reference is not None:
            comparison = compare_reference(
                extracted,
                reference,
                args.bbox_tol_mm,
            )
        payload = {
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
        if comparison is not None:
            payload["comparison"] = comparison
        write_summary(payload, work_dir)
        if comparison is not None and (
            comparison["labels_only_in_current"] or comparison["labels_only_in_reference"]
        ):
            raise RuntimeError(
                "Reference label sets differ. "
                f"Only in current: {comparison['labels_only_in_current']}. "
                f"Only in reference: {comparison['labels_only_in_reference']}."
            )
        if missing:
            print(
                "Probe finished after Import Geometry. "
                "Config labels are missing from the CAD."
            )
            return 1
        if comparison is not None and not comparison["passed"]:
            print(
                "Probe finished after Import Geometry. "
                "Reference comparison failed."
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
