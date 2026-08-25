"""Print CP definition comparison table from post-process JSON artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Optional


def _load_segmented_cp_values(report_json_path: Path) -> Mapping[str, Any]:
    payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    segmented = payload.get("derived_values", {}).get("segmented_cp_values")
    if segmented is None:
        derived = payload.get("derived", {})
        segmented = derived.get("segmented_cp_values")
    if not isinstance(segmented, Mapping):
        raise ValueError(
            f"No segmented_cp_values in {report_json_path}."
        )
    return segmented


def _metric(segmented: Mapping[str, Any], key: str) -> Any:
    return segmented.get(key)


def _ratio(a: float | None, b: float | None) -> str:
    if a is None or b is None or b == 0.0:
        return "-"
    return f"{a / b:.6f}"


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rel_gap_percent(left: Optional[float], right: Optional[float]) -> str:
    if left is None or right is None:
        return "-"
    mean = (left + right) / 2.0
    if mean == 0.0:
        return "-"
    return f"{abs(left - right) / mean * 100.0:.4f}%"


def _cell_profile_legacy_warning(case_path: Optional[Path]) -> Optional[str]:
    if case_path is None:
        return None
    from ro.fluent_report_helpers import find_cell_profile_csv

    csv_path = find_cell_profile_csv(case_path)
    if csv_path is None:
        return None
    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    if "ref_mean_lmh_cells_3_5" in header:
        return (
            "WARNING: cell_profile.csv contains ref_mean_lmh_cells_3_5 — the "
            "original extract used spacer cells 3–5 as its reference span, not "
            "the current lead=3 window (cells 5–8). Numbers already in that "
            "CSV are not comparable to the new windowed CP values."
        )
    return None


def print_grid_cp_noise_table(
    left_segmented: Mapping[str, Any],
    right_segmented: Mapping[str, Any],
    *,
    left_label: str = "bl4",
    right_label: str = "bl6",
    left_cell_count: Optional[int] = None,
    right_cell_count: Optional[int] = None,
    left_case_path: Optional[Path] = None,
    right_case_path: Optional[Path] = None,
) -> None:
    """Print bl4-vs-bl6 grid-noise CP table (canonical / L1 / L2)."""
    for warning_path in (left_case_path, right_case_path):
        warning = _cell_profile_legacy_warning(warning_path)
        if warning:
            print(warning)
            print()

    rows = [
        ("CANONICAL", "canon"),
        ("L1", "L1"),
        ("L2", "L2"),
    ]
    left_win = f"{left_label} windowed"
    left_all = f"{left_label} all-active"
    right_win = f"{right_label} windowed"
    right_all = f"{right_label} all-active"
    header = (
        f"{'':12}  {left_win:>16}  {left_all:>16}  "
        f"{right_win:>16}  {right_all:>16}"
    )
    print(header)
    print("-" * len(header))

    for label, prefix in rows:
        left_win_avg = _metric(left_segmented, f"cp_{prefix}_window_avg")
        left_all_avg = _metric(left_segmented, f"cp_{prefix}_all_active_avg")
        right_win_avg = _metric(right_segmented, f"cp_{prefix}_window_avg")
        right_all_avg = _metric(right_segmented, f"cp_{prefix}_all_active_avg")
        print(
            f"{label + ' avg':12}  "
            f"{left_win_avg!s:>16}  {left_all_avg!s:>16}  "
            f"{right_win_avg!s:>16}  {right_all_avg!s:>16}"
        )
        left_win_max = _metric(left_segmented, f"cp_{prefix}_window_max")
        left_all_max = _metric(left_segmented, f"cp_{prefix}_all_active_max")
        right_win_max = _metric(right_segmented, f"cp_{prefix}_window_max")
        right_all_max = _metric(right_segmented, f"cp_{prefix}_all_active_max")
        print(
            f"{label + ' max':12}  "
            f"{left_win_max!s:>16}  {left_all_max!s:>16}  "
            f"{right_win_max!s:>16}  {right_all_max!s:>16}"
        )

    print()
    print("bl4-vs-bl6 relative gap (% of mean):")
    for label, prefix in rows:
        left_win_avg = _as_float(_metric(left_segmented, f"cp_{prefix}_window_avg"))
        left_all_avg = _as_float(_metric(left_segmented, f"cp_{prefix}_all_active_avg"))
        right_win_avg = _as_float(_metric(right_segmented, f"cp_{prefix}_window_avg"))
        right_all_avg = _as_float(
            _metric(right_segmented, f"cp_{prefix}_all_active_avg")
        )
        left_win_max = _as_float(_metric(left_segmented, f"cp_{prefix}_window_max"))
        left_all_max = _as_float(_metric(left_segmented, f"cp_{prefix}_all_active_max"))
        right_win_max = _as_float(_metric(right_segmented, f"cp_{prefix}_window_max"))
        right_all_max = _as_float(
            _metric(right_segmented, f"cp_{prefix}_all_active_max")
        )
        print(
            f"  {label:9} window avg: "
            f"{_rel_gap_percent(left_win_avg, right_win_avg):>10}  "
            f"all-active avg: {_rel_gap_percent(left_all_avg, right_all_avg):>10}"
        )
        print(
            f"  {label:9} window max: "
            f"{_rel_gap_percent(left_win_max, right_win_max):>10}  "
            f"all-active max: {_rel_gap_percent(left_all_max, right_all_max):>10}"
        )

    left_canon = _metric(left_segmented, "cp_canon_window_avg")
    right_canon = _metric(right_segmented, "cp_canon_window_avg")
    left_l1 = _metric(left_segmented, "cp_L1_window_avg")
    right_l1 = _metric(right_segmented, "cp_L1_window_avg")
    left_l2 = _metric(left_segmented, "cp_L2_window_avg")
    right_l2 = _metric(right_segmented, "cp_L2_window_avg")

    print()
    print(f"Canonical/L1 ({left_label} window):", _ratio(left_canon, left_l1))
    print(f"Canonical/L2 ({left_label} window):", _ratio(left_canon, left_l2))
    print(f"Canonical/L1 ({right_label} window):", _ratio(right_canon, right_l1))
    print(f"Canonical/L2 ({right_label} window):", _ratio(right_canon, right_l2))
    print()
    print(f"c_b_window ({left_label}):", _metric(left_segmented, "c_b_window_mol_m3"))
    print(f"c_b_window ({right_label}):", _metric(right_segmented, "c_b_window_mol_m3"))
    print(f"c_0 ({left_label}):", _metric(left_segmented, "c_inlet_ref_mol_m3"))
    print(f"c_0 ({right_label}):", _metric(right_segmented, "c_inlet_ref_mol_m3"))
    print(
        f"delta guard max ({left_label}):",
        _metric(left_segmented, "cp_canon_rescale_delta_max"),
    )
    print(
        f"delta guard max ({right_label}):",
        _metric(right_segmented, "cp_canon_rescale_delta_max"),
    )
    print(
        "delta threshold:",
        _metric(left_segmented, "cp_scalar_rescale_guard_threshold"),
    )
    if left_cell_count is not None:
        print(f"cell count ({left_label}):", left_cell_count)
    if right_cell_count is not None:
        print(f"cell count ({right_label}):", right_cell_count)


def print_cp_definition_table(
    plug_segmented: Mapping[str, Any],
    parabolic_segmented: Mapping[str, Any],
) -> None:
    """Backward-compatible plug-vs-parabolic alias."""
    print_grid_cp_noise_table(
        plug_segmented,
        parabolic_segmented,
        left_label="plug",
        right_label="parabolic",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print CP definition comparison from raw_report_values.json"
    )
    parser.add_argument(
        "--left-json",
        type=Path,
        help="raw_report_values.json for the left / bl4 run",
    )
    parser.add_argument(
        "--right-json",
        type=Path,
        help="raw_report_values.json for the right / bl6 run",
    )
    parser.add_argument(
        "--plug-json",
        type=Path,
        help="raw_report_values.json for plug inlet run (legacy alias)",
    )
    parser.add_argument(
        "--parabolic-json",
        type=Path,
        help="raw_report_values.json for parabolic inlet run (legacy alias)",
    )
    parser.add_argument("--left-label", type=str, default="bl4")
    parser.add_argument("--right-label", type=str, default="bl6")
    parser.add_argument("--left-cells", type=int, default=None)
    parser.add_argument("--right-cells", type=int, default=None)
    parser.add_argument("--left-case-path", type=Path, default=None)
    parser.add_argument("--right-case-path", type=Path, default=None)
    args = parser.parse_args()

    left_json = args.left_json or args.plug_json
    right_json = args.right_json or args.parabolic_json
    if left_json is None or right_json is None:
        parser.error(
            "Provide --left-json and --right-json "
            "(or legacy --plug-json and --parabolic-json)."
        )

    left = _load_segmented_cp_values(left_json)
    right = _load_segmented_cp_values(right_json)
    print_grid_cp_noise_table(
        left,
        right,
        left_label=args.left_label,
        right_label=args.right_label,
        left_cell_count=args.left_cells,
        right_cell_count=args.right_cells,
        left_case_path=args.left_case_path,
        right_case_path=args.right_case_path,
    )


if __name__ == "__main__":
    main()
