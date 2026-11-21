"""Follow-up queue for the two non-converged 0.2 m/s leaves (2026-11-22).

1. ``cont-P_p100_h00``: ``--continue-iterations 2000 --continue-every 500``
   on P_p100_h00 ``u0p2_p6M`` (standard v3 mesh). Its gate is FAIL only on
   continuity_final (1.2754e-4 > 1e-4) with flat monitors. A continue needs
   only a complete final pair and ``iterations_run``; the gate is not a
   precondition. Every chunk re-extracts into ``post/reports_iter<N>`` and
   rewrites the manifest gate; ``post/reports`` keeps the 2000 final, so the
   re-extract emitter lists the leaf as STALE afterwards.
2. ``init-D2450_a30-u0p2``: fresh ``u0p2_p6M`` solve of D2450_a30
   (standard mesh, ``--require-mesh``) initialised from its ``u0p1_p6M``
   PASS final (``--init-from``). The failed leaf is moved aside by the
   driver. Run it only after the main queue's ``u0p1_p6M-D2450_a30`` job
   finished PASS; otherwise the job refuses before touching anything.

``--dry-run`` prints both jobs and the state of their leaves.
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

import campaign_v3_main_queue as main_queue  # noqa: E402
from ro.job_ids import require_job_ids  # noqa: E402

campaign = main_queue.campaign
CONTINUE_GEO, CONTINUE_RUN = "P_p100_h00", "u0p2_p6M"
CONTINUE_ITERATIONS, CONTINUE_EVERY = 2000, 500
INIT_GEO, INIT_RUN, INIT_SOURCE_RUN = "D2450_a30", "u0p2_p6M", "u0p1_p6M"


def continue_timeout_hours(solve_hours):
    """2 x estimate + one extract cap per chunk extract (start + chunks) + 1."""
    extracts = 1 + math.ceil(CONTINUE_ITERATIONS / CONTINUE_EVERY)
    extract_hours = extracts * main_queue.EXTRACT_WALL_CAP_HOURS
    if solve_hours is None:
        return main_queue.SOLVE_WALL_CAP_HOURS + extract_hours + 1.0
    return float(max(main_queue.SOLVE_TIMEOUT_MIN_HOURS,
                     math.ceil(2.0 * solve_hours + extract_hours + 1.0)))


def _leaf_state(path):
    manifest = Path(path) / "manifest.json"
    if not manifest.is_file():
        return "missing"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable manifest"
    return (f"gate={payload.get('convergence_quality')} "
            f"iterations_run={payload.get('iterations_run')} "
            f"stop_reason={payload.get('stop_reason')}")


def plan(data_root, spec, spec_path):
    standard = campaign.mesh_ids_for_spec(spec)
    rows = []

    job = main_queue._single_job(spec, spec_path, "solve", CONTINUE_GEO, CONTINUE_RUN, False)
    argv = job["argv"]
    del argv[argv.index("--max-iterations"):argv.index("--max-iterations") + 2]
    argv[argv.index("--require-mesh")] = "--reuse-mesh"
    argv.extend(["--continue-iterations", str(CONTINUE_ITERATIONS),
                 "--continue-every", str(CONTINUE_EVERY)])
    job["id"] = f"cont-{CONTINUE_GEO}"
    mesh_id = standard[CONTINUE_GEO]
    cells = main_queue._cell_count(data_root, CONTINUE_GEO, mesh_id)
    solve_hours = campaign.estimate_solve_hours(cells)
    leaf = campaign.run_leaf(data_root, CONTINUE_GEO, mesh_id, CONTINUE_RUN)
    rows.append(_row(job, CONTINUE_GEO, CONTINUE_RUN, mesh_id, cells, solve_hours,
                     continue_timeout_hours(solve_hours), f"leaf: {_leaf_state(leaf)}"))

    job = main_queue._single_job(spec, spec_path, "solve", INIT_GEO, INIT_RUN, False)
    mesh_id = standard[INIT_GEO]
    source = campaign.run_leaf(data_root, INIT_GEO, mesh_id, INIT_SOURCE_RUN)
    # The driver requires the source under the job's own --data-root.
    job_root = job["argv"][job["argv"].index("--data-root") + 1]
    job["argv"].extend([
        "--init-from",
        Path(campaign.run_leaf(job_root, INIT_GEO, mesh_id, INIT_SOURCE_RUN)).as_posix(),
    ])
    job["id"] = f"init-{INIT_GEO}-u0p2"
    cells = main_queue._cell_count(data_root, INIT_GEO, mesh_id)
    solve_hours = campaign.estimate_solve_hours(cells)
    target = campaign.run_leaf(data_root, INIT_GEO, mesh_id, INIT_RUN)
    state = f"source {INIT_SOURCE_RUN}: {_leaf_state(source)}; target: {_leaf_state(target)}"
    rows.append(_row(job, INIT_GEO, INIT_RUN, mesh_id, cells, solve_hours,
                     main_queue.solve_timeout_hours(solve_hours), state))
    require_job_ids([row["job"] for row in rows])
    return rows


def _row(job, geo_id, run_id, mesh_id, cells, solve_hours, timeout, state):
    job["timeout_s"] = timeout * 3600.0
    return {
        "job": job, "geo_id": geo_id, "run_id": run_id, "mesh_id": mesh_id, "cells": cells,
        "estimate_h": None if solve_hours is None else solve_hours + main_queue.EXTRACT_ESTIMATE_HOURS,
        "timeout_h": timeout, "state": state,
    }


def format_plan(rows):
    lines = []
    for row in rows:
        cells = "?" if row["cells"] is None else f"{row['cells']:,}"
        estimate = "?" if row["estimate_h"] is None else f"{row['estimate_h']:.1f}"
        lines.append(f"{row['job']['id']}: {row['geo_id']} {row['run_id']} {row['mesh_id']} "
                     f"cells={cells} est_h={estimate} timeout_h={row['timeout_h']:.0f}")
        lines.append(f"    {row['state']}")
        lines.append("    argv: " + " ".join(row["job"]["argv"]))
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--emit-queue", default=None, help="Queue JSON path (must not exist).")
    parser.add_argument("--dry-run", action="store_true", help="Print the jobs; write nothing.")
    parser.add_argument("--data-root", default=campaign.DATA_ROOT)
    parser.add_argument("--spec", default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.dry_run and not args.emit_queue:
        raise SystemExit("Choose --dry-run or --emit-queue PATH.")
    if args.emit_queue and Path(args.emit_queue).exists():
        raise SystemExit(f"Refusing to overwrite an existing queue: {args.emit_queue}")
    spec_path = main_queue.resolve_spec_path(
        main_queue.default_spec_path() if args.spec is None else args.spec)
    rows = plan(args.data_root, main_queue.load_mesh_spec(spec_path), spec_path)
    print(format_plan(rows))
    if args.dry_run:
        print("DRY RUN: nothing written.")
        return 0
    path = main_queue.write_queue_exclusive(args.emit_queue, [row["job"] for row in rows])
    print(f"Wrote {len(rows)} jobs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
