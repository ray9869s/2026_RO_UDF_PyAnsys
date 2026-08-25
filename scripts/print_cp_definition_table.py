"""Print CP definition comparison table from post-process JSON artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def _load_segmented_cp_values(report_json_path: Path) -> Mapping[str, Any]:
    payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    segmented = payload.get("segmented_cp_values")
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


def print_cp_definition_table(
    plug_segmented: Mapping[str, Any],
    parabolic_segmented: Mapping[str, Any],
) -> None:
    """Print the plug-vs-parabolic definition-sensitivity table."""
    rows = [
        ("CANONICAL", "canon", "canon"),
        ("L1", "L1", "L1"),
        ("L2", "L2", "L2"),
    ]
    header = (
        f"{'':12}  {'plug win avg':>14}  {'plug all avg':>14}  "
        f"{'para win avg':>14}  {'para all avg':>14}  "
        f"{'plug win max':>14}  {'plug all max':>14}  "
        f"{'para win max':>14}  {'para all max':>14}"
    )
    print(header)
    print("-" * len(header))
    for label, window_prefix, all_prefix in rows:
        plug_win_avg = _metric(plug_segmented, f"cp_{window_prefix}_window_avg")
        plug_all_avg = _metric(plug_segmented, f"cp_{all_prefix}_all_active_avg")
        para_win_avg = _metric(parabolic_segmented, f"cp_{window_prefix}_window_avg")
        para_all_avg = _metric(parabolic_segmented, f"cp_{all_prefix}_all_active_avg")
        plug_win_max = _metric(plug_segmented, f"cp_{window_prefix}_window_max")
        plug_all_max = _metric(plug_segmented, f"cp_{all_prefix}_all_active_max")
        para_win_max = _metric(parabolic_segmented, f"cp_{window_prefix}_window_max")
        para_all_max = _metric(parabolic_segmented, f"cp_{all_prefix}_all_active_max")
        print(
            f"{label:12}  "
            f"{plug_win_avg!s:>14}  {plug_all_avg!s:>14}  "
            f"{para_win_avg!s:>14}  {para_all_avg!s:>14}  "
            f"{plug_win_max!s:>14}  {plug_all_max!s:>14}  "
            f"{para_win_max!s:>14}  {para_all_max!s:>14}"
        )

    plug_canon = _metric(plug_segmented, "cp_canon_window_avg")
    para_canon = _metric(parabolic_segmented, "cp_canon_window_avg")
    plug_l1 = _metric(plug_segmented, "cp_L1_window_avg")
    para_l1 = _metric(parabolic_segmented, "cp_L1_window_avg")
    plug_l2 = _metric(plug_segmented, "cp_L2_window_avg")
    para_l2 = _metric(parabolic_segmented, "cp_L2_window_avg")
    print()
    print("Canonical/L1 (plug window):", _ratio(plug_canon, plug_l1))
    print("Canonical/L2 (plug window):", _ratio(plug_canon, plug_l2))
    print("Canonical/L1 (parabolic window):", _ratio(para_canon, para_l1))
    print("Canonical/L2 (parabolic window):", _ratio(para_canon, para_l2))
    print()
    print("c_b_window (plug):", _metric(plug_segmented, "c_b_window_mol_m3"))
    print("c_b_window (parabolic):", _metric(parabolic_segmented, "c_b_window_mol_m3"))
    print("c_0 (plug):", _metric(plug_segmented, "c_inlet_ref_mol_m3"))
    print("c_0 (parabolic):", _metric(parabolic_segmented, "c_inlet_ref_mol_m3"))
    print(
        "delta guard max (plug):",
        _metric(plug_segmented, "cp_canon_rescale_delta_max"),
    )
    print(
        "delta guard max (parabolic):",
        _metric(parabolic_segmented, "cp_canon_rescale_delta_max"),
    )
    print(
        "delta threshold:",
        _metric(plug_segmented, "cp_scalar_rescale_guard_threshold"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print CP definition comparison from raw_report_values.json"
    )
    parser.add_argument(
        "--plug-json",
        type=Path,
        required=True,
        help="raw_report_values.json for plug inlet run",
    )
    parser.add_argument(
        "--parabolic-json",
        type=Path,
        required=True,
        help="raw_report_values.json for parabolic inlet run",
    )
    args = parser.parse_args()
    plug = _load_segmented_cp_values(args.plug_json)
    parabolic = _load_segmented_cp_values(args.parabolic_json)
    print_cp_definition_table(plug, parabolic)


if __name__ == "__main__":
    main()
