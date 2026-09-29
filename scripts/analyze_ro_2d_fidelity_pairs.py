"""Compare saved medium and very_fine 2D results.

Does not launch Fluent. Writes a JSON and CSV summary under
``RO_2D_DATA_ROOT/studies/fidelity_pairs/``. Does not decide whether
medium is an acceptable low-fidelity model.
"""

from __future__ import annotations

import argparse
import sys

from ro_2d_pilot.fidelity_screen import (
    analyze_rows,
    format_analysis,
    load_plan,
    write_summary,
)
from ro_2d_pilot.paths import data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare saved 2D medium and very_fine pairs. "
            "Does not launch Fluent."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        root = data_root()
        report = analyze_rows(load_plan(root))
        written = write_summary(root, report)
    except (OSError, TypeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(format_analysis(report))
    print(f"wrote json: {written['json']}")
    print(f"wrote csv: {written['csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
