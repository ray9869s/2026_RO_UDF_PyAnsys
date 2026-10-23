"""Export membrane face arrays for the seminar maps.

For each leaf, copy the final case, final data, ``libudf``, and the UDF
source into ``C:/ro_data_mfbo/seminar_figs/work/<geo>/``. The leaf is only
read. Fluent is launched at 4 processes with no GUI, in meshing mode, then
switched to the solver (direct solver launch fails on this host). The copy
is read. There is no iteration and no case/data write.

Per-face arrays on ``wall_bottom_mem`` and ``wall_top_mem`` come from
``read_membrane_clip_faces``: centroid, polygon area, UDM_CM (``udm-7``,
``boundary_value=False``), and the adjacent-cell salt mass fraction
(``nacl``) converted with ``mass_fraction_to_molar_concentration``.
``c_b_window_mol_m3`` is copied from the leaf summary.

Free RAM is printed before every launch. Below 16 GB the launch is refused.
On failure only the PID captured for that session is terminated.

Importing this module does not launch Fluent.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import shutil
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ro.domain_layout import DomainLayout  # noqa: E402
from ro.fluent_report_helpers import (  # noqa: E402
    mass_fraction_to_molar_concentration,
)
from ro.solver_common import path_to_fluent_str  # noqa: E402
from seminar.plot_membrane_cm import (  # noqa: E402
    PRODUCTION_MESH_ID,
    SEMINAR_GEO_IDS,
    campaign_layout,
    two_cell_crop,
)

import numpy as np  # noqa: E402

FIG_DIR = Path("C:/ro_data_mfbo/seminar_figs")
WORK_ROOT = FIG_DIR / "work"
MESH_ID = PRODUCTION_MESH_ID
RUN_ID = "u0p2_p6M"
_LEAF_ROOT = "C:/ro_data_mfbo/campaign_reextract_v2/runs"
LEAF_PATHS = (
    f"{_LEAF_ROOT}/empty/REF_empty/{MESH_ID}/{RUN_ID}",
    f"{_LEAF_ROOT}/diamond/D2450_a45/{MESH_ID}/{RUN_ID}",
    f"{_LEAF_ROOT}/ml/M_c267/{MESH_ID}/{RUN_ID}",
    f"{_LEAF_ROOT}/pillar/P_p80_h15/{MESH_ID}/{RUN_ID}",
    f"{_LEAF_ROOT}/sin/S_a144_l3465/{MESH_ID}/{RUN_ID}",
)
WALLS = ("wall_bottom_mem", "wall_top_mem")
PROCESSOR_COUNT = 4
MIN_FREE_RAM_BYTES = 16 * 1024**3
# configs/post_config.py: the extract's molar conversion.
RHO_KG_PER_M3 = 998.2
SALT_MW_KG_PER_MOL = 0.05844
PRODUCT_VERSION = "25.1.0"
START_TIMEOUT_S = 600
CLIP_PAD_M = 1.0e-6


def available_ram_bytes():
    """Free physical RAM. Windows uses ``GlobalMemoryStatusEx``."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = (
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            )

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed.")
        return int(status.ullAvailPhys)
    text = Path("/proc/meminfo").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise OSError("MemAvailable is missing from /proc/meminfo.")


def require_free_ram(available_bytes=None):
    """Print free RAM and refuse to launch below 16 GB."""
    available = available_ram_bytes() if available_bytes is None else int(available_bytes)
    gib = available / 1024**3
    print(f"free RAM {gib:.2f} GiB", flush=True)
    if available < MIN_FREE_RAM_BYTES:
        raise RuntimeError(
            f"free RAM is {gib:.2f} GiB, below 16 GB. Not launching Fluent."
        )
    return available


def leaf_identity(leaf):
    """Family, geo, mesh, and run from the leaf path, checked against its manifest."""
    directory = Path(leaf)
    identity = {
        "run_id": directory.name,
        "mesh_id": directory.parent.name,
        "geo_id": directory.parent.parent.name,
        "family": directory.parent.parent.parent.name,
    }
    manifest_path = directory / "manifest.json"
    payload = {}
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{manifest_path} is not a JSON object.")
        for key, expected in identity.items():
            recorded = payload.get(key)
            if recorded is not None and recorded != expected:
                raise ValueError(
                    f"{manifest_path} {key}={recorded!r} does not match "
                    f"the directory {expected!r}."
                )
    udf_name = payload.get("udf_version")
    if not isinstance(udf_name, str) or not udf_name.strip():
        matches = sorted(path.name for path in directory.glob("*_RO_UDF.c"))
        if len(matches) != 1:
            raise FileNotFoundError(
                f"{directory} has no udf_version and {len(matches)} *_RO_UDF.c files."
            )
        udf_name = matches[0]
    identity["udf_version"] = udf_name
    if identity["geo_id"] not in SEMINAR_GEO_IDS:
        raise ValueError(f"unexpected seminar geo_id {identity['geo_id']!r}.")
    return identity


def leaf_artifacts(leaf, identity):
    directory = Path(leaf)
    cas = directory / f"{identity['geo_id']}_{identity['run_id']}_final.cas.h5"
    dat = directory / f"{identity['geo_id']}_{identity['run_id']}_final.dat.h5"
    libudf = directory / "libudf"
    udf = directory / identity["udf_version"]
    missing = [str(path) for path in (cas, dat, libudf, udf) if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "leaf is missing case, data, libudf, or UDF source: "
            + ", ".join(missing)
        )
    return cas, dat, libudf, udf


def _assert_scratch_outside_leaf(leaf, scratch):
    leaf_path = Path(leaf).resolve()
    scratch_path = Path(scratch).resolve()
    if (
        scratch_path == leaf_path
        or leaf_path in scratch_path.parents
        or scratch_path in leaf_path.parents
    ):
        raise ValueError(
            f"scratch {scratch_path} overlaps the leaf {leaf_path}."
        )
    return leaf_path, scratch_path


def copy_leaf_to_scratch(leaf, scratch):
    """Copy the four artifacts. Does not write inside the leaf."""
    leaf_path, scratch_path = _assert_scratch_outside_leaf(leaf, scratch)
    identity = leaf_identity(leaf_path)
    if scratch_path.name != identity["geo_id"]:
        raise ValueError(
            f"scratch directory must be named {identity['geo_id']}, "
            f"got {scratch_path.name!r}."
        )
    cas, dat, libudf, udf = leaf_artifacts(leaf_path, identity)
    before = {
        path: path.stat().st_mtime_ns
        for path in (cas, dat, udf)
    }
    if scratch_path.exists():
        shutil.rmtree(scratch_path)
    scratch_path.mkdir(parents=True)
    copied = []
    for source in (cas, dat, udf):
        destination = scratch_path / source.name
        shutil.copy2(source, destination)
        if destination.resolve() == source.resolve():
            raise RuntimeError(f"copy landed on the leaf file {source}.")
        copied.append(destination)
    shutil.copytree(libudf, scratch_path / "libudf")
    for path, mtime in before.items():
        if path.stat().st_mtime_ns != mtime:
            raise RuntimeError(f"leaf file was modified: {path}.")
    return identity, copied[0]


def read_c_b_window(leaf):
    """``c_b_window_mol_m3`` from the leaf summary. The leaf is not written."""
    path = Path(leaf) / "post" / "reports" / "summary_metrics_wide.csv"
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"{path} should have one data row, got {len(rows)}.")
    raw = (rows[0].get("c_b_window_mol_m3") or "").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{path} c_b_window_mol_m3 is not numeric: {raw!r}."
        ) from exc
    if not value > 0.0:
        raise ValueError(f"{path} c_b_window_mol_m3 must be positive, got {value!r}.")
    return value


def _summary_row(leaf):
    path = Path(leaf) / "post" / "reports" / "summary_metrics_wide.csv"
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        return None
    return rows[0]


def boundaries_from_summary(leaf, n_planes):
    """Extract unit-cell boundary x, when the wide summary has every plane."""
    row = _summary_row(leaf)
    if row is None:
        return None
    values = []
    for index in range(n_planes):
        raw = (row.get(f"pp_unit_cell_boundary_{index}_x_m") or "").strip()
        if not raw:
            return None
        try:
            values.append(float(raw))
        except ValueError:
            return None
    return values


def _layout_from_manifest_payload(payload):
    return DomainLayout(
        n_buffer_in=int(payload["n_buffer_in"]),
        n_active=int(payload["n_active_cells"]),
        n_buffer_out=int(payload["n_buffer_out"]),
        cell_length_x_m=float(payload["cell_length_x_m"]),
        buffer_length_in_m=float(payload["buffer_length_in_m"]),
        buffer_length_out_m=float(payload["buffer_length_out_m"]),
    )


def resolve_layout(leaf, identity):
    """Mesh layout from a sibling or production manifest, else the campaign pitch."""
    parts = Path(leaf).parts
    if "runs" in parts:
        index = parts.index("runs")
        sibling = (
            Path(*parts[:index])
            / "meshes"
            / identity["family"]
            / identity["geo_id"]
            / identity["mesh_id"]
            / "manifest.json"
        )
        if sibling.is_file():
            payload = json.loads(sibling.read_text(encoding="utf-8"))
            shift = payload.get("periodic_shift_y_m")
            if shift is None:
                _layout, shift = campaign_layout(identity["geo_id"])
            return _layout_from_manifest_payload(payload), float(shift)
    try:
        from ro.domain_layout import layout_from_mesh_manifest
        from ro.paths import mesh_dir

        directory = mesh_dir(
            identity["family"], identity["geo_id"], identity["mesh_id"]
        )
    except ValueError:
        directory = None
    if directory is not None and (directory / "manifest.json").is_file():
        record = layout_from_mesh_manifest(directory)
        payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        return record.layout, float(payload["periodic_shift_y_m"])
    return campaign_layout(identity["geo_id"])


def _session_pid(session):
    process = getattr(session, "_process", None)
    pid = getattr(process, "pid", None) if process is not None else None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def _kill_only(pid):
    from ro.session_retry import _kill_recorded_fluent_pid

    if pid is None:
        raise RuntimeError(
            "Fluent failed and its PID was not captured. "
            "Refusing to terminate any process by name."
        )
    print(_kill_recorded_fluent_pid(pid), flush=True)


def _load_extract():
    import ansys.fluent.core as pyfluent
    import pyfluent_report_extract as extract

    return pyfluent, extract


def _wall_zones(extract, setup, base_name):
    zones = extract.collect_boundary_zones(setup)
    names = [name for group in zones.values() for name in group]
    matched = extract.find_zones_by_base_name(names, base_name)
    if not matched:
        raise RuntimeError(
            f"{base_name} is not in the solver boundary zones: {names}."
        )
    return matched


def _export_wall(extract, solver, zones, x_min, x_max, clip_name):
    faces = extract.read_membrane_clip_faces(
        solver,
        zones,
        x_min,
        x_max,
        clip_name,
        extra_fields=("nacl",),
        include_centroids=True,
    )
    if not faces:
        raise RuntimeError(f"{clip_name} returned no faces.")
    for face in faces:
        for key in ("x", "y", "z", "area", "cm", "yi"):
            if key not in face:
                raise RuntimeError(f"{clip_name} face is missing {key}.")
    return {
        "x": np.asarray([face["x"] for face in faces], dtype=np.float64),
        "y": np.asarray([face["y"] for face in faces], dtype=np.float64),
        "z": np.asarray([face["z"] for face in faces], dtype=np.float64),
        "area": np.asarray([face["area"] for face in faces], dtype=np.float64),
        "udm_cm": np.asarray([face["cm"] for face in faces], dtype=np.float64),
        "salt_mol_m3": np.asarray(
            [
                mass_fraction_to_molar_concentration(
                    face["yi"],
                    RHO_KG_PER_M3,
                    SALT_MW_KG_PER_MOL,
                )
                for face in faces
            ],
            dtype=np.float64,
        ),
    }


def export_open_session(extract, solver, setup, leaf, identity, case_file):
    """Read the copy and return the npz payload. Does not iterate or write."""
    solver.settings.file.read_case_data(file_name=path_to_fluent_str(case_file))
    layout, shift = resolve_layout(leaf, identity)
    boundaries = boundaries_from_summary(leaf, layout.n_total + 1)
    crop = two_cell_crop(
        identity["geo_id"],
        layout=layout,
        periodic_shift_y_m=shift,
        mesh_id=identity["mesh_id"],
        boundaries=boundaries,
    )
    if boundaries is None:
        boundaries = layout.boundary_positions(0.0)
    x_min = float(boundaries[0]) - CLIP_PAD_M
    x_max = float(boundaries[-1]) + CLIP_PAD_M
    walls = {}
    for base_name in WALLS:
        zones = _wall_zones(extract, setup, base_name)
        prefix = "bottom" if base_name.endswith("bottom_mem") else "top"
        walls[prefix] = _export_wall(
            extract,
            solver,
            zones,
            x_min,
            x_max,
            f"seminar_cm_{prefix}",
        )
        print(
            f"{identity['geo_id']} {base_name}: {walls[prefix]['x'].size} faces",
            flush=True,
        )
    c_b = read_c_b_window(leaf)
    payload = {
        "geo_id": np.asarray(identity["geo_id"]),
        "family": np.asarray(identity["family"]),
        "mesh_id": np.asarray(identity["mesh_id"]),
        "run_id": np.asarray(identity["run_id"]),
        "c_b_window_mol_m3": np.asarray(c_b),
        "cell_length_x_m": np.asarray(crop["cell_length_x_m"]),
        "periodic_shift_y_m": np.asarray(crop["periodic_shift_y_m"]),
        "crop_cell_first": np.asarray(crop["cells"][0]),
        "crop_cell_second": np.asarray(crop["cells"][1]),
        "crop_x_min_m": np.asarray(crop["x_min_m"]),
        "crop_x_max_m": np.asarray(crop["x_max_m"]),
        "crop_y_min_m": np.asarray(crop["y_min_m"]),
        "crop_y_max_m": np.asarray(crop["y_max_m"]),
    }
    for prefix, columns in walls.items():
        for name, values in columns.items():
            payload[f"{prefix}_{name}"] = values
    return payload


def write_npz(payload, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **payload)
    return path


def export_leaf(leaf, work_root, fig_dir):
    """Copy, launch, export, and close one leaf."""
    identity = leaf_identity(leaf)
    scratch = Path(work_root) / identity["geo_id"]
    _identity, case_file = copy_leaf_to_scratch(leaf, scratch)
    require_free_ram()
    pyfluent, extract = _load_extract()
    pyfluent.config.check_health_timeout = START_TIMEOUT_S
    session = None
    pid = None
    try:
        print(
            f"launching Fluent for {identity['geo_id']} "
            f"processor_count={PROCESSOR_COUNT} ui_mode=no_gui",
            flush=True,
        )
        session = pyfluent.launch_fluent(
            product_version=PRODUCT_VERSION,
            mode="meshing",
            dimension=3,
            precision="double",
            processor_count=PROCESSOR_COUNT,
            ui_mode="no_gui",
            start_timeout=START_TIMEOUT_S,
            cwd=path_to_fluent_str(scratch),
        )
        from ro.session_retry import print_fluent_launch_identity

        print_fluent_launch_identity(session)
        pid = _session_pid(session)
        solver = session.switch_to_solver()
        session = solver
        solver_pid = _session_pid(solver)
        if solver_pid is not None:
            pid = solver_pid
        payload = export_open_session(
            extract,
            solver,
            solver.settings.setup,
            leaf,
            identity,
            case_file,
        )
        destination = (
            Path(fig_dir)
            / f"{identity['geo_id']}_{identity['run_id']}_membrane_cm.npz"
        )
        written = write_npz(payload, destination)
        solver.exit()
        session = None
        print(f"wrote {written}", flush=True)
        return written
    except Exception:
        if session is not None:
            try:
                session.exit()
            except Exception as exc:
                print(
                    f"Fluent exit failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
        if pid is None:
            print(
                "Fluent PID was not captured. Not terminating any process.",
                flush=True,
            )
        else:
            _kill_only(pid)
        raise


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Export seminar membrane face arrays with a 4-process Fluent "
            "session. Does not modify the source leaves."
        )
    )
    parser.add_argument(
        "--leaf",
        action="append",
        default=None,
        help="Leaf directory. Repeatable. Default is the five u0p2 leaves.",
    )
    parser.add_argument("--work-root", default=str(WORK_ROOT))
    parser.add_argument("--fig-dir", default=str(FIG_DIR))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    leaves = LEAF_PATHS if not args.leaf else tuple(args.leaf)
    written = []
    for leaf in leaves:
        written.append(export_leaf(leaf, args.work_root, args.fig_dir))
    print(f"exported {len(written)} leaves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
