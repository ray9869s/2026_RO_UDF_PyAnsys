"""Repair a run manifest whose final pair is a saved ``*_iter_<N>`` pair.

A continue that is stopped mid-chunk (D2450_a60, main3 stopped at
2026-11-17) leaves the manifest at ``stop_reason=RUNNING`` with no
``final_case_sha256`` / ``final_data_sha256``, while the final pair on
disk is still the pair the last finished chunk wrote and copied to
``*_iter_<N>``. The re-extract emitter skips such a leaf.

This tool proves that by hash, not by size or timestamp:

1. hash the final pair;
2. find the saved ``{geo}_{run}_iter_<N>.cas/.dat.h5`` pair with the same
   hashes (sizes are compared first, so only candidates are hashed);
3. take the stop reason the continue record logged for chunk ``N``
   (``continue.chunks[].iterations == N``), or ``--stop-reason`` when the
   record has none.

It then records ``final_case_sha256``, ``final_data_sha256``,
``iterations_run=N``, that ``stop_reason``, and
``final_provenance="repaired_from_iter_<N>"`` (details in
``final_repair``). The gate stays cleared, so the emitter sees the leaf as
stale (its ``post/reports`` is from another final) and re-extracts it.

Dry-run by default; ``--apply`` writes the manifest. Refuses when no
saved pair matches, when more than one does, when the leaf has a BROKEN
marker, or when the manifest already has final hashes that differ from
the files. Never touches case/data files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_SRC = _SCRIPTS.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.atomic_reports import write_manifest_json  # noqa: E402
from ro.paths import leaf_data_root, refuse_production_root, using_data_root  # noqa: E402
from ro.report_binding import BROKEN_MARKER, GATE_GENERATION_FIELD  # noqa: E402
from ro.solver_common import (  # noqa: E402
    FINAL_CASE_SHA256_FIELD,
    FINAL_DATA_SHA256_FIELD,
    STOP_REASON_VALUES,
    sha256_file,
)

PROVENANCE_FIELD = "final_provenance"
REPAIR_FIELD = "final_repair"


class RepairError(ValueError):
    pass


def leaf_identity(leaf):
    leaf = Path(leaf).resolve()
    if leaf_data_root(leaf) is None:
        raise RepairError(f"Not a <root>/runs/<family>/<geo>/<mesh>/<run> leaf: {leaf}")
    return leaf, leaf.parent.parent.name, leaf.name


def final_pair(leaf, geo, run):
    return tuple(leaf / f"{geo}_{run}_final.{ext}.h5" for ext in ("cas", "dat"))


def saved_pairs(leaf, geo, run):
    """``{N: (case, data)}`` for every complete ``*_iter_<N>`` pair."""
    pattern = re.compile(rf"^{re.escape(geo)}_{re.escape(run)}_iter_(\d{{6}})\.cas\.h5$")
    pairs = {}
    for path in sorted(leaf.iterdir()):
        match = pattern.fullmatch(path.name)
        if not match:
            continue
        data = path.with_name(path.name[: -len(".cas.h5")] + ".dat.h5")
        if data.is_file():
            pairs[int(match.group(1))] = (path, data)
    return pairs


def chunk_stop_reason(manifest, iteration):
    record = manifest.get("continue")
    if not isinstance(record, dict):
        return None
    reasons = [
        item.get("stop_reason")
        for item in record.get("chunks") or []
        if isinstance(item, dict) and item.get("iterations") == iteration
    ]
    return reasons[-1] if reasons else None


def plan_repair(leaf, *, stop_reason=None, hasher=sha256_file, report=print):
    """Return ``(leaf, updated_manifest, iteration)``; raise RepairError to refuse."""
    leaf, geo, run = leaf_identity(leaf)
    if (leaf / BROKEN_MARKER).exists():
        raise RepairError(f"Leaf has a {BROKEN_MARKER} marker; restore it by hand first: {leaf}")
    manifest_path = leaf / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RepairError(f"run manifest is not a JSON object: {manifest_path}")
    case, data = final_pair(leaf, geo, run)
    for path in (case, data):
        if not path.is_file() or path.stat().st_size <= 0:
            raise RepairError(f"Missing or empty final: {path}")

    sizes = (case.stat().st_size, data.stat().st_size)
    candidates = {
        n: pair
        for n, pair in saved_pairs(leaf, geo, run).items()
        if (pair[0].stat().st_size, pair[1].stat().st_size) == sizes
    }
    report(f"Final pair: {case.name} {sizes[0]} B, {data.name} {sizes[1]} B")
    report(f"Saved pairs with the same sizes: {sorted(candidates) or 'none'}")
    if not candidates:
        raise RepairError(f"No saved *_iter_<N> pair has the final pair's sizes: {leaf}")
    final_hashes = (hasher(case), hasher(data))
    report(f"Final sha256: case {final_hashes[0][:12]} data {final_hashes[1][:12]}")

    recorded = (manifest.get(FINAL_CASE_SHA256_FIELD), manifest.get(FINAL_DATA_SHA256_FIELD))
    if any(recorded):
        if recorded == final_hashes:
            raise RepairError(f"Manifest already records this final; nothing to repair: {leaf}")
        raise RepairError(
            "Manifest records final hashes that differ from the files "
            f"(case {str(recorded[0])[:12]} data {str(recorded[1])[:12]}); not a "
            f"missing-hash leaf, refusing: {leaf}"
        )

    matches = []
    for n in sorted(candidates, reverse=True):
        pair_hashes = (hasher(candidates[n][0]), hasher(candidates[n][1]))
        same = pair_hashes == final_hashes
        report(f"  iter {n:06d}: case {pair_hashes[0][:12]} data {pair_hashes[1][:12]} "
               f"{'MATCH' if same else 'differs'}")
        if same:
            matches.append(n)
    if not matches:
        raise RepairError(f"No saved *_iter_<N> pair equals the final pair by hash: {leaf}")
    if len(matches) > 1:
        raise RepairError(f"More than one saved pair equals the final ({matches}); refusing: {leaf}")
    iteration = matches[0]

    logged = chunk_stop_reason(manifest, iteration)
    if stop_reason is None:
        stop_reason = logged
        if stop_reason is None:
            raise RepairError(
                f"The continue record has no stop reason for iteration {iteration}; "
                "pass --stop-reason (one of: " + ", ".join(STOP_REASON_VALUES) + ")."
            )
    elif logged is not None and logged != stop_reason:
        raise RepairError(
            f"--stop-reason {stop_reason} differs from the continue record's "
            f"{logged} for iteration {iteration}."
        )
    if stop_reason not in STOP_REASON_VALUES:
        raise RepairError(f"stop reason must be a solver stop reason, got {stop_reason!r}.")

    provenance = f"repaired_from_iter_{iteration}"
    updated = dict(manifest)
    updated[FINAL_CASE_SHA256_FIELD] = final_hashes[0]
    updated[FINAL_DATA_SHA256_FIELD] = final_hashes[1]
    updated["iterations_run"] = iteration
    updated["stop_reason"] = stop_reason
    updated["convergence_quality"] = None
    updated[GATE_GENERATION_FIELD] = None
    updated[PROVENANCE_FIELD] = provenance
    updated[REPAIR_FIELD] = {
        "provenance": provenance,
        "iteration": iteration,
        "snapshot_case": candidates[iteration][0].name,
        "snapshot_data": candidates[iteration][1].name,
        "stop_reason_source": "continue chunk" if logged is not None else "--stop-reason",
        "previous_stop_reason": manifest.get("stop_reason"),
        "previous_iterations_run": manifest.get("iterations_run"),
        "repaired_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return leaf, updated, iteration


def apply_repair(leaf, updated):
    """Schema-checked write when the original manifest validates, else plain JSON."""
    from ro.manifest import ManifestError, read_run_manifest, write_run_manifest

    refuse_production_root(leaf)
    with using_data_root(leaf_data_root(leaf)):
        try:
            read_run_manifest(leaf)
        except (ManifestError, ValueError) as exc:
            print(f"Original manifest does not pass the schema check ({exc}); writing plain JSON.")
            return write_manifest_json(leaf / "manifest.json", updated)
        return write_run_manifest(leaf, updated)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-leaf", required=True)
    parser.add_argument("--stop-reason", default=None,
                        help="Only when the continue record has none for the matched iteration.")
    parser.add_argument("--apply", action="store_true", help="Write the manifest (default: dry run).")
    args = parser.parse_args(argv)
    try:
        leaf, updated, iteration = plan_repair(args.run_leaf, stop_reason=args.stop_reason)
    except RepairError as exc:
        print(f"REFUSED: {exc}")
        return 2
    summary = {key: updated[key] for key in (
        FINAL_CASE_SHA256_FIELD, FINAL_DATA_SHA256_FIELD, "iterations_run", "stop_reason",
        PROVENANCE_FIELD,
    )}
    print("Manifest fields to record:\n" + json.dumps(summary, indent=2))
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply.")
        return 0
    path = apply_repair(leaf, updated)
    print(f"REPAIRED {path} ({PROVENANCE_FIELD}=repaired_from_iter_{iteration}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
