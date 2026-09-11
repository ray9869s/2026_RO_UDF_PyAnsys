#!/usr/bin/env python3
"""Report R-10 legacy M_r* paths in campaign trees. No Fluent.

Scope is RO_DATA_ROOT/{geometries,meshes,runs}/ml/M_r* only.
data_root/_archive is out of scope, including
_archive/ml_pre_wedge_rule and _archive/sin_a193_l1733_pre_gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ro.campaign_geo_ids import (
    CAMPAIGN_DATA_TREES,
    LEGACY_ML_GEO_ID_PREFIX,
    iter_legacy_ml_geo_paths,
)
from ro.paths import data_root


KNOWN_ARCHIVES_OUT_OF_SCOPE = (
    Path("_archive") / "ml_pre_wedge_rule",
    Path("_archive") / "sin_a193_l1733_pre_gate",
)


def main(argv=None) -> int:
    del argv
    root = data_root()
    print("LEGACY ML PATH REPORT (no Fluent)")
    print(f"data_root={root}")
    print(
        "scope="
        + ", ".join(f"{tree}/ml/{LEGACY_ML_GEO_ID_PREFIX}*" for tree in CAMPAIGN_DATA_TREES)
    )
    if not root.is_dir():
        print("root=MISSING")
        print("Summary: campaign_tree_legacy=UNKNOWN (data root not a directory).")
        return 1

    for rel in KNOWN_ARCHIVES_OUT_OF_SCOPE:
        archive = root / rel
        state = "PRESENT_OUT_OF_SCOPE" if archive.exists() else "ABSENT"
        print(f"archive  {state}  {rel.as_posix()}")

    found = iter_legacy_ml_geo_paths(root)
    for path in found:
        print(f"legacy  REJECT  {path}")
    print(
        f"Summary: campaign_tree_legacy={len(found)} "
        f"(reject if >0; _archive not in scope)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
