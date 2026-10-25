"""Predicted HF landscape of the 3D fidelity screen. No Fluent.

Reads the screen CSV, or the LF_d and HF_m leaves, and fits the same
autoregressive model the 3D loop uses to each level's valid points.
Prints the feasible fraction, the constrained optimum, and the
probability of feasibility on the screen points, then the same report
for a small threshold sweep. Writes one PNG with LF and HF side by side.
``--legend`` names the constraint curves and markers. ``--overlay`` adds
white diamonds. Neither is on unless asked, so the default PNG is unchanged.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_landscape import (  # noqa: E402
    Box,
    analyze,
    collect_from_leaves,
    format_report,
    read_screen_rows,
    write_contours,
)

_REPO = Path(__file__).resolve().parents[2]


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="HF/LF Gaussian-process landscape from the 3D fidelity screen. No Fluent.",
    )
    parser.add_argument("--csv", type=Path, default=None, help="v1 screen CSV.")
    parser.add_argument(
        "--from-leaves",
        action="store_true",
        help="Read LF_d and HF_m leaves instead of the CSV.",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--fidelity-table", type=Path, default=None)
    parser.add_argument("--designs", type=Path, default=None)
    parser.add_argument("--run-id", default="u0p2_p6M")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--cp-excess-max", type=float, default=0.040)
    parser.add_argument("--dp-max", type=float, default=71000.0)
    parser.add_argument("--d-p-min", type=float, default=0.60)
    parser.add_argument("--d-p-max", type=float, default=1.00)
    parser.add_argument("--d-h-min", type=float, default=0.00)
    parser.add_argument("--d-h-max", type=float, default=0.30)
    parser.add_argument(
        "--grid",
        type=int,
        default=None,
        help="Square search grid for the feasible count and optimum. Contours stay fine.",
    )
    parser.add_argument(
        "--legend",
        action="store_true",
        help="Name the constraint curves, the predicted optimum, the failed-gate mark, and any --overlay labels.",
    )
    parser.add_argument(
        "--overlay",
        action="append",
        default=None,
        metavar="D_P,D_H,LABEL",
        help="Extra point on both panels, drawn as a white diamond. Repeatable.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        designs = _designs(args)
        box = Box(args.d_p_min, args.d_p_max, args.d_h_min, args.d_h_max)
        report = analyze(
            designs,
            box,
            cp_excess_max=args.cp_excess_max,
            dp_max_pa_per_m=args.dp_max,
            search_grid=args.grid,
        )
        text = format_report(report)
        print(text, end="", flush=True)
        destination = args.out_dir / "landscape_3d_lmh.png"
        write_contours(
            report,
            destination,
            legend=args.legend,
            overlays=[_overlay(item) for item in (args.overlay or ())],
        )
        print(f"wrote {destination}", flush=True)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _overlay(text: str) -> tuple[float, float, str]:
    parts = text.split(",", 2)
    if len(parts) != 3:
        raise ValueError(f"Overlay must be D_p,d_h,label, got {text!r}.")
    try:
        d_p = float(parts[0])
        d_h = float(parts[1])
    except ValueError as exc:
        raise ValueError(f"Overlay coordinates must be numbers, got {text!r}.") from exc
    label = parts[2].strip()
    if not label or not math.isfinite(d_p) or not math.isfinite(d_h):
        raise ValueError(f"Overlay must be D_p,d_h,label with a finite point and a label, got {text!r}.")
    return d_p, d_h, label


def _designs(args):
    if args.from_leaves and args.csv is not None:
        raise ValueError("Pass --csv or --from-leaves, not both.")
    if args.from_leaves:
        if not args.data_root or args.fidelity_table is None:
            raise ValueError("--from-leaves requires --data-root and --fidelity-table.")
        from ro.mfbo_adapter import resolve_data_root
        from ro.mfbo_fidelity_screen import load_designs, load_fidelity_table

        designs_path = args.designs
        if designs_path is None:
            designs_path = _REPO / "configs" / "mfbo_3d_screen_designs.json"
        return collect_from_leaves(
            load_designs(designs_path),
            data_root=resolve_data_root(args.data_root),
            fidelity_table=load_fidelity_table(args.fidelity_table),
            run_id=args.run_id,
        )
    if args.csv is None:
        raise ValueError("Pass --csv or --from-leaves.")
    return read_screen_rows(args.csv)


if __name__ == "__main__":
    sys.exit(main())
