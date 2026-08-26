#!/usr/bin/env python3
"""Compare regenerated D2450_a45 against archive-known-good reference values.

Standalone diagnostic: prints actual vs expected with absolute and relative
differences, then warns on drift. Never raises on mismatch — nothing else in
the pipeline depends on this script.

Legacy archive paths live under RO_DATA_ROOT/archive/ (outside the live
meshes/ / runs/ trees). Expected constants below were recovered from those
archives; the script reads only the live schema-v2 manifests.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from typing import Any

from ro.manifest import ManifestError, read_mesh_manifest, read_run_manifest
from ro.paths import mesh_dir, run_dir

FAMILY = "diamond"
GEO_ID = "D2450_a45"
MESH_ID = "max085_min006_cpg5_bl4_peel2"
# Campaign parabolic u0p2 / 6 MPa run that stores u_mean_ms on the run manifest.
RUN_ID = "u0p2_p6M"

# ---------------------------------------------------------------------------
# Archive-known-good references (provenance comments are intentional).
# ---------------------------------------------------------------------------

# cell_count = 796009
#   from archive/03_Results/D2450_a45_7c_brg110/
#        mesh_max085_min006_cpg5_bl4/mesh_run_record.json:65
#   Trust only when peel_layers == 2 in that same file (peel0 recorded
#   987599 cells, +24% — not interchangeable). Live mesh_id encodes peel2.
EXPECTED_CELL_COUNT = 796009
EXPECTED_PEEL = 2

# u_mean_ms = 0.199281
#   from archive/03_Results/D2450_a45_7c_brg110/blconv_para_u0p2/
#        solver_log_blconv_para_u0p2.txt:1523
#   Appears identically across blconv_*, inlettest_*, probe_*, udm12_*
#   runs on the same mesh → mesh-specific, not run-specific.
#   Solver logs printed ~6 significant figures; warn at 5e-7 abs.
EXPECTED_U_MEAN_MS = 0.199281
U_MEAN_ABS_TOL = 5.0e-7

# inlet_profile_G = 1.00361
#   from archive/03_Results/D2450_a45_7c_brg110/recon_bl4_u0p2/
#        solver_log_recon_bl4_u0p2.txt:1780
#   IMPORTANT: appears ONLY in recon_bl4_u0p2 and y1_bl4_u0p2 — not in
#   blconv_* or inlettest_*. Computed on the analytic wall-concentration
#   reconstruction path (RO_ANALYTIC_CWALL=1). Precondition for this check.
#   Solver logs print 6 significant figures only → abs tol 5e-6.
EXPECTED_INLET_PROFILE_G = 1.00361
INLET_PROFILE_G_ABS_TOL = 5.0e-6
INLET_PROFILE_G_PRECONDITION = "RO_ANALYTIC_CWALL=1"

# Legacy archive runs used 260810_RO_UDF.c; current production UDF is
# 260822_RO_UDF.c (adds the inlet_profile_G transcript marker). A G
# mismatch is therefore not automatically a remesh/physics regression.


@dataclass(frozen=True)
class Comparison:
    name: str
    actual: Any
    expected: Any
    abs_diff: float | None
    rel_diff: float | None
    within_tol: bool
    note: str = ""


def _rel_diff(actual: float, expected: float) -> float | None:
    if expected == 0.0:
        return None if actual == 0.0 else math.inf
    return abs(actual - expected) / abs(expected)


def compare_number(
    name: str,
    actual: Any,
    expected: float | int,
    *,
    abs_tol: float,
    note: str = "",
) -> Comparison:
    if actual is None:
        return Comparison(
            name=name,
            actual=None,
            expected=expected,
            abs_diff=None,
            rel_diff=None,
            within_tol=False,
            note=note or "actual is missing",
        )
    try:
        actual_f = float(actual)
        expected_f = float(expected)
    except (TypeError, ValueError):
        return Comparison(
            name=name,
            actual=actual,
            expected=expected,
            abs_diff=None,
            rel_diff=None,
            within_tol=False,
            note=note or f"non-numeric actual {actual!r}",
        )
    abs_diff = abs(actual_f - expected_f)
    return Comparison(
        name=name,
        actual=actual_f if not isinstance(expected, int) else actual,
        expected=expected,
        abs_diff=abs_diff,
        rel_diff=_rel_diff(actual_f, expected_f),
        within_tol=abs_diff <= abs_tol,
        note=note,
    )


def format_comparison(row: Comparison) -> str:
    abs_s = "n/a" if row.abs_diff is None else f"{row.abs_diff:.6g}"
    rel_s = "n/a" if row.rel_diff is None else f"{row.rel_diff:.6g}"
    status = "OK" if row.within_tol else "DRIFT"
    line = (
        f"{row.name}: actual={row.actual!r} expected={row.expected!r} "
        f"abs_diff={abs_s} rel_diff={rel_s} [{status}]"
    )
    if row.note:
        line = f"{line}  ({row.note})"
    return line


def collect_comparisons(
    *,
    mesh_payload: dict[str, Any] | None,
    run_payload: dict[str, Any] | None,
) -> list[Comparison]:
    rows: list[Comparison] = []

    peel = None if mesh_payload is None else mesh_payload.get("peel")
    peel_note = ""
    if peel is None:
        peel_note = "peel missing; cell_count reference assumes peel2"
    elif int(peel) != EXPECTED_PEEL:
        peel_note = (
            f"peel={peel!r} != {EXPECTED_PEEL}; archive peel0 was 987599 "
            "cells (+24%) — cell_count reference may not apply"
        )
    else:
        peel_note = f"peel={peel} matches peel2 archive reference"

    cell_actual = None if mesh_payload is None else mesh_payload.get("cell_count")
    rows.append(
        compare_number(
            "cell_count",
            cell_actual,
            EXPECTED_CELL_COUNT,
            abs_tol=0.0,
            note=peel_note,
        )
    )

    u_actual = None if run_payload is None else run_payload.get("u_mean_ms")
    rows.append(
        compare_number(
            "u_mean_ms",
            u_actual,
            EXPECTED_U_MEAN_MS,
            abs_tol=U_MEAN_ABS_TOL,
            note=f"from run_id={RUN_ID}",
        )
    )

    g_actual = None if mesh_payload is None else mesh_payload.get("inlet_profile_G")
    rows.append(
        compare_number(
            "inlet_profile_G",
            g_actual,
            EXPECTED_INLET_PROFILE_G,
            abs_tol=INLET_PROFILE_G_ABS_TOL,
            note=(
                f"precondition {INLET_PROFILE_G_PRECONDITION}; "
                "legacy archive used 260810_RO_UDF.c, current is "
                "260822_RO_UDF.c (inlet_profile_G marker) — G drift may "
                "be a UDF change, not a remesh regression"
            ),
        )
    )
    return rows


def _load_payloads() -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    mesh_payload = None
    run_payload = None
    mesh_directory = mesh_dir(FAMILY, GEO_ID, MESH_ID)
    run_directory = run_dir(FAMILY, GEO_ID, MESH_ID, RUN_ID)
    try:
        mesh_payload = read_mesh_manifest(mesh_directory)
    except (ManifestError, OSError, ValueError) as exc:
        warnings.append(f"mesh manifest unavailable at {mesh_directory}: {exc}")
    try:
        run_payload = read_run_manifest(run_directory)
    except (ManifestError, OSError, ValueError) as exc:
        warnings.append(f"run manifest unavailable at {run_directory}: {exc}")
    return mesh_payload, run_payload, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    print("D2450_a45 fresh-start reference check (diagnostic, non-gating)")
    print(f"  mesh: {FAMILY}/{GEO_ID}/{MESH_ID}")
    print(f"  run:  {FAMILY}/{GEO_ID}/{MESH_ID}/{RUN_ID}")
    print()

    mesh_payload, run_payload, load_warnings = _load_payloads()
    for message in load_warnings:
        print(f"WARNING: {message}")

    rows = collect_comparisons(mesh_payload=mesh_payload, run_payload=run_payload)
    for row in rows:
        print(format_comparison(row))

    drifted = [row for row in rows if not row.within_tol]
    if drifted:
        print()
        for row in drifted:
            extra = ""
            if row.name == "inlet_profile_G":
                extra = (
                    " Legacy runs used 260810_RO_UDF.c; current UDF is "
                    "260822_RO_UDF.c with the inlet_profile_G marker — "
                    "a G mismatch is not automatically a regression."
                )
            print(
                f"WARNING: {row.name} drifted from archive reference "
                f"(abs_diff={row.abs_diff!r}, rel_diff={row.rel_diff!r})."
                f"{extra}"
            )
    else:
        print()
        print("All three reference values within tolerance.")

    # Always succeed: this is a diagnostic, not a gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
