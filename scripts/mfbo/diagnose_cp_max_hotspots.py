"""Locate per-cell CP max outliers on a copied production run.

Reads a production run leaf under ``C:/ro_data`` only to copy it. Fluent
opens the copy under ``--data-root``, because reading the production case
can compile ``libudf`` into that case directory.

``pp_cp_canon_max_cell_N`` is not an area quantile. It is the surface
facet-maximum of ``udm-9`` times the per-cell rescale ``k_N``
(``src/ro/fluent_report_helpers.py`` 1969 and 2118). The 99.9% area
quantile in this diagnostic is the cm (``udm-7``) quantile the rescale
guard uses for ``cp_perm_max`` (same file, 2020–2038), evaluated on the
same evaluation-window iso-clip face set.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mfbo._common as mfbo_common
import mfbo.run_parity_mesh as parity_mesh
from ro.cp_metrics import CP_SPREAD_AREA_QUANTILE_HI
from ro.domain_layout import (
    CURRENT_EVALUATION_WINDOW,
    layout_from_run_directory,
)
from ro.fluent_report_helpers import (
    create_x_range_iso_clip,
    delete_iso_clip,
    list_named_object_names,
    scoring_geometry_from_layout,
)
from ro.paths import RUN_ID_RE, project_root
from ro.udm_layout import FIELD_UDM_CM, FIELD_UDM_CP, FIELD_UDM_JW, FIELD_UDM_Y1

MEMBRANE_WALLS = ("wall_top_mem", "wall_bottom_mem")
NACL_CELL_FIELD = "concentration-nacl"
TOP_FACE_COUNT = 20
CM_AREA_QUANTILE = CP_SPREAD_AREA_QUANTILE_HI
_PRODUCTION_ROOTS = ("c:/ro_data", "/mnt/c/ro_data")

# Matched production path. Line numbers are the current source.
CP_CANON_MAX_PATH = {
    "column": "pp_cp_canon_max_cell_N",
    "field": FIELD_UDM_CP,
    "reduction": "surface-facetmax",
    "quantile": None,
    "formula": "pp_cp_canon_max_cell_N = facetmax(udm-9) * k_N",
    "facetmax_file": "src/ro/fluent_report_helpers.py",
    "facetmax_line": 1969,
    "assignment_line": 2118,
    "extract_call": "scripts/pyfluent_report_extract.py:1700",
    "face_set": (
        "x-range iso-clip of the evaluation-window cells on "
        "wall_top_mem and wall_bottom_mem"
    ),
    "cm_quantile_for_cp_perm_max": {
        "field": FIELD_UDM_CM,
        "quantile": CM_AREA_QUANTILE,
        "file": "src/ro/fluent_report_helpers.py",
        "lines": "2020-2038",
        "note": (
            "99.9% area quantile of udm-7 with the 0.1% Jw quantile "
            "feeds cp_perm_max for the rescale guard, not cp_canon_max."
        ),
    },
}

FACE_CSV_FIELDS = (
    "surface",
    "cell",
    "rank",
    "x",
    "y",
    "z",
    "area",
    "cm",
    "concentration_nacl",
    "jw",
    "y1",
    "jw_y1_over_d_salt",
    "exp_jw_y1_over_d_salt",
    "spacer_distance_m",
    "spacer_distance_note",
    "cm_area_quantile_999",
    "concentration_nacl_area_quantile_999",
)


def _folded(path) -> str:
    return str(path).replace("\\", "/").casefold().rstrip("/")


def _under_production(path) -> bool:
    folded = _folded(path)
    return any(
        folded == root or folded.startswith(root + "/")
        for root in _PRODUCTION_ROOTS
    )


def _is_windows_absolute(text: str) -> bool:
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def resolve_data_root(value):
    """Absolute data root that is not ``C:/ro_data`` or under it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    folded = text.casefold().rstrip("/")
    if folded == "c:/ro_data" or folded.startswith("c:/ro_data/"):
        raise ValueError(f"Refusing production data root: {value}")
    if _is_windows_absolute(text):
        path = Path(text)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"data root must be absolute, got {value!r}.")
        path = path.resolve()
    if _under_production(path):
        raise ValueError(f"Refusing production data root: {value}")
    return path


def parse_production_run_leaf(value):
    """Return identity for a read-only production run leaf.

    The path must be ``C:/ro_data/runs/<family>/<geo_id>/<mesh_id>/<run_id>``.
    """
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"source run is required, got {value!r}.")
    text = str(value).strip().replace("\\", "/")
    if not _under_production(text):
        raise ValueError(
            f"source run must be a production leaf under C:/ro_data, got {value!r}."
        )
    leaf = Path(text)
    if len(leaf.parents) < 5 or leaf.parents[3].name != "runs":
        raise ValueError(
            "source run must be C:/ro_data/runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {value!r}."
        )
    run_id = leaf.name
    mesh_id = leaf.parent.name
    geo_id = leaf.parents[1].name
    family = leaf.parents[2].name
    production_root = leaf.parents[4]
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"run id must match {RUN_ID_RE.pattern}, got {run_id!r}.")
    mesh_leaf = production_root / "meshes" / family / geo_id / mesh_id
    return {
        "run_leaf": leaf,
        "mesh_leaf": mesh_leaf,
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
    }


def relative_files(directory):
    root = Path(directory)
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def sha256_map(directory):
    root = Path(directory)
    return {
        relative: parity_mesh.sha256_file(root / relative)
        for relative in relative_files(root)
    }


def copy_or_reuse_tree(source, dest, *, label):
    """Copy a leaf with ``copy_geometry_file``. Refuse a different existing tree.

    Copy only. An existing destination is reused when every relative-path
    sha256 matches, and refused otherwise. Nothing is overwritten or deleted.
    """
    source = Path(source)
    dest = Path(dest)
    if _under_production(dest):
        raise ValueError(f"Refusing to copy {label} into C:/ro_data: {dest}")
    if not source.is_dir():
        raise FileNotFoundError(f"{label} not found: {source}")
    source_hashes = sha256_map(source)
    if not source_hashes:
        raise FileNotFoundError(f"{label} has no files: {source}")
    if dest.exists():
        if not dest.is_dir():
            raise FileExistsError(
                f"Refusing to overwrite {label} path that is not a directory: {dest}"
            )
        dest_hashes = sha256_map(dest)
        if dest_hashes != source_hashes:
            raise RuntimeError(
                f"{label} already exists and sha256s are not identical: {dest}."
            )
        return dest_hashes
    for relative, digest in source_hashes.items():
        written = parity_mesh.copy_geometry_file(source / relative, dest / relative)
        if written != digest:
            raise RuntimeError(
                f"Copied {label} file sha256 changed for {relative}: "
                f"{digest} -> {written}."
            )
    if not source.is_dir() or sha256_map(source) != source_hashes:
        raise RuntimeError(f"{label} source changed during copy: {source}")
    return source_hashes


def destination_leaves(data_root, identity):
    family = identity["family"]
    geo_id = identity["geo_id"]
    mesh_id = identity["mesh_id"]
    run_id = identity["run_id"]
    run_leaf = data_root / "runs" / family / geo_id / mesh_id / run_id
    mesh_leaf = data_root / "meshes" / family / geo_id / mesh_id
    return run_leaf, mesh_leaf


def copy_source_leaves(data_root, identity):
    """Copy the production run leaf and its mesh leaf under ``data_root``."""
    run_dest, mesh_dest = destination_leaves(data_root, identity)
    mesh_hashes = copy_or_reuse_tree(
        identity["mesh_leaf"], mesh_dest, label="mesh leaf"
    )
    run_hashes = copy_or_reuse_tree(
        identity["run_leaf"], run_dest, label="run leaf"
    )
    return {
        "run_leaf": run_dest,
        "mesh_leaf": mesh_dest,
        "mesh_sha256": mesh_hashes,
        "run_sha256": run_hashes,
    }


def final_case_data(run_leaf, geo_id, run_id):
    case_file = Path(run_leaf) / f"{geo_id}_{run_id}_final.cas.h5"
    data_file = Path(run_leaf) / f"{geo_id}_{run_id}_final.dat.h5"
    return case_file, data_file


def assert_fluent_opens_copies(data_root, run_leaf, case_file, data_file):
    """Fluent may only open files under the non-production data root."""
    data_root = Path(data_root)
    for label, path in (
        ("run leaf", run_leaf),
        ("case file", case_file),
        ("data file", data_file),
    ):
        path = Path(path)
        if _under_production(path):
            raise ValueError(
                f"Refusing to open {label} under C:/ro_data in Fluent: {path}"
            )
        if data_root != path and data_root not in path.parents:
            raise ValueError(
                f"Fluent {label} is not under the data root {data_root}: {path}"
            )
    if not case_file.is_file():
        raise FileNotFoundError(f"Copied final case not found: {case_file}")
    if not data_file.is_file():
        raise FileNotFoundError(f"Copied final data not found: {data_file}")


def load_run_config():
    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "diagnose_cp_run_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load run config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_post_launch_kwargs(cwd, run_config):
    """Meshing-mode launch used by report extraction, with 2 processors.

    Other launch values come from ``configs/run_config.py``. ``cwd`` must
    already be the copied run leaf.
    """
    if _under_production(cwd):
        raise ValueError(f"Refusing to launch Fluent under C:/ro_data: {cwd}")
    return {
        "product_version": run_config.product_version,
        "mode": "meshing",
        "dimension": 3,
        "precision": "double",
        "processor_count": 2,
        "ui_mode": "gui",
        "graphics_driver": run_config.graphics_driver,
        "start_timeout": run_config.fluent_start_timeout,
        "cwd": os.path.abspath(cwd).replace("\\", "/"),
    }


def evaluation_window_cells(layout, window=CURRENT_EVALUATION_WINDOW):
    """Global cell numbers for the production evaluation window."""
    return list(window.evaluation_cell_numbers(layout))


def cell_x_bounds(layout, cell_numbers, domain_x_min_m):
    geometry = scoring_geometry_from_layout(layout, domain_x_min_m)
    bounds = geometry.unit_cell_boundary_x_m
    spans = {}
    for cell_number in cell_numbers:
        index = int(cell_number) - 1
        if index < 0 or index + 1 >= len(bounds):
            raise ValueError(
                f"Cell {cell_number} is outside the layout x bounds."
            )
        spans[int(cell_number)] = (bounds[index], bounds[index + 1])
    return spans


def polygon_area(points):
    """Area of a 3D polygon (Newell)."""
    count = len(points)
    if count < 3:
        return 0.0
    nx = ny = nz = 0.0
    for index in range(count):
        x1, y1, z1 = points[index]
        x2, y2, z2 = points[(index + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def face_areas(vertices, connectivity):
    """Per-face areas from vertices and one node-id sequence per face."""
    areas = []
    for face in connectivity:
        ids = [int(node) for node in face]
        points = [vertices[node] for node in ids]
        areas.append(polygon_area(points))
    return areas


def area_quantile(values, areas, quantile):
    """Lowest value whose cumulative positive area fraction reaches ``quantile``."""
    pairs = [
        (float(value), float(area))
        for value, area in zip(values, areas)
        if float(area) > 0.0 and math.isfinite(float(value)) and math.isfinite(float(area))
    ]
    if not pairs:
        raise ValueError("Area quantile needs at least one face with positive area.")
    pairs.sort(key=lambda item: item[0])
    total = sum(area for _value, area in pairs)
    if total <= 0.0:
        raise ValueError("Area quantile total area is not positive.")
    covered = 0.0
    target = float(quantile) * total
    for value, area in pairs:
        covered += area
        if covered >= target:
            return value
    return pairs[-1][0]


def film_terms(jw, y1, d_salt):
    """Return ``Jw*y1/D_SALT`` and its exponential, or nulls when undefined."""
    if jw is None or y1 is None:
        return None, None
    if isinstance(d_salt, bool) or not isinstance(d_salt, (int, float)):
        raise TypeError(f"D_SALT must be a number, got {d_salt!r}.")
    if float(d_salt) == 0.0 or not math.isfinite(float(d_salt)):
        raise ValueError(f"D_SALT must be finite and non-zero, got {d_salt!r}.")
    argument = float(jw) * float(y1) / float(d_salt)
    if not math.isfinite(argument):
        return None, None
    return argument, math.exp(argument)


def top_faces_by_cm(faces, count=TOP_FACE_COUNT):
    """Highest-``cm`` faces. ``faces`` items are mappings with a ``cm`` key."""
    ranked = sorted(
        faces,
        key=lambda face: float("-inf") if face.get("cm") is None else float(face["cm"]),
        reverse=True,
    )
    return ranked[: int(count)]


def nearest_spacer_distances(face_xyz, spacer_xyz):
    """Distance to the nearest spacer-wall face centroid.

    Returns ``(distances, note)``. An empty spacer cloud is not a guess.
    """
    if not spacer_xyz:
        return [None] * len(face_xyz), (
            "not_computable: no spacer-wall face centroids"
        )
    from scipy.spatial import cKDTree

    tree = cKDTree(spacer_xyz)
    distances, _indexes = tree.query(face_xyz)
    if len(face_xyz) == 1:
        distances = [float(distances)]
    else:
        distances = [float(value) for value in distances]
    return distances, "nearest spacer-wall face centroid"


def zone_matches_base_name(zone_name, base_name):
    return zone_name == base_name or zone_name.startswith(base_name + ".")


def zones_for_base_name(zone_names, base_name):
    return sorted(
        name for name in zone_names if zone_matches_base_name(name, base_name)
    )


def output_directory(data_root, geo_id, run_id):
    path = Path(data_root) / "diagnostics" / f"{geo_id}_{run_id}"
    if _under_production(path):
        raise ValueError(f"Refusing to write diagnostics under C:/ro_data: {path}")
    return path


def _csv_cell(value):
    if value is None:
        return ""
    return value


def write_hotspot_report(directory, payload, face_rows):
    """Write ``hotspots.json`` and ``hotspots.csv``. Does not delete prior files."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "hotspots.json"
    csv_path = directory / "hotspots.csv"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FACE_CSV_FIELDS)
        writer.writeheader()
        for row in face_rows:
            writer.writerow({key: _csv_cell(row.get(key)) for key in FACE_CSV_FIELDS})
    return json_path, csv_path


def _as_vectors(array):
    rows = []
    for item in array:
        rows.append([float(item[0]), float(item[1]), float(item[2])])
    return rows


def _as_floats(array):
    return [float(value) for value in array]


def _surface_block(payload, data_type):
    """One surface's array from a ``get_surface_data`` mapping."""
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Surface data payload is empty.")
    surface_id = sorted(payload, key=str)[0]
    block = payload[surface_id]
    if isinstance(block, dict):
        if data_type not in block:
            raise RuntimeError(f"Surface data has no {data_type!r}.")
        return block[data_type]
    return block


def _scalar_values(payload):
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Scalar field payload is empty.")
    surface_id = sorted(payload, key=str)[0]
    return _as_floats(payload[surface_id])


def collect_boundary_names(setup):
    names = []
    for boundary_type in (
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
    ):
        try:
            group = getattr(setup.boundary_conditions, boundary_type)
        except Exception:
            continue
        names.extend(
            list_named_object_names(group, f"boundary_conditions.{boundary_type}")
        )
    return names


def fetch_face_table(solver, surface_name, field_names):
    """Per-face centroids, areas, and scalar values on one surface."""
    from ansys.fluent.core.field_data_interfaces import SurfaceDataType

    surface_data = solver.fields.field_data.get_surface_data(
        data_types=[
            SurfaceDataType.Vertices,
            SurfaceDataType.FacesConnectivity,
            SurfaceDataType.FacesCentroid,
        ],
        surfaces=[surface_name],
    )
    vertices = _as_vectors(_surface_block(surface_data, SurfaceDataType.Vertices))
    connectivity = _surface_block(surface_data, SurfaceDataType.FacesConnectivity)
    centroids = _as_vectors(
        _surface_block(surface_data, SurfaceDataType.FacesCentroid)
    )
    areas = face_areas(vertices, connectivity)
    if len(areas) != len(centroids):
        raise RuntimeError(
            f"{surface_name}: {len(areas)} faces and {len(centroids)} centroids."
        )
    columns = {}
    for field_name, boundary_value in field_names:
        scalar = solver.fields.field_data.get_scalar_field_data(
            field_name=field_name,
            surfaces=[surface_name],
            node_value=False,
            boundary_value=boundary_value,
        )
        values = _scalar_values(scalar)
        if len(values) != len(centroids):
            raise RuntimeError(
                f"{surface_name} field {field_name}: {len(values)} values "
                f"for {len(centroids)} faces."
            )
        columns[field_name] = values
    faces = []
    for index, centroid in enumerate(centroids):
        faces.append(
            {
                "x": centroid[0],
                "y": centroid[1],
                "z": centroid[2],
                "area": areas[index],
                "cm": columns[FIELD_UDM_CM][index],
                "concentration_nacl": columns[NACL_CELL_FIELD][index],
                "jw": columns[FIELD_UDM_JW][index],
                "y1": columns[FIELD_UDM_Y1][index],
            }
        )
    return faces


def _face_rows(surface, cell_number, faces, d_salt, distances, distance_note, quantiles):
    tagged = []
    for index, face in enumerate(faces):
        item = dict(face)
        item["_index"] = index
        tagged.append(item)
    selected = top_faces_by_cm(tagged)
    rows = []
    for rank, face in enumerate(selected, start=1):
        argument, exponential = film_terms(face["jw"], face["y1"], d_salt)
        distance = None if distances is None else distances[face["_index"]]
        rows.append(
            {
                "surface": surface,
                "cell": cell_number,
                "rank": rank,
                "x": face["x"],
                "y": face["y"],
                "z": face["z"],
                "area": face["area"],
                "cm": face["cm"],
                "concentration_nacl": face["concentration_nacl"],
                "jw": face["jw"],
                "y1": face["y1"],
                "jw_y1_over_d_salt": argument,
                "exp_jw_y1_over_d_salt": exponential,
                "spacer_distance_m": distance,
                "spacer_distance_note": distance_note,
                "cm_area_quantile_999": quantiles["cm"],
                "concentration_nacl_area_quantile_999": quantiles["concentration_nacl"],
            }
        )
    return rows


def _quantiles(faces):
    return {
        "cm": area_quantile(
            [face["cm"] for face in faces],
            [face["area"] for face in faces],
            CM_AREA_QUANTILE,
        ),
        "concentration_nacl": area_quantile(
            [face["concentration_nacl"] for face in faces],
            [face["area"] for face in faces],
            CM_AREA_QUANTILE,
        ),
    }


def _require_scalar_fields(solver):
    info = solver.fields.field_info.get_scalar_fields_info()
    names = set()

    def walk(value):
        if isinstance(value, str):
            names.add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    names.add(key)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(info)
    required = (
        FIELD_UDM_CM,
        FIELD_UDM_JW,
        FIELD_UDM_Y1,
        NACL_CELL_FIELD,
    )
    missing = [name for name in required if name not in names]
    if missing:
        raise RuntimeError(
            "Scalar fields missing from the session: " + ", ".join(missing) + "."
        )


def fetch_centroids(solver, surface_name):
    """Face-centroid coordinates on one surface."""
    from ansys.fluent.core.field_data_interfaces import SurfaceDataType

    surface_data = solver.fields.field_data.get_surface_data(
        data_types=[SurfaceDataType.FacesCentroid],
        surfaces=[surface_name],
    )
    return _as_vectors(_surface_block(surface_data, SurfaceDataType.FacesCentroid))


def diagnose_session(solver, setup, layout, window, domain_x_min_m, d_salt):
    """Per-wall, per evaluation-cell face table on the open solver session."""
    cell_numbers = evaluation_window_cells(layout, window)
    spans = cell_x_bounds(layout, cell_numbers, domain_x_min_m)
    boundary_names = collect_boundary_names(setup)
    walls = {}
    for base_name in MEMBRANE_WALLS:
        matched = zones_for_base_name(boundary_names, base_name)
        if not matched:
            raise RuntimeError(f"Membrane wall {base_name!r} was not found.")
        walls[base_name] = matched
    spacer_names = sorted(
        name for name in boundary_names if name.startswith("wall_spacer")
    )
    _require_scalar_fields(solver)
    spacer_xyz = []
    spacer_note = "nearest spacer-wall face centroid"
    try:
        if not spacer_names:
            raise RuntimeError("no wall_spacer zones")
        for spacer_name in spacer_names:
            spacer_xyz.extend(fetch_centroids(solver, spacer_name))
    except Exception as exc:
        spacer_xyz = []
        spacer_note = f"not_computable: {exc}"

    field_requests = (
        (FIELD_UDM_CM, True),
        (FIELD_UDM_JW, True),
        (FIELD_UDM_Y1, True),
        (NACL_CELL_FIELD, False),
    )
    cell_reports = []
    face_rows = []
    for cell_number in cell_numbers:
        x_min, x_max = spans[cell_number]
        per_wall = {}
        combined = []
        for base_name, zone_names in walls.items():
            clip_name = f"cp_hotspot_{base_name}_{cell_number}"
            create_x_range_iso_clip(
                solver,
                clip_name,
                zone_names,
                x_min,
                x_max,
            )
            try:
                faces = fetch_face_table(solver, clip_name, field_requests)
            finally:
                delete_iso_clip(solver, clip_name)
            distances, distance_note = nearest_spacer_distances(
                [[face["x"], face["y"], face["z"]] for face in faces],
                spacer_xyz,
            )
            if spacer_note.startswith("not_computable"):
                distance_note = spacer_note
                distances = [None] * len(faces)
            quantiles = _quantiles(faces)
            per_wall[base_name] = {
                "x_min_m": x_min,
                "x_max_m": x_max,
                "face_count": len(faces),
                "cm_area_quantile_999": quantiles["cm"],
                "concentration_nacl_area_quantile_999": quantiles[
                    "concentration_nacl"
                ],
                "spacer_distance_note": distance_note,
            }
            face_rows.extend(
                _face_rows(
                    base_name,
                    cell_number,
                    faces,
                    d_salt,
                    distances,
                    distance_note,
                    quantiles,
                )
            )
            combined.extend(faces)
        combined_quantiles = _quantiles(combined)
        combined_distances, combined_note = nearest_spacer_distances(
            [[face["x"], face["y"], face["z"]] for face in combined],
            spacer_xyz,
        )
        if spacer_note.startswith("not_computable"):
            combined_note = spacer_note
            combined_distances = [None] * len(combined)
        face_rows.extend(
            _face_rows(
                "combined",
                cell_number,
                combined,
                d_salt,
                combined_distances,
                combined_note,
                combined_quantiles,
            )
        )
        cell_reports.append(
            {
                "cell": cell_number,
                "x_min_m": x_min,
                "x_max_m": x_max,
                "walls": per_wall,
                "combined": {
                    "face_count": len(combined),
                    "cm_area_quantile_999": combined_quantiles["cm"],
                    "concentration_nacl_area_quantile_999": combined_quantiles[
                        "concentration_nacl"
                    ],
                    "spacer_distance_note": combined_note,
                },
            }
        )
    return {
        "evaluation_cells": cell_numbers,
        "cells": cell_reports,
        "spacer_distance_note": spacer_note,
    }, face_rows


def launch_and_diagnose(data_root, copied, identity, run_config):
    """Launch Fluent on the copied case and write the hotspot report."""
    import ansys.fluent.core as pyfluent

    run_leaf = copied["run_leaf"]
    case_file, data_file = final_case_data(
        run_leaf, identity["geo_id"], identity["run_id"]
    )
    assert_fluent_opens_copies(data_root, run_leaf, case_file, data_file)
    record, _payload = mfbo_common.call_with_data_root(
        data_root,
        lambda: layout_from_run_directory(run_leaf),
    )
    kwargs = production_post_launch_kwargs(run_leaf, run_config)
    pyfluent.config.check_health_timeout = run_config.fluent_health_timeout
    meshing = None
    solver = None
    try:
        meshing = pyfluent.launch_fluent(**kwargs)
        solver = meshing.switch_to_solver()
        meshing = None
        solver.settings.file.read_case_data(
            file_name=os.path.abspath(case_file).replace("\\", "/")
        )
        report, face_rows = diagnose_session(
            solver,
            solver.settings.setup,
            record.layout,
            record.evaluation_window,
            run_config.domain_x_min_m,
            run_config.mass_diffusivity,
        )
    finally:
        if meshing is not None:
            meshing.exit()
        if solver is not None:
            solver.exit()
    payload = {
        "cp_canon_max_path": CP_CANON_MAX_PATH,
        "d_salt_m2_s": run_config.mass_diffusivity,
        "geo_id": identity["geo_id"],
        "run_id": identity["run_id"],
        "mesh_id": identity["mesh_id"],
        "family": identity["family"],
        "copied_run_leaf": str(run_leaf),
        "copied_case_file": str(case_file),
        "fields": {
            "cm": FIELD_UDM_CM,
            "jw": FIELD_UDM_JW,
            "y1": FIELD_UDM_Y1,
            "concentration_nacl": NACL_CELL_FIELD,
            "concentration_location": "cell centre (boundary_value=False)",
        },
        **report,
    }
    directory = output_directory(data_root, identity["geo_id"], identity["run_id"])
    json_path, csv_path = write_hotspot_report(directory, payload, face_rows)
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    return json_path, csv_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a production run and its mesh, then report CP-max "
            "hotspot faces from the copy. Does not open C:/ro_data in Fluent."
        )
    )
    parser.add_argument(
        "--source-run",
        required=True,
        help="Production run leaf under C:/ro_data (read-only).",
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Destination data root. Refuses C:/ro_data.",
    )
    args = parser.parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    identity = parse_production_run_leaf(args.source_run)
    copied = copy_source_leaves(data_root, identity)
    run_config = load_run_config()
    launch_and_diagnose(data_root, copied, identity, run_config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
