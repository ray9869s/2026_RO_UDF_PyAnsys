"""Compare a full extract with a ``--profile mfbo`` re-extract of a copy.

Importing this module does not launch Fluent. ``--emit-queue`` writes a
job-queue JSON file and does not copy or extract. The queued command is
this script without ``--emit-queue``, so the host runs the copy, the
lightweight extract, and the column comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
_SCRIPTS = Path(__file__).resolve().parents[1]
for _path in (_SRC, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import mfbo._common as mfbo_common
from ro.extract_profile import (
    PROFILE_MFBO,
    compare_shared_summary_columns,
)
from ro.mfbo_fidelity_screen import write_queue

SUMMARY_NAME = "summary_metrics_wide.csv"
PARITY_NAME = "mfbo_profile_parity.json"


class ParityMismatch(RuntimeError):
    """Shared summary columns disagree beyond the relative tolerance."""

    def __init__(self, report):
        self.report = report
        columns = [item["column"] for item in report["mismatches"]]
        super().__init__(
            "MFBO profile parity failed for shared columns "
            f"{columns!r}."
        )


def summary_csv(run_leaf: Path) -> Path:
    return Path(run_leaf) / "post" / "reports" / SUMMARY_NAME


def read_wide_summary(path: Path) -> dict[str, str]:
    """One wide-summary row. Values stay as CSV text."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(
            f"Expected one summary row in {path}, found {len(rows)}."
        )
    return rows[0]


def queue_path_text(value) -> str:
    """Path text for a queue argv entry. Windows absolute paths stay as given."""
    text = str(value).strip().replace("\\", "/")
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        return text
    path = Path(value)
    if not path.is_absolute():
        path = path.resolve()
    return path.as_posix()


def parity_job_id(run_leaf: Path) -> str:
    slug = "".join(
        char if char.isalnum() or char in "._-" else "-"
        for char in Path(run_leaf).name
    ).strip("-")
    if not slug:
        raise ValueError(f"Run leaf name {run_leaf.name!r} has no job-id characters.")
    job_id = f"mfbo-profile-parity-{slug}"
    if len(job_id) > 80:
        job_id = job_id[:80].rstrip("-.")
    return job_id


def _run_identity(run_leaf):
    """``.../runs/<family>/<geo_id>/<mesh_id>/<run_id>`` identity."""
    leaf = Path(run_leaf)
    if len(leaf.parents) < 5 or leaf.parents[3].name != "runs":
        raise ValueError(
            "run leaf must be <data-root>/runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {run_leaf!r}."
        )
    return {
        "run_leaf": leaf,
        "source_root": leaf.parents[4],
        "family": leaf.parents[2].name,
        "geo_id": leaf.parents[1].name,
        "mesh_id": leaf.parent.name,
        "run_id": leaf.name,
    }


def _under_production_leaf(run_leaf) -> bool:
    text = str(run_leaf).strip().replace("\\", "/").casefold().rstrip("/")
    return (
        text == "c:/ro_data"
        or text.startswith("c:/ro_data/")
        or text == "/mnt/c/ro_data"
        or text.startswith("/mnt/c/ro_data/")
    )


def parity_copy_dir(data_root, run_leaf) -> Path:
    """Canonical run leaf under ``data_root``, where ``run_dir()`` expects it."""
    identity = _run_identity(run_leaf)
    return (
        Path(data_root)
        / "runs"
        / identity["family"]
        / identity["geo_id"]
        / identity["mesh_id"]
        / identity["run_id"]
    )


def _mesh_leaf(root, identity) -> Path:
    return (
        Path(root)
        / "meshes"
        / identity["family"]
        / identity["geo_id"]
        / identity["mesh_id"]
    )


def _drop_copied_reports(dest):
    import shutil

    reports = Path(dest) / "post" / "reports"
    if reports.exists():
        shutil.rmtree(reports)


def prepare_mfbo_profile_copy(run_leaf, data_root) -> Path:
    """Copy a full-extract leaf onto ``{data_root}/runs/...``.

    Production leaves use ``copy_production_for_reextract``. Any other leaf
    is copied, with its mesh leaf, to the same relative place under
    ``data_root``. ``read_run_manifest`` then matches ``run_dir()`` when
    ``RO_DATA_ROOT`` is that data root. The reference summary is left in
    place when the source and the destination are the same path.
    """
    from mfbo.diagnose_cp_max_hotspots import copy_or_reuse_tree
    from mfbo.reextract_runs import copy_production_for_reextract

    leaf = Path(run_leaf)
    source = summary_csv(leaf)
    if not source.is_file():
        raise FileNotFoundError(f"Full extract summary is missing: {source}")
    reference = read_wide_summary(source)
    profile = reference.get("profile", "")
    if profile not in ("", "full"):
        raise ValueError(
            "Reference summary is not a full extract "
            f"(profile={profile!r}): {source}"
        )
    if _under_production_leaf(leaf):
        dest = Path(copy_production_for_reextract(leaf, data_root))
    else:
        identity = _run_identity(leaf)
        dest = parity_copy_dir(data_root, leaf)
        if dest.resolve() == leaf.resolve():
            return dest
        if dest.exists():
            raise FileExistsError(f"MFBO profile copy already exists: {dest}")
        mesh_source = _mesh_leaf(identity["source_root"], identity)
        copy_or_reuse_tree(
            mesh_source,
            _mesh_leaf(data_root, identity),
            label="mesh leaf",
        )
        copy_or_reuse_tree(leaf, dest, label="run leaf")
    if dest.resolve() != leaf.resolve():
        _drop_copied_reports(dest)
    return dest


def build_parity_job(run_leaf, data_root) -> dict:
    """One queue job. The host runs this script, which launches the extract."""
    leaf_text = queue_path_text(run_leaf)
    root_text = queue_path_text(data_root)
    return {
        "id": parity_job_id(Path(leaf_text)),
        "argv": [
            "{python}",
            "scripts/mfbo/parity_mfbo_extract.py",
            "--run-leaf",
            leaf_text,
            "--data-root",
            root_text,
        ],
        "cwd": queue_path_text(Path(__file__).resolve().parents[2]),
        "env": {"RO_DATA_ROOT": root_text},
    }


def default_runner(data_root, copy_dir) -> int:
    """Launch ``pyfluent_report_extract.py --profile mfbo`` on the copy.

    The manifest is read inside ``operating_point_case``, which sets
    ``RO_DATA_ROOT`` to ``data_root`` first. ``run_dir()`` then matches the
    canonical copy.
    """
    from mfbo.reextract_runs import operating_point_case

    identity = _run_identity(copy_dir)
    case = operating_point_case(data_root, copy_dir)
    result = mfbo_common.launch_extract(
        data_root,
        copy_dir,
        case,
        geo_id=identity["geo_id"],
        mesh_id=identity["mesh_id"],
        run_id=identity["run_id"],
        profile=PROFILE_MFBO,
    )
    return int(result.returncode)


def _child_log(copy_dir: Path) -> Path:
    logs = sorted(Path(copy_dir).glob("*__extract_attempt*.log"))
    if logs:
        return logs[-1]
    return Path(copy_dir)


def run_parity(run_leaf, data_root, *, runner=None) -> dict:
    """Copy, re-extract, and compare. Raises when the extract or the compare fails."""
    leaf = Path(run_leaf)
    dest = prepare_mfbo_profile_copy(leaf, data_root)
    if runner is None:
        runner = default_runner
    code = runner(data_root, dest)
    if code != 0:
        raise RuntimeError(
            f"mfbo profile extract exited {code}. Child log: {_child_log(dest)}"
        )
    mfbo_csv = summary_csv(dest)
    if not mfbo_csv.is_file():
        raise FileNotFoundError(
            f"MFBO profile extract did not write {mfbo_csv}"
        )
    report = compare_shared_summary_columns(
        read_wide_summary(summary_csv(leaf)),
        read_wide_summary(mfbo_csv),
    )
    report["full_summary"] = str(summary_csv(leaf))
    report["mfbo_summary"] = str(mfbo_csv)
    report["copy"] = str(dest)
    destination = dest / "post" / "reports" / PARITY_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"MFBO profile parity: {destination}")
    if not report["ok"]:
        raise ParityMismatch(report)
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Re-extract a copied full-extract leaf with --profile mfbo "
            "and compare shared summary columns. "
            "--emit-queue writes a job queue and does not extract."
        ),
    )
    parser.add_argument("--run-leaf", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument(
        "--emit-queue",
        type=Path,
        help="Write a job-queue JSON file and do not copy or extract.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.emit_queue is not None:
        path = write_queue(
            args.emit_queue,
            [build_parity_job(args.run_leaf, args.data_root)],
        )
        print(path)
        return 0
    run_parity(args.run_leaf, args.data_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
