"""Read-only table of cpc versus canon from existing wide summaries.

Groups:

- campaign: every leaf under ``--campaign-root`` whose run manifest has
  ``convergence_quality`` PASS
- y1: every leaf under ``--meshstudy-root`` for D2450_a45, P_p80_h15,
  M_c160, and S_a144_l3465 whose mesh id contains ``y1h020``

A row is flagged when both CP columns are present and
``|cpc_window_avg_flux - cp_canon_window_avg| > 0.005``.

Does not launch Fluent. Does not modify the data roots.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_SRC = _SCRIPTS.parent / "src"
for _path in (_SRC, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

FLAG_ABS = 0.005
Y1_TOKEN = "y1h020"
Y1_GEO_IDS = ("D2450_a45", "P_p80_h15", "M_c160", "S_a144_l3465")
COLUMNS = (
    "group",
    "family",
    "geo_id",
    "mesh_id",
    "run_id",
    "convergence_quality",
    "cpc_window_avg_flux",
    "cp_canon_window_avg",
    "cp_L2_window_avg",
    "cm_max",
    "cp_canon_live_window_avg",
    "dead_zone_area_fraction_window",
    "abs_cpc_minus_canon",
    "flag",
)


def _finite(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _read_wide(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(
            f"{path} must have one data row, got {len(rows)}."
        )
    return rows[0]


def _read_manifest(path):
    path = Path(path)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return payload


def iter_run_leaves(root):
    """Yield (family, geo_id, mesh_id, run_id, summary_path, manifest_path)."""
    runs = Path(root) / "runs"
    if not runs.is_dir():
        raise FileNotFoundError(f"runs directory not found: {runs}")
    for family_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
        for geo_dir in sorted(path for path in family_dir.iterdir() if path.is_dir()):
            for mesh_dir in sorted(path for path in geo_dir.iterdir() if path.is_dir()):
                for run_dir in sorted(path for path in mesh_dir.iterdir() if path.is_dir()):
                    summary = run_dir / "post" / "reports" / "summary_metrics_wide.csv"
                    if not summary.is_file():
                        continue
                    yield (
                        family_dir.name,
                        geo_dir.name,
                        mesh_dir.name,
                        run_dir.name,
                        summary,
                        run_dir / "manifest.json",
                    )


def survey_row(group, family, geo_id, mesh_id, run_id, summary_path, manifest_path):
    """One comparison row. Missing CP columns stay blank and are not flagged."""
    wide = _read_wide(summary_path)
    manifest = _read_manifest(manifest_path)
    cpc = _finite(wide.get("cpc_window_avg_flux"))
    canon = _finite(wide.get("cp_canon_window_avg"))
    difference = None if cpc is None or canon is None else abs(cpc - canon)
    gate = manifest.get("convergence_quality")
    if not isinstance(gate, str):
        gate = ""
    return {
        "group": group,
        "family": family,
        "geo_id": geo_id,
        "mesh_id": mesh_id,
        "run_id": run_id,
        "convergence_quality": gate,
        "cpc_window_avg_flux": cpc,
        "cp_canon_window_avg": canon,
        "cp_L2_window_avg": _finite(wide.get("cp_L2_window_avg")),
        "cm_max": _finite(wide.get("cm_max")),
        "cp_canon_live_window_avg": _finite(wide.get("cp_canon_live_window_avg")),
        "dead_zone_area_fraction_window": _finite(
            wide.get("dead_zone_area_fraction_window")
        ),
        "abs_cpc_minus_canon": difference,
        "flag": "FLAG" if difference is not None and difference > FLAG_ABS else "",
    }


def campaign_pass_rows(root):
    """PASS leaves only. The manifest gate is ``convergence_quality``."""
    rows = []
    for leaf in iter_run_leaves(root):
        manifest = _read_manifest(leaf[-1])
        if manifest.get("convergence_quality") != "PASS":
            continue
        rows.append(survey_row("campaign_pass", *leaf))
    return rows


def y1_rows(root):
    """y1 = 2 um mesh-study leaves for the four contact-series geometries."""
    rows = []
    for leaf in iter_run_leaves(root):
        _family, geo_id, mesh_id = leaf[0], leaf[1], leaf[2]
        if geo_id not in Y1_GEO_IDS or Y1_TOKEN not in mesh_id:
            continue
        rows.append(survey_row("y1_2um", *leaf))
    return rows


def _cell(value):
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def write_csv(path, rows):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in COLUMNS})
    return destination


def format_flagged(rows):
    """Text table of flagged rows, plus the group counts."""
    flagged = [row for row in rows if row["flag"] == "FLAG"]
    lines = [
        f"rows {len(rows)}, flagged {len(flagged)} "
        f"(|cpc - canon| > {FLAG_ABS}).",
    ]
    headers = (
        "group",
        "geo_id",
        "mesh_id",
        "run_id",
        "cpc_window_avg_flux",
        "cp_canon_window_avg",
        "cp_L2_window_avg",
        "cm_max",
        "abs_cpc_minus_canon",
    )
    if not flagged:
        lines.append("No flagged rows.")
        return "\n".join(lines)
    body = []
    for row in flagged:
        body.append(tuple(_cell(row.get(key)) for key in headers))
    widths = [len(header) for header in headers]
    for cells in body:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    def _format(cells):
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(cells)
        ).rstrip()

    lines.append(_format(headers))
    lines.extend(_format(cells) for cells in body)
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare cpc_window_avg_flux with cp_canon_window_avg on "
            "existing summary_metrics_wide.csv files. Does not launch Fluent."
        )
    )
    parser.add_argument(
        "--campaign-root",
        default=None,
        help="Data root whose PASS leaves are campaign_reextract_v2.",
    )
    parser.add_argument(
        "--meshstudy-root",
        default=None,
        help="Data root whose y1h020 leaves are the mesh study.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="CSV path to write. This is the only file the script creates.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.campaign_root is None and args.meshstudy_root is None:
        raise SystemExit("Pass --campaign-root, --meshstudy-root, or both.")
    rows = []
    if args.campaign_root is not None:
        rows.extend(campaign_pass_rows(args.campaign_root))
    if args.meshstudy_root is not None:
        rows.extend(y1_rows(args.meshstudy_root))
    path = write_csv(args.output, rows)
    print(format_flagged(rows))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
