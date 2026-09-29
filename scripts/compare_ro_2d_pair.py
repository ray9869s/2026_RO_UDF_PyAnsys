"""Compare one saved low result.json with one saved high result.json.

Does not launch Fluent. Refuses the comparison when geometry or the
operating point differs. Prints measured differences only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ro_2d_pilot.pair import PairMismatch, compare_results, format_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare saved low and high 2D result.json files. "
            "Does not launch Fluent."
        ),
    )
    parser.add_argument("--low", type=Path, required=True)
    parser.add_argument("--high", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        low = json.loads(args.low.read_text(encoding="utf-8"))
        high = json.loads(args.high.read_text(encoding="utf-8"))
        report = compare_results(low, high)
    except (OSError, json.JSONDecodeError, PairMismatch, TypeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(format_comparison(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
