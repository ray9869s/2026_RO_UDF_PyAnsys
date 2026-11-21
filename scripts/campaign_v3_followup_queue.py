"""Follow-up queue for D2450_a30 ``u0p2_p6M`` (2026-11-22): one job.

``init-D2450_a30-u0p2``: fresh ``u0p2_p6M`` solve of D2450_a30 (standard
mesh, ``--require-mesh``) initialised from its ``u0p1_p6M`` PASS final
(``--init-from``). The failed leaf is moved aside by the driver. Run it
only after the main queue's ``u0p1_p6M-D2450_a30`` job finished PASS;
otherwise the job refuses before touching anything.

P_p100_h00 ``u0p2_p6M`` is not continued: its continuity plateaued at
1.25-2.7e-4 with flat monitors. It is reported as
``convergence_class=continuity_boundary`` (``ro.convergence_quality``).

``--dry-run`` prints the job and the state of its leaves.
``--emit-queue PATH`` refuses an existing path. Does not launch Fluent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import campaign_v3_main_queue as main_queue  # noqa: E402
from ro.job_ids import require_job_ids  # noqa: E402

campaign = main_queue.campaign
INIT_GEO, INIT_RUN, INIT_SOURCE_RUN = "D2450_a30", "u0p2_p6M", "u0p1_p6M"


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
