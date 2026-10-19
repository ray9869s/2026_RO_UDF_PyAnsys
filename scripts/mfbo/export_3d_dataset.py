"""Export evaluated 3D HF_m and LF_d leaves at one run id. No Fluent.

Host-side collector for ``C:/ro_data_mfbo/mfbo3d``. One row per run leaf
under ``runs/pillar`` whose directory is ``<run-id>_hfm`` or
``<run-id>_lfd``, including ``_run_failed_`` archives. Screen, v1, v2,
and later seeds share those leaves, so a design evaluated once appears
once. Diameters come from the geometry meta when it is present, so a
grid point such as ``d_h`` 0.05625 mm is not rounded to the geo-id token.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_dataset import collect_observations, write_dataset  # noqa: E402


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Write one CSV of evaluated HF_m and LF_d leaves. Does not launch Fluent.",
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-id", default="u0p2_p6M")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rows = collect_observations(args.data_root, run_id=args.run_id)
        destination = write_dataset(args.out, rows)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    counts = Counter(row.status for row in rows)
    levels = Counter(row.fidelity for row in rows)
    print(
        f"wrote {len(rows)} rows to {destination} "
        f"fidelity={dict(levels)} status={dict(counts)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
