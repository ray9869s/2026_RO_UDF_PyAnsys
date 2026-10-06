"""Import-only extraction of a production reference CAD file.

Launches Fluent Meshing and imports through Watertight Import Geometry.
The default is one pillar file, ``P_p100_h30.dsco``. ``--family diamond``
imports the nine production Diamond ``.dsco`` files and prints labels,
per-label face counts, areas, bounding boxes, body count, and region
volume. Does not execute any meshing task after Import Geometry.

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

from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.paths import geometry_dir, project_root

FAMILY = "pillar"
GEO_ID = "P_p100_h30"
DIAMOND_FAMILY = "diamond"
DIAMOND_WORK_DIR = "C:/ro_data/geom_smoke/probe_diamond_reference"
DIAMOND_SUMMARY_NAME = "diamond_reference_geometry.json"
DEFAULT_D_H_MM = 0.30
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


def expected_config_labels(batch, d_h_mm):
    """Labels this pillar case declares. Strings come from the config module."""
    active_membrane_wall_labels = list(
        batch._COMMON_MESH["active_membrane_wall_labels"]
    )
    buffer_wall_labels = list(batch._COMMON_MESH["buffer_wall_labels"])
    return (
        active_membrane_wall_labels
        + buffer_wall_labels
        + list(batch._pillar_spacer_labels(d_h_mm))
    )


def resolve_d_h_mm(cad_path, d_h_mm):
    """No-arg runs use 0.30. ``--cad-path`` requires ``--d-h-mm``."""
    if cad_path is not None and d_h_mm is None:
        raise ValueError("--d-h-mm is required when --cad-path is given.")
    if d_h_mm is None:
        d_h_mm = DEFAULT_D_H_MM
    if isinstance(d_h_mm, bool) or not isinstance(d_h_mm, (int, float)):
        raise ValueError(f"--d-h-mm must be a number, got {d_h_mm!r}.")
    if not math.isfinite(d_h_mm) or d_h_mm < 0.0:
        raise ValueError(f"--d-h-mm must be finite and >= 0, got {d_h_mm!r}.")
    return float(d_h_mm)


def _unique(items):
    seen = set()
    unique = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def production_diamond_geo_ids():
    """The nine campaign Diamond ids, in campaign order."""
    return tuple(
        geo_id
        for geo_id in CAMPAIGN_GEO_ID_ORDER
        if family_for_geo_id(geo_id) == DIAMOND_FAMILY
    )


def production_diamond_cases():
    """Read-only paths of the nine manual Diamond ``.dsco`` files."""
    cases = []
    for geo_id in production_diamond_geo_ids():
        cases.append(
            {
                "geo_id": geo_id,
                "cad_path": (
                    f"C:/ro_data/geometries/diamond/{geo_id}/{geo_id}.dsco"
                ),
            }
        )
    if len(cases) != 9:
        raise RuntimeError(f"Expected 9 Diamond geometries, found {len(cases)}.")
    return cases


def reference_dump(extracted):
    """Labels, face counts, areas, boxes, and body volumes from one import."""
    if not isinstance(extracted, dict):
        raise TypeError(
            f"extract result must be an object, got {type(extracted).__name__}."
        )
    labels = {}
    for label, record in extracted["labels"].items():
        if not isinstance(record, dict):
            raise TypeError(f"label {label!r} is not an object.")
        zone_ids = _require_zone_ids(
            record.get("face_zone_ids"), f"label {label}"
        )
        labels[label] = {
            "face_zone_count": len(zone_ids),
            "face_count": record.get("face_count"),
            "area": record.get("face_zone_area"),
            "bounding_box_mm": _require_bbox_mm(
                record.get("bounding_box_mm"), f"label {label}"
            ),
        }
    objects = extracted.get("objects")
    if not isinstance(objects, list):
        raise TypeError("extract result 'objects' must be a list.")
    bodies = extracted.get("bodies")
    if bodies is None:
        bodies = []
    if not isinstance(bodies, list):
        raise TypeError("extract result 'bodies' must be a list.")
    return {
        "body_count": len(objects),
        "bodies": bodies,
        "labels": labels,
        "overall_bounding_box_mm": _require_bbox_mm(
            extracted.get("overall_bounding_box_mm"), "overall"
        ),
    }


def format_diamond_case_report(geo_id, dump):
    """One pasteable block for a Diamond import."""
    lines = [f"=== {geo_id} ===", f"body_count: {dump['body_count']}"]
    if not dump["bodies"]:
        lines.append("bodies: (none recorded)")
    for body in dump["bodies"]:
        name = body.get("name")
        if body.get("error"):
            lines.append(f"body {name}: VOLUME_UNREAD {body['error']}")
            continue
        regions = body.get("regions") or []
        volumes = body.get("volumes") or []
        if not regions:
            lines.append(f"body {name}: no regions")
            continue
        for region, volume in zip(regions, volumes):
            lines.append(
                f"body {name} region {region}: volume_mm3={volume}"
            )
    overall = dump["overall_bounding_box_mm"]
    lines.append(f"overall_bounding_box_mm: {overall}")
    lines.append(
        f"{'label':<28} {'zones':>6} {'faces':>8} {'area':>14}  bbox_mm"
    )
    for label in sorted(dump["labels"]):
        record = dump["labels"][label]
        face_count = record["face_count"]
        face_text = "-" if face_count is None else str(face_count)
        area = record["area"]
        area_text = "-" if area is None else f"{area:.8g}"
        lines.append(
            f"{label:<28} {record['face_zone_count']:6d} {face_text:>8} "
            f"{area_text:>14}  {record['bounding_box_mm']}"
        )
    return "\n".join(lines)


def _require_volume(raw, source):
    """Require the float returned by get_region_volume. Length unit is mm."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"{source} volume must be a float, got {raw!r}.")
    volume = float(raw)
    if not math.isfinite(volume):
        raise RuntimeError(f"{source} volume is not finite: {raw!r}.")
    return volume


def _face_zone_count(raw, source):
    """Require the int returned by get_face_zone_count."""
    _print_raw(source, raw)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"{source} must return an int, got {raw!r}.")
    if raw < 0:
        raise RuntimeError(f"{source} count is negative: {raw!r}.")
    print(f"{source} count: {raw}")
    return raw


def _object_volumes(utilities, object_names):
    """Region volumes for each imported object. A read error is recorded."""
    bodies = []
    for name in object_names:
        entry = {"name": name, "regions": None, "volumes": None, "error": None}
        try:
            raw_regions = utilities.get_regions(object_name=name)
            print(f"RAW regions {name!r}: {raw_regions!r}")
            if isinstance(raw_regions, (str, bytes)):
                raise TypeError(
                    f"get_regions({name!r}) returned text: {raw_regions!r}"
                )
            regions = [str(item) for item in list(raw_regions)]
            volumes = []
            for region in regions:
                raw_volume = utilities.get_region_volume(
                    object_name=name,
                    region_name=region,
                )
                print(f"RAW volume {name!r} {region!r}: {raw_volume!r}")
                volumes.append(
                    _require_volume(raw_volume, f"{name}/{region}")
                )
            entry["regions"] = regions
            entry["volumes"] = volumes
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"Volume read failed for {name!r}: {entry['error']}")
        bodies.append(entry)
    return bodies


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
        area = _face_zone_area(
            utilities.get_face_zone_area(face_zone_id_list=zone_ids),
            f"meshing_utilities.get_face_zone_area label={label!r}",
        )
        face_count = _face_zone_count(
            utilities.get_face_zone_count(face_zone_id_list=zone_ids),
            f"meshing_utilities.get_face_zone_count label={label!r}",
        )
        labels_report[label] = {
            "face_zone_ids": zone_ids,
            "zone_names": zone_names,
            "bounding_box_mm": bbox,
            "face_zone_area": area,
            "face_count": face_count,
        }

    print(f"Overall zone_id_list: {all_face_zone_ids}")
    overall_bbox = _bbox_pair_mm(
        utilities.get_bounding_box_of_zone_list(zone_id_list=all_face_zone_ids),
        "meshing_utilities.get_bounding_box_of_zone_list overall",
    )
    bodies = _object_volumes(utilities, object_names)
    print(f"Body count: {len(object_names)}")
    return {
        "objects": objects,
        "bodies": bodies,
        "body_count": len(object_names),
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


def _face_zone_area(raw, source):
    """Require the float returned by get_face_zone_area."""
    _print_raw(source, raw)
    if raw is None:
        raise RuntimeError(f"{source} returned None.")
    if isinstance(raw, (str, bytes, bool)) or not isinstance(raw, (int, float)):
        raise TypeError(f"{source} must return a float, got {raw!r}.")
    area = float(raw)
    if not math.isfinite(area):
        raise RuntimeError(f"{source} area is not finite: {raw!r}.")
    print(f"{source} area: {area}")
    return area


def _stored_area(record, source):
    if "face_zone_area" not in record:
        return None
    return _face_zone_area(record["face_zone_area"], source)


def _area_rel_diff(current, reference):
    if reference == 0.0:
        if current == 0.0:
            return 0.0
        return None
    return abs(current - reference) / abs(reference)


def compare_reference(current, reference, tolerance_mm, area_rtol=None):
    """Compare label sets, per-label boxes, and the overall box.

    The body label (face zones equal to all face zones) is printed and then
    excluded from the label-set comparison. When ``area_rtol`` is set, labels
    that record ``face_zone_area`` are compared too. Older reference JSON
    without that field is still accepted.
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
        row = {
            "label": label,
            "max_abs_diff_mm": max_abs_diff_mm,
            "bbox": _bbox_status(max_abs_diff_mm, tolerance_mm),
            "face_zone_count_current": len(current_ids),
            "face_zone_count_reference": len(reference_ids),
            "zone_count": zone_status,
        }
        if area_rtol is not None:
            current_area = _stored_area(current_record, f"current {label} area")
            reference_area = _stored_area(reference_record, f"reference {label} area")
            if current_area is not None or reference_area is not None:
                if current_area is None or reference_area is None:
                    rel_diff = None
                    area_status = "FAIL"
                else:
                    rel_diff = _area_rel_diff(current_area, reference_area)
                    area_status = (
                        "PASS"
                        if rel_diff is not None and rel_diff <= area_rtol
                        else "FAIL"
                    )
                row["area_current"] = current_area
                row["area_reference"] = reference_area
                row["area_rel_diff"] = rel_diff
                row["area"] = area_status
        rows.append(row)

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
    area_rows = [row for row in rows if "area" in row]
    area_failed = any(row["area"] == "FAIL" for row in area_rows)
    area_rel_diffs = [
        row["area_rel_diff"]
        for row in area_rows
        if isinstance(row.get("area_rel_diff"), (int, float))
        and not isinstance(row.get("area_rel_diff"), bool)
    ]
    area_max_rel_diff = max(area_rel_diffs) if area_rel_diffs else None
    area_header = ""
    if area_rows:
        area_header = f" {'area_rel':>12} {'area':>6}"
    print(
        f"{'label':<32} {'max_|diff|_mm':>14} {'bbox':>6} "
        f"{'zones_current':>14} {'zones_reference':>16} {'zones':>6}"
        f"{area_header}"
    )
    for row in rows:
        line = (
            f"{row['label']:<32} {row['max_abs_diff_mm']:14.6g} {row['bbox']:>6} "
            f"{row['face_zone_count_current']:14d} {row['face_zone_count_reference']:16d} "
            f"{row['zone_count']:>6}"
        )
        if "area" in row:
            rel = "" if row["area_rel_diff"] is None else f"{row['area_rel_diff']:.6g}"
            line += f" {rel:>12} {row['area']:>6}"
        print(line)
    print(
        f"{overall['label']:<32} {overall['max_abs_diff_mm']:14.6g} {overall['bbox']:>6}"
    )
    result = {
        "bbox_tol_mm": tolerance_mm,
        "body_label_current": current_body,
        "body_label_reference": reference_body,
        "labels_only_in_current": only_current,
        "labels_only_in_reference": only_reference,
        "labels": rows,
        "overall": overall,
        "passed": not (label_sets_differ or bbox_failed or zones_failed or area_failed),
    }
    if area_rtol is not None:
        result["area_rtol"] = area_rtol
        result["area_max_rel_diff"] = area_max_rel_diff
    return result


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
        "--d-h-mm",
        type=float,
        default=None,
        help="Bore diameter in mm. Required with --cad-path. No-arg default is 0.30.",
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
    parser.add_argument(
        "--area-rtol",
        type=float,
        default=1e-3,
        help="Maximum relative per-label face-zone area difference.",
    )
    parser.add_argument(
        "--family",
        choices=(FAMILY, DIAMOND_FAMILY),
        default=FAMILY,
        help=(
            "pillar imports the single reference file. "
            "diamond imports the nine production Diamond .dsco files."
        ),
    )
    return parser


def _import_geometry(meshing, geometry_file):
    """Initialize Watertight Geometry, import one file, and read it back."""
    workflow = meshing.workflow
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
    return extract_reference(meshing)


def run_diamond_reference(args):
    """Import the nine production Diamond files. Read-only on the CAD tree."""
    if args.cad_path is not None or args.d_h_mm is not None or args.compare_to is not None:
        raise ValueError(
            "--family diamond does not take --cad-path, --d-h-mm, or --compare-to."
        )
    work_dir = DIAMOND_WORK_DIR if args.work_dir is None else args.work_dir
    if _under_manual_cad(work_dir):
        raise ValueError(
            f"work dir {work_dir} is under C:/ro_data/geometries. "
            "That tree is reserved for the manual CAD."
        )
    cases = production_diamond_cases()
    missing = [case["cad_path"] for case in cases if not os.path.isfile(case["cad_path"])]
    if missing:
        raise FileNotFoundError(
            "Diamond reference CAD is missing: " + ", ".join(missing)
        )

    cfg = load_run_config()
    product_version = cfg.product_version
    graphics_driver = cfg.graphics_driver
    processor_count = 2
    print(f"Family: {DIAMOND_FAMILY}")
    print(f"Work dir: {work_dir}")
    print(
        "Length unit on import is mm. Region volume is reported as mm^3 "
        "when Fluent returns a number in that unit."
    )
    print(
        "launch_fluent args: "
        f"product_version={product_version!r}, mode='meshing', dimension=3, "
        f"precision='double', processor_count={processor_count!r}, "
        f"ui_mode='gui', graphics_driver={graphics_driver!r}"
    )
    for case in cases:
        print(f"DSCO: {case['cad_path']}")

    meshing = None
    transcript_is_running = False
    changed_directory = False
    original_working_directory = os.getcwd()
    dumps = []
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
        meshing.transcript.start(file_name=as_fluent_path(transcript_path))
        transcript_is_running = True
        print(f"Transcript: {as_fluent_path(transcript_path)}")
        for case in cases:
            print(f"Importing {case['geo_id']}")
            extracted = _import_geometry(meshing, case["cad_path"])
            dump = reference_dump(extracted)
            dumps.append(
                {
                    "family": DIAMOND_FAMILY,
                    "geo_id": case["geo_id"],
                    "geometry_file": case["cad_path"],
                    **dump,
                }
            )
            print(format_diamond_case_report(case["geo_id"], dump))
        payload = {"family": DIAMOND_FAMILY, "cases": dumps}
        output_path = os.path.join(work_dir, DIAMOND_SUMMARY_NAME)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        print(f"Wrote {output_path}")
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


def main(argv=None):
    args = build_parser().parse_args(argv)
    if isinstance(args.bbox_tol_mm, bool) or not math.isfinite(args.bbox_tol_mm):
        raise ValueError(f"--bbox-tol-mm must be finite, got {args.bbox_tol_mm!r}.")
    if args.bbox_tol_mm < 0.0:
        raise ValueError(f"--bbox-tol-mm must be >= 0, got {args.bbox_tol_mm}.")
    if isinstance(args.area_rtol, bool) or not math.isfinite(args.area_rtol):
        raise ValueError(f"--area-rtol must be finite, got {args.area_rtol!r}.")
    if args.area_rtol < 0.0:
        raise ValueError(f"--area-rtol must be >= 0, got {args.area_rtol}.")
    if args.family == DIAMOND_FAMILY:
        return run_diamond_reference(args)
    d_h_mm = resolve_d_h_mm(args.cad_path, args.d_h_mm)

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
    expected = expected_config_labels(batch, d_h_mm)
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
        meshing.transcript.start(file_name=as_fluent_path(transcript_path))
        transcript_is_running = True
        print(f"Transcript: {as_fluent_path(transcript_path)}")

        extracted = _import_geometry(meshing, geometry_file)
        missing, extra = reconcile_labels(expected, extracted["cad_labels"])
        comparison = None
        if reference is not None:
            comparison = compare_reference(
                extracted,
                reference,
                args.bbox_tol_mm,
                area_rtol=args.area_rtol,
            )
        payload = {
            "family": FAMILY,
            "geo_id": GEO_ID,
            "geometry_file": str(geometry_file),
            "objects": extracted["objects"],
            "bodies": extracted["bodies"],
            "body_count": extracted["body_count"],
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
