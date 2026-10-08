"""Generate one Diamond CAD file with Discovery 25.1.

Does not launch Discovery until this script is executed. Windows only.
``--out-dir`` under ``C:/ro_data`` is refused.
"""

from __future__ import annotations

import argparse
import sys
import traceback


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate a Diamond fluid body as PMDB and SCDOCX.",
    )
    parser.add_argument(
        "--geo-id",
        required=True,
        help="One of the nine production Diamond ids. Pitch and angle come from the registry.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory. Paths under C:/ro_data are refused.",
    )
    parser.add_argument(
        "--n-active",
        type=int,
        default=None,
        help="Active cell count. Default is the production count for --geo-id.",
    )
    parser.add_argument(
        "--debug-booleans",
        action="store_true",
        help=(
            "Log every boolean to <geo-id>_boolean_debug.log in --out-dir. "
            "On the first failure, or if more than one body remains, save "
            "the design .pmdb and .scdocx next to that log."
        ),
    )
    return parser


def main(argv=None):
    from ro.diamond_cad import generate_diamond_cad

    args = build_parser().parse_args(argv)
    written = generate_diamond_cad(
        geo_id=args.geo_id,
        out_dir=args.out_dir,
        n_active=args.n_active,
        debug_booleans=args.debug_booleans,
    )
    for kind, path in written["paths"].items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"DIAMOND CAD FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
