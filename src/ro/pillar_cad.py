"""Build one Pillar or Hole-Pillar fluid body with PyAnsys Geometry.

Discovery 25.1 only. Importing this module does not launch Discovery.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import ansys.geometry.core as pyansys_geometry
from ansys.geometry.core.connection.backend import ApiVersions
from ansys.geometry.core.connection.launcher import launch_modeler_with_discovery
from ansys.geometry.core.designer.body import CollisionType
from ansys.geometry.core.designer.face import SurfaceType
from ansys.geometry.core.math.plane import Plane
from ansys.geometry.core.math.point import Point2D, Point3D
from ansys.geometry.core.math.vector import UnitVector3D
from ansys.geometry.core.shapes.surfaces.cylinder import Cylinder
from ansys.geometry.core.shapes.surfaces.plane import PlaneSurface
from ansys.geometry.core.sketch.sketch import Sketch

from ro.paths import project_root

# Channel height is not stored in configs/batch_config.py.
CHANNEL_HEIGHT_M = 0.770e-3
# Pillar ends pass the membranes so no end face is coplanar with a membrane.
PILLAR_END_MARGIN_M = 0.1e-3
# periodic_shift_y and the CLI diameters are millimetres. Lengths below are metres.
MM_TO_M = 1.0e-3

POSITION_TOL_M = 1.0e-9
DIRECTION_TOL = 1.0e-9
RADIUS_TOL_M = 1.0e-9
AREA_REL_TOL = 1.0e-6

_MANUAL_CAD_ROOT = "c:/ro_data/geometries"
_PLUS_X = (1.0, 0.0, 0.0)
_PLUS_Y = (0.0, 1.0, 0.0)
_PLUS_Z = (0.0, 0.0, 1.0)
_MINUS_X = (-1.0, 0.0, 0.0)
_MINUS_Y = (0.0, -1.0, 0.0)
_MINUS_Z = (0.0, 0.0, -1.0)
_INV_SQRT2 = 1.0 / math.sqrt(2.0)
_FILAMENT_DIRECTIONS = (
    (_INV_SQRT2, _INV_SQRT2, 0.0),
    (_INV_SQRT2, -_INV_SQRT2, 0.0),
)

_BASE_LABELS = (
    "inlet",
    "outlet",
    "wall_spacer_buffer",
    "periodic_r",
    "periodic_l",
    "wall_top_buffer_in",
    "wall_top_buffer_out",
    "wall_top_mem",
    "wall_bottom_buffer_in",
    "wall_bottom_buffer_out",
    "wall_bottom_mem",
    "wall_spacer_pillar",
    "wall_spacer_filament",
)
_HOLE_LABEL = "wall_spacer_hole"


def generate_pillar_cad(*, d_p_mm, d_h_mm, geo_id, out_dir):
    """Write ``<geo_id>.pmdb``, ``.scdocx``, and ``_meta.json`` under ``out_dir``."""
    d_p_mm = _require_real("d_p_mm", d_p_mm)
    d_h_mm = _require_real("d_h_mm", d_h_mm)
    geo_id = _require_geo_id(geo_id)
    out_dir = _require_out_dir(out_dir)
    if d_p_mm <= 0.0:
        raise ValueError(f"d_p_mm must be positive, got {d_p_mm}.")
    if d_h_mm < 0.0:
        raise ValueError(f"d_h_mm must be >= 0, got {d_h_mm}.")
    if d_h_mm >= d_p_mm:
        raise ValueError(
            f"d_h_mm must be smaller than d_p_mm, got d_h_mm={d_h_mm}, d_p_mm={d_p_mm}."
        )

    layout = _layout_from_config(geo_id)
    d_p_m = d_p_mm * MM_TO_M
    d_h_m = d_h_mm * MM_TO_M
    paths = _output_paths(out_dir, geo_id)
    _refuse_existing(paths)
    out_dir.mkdir(parents=True, exist_ok=True)

    modeler = None
    try:
        modeler = launch_modeler_with_discovery(
            version=251,
            api_version=ApiVersions.V_251,
            hidden=True,
        )
        design = modeler.create_design(geo_id)
        if design.name != geo_id:
            raise RuntimeError(
                f"Design name {design.name!r} does not match geo_id {geo_id!r}."
            )
        body, nodes, line_count = _build_fluid(design, layout, d_p_m, d_h_m)
        counts = _classify_and_name(design, body, layout, nodes, d_p_m, d_h_m)
        design.export_to_pmdb(out_dir)
        design.export_to_scdocx(out_dir)
        if not paths["pmdb"].is_file() or not paths["scdocx"].is_file():
            raise RuntimeError(
                "Export did not write the expected files: "
                f"{paths['pmdb']} and {paths['scdocx']}."
            )
        meta = _meta_payload(
            geo_id=geo_id,
            d_p_mm=d_p_mm,
            d_h_mm=d_h_mm,
            layout=layout,
            d_p_m=d_p_m,
            d_h_m=d_h_m,
            nodes=nodes,
            line_count=line_count,
            counts=counts,
            backend_version=str(modeler.client.backend_version),
        )
        paths["meta"].write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        close_error = None
        if modeler is not None:
            try:
                modeler.close()
            except Exception as exc:
                close_error = exc
        if close_error is not None and sys.exc_info()[0] is None:
            raise close_error
        if close_error is not None:
            body_error = sys.exc_info()[1]
            raise RuntimeError(
                "Discovery close failed after "
                f"{type(body_error).__name__}: {body_error}"
            ) from close_error

    print("Face counts:")
    for label in (*_BASE_LABELS, _HOLE_LABEL):
        print(f"  {label}: {counts[label]}")
    print(f"  total_faces: {sum(counts.values())}")
    return {"paths": {key: str(path) for key, path in paths.items()}, "face_counts": counts}


def _layout_from_config(geo_id):
    batch = _load_batch_config()
    matches = [
        case
        for case in batch.mesh_batch_cases
        if case.get("family") == "pillar" and case.get("geo_id") == geo_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"geo_id {geo_id!r} is not exactly one pillar case in "
            "configs/batch_config.py."
        )
    case = matches[0]
    a_m = _require_real("cell_length_x_m", case["cell_length_x_m"])
    n_active = _require_count("n_active_cells", case["n_active_cells"])
    n_buffer_in = _require_count("n_buffer_in", case["n_buffer_in"])
    n_buffer_out = _require_count("n_buffer_out", case["n_buffer_out"])
    filament_d_m = _require_real("filament_d_m", case["filament_d_m"])
    periodic_shift_y = _require_real("periodic_shift_y", case["periodic_shift_y"])
    if a_m <= 0.0 or filament_d_m <= 0.0:
        raise ValueError(
            f"cell_length_x_m and filament_d_m must be positive, got "
            f"{a_m} m and {filament_d_m} m."
        )
    if abs(periodic_shift_y * MM_TO_M - a_m) > POSITION_TOL_M:
        raise ValueError(
            "periodic_shift_y (mm) and cell_length_x_m (m) disagree: "
            f"{periodic_shift_y} mm vs {a_m} m."
        )
    if filament_d_m >= CHANNEL_HEIGHT_M:
        raise ValueError(
            f"filament_d_m {filament_d_m} m is not below the channel height "
            f"{CHANNEL_HEIGHT_M} m."
        )
    x_active_0 = n_buffer_in * a_m
    x_active_1 = (n_buffer_in + n_active) * a_m
    x_outlet = (n_buffer_in + n_active + n_buffer_out) * a_m
    return {
        "a_m": a_m,
        "h_m": CHANNEL_HEIGHT_M,
        "n_active": n_active,
        "n_buffer_in": n_buffer_in,
        "n_buffer_out": n_buffer_out,
        "filament_d_m": filament_d_m,
        "periodic_shift_y_mm": periodic_shift_y,
        "x_active_0": x_active_0,
        "x_active_1": x_active_1,
        "x_outlet": x_outlet,
        "y_min": -0.5 * a_m,
        "y_max": 0.5 * a_m,
        "z_min": -0.5 * CHANNEL_HEIGHT_M,
        "z_max": 0.5 * CHANNEL_HEIGHT_M,
    }


def _build_fluid(design, layout, d_p_m, d_h_m):
    nodes = _nodes(layout)
    lines = _filament_lines(nodes, layout)
    if not lines:
        raise RuntimeError("The node list produced no filament lines.")
    for index, line in enumerate(lines):
        _extrude_cylinder(
            design,
            "spacer" if index == 0 else f"filament_{index}",
            line["origin"],
            line["direction"],
            0.5 * layout["filament_d_m"],
            line["length"],
        )
    other_names = [f"filament_{index}" for index in range(1, len(lines))]
    other_names.extend(_pillar_bodies(design, nodes, d_p_m, layout))
    _unite_touching(design, "spacer", other_names)
    bore_names = _bore_bodies(design, nodes, d_h_m, layout)
    for name in bore_names:
        _body_named(design, "spacer").subtract(_body_named(design, name))

    _extrude_box(design, "buffer_in", 0.0, layout["x_active_0"], layout)
    _extrude_box(design, "active", layout["x_active_0"], layout["x_active_1"], layout)
    _extrude_box(design, "buffer_out", layout["x_active_1"], layout["x_outlet"], layout)
    _body_named(design, "active").subtract(_body_named(design, "spacer"))
    _body_named(design, "buffer_in").unite(_body_named(design, "active"))
    _body_named(design, "buffer_in").unite(_body_named(design, "buffer_out"))
    fluid = _body_named(design, "buffer_in")
    fluid.name = "solid"
    fluid = _body_named(design, "solid")
    alive = [body for body in design.bodies if body.is_alive]
    if len(alive) != 1 or alive[0].id != fluid.id:
        raise RuntimeError(
            "Boolean construction left "
            f"{[(body.name, body.id) for body in alive]!r}, expected one body named 'solid'."
        )
    _imprint_membranes(fluid, layout)
    return fluid, nodes, len(lines)


def _nodes(layout):
    """Corner nodes ``(a*i, ±a/2)`` and centre nodes ``(a*i + a/2, 0)``, z = 0."""
    a_m = layout["a_m"]
    y_min = layout["y_min"]
    y_max = layout["y_max"]
    nodes = []
    n_corners = layout["n_buffer_in"] + layout["n_active"]
    for index in range(1, n_corners + 1):
        x_m = a_m * index
        nodes.append((x_m, y_min, 0.0))
        nodes.append((x_m, y_max, 0.0))
    for index in range(1, layout["n_active"] + 1):
        nodes.append((a_m * index + 0.5 * a_m, 0.0, 0.0))
    return nodes


def _filament_lines(nodes, layout):
    """One cylinder per distinct filament line, extended by one pitch past the active box."""
    lines = []
    extension = layout["a_m"]
    for direction in _FILAMENT_DIRECTIONS:
        nx = -direction[1]
        ny = direction[0]
        groups = []
        for node in nodes:
            intercept = nx * node[0] + ny * node[1]
            matched = None
            for group in groups:
                if abs(intercept - group["intercept"]) <= POSITION_TOL_M:
                    matched = group
                    break
            if matched is None:
                groups.append({"intercept": intercept, "nodes": [node]})
            else:
                matched["nodes"].append(node)
        for group in groups:
            origin = group["nodes"][0]
            t0, t1 = _box_parameters(
                origin,
                direction,
                layout["x_active_0"],
                layout["x_active_1"],
                layout["y_min"],
                layout["y_max"],
            )
            start_t = t0 - extension
            end_t = t1 + extension
            length = end_t - start_t
            if length <= 0.0:
                raise RuntimeError(
                    f"Filament length is not positive for direction {direction}."
                )
            lines.append(
                {
                    "origin": (
                        origin[0] + start_t * direction[0],
                        origin[1] + start_t * direction[1],
                        0.0,
                    ),
                    "direction": direction,
                    "length": length,
                }
            )
    return lines


def _box_parameters(origin, direction, x0, x1, y0, y1):
    """Parameter interval of the line inside the active box."""
    tx = _axis_bounds(origin[0], direction[0], x0, x1)
    ty = _axis_bounds(origin[1], direction[1], y0, y1)
    if tx is None or ty is None:
        raise RuntimeError(f"Filament line misses the active box: origin={origin}.")
    t0 = max(tx[0], ty[0])
    t1 = min(tx[1], ty[1])
    if t1 < t0 - POSITION_TOL_M:
        raise RuntimeError(f"Filament line misses the active box: origin={origin}.")
    if t1 < t0:
        t1 = t0
    return t0, t1


def _axis_bounds(coord, component, low, high):
    if abs(component) <= POSITION_TOL_M:
        if coord < low - POSITION_TOL_M or coord > high + POSITION_TOL_M:
            return None
        return (-math.inf, math.inf)
    t_low = (low - coord) / component
    t_high = (high - coord) / component
    return (min(t_low, t_high), max(t_low, t_high))


def _pillar_bodies(design, nodes, d_p_m, layout):
    z0 = layout["z_min"] - PILLAR_END_MARGIN_M
    length = layout["h_m"] + 2.0 * PILLAR_END_MARGIN_M
    names = []
    for index, node in enumerate(nodes):
        name = f"pillar_{index}"
        _extrude_cylinder(
            design,
            name,
            (node[0], node[1], z0),
            _PLUS_Z,
            0.5 * d_p_m,
            length,
        )
        names.append(name)
    return names


def _bore_bodies(design, nodes, d_h_m, layout):
    if d_h_m == 0.0:
        return []
    allowed = (layout["y_min"], 0.0, layout["y_max"])
    ys = []
    for node in nodes:
        if not any(abs(node[1] - y_m) <= POSITION_TOL_M for y_m in allowed):
            raise RuntimeError(
                f"Node y {node[1]} m is outside {-0.5}*a, 0, and {0.5}*a."
            )
        if not any(abs(node[1] - y_m) <= POSITION_TOL_M for y_m in ys):
            ys.append(node[1])
    x0 = layout["x_active_0"] - layout["a_m"]
    x1 = layout["x_active_1"] + layout["a_m"]
    names = []
    for index, y_m in enumerate(ys):
        name = f"bore_{index}"
        _extrude_cylinder(
            design,
            name,
            (x0, y_m, 0.0),
            _PLUS_X,
            0.5 * d_h_m,
            x1 - x0,
        )
        names.append(name)
    return names


def _extrude_box(design, name, x0, x1, layout):
    if x1 <= x0:
        raise RuntimeError(f"Box {name} has non-positive length: {x0} to {x1}.")
    plane = Plane(Point3D([0.0, 0.0, layout["z_min"]]), UnitVector3D(_PLUS_X), UnitVector3D(_PLUS_Y))
    sketch = Sketch(plane)
    sketch.box(
        Point2D([0.5 * (x0 + x1), 0.0]),
        x1 - x0,
        layout["y_max"] - layout["y_min"],
    )
    body = design.extrude_sketch(name, sketch, layout["h_m"])
    if body is None:
        raise RuntimeError(f"extrude_sketch returned None for {name}.")
    return body


def _extrude_cylinder(design, name, origin, direction, radius, length):
    if radius <= 0.0 or length <= 0.0:
        raise RuntimeError(
            f"{name} radius and length must be positive, got {radius} m and {length} m."
        )
    plane = _plane_normal_to(origin, direction)
    sketch = Sketch(plane)
    sketch.circle(Point2D([0.0, 0.0]), radius)
    body = design.extrude_sketch(name, sketch, length)
    if body is None:
        raise RuntimeError(f"extrude_sketch returned None for {name}.")
    return body


def _plane_normal_to(origin, direction):
    if _direction_close(direction, _PLUS_Z) or _direction_close(direction, _MINUS_Z):
        dir_x, dir_y = _PLUS_X, _PLUS_Y
    elif _direction_close(direction, _PLUS_X) or _direction_close(direction, _MINUS_X):
        dir_x, dir_y = _PLUS_Y, _PLUS_Z
    else:
        dir_x = (-direction[1], direction[0], 0.0)
        dir_y = _PLUS_Z
    return Plane(Point3D(list(origin)), UnitVector3D(dir_x), UnitVector3D(dir_y))


def _imprint_membranes(body, layout):
    """Split each membrane plane at the active-buffer stations. Ignore the return."""
    y0 = layout["y_min"] - PILLAR_END_MARGIN_M
    y1 = layout["y_max"] + PILLAR_END_MARGIN_M
    stations = (layout["x_active_0"], layout["x_active_1"])
    for z_m, normal in ((layout["z_max"], _PLUS_Z), (layout["z_min"], _MINUS_Z)):
        faces = _faces_on_membrane(body, z_m, normal)
        if len(faces) != 1:
            raise RuntimeError(
                f"Expected one membrane face at z={z_m} m before imprint, found {len(faces)}."
            )
        plane = Plane(
            Point3D([0.0, 0.0, z_m]),
            UnitVector3D(_PLUS_X),
            UnitVector3D(_PLUS_Y),
        )
        sketch = Sketch(plane)
        for x_m in stations:
            sketch.segment(Point2D([x_m, y0]), Point2D([x_m, y1]))
        body.imprint_curves(faces=faces, sketch=sketch)
        split = _faces_on_membrane(body, z_m, normal)
        if len(split) != 3:
            raise RuntimeError(
                f"Expected 3 membrane faces at z={z_m} m after imprint, found {len(split)}."
            )


def _faces_on_membrane(body, z_m, normal):
    found = []
    for face in body.faces:
        if face.surface_type is not SurfaceType.SURFACETYPE_PLANE:
            continue
        geometry = _plane_geometry(face)
        if not _direction_close(_vector(face.normal()), normal):
            continue
        if abs(_point(geometry.origin)[2] - z_m) <= POSITION_TOL_M:
            found.append(face)
    return found


def _classify_and_name(design, body, layout, nodes, d_p_m, d_h_m):
    groups = {label: [] for label in _required_labels(d_h_m)}
    membrane_top = []
    membrane_bottom = []
    seen = set()
    for face in body.faces:
        if face.id in seen:
            raise RuntimeError(f"Face id {face.id!r} was listed twice.")
        seen.add(face.id)
        kind = face.surface_type
        if kind is SurfaceType.SURFACETYPE_PLANE:
            label = _plane_label(face, layout)
            if label == "membrane_top":
                membrane_top.append(face)
            elif label == "membrane_bottom":
                membrane_bottom.append(face)
            else:
                groups[label].append(face)
        elif kind is SurfaceType.SURFACETYPE_CYLINDER:
            _require_axis_near_node(face, nodes)
            groups[_cylinder_label(face, d_p_m, d_h_m, layout)].append(face)
        else:
            raise RuntimeError(
                f"Face {face.id!r} has unsupported surface type {kind!r}."
            )
    _assign_membrane_areas(groups, membrane_top, "top", layout)
    _assign_membrane_areas(groups, membrane_bottom, "bottom", layout)
    for label, faces in groups.items():
        if label == _HOLE_LABEL:
            continue
        if not faces:
            raise RuntimeError(f"Label {label} has no faces.")
    hole_faces = groups.get(_HOLE_LABEL, [])
    if (d_h_m > 0.0) != (len(hole_faces) > 0):
        raise RuntimeError(
            f"{_HOLE_LABEL} has {len(hole_faces)} faces and d_h_m is {d_h_m}."
        )
    for label, faces in groups.items():
        if faces:
            design.create_named_selection(label, faces=faces)
    counts = {label: len(faces) for label, faces in groups.items()}
    if d_h_m == 0.0:
        counts[_HOLE_LABEL] = 0
    return counts


def _plane_label(face, layout):
    geometry = _plane_geometry(face)
    normal = _vector(face.normal())
    origin = _point(geometry.origin)
    if _direction_close(normal, _PLUS_X) or _direction_close(normal, _MINUS_X):
        x_m = origin[0]
        if abs(x_m - 0.0) <= POSITION_TOL_M:
            return "inlet"
        if abs(x_m - layout["x_outlet"]) <= POSITION_TOL_M:
            return "outlet"
        if (
            abs(x_m - layout["x_active_0"]) <= POSITION_TOL_M
            or abs(x_m - layout["x_active_1"]) <= POSITION_TOL_M
        ):
            return "wall_spacer_buffer"
        raise RuntimeError(f"Plane normal ±x at x={x_m} m has no label.")
    if _axis_is_y(normal) and abs(origin[1] - layout["y_min"]) <= POSITION_TOL_M:
        return "periodic_r"
    if _axis_is_y(normal) and abs(origin[1] - layout["y_max"]) <= POSITION_TOL_M:
        return "periodic_l"
    if _direction_close(normal, _PLUS_Z) and abs(origin[2] - layout["z_max"]) <= POSITION_TOL_M:
        return "membrane_top"
    if _direction_close(normal, _MINUS_Z) and abs(origin[2] - layout["z_min"]) <= POSITION_TOL_M:
        return "membrane_bottom"
    raise RuntimeError(
        f"Plane face {face.id!r} normal={normal} origin={origin} has no label."
    )


def _assign_membrane_areas(groups, faces, side, layout):
    if len(faces) != 3:
        raise RuntimeError(f"Membrane {side} has {len(faces)} faces, expected 3.")
    area_in = layout["n_buffer_in"] * layout["a_m"] ** 2
    area_out = layout["n_buffer_out"] * layout["a_m"] ** 2
    area_cap = layout["n_active"] * layout["a_m"] ** 2
    buckets = {"in": [], "out": [], "mem": []}
    for face in faces:
        area = _area_m2(face.area)
        if _relative_close(area, area_in):
            buckets["in"].append(face)
        elif _relative_close(area, area_out):
            buckets["out"].append(face)
        else:
            if not (0.0 < area < area_cap):
                raise RuntimeError(
                    f"Membrane {side} face area {area} m^2 is outside (0, {area_cap})."
                )
            buckets["mem"].append(face)
    if any(len(bucket) != 1 for bucket in buckets.values()):
        raise RuntimeError(
            f"Membrane {side} areas did not split into buffer_in, buffer_out, and mem: "
            f"in={len(buckets['in'])}, out={len(buckets['out'])}, mem={len(buckets['mem'])}."
        )
    groups[f"wall_{side}_buffer_in"].extend(buckets["in"])
    groups[f"wall_{side}_buffer_out"].extend(buckets["out"])
    groups[f"wall_{side}_mem"].extend(buckets["mem"])


def _cylinder_label(face, d_p_m, d_h_m, layout):
    geometry = _cylinder_geometry(face)
    direction = _vector(geometry.dir_z)
    radius = _length_m(geometry.radius)
    if _axis_is_z(direction) and abs(radius - 0.5 * d_p_m) <= RADIUS_TOL_M:
        return "wall_spacer_pillar"
    if _axis_is_45(direction) and abs(radius - 0.5 * layout["filament_d_m"]) <= RADIUS_TOL_M:
        return "wall_spacer_filament"
    if d_h_m > 0.0 and _axis_is_x(direction) and abs(radius - 0.5 * d_h_m) <= RADIUS_TOL_M:
        return _HOLE_LABEL
    raise RuntimeError(
        f"Cylinder face {face.id!r} axis={direction} radius={radius} m has no label."
    )


def _require_axis_near_node(face, nodes):
    geometry = _cylinder_geometry(face)
    origin = _point(geometry.origin)
    direction = _vector(geometry.dir_z)
    norm = math.sqrt(sum(component * component for component in direction))
    if abs(norm - 1.0) > 1.0e-6:
        raise RuntimeError(
            f"Cylinder face {face.id!r} axis is not a unit vector: {direction}."
        )
    nearest = min(_point_line_distance(node, origin, direction) for node in nodes)
    if nearest > POSITION_TOL_M:
        raise RuntimeError(
            f"Cylinder face {face.id!r} axis misses every node by {nearest} m."
        )


def _point_line_distance(point, origin, direction):
    vx = point[0] - origin[0]
    vy = point[1] - origin[1]
    vz = point[2] - origin[2]
    cx = vy * direction[2] - vz * direction[1]
    cy = vz * direction[0] - vx * direction[2]
    cz = vx * direction[1] - vy * direction[0]
    return math.sqrt(cx * cx + cy * cy + cz * cz)


def _plane_geometry(face):
    geometry = face.shape.geometry
    if not isinstance(geometry, PlaneSurface):
        raise RuntimeError(
            f"Face {face.id!r} is PLANE but geometry is {type(geometry).__name__}."
        )
    return geometry


def _cylinder_geometry(face):
    geometry = face.shape.geometry
    if not isinstance(geometry, Cylinder):
        raise RuntimeError(
            f"Face {face.id!r} is CYLINDER but geometry is {type(geometry).__name__}."
        )
    return geometry


def _unite_touching(design, host_name, other_names):
    """Unite bodies that touch the growing solid. A disjoint remainder raises."""
    pending = list(other_names)
    while pending:
        host = _body_named(design, host_name)
        matched = None
        for name in pending:
            if host.get_collision(_body_named(design, name)) is not CollisionType.NONE:
                matched = name
                break
        if matched is None:
            raise RuntimeError(
                "Spacer union stopped. These bodies do not touch "
                f"{host_name!r}: {pending}."
            )
        _body_named(design, host_name).unite(_body_named(design, matched))
        pending.remove(matched)


def _body_named(design, name):
    matches = [body for body in design.bodies if body.is_alive and body.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one live body named {name!r}, found {len(matches)}.")
    return matches[0]


def _required_labels(d_h_m):
    labels = list(_BASE_LABELS)
    if d_h_m > 0.0:
        labels.append(_HOLE_LABEL)
    return labels


def _meta_payload(*, geo_id, d_p_mm, d_h_mm, layout, d_p_m, d_h_m, nodes, line_count, counts, backend_version):
    return {
        "geo_id": geo_id,
        "inputs": {"d_p_mm": d_p_mm, "d_h_mm": d_h_mm, "geo_id": geo_id},
        "derived": {
            "a_m": layout["a_m"],
            "channel_height_m": layout["h_m"],
            "filament_d_m": layout["filament_d_m"],
            "pillar_diameter_m": d_p_m,
            "bore_diameter_m": d_h_m,
            "pillar_end_margin_m": PILLAR_END_MARGIN_M,
            "periodic_shift_y_mm": layout["periodic_shift_y_mm"],
            "n_active_cells": layout["n_active"],
            "n_buffer_in": layout["n_buffer_in"],
            "n_buffer_out": layout["n_buffer_out"],
            "x_active_m": [layout["x_active_0"], layout["x_active_1"]],
            "x_outlet_m": layout["x_outlet"],
            "y_m": [layout["y_min"], layout["y_max"]],
            "z_m": [layout["z_min"], layout["z_max"]],
            "filament_line_count": line_count,
        },
        "node_count": len(nodes),
        "face_counts": counts,
        "ansys_geometry_core_version": pyansys_geometry.__version__,
        "backend_version": backend_version,
    }


def _output_paths(out_dir, geo_id):
    return {
        "pmdb": out_dir / f"{geo_id}.pmdb",
        "scdocx": out_dir / f"{geo_id}.scdocx",
        "meta": out_dir / f"{geo_id}_meta.json",
    }


def _refuse_existing(paths):
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite existing files: {existing}")


def _require_out_dir(out_dir):
    if not isinstance(out_dir, (str, Path)):
        raise TypeError(f"out_dir must be a path, got {type(out_dir).__name__}.")
    path = Path(out_dir)
    if _is_manual_cad_path(path):
        raise ValueError(
            f"out_dir {path} is under C:/ro_data/geometries. "
            "That tree is reserved for the manual CAD."
        )
    if path.exists() and not path.is_dir():
        raise ValueError(f"out_dir is not a directory: {path}")
    return path


def _is_manual_cad_path(path):
    texts = [path.as_posix()]
    try:
        texts.append(path.resolve().as_posix())
    except OSError as exc:
        raise ValueError(f"Could not resolve out_dir {path}: {exc}") from exc
    for text in texts:
        folded = text.replace("\\", "/").lower()
        if folded == _MANUAL_CAD_ROOT or folded.startswith(_MANUAL_CAD_ROOT + "/"):
            return True
    return False


def _require_geo_id(geo_id):
    if not isinstance(geo_id, str) or not geo_id or geo_id.strip() != geo_id:
        raise ValueError(f"geo_id must be a non-empty string, got {geo_id!r}.")
    if any(part in geo_id for part in ("/", "\\", "..")):
        raise ValueError(f"geo_id must not be a path, got {geo_id!r}.")
    return geo_id


def _load_batch_config():
    path = project_root() / "configs" / "batch_config.py"
    if not path.is_file():
        raise FileNotFoundError(f"Batch config not found: {path}")
    spec = importlib.util.spec_from_file_location("pillar_cad_batch_config", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load batch config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_real(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return number


def _require_count(name, value):
    number = _require_real(name, value)
    if number < 1.0 or int(number) != number:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return int(number)


def _length_m(value):
    if hasattr(value, "to"):
        return float(value.to("meter").magnitude)
    return float(value)


def _area_m2(value):
    if hasattr(value, "to"):
        return float(value.to("meter ** 2").magnitude)
    return float(value)


def _point(value):
    return (float(value[0]), float(value[1]), float(value[2]))


def _vector(value):
    return (float(value[0]), float(value[1]), float(value[2]))


def _direction_close(left, right):
    return all(abs(left[index] - right[index]) <= DIRECTION_TOL for index in range(3))


def _axis_is_z(direction):
    return _direction_close(direction, _PLUS_Z) or _direction_close(direction, _MINUS_Z)


def _axis_is_x(direction):
    return _direction_close(direction, _PLUS_X) or _direction_close(direction, _MINUS_X)


def _axis_is_y(direction):
    return _direction_close(direction, _PLUS_Y) or _direction_close(direction, _MINUS_Y)


def _axis_is_45(direction):
    return (
        abs(direction[2]) <= DIRECTION_TOL
        and abs(abs(direction[0]) - _INV_SQRT2) <= DIRECTION_TOL
        and abs(abs(direction[1]) - _INV_SQRT2) <= DIRECTION_TOL
    )


def _relative_close(actual, expected):
    if expected == 0.0:
        raise RuntimeError("Relative area comparison needs a non-zero expected area.")
    return abs(actual - expected) / abs(expected) <= AREA_REL_TOL
