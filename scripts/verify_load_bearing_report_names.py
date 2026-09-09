#!/usr/bin/env python3
"""Cross-check LOAD_BEARING_REPORT_NAMES against Cell 7 report_names templates.

No Fluent required. Prints the full Cell 7 name list for the campaign layout
(1+7+2 -> n_unit_cells=10) and flags any load-bearing name that Cell 7 never
creates.
"""
from __future__ import annotations

import argparse
import sys

from ro.fluent_report_helpers import (
    LOAD_BEARING_REPORT_NAMES,
    expected_cell_7_report_names,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--n-unit-cells",
        type=int,
        default=10,
        help="layout.n_total for the campaign mesh (default: 10 = 1+7+2).",
    )
    args = parser.parse_args(argv)

    cell_7_names = expected_cell_7_report_names(args.n_unit_cells)
    cell_7_set = set(cell_7_names)
    missing_from_cell_7 = sorted(LOAD_BEARING_REPORT_NAMES - cell_7_set)
    load_bearing_in_cell_7 = sorted(LOAD_BEARING_REPORT_NAMES & cell_7_set)

    print(f"Cell 7 report_names (n_unit_cells={args.n_unit_cells}, count={len(cell_7_names)}):")
    for name in cell_7_names:
        marker = "  [load-bearing]" if name in LOAD_BEARING_REPORT_NAMES else ""
        print(f"  {name}{marker}")

    print()
    print("LOAD_BEARING_REPORT_NAMES verified against Cell 7:")
    for name in load_bearing_in_cell_7:
        print(f"  OK  {name}")

    if missing_from_cell_7:
        print()
        print("ERROR: load-bearing names NOT created by Cell 7:")
        for name in missing_from_cell_7:
            print(f"  MISSING  {name}")
        return 1

    print()
    print("Fluent report name -> wide CSV column (where renamed):")
    print("  pp_m_in                          -> m_in")
    print("  pp_m_out                         -> m_out")
    print("  pp_area_mem                      -> area_mem")
    print("  pp_udm_area_sum                  -> pp_udm_area_sum")
    print("  pp_lmh_mass_balance              -> lmh_mass_balance")
    print("  pp_lmh_udm_avg                   -> lmh_udm_avg")
    print("  pp_volint_salt_mass_source       -> salt_sink_volume_integral_UDM0")
    print("  pp_volint_total_mass_source      -> total_sink_volume_integral_UDM2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
