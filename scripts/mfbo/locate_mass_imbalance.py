"""Where the steady mass imbalance sits in a run leaf.

No Fluent on import. A live session is read-only on a copy: the case is
read and never written, and the copy is under ``--data-root``. ``C:/ro_data``
is refused as a data root and as a copy destination. A production leaf is
only read so it can be copied.

The per-cell field is ``mass-imbalance``. That is the PyFluent scalar name
for Fluent's "Mass Imbalance" (Ansys Fluent User's Guide 2025 R1, section
42.3, Table 42.17 Residuals, tag ``seg``; section 36.16 says the
pressure-based solver stores this cell value by default). PyFluent maps
``MASS_IMBALANCE`` to ``mass-imbalance`` and ``CELL_VOLUME`` to
``cell-volume`` in ``ansys/fluent/core/variable_strategies/field.py``.
Cell centres are ``x-coordinate``, ``y-coordinate``, and ``z-coordinate``
with node values off. The session lists the scalar fields and raises if
``mass-imbalance`` is not among them.

``configs/run_config.py`` ``product_version`` is ``25.1.0``. Unit-cell
labels come from ``DomainLayout.spans`` with ``domain_x_min_m = 0.0``
(``configs/run_config.py``).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.domain_layout import DEFAULT_EXTENT_REL_TOL, layout_from_mesh_manifest
from ro.paths import FAMILY_RE, GEO_ID_RE, MESH_ID_RE, RUN_ID_RE
from ro.solver_common import sha256_file

MASS_IMBALANCE_FIELD = "mass-imbalance"
CELL_VOLUME_FIELD = "cell-volume"
COORDINATE_FIELDS = ("x-coordinate", "y-coordinate", "z-coordinate")
DOMAIN_X_MIN_M = 0.0
DEFAULT_TOP = 50
TOP_FRACTIONS = (0.001, 0.01)
DATA_ROOT = "C:/ro_data_mfbo/mass_imbalance"
SCRIPT = "scripts/mfbo/locate_mass_imbalance.py"
_PRODUCTION_ROOTS = ("c:/ro_data", "/mnt/c/ro_data")
_ROLE_STALLED = "stalled"
_ROLE_CONTROL = "control"

STUDY_LEAVES = (
    {
        "role": _ROLE_STALLED,
        "source": (
            "C:/ro_data_mfbo/meshstudy/runs/pillar/P_p100_h00/"
            "max045_min006_cpg5_bl12s4_peel2/u0p3_p6M"
        ),
    },
    {
        "role": _ROLE_STALLED,
        "source": (
            "C:/ro_data/runs/pillar/P_p100_h00/"
            "max085_min006_cpg5_bl4_peel2/u0p2_p6M"
        ),
    },
    {
        "role": _ROLE_STALLED,
        "source": (
            "C:/ro_data/runs/diamond/D0817_a30/"
            "max085_min006_cpg5_bl4_peel2/u0p3_p6M"
        ),
    },
    {
        "role": _ROLE_CONTROL,
        "source": (
            "C:/ro_data/runs/pillar/P_p100_h30/"
            "max085_min006_cpg5_bl4_peel2/u0p3_p6M"
        ),
    },
)

_TOP_FIELDS = (
    "rank",
    "abs_imbalance",
    "imbalance",
    "x_m",
    "y_m",
    "z_m",
    "volume_m3",
    "unit_cell",
)
_HISTOGRAM_FIELDS = (
    "unit_cell",
    "x_min_m",
    "x_max_m",
    "cell_count",
    "sum_abs_imbalance",
    "max_abs_imbalance",
)


def _folded(value) -> str:
    return str(value).replace("\\", "/").casefold().rstrip("/")


def under_production(value) -> bool:
    folded = _folded(value)
    return any(
        folded == root or folded.startswith(root + "/")
        for root in _PRODUCTION_ROOTS
    )


def _normalize(value):
    """Forward-slash path that keeps a Windows drive or a leading slash."""
    text = str(value).strip().replace("\\", "/")
    windows = len(text) >= 3 and text[1] == ":" and text[2] == "/"
    if windows:
        body = [part for part in text[2:].split("/") if part and part != "."]
        return text[:2] + "/" + "/".join(body)
    absolute = text.startswith("/")
    body = [part for part in text.split("/") if part and part != "."]
    joined = "/".join(body)
    if absolute:
        return "/" + joined
    return joined


def resolve_data_root(value):
    """Absolute data root that is not ``C:/ro_data`` or under it."""
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data root is required.")
    text = value.strip().replace("\\", "/")
    if under_production(text):
        raise ValueError(f"Refusing production data root: {value}")
    windows = len(text) >= 3 and text[1] == ":" and text[2] == "/"
    if windows:
        return text.rstrip("/")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"data root must be absolute, got {value!r}.")
    resolved = path.resolve().as_posix()
    if under_production(resolved):
        raise ValueError(f"Refusing production data root: {value}")
    return resolved


def parse_run_leaf(value):
    """Identity of ``.../runs/<family>/<geo_id>/<mesh_id>/<run_id>``."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"source run is required, got {value!r}.")
    text = _normalize(value)
    parts = [part for part in text.split("/") if part]
    if "runs" not in parts:
        raise ValueError(
            "source run must contain runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {value!r}."
        )
    index = len(parts) - 1 - parts[::-1].index("runs")
    tail = parts[index + 1 :]
    if len(tail) != 4:
        raise ValueError(
            "source run must end at runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {value!r}."
        )
    family, geo_id, mesh_id, run_id = tail
    if FAMILY_RE.fullmatch(family) is None:
        raise ValueError(f"family must match {FAMILY_RE.pattern}, got {family!r}.")
    if GEO_ID_RE.fullmatch(geo_id) is None:
        raise ValueError(f"geo_id must match {GEO_ID_RE.pattern}, got {geo_id!r}.")
    if MESH_ID_RE.fullmatch(mesh_id) is None:
        raise ValueError(f"mesh_id must match {MESH_ID_RE.pattern}, got {mesh_id!r}.")
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"run_id must match {RUN_ID_RE.pattern}, got {run_id!r}.")
    marker = "/runs/"
    position = text.rfind(marker)
    if position < 0:
        raise ValueError(
            "source run must contain runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {value!r}."
        )
    prefix = text[:position]
    run_leaf = f"{prefix}/runs/{family}/{geo_id}/{mesh_id}/{run_id}"
    mesh_leaf = f"{prefix}/meshes/{family}/{geo_id}/{mesh_id}"
    return {
        "root": prefix,
        "run_leaf": run_leaf,
        "mesh_leaf": mesh_leaf,
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
    }


def _relative_files(directory):
    root = Path(directory)
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _sha256_map(directory):
    root = Path(directory)
    return {
        relative: sha256_file(root / relative) for relative in _relative_files(root)
    }


def copy_or_reuse_tree(source, dest, *, label):
    """Copy a directory. Reuse it when every relative sha256 matches.

    An existing different tree is refused. Nothing under ``C:/ro_data``
    is written.
    """
    source = Path(source)
    dest = Path(dest)
    if under_production(dest):
        raise ValueError(f"Refusing to copy {label} into C:/ro_data: {dest}")
    if not source.is_dir():
        raise FileNotFoundError(f"{label} not found: {source}")
    source_hashes = _sha256_map(source)
    if not source_hashes:
        raise FileNotFoundError(f"{label} has no files: {source}")
    if dest.exists():
        if not dest.is_dir():
            raise FileExistsError(
                f"Refusing to overwrite {label} path that is not a directory: {dest}"
            )
        if _sha256_map(dest) != source_hashes:
            raise RuntimeError(
                f"{label} already exists and sha256s are not identical: {dest}."
            )
        return dest
    for relative in source_hashes:
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        if sha256_file(target) != source_hashes[relative]:
            raise RuntimeError(f"Copied {label} sha256 changed for {relative}.")
    if _sha256_map(source) != source_hashes:
        raise RuntimeError(f"{label} source changed during copy: {source}")
    return dest


def copy_file_or_reuse(source, dest, *, label):
    """Copy one file. Reuse it when the sha256 matches."""
    source = Path(source)
    dest = Path(dest)
    if under_production(dest):
        raise ValueError(f"Refusing to copy {label} into C:/ro_data: {dest}")
    if not source.is_file():
        raise FileNotFoundError(f"{label} not found: {source}")
    digest = sha256_file(source)
    if dest.exists():
        if not dest.is_file():
            raise FileExistsError(
                f"Refusing to overwrite {label} path that is not a file: {dest}"
            )
        if sha256_file(dest) != digest:
            raise RuntimeError(
                f"{label} already exists and sha256s are not identical: {dest}."
            )
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    if sha256_file(dest) != digest:
        raise RuntimeError(f"Copied {label} sha256 changed: {dest}")
    return dest


def destination_paths(data_root, identity):
    family = identity["family"]
    geo_id = identity["geo_id"]
    mesh_id = identity["mesh_id"]
    run_id = identity["run_id"]
    root = Path(data_root)
    run_leaf = root / "runs" / family / geo_id / mesh_id / run_id
    mesh_manifest = (
        root / "meshes" / family / geo_id / mesh_id / "manifest.json"
    )
    return run_leaf, mesh_manifest


def prepare_copy(source, data_root):
    """Copy the run leaf and the mesh manifest under ``data_root``.

    The mesh body is not copied. Layout is read from the copied manifest.
    Fluent is not started here.
    """
    root = resolve_data_root(data_root)
    identity = parse_run_leaf(source)
    run_dest, mesh_dest = destination_paths(root, identity)
    if under_production(run_dest) or under_production(mesh_dest):
        raise ValueError(f"Refusing to copy into C:/ro_data: {run_dest}")
    copy_or_reuse_tree(identity["run_leaf"], run_dest, label="run leaf")
    manifest_source = Path(identity["mesh_leaf"]) / "manifest.json"
    copy_file_or_reuse(manifest_source, mesh_dest, label="mesh manifest")
    case_file = run_dest / f"{identity['geo_id']}_{identity['run_id']}_final.cas.h5"
    data_file = run_dest / f"{identity['geo_id']}_{identity['run_id']}_final.dat.h5"
    if under_production(case_file) or under_production(data_file):
        raise ValueError(f"Refusing to open a case under C:/ro_data: {case_file}")
    if not case_file.is_file():
        raise FileNotFoundError(f"Copied final case not found: {case_file}")
    if not data_file.is_file():
        raise FileNotFoundError(f"Copied final data not found: {data_file}")
    return {
        "data_root": root,
        "identity": identity,
        "run_leaf": run_dest,
        "mesh_manifest": mesh_dest,
        "case_file": case_file,
        "data_file": data_file,
    }


def require_scalar_field(names, field_name):
    """Raise if ``field_name`` is not in the session's scalar-field list."""
    available = sorted({str(name) for name in names})
    if field_name not in available:
        raise RuntimeError(
            f"Fluent scalar fields do not include {field_name!r}. "
            f"Session fields: {available}."
        )
    return field_name


def unit_cell_label(x, spans, tolerance_m):
    """Label of the layout span that contains ``x``.

    The last span is closed on the right. ``x`` within ``tolerance_m`` of
    the domain is snapped onto the end span. Anything farther is
    ``outside``.
    """
    if not spans:
        raise ValueError("layout spans are empty.")
    if tolerance_m < 0.0:
        raise ValueError(f"tolerance must be >= 0, got {tolerance_m!r}.")
    position = float(x)
    first = float(spans[0][1])
    last = float(spans[-1][2])
    if position < first - tolerance_m or position > last + tolerance_m:
        return "outside"
    if position < first:
        position = first
    if position > last:
        position = last
    for index, (label, x_min, x_max) in enumerate(spans):
        closed = index == len(spans) - 1
        if float(x_min) <= position < float(x_max) or (
            closed and float(x_min) <= position <= float(x_max)
        ):
            return label
    return "outside"


def _finite(value, label):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    return number


def assemble_cells(imbalance, volume, x, y, z):
    """Zip one fluid zone's field arrays into cell records."""
    count = len(imbalance)
    if not (count == len(volume) == len(x) == len(y) == len(z)):
        raise ValueError(
            "Field lengths differ: "
            f"imbalance={len(imbalance)} volume={len(volume)} "
            f"x={len(x)} y={len(y)} z={len(z)}."
        )
    if count == 0:
        raise ValueError("Mass-imbalance field is empty.")
    cells = []
    for index in range(count):
        mass = _finite(imbalance[index], "imbalance")
        cell_volume = _finite(volume[index], "volume")
        if cell_volume <= 0.0:
            raise ValueError(f"cell volume must be > 0, got {cell_volume!r}.")
        cells.append(
            {
                "imbalance": mass,
                "abs_imbalance": abs(mass),
                "volume_m3": cell_volume,
                "x_m": _finite(x[index], "x"),
                "y_m": _finite(y[index], "y"),
                "z_m": _finite(z[index], "z"),
            }
        )
    return cells


def tag_unit_cells(cells, spans, tolerance_m):
    tagged = []
    for cell in cells:
        item = dict(cell)
        item["unit_cell"] = unit_cell_label(item["x_m"], spans, tolerance_m)
        tagged.append(item)
    return tagged


def top_cells(cells, count):
    """Cells with the largest ``|imbalance|``. Ties break on index."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"top N must be an integer >= 1, got {count!r}.")
    ordered = sorted(
        enumerate(cells),
        key=lambda item: (-item[1]["abs_imbalance"], item[0]),
    )
    rows = []
    for rank, (_index, cell) in enumerate(ordered[:count], start=1):
        row = dict(cell)
        row["rank"] = rank
        rows.append(row)
    return rows


def histogram_by_unit_cell(cells, spans):
    """Sum and max of ``|imbalance|`` on each layout span, then ``outside``."""
    bins = []
    for label, x_min, x_max in spans:
        bins.append(
            {
                "unit_cell": label,
                "x_min_m": float(x_min),
                "x_max_m": float(x_max),
                "cell_count": 0,
                "sum_abs_imbalance": 0.0,
                "max_abs_imbalance": 0.0,
            }
        )
    outside = {
        "unit_cell": "outside",
        "x_min_m": None,
        "x_max_m": None,
        "cell_count": 0,
        "sum_abs_imbalance": 0.0,
        "max_abs_imbalance": 0.0,
    }
    by_label = {item["unit_cell"]: item for item in bins}
    by_label["outside"] = outside
    for cell in cells:
        label = cell["unit_cell"]
        if label not in by_label:
            raise ValueError(f"Unknown unit cell {label!r}.")
        slot = by_label[label]
        slot["cell_count"] += 1
        slot["sum_abs_imbalance"] += cell["abs_imbalance"]
        if cell["abs_imbalance"] > slot["max_abs_imbalance"]:
            slot["max_abs_imbalance"] = cell["abs_imbalance"]
    if outside["cell_count"]:
        bins.append(outside)
    return bins


def concentration_fraction(cells, fraction):
    """Share of total ``|imbalance|`` held by the top ``fraction`` of cells.

    The count is ``max(1, ceil(fraction * n))``. A zero total returns 0.
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction!r}.")
    count = len(cells)
    if count == 0:
        raise ValueError("concentration needs at least one cell.")
    total = sum(cell["abs_imbalance"] for cell in cells)
    keep = max(1, math.ceil(fraction * count))
    ordered = sorted(
        (cell["abs_imbalance"] for cell in cells),
        reverse=True,
    )
    held = sum(ordered[:keep])
    return {
        "fraction_requested": fraction,
        "cell_count": keep,
        "sum_abs_imbalance": held,
        "total_abs_imbalance": total,
        "share": 0.0 if total == 0.0 else held / total,
    }


def analyze_cells(cells, spans, *, top_n, tolerance_m):
    tagged = tag_unit_cells(cells, spans, tolerance_m)
    fractions = [
        concentration_fraction(tagged, fraction) for fraction in TOP_FRACTIONS
    ]
    return {
        "cell_count": len(tagged),
        "total_abs_imbalance": sum(cell["abs_imbalance"] for cell in tagged),
        "top": top_cells(tagged, top_n),
        "histogram": histogram_by_unit_cell(tagged, spans),
        "fractions": fractions,
    }


def _format(value):
    if value is None:
        return ""
    return format(float(value), ".12g")


def render_markdown(meta, analysis):
    lines = [
        "# Mass imbalance location",
        "",
        f"Role: {meta['role']}",
        f"Source: `{meta['source']}`",
        f"Copy: `{meta['run_leaf']}`",
        f"Field: `{MASS_IMBALANCE_FIELD}` "
        "(Fluent 2025 R1 User's Guide Table 42.17, Mass Imbalance; "
        "PyFluent scalar name `mass-imbalance`).",
        f"Cell volume field: `{CELL_VOLUME_FIELD}`. "
        "Coordinates use node values off.",
        "",
        f"Cells: {analysis['cell_count']}",
        f"Total |imbalance|: {_format(analysis['total_abs_imbalance'])}",
        "",
        "Share of total |imbalance|:",
        "",
    ]
    for item in analysis["fractions"]:
        percent = item["fraction_requested"] * 100.0
        lines.append(
            f"- top {percent:g}% ({item['cell_count']} cells): "
            f"{_format(item['share'])}"
        )
    lines.extend(
        [
            "",
            "| unit cell | cells | sum |imbalance| | max |imbalance| |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in analysis["histogram"]:
        lines.append(
            f"| {row['unit_cell']} | {row['cell_count']} | "
            f"{_format(row['sum_abs_imbalance'])} | "
            f"{_format(row['max_abs_imbalance'])} |"
        )
    lines.extend(
        [
            "",
            f"Top {len(analysis['top'])} cells are in `top_cells.csv`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path, fieldnames, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: ""
                    if row.get(key) is None
                    else _format(row.get(key))
                    if isinstance(row.get(key), float)
                    else row.get(key)
                    for key in fieldnames
                }
            )


def write_analysis(directory, meta, analysis):
    """Write the markdown, both CSVs, and the summary JSON."""
    destination = Path(directory)
    if under_production(destination):
        raise ValueError(
            f"Refusing to write the mass-imbalance report under C:/ro_data: "
            f"{destination}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    markdown = destination / "mass_imbalance.md"
    top_path = destination / "top_cells.csv"
    histogram_path = destination / "histogram.csv"
    summary_path = destination / "summary.json"
    markdown.write_text(render_markdown(meta, analysis), encoding="utf-8")
    _write_csv(top_path, _TOP_FIELDS, analysis["top"])
    _write_csv(histogram_path, _HISTOGRAM_FIELDS, analysis["histogram"])
    payload = {
        "role": meta["role"],
        "source": meta["source"],
        "run_leaf": str(meta["run_leaf"]),
        "field": MASS_IMBALANCE_FIELD,
        "cell_volume_field": CELL_VOLUME_FIELD,
        "cell_count": analysis["cell_count"],
        "total_abs_imbalance": analysis["total_abs_imbalance"],
        "fractions": analysis["fractions"],
        "histogram": analysis["histogram"],
    }
    summary_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return markdown, top_path, histogram_path, summary_path


def study_jobs(data_root=DATA_ROOT, top_n=DEFAULT_TOP):
    """Queue jobs for the stalled leaves and the converged control."""
    root = resolve_data_root(data_root)
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError(f"top N must be an integer >= 1, got {top_n!r}.")
    jobs = []
    seen = set()
    for leaf in STUDY_LEAVES:
        identity = parse_run_leaf(leaf["source"])
        job_id = (
            f"{leaf['role']}-{identity['geo_id']}-"
            f"{identity['mesh_id']}-{identity['run_id']}"
        )
        if job_id in seen:
            raise ValueError(f"duplicate job id {job_id!r}.")
        seen.add(job_id)
        jobs.append(
            {
                "id": job_id,
                "argv": [
                    "{python}",
                    SCRIPT,
                    "--source",
                    identity["run_leaf"],
                    "--data-root",
                    root,
                    "--role",
                    leaf["role"],
                    "--top",
                    str(top_n),
                ],
                "env": {"RO_DATA_ROOT": root},
            }
        )
    return jobs


def write_queue(path, jobs):
    destination = Path(path)
    if under_production(destination):
        raise ValueError(f"Refusing to write a queue under C:/ro_data: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    return destination


def _scalar_values(payload):
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Scalar field payload is empty.")
    surface_id = sorted(payload, key=str)[0]
    return [float(value) for value in payload[surface_id]]


def _fluid_zone_names(setup):
    try:
        group = setup.cell_zone_conditions.fluid
    except Exception as exc:
        raise RuntimeError(f"Could not list fluid cell zones: {exc}") from exc
    try:
        names = group.get_object_names()
    except Exception as exc:
        raise RuntimeError(f"Could not list fluid cell zones: {exc}") from exc
    if not names:
        raise RuntimeError("The case has no fluid cell zone.")
    return [str(name) for name in names]


def _session_scalar_names(field_data):
    scalar_fields = getattr(field_data, "scalar_fields", None)
    if scalar_fields is not None and hasattr(scalar_fields, "allowed_values"):
        return [str(name) for name in scalar_fields.allowed_values()]
    info = getattr(field_data, "_field_info", None)
    getter = getattr(info, "_get_scalar_fields_info", None) if info else None
    if getter is None:
        raise RuntimeError(
            "This Fluent session does not list scalar fields, so "
            f"{MASS_IMBALANCE_FIELD!r} cannot be verified."
        )
    payload = getter()
    names = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, dict) and "name" in value:
                names.append(str(value["name"]))
            else:
                names.append(str(key))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, dict) and "name" in item:
                names.append(str(item["name"]))
            else:
                names.append(str(item))
    else:
        raise RuntimeError(
            f"Unrecognised scalar-field listing: {type(payload).__name__}."
        )
    return names


def fetch_cells(solver):
    """Per-cell imbalance, volume, and centroid from the open case.

    Reads field data only. Does not iterate and does not write the case.
    """
    names = _session_scalar_names(solver.fields.field_data)
    for field_name in (
        MASS_IMBALANCE_FIELD,
        CELL_VOLUME_FIELD,
        *COORDINATE_FIELDS,
    ):
        require_scalar_field(names, field_name)
    zones = _fluid_zone_names(solver.settings.setup)
    cells = []
    for zone in zones:
        columns = {}
        for field_name in (
            MASS_IMBALANCE_FIELD,
            CELL_VOLUME_FIELD,
            *COORDINATE_FIELDS,
        ):
            payload = solver.fields.field_data.get_scalar_field_data(
                field_name=field_name,
                surfaces=[zone],
                node_value=False,
                boundary_value=False,
            )
            columns[field_name] = _scalar_values(payload)
        cells.extend(
            assemble_cells(
                columns[MASS_IMBALANCE_FIELD],
                columns[CELL_VOLUME_FIELD],
                columns["x-coordinate"],
                columns["y-coordinate"],
                columns["z-coordinate"],
            )
        )
    return cells


def _launch_kwargs(cwd, run_config):
    if under_production(cwd):
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


def _load_run_config():
    import importlib.util

    from ro.paths import project_root

    path = project_root() / "configs" / "run_config.py"
    spec = importlib.util.spec_from_file_location(
        "locate_mass_imbalance_run_config",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load run config: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locate_copy(prepared, *, role, top_n):
    """Open the copied case, score the field, and write the report."""
    import ansys.fluent.core as pyfluent

    run_config = _load_run_config()
    if run_config.product_version.split(".")[0:2] != ["25", "1"]:
        raise RuntimeError(
            "mass-imbalance was checked for Fluent 25.1, "
            f"got product_version {run_config.product_version!r}."
        )
    layout = layout_from_mesh_manifest(Path(prepared["mesh_manifest"]).parent)
    spans = layout.layout.spans(DOMAIN_X_MIN_M)
    tolerance = layout.layout.total_length_m * DEFAULT_EXTENT_REL_TOL
    kwargs = _launch_kwargs(prepared["run_leaf"], run_config)
    pyfluent.config.check_health_timeout = run_config.fluent_health_timeout
    meshing = None
    solver = None
    try:
        meshing = pyfluent.launch_fluent(**kwargs)
        solver = meshing.switch_to_solver()
        meshing = None
        solver.settings.file.read_case_data(
            file_name=os.path.abspath(prepared["case_file"]).replace("\\", "/")
        )
        cells = fetch_cells(solver)
    finally:
        if meshing is not None:
            meshing.exit()
        if solver is not None:
            solver.exit()
    analysis = analyze_cells(
        cells,
        spans,
        top_n=top_n,
        tolerance_m=tolerance,
    )
    report_dir = (
        Path(prepared["data_root"])
        / "reports"
        / prepared["identity"]["geo_id"]
        / prepared["identity"]["mesh_id"]
        / prepared["identity"]["run_id"]
    )
    meta = {
        "role": role,
        "source": prepared["identity"]["run_leaf"],
        "run_leaf": prepared["run_leaf"],
    }
    paths = write_analysis(report_dir, meta, analysis)
    print(render_markdown(meta, analysis))
    for path in paths:
        print(f"Wrote {path}")
    return paths


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Locate the per-cell mass imbalance on a copied run leaf. "
            "Does not write into C:/ro_data."
        )
    )
    parser.add_argument(
        "--source",
        help="Run leaf. Production leaves are copied; they are not opened.",
    )
    parser.add_argument(
        "--data-root",
        help="Copy and report root. Refuses C:/ro_data. "
        f"The study queue uses {DATA_ROOT}.",
    )
    parser.add_argument(
        "--role",
        choices=(_ROLE_STALLED, _ROLE_CONTROL),
        default=_ROLE_STALLED,
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument(
        "--emit-queue",
        help="Write the study job queue and do not launch Fluent.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.emit_queue:
        path = write_queue(args.emit_queue, study_jobs(top_n=args.top))
        print(f"Wrote {path}")
        return 0
    if not args.source or not args.data_root:
        raise ValueError("--source and --data-root are required.")
    prepared = prepare_copy(args.source, args.data_root)
    locate_copy(prepared, role=args.role, top_n=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
