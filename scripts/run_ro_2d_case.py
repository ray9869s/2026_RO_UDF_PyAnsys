"""Solve one 2D RO pilot case, or write its mesh and records only.

Requires ``RO_2D_DATA_ROOT``. Does not launch Fluent when ``--dry-run``
is set. A solved case exits 0 when validity is ``valid`` and 2 when the
record is ``invalid``. Setup and launch failures exit 1.
"""

from __future__ import annotations

import argparse
import sys

from ro_2d_pilot.config import (
    DEFAULT_INLET_VELOCITY_M_S,
    DEFAULT_OUTLET_GAUGE_PRESSURE_PA,
    FIDELITIES,
    OperatingPoint,
    PilotConfig,
)
from ro_2d_pilot.execute import run_case
from ro_2d_pilot.paths import data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mesh and optionally solve one 2D RO pilot case.",
    )
    parser.add_argument("--d-m", type=float, required=True)
    parser.add_argument("--l-m", type=float, required=True)
    parser.add_argument("--u-ms", type=float, default=DEFAULT_INLET_VELOCITY_M_S)
    parser.add_argument(
        "--pressure-pa",
        type=float,
        default=DEFAULT_OUTLET_GAUGE_PRESSURE_PA,
    )
    parser.add_argument("--fidelity", choices=FIDELITIES, default="low")
    parser.add_argument("--n-pitches", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the mesh and JSON records. Do not launch Fluent.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = data_root()
        record = run_case(
            PilotConfig(
                d_m=args.d_m,
                L_m=args.l_m,
                fidelity=args.fidelity,
                operating=OperatingPoint(
                    inlet_velocity_m_s=args.u_ms,
                    outlet_gauge_pressure_pa=args.pressure_pa,
                ),
                n_pitches=args.n_pitches,
            ),
            root,
            dry_run=args.dry_run,
            max_iterations=args.max_iterations,
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"validity={record['validity']} geo_id={record['geo_id']}")
    if args.dry_run:
        return 0
    if record["validity"] == "valid":
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
