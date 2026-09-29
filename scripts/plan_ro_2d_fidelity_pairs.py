"""Print the medium/very_fine screening plan.

Does not launch Fluent. Reads ``RO_2D_DATA_ROOT`` only to see which
result.json files already exist.
"""

from __future__ import annotations

import argparse
import sys

from ro_2d_pilot.fidelity_screen import format_plan, load_plan
from ro_2d_pilot.paths import data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print the 2D medium/very_fine geometry list and the Git Bash "
            "commands for runs that are not yet valid. Does not launch Fluent."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        root = data_root()
        rows = load_plan(root)
    except (OSError, TypeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(format_plan(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
