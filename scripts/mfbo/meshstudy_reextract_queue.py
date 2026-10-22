"""Re-extract queue for five finished mesh-study leaves. No re-solve.

The solves wrote ``*_final.cas.h5`` and ``*_final.dat.h5``. Extract then
died on ``post_config.mesh_id``. These jobs call ``reextract_runs.py``,
which archives ``post/reports`` and runs the current extract worker.
No ``--profile`` and no fidelity-lead flag, so the window is the
per-geometry row in ``configs/evaluation_window_table.json``.

Leaves, all ``u0p2_p6M`` under ``C:/ro_data_mfbo/meshstudy``:

- D2450_a45 ``max085_min006_cpg5_bl4_peel2_y1h057``
- D2450_a45 ``max085_min006_cpg5_bl4_peel2_y1h030``
- D2450_a45 ``max085_min006_cpg5_bl4_peel2_y1h020``
- D2450_a45 ``max085_min006_cpg5_bl4_peel2_y1h014``
- D0817_a30 ``max045_min006_cpg5_bl4_peel2_lpx13m002``

``_y1h010`` is not here. That mesh failed the quality gate (aspect
ratio 178.6 > 150) and has no finished solve.

Importing this module does not launch Fluent. ``--emit-queue`` writes a
job-queue JSON file and does not extract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from ro.campaign_geo_ids import family_for_geo_id  # noqa: E402
from ro.paths import project_root  # noqa: E402

DATA_ROOT = "C:/ro_data_mfbo/meshstudy"
RUN_ID = "u0p2_p6M"

# (job id, geo_id, mesh_id). Exactly the leaves whose extract died.
LEAVES = (
    (
        "D2450_a45-y1h057-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h057",
    ),
    (
        "D2450_a45-y1h030-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h030",
    ),
    (
        "D2450_a45-y1h020-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h020",
    ),
    (
        "D2450_a45-y1h014-reextract",
        "D2450_a45",
        "max085_min006_cpg5_bl4_peel2_y1h014",
    ),
    (
        "D0817_a30-lpx13m002-reextract",
        "D0817_a30",
        "max045_min006_cpg5_bl4_peel2_lpx13m002",
    ),
)


def run_leaf(geo_id, mesh_id, *, data_root=None, run_id=RUN_ID):
    """Absolute run leaf. Forward slashes, so the host path stays portable."""
    root = DATA_ROOT if data_root is None else str(data_root).replace("\\", "/")
    family = family_for_geo_id(geo_id)
    return f"{root}/runs/{family}/{geo_id}/{mesh_id}/{run_id}"


def reextract_jobs(data_root=None):
    """One reextract_runs job per finished leaf. No mesh and no solve."""
    jobs = []
    seen = set()
    for job_id, geo_id, mesh_id in LEAVES:
        if job_id in seen:
            raise ValueError(f"duplicate job id {job_id!r}.")
        seen.add(job_id)
        jobs.append(
            {
                "id": job_id,
                "argv": [
                    "{python}",
                    "scripts/mfbo/reextract_runs.py",
                    run_leaf(geo_id, mesh_id, data_root=data_root),
                ],
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
            "Write the mesh-study re-extract queue for the five leaves "
            "whose extract failed after a finished solve. Does not launch Fluent."
        ),
    )
    parser.add_argument(
        "--emit-queue",
        required=True,
        help="Queue JSON path to write.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    jobs = reextract_jobs()
    path = write_queue(args.emit_queue, jobs)
    for job in jobs:
        print(f"{job['id']}: {job['argv'][-1]}")
    print(f"Wrote {len(jobs)} jobs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
