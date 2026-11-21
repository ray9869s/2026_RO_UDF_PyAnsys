"""Emit the campaign v3 main queue as ONE job_queue file.

Order (decided 2026-11-22 from the host results at ``4e0dfc2``):

1. mesh-only ``_ar250`` meshes for S_a072_l1733 and S_a144_l1733;
2. ``u0p05_p6M`` solve of P_p80_h15 (low-velocity convergence check);
3. ``u0p2_p6M`` solves of the four ``_ar250`` geometries;
4. ``u0p05_p6M``, ``u0p15_p6M``, ``u0p25_p6M`` solves of REF_empty,
   D2450_a45, M_c267, P_p80_h15 (its u0p05 is step 2), S_a144_l3465,
   velocity by velocity;
5. ``u0p1_p6M`` solves of all 31 geometries in campaign order.

D1225_a30, S_a072_l1733, S_a144_l1733 and S_a193_l1733 use the
``_ar250`` mesh (hard global AR ceiling 250) for every velocity; the
other 27 use the standard v3 mesh. Finer membrane faces did not bring
the contact meshes under the standard ceiling (S_a193_l1733 AR 228.5 /
205.9 / 223.7 at 0.035 / 0.025 / 0.020 mm): the worst cells come from
the closing filament-membrane wedge.

Every solve uses the 2000-iteration cap (every family), live CP off,
and ``--require-mesh``: it reuses the existing standard or ``_ar250``
mesh and fails before any Fluent launch when that mesh is missing.
Every job carries ``timeout_s``.

Estimates: solve hours are linear in cell count from the D2450_a45
calibration (``campaign_v3.estimate_solve_hours``), plus
``EXTRACT_ESTIMATE_HOURS``. A mesh that does not exist yet (made by step
1) is estimated from that geometry's standard mesh. A mesh-only job is
``MESH_ONLY_ESTIMATE_HOURS``. These are planning numbers, not measured
times. Timeouts: mesh-only ``MESH_WALL_CAP_HOURS + 1``; a solve
``2 x solve estimate + EXTRACT_WALL_CAP_HOURS + 1``, clamped to
[``SOLVE_TIMEOUT_MIN_HOURS``, ``SOLVE_WALL_CAP_HOURS + EXTRACT_WALL_CAP_HOURS + 1``];
an unknown cell count gets the maximum.

``--dry-run`` prints the job list (geometry, run id, mesh id, cells,
estimated hours, timeout, mesh / run-leaf state) and writes nothing.
``--emit-queue PATH`` refuses an existing path. Does not launch Fluent.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import campaign_v3 as campaign  # noqa: E402
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id  # noqa: E402
from ro.job_ids import require_job_ids  # noqa: E402
from ro.mesh_spec import default_spec_path, load_mesh_spec, resolve_spec_path  # noqa: E402

AR250 = 250
AR250_GEO_IDS = ("D1225_a30", "S_a072_l1733", "S_a144_l1733", "S_a193_l1733")
AR250_MESH_ONLY_GEO_IDS = ("S_a072_l1733", "S_a144_l1733")
LOW_VELOCITY_CHECK = ("P_p80_h15", "u0p05_p6M")
EXTRA_VELOCITY_RUN_IDS = ("u0p05_p6M", "u0p15_p6M", "u0p25_p6M")
EXTRA_VELOCITY_GEO_IDS = ("REF_empty", "D2450_a45", "M_c267", "P_p80_h15", "S_a144_l3465")
ALL_GEOMETRY_RUN_ID = "u0p1_p6M"
MAIN_QUEUE_MAX_ITERATIONS = 2000

# Watchdog defaults in configs/run_config.py (a test keeps them equal).
MESH_WALL_CAP_HOURS = 12.0
SOLVE_WALL_CAP_HOURS = 48.0
EXTRACT_WALL_CAP_HOURS = 6.0
# Planning assumptions, not measurements.
MESH_ONLY_ESTIMATE_HOURS = 1.5
EXTRACT_ESTIMATE_HOURS = 1.5
SOLVE_TIMEOUT_MIN_HOURS = 12.0
SOLVE_TIMEOUT_MAX_HOURS = SOLVE_WALL_CAP_HOURS + EXTRACT_WALL_CAP_HOURS + 1.0
MESH_TIMEOUT_HOURS = MESH_WALL_CAP_HOURS + 1.0


def planned_steps():
    """``[(step, kind, geo_id, run_id, uses_ar250)]`` in queue order."""
    steps = [(1, "mesh", geo_id, None, True) for geo_id in AR250_MESH_ONLY_GEO_IDS]
    geo, run = LOW_VELOCITY_CHECK
    steps.append((2, "solve", geo, run, geo in AR250_GEO_IDS))
    steps.extend((3, "solve", geo_id, "u0p2_p6M", True) for geo_id in AR250_GEO_IDS)
    for run_id in EXTRA_VELOCITY_RUN_IDS:
        for geo_id in EXTRA_VELOCITY_GEO_IDS:
            if (geo_id, run_id) == LOW_VELOCITY_CHECK:
                continue
            steps.append((4, "solve", geo_id, run_id, geo_id in AR250_GEO_IDS))
    steps.extend(
        (5, "solve", geo_id, ALL_GEOMETRY_RUN_ID, geo_id in AR250_GEO_IDS)
        for geo_id in CAMPAIGN_GEO_ID_ORDER
    )
    for _step, _kind, geo_id, run_id, _ar in steps:
        if geo_id not in CAMPAIGN_GEO_ID_ORDER:
            raise ValueError(f"{geo_id} is not a campaign geometry.")
        if run_id is not None and run_id not in campaign.run_ids():
            raise ValueError(f"{run_id} is not a campaign v3 run id.")
    return steps


def _cell_count(data_root, geo_id, mesh_id):
    path = campaign.mesh_manifest_path(data_root, geo_id, mesh_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("cell_count")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def solve_timeout_hours(solve_hours):
    if solve_hours is None:
        return SOLVE_TIMEOUT_MAX_HOURS
    hours = math.ceil(2.0 * solve_hours + EXTRACT_WALL_CAP_HOURS + 1.0)
    return float(min(SOLVE_TIMEOUT_MAX_HOURS, max(SOLVE_TIMEOUT_MIN_HOURS, hours)))


def _single_job(spec, spec_path, kind, geo_id, run_id, uses_ar250):
    ceiling = AR250 if uses_ar250 else None
    if kind == "mesh":
        (job,) = campaign.mesh_jobs(spec, spec_path, geo_ids=[geo_id], max_aspect_ratio=ceiling)
        return job
    (job,) = campaign.solve_jobs(
        spec,
        spec_path,
        run_id_filter=[run_id],
        geo_ids=[geo_id],
        max_iterations_by_family=dict.fromkeys(campaign.FAMILIES, MAIN_QUEUE_MAX_ITERATIONS),
        max_aspect_ratio=ceiling,
    )
    argv = job["argv"]
    if "--cp-live" in argv:
        raise ValueError("main queue solves run with live CP off.")
    argv[argv.index("--reuse-mesh")] = "--require-mesh"
    return job


def plan(data_root, spec, spec_path):
    """One row per job: the queue entry plus what ``--dry-run`` prints."""
    standard = campaign.mesh_ids_for_spec(spec)
    ar250 = campaign.mesh_ids_for_spec(spec, max_aspect_ratio=AR250)
    meshed_here = set()
    rows = []
    for step, kind, geo_id, run_id, uses_ar250 in planned_steps():
        mesh_id = (ar250 if uses_ar250 else standard)[geo_id]
        job = _single_job(spec, spec_path, kind, geo_id, run_id, uses_ar250)
        cells = _cell_count(data_root, geo_id, mesh_id)
        mesh_dir = campaign.mesh_manifest_path(data_root, geo_id, mesh_id).parent
        if kind == "mesh":
            state = "mesh leaf EXISTS (job will refuse)" if mesh_dir.exists() else "new mesh"
            meshed_here.add((geo_id, mesh_id))
            estimate, timeout = MESH_ONLY_ESTIMATE_HOURS, MESH_TIMEOUT_HOURS
        else:
            cell_note = ""
            if cells is None:
                proxy = _cell_count(data_root, geo_id, standard[geo_id])
                if proxy is not None and mesh_id != standard[geo_id]:
                    cells, cell_note = proxy, " (cells from standard mesh)"
            if mesh_dir.is_dir():
                state = "mesh ok"
            elif (geo_id, mesh_id) in meshed_here:
                state = "mesh from step 1"
            else:
                state = "MESH MISSING (job will fail fast)"
            state += cell_note
            leaf = campaign.run_leaf(data_root, geo_id, mesh_id, run_id)
            if leaf.exists():
                state += "; RUN LEAF EXISTS (job will refuse)"
            solve_hours = campaign.estimate_solve_hours(cells)
            estimate = None if solve_hours is None else solve_hours + EXTRACT_ESTIMATE_HOURS
            timeout = solve_timeout_hours(solve_hours)
        job["timeout_s"] = timeout * 3600.0
        rows.append(
            {
                "step": step,
                "job": job,
                "kind": kind,
                "geo_id": geo_id,
                "family": family_for_geo_id(geo_id),
                "run_id": run_id or "-",
                "mesh_id": mesh_id,
                "cells": cells,
                "estimate_h": estimate,
                "timeout_h": timeout,
                "state": state,
            }
        )
    require_job_ids([row["job"] for row in rows])
    return rows


def format_plan(rows):
    lines = [
        f"{'#':>3} {'step':>4} {'id':<34} {'geo':<14} {'run':<10} {'mesh_id':<52} "
        f"{'cells':>10} {'est_h':>6} {'tmo_h':>6}  state"
    ]
    for index, row in enumerate(rows, 1):
        cells = "?" if row["cells"] is None else f"{row['cells']:,}"
        estimate = "?" if row["estimate_h"] is None else f"{row['estimate_h']:.1f}"
        lines.append(
            f"{index:>3} {row['step']:>4} {row['job']['id']:<34} {row['geo_id']:<14} "
            f"{row['run_id']:<10} {row['mesh_id']:<52} {cells:>10} {estimate:>6} "
            f"{row['timeout_h']:>6.0f}  {row['state']}"
        )
    known = [row["estimate_h"] for row in rows if row["estimate_h"] is not None]
    unknown = len(rows) - len(known)
    lines.append(
        f"{len(rows)} jobs; estimated {sum(known):.1f} h"
        + (f" plus {unknown} job(s) with no cell count" if unknown else "")
    )
    problems = [row for row in rows if "MISSING" in row["state"] or "EXISTS" in row["state"]]
    if problems:
        lines.append(f"WARNING: {len(problems)} job(s) will fail or refuse as listed (see state).")
    return "\n".join(lines)


def write_queue_exclusive(path, jobs):
    """Write the queue; refuse an existing path."""
    destination = Path(path)
    require_job_ids(jobs)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite an existing queue: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(jobs, indent=2) + "\n")
    return destination


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--emit-queue", default=None, help="Queue JSON path (must not exist).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the job list and write nothing.")
    parser.add_argument("--data-root", default=campaign.DATA_ROOT,
                        help=f"Campaign data root (default {campaign.DATA_ROOT}).")
    parser.add_argument("--spec", default=None, help="Mesh spec JSON. Default configs/mesh_spec_v3.json.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.dry_run and not args.emit_queue:
        raise SystemExit("Choose --dry-run or --emit-queue PATH.")
    if args.emit_queue and Path(args.emit_queue).exists():
        raise SystemExit(f"Refusing to overwrite an existing queue: {args.emit_queue}")
    spec_path = resolve_spec_path(default_spec_path() if args.spec is None else args.spec)
    rows = plan(args.data_root, load_mesh_spec(spec_path), spec_path)
    print(format_plan(rows))
    if args.dry_run:
        print("DRY RUN: nothing written.")
        return 0
    path = write_queue_exclusive(args.emit_queue, [row["job"] for row in rows])
    print(f"Wrote {len(rows)} jobs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
