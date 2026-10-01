"""Re-extract explicit run leaves outside production ``C:/ro_data``.

Keeps the previous ``post/reports/`` by renaming it to
``post/reports_prev_<UTC>/``. Does not delete it. Extraction goes through
``mfbo._common.launch_extract``, which is the production worker launcher.
``RO_DATA_ROOT`` on the child is the leaf's data root (the directory that
contains ``runs/``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mfbo._common as mfbo_common
from ro.manifest import read_run_manifest
from ro.paths import RUN_ID_RE

_PRODUCTION_ROOTS = ("c:/ro_data", "/mnt/c/ro_data")


def _folded(path) -> str:
    return str(path).replace("\\", "/").casefold().rstrip("/")


def _under_production(path) -> bool:
    folded = _folded(path)
    return any(
        folded == root or folded.startswith(root + "/")
        for root in _PRODUCTION_ROOTS
    )


def _is_windows_absolute(text: str) -> bool:
    return len(text) >= 3 and text[1] == ":" and text[2] == "/"


def parse_run_leaf(value):
    """Return (leaf, data_root, family, geo_id, mesh_id, run_id).

    Refuses the production data root and anything stored under it.
    """
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"run leaf path is required, got {value!r}.")
    text = str(value).strip().replace("\\", "/")
    if _under_production(text):
        raise ValueError(f"Refusing a run under production C:/ro_data: {value}")
    if _is_windows_absolute(text):
        leaf = Path(text)
    else:
        leaf = Path(value).expanduser()
        if not leaf.is_absolute():
            raise ValueError(f"run leaf must be absolute, got {value!r}.")
        leaf = leaf.resolve()
        if _under_production(leaf):
            raise ValueError(f"Refusing a run under production C:/ro_data: {value}")
    if len(leaf.parents) < 5 or leaf.parents[3].name != "runs":
        raise ValueError(
            "run leaf must be <data-root>/runs/<family>/<geo_id>/<mesh_id>/<run_id>, "
            f"got {value!r}."
        )
    run_id = leaf.name
    mesh_id = leaf.parent.name
    geo_id = leaf.parents[1].name
    family = leaf.parents[2].name
    data_root = leaf.parents[4]
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(
            f"run id must match {RUN_ID_RE.pattern}, got {run_id!r}."
        )
    if _under_production(data_root):
        raise ValueError(f"Refusing a run under production C:/ro_data: {value}")
    return leaf, data_root, family, geo_id, mesh_id, run_id


def archive_reports(run_leaf, *, now=None):
    """Rename ``post/reports`` to ``post/reports_prev_<UTC>``. Never deletes.

    Returns the archive path, or None when there is no reports directory.
    """
    reports = Path(run_leaf) / "post" / "reports"
    if not reports.exists():
        return None
    if not reports.is_dir():
        raise RuntimeError(f"post/reports is not a directory: {reports}")
    moment = now if now is not None else datetime.now(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    dest = reports.parent / f"reports_prev_{stamp}"
    if dest.exists():
        raise FileExistsError(
            f"Refusing to replace existing report archive: {dest}"
        )
    reports.rename(dest)
    return dest


def operating_point_case(data_root, run_leaf):
    """Inlet velocity and outlet pressure from the run manifest."""

    def _read():
        payload = read_run_manifest(run_leaf)
        return {
            "inlet_velocity_value": payload["u_target_ms"],
            "outlet_gauge_pressure": payload["p_gauge_pa"],
        }

    return mfbo_common.call_with_data_root(data_root, _read)


def reextract_leaf(run_leaf_value):
    """Archive reports, then run the production extract launcher."""
    leaf, data_root, _family, geo_id, mesh_id, run_id = parse_run_leaf(
        run_leaf_value
    )
    archive_reports(leaf)
    case = operating_point_case(data_root, leaf)
    return mfbo_common.launch_extract(
        data_root,
        leaf,
        case,
        geo_id=geo_id,
        mesh_id=mesh_id,
        run_id=run_id,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-extract explicit run leaves. Refuses C:/ro_data. "
            "Renames post/reports to post/reports_prev_<UTC>/."
        )
    )
    parser.add_argument(
        "run_leaves",
        nargs="+",
        help="Absolute run leaf paths (.../runs/<family>/<geo>/<mesh>/<run_id>).",
    )
    args = parser.parse_args(argv)
    code = 0
    for raw in args.run_leaves:
        result = reextract_leaf(raw)
        if result.returncode != 0:
            code = result.returncode
    return code


if __name__ == "__main__":
    sys.exit(main())
