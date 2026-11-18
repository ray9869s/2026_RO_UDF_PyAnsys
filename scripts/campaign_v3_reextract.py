"""Emit re-extract jobs for campaign v3 u0p2 finals; run one as a worker.

Importing or emitting never launches Fluent. Archived leaves and iteration
snapshots are excluded. A leaf is emitted when it is PASS or when its
``post/reports`` is not bound to the current final (``ro.report_binding``:
a continued leaf, or a continue stopped mid-way with no gate). Each worker
checks the final pair against the manifest hashes before archiving
post/reports through the existing re-extract driver.

``--snapshot GEO_ID:N`` (repeatable, with ``--emit-queue``) emits jobs that
re-extract the saved ``*_iter_<N>`` pair into
``post/reports_iter<N>_meanfirst/``. Those jobs never touch post/reports or
the run manifest gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

from ro.report_binding import BROKEN_MARKER, report_binding_reason
from ro.solver_common import (
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    sha256_file,
)

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import campaign_v3 as campaign
from mfbo import reextract_runs
from ro.campaign_geo_ids import CAMPAIGN_GEO_ID_ORDER, family_for_geo_id
from ro.paths import MESH_ID_RE

NEW_CP_COLUMNS = (
    "cp_meanfirst_live_window_avg", "cp_meanfirst_window_avg", "cp_ref_area_live",
)


def read_wide(directory, dirname="reports"):
    path = Path(directory) / "post" / dirname / "summary_metrics_wide.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"Expected one summary row: {path}")
    return rows[0]


def _final_pair(leaf, geo, run):
    return tuple(leaf / f"{geo}_{run}_final.{extension}.h5" for extension in ("cas", "dat"))


def require_final_hashes(leaf, geo, run, manifest):
    """The final pair on disk is the pair the manifest published."""
    case, data = _final_pair(leaf, geo, run)
    for field, path in ((FINAL_CASE_SHA256_FIELD, case), (FINAL_DATA_SHA256_FIELD, data)):
        expected = manifest.get(field)
        if not expected:
            raise ValueError(f"Manifest has no {field}: {leaf}")
        if sha256_file(path) != expected:
            raise ValueError(f"{path.name} does not match manifest {field}: {leaf}")


def _leaf_identity(leaf, root):
    relative = leaf.relative_to(root)
    parts = relative.parts
    if len(parts) != 5 or parts[0] != "runs" or parts[4] != "u0p2_p6M":
        raise ValueError(f"Not a campaign u0p2 final leaf: {leaf}")
    _, family, geo, mesh, run = parts
    if geo not in CAMPAIGN_GEO_ID_ORDER or family_for_geo_id(geo) != family:
        raise ValueError(f"Not a campaign geometry/family: {leaf}")
    if MESH_ID_RE.fullmatch(mesh) is None:
        raise ValueError(f"Invalid mesh id: {mesh}")
    return family, geo, mesh, run


def require_reextract_final(leaf, root, *, verify_hashes=True):
    """PASS or stale reports, a complete final pair, matching identities.

    ``verify_hashes`` hashes the final pair against the manifest; the
    emitter skips it (cheap scan), the worker does it before Fluent.
    """
    leaf, root = Path(leaf).resolve(), Path(root).resolve()
    family, geo, mesh, run = _leaf_identity(leaf, root)
    if (leaf / BROKEN_MARKER).exists():
        raise ValueError(f"Leaf has a {BROKEN_MARKER} marker; restore it by hand first: {leaf}")
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    stale = report_binding_reason(leaf)
    if manifest.get("convergence_quality") != "PASS" and stale is None:
        raise ValueError(f"Final leaf is neither PASS nor stale: {leaf}")
    for key, expected in (("family", family), ("geo_id", geo), ("mesh_id", mesh), ("run_id", run)):
        if key in manifest and manifest[key] != expected:
            raise ValueError(f"Manifest {key} does not match final leaf: {leaf}")
    for path in _final_pair(leaf, geo, run):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Missing or empty final solution: {path}")
    for field in (FINAL_CASE_SHA256_FIELD, FINAL_DATA_SHA256_FIELD):
        if not manifest.get(field):
            raise ValueError(f"Manifest has no {field}: {leaf}")
    if verify_hashes:
        require_final_hashes(leaf, geo, run, manifest)
    return leaf


def require_pass_final(leaf, root):
    leaf, root = Path(leaf).resolve(), Path(root).resolve()
    relative = leaf.relative_to(root)
    parts = relative.parts
    if len(parts) != 5 or parts[0] != "runs" or parts[4] != "u0p2_p6M":
        raise ValueError(f"Not a campaign u0p2 final leaf: {leaf}")
    _, family, geo, mesh, run = parts
    if geo not in CAMPAIGN_GEO_ID_ORDER or family_for_geo_id(geo) != family:
        raise ValueError(f"Not a campaign geometry/family: {leaf}")
    if MESH_ID_RE.fullmatch(mesh) is None:
        raise ValueError(f"Invalid mesh id: {mesh}")
    manifest = json.loads((leaf / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("convergence_quality") != "PASS":
        raise ValueError(f"Final leaf is not PASS: {leaf}")
    for key, expected in (("family", family), ("geo_id", geo), ("mesh_id", mesh), ("run_id", run)):
        if key in manifest and manifest[key] != expected:
            raise ValueError(f"Manifest {key} does not match final leaf: {leaf}")
    for extension in ("cas", "dat"):
        path = leaf / f"{geo}_{run}_final.{extension}.h5"
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Missing or empty final solution: {path}")
    read_wide(leaf)
    return leaf


def validate_meanfirst_summary(leaf, dirname="reports"):
    wide = read_wide(leaf, dirname)
    values = {}
    for key in (*NEW_CP_COLUMNS, "c_b_window_mol_m3", "cm_area_mean_window_live",
                "live_membrane_area_window_m2", "live_exclusion_threshold_mol_m3",
                "cpc_window_avg_area"):
        try:
            number = float(wide[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Missing/non-numeric mean-first input {key}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Non-finite mean-first input {key}")
        values[key] = number
    if values["live_membrane_area_window_m2"] <= 0:
        raise ValueError("Non-positive live membrane area")
    if values["c_b_window_mol_m3"] <= values["cp_ref_area_live"]:
        raise ValueError("Non-positive mean-first live denominator")
    expected = ((values["cm_area_mean_window_live"] - values["cp_ref_area_live"])
                / (values["c_b_window_mol_m3"] - values["cp_ref_area_live"]))
    if not math.isclose(values[NEW_CP_COLUMNS[0]], expected, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("Saved mean-first live CP does not match its saved inputs")
    if not math.isclose(values[NEW_CP_COLUMNS[1]], values["cpc_window_avg_area"], rel_tol=1e-9):
        raise ValueError("All-faces mean-first CP differs from cpc_window_avg_area")
    if wide.get("live_exclusion_rule") != "c_p >= min(c_0, c_b)":
        raise ValueError("Missing or unexpected live exclusion rule")
    return values


def pass_final_jobs(root, *, report=print):
    """PASS finals plus every final whose post/reports is stale.

    A leaf that is neither is skipped silently. A stale or PASS leaf whose
    final pair or hashes are incomplete is reported and skipped.
    """
    root = campaign.mesh_study.resolve_data_root(str(root))
    jobs = []
    for manifest_path in sorted((root / "runs").glob("*/*/*/u0p2_p6M/manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale = report_binding_reason(manifest_path.parent)
        if payload.get("convergence_quality") != "PASS" and stale is None:
            continue
        try:
            leaf = require_reextract_final(manifest_path.parent, root, verify_hashes=False)
        except ValueError as exc:
            report(f"SKIP {manifest_path.parent}: {exc}")
            continue
        if stale is not None:
            report(f"STALE {leaf}: {stale}")
        family, geo, mesh, run = leaf.parts[-4:]
        jobs.append(campaign._job(
            f"reextract-{geo}-{mesh}-{run}",
            ["{python}", "scripts/campaign_v3_reextract.py", "--data-root", str(root),
             "--run-leaf", str(leaf)],
        ))
    return jobs


def snapshot_dirname(iteration):
    return f"reports_iter{int(iteration)}_meanfirst"


def snapshot_pair(leaf, geo, run, iteration):
    stem = f"{geo}_{run}_iter_{int(iteration):06d}"
    return leaf / f"{stem}.cas.h5", leaf / f"{stem}.dat.h5"


def parse_snapshot(value):
    geo, sep, iteration = str(value).partition(":")
    if not sep or geo not in CAMPAIGN_GEO_ID_ORDER or not iteration.isdigit() or int(iteration) < 1:
        raise ValueError(f"--snapshot must be GEO_ID:N with a campaign geo id, got {value!r}.")
    return geo, int(iteration)


def require_snapshot_leaf(leaf, root, iteration):
    leaf, root = Path(leaf).resolve(), Path(root).resolve()
    _family, geo, _mesh, run = _leaf_identity(leaf, root)
    for path in snapshot_pair(leaf, geo, run, iteration):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Missing or empty snapshot: {path}")
    return leaf


def snapshot_jobs(root, snapshots):
    """One job per GEO_ID:N. Exactly one u0p2 leaf must hold that snapshot."""
    root = campaign.mesh_study.resolve_data_root(str(root))
    jobs = []
    for geo, iteration in snapshots:
        family = family_for_geo_id(geo)
        candidates = []
        for leaf in sorted((root / "runs" / family / geo).glob("*/u0p2_p6M")):
            run = leaf.name
            if all(path.is_file() for path in snapshot_pair(leaf, geo, run, iteration)):
                candidates.append(leaf)
        if len(candidates) != 1:
            raise ValueError(
                f"Snapshot {geo}:{iteration} must exist in exactly one u0p2 leaf, "
                f"found {[str(path) for path in candidates]}."
            )
        leaf = require_snapshot_leaf(candidates[0], root, iteration)
        mesh, run = leaf.parts[-2:]
        jobs.append(campaign._job(
            f"reextract-{geo}-{mesh}-{run}-iter{iteration}-meanfirst",
            ["{python}", "scripts/campaign_v3_reextract.py", "--data-root", str(root),
             "--run-leaf", str(leaf), "--snapshot-iteration", str(iteration)],
        ))
    return jobs


def reextract_snapshot(leaf, root, iteration):
    """Extract ``*_iter_<N>`` into post/reports_iter<N>_meanfirst/."""
    leaf = require_snapshot_leaf(leaf, root, iteration)
    leaf, data_root, _family, geo, mesh, run = reextract_runs.parse_run_leaf(leaf)
    case_file, data_file = snapshot_pair(leaf, geo, run, iteration)
    case = reextract_runs.operating_point_case(data_root, leaf)
    return reextract_runs.mfbo_common.launch_extract(
        data_root,
        leaf,
        case,
        geo_id=geo,
        mesh_id=mesh,
        run_id=run,
        profile="full",
        report_dirname=snapshot_dirname(iteration),
        final_case=case_file,
        final_data=data_file,
        snapshot_iteration=iteration,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=campaign.DATA_ROOT)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--emit-queue")
    actions.add_argument("--run-leaf")
    parser.add_argument(
        "--snapshot", action="append", default=[], metavar="GEO_ID:N",
        help="With --emit-queue: emit only snapshot jobs for these saved *_iter_<N> pairs.",
    )
    parser.add_argument(
        "--snapshot-iteration", type=int, default=None,
        help="With --run-leaf: extract *_iter_<N> into post/reports_iter<N>_meanfirst/.",
    )
    args = parser.parse_args(argv)
    root = campaign.mesh_study.resolve_data_root(args.data_root)
    if args.snapshot and not args.emit_queue:
        raise ValueError("--snapshot goes with --emit-queue.")
    if args.snapshot_iteration is not None and not args.run_leaf:
        raise ValueError("--snapshot-iteration goes with --run-leaf.")
    if args.emit_queue:
        destination = Path(args.emit_queue)
        if destination.exists():
            raise FileExistsError(f"Refusing to replace queue: {destination}")
        if args.snapshot:
            jobs = snapshot_jobs(root, [parse_snapshot(value) for value in args.snapshot])
            kind = "snapshot"
        else:
            jobs = pass_final_jobs(root)
            kind = "PASS/stale-final"
        if not jobs:
            raise ValueError("No observed campaign u0p2 PASS or stale final leaves; no queue written.")
        campaign.write_queue(destination, jobs)
        print(f"Wrote {len(jobs)} {kind} re-extract jobs to {destination}")
        return 0
    if args.snapshot_iteration is not None:
        result = reextract_snapshot(args.run_leaf, root, args.snapshot_iteration)
        if result.returncode:
            return result.returncode
        validate_meanfirst_summary(args.run_leaf, snapshot_dirname(args.snapshot_iteration))
        return 0
    leaf = require_reextract_final(args.run_leaf, root)
    result = reextract_runs.reextract_leaf(leaf, profile="full")
    if result.returncode:
        return result.returncode
    validate_meanfirst_summary(leaf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
