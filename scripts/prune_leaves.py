"""Report run-leaf disk use and drop checkpoints from leaves that passed.

Per data root, print the space in checkpoint case/data files, setup
cases, and final pairs. A leaf is eligible when its run manifest
``convergence_quality`` is PASS, the wide CSV validates, and
``post/reports/extract_source.json`` is present. The final pair must
still be on disk. Dry-run is the default. ``--apply`` deletes the
checkpoint files and ``{geo}_{run}_setup.cas.h5`` on eligible leaves.
Final pairs, ``post/``, logs, and the manifest stay.

Does not launch Fluent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_report_extract
from ro.convergence_quality import QUALITY_PASS
from ro.leaf_disk import (
    checkpoint_files,
    convergence_gate,
    extract_record_present,
    final_pair_files,
    final_pair_ready,
    format_bytes,
    releasable_files,
    setup_case_files,
    unlink_files,
)


def iter_run_leaves(data_root):
    """Yield ``(family, geo_id, mesh_id, run_id, leaf)`` under ``runs/``."""
    runs = Path(data_root) / "runs"
    if not runs.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs}.")
    leaves = []
    for family_dir in runs.iterdir():
        if not family_dir.is_dir():
            continue
        for geo_dir in family_dir.iterdir():
            if not geo_dir.is_dir():
                continue
            for mesh_dir in geo_dir.iterdir():
                if not mesh_dir.is_dir():
                    continue
                for run_dir in mesh_dir.iterdir():
                    if not run_dir.is_dir():
                        continue
                    leaves.append(
                        (
                            family_dir.name,
                            geo_dir.name,
                            mesh_dir.name,
                            run_dir.name,
                            run_dir,
                        )
                    )
    leaves.sort(key=lambda item: item[:4])
    return leaves


def _bytes(paths) -> int:
    return sum(path.stat().st_size for path in paths)


def _count(number: int, singular: str, plural: str) -> str:
    word = singular if number == 1 else plural
    return f"{number} {word}"


def leaf_is_eligible(leaf, geo: str, run: str, validator) -> bool:
    """PASS manifest, a finished extract, and a final pair still present."""
    if convergence_gate(leaf) != QUALITY_PASS:
        return False
    if not extract_record_present(leaf):
        return False
    if not final_pair_ready(leaf, geo, run):
        return False
    summary = Path(leaf) / "post" / "reports" / "summary_metrics_wide.csv"
    ok, _message = validator(summary)
    return bool(ok)


def report_root(data_root, *, apply: bool, validator=None) -> tuple[str, list[str]]:
    """Summarize one data root. ``--apply`` unlinks eligible checkpoint files."""
    if not isinstance(apply, bool):
        raise TypeError(f"apply must be a bool, got {apply!r}.")
    if validator is None:
        validator = batch_report_extract.validate_summary_wide_csv
    checkpoints = []
    setup = []
    finals = []
    eligible = []
    eligible_leaves = 0
    kept = []
    for _family, geo, _mesh, run, leaf in iter_run_leaves(data_root):
        leaf_checkpoints = checkpoint_files(leaf, geo, run)
        leaf_setup = setup_case_files(leaf, geo, run)
        checkpoints.extend(leaf_checkpoints)
        setup.extend(leaf_setup)
        finals.extend(final_pair_files(leaf, geo, run))
        releasable = releasable_files(leaf, geo, run)
        if releasable and leaf_is_eligible(leaf, geo, run, validator):
            eligible.extend(releasable)
            eligible_leaves += 1
        else:
            kept.extend(releasable)
    checkpoint_bytes = _bytes(checkpoints)
    setup_bytes = _bytes(setup)
    final_bytes = _bytes(finals)
    eligible_bytes = _bytes(eligible)
    kept_bytes = _bytes(kept)
    errors: list[str] = []
    removed = []
    removed_bytes = 0
    if apply and eligible:
        sizes = {path: path.stat().st_size for path in eligible}
        removed, errors = unlink_files(eligible)
        removed_bytes = sum(sizes[path] for path in removed)
    lines = [
        f"data_root: {data_root}",
        f"checkpoints: {_count(len(checkpoints), 'file', 'files')}, "
        f"{format_bytes(checkpoint_bytes)}",
        f"setup: {_count(len(setup), 'file', 'files')}, "
        f"{format_bytes(setup_bytes)}",
        f"final: {_count(len(finals), 'file', 'files')}, "
        f"{format_bytes(final_bytes)}",
        f"eligible: {_count(eligible_leaves, 'leaf', 'leaves')}, "
        f"{format_bytes(eligible_bytes)}",
        f"kept: {format_bytes(kept_bytes)}",
    ]
    if apply:
        lines.append(
            f"removed: {_count(len(removed), 'file', 'files')}, "
            f"{format_bytes(removed_bytes)}"
        )
    else:
        lines.append("dry-run")
    for error in errors:
        lines.append(f"delete failed: {error}")
    return "\n".join(lines), errors


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        action="append",
        required=True,
        help="Data root to report. Repeat for more than one.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Delete checkpoint files and setup cases on PASS leaves "
            "whose extract succeeded. Default prints the plan only."
        ),
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    code = 0
    blocks = []
    for data_root in args.data_root:
        try:
            text, errors = report_root(data_root, apply=args.apply)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            code = 1
            continue
        blocks.append(text)
        if errors:
            code = 1
    if blocks:
        print("\n".join(blocks))
    return code


if __name__ == "__main__":
    sys.exit(main())
