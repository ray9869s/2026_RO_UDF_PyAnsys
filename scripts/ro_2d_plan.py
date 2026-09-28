"""Print a 2D RO pilot case plan.

Does not launch Fluent. ``--write`` stores JSON under ``RO_2D_DATA_ROOT``.
"""

from __future__ import annotations

import argparse
import json
import sys

from ro_2d_pilot.config import (
    DEFAULT_INLET_VELOCITY_M_S,
    DEFAULT_OUTLET_GAUGE_PRESSURE_PA,
    FIDELITIES,
    OperatingPoint,
    PilotConfig,
)
from ro_2d_pilot.paths import data_root
from ro_2d_pilot.plan import build_plan, materialize, plans_for_fidelities


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print a 2D RO pilot case plan. Does not launch Fluent.",
    )
    parser.add_argument("--d-m", type=float, required=True)
    parser.add_argument("--l-m", type=float, required=True)
    parser.add_argument(
        "--fidelity",
        choices=(*FIDELITIES, "both"),
        default="both",
    )
    parser.add_argument("--n-pitches", type=int, default=1)
    parser.add_argument(
        "--inlet-velocity",
        type=float,
        default=DEFAULT_INLET_VELOCITY_M_S,
    )
    parser.add_argument(
        "--outlet-gauge-pressure",
        type=float,
        default=DEFAULT_OUTLET_GAUGE_PRESSURE_PA,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write JSON files under RO_2D_DATA_ROOT.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    operating = OperatingPoint(
        inlet_velocity_m_s=args.inlet_velocity,
        outlet_gauge_pressure_pa=args.outlet_gauge_pressure,
    )
    if args.fidelity == "both":
        plans = plans_for_fidelities(
            d_m=args.d_m,
            L_m=args.l_m,
            operating=operating,
            n_pitches=args.n_pitches,
        )
        payload: object = plans
        selected = list(plans.values())
    else:
        plan = build_plan(
            PilotConfig(
                d_m=args.d_m,
                L_m=args.l_m,
                fidelity=args.fidelity,
                operating=operating,
                n_pitches=args.n_pitches,
            )
        )
        payload = plan
        selected = [plan]
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if args.write:
        root = data_root()
        for plan in selected:
            written = materialize(plan, root)
            for kind, path in written.items():
                print(f"wrote {kind}: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
