"""Emit final-only re-extract jobs from observed campaign v3 PASS leaves.

Importing or emitting never launches Fluent. Archived leaves and iteration
snapshots are excluded. Each worker rechecks PASS and the final pair before
archiving post/reports through the existing re-extract driver.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

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


def read_wide(directory):
    path = Path(directory) / "post" / "reports" / "summary_metrics_wide.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"Expected one summary row: {path}")
    return rows[0]


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


def validate_meanfirst_summary(leaf):
    wide = read_wide(leaf)
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


def pass_final_jobs(root):
    root = campaign.mesh_study.resolve_data_root(str(root))
    jobs = []
    for manifest_path in sorted((root / "runs").glob("*/*/*/u0p2_p6M/manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("convergence_quality") != "PASS":
            continue
        leaf = require_pass_final(manifest_path.parent, root)
        family, geo, mesh, run = leaf.parts[-4:]
        jobs.append(campaign._job(
            f"reextract-{geo}-{mesh}-{run}",
            ["{python}", "scripts/campaign_v3_reextract.py", "--data-root", str(root),
             "--run-leaf", str(leaf)],
        ))
    return jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=campaign.DATA_ROOT)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--emit-queue")
    actions.add_argument("--run-leaf")
    args = parser.parse_args(argv)
    root = campaign.mesh_study.resolve_data_root(args.data_root)
    if args.emit_queue:
        jobs = pass_final_jobs(root)
        if not jobs:
            raise ValueError("No observed campaign u0p2 PASS final leaves; no queue written.")
        destination = Path(args.emit_queue)
        if destination.exists():
            raise FileExistsError(f"Refusing to replace queue: {destination}")
        campaign.write_queue(destination, jobs)
        print(f"Wrote {len(jobs)} PASS-final re-extract jobs to {destination}")
        return 0
    leaf = require_pass_final(args.run_leaf, root)
    result = reextract_runs.reextract_leaf(leaf, profile="full")
    if result.returncode:
        return result.returncode
    validate_meanfirst_summary(leaf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
