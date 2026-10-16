"""Generate Pillar or Hole-Pillar CAD with Discovery 25.1.

Does not launch Discovery until this script is executed. Windows only.
The backend start timeout is 300 s. A backend timeout is cleaned up and
retried once. Attempts are written on the meta, or on ``<geo_id>_error.json``.

``--batch`` keeps one session for every entry. ``--serve`` keeps that session
and answers generate requests from a worker directory. Without either flag,
one geometry still gets its own session.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate a Pillar/Hole-Pillar fluid body as PMDB and SCDOCX.",
    )
    parser.add_argument(
        "--batch",
        type=Path,
        help="JSON list of designs. One Discovery session for the whole list.",
    )
    parser.add_argument(
        "--serve",
        type=Path,
        help="Worker directory. Serve generate requests on one Discovery session.",
    )
    parser.add_argument(
        "--stop-file",
        type=Path,
        help="With --serve, finish the current design and exit when this file exists.",
    )
    parser.add_argument("--d-p-mm", type=float, help="Pillar diameter, mm.")
    parser.add_argument(
        "--d-h-mm",
        type=float,
        help="Bore diameter, mm. Zero omits the bore.",
    )
    parser.add_argument(
        "--d-f-mm",
        type=float,
        help="Filament diameter, mm.",
    )
    parser.add_argument(
        "--geo-id",
        help="Design name written into the output filenames. Not looked up in batch_config.",
    )
    parser.add_argument(
        "--out-dir",
        help="Output directory. Paths under C:/ro_data/geometries are refused.",
    )
    parser.add_argument(
        "--n-active",
        type=int,
        default=None,
        help="Active cells. Buffers stay 1 in + 2 out. Default 7, the unsuffixed domain.",
    )
    return parser


def main(argv=None):
    from ro.pillar_cad import generate_pillar_batch, generate_pillar_cad, serve_pillar_worker

    args = build_parser().parse_args(argv)
    _refuse_mixed_modes(args)
    if args.serve is not None:
        serve_pillar_worker(args.serve, stop_file=args.stop_file)
        return 0
    if args.batch is not None:
        entries = _load_batch(args.batch)
        results = generate_pillar_batch(entries)
        _write_batch_result(args.batch, results)
        failed = 0
        for result in results:
            print(
                f"{result['geo_id']}: {result['status']} "
                f"session_reused={result['session_reused']} "
                f"generate_wall_time_s={result['generate_wall_time_s']}",
                flush=True,
            )
            if result["status"] != "ok":
                failed += 1
                print(result["error"], file=sys.stderr)
        return 1 if failed else 0
    missing = [
        flag
        for flag, value in (
            ("--d-p-mm", args.d_p_mm),
            ("--d-h-mm", args.d_h_mm),
            ("--d-f-mm", args.d_f_mm),
            ("--geo-id", args.geo_id),
            ("--out-dir", args.out_dir),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            "single-geometry generation needs "
            + ", ".join(missing)
            + ". Or pass --batch, or --serve."
        )
    written = generate_pillar_cad(
        d_p_mm=args.d_p_mm,
        d_h_mm=args.d_h_mm,
        d_f_mm=args.d_f_mm,
        geo_id=args.geo_id,
        out_dir=args.out_dir,
        n_active=7 if args.n_active is None else args.n_active,
    )
    for kind, path in written["paths"].items():
        print(f"{kind}: {path}")
    return 0


def _refuse_mixed_modes(args) -> None:
    modes = [args.batch is not None, args.serve is not None]
    single = any(
        value is not None
        for value in (args.d_p_mm, args.d_h_mm, args.d_f_mm, args.geo_id, args.out_dir, args.n_active)
    )
    if sum(modes) > 1:
        raise ValueError("--batch and --serve cannot be combined.")
    if any(modes) and single:
        raise ValueError("single-geometry arguments cannot be combined with --batch or --serve.")
    if args.stop_file is not None and args.serve is None:
        raise ValueError("--stop-file requires --serve.")


def _load_batch(path: Path) -> list:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid batch JSON in {path}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"batch file must be a non-empty JSON list: {path}")
    return payload


def _write_batch_result(path: Path, results: list) -> None:
    destination = Path(path).with_suffix(".result.json")
    destination.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(f"PILLAR CAD FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
