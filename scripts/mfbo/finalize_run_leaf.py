"""Validate an existing mfbo extract and record that the leaf is complete.

Does not launch Fluent. A run leaf is finalized only when the extract
already wrote the report files and the run manifest. The column check is
``extract_profile.critical_summary_columns("mfbo")``. The status write sets
``extract_profile`` and ``extract_validation`` on that manifest.

A leaf that is missing any required file is refused.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import batch_report_extract
from ro.extract_profile import PROFILE_MFBO
from ro.manifest import ManifestError, update_run_manifest_fields
from ro.mfbo_adapter import is_archived_leaf_name, resolve_data_root
from ro.paths import using_data_root

# Files the mfbo extract writes before it returns 0. The wide summary is
# the column source; the other four are the reports the host extract saved.
REQUIRED_REPORT_FILES = (
    "summary_metrics_wide.csv",
    "mass_balance.csv",
    "pressure_report.csv",
    "raw_report_values.json",
    "report_extract_timing.json",
)
EXTRACT_VALIDATION_PASS = "PASS"


def missing_required_files(run_directory: Path) -> list[str]:
    """Report files and the run manifest that a complete extract must have."""
    directory = Path(run_directory)
    reports = directory / "post" / "reports"
    missing = [
        name for name in REQUIRED_REPORT_FILES if not (reports / name).is_file()
    ]
    if not (directory / "manifest.json").is_file():
        missing.append("manifest.json")
    return missing


def read_summary_profile(summary_csv: Path) -> str | None:
    """Return the wide-summary ``profile`` cell, or None when it is absent."""
    with Path(summary_csv).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(
            f"summary_metrics_wide.csv must have one data row, got {len(rows)}: "
            f"{summary_csv}"
        )
    value = rows[0].get("profile")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def finalize_run_leaf(run_directory: Path, data_root: Path) -> None:
    """Validate one complete mfbo extract and write the pass status.

    The manifest rewrite uses ``data_root``, not the process ``RO_DATA_ROOT``.
    Raises FileNotFoundError when a required file is missing, and ValueError
    when the summary is not an mfbo extract or the column check fails.
    """
    directory = Path(run_directory)
    missing = missing_required_files(directory)
    if missing:
        raise FileNotFoundError(
            f"Refusing to finalize {directory}: missing {missing}."
        )
    summary = directory / "post" / "reports" / "summary_metrics_wide.csv"
    profile = read_summary_profile(summary)
    if profile != PROFILE_MFBO:
        raise ValueError(
            f"Refusing to finalize {directory}: summary profile is {profile!r}, "
            f"expected {PROFILE_MFBO!r}."
        )
    valid, message = batch_report_extract.validate_summary_wide_csv(
        summary, profile=PROFILE_MFBO
    )
    if not valid:
        raise ValueError(f"FAILED_METRIC_VALIDATION: {message}")
    with using_data_root(data_root):
        update_run_manifest_fields(
            directory,
            {
                "extract_profile": PROFILE_MFBO,
                "extract_validation": EXTRACT_VALIDATION_PASS,
            },
        )


def iter_run_leaves(data_root: Path):
    """Yield run directories under ``data_root/runs`` that have a manifest."""
    runs = Path(data_root) / "runs"
    if not runs.is_dir():
        raise FileNotFoundError(f"runs directory not found: {runs}")
    for manifest in sorted(runs.rglob("manifest.json")):
        leaf = manifest.parent
        relative = leaf.relative_to(runs)
        if any(is_archived_leaf_name(part) for part in relative.parts):
            continue
        yield leaf


def _resolve_leaf(data_root: Path, text: str) -> Path:
    raw = Path(text)
    leaf = raw if raw.is_absolute() else data_root / text
    try:
        leaf.resolve().relative_to(data_root.resolve())
    except ValueError as exc:
        raise ValueError(f"run leaf is not under {data_root}: {text}") from exc
    return leaf


def _classify(leaf: Path) -> tuple[str, str]:
    """Return (action, detail). action is skip, refuse, or finalize."""
    summary = leaf / "post" / "reports" / "summary_metrics_wide.csv"
    if not summary.is_file():
        return "skip", "no summary_metrics_wide.csv"
    try:
        profile = read_summary_profile(summary)
    except (OSError, ValueError) as exc:
        return "refuse", str(exc)
    if profile != PROFILE_MFBO:
        return "skip", f"profile {profile!r}"
    missing = missing_required_files(leaf)
    if missing:
        return "refuse", f"missing {missing}"
    return "finalize", ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate complete mfbo extracts and write extract_validation "
            "on the run manifest. Does not launch Fluent."
        ),
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--run-leaf",
        action="append",
        default=None,
        help="Run directory to finalize. Repeatable. Default: walk runs/.",
    )
    args = parser.parse_args(argv)
    try:
        root = resolve_data_root(args.data_root)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.run_leaf:
        try:
            leaves = [_resolve_leaf(root, text) for text in args.run_leaf]
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        explicit = True
    else:
        try:
            leaves = list(iter_run_leaves(root))
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        explicit = False

    finalized = 0
    refused = 0
    for leaf in leaves:
        action, detail = _classify(leaf)
        if action == "skip":
            if explicit:
                print(f"REFUSE {leaf}: {detail}", flush=True)
                refused += 1
            else:
                print(f"skip {leaf}: {detail}", flush=True)
            continue
        if action == "refuse":
            print(f"REFUSE {leaf}: {detail}", flush=True)
            refused += 1
            continue
        try:
            finalize_run_leaf(leaf, root)
        except (OSError, ValueError, ManifestError) as exc:
            print(f"REFUSE {leaf}: {exc}", flush=True)
            refused += 1
            continue
        print(f"finalized {leaf}", flush=True)
        finalized += 1

    print(f"finalized: {finalized}", flush=True)
    if refused or finalized == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
