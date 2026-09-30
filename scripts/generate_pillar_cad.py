"""Generate one Pillar or Hole-Pillar CAD file with Discovery 25.1.

Does not launch Discovery until this script is executed. Windows only.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from ro.pillar_cad import generate_pillar_cad


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate a Pillar/Hole-Pillar fluid body as PMDB and SCDOCX.",
    )
    parser.add_argument("--d-p-mm", required=True, type=float, help="Pillar diameter, mm.")
    parser.add_argument(
        "--d-h-mm",
        required=True,
        type=float,
        help="Bore diameter, mm. Zero omits the bore.",
    )
    parser.add_argument(
        "--d-f-mm",
        required=True,
        type=float,
        help="Filament diameter, mm.",
    )
    parser.add_argument(
        "--geo-id",
        required=True,
        help="Design name written into the output filenames. Not looked up in batch_config.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Output directory. Paths under C:/ro_data/geometries are refused.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    written = generate_pillar_cad(
        d_p_mm=args.d_p_mm,
        d_h_mm=args.d_h_mm,
        d_f_mm=args.d_f_mm,
        geo_id=args.geo_id,
        out_dir=args.out_dir,
    )
    for kind, path in written["paths"].items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"PILLAR CAD FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
