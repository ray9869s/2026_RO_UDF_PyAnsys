"""D2450_a45 membrane first-height queue at m_max 0.085 mm and BL 4.

Five leaves, u0p2_p6M, on ``C:/ro_data_mfbo/meshstudy`` with geometry from
``C:/ro_data_mfbo/geometries_pmdb``. Each leaf requests a wall-to-centroid
y1. The control is 5.7 um (``_y1h057``), next to the realised smooth-transition
mean of 5.74 um at this ``m_max``, so a later comparison isolates the
last-ratio switch and its layer count. The other requests are 3.0, 2.0,
1.4, and 1.0 um (``_y1h030``, ``_y1h020``, ``_y1h014``, ``_y1h010``).
The membrane boundary layer is last-ratio at that first height. The spacer
boundary layer stays the production smooth-transition stack.

``--mesh-only`` writes jobs that mesh and print the cell count. Without
it, the queue is mesh, solve, and extract, and each job passes
``--reuse-mesh`` so a completed mesh-only leaf is reused. ``--max-cells N``
drops variants whose mesh-only manifest cell count is greater than N.

Importing this module does not launch Fluent. ``--emit-queue`` writes a
job-queue JSON file and does not mesh.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mfbo.mesh_study_case as mesh_study  # noqa: E402
from ro.paths import project_root  # noqa: E402

DATA_ROOT = "C:/ro_data_mfbo/meshstudy"
GEOMETRY_ROOT = "C:/ro_data_mfbo/geometries_pmdb"
GEO_ID = "D2450_a45"
CASE_ID = "u0p2_p6M"
M_MAX = 0.085
BL_LAYERS = 4
# 5.7 um is the control: today's area-weighted y1 at m_max 0.085 is 5.74 um.
Y1_UM = (5.7, 3.0, 2.0, 1.4, 1.0)


def y1_variants():
    """Name and override dict for each requested y1. Names are job-id fragments."""
    return tuple(
        (f"y1h{int(round(y1_um * 10)):03d}", {"membrane_bl_y1_um": y1_um})
        for y1_um in Y1_UM
    )


def y1_study_overrides():
    """Production template plus m_max 0.085, BL 4, and one y1 request."""
    _case, template, _retries = mesh_study.load_mesh_template(GEO_ID)
    built = []
    for name, changes in y1_variants():
        overrides = mesh_study.apply_study_overrides(
            template,
            {"m_max": M_MAX, "bl_layers": BL_LAYERS, **changes},
        )
        built.append((name, overrides))
    return built


def mesh_only_cell_count(data_root, family, mesh_id):
    """Cell count on the mesh-only manifest for one variant."""
    path = (
        Path(data_root) / "meshes" / family / GEO_ID / mesh_id / "manifest.json"
    )
    if not path.is_file():
        raise FileNotFoundError(
            f"mesh-only manifest is missing for {mesh_id}: {path}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = payload.get("cell_count") if isinstance(payload, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(
            f"mesh-only cell_count for {mesh_id} is not a positive integer: "
            f"{count!r}."
        )
    return count


def variants_under_cell_cap(studies, max_cells, data_root):
    """Split studies into those at or under ``max_cells`` and those over it.

    ``dropped`` entries are ``(name, mesh_id, cell_count)``. A missing
    manifest raises; it is not treated as under the cap.
    """
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells < 1:
        raise ValueError(
            f"--max-cells must be an integer >= 1, got {max_cells!r}."
        )
    kept = []
    dropped = []
    for name, overrides in studies:
        count = mesh_only_cell_count(
            data_root, overrides["family"], overrides["mesh_id"]
        )
        if count > max_cells:
            dropped.append((name, overrides["mesh_id"], count))
        else:
            kept.append((name, overrides))
    return kept, dropped


def y1_jobs(mesh_only=False, *, studies=None, data_root=None):
    """Job-queue records. Mesh-only ids end in ``-mesh`` so logs do not collide.

    Full jobs pass ``--reuse-mesh``. Mesh-only jobs do not.
    """
    if studies is None:
        studies = y1_study_overrides()
    root = DATA_ROOT if data_root is None else str(data_root)
    jobs = []
    seen = set()
    for name, overrides in studies:
        job_id = f"D2450_a45-{name}"
        if mesh_only:
            job_id += "-mesh"
        if job_id in seen:
            raise ValueError(f"duplicate job id {job_id!r}.")
        seen.add(job_id)
        y1_um = overrides["membrane_bl_y1"]["y1_um"]
        argv = [
            "{python}",
            "scripts/mfbo/mesh_study_case.py",
            "--data-root",
            root,
            "--geo-id",
            GEO_ID,
            "--case",
            CASE_ID,
            "--m-max",
            "0.085",
            "--bl-layers",
            "4",
            "--membrane-bl-y1-um",
            f"{y1_um:g}",
            "--geometry-suffix",
            ".pmdb",
            "--geometry-root",
            GEOMETRY_ROOT,
        ]
        if mesh_only:
            argv.append("--mesh-only")
        else:
            argv.append("--reuse-mesh")
        jobs.append(
            {
                "id": job_id,
                "argv": argv,
                "cwd": str(project_root()),
            }
        )
    return jobs


def write_queue(path, jobs):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(jobs, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Write the D2450_a45 membrane y1 job queue. "
            "Does not launch Fluent."
        ),
    )
    parser.add_argument(
        "--emit-queue",
        required=True,
        help="Queue JSON path to write.",
    )
    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Jobs mesh and print the cell count. They do not solve.",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        default=None,
        help=(
            "Full queue only. Drop variants whose mesh-only manifest "
            "cell_count exceeds this. The manifests are under the "
            "meshstudy data root. Kept jobs reuse those leaves."
        ),
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mesh_only and args.max_cells is not None:
        raise ValueError(
            "--max-cells reads completed mesh-only manifests. Omit --mesh-only."
        )
    studies = y1_study_overrides()
    if args.max_cells is not None:
        studies, dropped = variants_under_cell_cap(
            studies, args.max_cells, DATA_ROOT
        )
        for name, mesh_id, count in dropped:
            print(
                f"dropped {name} {mesh_id}: "
                f"cell_count {count} > {args.max_cells}"
            )
        if not studies:
            raise ValueError(
                f"every variant exceeds --max-cells {args.max_cells}."
            )
    jobs = y1_jobs(mesh_only=args.mesh_only, studies=studies)
    path = write_queue(args.emit_queue, jobs)
    for name, overrides in studies:
        print(f"{name}: {overrides['mesh_id']}")
    print(f"Wrote {len(jobs)} jobs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
