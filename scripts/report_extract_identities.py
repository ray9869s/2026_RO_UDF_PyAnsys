#!/usr/bin/env python3
"""Report R-12 extract identities on existing run CSV leaves. No Fluent.

Missing columns are N/A, not rejects, and are not defaulted to 0.
Does not add required schema / load-bearing fields. WSL without
RO_DATA_ROOT cannot scan C:/ro_data; run this on the workstation.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from ro.fluent_report_helpers import (
    active_cell_numbers_from_counts,
    area_mem_udm_identity_applies,
    area_mem_udm_identity_block_reason,
    csv_flux_three_key_applies,
    csv_flux_three_key_block_reason,
    spacer_dp_active_cell_sum_applies,
    spacer_dp_active_cell_sum_block_reason,
)
from ro.manifest import ManifestError, iter_run_manifests, read_mesh_manifest
from ro.paths import data_root, mesh_dir, runs_root


WIDE_CSV_RELATIVE = Path("post") / "reports" / "summary_metrics_wide.csv"


def _label(reason, *, applies: bool = True) -> str:
    if not applies:
        return "N/A"
    return "PASS" if reason is None else "REJECT"


def read_summary_wide_record(csv_path: Path):
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        return None, f"unreadable: {exc}"
    if not rows:
        return None, "summary_metrics_wide.csv has no data row"
    return rows[0], None


def report_run_leaf(manifest_path: Path, payload: dict, *, file=None):
    file = file or sys.stdout
    leaf = (
        f"{payload['family']}/{payload['geo_id']}/"
        f"{payload['mesh_id']}/{payload['run_id']}"
    )
    csv_path = manifest_path.parent / WIDE_CSV_RELATIVE
    if not csv_path.is_file():
        print(f"run   NO_CSV  {leaf}", file=file)
        return {"kind": "run", "leaf": leaf, "no_csv": True}

    record, error = read_summary_wide_record(csv_path)
    if error is not None:
        print(f"run   UNREADABLE  {leaf}: {error}", file=file)
        return {"kind": "run", "leaf": leaf, "unreadable": True}

    cells = []
    try:
        mesh_payload = read_mesh_manifest(
            mesh_dir(payload["family"], payload["geo_id"], payload["mesh_id"])
        )
        cells = active_cell_numbers_from_counts(
            mesh_payload["n_buffer_in"],
            mesh_payload["n_active_cells"],
        )
    except (KeyError, TypeError, ValueError, OSError, ManifestError):
        cells = []
    area_applies = area_mem_udm_identity_applies(record)
    area = area_mem_udm_identity_block_reason(record)
    flux_in_applies = csv_flux_three_key_applies(record, side="in")
    flux_in = csv_flux_three_key_block_reason(record, side="in")
    flux_out_applies = csv_flux_three_key_applies(record, side="out")
    flux_out = csv_flux_three_key_block_reason(record, side="out")
    spacer_applies = spacer_dp_active_cell_sum_applies(record, cells)
    spacer = spacer_dp_active_cell_sum_block_reason(record, cells)
    extra = ""
    if area_applies and area:
        extra += f"  area_reason={area}"
    if flux_in_applies and flux_in:
        extra += f"  flux_in_reason={flux_in}"
    if flux_out_applies and flux_out:
        extra += f"  flux_out_reason={flux_out}"
    if spacer_applies and spacer:
        extra += f"  spacer_reason={spacer}"
    print(
        f"run   area={_label(area, applies=area_applies)}  "
        f"flux_in={_label(flux_in, applies=flux_in_applies)}  "
        f"flux_out={_label(flux_out, applies=flux_out_applies)}  "
        f"spacer_dp={_label(spacer, applies=spacer_applies)}  {leaf}{extra}",
        file=file,
    )
    return {
        "kind": "run",
        "leaf": leaf,
        "area": area if area_applies else None,
        "flux_in": flux_in if flux_in_applies else None,
        "flux_out": flux_out if flux_out_applies else None,
        "spacer_dp": spacer if spacer_applies else None,
        "no_csv": False,
        "unreadable": False,
    }


def _reject_n(rows, key):
    return sum(1 for row in rows if row.get(key))


def main(argv=None) -> int:
    del argv
    data_root()
    print("EXTRACT IDENTITY REPORT (no Fluent)")
    print(f"runs_root={runs_root()}")
    if not runs_root().is_dir():
        raise NotADirectoryError(f"runs_root is not a directory: {runs_root()}")
    skipped_runs: list[tuple[Path, str]] = []
    rows = []
    for manifest_path, payload in iter_run_manifests(
        skip_invalid=True,
        skipped_manifests=skipped_runs,
    ):
        rows.append(report_run_leaf(manifest_path, payload))
    for manifest_path, error in skipped_runs:
        print(f"run   UNREADABLE  {manifest_path}: {error}")
        rows.append({"kind": "run", "unreadable": True})
    unread = sum(1 for row in rows if row.get("unreadable"))
    no_csv = sum(1 for row in rows if row.get("no_csv"))
    print(
        "Summary: "
        f"run {len(rows)} unread={unread} no_csv={no_csv} "
        f"(area_reject={_reject_n(rows, 'area')} "
        f"flux_in_reject={_reject_n(rows, 'flux_in')} "
        f"flux_out_reject={_reject_n(rows, 'flux_out')} "
        f"spacer_dp_reject={_reject_n(rows, 'spacer_dp')})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
