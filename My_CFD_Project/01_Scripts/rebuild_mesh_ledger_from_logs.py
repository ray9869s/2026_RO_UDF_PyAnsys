"""Rebuild the structured meshing ledger from existing Fluent transcripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ro.mesh_common import (
    build_mesh_ledger_record,
    mesh_parameters_from_mapping,
    parse_mesh_metrics_text,
    parse_meshing_input_summary,
    upsert_mesh_ledger_csv,
)
from ro.paths import data_root, meshes_root


def rebuild_mesh_ledger_records(results_root):
    """Return one best-effort ledger record for each existing mesh transcript."""
    results_root = Path(results_root)
    records = []
    for log_path in sorted(results_root.rglob("mesh_log_*.txt")):
        mesh_case_name = log_path.parent.name
        geo_name = log_path.parent.parent.name
        mesh_file_path = (
            log_path.parent
            / f"{geo_name}_{mesh_case_name}.msh.h5"
        )
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        mesh_parameters = mesh_parameters_from_mapping(
            parse_meshing_input_summary(text)
        )
        metrics = parse_mesh_metrics_text(text)
        mesh_exists = mesh_file_path.is_file()
        records.append(
            build_mesh_ledger_record(
                geo_name=geo_name,
                mesh_case_name=mesh_case_name,
                mesh_parameters=mesh_parameters,
                status="SUCCESS" if mesh_exists else "FAILED_OR_INCOMPLETE",
                exit_code=None,
                wall_time_seconds=None,
                metrics=metrics,
                mesh_log_path=log_path,
                mesh_file_path=mesh_file_path,
                error_summary=(
                    "Retroactive record: exit code and wall time were not "
                    "available in the transcript."
                ),
            )
        )
    return records


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="Tree to scan for mesh_log_*.txt (default: RO_DATA_ROOT/meshes).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Default: RO_DATA_ROOT/inventory/mesh_ledger.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print reconstructed records without writing the ledger.",
    )
    return parser.parse_args(argv)


def resolve_path_defaults(args):
    args.results_root = (args.results_root or meshes_root()).resolve()
    if not args.results_root.is_dir():
        raise NotADirectoryError(
            f"Results root is not an existing directory: {args.results_root}"
        )
    args.output = (
        args.output or (data_root() / "inventory" / "mesh_ledger.csv")
    ).resolve()
    return args


def main(argv=None):
    try:
        args = resolve_path_defaults(parse_args(argv))
    except (ValueError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    records = rebuild_mesh_ledger_records(args.results_root)
    if args.dry_run:
        print(json.dumps(records, indent=2, default=str))
    else:
        upsert_mesh_ledger_csv(args.output, records)
        print(f"Wrote {len(records)} mesh ledger record(s): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
