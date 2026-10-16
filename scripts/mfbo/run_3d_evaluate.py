"""Evaluate one pillar design at one fidelity.

Importing this module does not launch Fluent. Running it uses
``ro.mfbo_drivers`` and does. A fidelity marked ``TBD`` is refused
before any driver starts. The result record is printed as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_adapter import PillarDesign, evaluate, resolve_data_root
from ro.mfbo_drivers import make_drivers
from ro.mfbo_fidelity_screen import load_fidelity_table, require_runnable_fidelity


def build_parser():
    parser = argparse.ArgumentParser(
        description="Evaluate one pillar design and print the result record as JSON.",
    )
    parser.add_argument("--d-p-mm", required=True, type=float)
    parser.add_argument("--d-h-mm", required=True, type=float)
    parser.add_argument("--d-f-mm", required=True, type=float)
    parser.add_argument("--fidelity", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--fidelity-table", required=True, type=Path)
    parser.add_argument(
        "--geometry-worker",
        action="store_true",
        help="Generate CAD through one Discovery session kept for this process.",
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
    table = load_fidelity_table(args.fidelity_table)
    require_runnable_fidelity(table, args.fidelity)
    root = resolve_data_root(args.data_root)
    design = PillarDesign(
        d_p_mm=args.d_p_mm,
        d_h_mm=args.d_h_mm,
        d_f_mm=args.d_f_mm,
    )
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
        record = evaluate(
            design,
            args.fidelity,
            run_id=args.run_id,
            data_root=root,
            fidelity_table=table,
            drivers=drivers,
        )
    finally:
        if supervisor is not None:
            supervisor.shutdown()
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
