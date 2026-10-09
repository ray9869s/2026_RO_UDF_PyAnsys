"""Build one Diamond fluid body with PyAnsys Geometry.

Discovery 25.1 only. Importing this module does not launch Discovery.

The nine production layouts come from the campaign registry. The probe of
the manual ``.dsco`` files fixes the domain and the in-plane lattice:
buffers are 3.465 mm in and 6.93 mm out, the spacer stays inside the
active window, and each membrane is blocked by one family of diagonals of
the rectangular cells. Filament axes sit at ``z = ±filament_radius`` so the
stack reaches ``Sigma_d`` and the membrane trim matches the registry.
A joint sphere of ``bridge_radius_m`` sits at each diagonal crossing and
at each of the four active-window corners, at ``z = 0``.

Viewed from above (looking along ``-z``), with ``+x`` to the right and
``+y`` up, the manual CAD puts the upper layer (``z > 0``) on the diagonal
from upper-left to lower-right, so ``dy/dx < 0`` along ``+x``. The lower
layer is the other diagonal, ``dy/dx > 0``. Face areas do not show which
family is on top; the parity check reads cylinder axes.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

from ro.campaign_geo_ids import family_for_geo_id
from ro.campaign_geometry import (
    CAMPAIGN_H_M,
    geometry_parameters_for_geo_id,
    membrane_contact_width_m,
)
from ro.domain_layout import BUFFER_LENGTH_IN_M, BUFFER_LENGTH_OUT_M
from ro.solver_common import sha256_file

MM_TO_M = 1.0e-3
POSITION_TOL_M = 1.0e-9
DIRECTION_TOL = 1.0e-9
ANGLE_IDENTITY_RTOL = 1.0e-9
# Station imprint is extended past the periodic faces so the cut crosses them.
IMPRINT_MARGIN_M = 0.1e-3

_PRODUCTION_ROOTS = ("c:/ro_data", "/mnt/c/ro_data")
_PLUS_X = (1.0, 0.0, 0.0)
_PLUS_Y = (0.0, 1.0, 0.0)
_PLUS_Z = (0.0, 0.0, 1.0)
_MINUS_X = (-1.0, 0.0, 0.0)
_MINUS_Y = (0.0, -1.0, 0.0)
_MINUS_Z = (0.0, 0.0, -1.0)

BOUNDARY_LABELS = (
    "inlet",
    "outlet",
    "periodic_l",
    "periodic_r",
    "wall_top_buffer_in",
    "wall_top_mem",
    "wall_top_buffer_out",
    "wall_bottom_buffer_in",
    "wall_bottom_mem",
    "wall_bottom_buffer_out",
    "wall_spacer",
)


def diamond_layout(geo_id, n_active=None):
    """Domain, filament axes, and sphere radius for one Diamond design.

    ``n_active`` replaces the registry cell count. The pitch, span, angle,
    and buffer lengths stay on the production design, so a larger count
    lengthens only the active section.
    """
    geo_id = _require_geo_id(geo_id)
    if family_for_geo_id(geo_id) != "diamond":
        raise ValueError(f"geo_id {geo_id!r} is not a Diamond design.")
    entry = geometry_parameters_for_geo_id(geo_id)
    pitch_m = _require_real("cell_length_x_m", entry["cell_length_x_m"])
    periodic_dy_m = _require_real("periodic_shift_y_m", entry["periodic_shift_y_m"])
    attack_deg = _require_real("attack_angle_deg", entry["attack_angle_deg"])
    filament_d_m = _require_real("filament_d_m", entry["filament_d_m"])
    sphere_r_m = _require_real("bridge_radius_m", entry["bridge_radius_m"])
    trim_m = _require_real("membrane_trim_m", entry["membrane_trim_m"])
    registry_n = _require_count("n_active_cells", entry["n_active_cells"])
    if n_active is None:
        n_active = registry_n
    else:
        n_active = _require_count("n_active", n_active)
    if pitch_m <= 0.0 or periodic_dy_m <= 0.0 or filament_d_m <= 0.0 or sphere_r_m <= 0.0:
        raise ValueError(
            f"{geo_id} pitch, span, filament diameter, and sphere radius must be positive."
        )
    if trim_m <= 0.0:
        raise ValueError(f"{geo_id} membrane_trim_m must be positive, got {trim_m}.")
    _require_angle_identity(pitch_m, periodic_dy_m, attack_deg, geo_id)
    filament_r_m = 0.5 * filament_d_m
    half_h_m = 0.5 * CAMPAIGN_H_M
    # Axes at ±radius: the outer surface is at ±filament_d, and the membrane
    # at ±h/2 cuts it by membrane_trim_m.
    penetration_m = filament_d_m - half_h_m
    if abs(penetration_m - trim_m) > POSITION_TOL_M:
        raise ValueError(
            f"{geo_id} filament stack does not meet the membrane by membrane_trim_m: "
            f"penetration {penetration_m} m, trim {trim_m} m."
        )
    x_active_0 = BUFFER_LENGTH_IN_M
    x_active_1 = x_active_0 + n_active * pitch_m
    return {
        "geo_id": geo_id,
        "pitch_m": pitch_m,
        "periodic_dy_m": periodic_dy_m,
        "attack_angle_deg": attack_deg,
        "filament_d_m": filament_d_m,
        "filament_radius_m": filament_r_m,
        "sphere_radius_m": sphere_r_m,
        "membrane_trim_m": trim_m,
        "n_active": n_active,
        "n_active_registry": registry_n,
        "h_m": CAMPAIGN_H_M,
        "buffer_in_m": BUFFER_LENGTH_IN_M,
        "buffer_out_m": BUFFER_LENGTH_OUT_M,
        "x_active_0": x_active_0,
        "x_active_1": x_active_1,
        "x_outlet": x_active_1 + BUFFER_LENGTH_OUT_M,
        "y_min": -0.5 * periodic_dy_m,
        "y_max": 0.5 * periodic_dy_m,
        "z_min": -half_h_m,
        "z_max": half_h_m,
        "upper_axis_z_m": filament_r_m,
        "lower_axis_z_m": -filament_r_m,
        "filament_angle_from_x_rad": math.atan2(periodic_dy_m, pitch_m),
    }


def filament_segments(layout):
    """One cylinder per cell per layer, extended one pitch past the active box.

    Upper layer (``z > 0``): ``dir_x * dir_y < 0``, from ``+y`` toward ``-y``
    as ``x`` increases. Lower layer: ``dir_x * dir_y > 0``. That is the manual
    CAD orientation.
    """
    phi = layout["filament_angle_from_x_rad"]
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    pitch_m = layout["pitch_m"]
    diagonal_m = math.hypot(pitch_m, layout["periodic_dy_m"])
    extension_m = pitch_m
    length_m = diagonal_m + 2.0 * extension_m
    segments = []
    for index in range(layout["n_active"]):
        x_m = layout["x_active_0"] + index * pitch_m
        segments.append(
            _segment(
                layer="upper",
                start=(x_m, layout["y_max"], layout["upper_axis_z_m"]),
                direction=(cos_phi, -sin_phi, 0.0),
                extension_m=extension_m,
                length_m=length_m,
            )
        )
        segments.append(
            _segment(
                layer="lower",
                start=(x_m, layout["y_min"], layout["lower_axis_z_m"]),
                direction=(cos_phi, sin_phi, 0.0),
                extension_m=extension_m,
                length_m=length_m,
            )
        )
    return segments


def sphere_centers(layout):
    """Crossings of the upper-layer axes with the lower-layer axes.

    Each crossing is an intersection of one finite upper axis and one finite
    lower axis, at ``z = 0``, inside the periodic span. The four corners of
    the active window are the end of only one family; the manual CAD still
    puts a sphere on each of them. A 7-cell domain therefore has 23 spheres.
    Extension crossings outside ``y = ±span/2`` are not centres.
    """
    uppers = []
    lowers = []
    for segment in filament_segments(layout):
        if segment["layer"] == "upper":
            uppers.append(segment)
        else:
            lowers.append(segment)
    found = []
    y_min = layout["y_min"]
    y_max = layout["y_max"]
    for upper in uppers:
        upper_xy = _axis_segment_xy(upper)
        for lower in lowers:
            point = _xy_segment_intersection(upper_xy, _axis_segment_xy(lower))
            if point is None:
                continue
            if point[1] < y_min - POSITION_TOL_M or point[1] > y_max + POSITION_TOL_M:
                continue
            if any(
                math.hypot(point[0] - prior[0], point[1] - prior[1]) < POSITION_TOL_M
                for prior in found
            ):
                continue
            found.append((point[0], point[1], 0.0))
    if not found:
        raise RuntimeError(f"{layout['geo_id']} has no upper/lower axis crossings.")
    for corner in _active_window_corners(layout):
        if any(
            math.hypot(corner[0] - prior[0], corner[1] - prior[1]) < POSITION_TOL_M
            for prior in found
        ):
            continue
        found.append(corner)
    found.sort(key=lambda point: (point[0], point[1]))
    return found


def _active_window_corners(layout):
    """The four corners of the active window, at ``z = 0``."""
    return [
        (layout["x_active_0"], layout["y_min"], 0.0),
        (layout["x_active_0"], layout["y_max"], 0.0),
        (layout["x_active_1"], layout["y_min"], 0.0),
        (layout["x_active_1"], layout["y_max"], 0.0),
    ]


def check_spacer_contacts(layout, segments=None, centers=None):
    """Raise unless the spheres sit on the filaments and the solids connect.

    No Discovery session. A crossing fails when its in-plane distance to the
    nearest upper axis or the nearest lower axis is at least ``1e-9`` m.
    An active-window corner is the end of one family, so it only has to meet
    that one axis. Two solids overlap when the distance between their axes
    (a sphere axis is its centre) is less than the sum of the radii. That
    graph must be one component.
    """
    if segments is None:
        segments = filament_segments(layout)
    if centers is None:
        centers = sphere_centers(layout)
    names = _spacer_body_names(len(segments), len(centers))
    filament_radius = layout["filament_radius_m"]
    sphere_radius = layout["sphere_radius_m"]
    upper_axes = [
        _axis_segment_xy(segment)
        for segment in segments
        if segment["layer"] == "upper"
    ]
    lower_axes = [
        _axis_segment_xy(segment)
        for segment in segments
        if segment["layer"] == "lower"
    ]
    axis_errors = []
    corners = _active_window_corners(layout)
    for index, center in enumerate(centers):
        point = (center[0], center[1])
        upper_distance = min(
            _xy_point_segment_distance(point, axis) for axis in upper_axes
        )
        lower_distance = min(
            _xy_point_segment_distance(point, axis) for axis in lower_axes
        )
        on_upper = upper_distance < POSITION_TOL_M
        on_lower = lower_distance < POSITION_TOL_M
        if on_upper and on_lower:
            continue
        if (on_upper or on_lower) and _is_active_window_corner(point, corners):
            continue
        axis_errors.append(
            f"sphere_{index} upper {upper_distance:.6e} m, "
            f"lower {lower_distance:.6e} m"
        )
    solids = []
    for index, segment in enumerate(segments):
        solids.append((_axis_segment_3d(segment), filament_radius))
    for center in centers:
        solids.append(((center, center), sphere_radius))
    contacts = {name: set() for name in names}
    for left in range(len(names)):
        for right in range(left + 1, len(names)):
            gap = _segment_distance_3d(solids[left][0], solids[right][0])
            if gap < solids[left][1] + solids[right][1]:
                contacts[names[left]].add(names[right])
                contacts[names[right]].add(names[left])
    _absorbed, outside = _contact_component(names[0], contacts)
    if not axis_errors and not outside:
        return contacts
    lines = [f"{layout['geo_id']} spacer contact check failed."]
    if axis_errors:
        lines.append(
            "Spheres not on both filament axes: "
            + ", ".join(axis_errors)
        )
    if outside:
        lines.append(
            f"Bodies outside the contact component of {names[0]!r}: "
            + ", ".join(outside)
        )
    raise RuntimeError(" ".join(lines))


def nominal_areas_m2(layout):
    """Fluid-boundary areas for the probed domain.

    Inlet, outlet, and both buffer strips are the full rectangles. Each
    membrane value is the active rectangle minus one contact strip per cell
    (``membrane_contact_width`` times the cell diagonal). The filaments cut
    ``membrane_trim_m`` into the membrane, so those strips are not fluid
    membrane faces. For production ``D2450_a45`` the two membrane values sum
    to about ``1.5766e-4`` m², against ``1.6809e-4`` m² for the two full
    rectangles. Tests compare these values with the manual ``.dsco`` areas.
    The generator classifies faces by position, not by this area.
    """
    width_m = layout["y_max"] - layout["y_min"]
    band_m = membrane_contact_width_m(layout["filament_d_m"], layout["membrane_trim_m"])
    diagonal_m = math.hypot(layout["pitch_m"], layout["periodic_dy_m"])
    blocked_m2 = layout["n_active"] * diagonal_m * band_m
    active_m2 = layout["n_active"] * layout["pitch_m"] * width_m
    full_end_m2 = width_m * layout["h_m"]
    return {
        "inlet": full_end_m2,
        "outlet": full_end_m2,
        "wall_top_buffer_in": layout["buffer_in_m"] * width_m,
        "wall_bottom_buffer_in": layout["buffer_in_m"] * width_m,
        "wall_top_buffer_out": layout["buffer_out_m"] * width_m,
        "wall_bottom_buffer_out": layout["buffer_out_m"] * width_m,
        "wall_top_mem": active_m2 - blocked_m2,
        "wall_bottom_mem": active_m2 - blocked_m2,
    }


def generate_diamond_cad(
    *,
    geo_id,
    out_dir,
    n_active=None,
    debug_booleans=False,
    unite_spacer=False,
):
    """Write ``<geo_id>.pmdb``, ``.scdocx``, and ``_meta.json`` under ``out_dir``.

    ``out_dir`` must not be ``C:/ro_data`` or anywhere under it.
    ``debug_booleans`` writes ``<geo_id>_boolean_debug.log`` in that directory
    and, on the first boolean failure or a multi-body result, the design
    ``.pmdb`` and ``.scdocx`` next to the log. The default cuts the active
    box by every upper filament, then every joint sphere, then every lower
    filament. ``unite_spacer`` builds one spacer first: upper filaments and
    joint spheres, then the lower filaments, then the active box minus that
    spacer.
    """
    layout = diamond_layout(geo_id, n_active=n_active)
    out_dir = _require_out_dir(out_dir)
    paths = _output_paths(out_dir, layout["geo_id"])
    _refuse_existing(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = filament_segments(layout)
    centers = sphere_centers(layout)
    contacts = check_spacer_contacts(layout, segments, centers)
    trace = None
    if debug_booleans:
        trace = _BooleanDebug(out_dir, layout["geo_id"])
        print(f"boolean debug log: {trace.log_path}", flush=True)

    modeler = None
    try:
        _bind_geometry_symbols()
        modeler = launch_modeler_with_discovery(
            version=251,
            api_version=ApiVersions.V_251,
            hidden=True,
        )
        design = modeler.create_design(layout["geo_id"])
        if design.name != layout["geo_id"]:
            raise RuntimeError(
                f"Design name {design.name!r} does not match geo_id {layout['geo_id']!r}."
            )
        body = _build_fluid(
            design,
            layout,
            segments,
            centers,
            contacts,
            trace,
            unite_spacer=unite_spacer,
        )
        counts = _classify_and_name(design, body, layout)
        design.export_to_pmdb(out_dir)
        design.export_to_scdocx(out_dir)
        if not paths["pmdb"].is_file() or not paths["scdocx"].is_file():
            raise RuntimeError(
                "Export did not write the expected files: "
                f"{paths['pmdb']} and {paths['scdocx']}."
            )
        meta = _meta_payload(
            layout=layout,
            segment_count=len(segments),
            sphere_count=len(centers),
            counts=counts,
            backend_version=str(modeler.client.backend_version),
            pmdb_sha256=sha256_file(paths["pmdb"]),
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
    for label in BOUNDARY_LABELS:
        print(f"  {label}: {counts[label]}")
    print(f"  total_faces: {sum(counts.values())}")
    return {"paths": {key: str(path) for key, path in paths.items()}, "face_counts": counts}


def _segment(*, layer, start, direction, extension_m, length_m):
    return {
        "layer": layer,
        "origin": (
            start[0] - extension_m * direction[0],
            start[1] - extension_m * direction[1],
            start[2],
        ),
        "direction": direction,
        "length": length_m,
        "start": start,
    }


def _require_angle_identity(pitch_m, periodic_dy_m, attack_deg, geo_id):
    theta = math.radians(attack_deg)
    sine = math.sin(theta)
    if sine <= 0.0:
        raise ValueError(f"{geo_id} attack angle must be in (0, 180) degrees.")
    left = pitch_m * math.cos(theta)
    right = periodic_dy_m * sine
    if abs(left - right) > ANGLE_IDENTITY_RTOL * abs(left):
        raise ValueError(
            f"{geo_id} does not satisfy pitch*cos(angle) = span*sin(angle): "
            f"{left} m vs {right} m."
        )


def _bind_geometry_symbols():
    """Import PyAnsys Geometry when a body is built, not at module import."""
    global pyansys_geometry, ApiVersions, launch_modeler_with_discovery
    global SurfaceType, Plane, Point2D, Point3D, UnitVector3D
    global PlaneSurface, Sketch, Distance
    pyansys_geometry = importlib.import_module("ansys.geometry.core")
    ApiVersions = importlib.import_module(
        "ansys.geometry.core.connection.backend"
    ).ApiVersions
    launch_modeler_with_discovery = importlib.import_module(
        "ansys.geometry.core.connection.launcher"
    ).launch_modeler_with_discovery
    SurfaceType = importlib.import_module(
        "ansys.geometry.core.designer.face"
    ).SurfaceType
    Plane = importlib.import_module("ansys.geometry.core.math.plane").Plane
    points = importlib.import_module("ansys.geometry.core.math.point")
    Point2D = points.Point2D
    Point3D = points.Point3D
    UnitVector3D = importlib.import_module(
        "ansys.geometry.core.math.vector"
    ).UnitVector3D
    PlaneSurface = importlib.import_module(
        "ansys.geometry.core.shapes.surfaces.plane"
    ).PlaneSurface
    Sketch = importlib.import_module("ansys.geometry.core.sketch.sketch").Sketch
    Distance = importlib.import_module(
        "ansys.geometry.core.misc.measurements"
    ).Distance


def _build_fluid(
    design, layout, segments, centers, contacts, trace=None, unite_spacer=False
):
    if not segments:
        raise RuntimeError("The layout produced no filament segments.")
    upper_names = []
    lower_names = []
    for index, segment in enumerate(segments):
        name = f"filament_{index}"
        _extrude_cylinder(
            design,
            name,
            segment["origin"],
            segment["direction"],
            layout["filament_radius_m"],
            segment["length"],
        )
        if segment["layer"] == "upper":
            upper_names.append(name)
        else:
            lower_names.append(name)
    sphere_names = []
    for index, center in enumerate(centers):
        name = f"sphere_{index}"
        body = design.create_sphere(
            name,
            Point3D(list(center)),
            Distance(layout["sphere_radius_m"]),
        )
        if body is None:
            raise RuntimeError(f"create_sphere returned None for {name}.")
        sphere_names.append(name)
    _extrude_box(design, "buffer_in", 0.0, layout["x_active_0"], layout)
    _extrude_box(design, "active", layout["x_active_0"], layout["x_active_1"], layout)
    _extrude_box(design, "buffer_out", layout["x_active_1"], layout["x_outlet"], layout)
    counter = [0]
    for op, host_name, tool_name in _cutting_operations(
        unite_spacer=unite_spacer,
        upper_names=upper_names,
        sphere_names=sphere_names,
        lower_names=lower_names,
        contacts=contacts,
    ):
        _apply_boolean(
            design,
            op,
            host_name,
            tool_name,
            trace,
            counter,
            fluid_body=(op == "subtract"),
            volume_change="decrease" if op == "subtract" else "increase",
        )
    if unite_spacer:
        _body_named(design, upper_names[0]).name = "spacer"
        _apply_boolean(
            design,
            "subtract",
            "active",
            "spacer",
            trace,
            counter,
            fluid_body=True,
            volume_change="decrease",
        )
    _apply_boolean(
        design,
        "unite",
        "buffer_in",
        "active",
        trace,
        counter,
        fluid_body=False,
        volume_change="increase",
    )
    _apply_boolean(
        design,
        "unite",
        "buffer_in",
        "buffer_out",
        trace,
        counter,
        fluid_body=False,
        volume_change="increase",
    )
    fluid = _body_named(design, "buffer_in")
    fluid.name = f"{layout['geo_id'].lower()}-solid"
    fluid = _body_named(design, fluid.name)
    if trace is not None:
        trace.note_final(design, fluid)
    alive = [body for body in design.bodies if body.is_alive]
    if len(alive) != 1 or alive[0].id != fluid.id:
        raise RuntimeError(
            "Boolean construction left "
            f"{[(body.name, body.id) for body in alive]!r}, expected one body named "
            f"{fluid.name!r}."
        )
    _imprint_membranes(fluid, layout)
    return fluid


def _extrude_box(design, name, x0, x1, layout):
    if x1 <= x0:
        raise RuntimeError(f"Box {name} has non-positive length: {x0} to {x1}.")
    plane = Plane(
        Point3D([0.0, 0.0, layout["z_min"]]),
        UnitVector3D(_PLUS_X),
        UnitVector3D(_PLUS_Y),
    )
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
    """Split each membrane at the active-buffer stations. Ignore the return."""
    y0 = layout["y_min"] - IMPRINT_MARGIN_M
    y1 = layout["y_max"] + IMPRINT_MARGIN_M
    stations = (layout["x_active_0"], layout["x_active_1"])
    for z_m, normal in ((layout["z_max"], _PLUS_Z), (layout["z_min"], _MINUS_Z)):
        faces = _faces_on_membrane(body, z_m, normal)
        if not faces:
            raise RuntimeError(f"No membrane face at z={z_m} m before imprint.")
        plane = Plane(
            Point3D([0.0, 0.0, z_m]),
            UnitVector3D(_PLUS_X),
            UnitVector3D(_PLUS_Y),
        )
        sketch = Sketch(plane)
        for x_m in stations:
            sketch.segment(Point2D([x_m, y0]), Point2D([x_m, y1]))
        body.imprint_curves(faces=faces, sketch=sketch)


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


def _classify_and_name(design, body, layout):
    groups = {label: [] for label in BOUNDARY_LABELS}
    seen = set()
    for face in body.faces:
        if face.id in seen:
            raise RuntimeError(f"Face id {face.id!r} was listed twice.")
        seen.add(face.id)
        kind = face.surface_type
        if kind is SurfaceType.SURFACETYPE_PLANE:
            groups[_plane_label(face, layout)].append(face)
        elif kind in (
            SurfaceType.SURFACETYPE_CYLINDER,
            SurfaceType.SURFACETYPE_SPHERE,
        ):
            groups["wall_spacer"].append(face)
        else:
            raise RuntimeError(
                f"Face {face.id!r} has unsupported surface type {kind!r}."
            )
    for label, faces in groups.items():
        if not faces:
            raise RuntimeError(f"Label {label} has no faces.")
        design.create_named_selection(label, faces=faces)
    return {label: len(faces) for label, faces in groups.items()}


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
        return "wall_spacer"
    if _axis_is_y(normal) and abs(origin[1] - layout["y_min"]) <= POSITION_TOL_M:
        return "periodic_r"
    if _axis_is_y(normal) and abs(origin[1] - layout["y_max"]) <= POSITION_TOL_M:
        return "periodic_l"
    if _direction_close(normal, _PLUS_Z) and abs(origin[2] - layout["z_max"]) <= POSITION_TOL_M:
        return _membrane_label(face, "top", layout)
    if _direction_close(normal, _MINUS_Z) and abs(origin[2] - layout["z_min"]) <= POSITION_TOL_M:
        return _membrane_label(face, "bottom", layout)
    raise RuntimeError(
        f"Plane face {face.id!r} normal={normal} origin={origin} has no label."
    )


def _membrane_label(face, side, layout):
    box = face.bounding_box
    xmin = _length_m(box.min_corner.x)
    xmax = _length_m(box.max_corner.x)
    if xmax <= layout["x_active_0"] + POSITION_TOL_M:
        return f"wall_{side}_buffer_in"
    if xmin >= layout["x_active_1"] - POSITION_TOL_M:
        return f"wall_{side}_buffer_out"
    if (
        xmin >= layout["x_active_0"] - POSITION_TOL_M
        and xmax <= layout["x_active_1"] + POSITION_TOL_M
    ):
        return f"wall_{side}_mem"
    raise RuntimeError(
        f"Membrane {side} face x-range [{xmin}, {xmax}] m crosses an active station."
    )


def _is_active_window_corner(point, corners):
    return any(
        math.hypot(point[0] - corner[0], point[1] - corner[1]) < POSITION_TOL_M
        for corner in corners
    )


def _cutting_operations(
    *, unite_spacer, upper_names, sphere_names, lower_names, contacts
):
    """Boolean steps that cut the spacer out of the active box.

    The default subtracts every upper filament, then every joint sphere,
    then every lower filament. ``unite_spacer`` absorbs each sphere into
    the filament it touches (an upper when it has one) before any lower
    filament is united into an upper, then leaves one spacer to subtract.
    """
    if not unite_spacer:
        return [
            ("subtract", "active", name)
            for name in (*upper_names, *sphere_names, *lower_names)
        ]
    pairs = _plan_layer_union(contacts, upper_names, sphere_names, lower_names)
    return [("unite", host, tool) for host, tool in pairs]


def _plan_layer_union(contacts, upper_names, sphere_names, lower_names):
    """Return ``(host, tool)`` unites that keep the second layer last.

    Spheres that meet an upper filament are absorbed into that upper first.
    A corner sphere that meets only a lower filament is absorbed into that
    lower before the lower is united into an upper. The survivor is
    ``upper_names[0]``.
    """
    graph = {name: set(neighbors) for name, neighbors in contacts.items()}
    host = upper_names[0]
    uppers = set(upper_names)
    pairs = []

    def absorb(tool):
        neighbors = graph[tool]
        target = next((name for name in upper_names if name in neighbors), None)
        if target is None:
            target = next((name for name in lower_names if name in neighbors), None)
        if target is None:
            raise RuntimeError(f"{tool} does not touch a filament.")
        _transfer_contacts(graph, target, tool)
        pairs.append((target, tool))

    for name in sphere_names:
        if any(upper in graph[name] for upper in uppers):
            absorb(name)
    for name in sphere_names:
        if name in graph:
            absorb(name)
    guard = 0
    limit = len(contacts) + 1
    while len(graph) > 1:
        guard += 1
        if guard > limit:
            raise RuntimeError(
                "Layer union stopped with "
                + ", ".join(sorted(graph))
                + "."
            )
        choice = _next_layer_unite(graph, host, upper_names, lower_names)
        _transfer_contacts(graph, choice[0], choice[1])
        pairs.append(choice)
    if host not in graph:
        raise RuntimeError(f"Layer union did not keep {host!r}.")
    return pairs


def _next_layer_unite(graph, host, upper_names, lower_names):
    for name in lower_names:
        if name in graph and host in graph[name]:
            return host, name
    for name in graph:
        if name != host and host in graph[name]:
            return host, name
    for name in lower_names:
        if name not in graph:
            continue
        for upper in upper_names:
            if upper in graph and upper in graph[name]:
                return upper, name
    raise RuntimeError(
        "Layer union has no touching pair among " + ", ".join(sorted(graph)) + "."
    )


def _transfer_contacts(graph, host, tool):
    for other in list(graph[tool]):
        graph[other].discard(tool)
        if other != host:
            graph[other].add(host)
            graph[host].add(other)
    graph[host].discard(tool)
    del graph[tool]


def _apply_boolean(
    design,
    op,
    host_name,
    tool_name,
    trace,
    counter,
    *,
    fluid_body,
    volume_change,
):
    """Run one boolean, then require the body count and the volume change."""
    host = _body_named(design, host_name)
    host_id = host.id
    volume_before = _volume_m3(host)
    alive_before = _alive_bodies(design)
    _run_boolean(design, op, host_name, tool_name, trace)
    counter[0] += 1
    op_index = counter[0]
    _require_boolean_result(
        design,
        trace,
        op_index=op_index,
        op=op,
        host_name=host_name,
        host_id=host_id,
        tool_name=tool_name,
        alive_before=alive_before,
        volume_before=volume_before,
        fluid_body=fluid_body,
        volume_change=volume_change,
    )


def _require_boolean_result(
    design,
    trace,
    *,
    op_index,
    op,
    host_name,
    host_id,
    tool_name,
    alive_before,
    volume_before,
    fluid_body,
    volume_change,
):
    try:
        alive = _alive_bodies(design)
        hosts = [body for body in alive if getattr(body, "id", None) == host_id]
        volume_after = _volume_m3(hosts[0]) if len(hosts) == 1 else None
    except Exception as exc:
        _fail_boolean(
            trace,
            design,
            op_index,
            f"{op} host={host_name} tool={tool_name}: "
            f"{type(exc).__name__}: {exc}",
        )
    expected = len(alive_before) - 1
    if len(hosts) != 1 or len(alive) != expected:
        _fail_boolean(
            trace,
            design,
            op_index,
            f"{op} host={host_name} tool={tool_name}: "
            f"alive {len(alive)}, expected {expected}; host bodies {len(hosts)}.",
        )
    if fluid_body:
        named = [body for body in alive if getattr(body, "name", None) == host_name]
        if len(named) != 1:
            _fail_boolean(
                trace,
                design,
                op_index,
                f"{op} host={host_name} tool={tool_name}: "
                f"expected one fluid body, found {len(named)}.",
            )
    if any(getattr(body, "name", None) == tool_name for body in alive):
        _fail_boolean(
            trace,
            design,
            op_index,
            f"{op} host={host_name} tool={tool_name}: tool is still alive.",
        )
    decreased = volume_after < volume_before
    increased = volume_after > volume_before
    if volume_change == "decrease" and not decreased:
        _fail_boolean(
            trace,
            design,
            op_index,
            f"{op} host={host_name} tool={tool_name}: fluid volume "
            f"{volume_before:.6e} did not decrease (now {volume_after:.6e}).",
        )
    if volume_change == "increase" and not increased:
        _fail_boolean(
            trace,
            design,
            op_index,
            f"{op} host={host_name} tool={tool_name}: volume "
            f"{volume_before:.6e} did not increase (now {volume_after:.6e}).",
        )


def _fail_boolean(trace, design, op_index, detail):
    message = f"op {op_index} {detail}"
    if trace is not None:
        trace._append(message + "\n")
        trace.save(design, message)
    raise RuntimeError(message)


def _alive_bodies(design):
    return [body for body in design.bodies if body.is_alive]


def _volume_m3(body):
    volume = body.volume
    if isinstance(volume, (int, float)):
        return float(volume)
    for attr in ("m", "value"):
        magnitude = getattr(volume, attr, None)
        if isinstance(magnitude, (int, float)):
            return float(magnitude)
    raise RuntimeError(
        f"Body {getattr(body, 'name', '?')!r} volume is {volume!r}."
    )


def _run_boolean(design, op, host_name, tool_name, trace):
    """Run one unite or subtract. The trace records the design after it."""
    error = None
    try:
        host = _body_named(design, host_name)
        tool = _body_named(design, tool_name)
        if op == "unite":
            host.unite(tool)
        elif op == "subtract":
            host.subtract(tool)
        else:
            raise RuntimeError(f"Unknown boolean op {op!r}.")
    except Exception as exc:
        error = exc
    if trace is not None:
        trace.record(design, op, host_name, tool_name, error)
    if error is not None:
        raise error


class _BooleanDebug:
    """Append one block per boolean to ``<geo_id>_boolean_debug.log``.

    The first raised boolean, or a final design that is not a single body,
    is exported as ``.pmdb`` and ``.scdocx`` in the same directory. ``out_dir``
    has already been refused when it is under ``C:/ro_data``.
    """

    def __init__(self, out_dir, geo_id):
        self.out_dir = Path(out_dir)
        self.log_path = self.out_dir / f"{geo_id}_boolean_debug.log"
        self._index = 0
        self.saved = False
        self.log_path.write_text(
            f"geo_id={geo_id}\n"
            "host and tool are the names requested before the op. "
            "The body list is the design after the op.\n",
            encoding="utf-8",
        )

    def record(self, design, op, host_name, tool_name, error):
        self._index += 1
        if error is None:
            raised = "no"
        else:
            raised = f"yes {type(error).__name__}: {error}"
        self._append(
            f"op {self._index} {op} host={host_name} tool={tool_name} raised={raised}\n"
            + _boolean_body_block(design)
        )
        if error is not None:
            self.save(design, f"op {self._index} {op} raised")

    def note_final(self, design, fluid):
        try:
            alive = [body for body in design.bodies if body.is_alive]
            ok = len(alive) == 1 and alive[0].id == fluid.id
            summary = f"end alive={len(alive)} ok={'yes' if ok else 'no'}"
        except Exception as exc:
            ok = False
            summary = f"end check unavailable: {type(exc).__name__}: {exc}"
        self._append(summary + "\n" + _boolean_body_block(design))
        if not ok:
            self.save(design, summary)

    def save(self, design, reason):
        if self.saved:
            self._append(f"save skipped ({reason}); design already saved\n")
            return
        self.saved = True
        self._append(f"save reason: {reason}\n")
        for label, method in (("pmdb", "export_to_pmdb"), ("scdocx", "export_to_scdocx")):
            try:
                path = getattr(design, method)(self.out_dir)
            except Exception as exc:
                self._append(f"save {label} failed: {type(exc).__name__}: {exc}\n")
            else:
                self._append(f"saved {label}: {path}\n")

    def _append(self, text):
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()


def _boolean_body_block(design):
    try:
        bodies = list(design.bodies)
    except Exception as exc:
        return f"  bodies unavailable: {type(exc).__name__}: {exc}\n"
    if not bodies:
        return "  bodies: none\n"
    lines = []
    for body in bodies:
        name = getattr(body, "name", "?")
        ident = getattr(body, "id", "?")
        alive = getattr(body, "is_alive", "?")
        lines.append(
            f"  body name={name} id={ident} alive={alive} volume={_volume_text(body)}"
        )
    return "\n".join(lines) + "\n"


def _volume_text(body):
    try:
        volume = body.volume
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"
    if isinstance(volume, (int, float)):
        return f"{float(volume):.6e}"
    for attr in ("m", "value"):
        magnitude = getattr(volume, attr, None)
        if isinstance(magnitude, (int, float)):
            return f"{float(magnitude):.6e}"
    if volume is None:
        return "unavailable"
    return str(volume)


def _spacer_body_names(segment_count, sphere_count):
    names = [f"filament_{index}" for index in range(segment_count)]
    names.extend(f"sphere_{index}" for index in range(sphere_count))
    return names


def _contact_component(host, contacts):
    """Return bodies absorbed in unite order, then those still outside."""
    absorbed = {host}
    order = []
    pending = [name for name in contacts if name != host]
    changed = True
    while changed and pending:
        changed = False
        still = []
        for name in pending:
            if contacts[name].isdisjoint(absorbed):
                still.append(name)
                continue
            absorbed.add(name)
            order.append(name)
            changed = True
        pending = still
    return order, pending


def _axis_segment_xy(segment):
    origin = segment["origin"]
    direction = segment["direction"]
    length = segment["length"]
    start = (origin[0], origin[1])
    end = (
        origin[0] + length * direction[0],
        origin[1] + length * direction[1],
    )
    return start, end


def _axis_segment_3d(segment):
    origin = segment["origin"]
    direction = segment["direction"]
    length = segment["length"]
    start = origin
    end = tuple(origin[axis] + length * direction[axis] for axis in range(3))
    return start, end


def _xy_segment_intersection(first, second):
    """Return the point where two finite xy segments meet, or None."""
    ax, ay = first[0]
    bx, by = first[1]
    cx, cy = second[0]
    dx, dy = second[1]
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denom = rx * sy - ry * sx
    if abs(denom) <= POSITION_TOL_M * POSITION_TOL_M:
        return None
    qx, qy = cx - ax, cy - ay
    t = (qx * sy - qy * sx) / denom
    u = (qx * ry - qy * rx) / denom
    if t < -POSITION_TOL_M or t > 1.0 + POSITION_TOL_M:
        return None
    if u < -POSITION_TOL_M or u > 1.0 + POSITION_TOL_M:
        return None
    return (ax + t * rx, ay + t * ry)


def _xy_point_segment_distance(point, segment):
    ax, ay = segment[0]
    bx, by = segment[1]
    px, py = point
    vx, vy = bx - ax, by - ay
    length_sq = vx * vx + vy * vy
    if length_sq <= POSITION_TOL_M * POSITION_TOL_M:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * vx + (py - ay) * vy) / length_sq
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _segment_distance_3d(first, second):
    """Shortest distance between two finite 3D segments."""
    start_a, end_a = first
    start_b, end_b = second
    direction_a = [end_a[axis] - start_a[axis] for axis in range(3)]
    direction_b = [end_b[axis] - start_b[axis] for axis in range(3)]
    offset = [start_a[axis] - start_b[axis] for axis in range(3)]
    aa = sum(value * value for value in direction_a)
    bb = sum(direction_a[axis] * direction_b[axis] for axis in range(3))
    cc = sum(value * value for value in direction_b)
    dd = sum(direction_a[axis] * offset[axis] for axis in range(3))
    ee = sum(direction_b[axis] * offset[axis] for axis in range(3))
    denom = aa * cc - bb * bb
    small = POSITION_TOL_M * POSITION_TOL_M
    if aa <= small and cc <= small:
        s_param = 0.0
        t_param = 0.0
    elif aa <= small:
        s_param = 0.0
        t_param = _clamp_unit(ee / cc) if cc > small else 0.0
    elif cc <= small:
        t_param = 0.0
        s_param = _clamp_unit(-dd / aa)
    else:
        s_param = _clamp_unit((bb * ee - cc * dd) / denom) if abs(denom) > small else 0.0
        t_param = (bb * s_param + ee) / cc
        if t_param < 0.0:
            t_param = 0.0
            s_param = _clamp_unit(-dd / aa)
        elif t_param > 1.0:
            t_param = 1.0
            s_param = _clamp_unit((bb - dd) / aa)
    nearest_a = [start_a[axis] + s_param * direction_a[axis] for axis in range(3)]
    nearest_b = [start_b[axis] + t_param * direction_b[axis] for axis in range(3)]
    return math.dist(nearest_a, nearest_b)


def _clamp_unit(value):
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _body_named(design, name):
    matches = [body for body in design.bodies if body.is_alive and body.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one live body named {name!r}, found {len(matches)}.")
    return matches[0]


def _meta_payload(*, layout, segment_count, sphere_count, counts, backend_version, pmdb_sha256):
    return {
        "status": "success",
        "geo_id": layout["geo_id"],
        "inputs": {
            "geo_id": layout["geo_id"],
            "n_active": layout["n_active"],
        },
        "derived": {
            "pitch_m": layout["pitch_m"],
            "periodic_dy_m": layout["periodic_dy_m"],
            "attack_angle_deg": layout["attack_angle_deg"],
            "filament_angle_from_x_deg": math.degrees(layout["filament_angle_from_x_rad"]),
            "upper_layer": "dy/dx < 0, z = +filament_radius",
            "filament_d_m": layout["filament_d_m"],
            "sphere_radius_m": layout["sphere_radius_m"],
            "membrane_trim_m": layout["membrane_trim_m"],
            "channel_height_m": layout["h_m"],
            "buffer_in_m": layout["buffer_in_m"],
            "buffer_out_m": layout["buffer_out_m"],
            "n_active_cells": layout["n_active"],
            "n_active_registry": layout["n_active_registry"],
            "x_active_m": [layout["x_active_0"], layout["x_active_1"]],
            "x_outlet_m": layout["x_outlet"],
            "y_m": [layout["y_min"], layout["y_max"]],
            "z_m": [layout["z_min"], layout["z_max"]],
            "filament_segment_count": segment_count,
            "sphere_count": sphere_count,
        },
        "face_counts": counts,
        "ansys_geometry_core_version": pyansys_geometry.__version__,
        "backend_version": backend_version,
        "pmdb_sha256": pmdb_sha256,
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
    if _is_production_data_root(path):
        raise ValueError(
            f"out_dir {path} is under C:/ro_data. Write Diamond CAD under an MFBO data root."
        )
    if path.exists() and not path.is_dir():
        raise ValueError(f"out_dir is not a directory: {path}")
    return path


def _is_production_data_root(path):
    texts = [str(path).replace("\\", "/"), path.as_posix()]
    try:
        texts.append(path.resolve().as_posix())
    except OSError as exc:
        raise ValueError(f"Could not resolve out_dir {path}: {exc}") from exc
    for text in texts:
        folded = text.replace("\\", "/").casefold().rstrip("/")
        for root in _PRODUCTION_ROOTS:
            if folded == root or folded.startswith(root + "/"):
                return True
    return False


def _require_geo_id(geo_id):
    if not isinstance(geo_id, str) or not geo_id or geo_id.strip() != geo_id:
        raise ValueError(f"geo_id must be a non-empty string, got {geo_id!r}.")
    if any(part in geo_id for part in ("/", "\\", "..")):
        raise ValueError(f"geo_id must not be a path, got {geo_id!r}.")
    return geo_id


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
    if hasattr(value, "m"):
        return float(value.m)
    return float(value)


def _plane_geometry(face):
    geometry = face.shape.geometry
    if not isinstance(geometry, PlaneSurface):
        raise RuntimeError(
            f"Face {face.id!r} is PLANE but geometry is {type(geometry).__name__}."
        )
    return geometry


def _point(value):
    return (float(value[0]), float(value[1]), float(value[2]))


def _vector(value):
    return (float(value[0]), float(value[1]), float(value[2]))


def _direction_close(left, right):
    return all(abs(left[index] - right[index]) <= DIRECTION_TOL for index in range(3))


def _axis_is_y(direction):
    return _direction_close(direction, _PLUS_Y) or _direction_close(direction, _MINUS_Y)
