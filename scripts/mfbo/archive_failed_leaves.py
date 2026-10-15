"""List and archive failed MFBO leaves. Successful leaves are not moved.

Importing this module does not launch Fluent or Discovery. An infrastructure
leftover (no CAD or Fluent output, or an infrastructure error record) is
renamed beside itself. A geometry, mesh, or solver failure stays where it is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_adapter import (  # noqa: E402
    FAILURE_INFRASTRUCTURE,
    archive_infrastructure_leaves,
    resolve_data_root,
    summarize_mfbo_leaves,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "List failed MFBO geometry, mesh, and run leaves. "
            "Archive infrastructure leftovers. Do not move successful leaves."
        ),
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print the classification and do not rename anything.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_data_root(args.data_root)
    summary = summarize_mfbo_leaves(root)
    print(f"untouched: {summary['success_count']}", flush=True)
    if args.list_only:
        for row in summary["failed"]:
            print(f"list {row['class']} {row['kind']} {row['path']}", flush=True)
        print("This script did not archive.", flush=True)
        return 0
    archived = {
        item["path"]: item["archived"]
        for item in archive_infrastructure_leaves(root)
    }
    for row in summary["failed"]:
        destination = archived.get(row["path"])
        if destination is not None:
            print(
                f"archived {row['class']} {row['kind']} {row['path']} -> {destination}",
                flush=True,
            )
        else:
            print(f"left {row['class']} {row['kind']} {row['path']}", flush=True)
    print(f"archived: {len(archived)}", flush=True)
    if any(row["class"] != FAILURE_INFRASTRUCTURE for row in summary["failed"]):
        print("Geometry, mesh, and solver failures were left in place.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
