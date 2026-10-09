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
from ro.cp_concentration_stats import B_PERM_M_PER_S
from ro.cp_metrics import CP_SPREAD_AREA_QUANTILE_HI
from ro.domain_layout import (
    CURRENT_EVALUATION_WINDOW,
    layout_from_run_directory,
)
from ro.fluent_report_helpers import (
    create_channel_midplane_plane,
    create_x_range_iso_clip,
    delete_iso_clip,
    evaluation_window_midplane_bulk_concentrations,
    list_named_object_names,
    scoring_geometry_from_layout,
)
from ro.paths import RUN_ID_RE, project_root
from ro.udm_layout import FIELD_UDM_CM, FIELD_UDM_CP, FIELD_UDM_JW, FIELD_UDM_Y1

MEMBRANE_WALLS = ("wall_top_mem", "wall_bottom_mem")
NACL_CELL_FIELD = "concentration-nacl"
TOP_FACE_COUNT = 20
CM_AREA_QUANTILE = CP_SPREAD_AREA_QUANTILE_HI
# B_perm is parsed from udfs/260822_RO_UDF.c:194 (static real, not a #define).
# udfs/260822_RO_UDF.c:48 is ``#define C_INLET_REF 597.8268309``.
C_INLET_REF_MOL_PER_M3 = 597.8268309
NEAR_DENOMINATOR_FRACTION = 0.1
ROBUST_CM_QUANTILES = (0.99, 0.999)
# Same salt-field order as scripts/pyfluent_report_extract.py cell 8.4.
MIDPLANE_SALT_FIELDS = (
    "nacl",
    "mass-fraction-of-nacl",
    "yi-0",
    "species-0",
    "udm-7",
)
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


def require_existing_copies(data_root, identity):
    """Locate the already-copied run and mesh. Does not read ``C:/ro_data``."""
    run_leaf, mesh_leaf = destination_leaves(data_root, identity)
    if not run_leaf.is_dir():
        raise FileNotFoundError(
            "Copied run leaf was not found. --cp-definition-check does not "
            f"copy from C:/ro_data: {run_leaf}"
        )
    if not mesh_leaf.is_dir():
        raise FileNotFoundError(
            "Copied mesh leaf was not found. --cp-definition-check does not "
            f"copy from C:/ro_data: {mesh_leaf}"
        )
    case_file, data_file = final_case_data(
        run_leaf, identity["geo_id"], identity["run_id"]
    )
    assert_fluent_opens_copies(data_root, run_leaf, case_file, data_file)
    return {"run_leaf": run_leaf, "mesh_leaf": mesh_leaf}


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


def load_post_config():
    path = project_root() / "configs" / "post_config.py"
    spec = importlib.util.spec_from_file_location(
        "diagnose_cp_post_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load post config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    pairs = []
    for value, area in zip(values, areas):
        if value is None or area is None:
            continue
        if not math.isfinite(float(value)) or not math.isfinite(float(area)):
            continue
        if float(area) <= 0.0:
            continue
        pairs.append((float(value), float(area)))
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


def top_faces_by_udm9(faces, count=TOP_FACE_COUNT):
    """Highest stored ``udm-9`` faces, not highest ``cm``."""
    ranked = sorted(
        faces,
        key=lambda face: (
            float("-inf") if face.get("udm9") is None else float(face["udm9"])
        ),
        reverse=True,
    )
    return ranked[: int(count)]


def cp_perm_mol_per_m3(cm, jw, b_perm=B_PERM_M_PER_S):
    """``B*cm/(Jw+B)``. None when the flux denominator is zero or non-finite."""
    if cm is None or jw is None:
        return None
    if not math.isfinite(float(cm)) or not math.isfinite(float(jw)):
        return None
    if not math.isfinite(float(b_perm)) or float(b_perm) == 0.0:
        raise ValueError(f"B_perm must be finite and non-zero, got {b_perm!r}.")
    denominator = float(jw) + float(b_perm)
    if denominator == 0.0 or not math.isfinite(denominator):
        return None
    return float(b_perm) * float(cm) / denominator


def _positive_area_average(values, areas):
    total = 0.0
    weight = 0.0
    for value, area in zip(values, areas):
        if value is None or area is None:
            continue
        if not math.isfinite(float(value)) or not math.isfinite(float(area)):
            continue
        if float(area) <= 0.0:
            continue
        total += float(value) * float(area)
        weight += float(area)
    if weight <= 0.0:
        return None
    return total / weight


def _facet_max(values):
    finite = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    if not finite:
        return None
    return max(finite)


def annotate_cp_definition_faces(
    faces,
    *,
    b_perm=B_PERM_M_PER_S,
    c_inlet_ref=C_INLET_REF_MOL_PER_M3,
    near_fraction=NEAR_DENOMINATOR_FRACTION,
):
    """Add cp_perm, denominator, and the two exclusion flags to each face."""
    threshold = float(near_fraction) * float(c_inlet_ref)
    annotated = []
    for index, face in enumerate(faces):
        perm = cp_perm_mol_per_m3(face.get("cm"), face.get("jw"), b_perm)
        if perm is None:
            denominator = None
            near = True
        else:
            denominator = float(c_inlet_ref) - perm
            near = abs(denominator) < threshold
        udm9 = face.get("udm9")
        negative = (
            udm9 is not None
            and math.isfinite(float(udm9))
            and float(udm9) < 0.0
        )
        jw = face.get("jw")
        jw_over_b = None
        if jw is not None and math.isfinite(float(jw)):
            jw_over_b = float(jw) / float(b_perm)
        item = dict(face)
        item["_index"] = index
        item.update(
            {
                "cp_perm": perm,
                "denominator": denominator,
                "jw_over_b_perm": jw_over_b,
                "near_singular": near,
                "negative_udm9": negative,
                "excluded": bool(near or negative),
            }
        )
        annotated.append(item)
    return annotated


def _count_and_area(faces, key):
    selected = [face for face in faces if face.get(key)]
    area = 0.0
    for face in selected:
        face_area = face.get("area")
        if face_area is not None and math.isfinite(float(face_area)):
            area += float(face_area)
    return len(selected), area


def robust_cp_from_quantile(quantile_cm, cp_ref, c_b):
    """``(q(cm) - cp_ref) / (c_b - cp_ref)``. None when the denominator is 0."""
    if (
        quantile_cm is None
        or cp_ref is None
        or c_b is None
        or not math.isfinite(float(quantile_cm))
        or not math.isfinite(float(cp_ref))
        or not math.isfinite(float(c_b))
    ):
        return None
    denominator = float(c_b) - float(cp_ref)
    if denominator == 0.0 or not math.isfinite(denominator):
        return None
    return (float(quantile_cm) - float(cp_ref)) / denominator


def cp_definition_metrics(faces, c_b, *, b_perm=B_PERM_M_PER_S, c_inlet_ref=C_INLET_REF_MOL_PER_M3):
    """Current udm-9 reductions, exclusion, and robust quantile candidates.

    ``c_b`` is the mid-plane bulk for this evaluation cell from
    ``evaluation_window_midplane_bulk_concentrations``. Area average uses
    positive-area faces. Facet max includes non-positive area, matching
    surface-facetmax. Exclusion is the union of near-zero
    ``C_INLET_REF - cp_perm`` and ``udm-9 < 0``.
    """
    annotated = annotate_cp_definition_faces(
        faces,
        b_perm=b_perm,
        c_inlet_ref=c_inlet_ref,
    )
    udm9_values = [face.get("udm9") for face in annotated]
    areas = [face.get("area") for face in annotated]
    udm9_avg = _positive_area_average(udm9_values, areas)
    udm9_max = _facet_max(udm9_values)
    near_count, near_area = _count_and_area(annotated, "near_singular")
    negative_count, negative_area = _count_and_area(annotated, "negative_udm9")
    kept = [face for face in annotated if not face["excluded"]]
    udm9_avg_excluding = _positive_area_average(
        [face.get("udm9") for face in kept],
        [face.get("area") for face in kept],
    )
    if udm9_avg is None or udm9_avg_excluding is None:
        change = None
    else:
        change = udm9_avg_excluding - udm9_avg
    cp_ref = _positive_area_average(
        [face.get("cp_perm") for face in annotated],
        areas,
    )
    cm_q99 = area_quantile(
        [face.get("cm") for face in annotated],
        areas,
        ROBUST_CM_QUANTILES[0],
    )
    cm_q999 = area_quantile(
        [face.get("cm") for face in annotated],
        areas,
        ROBUST_CM_QUANTILES[1],
    )
    return {
        "face_count": len(annotated),
        "udm9_area_avg": udm9_avg,
        "udm9_facet_max": udm9_max,
        "near_singular_count": near_count,
        "near_singular_area": near_area,
        "negative_udm9_count": negative_count,
        "negative_udm9_area": negative_area,
        "excluded_count": sum(1 for face in annotated if face["excluded"]),
        "udm9_area_avg_excluding": udm9_avg_excluding,
        "udm9_area_avg_change": change,
        "cm_area_quantile_99": cm_q99,
        "cm_area_quantile_999": cm_q999,
        "cp_ref_area_avg": cp_ref,
        "c_b_midplane_mol_m3": None if c_b is None else float(c_b),
        "robust_q99": robust_cp_from_quantile(cm_q99, cp_ref, c_b),
        "robust_q999": robust_cp_from_quantile(cm_q999, cp_ref, c_b),
        "annotated_faces": annotated,
    }


def format_definition_summary(rows):
    """One compact stdout line per surface and cell."""

    def _num(value):
        if value is None:
            return "null"
        return f"{float(value):.6g}"

    lines = []
    for row in rows:
        lines.append(
            "cell {cell} {surface}: udm9_avg={avg} udm9_max={max} "
            "near_n={near_n} near_area={near_area} neg_n={neg_n} "
            "avg_ex={avg_ex} delta={delta} robust99={r99} robust999={r999} "
            "c_b={cb}".format(
                cell=row["cell"],
                surface=row["surface"],
                avg=_num(row.get("udm9_area_avg")),
                max=_num(row.get("udm9_facet_max")),
                near_n=row.get("near_singular_count"),
                near_area=_num(row.get("near_singular_area")),
                neg_n=row.get("negative_udm9_count"),
                avg_ex=_num(row.get("udm9_area_avg_excluding")),
                delta=_num(row.get("udm9_area_avg_change")),
                r99=_num(row.get("robust_q99")),
                r999=_num(row.get("robust_q999")),
                cb=_num(row.get("c_b_midplane_mol_m3")),
            )
        )
    return "\n".join(lines)


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
    field_keys = {
        FIELD_UDM_CM: "cm",
        FIELD_UDM_JW: "jw",
        FIELD_UDM_Y1: "y1",
        FIELD_UDM_CP: "udm9",
        NACL_CELL_FIELD: "concentration_nacl",
    }
    faces = []
    for index, centroid in enumerate(centroids):
        face = {
            "x": centroid[0],
            "y": centroid[1],
            "z": centroid[2],
            "area": areas[index],
        }
        for field_name, column in columns.items():
            face[field_keys[field_name]] = column[index]
        faces.append(face)
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


def _require_scalar_fields(solver, required=None):
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
    if required is None:
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


DEFINITION_SUMMARY_FIELDS = (
    "surface",
    "cell",
    "face_count",
    "udm9_area_avg",
    "udm9_facet_max",
    "near_singular_count",
    "near_singular_area",
    "negative_udm9_count",
    "negative_udm9_area",
    "excluded_count",
    "udm9_area_avg_excluding",
    "udm9_area_avg_change",
    "cm_area_quantile_99",
    "cm_area_quantile_999",
    "cp_ref_area_avg",
    "c_b_midplane_mol_m3",
    "robust_q99",
    "robust_q999",
)
DEFINITION_FACE_FIELDS = (
    "surface",
    "cell",
    "rank",
    "x",
    "y",
    "z",
    "area",
    "udm9",
    "cm",
    "jw",
    "jw_over_b_perm",
    "cp_perm",
    "denominator",
    "spacer_distance_m",
    "spacer_distance_note",
)


def collect_fluid_zone_names(setup):
    try:
        group = setup.cell_zone_conditions.fluid
    except Exception:
        return []
    return list_named_object_names(group, "cell_zone_conditions.fluid")


def midplane_bulk_for_window(
    solver,
    solution,
    setup,
    layout,
    window,
    domain_x_min_m,
    post_config,
):
    """Per-cell and window mid-plane c_b via the extraction function."""
    cell_numbers = evaluation_window_cells(layout, window)
    geometry = scoring_geometry_from_layout(layout, domain_x_min_m)
    plane_name, z_mid, diag = create_channel_midplane_plane(
        solver,
        solution=solution,
        setup=setup,
        fluid_zone_names=collect_fluid_zone_names(setup),
        membrane_wall_names=list(MEMBRANE_WALLS),
    )
    last_error = None
    for salt_field in MIDPLANE_SALT_FIELDS:
        try:
            by_cell, areas, c_b_window = (
                evaluation_window_midplane_bulk_concentrations(
                    solver=solver,
                    solution=solution,
                    midplane_surface_names=[plane_name],
                    unit_cell_boundary_x_m=geometry.unit_cell_boundary_x_m,
                    evaluation_cell_numbers=cell_numbers,
                    salt_field=salt_field,
                    density_kg_per_m3=post_config.rho,
                    molecular_weight_kg_per_mol=(
                        post_config.salt_molecular_weight_kg_per_mol
                    ),
                    salt_is_mass_fraction=True,
                )
            )
        except Exception as exc:
            last_error = exc
            continue
        return {
            "c_b_by_cell": by_cell,
            "midplane_area_by_cell": areas,
            "c_b_window_mol_m3": c_b_window,
            "salt_field": salt_field,
            "plane_name": plane_name,
            "z_mid_m": z_mid,
            "plane_diag": diag,
        }
    raise RuntimeError(
        "Mid-plane c_b failed for every salt field "
        f"{MIDPLANE_SALT_FIELDS}: {last_error}"
    ) from last_error


def _definition_face_rows(surface, cell_number, metrics, distances, distance_note):
    annotated = metrics["annotated_faces"]
    rows = []
    for rank, face in enumerate(top_faces_by_udm9(annotated), start=1):
        distance = None
        if distances is not None:
            distance = distances[face["_index"]]
        rows.append(
            {
                "surface": surface,
                "cell": cell_number,
                "rank": rank,
                "x": face.get("x"),
                "y": face.get("y"),
                "z": face.get("z"),
                "area": face.get("area"),
                "udm9": face.get("udm9"),
                "cm": face.get("cm"),
                "jw": face.get("jw"),
                "jw_over_b_perm": face.get("jw_over_b_perm"),
                "cp_perm": face.get("cp_perm"),
                "denominator": face.get("denominator"),
                "spacer_distance_m": distance,
                "spacer_distance_note": distance_note,
            }
        )
    return rows


def _summary_from_metrics(surface, cell_number, metrics):
    row = {
        key: metrics.get(key)
        for key in DEFINITION_SUMMARY_FIELDS
        if key not in ("surface", "cell")
    }
    row["surface"] = surface
    row["cell"] = cell_number
    return row


def definition_check_session(solver, setup, layout, window, domain_x_min_m, post_config):
    """udm-9 definition check on the evaluation-window membrane clips."""
    cell_numbers = evaluation_window_cells(layout, window)
    spans = cell_x_bounds(layout, cell_numbers, domain_x_min_m)
    bulk = midplane_bulk_for_window(
        solver,
        solver.settings.solution,
        setup,
        layout,
        window,
        domain_x_min_m,
        post_config,
    )
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
    _require_scalar_fields(
        solver,
        (FIELD_UDM_CM, FIELD_UDM_JW, FIELD_UDM_CP),
    )
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
        (FIELD_UDM_CP, True),
    )
    summary_rows = []
    face_rows = []
    cell_reports = []
    for cell_number in cell_numbers:
        if cell_number not in bulk["c_b_by_cell"]:
            raise RuntimeError(
                f"Mid-plane c_b missing for evaluation cell {cell_number}."
            )
        c_b = bulk["c_b_by_cell"][cell_number]
        x_min, x_max = spans[cell_number]
        per_wall = {}
        combined = []
        for base_name, zone_names in walls.items():
            clip_name = f"cp_def_{base_name}_{cell_number}"
            create_x_range_iso_clip(
                solver, clip_name, zone_names, x_min, x_max
            )
            try:
                faces = fetch_face_table(solver, clip_name, field_requests)
            finally:
                delete_iso_clip(solver, clip_name)
            metrics = cp_definition_metrics(faces, c_b)
            distances, distance_note = nearest_spacer_distances(
                [[face["x"], face["y"], face["z"]] for face in faces],
                spacer_xyz,
            )
            if spacer_note.startswith("not_computable"):
                distance_note = spacer_note
                distances = None
            summary = _summary_from_metrics(base_name, cell_number, metrics)
            summary_rows.append(summary)
            face_rows.extend(
                _definition_face_rows(
                    base_name, cell_number, metrics, distances, distance_note
                )
            )
            per_wall[base_name] = summary
            combined.extend(faces)
        combined_metrics = cp_definition_metrics(combined, c_b)
        combined_distances, combined_note = nearest_spacer_distances(
            [[face["x"], face["y"], face["z"]] for face in combined],
            spacer_xyz,
        )
        if spacer_note.startswith("not_computable"):
            combined_note = spacer_note
            combined_distances = None
        combined_summary = _summary_from_metrics(
            "combined", cell_number, combined_metrics
        )
        summary_rows.append(combined_summary)
        face_rows.extend(
            _definition_face_rows(
                "combined",
                cell_number,
                combined_metrics,
                combined_distances,
                combined_note,
            )
        )
        cell_reports.append(
            {
                "cell": cell_number,
                "x_min_m": x_min,
                "x_max_m": x_max,
                "c_b_midplane_mol_m3": c_b,
                "walls": per_wall,
                "combined": combined_summary,
            }
        )
    return {
        "evaluation_cells": cell_numbers,
        "c_b_window_mol_m3": bulk["c_b_window_mol_m3"],
        "midplane_salt_field": bulk["salt_field"],
        "midplane_z_m": bulk["z_mid_m"],
        "b_perm_m_per_s": B_PERM_M_PER_S,
        "c_inlet_ref_mol_per_m3": C_INLET_REF_MOL_PER_M3,
        "near_denominator_fraction": NEAR_DENOMINATOR_FRACTION,
        "exclusion": (
            "union of |C_INLET_REF - cp_perm| < 0.1*C_INLET_REF and udm-9 < 0"
        ),
        "c_b_source": (
            "evaluation_window_midplane_bulk_concentrations; "
            "robust candidates use the per-cell mid-plane c_b"
        ),
        "cells": cell_reports,
        "spacer_distance_note": spacer_note,
    }, summary_rows, face_rows


def write_definition_report(directory, payload, summary_rows, face_rows):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "cp_definition_check.json"
    summary_path = directory / "cp_definition_check.csv"
    faces_path = directory / "cp_definition_check_faces.csv"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(summary_path, DEFINITION_SUMMARY_FIELDS, summary_rows)
    _write_csv(faces_path, DEFINITION_FACE_FIELDS, face_rows)
    return json_path, summary_path, faces_path


def _write_csv(path, fieldnames, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: _csv_cell(row.get(key)) for key in fieldnames}
            )


def _open_copied_solver(data_root, copied, identity, run_config):
    """Launch Fluent on the copied case. Caller must exit the solver."""
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
    except Exception:
        if meshing is not None:
            meshing.exit()
        if solver is not None:
            solver.exit()
        raise
    return solver, record, case_file


def launch_and_diagnose(data_root, copied, identity, run_config):
    """Launch Fluent on the copied case and write the hotspot report."""
    solver = None
    try:
        solver, record, case_file = _open_copied_solver(
            data_root, copied, identity, run_config
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
        if solver is not None:
            solver.exit()
    payload = {
        "cp_canon_max_path": CP_CANON_MAX_PATH,
        "d_salt_m2_s": run_config.mass_diffusivity,
        "geo_id": identity["geo_id"],
        "run_id": identity["run_id"],
        "mesh_id": identity["mesh_id"],
        "family": identity["family"],
        "copied_run_leaf": str(copied["run_leaf"]),
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


def launch_definition_check(data_root, copied, identity, run_config, post_config):
    """Definition check on the already-copied leaf. Does not copy."""
    solver = None
    try:
        solver, record, case_file = _open_copied_solver(
            data_root, copied, identity, run_config
        )
        report, summary_rows, face_rows = definition_check_session(
            solver,
            solver.settings.setup,
            record.layout,
            record.evaluation_window,
            run_config.domain_x_min_m,
            post_config,
        )
    finally:
        if solver is not None:
            solver.exit()
    payload = {
        "geo_id": identity["geo_id"],
        "run_id": identity["run_id"],
        "mesh_id": identity["mesh_id"],
        "family": identity["family"],
        "copied_run_leaf": str(copied["run_leaf"]),
        "copied_case_file": str(case_file),
        "top_faces": face_rows,
        "udf": {
            "b_perm_m_per_s": B_PERM_M_PER_S,
            "b_perm_source": "udfs/260822_RO_UDF.c:194",
            "c_inlet_ref_mol_per_m3": C_INLET_REF_MOL_PER_M3,
            "c_inlet_ref_source": "udfs/260822_RO_UDF.c:48",
            "udm9": "(cm - cp_perm) / (C_INLET_REF - cp_perm)",
            "cp_perm": "B_perm * cm / (Jw + B_perm)",
        },
        **report,
    }
    directory = output_directory(data_root, identity["geo_id"], identity["run_id"])
    json_path, summary_path, faces_path = write_definition_report(
        directory, payload, summary_rows, face_rows
    )
    print(format_definition_summary(summary_rows))
    print(
        "c_b_window_mol_m3="
        f"{report['c_b_window_mol_m3']} salt_field={report['midplane_salt_field']}"
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {faces_path}")
    return json_path, summary_path, faces_path


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
    parser.add_argument(
        "--cp-definition-check",
        action="store_true",
        help=(
            "Run the udm-9 definition check on the already-copied leaf. "
            "Does not read or copy C:/ro_data."
        ),
    )
    args = parser.parse_args(argv)
    data_root = resolve_data_root(args.data_root)
    identity = parse_production_run_leaf(args.source_run)
    run_config = load_run_config()
    if args.cp_definition_check:
        copied = require_existing_copies(data_root, identity)
        launch_definition_check(
            data_root,
            copied,
            identity,
            run_config,
            load_post_config(),
        )
    else:
        copied = copy_source_leaves(data_root, identity)
        launch_and_diagnose(data_root, copied, identity, run_config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
