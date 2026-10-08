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
    build_queue,
    load_designs,
    load_fidelity_table,
    require_screen_table,
    run_screen,
    write_queue,
    write_summary,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare pillar designs at LF and HF. "
            "--emit-queue writes a sequential job queue and does not evaluate."
        ),
    )
    parser.add_argument("--designs", required=True, type=Path, help="JSON list of designs.")
    parser.add_argument(
        "--fidelity-table",
        required=True,
        type=Path,
        help="JSON object with LF and HF mesh settings.",
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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    designs = load_designs(args.designs)
    table = load_fidelity_table(args.fidelity_table)
    require_screen_table(table)
    root = resolve_data_root(args.data_root)
    if args.emit_queue is not None:
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
    report = run_screen(
        designs,
        table,
        run_id=args.run_id,
        data_root=root,
        drivers=make_drivers(root),
    )
    paths = write_summary(args.out_dir, report)
    print(f"csv: {paths['csv']}", flush=True)
    print(f"markdown: {paths['markdown']}", flush=True)
    print(
        f"comparable: {report['n_comparable']} of {report['n_designs']}",
        flush=True,
    )
    cost = report["cost_ratio"]
    print(f"median cost ratio: {cost['median']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
