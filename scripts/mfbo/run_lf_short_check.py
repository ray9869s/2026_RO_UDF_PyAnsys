"""HF versus two short-domain LF candidates for two pillar designs.

Importing this module does not launch Fluent. ``--emit-queue`` writes a
job-queue JSON file and does not evaluate. Without it, each design is
evaluated at HF, then LF_a, then LF_b, and the script prints the values,
the relative bias ``(LF - HF) / HF`` (CP as CP-1), and the mesh, solve,
extract, and total wall times.

Designs, the three level names, and their order are this check. The
fidelity table, run id, and data root are required.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_adapter import PillarDesign, evaluate, resolve_data_root
from ro.mfbo_drivers import make_drivers
from ro.mfbo_fidelity_screen import (
    build_named_level_queue,
    load_fidelity_table,
    require_runnable_fidelity,
    write_queue,
)

HF_LEVEL = "HF"
LF_LEVELS = ("LF_a", "LF_b")
CHECK_LEVELS = (HF_LEVEL, *LF_LEVELS)
DESIGNS = (
    PillarDesign(d_p_mm=0.80, d_h_mm=0.15, d_f_mm=0.40),
    PillarDesign(d_p_mm=1.00, d_h_mm=0.30, d_f_mm=0.40),
)
_COMPARED = (
    ("lmh", False),
    ("lmh_module_area", False),
    ("pressure_drop_per_length_pa_per_m", False),
    ("cp_average", True),
)
_WALL_KEYS = (
    "mesh_wall_time_s",
    "solver_wall_time_s",
    "extraction_wall_time_s",
)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate HF, LF_a, and LF_b for the two short-domain check designs. "
            "--emit-queue writes a job queue and does not evaluate."
        ),
    )
    parser.add_argument("--fidelity-table", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--emit-queue",
        type=Path,
        default=None,
        help="Write this queue JSON and do not evaluate.",
    )
    return parser


def require_check_table(table):
    """Require exactly HF, LF_a, and LF_b, each a runnable fidelity level."""
    if not isinstance(table, dict):
        raise TypeError("fidelity table must be a JSON object.")
    names = list(table)
    if set(names) != set(CHECK_LEVELS):
        raise ValueError(
            "The LF short-domain check needs exactly HF, LF_a, and LF_b, "
            f"got {names!r}."
        )
    for name in CHECK_LEVELS:
        require_runnable_fidelity(table, name)
    return table


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    table = require_check_table(load_fidelity_table(args.fidelity_table))
    root = resolve_data_root(args.data_root)
    if args.emit_queue is not None:
        jobs = build_named_level_queue(
            DESIGNS,
            CHECK_LEVELS,
            table,
            run_id=args.run_id,
            data_root=root,
            fidelity_table=args.fidelity_table,
        )
        path = write_queue(args.emit_queue, jobs)
        print(f"queue: {path}", flush=True)
        print(f"jobs: {len(jobs)}", flush=True)
        print("This script did not evaluate.", flush=True)
        return 0
    drivers = make_drivers(root)
    print(
        "Relative bias is (LF - HF) / HF. cp_average is compared as CP-1.",
        flush=True,
    )
    for design in DESIGNS:
        records = {}
        for fidelity in CHECK_LEVELS:
            records[fidelity] = evaluate(
                design,
                fidelity,
                run_id=args.run_id,
                data_root=root,
                fidelity_table=table,
                drivers=drivers,
            )
        _print_design(design, records)
    return 0


def _print_design(design: PillarDesign, records: dict) -> None:
    print(
        f"design d_p_mm={design.d_p_mm} d_h_mm={design.d_h_mm} "
        f"d_f_mm={design.d_f_mm}",
        flush=True,
    )
    high = records[HF_LEVEL]
    _print_record(HF_LEVEL, high)
    for fidelity in LF_LEVELS:
        low = records[fidelity]
        _print_record(fidelity, low)
        _print_bias(fidelity, low, high)
        _print_cost(fidelity, low, high)


def _print_record(fidelity: str, record: dict) -> None:
    print(
        f"  {fidelity} geo_id={record.get('geo_id')} "
        f"mesh_id={record.get('mesh_id')} status={record.get('status')} "
        f"failure_reason={record.get('failure_reason')}",
        flush=True,
    )
    parts = []
    for name, excess in _COMPARED:
        value = _finite(record.get(name))
        label = "cp_excess" if excess else name
        shown = None if value is None else (value - 1.0 if excess else value)
        parts.append(f"{label}={_fmt(shown)}")
        if excess:
            parts.append(f"cp_average={_fmt(value)}")
    print("    " + " ".join(parts), flush=True)
    walls = {key: _finite(record.get(key)) for key in _WALL_KEYS}
    total = None if any(value is None for value in walls.values()) else sum(walls.values())
    print(
        "    wall_s "
        f"mesh={_fmt(walls['mesh_wall_time_s'])} "
        f"solve={_fmt(walls['solver_wall_time_s'])} "
        f"extract={_fmt(walls['extraction_wall_time_s'])} "
        f"total={_fmt(total)}",
        flush=True,
    )


def _print_bias(fidelity: str, low: dict, high: dict) -> None:
    parts = []
    for name, excess in _COMPARED:
        low_value = _compared(low, name, excess)
        high_value = _compared(high, name, excess)
        label = "cp_excess" if excess else name
        parts.append(f"{label}={_fmt(_relative(low_value, high_value))}")
    print(f"    relative_bias_vs_HF {fidelity} " + " ".join(parts), flush=True)


def _print_cost(fidelity: str, low: dict, high: dict) -> None:
    low_total = _total_wall(low)
    high_total = _total_wall(high)
    ratio = None
    if low_total is not None and high_total not in (None, 0.0):
        ratio = low_total / high_total
    print(
        f"    cost_ratio {fidelity}/HF total_wall={_fmt(ratio)}",
        flush=True,
    )


def _compared(record: dict, name: str, excess: bool):
    value = _finite(record.get(name))
    if value is None:
        return None
    if excess:
        return value - 1.0
    return value


def _total_wall(record: dict):
    parts = [_finite(record.get(key)) for key in _WALL_KEYS]
    if any(value is None for value in parts):
        return None
    return sum(parts)


def _relative(low, high):
    if low is None or high is None or high == 0.0:
        return None
    return (low - high) / high


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _fmt(value) -> str:
    if value is None:
        return "undefined"
    return format(float(value), ".6g")


if __name__ == "__main__":
    sys.exit(main())
