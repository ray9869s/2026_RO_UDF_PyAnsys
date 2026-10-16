"""LF/HF screen for a list of pillar designs.

Importing this module does not launch Fluent. Without ``--emit-queue``
it evaluates every design at both levels through the pillar adapter.
``--emit-queue`` writes a job-queue JSON file and does not evaluate.
A fidelity marked ``TBD`` is refused in both modes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_adapter import resolve_data_root
from ro.mfbo_drivers import make_drivers
from ro.mfbo_fidelity_screen import (
    V1_LEVELS,
    build_named_level_queue,
    build_queue,
    load_designs,
    load_fidelity_table,
    require_designs_clear_opening_gap,
    run_from_leaves,
    run_screen,
    run_v1_screen,
    screen_table_kind,
    write_queue,
    write_summary,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare pillar designs at LF and HF, or at LF_d and HF_m. "
            "--emit-queue writes a sequential job queue and does not evaluate."
        ),
    )
    parser.add_argument("--designs", required=True, type=Path, help="JSON list of designs.")
    parser.add_argument(
        "--fidelity-table",
        required=True,
        type=Path,
        help="JSON object with LF and HF, or with LF_d and HF_m.",
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Directory for the CSV and Markdown tables. Required unless --emit-queue.",
    )
    parser.add_argument(
        "--emit-queue",
        type=Path,
        default=None,
        help="Write this queue JSON and do not evaluate.",
    )
    parser.add_argument(
        "--from-leaves",
        action="store_true",
        help="Write the tables from existing leaves. Does not generate, mesh, or solve.",
    )
    parser.add_argument(
        "--geometry-worker",
        action="store_true",
        help="Keep one Discovery session and send every new geometry to it.",
    )
    parser.add_argument(
        "--stop-file",
        type=Path,
        default=None,
        help="With --geometry-worker, release Discovery when this file appears.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    designs = load_designs(args.designs)
    table = load_fidelity_table(args.fidelity_table)
    kind = screen_table_kind(table)
    root = resolve_data_root(args.data_root)
    if kind == "v1":
        require_designs_clear_opening_gap(designs, table)
    if args.from_leaves and args.emit_queue is not None:
        raise ValueError("--from-leaves does not emit a queue.")
    if args.geometry_worker and (args.emit_queue is not None or args.from_leaves):
        raise ValueError("--geometry-worker is only for an in-process evaluation.")
    if args.emit_queue is not None:
        if kind == "v1":
            jobs = build_named_level_queue(
                designs,
                V1_LEVELS,
                table,
                run_id=args.run_id,
                data_root=root,
                fidelity_table=args.fidelity_table,
            )
        else:
            jobs = build_queue(
                designs,
                run_id=args.run_id,
                data_root=root,
                fidelity_table=args.fidelity_table,
            )
        path = write_queue(args.emit_queue, jobs)
        print(f"queue: {path}", flush=True)
        print(f"jobs: {len(jobs)}", flush=True)
        print("This script did not evaluate.", flush=True)
        return 0
    if args.out_dir is None:
        raise ValueError("--out-dir is required when --emit-queue is not set.")
    if args.from_leaves:
        report = run_from_leaves(
            designs,
            table,
            run_id=args.run_id,
            data_root=root,
        )
        paths = write_summary(args.out_dir, report)
        print(f"csv: {paths['csv']}", flush=True)
        print(f"markdown: {paths['markdown']}", flush=True)
        print(
            f"comparable: {report['n_comparable']} of {report['n_designs']}",
            flush=True,
        )
        print("This script did not evaluate.", flush=True)
        return 0
    supervisor = None
    try:
        if args.geometry_worker:
            from ro.geometry_worker import open_geometry_worker

            supervisor = open_geometry_worker(
                root, enabled=True, stop_file=args.stop_file
            )
            drivers = make_drivers(
                root,
                geometry_worker=supervisor.directory,
                geometry_supervisor=supervisor,
            )
        else:
            drivers = make_drivers(root)
        if kind == "v1":
            report = run_v1_screen(
                designs,
                table,
                run_id=args.run_id,
                data_root=root,
                drivers=drivers,
            )
        else:
            report = run_screen(
                designs,
                table,
                run_id=args.run_id,
                data_root=root,
                drivers=drivers,
            )
    finally:
        if supervisor is not None:
            supervisor.shutdown()
    paths = write_summary(args.out_dir, report)
    print(f"csv: {paths['csv']}", flush=True)
    print(f"markdown: {paths['markdown']}", flush=True)
    print(
        f"comparable: {report['n_comparable']} of {report['n_designs']}",
        flush=True,
    )
    cost = report["cost_ratio"]
    print(f"median wall cost ratio: {cost['median']}", flush=True)
    if kind == "v1":
        proxy = report["cost_proxy_ratio"]
        print(f"median proxy cost ratio: {proxy['median']}", flush=True)
        print(
            "CP-1 bias mean="
            f"{report['cp_excess_bias_mean']} "
            f"spread={report['cp_excess_bias_spread']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
