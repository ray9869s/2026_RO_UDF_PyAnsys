"""Replay the 3D loop on Gaussian-process truths. No Fluent.

Reads the CSV from ``export_3d_dataset.py``. Fits an HF truth, an LF
discrepancy, and an HF gate classifier, then runs ``hf_bo`` and ``mfbo``
on the v2 17×17 grid for each seed and LF/HF cost ratio. Failures are
charged the median recorded HF gate-fail cost. The same metrics are
written with and without failure-aware constrained EI.

Seed 0 at ratio 0.54 is compared with the live v2 runs: both reached
D_p 0.75 mm, d_h 0.05625 mm. The comparison is ``validation.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_dataset import read_dataset  # noqa: E402
from ro.mfbo_synthetic import DEFAULT_RATIOS, run_benchmark  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Synthetic 3D HF-only BO and MFBO across LF/HF cost ratios. No Fluent.",
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seeds", type=int, default=20, help="Seeds 0 .. N-1. Seed 0 is the v2 design.")
    parser.add_argument(
        "--ratios",
        default=",".join(str(ratio) for ratio in DEFAULT_RATIOS),
        help="Comma-separated LF/HF cost ratios.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if isinstance(args.seeds, bool) or not isinstance(args.seeds, int) or args.seeds < 1:
            raise ValueError(f"--seeds must be an integer >= 1, got {args.seeds!r}.")
        ratios = tuple(float(item) for item in str(args.ratios).split(",") if item.strip())
        if not ratios:
            raise ValueError("--ratios needs at least one positive cost ratio.")
        rows = read_dataset(args.csv)
        result = run_benchmark(
            rows,
            args.out_dir,
            repo_root=_REPO,
            seeds=tuple(range(args.seeds)),
            ratios=ratios,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(result["validation_text"], flush=True)
    print(f"wrote {args.out_dir / 'summary.csv'}", flush=True)
    print(f"wrote {result['plot']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
