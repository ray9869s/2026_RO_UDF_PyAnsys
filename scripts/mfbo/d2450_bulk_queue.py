"""D2450_a45 bulk-only queue at fixed surface m_max and membrane y1.

Three polyhedra levels, u0p2_p6M, on ``C:/ro_data_mfbo/meshstudy`` with
geometry from ``C:/ro_data_mfbo/geometries_pmdb``. Surface ``m_max`` is
0.035 mm, membrane y1 is 2.0 um (``_y1h020``), and ``bl`` stays 4. The
derived membrane stack at that pair is 5 layers on every leaf. The
volume fill is polyhedra. Bulk max cell length is 0.034, 0.026, and
0.020 mm (``_bms034``, ``_bms026``, ``_bms020``). Growth rate stays 1.2.

Expected cell counts are the shell-plus-core estimate from the
poly-hexcore split at ``m_max`` 0.035 (about 6.3 M, 12 M, and 25 M).
They are not measurements. Mesh-only jobs print the actual cell count
and ``membrane_bl_layers``. Compare those lines with the counts this
emitter prints, and confirm every leaf records 5 layers, before the
full queue.

``--mesh-only`` writes jobs that mesh and print that line. Without it,
each job passes ``--reuse-mesh`` and ``--checkpoint-every 200``.

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
BL_LAYERS = 4
Y1_UM = 2.0
M_MAX_MM = 0.035
# (token, bulk max cell length mm, expected cell count)
BULK_LEVELS = (
    ("bms034", 0.034, 6_300_000),
    ("bms026", 0.026, 12_000_000),
    ("bms020", 0.020, 25_000_000),
)


def bulk_study_overrides():
    """Production template plus m_max 0.035, y1 2.0 um, and one bulk size."""
    _case, template, _retries = mesh_study.load_mesh_template(GEO_ID)
    built = []
    for name, size_mm, _expected in BULK_LEVELS:
        overrides = mesh_study.apply_study_overrides(
            template,
            {
                "bl_layers": BL_LAYERS,
                "m_max": M_MAX_MM,
                "membrane_bl_y1_um": Y1_UM,
                "bulk_max_size_mm": size_mm,
            },
        )
        built.append((name, overrides))
    return built


def bulk_jobs(mesh_only=False, *, studies=None):
    """Job-queue records. Mesh-only ids end in ``-mesh`` so logs do not collide.

    Full jobs pass ``--reuse-mesh`` and ``--checkpoint-every 200``.
    Mesh-only jobs do not.
    """
    if studies is None:
        studies = bulk_study_overrides()
    jobs = []
    seen = set()
    for name, overrides in studies:
        job_id = f"D2450_a45-{name}-y1h020"
        if mesh_only:
            job_id += "-mesh"
        if job_id in seen:
            raise ValueError(f"duplicate job id {job_id!r}.")
        seen.add(job_id)
        size_mm = overrides["bulk_max_size_mm"]
        argv = [
            "{python}",
            "scripts/mfbo/mesh_study_case.py",
            "--data-root",
            DATA_ROOT,
            "--geo-id",
            GEO_ID,
            "--case",
            CASE_ID,
            "--m-max",
            f"{overrides['m_max']:.3f}",
            "--bl-layers",
            str(BL_LAYERS),
            "--membrane-bl-y1-um",
            f"{Y1_UM:g}",
            "--bulk-max-size",
            f"{size_mm:.3f}",
            "--geometry-suffix",
            ".pmdb",
            "--geometry-root",
            GEOMETRY_ROOT,
        ]
        if mesh_only:
            argv.append("--mesh-only")
        else:
            argv.extend(["--reuse-mesh", "--checkpoint-every", "200"])
        jobs.append(
            {
                "id": job_id,
                "argv": argv,
                "cwd": str(project_root()),
            }
        )
    return jobs


def expected_cell_counts():
    """Estimate printed with each mesh id. Not written into the queue JSON."""
    return {name: count for name, _size, count in BULK_LEVELS}


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
            "Write the D2450_a45 bulk-only job queue. Does not launch Fluent."
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    studies = bulk_study_overrides()
    jobs = bulk_jobs(mesh_only=args.mesh_only, studies=studies)
    path = write_queue(args.emit_queue, jobs)
    expected = expected_cell_counts()
    for name, overrides in studies:
        layers = overrides["membrane_bl_y1"]["layers"]
        print(
            f"{name}: {overrides['mesh_id']} "
            f"membrane_bl_layers={layers} "
            f"expected_cells={expected[name]}"
        )
    print(f"Wrote {len(jobs)} jobs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
