"""Compare saved 2D membrane-diagnostic fields.

Does not launch Fluent. Prints the measured membrane length, flux
consistency, and source-conversion factors. It does not assign pass
or fail, and it does not select a mesh level.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ro_2d_pilot.config import MESH_LEVELS
from ro_2d_pilot.ladder import LadderMismatch, compare_ladder
from ro_2d_pilot.membrane_diag import format_membrane_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare saved 2D membrane diagnostics. Does not launch Fluent."
        ),
    )
    parser.add_argument(
        "--result",
        action="append",
        type=Path,
        default=None,
        help="One result.json. Repeat for each mesh level.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="runs/{geo_id} directory containing medium|fine|very_fine.",
    )
    parser.add_argument("--run-id", default="u0p2_p6M")
    return parser


def discover_results(results_dir: Path, run_id: str) -> list[Path]:
    found: list[Path] = []
    for level in MESH_LEVELS:
        path = results_dir / level / run_id / "result.json"
        if path.is_file():
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = _selected_paths(args.result, args.results_dir, args.run_id)
        records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        if len(records) >= 2:
            compare_ladder(records)
        print(format_membrane_comparison(records))
    except (OSError, json.JSONDecodeError, LadderMismatch, TypeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _selected_paths(
    results: list[Path] | None,
    results_dir: Path | None,
    run_id: str,
) -> list[Path]:
    if results and results_dir is not None:
        raise ValueError("Pass either --result or --results-dir.")
    if results:
        return results
    if results_dir is None:
        raise ValueError("Pass --result or --results-dir.")
    found = discover_results(results_dir, run_id)
    if not found:
        raise ValueError(f"Found no result.json files under {results_dir}.")
    return found


if __name__ == "__main__":
    raise SystemExit(main())
