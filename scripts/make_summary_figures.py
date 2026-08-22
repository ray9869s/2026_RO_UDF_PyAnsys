# ==========================================================
# make_summary_figures.py
# Presentation-ready comparison tables and figures for
# geometry performance comparison.
#
# Reads:
#   RO_DATA_ROOT/inventory/all_cases_post_summary.csv
#   RO_DATA_ROOT/inventory/all_cases_post_status.csv  (optional but recommended)
#
# Writes to:
#   RO_DATA_ROOT/inventory/post_summary_figures/
#
# Does NOT launch Fluent or modify any existing CSVs.
#
# Usage (from project root):
#   python My_CFD_Project/01_Scripts/post_processing/make_summary_figures.py
# ==========================================================

from __future__ import annotations

import argparse
import sys
import textwrap
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — no display needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from ro.paths import data_root


# ----------------------------------------------------------
# Constants
# ----------------------------------------------------------

EXCLUDE_STATUSES = {"FAILED", "MISSING_CASE_DATA", "FAILED_METRIC_VALIDATION", "DRY_RUN"}

GEO_ORDER = [
    "Empty",
    "Diamond_Spacer",
    "Pillar",
    "Hole_Pillar",
    "Multi_Layer_equal",
    "Multi_Layer_diff",
    "Sin_ST",
    "Sin_SL",
]

REQUIRED_METRIC_COLS = [
    "lmh_mass_balance",
    "pressure_drop_spacer",
    "pressure_drop_spacer_per_m",
    "cp_inlet_avg",
    "wall_shear_rate_avg",
]

REQUIRED_ID_COLS = [
    "geo_name",
    "case_name",
    "inlet_velocity_value",
    "outlet_pressure_MPa",
    "use_for_final_comparison",
]

# Representative condition for the single-condition table
REP_U   = 0.2    # m/s
REP_P   = 6.0    # MPa


# ----------------------------------------------------------
# Helpers: I/O
# ----------------------------------------------------------

def load_summary(path: Path) -> pd.DataFrame:
    if not path.is_file():
        sys.exit(
            f"\nERROR: Summary CSV not found:\n  {path}\n"
            "Run batch_report_extract.py first to produce this file."
        )
    df = pd.read_csv(path, encoding="utf-8-sig")
    return df


def load_status(path: Path) -> pd.DataFrame | None:
    if not path.is_file():
        warnings.warn(
            f"\nWARNING: Status CSV not found:\n  {path}\n"
            "Cannot filter by run status. Falling back to use_for_final_comparison only.\n"
            "Cases with FAILED / FAILED_METRIC_VALIDATION status may be included.",
            stacklevel=2,
        )
        return None
    return pd.read_csv(path, encoding="utf-8-sig")


def check_required_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(
            f"\nERROR: {label} is missing required columns:\n"
            + "\n".join(f"  - {c}" for c in missing)
        )


# ----------------------------------------------------------
# Helpers: filtering
# ----------------------------------------------------------

def normalize_bool_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Normalize a column that may arrive as bool, 'True'/'False', or 0/1."""
    df = df.copy()
    raw = df[col]
    if raw.dtype == object or raw.dtype == "string":
        df[col] = raw.map(lambda v: str(v).strip().lower() == "true")
    else:
        df[col] = raw.astype(bool)
    return df


def filter_valid_cases(
    summary: pd.DataFrame,
    status_df: pd.DataFrame | None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Apply filtering rules:
      1. Merge status (status, geo_name, case_name only) onto summary to avoid
         column name collisions from duplicate fields in the status CSV.
      2. Exclude rows whose status is in EXCLUDE_STATUSES.
      3. Keep only rows where use_for_final_comparison == True.
    """
    excluded_counts: dict[str, int] = {}

    df = summary.copy()

    # Normalize use_for_final_comparison
    if "use_for_final_comparison" in df.columns:
        df = normalize_bool_column(df, "use_for_final_comparison")
    else:
        sys.exit(
            "\nERROR: 'use_for_final_comparison' column not found in summary CSV.\n"
            "This column is written by batch_report_extract.py — "
            "check that the correct merged file is being read."
        )

    if status_df is not None:
        # Merge only the status column to avoid column collisions
        status_slim = status_df[["geo_name", "case_name", "status"]].copy()
        df = df.merge(status_slim, on=["geo_name", "case_name"], how="left")

        # Count excluded statuses before filtering
        for bad_status in EXCLUDE_STATUSES:
            n = int((df["status"] == bad_status).sum())
            if n:
                excluded_counts[bad_status] = n

        # Remove bad statuses
        df = df[~df["status"].isin(EXCLUDE_STATUSES)].copy()
    else:
        excluded_counts["status_csv_missing"] = 0

    # Keep only final-comparison rows
    n_before = len(df)
    df = df[df["use_for_final_comparison"] == True].copy()  # noqa: E712
    n_excluded_flag = n_before - len(df)
    if n_excluded_flag:
        excluded_counts["use_for_final_comparison==False"] = n_excluded_flag

    return df, excluded_counts


# ----------------------------------------------------------
# Helpers: geometry ordering
# ----------------------------------------------------------

def ordered_geos(df: pd.DataFrame) -> list[str]:
    """Return GEO_ORDER intersected with geometries actually present, preserving order."""
    present = set(df["geo_name"].unique())
    ordered = [g for g in GEO_ORDER if g in present]
    # Append any geometries not in GEO_ORDER, sorted, so they aren't silently dropped
    extras = sorted(present - set(ordered))
    if extras:
        warnings.warn(
            f"\nWARNING: Geometries not in GEO_ORDER will be appended at the end: {extras}",
            stacklevel=2,
        )
    return ordered + extras


def make_condition_label(u: float, p_mpa: float) -> str:
    return f"u={u:.1f}, P={p_mpa:.0f} MPa"


# ----------------------------------------------------------
# Helpers: output
# ----------------------------------------------------------

def save_fig(fig: plt.Figure, out_dir: Path, stem: str) -> tuple[Path, Path]:
    png = out_dir / f"{stem}.png"
    pdf = out_dir / f"{stem}.pdf"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    return png, pdf


# ----------------------------------------------------------
# Output 1: cleaned_valid_cases.csv
# ----------------------------------------------------------

def write_cleaned_csv(df: pd.DataFrame, out_dir: Path) -> Path:
    path = out_dir / "cleaned_valid_cases.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ----------------------------------------------------------
# Output 2: representative_condition_table_u0p2_p6M.csv
# ----------------------------------------------------------

def write_representative_table(df: pd.DataFrame, out_dir: Path) -> Path:
    mask = (
        np.isclose(df["inlet_velocity_value"], REP_U, atol=1e-9)
        & np.isclose(df["outlet_pressure_MPa"], REP_P, atol=1e-9)
    )
    sub = df[mask].copy()

    if sub.empty:
        warnings.warn(
            f"\nWARNING: No valid cases found for the representative condition "
            f"u={REP_U}, P={REP_P} MPa. Table will be empty.",
            stacklevel=2,
        )

    cols = [
        "geo_name",
        "lmh_mass_balance",
        "pressure_drop_spacer",
        "pressure_drop_spacer_per_m_kPa_m",
        "cp_inlet_avg",
        "wall_shear_rate_avg",
    ]
    existing_cols = [c for c in cols if c in sub.columns]
    out = sub[existing_cols].copy()

    # Sort by GEO_ORDER
    geo_cat = pd.Categorical(out["geo_name"], categories=ordered_geos(out), ordered=True)
    out["_geo_order"] = geo_cat
    out = out.sort_values("_geo_order").drop(columns="_geo_order").reset_index(drop=True)

    path = out_dir / "representative_condition_table_u0p2_p6M.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ----------------------------------------------------------
# Output 3: geometry_metric_ranges.csv
# ----------------------------------------------------------

def write_metric_ranges(df: pd.DataFrame, out_dir: Path) -> tuple[Path, pd.DataFrame]:
    metrics = REQUIRED_METRIC_COLS + ["pressure_drop_spacer_per_m_kPa_m"]
    geos = ordered_geos(df)
    rows = []
    for geo in geos:
        sub = df[df["geo_name"] == geo]
        for metric in metrics:
            if metric not in df.columns:
                continue
            vals = sub[metric].dropna()
            rows.append({
                "geo_name": geo,
                "metric": metric,
                "count": len(vals),
                "min": vals.min() if len(vals) else np.nan,
                "mean": vals.mean() if len(vals) else np.nan,
                "max": vals.max() if len(vals) else np.nan,
            })
    range_df = pd.DataFrame(rows)
    path = out_dir / "geometry_metric_ranges.csv"
    range_df.to_csv(path, index=False, encoding="utf-8-sig")
    return path, range_df


# ----------------------------------------------------------
# Output 4: geometry_metric_range_figure
# ----------------------------------------------------------

def make_range_figure(df: pd.DataFrame, range_df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    geos = ordered_geos(df)
    n = len(geos)

    panel_specs = [
        ("lmh_mass_balance",               "LMH (mass balance)\n[LMH]"),
        ("pressure_drop_spacer_per_m_kPa_m", "Spacer ΔP per length\n[kPa/m]"),
        ("cp_inlet_avg",                   "CP inlet avg\n[-]"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle("Geometry Performance Ranges", fontsize=13, fontweight="bold", y=1.01)

    x = np.arange(n)
    colors = plt.cm.tab10(np.linspace(0, 0.9, n))

    for ax, (metric, ylabel) in zip(axes, panel_specs):
        sub = range_df[range_df["metric"] == metric].copy()
        # Reindex to geo order so x positions align
        sub = sub.set_index("geo_name").reindex(geos)

        for i, geo in enumerate(geos):
            row = sub.loc[geo] if geo in sub.index else None
            if row is None or pd.isna(row["mean"]):
                continue
            lo, hi, mean = row["min"], row["max"], row["mean"]
            ax.plot([x[i], x[i]], [lo, hi], color=colors[i], linewidth=3,
                    solid_capstyle="round", zorder=2)
            ax.scatter(x[i], mean, color=colors[i], s=80, zorder=3,
                       edgecolors="white", linewidths=1.2, label=geo)
            ax.scatter(x[i], lo, color=colors[i], marker="_", s=120, zorder=3, linewidths=2)
            ax.scatter(x[i], hi, color=colors[i], marker="_", s=120, zorder=3, linewidths=2)

        ax.set_xticks(x)
        ax.set_xticklabels(
            [g.replace("_", "\n") for g in geos],
            fontsize=7.5,
            rotation=0,
            ha="center",
        )
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_title(ylabel.split("\n")[0], fontsize=10, fontweight="bold")
        ax.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Legend outside the last panel
    handles = [
        plt.Line2D([0], [0], color=colors[i], linewidth=3, label=geos[i])
        for i in range(n)
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=min(n, 4),
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(0.5, -0.07),
    )
    fig.text(
        0.5, -0.13,
        "Vertical bar = min–max range   ●  = mean",
        ha="center", fontsize=8, color="dimgray",
    )

    fig.tight_layout()
    return save_fig(fig, out_dir, "geometry_metric_range_figure")


# ----------------------------------------------------------
# Shared heatmap builder
# ----------------------------------------------------------

def _build_heatmap_data(
    df: pd.DataFrame,
    metric: str,
    geos: list[str],
    conditions: list[tuple[float, float]],
    rank_ascending: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (value_pivot, rank_pivot).
    rank_ascending=True  → small value = rank 1
    rank_ascending=False → large value = rank 1
    """
    rows_val, rows_rank = [], []
    for geo in geos:
        sub = df[df["geo_name"] == geo]
        vals = {}
        for u, p in conditions:
            mask = (
                np.isclose(sub["inlet_velocity_value"], u, atol=1e-9)
                & np.isclose(sub["outlet_pressure_MPa"], p, atol=1e-9)
            )
            row = sub[mask]
            vals[make_condition_label(u, p)] = row[metric].iloc[0] if not row.empty else np.nan
        rows_val.append({"geo_name": geo, **vals})

    val_pivot = pd.DataFrame(rows_val).set_index("geo_name")
    # Rank within each geo (row) across conditions — method='min', axis=1
    rank_pivot = val_pivot.rank(axis=1, ascending=rank_ascending, method="min")
    return val_pivot, rank_pivot


def _draw_heatmap(
    ax: plt.Axes,
    val_pivot: pd.DataFrame,
    rank_pivot: pd.DataFrame,
    title: str,
    cmap_name: str,
    fmt_val: str,
    show_rank: bool,
    note: str | None = None,
) -> None:
    data = val_pivot.values.astype(float)
    rows, cols = data.shape

    # Normalize for color (ignore NaN)
    vmin = np.nanmin(data)
    vmax = np.nanmax(data)
    if vmin == vmax:
        vmax = vmin + 1.0

    cmap = plt.get_cmap(cmap_name)

    for r in range(rows):
        for c in range(cols):
            v = data[r, c]
            if np.isnan(v):
                color = "#eeeeee"
                cell_text = "N/A"
            else:
                norm_v = (v - vmin) / (vmax - vmin)
                color = cmap(norm_v)
                rank_v = int(rank_pivot.values[r, c])
                val_str = format(v, fmt_val)
                if show_rank:
                    cell_text = f"#{rank_v}\n{val_str}"
                else:
                    cell_text = val_str
            rect = plt.Rectangle([c, rows - r - 1], 1, 1, facecolor=color, edgecolor="white", linewidth=0.8)
            ax.add_patch(rect)

            # Choose text color for readability
            try:
                rgba = cmap((v - vmin) / (vmax - vmin))
                lum = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
                txt_color = "white" if lum < 0.5 else "black"
            except Exception:
                txt_color = "black"

            ax.text(
                c + 0.5, rows - r - 0.5,
                cell_text,
                ha="center", va="center",
                fontsize=7.5, color=txt_color,
                fontweight="bold" if show_rank else "normal",
            )

    ax.set_xlim(0, cols)
    ax.set_ylim(0, rows)
    ax.set_xticks(np.arange(cols) + 0.5)
    ax.set_xticklabels(list(val_pivot.columns), fontsize=7, rotation=30, ha="right")
    ax.set_yticks(np.arange(rows) + 0.5)
    ax.set_yticklabels(list(reversed(list(val_pivot.index))), fontsize=8)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=6)
    ax.tick_params(length=0)

    if note:
        ax.set_xlabel(note, fontsize=6.5, color="dimgray", labelpad=4)


def make_heatmap_figure(
    df: pd.DataFrame,
    geos: list[str],
    conditions: list[tuple[float, float]],
    out_dir: Path,
    stem: str,
    main_title: str,
    performance_mode: bool,
) -> tuple[Path, Path]:
    """
    performance_mode=True  → perf ranking: LMH higher=rank1, ΔP/CP lower=rank1
    performance_mode=False → value ranking: largest=rank1 for all metrics
    """
    panel_specs = [
        # (metric, panel_title, fmt, cmap, rank_ascending_for_perf)
        ("lmh_mass_balance",               "LMH (mass balance)\n[LMH]",          ".2f",  "Blues",  False),
        ("pressure_drop_spacer_per_m_kPa_m", "Spacer ΔP per length\n[kPa/m]",   ".1f",  "Reds",   True),
        ("cp_inlet_avg",                   "CP inlet avg\n[-]",                   ".3f",  "Oranges", True),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(16, max(3, len(geos) * 0.55 + 2.5)))
    fig.suptitle(main_title, fontsize=12, fontweight="bold", y=1.01)

    for ax, (metric, title, fmt, cmap, rank_asc_perf) in zip(axes, panel_specs):
        if metric not in df.columns:
            ax.set_visible(False)
            continue

        rank_ascending = rank_asc_perf if performance_mode else False

        val_pivot, rank_pivot = _build_heatmap_data(
            df, metric, geos, conditions, rank_ascending=rank_ascending,
        )

        note = None
        if not performance_mode:
            if "ΔP" in title or "CP" in title:
                note = "Note: rank 1 = largest value (not best performance)"

        _draw_heatmap(
            ax=ax,
            val_pivot=val_pivot,
            rank_pivot=rank_pivot,
            title=title,
            cmap_name=cmap,
            fmt_val=fmt,
            show_rank=True,
            note=note,
        )

    fig.tight_layout()
    return save_fig(fig, out_dir, stem)


# ----------------------------------------------------------
# Output 7 & 8: scatter plots
# ----------------------------------------------------------

def make_scatter(
    df: pd.DataFrame,
    geos: list[str],
    out_dir: Path,
    x_col: str,
    y_col: str,
    x_label: str,
    y_label: str,
    title: str,
    stem: str,
) -> tuple[Path, Path]:
    fig, ax = plt.subplots(figsize=(9, 6))
    colors = plt.cm.tab10(np.linspace(0, 0.9, len(geos)))
    geo_color = {g: colors[i] for i, g in enumerate(geos)}

    for geo in geos:
        sub = df[df["geo_name"] == geo]
        if sub.empty:
            continue
        ax.scatter(
            sub[x_col], sub[y_col],
            label=geo,
            color=geo_color[geo],
            s=70, alpha=0.85, edgecolors="white", linewidths=0.8,
            zorder=3,
        )

    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel(y_label, fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.legend(
        loc="best", fontsize=8, frameon=True,
        framealpha=0.85, edgecolor="#cccccc",
        ncol=2 if len(geos) > 4 else 1,
    )
    ax.grid(linestyle="--", alpha=0.45, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return save_fig(fig, out_dir, stem)


# ----------------------------------------------------------
# Output 9: analysis_summary.txt
# ----------------------------------------------------------

def write_analysis_summary(
    n_total: int,
    n_valid: int,
    excluded_counts: dict[str, int],
    valid_df: pd.DataFrame,
    output_files: list[Path],
    out_dir: Path,
) -> Path:
    lines = [
        "=" * 60,
        "POST-PROCESSING ANALYSIS SUMMARY",
        "=" * 60,
        "",
        f"Total rows loaded from all_cases_post_summary.csv : {n_total}",
        f"Valid rows after filtering                        : {n_valid}",
        "",
        "Excluded row counts by reason:",
    ]
    if excluded_counts:
        for reason, count in excluded_counts.items():
            lines.append(f"  {reason:<38}: {count}")
    else:
        lines.append("  (none)")

    lines += ["", "Geometry counts in valid data:"]
    if not valid_df.empty:
        geo_counts = valid_df["geo_name"].value_counts().reindex(ordered_geos(valid_df), fill_value=0)
        for geo, cnt in geo_counts.items():
            lines.append(f"  {geo:<30}: {cnt}")
    else:
        lines.append("  (no valid cases)")

    lines += ["", "Output files written:"]
    for f in output_files:
        lines.append(f"  {f}")

    lines += ["", "=" * 60]

    text = "\n".join(lines)
    path = out_dir / "analysis_summary.txt"
    path.write_text(text, encoding="utf-8")
    print(text)
    return path


# ----------------------------------------------------------
# Main
# ----------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build comparison tables and figures from aggregate post CSVs."
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=None,
        help="Default: RO_DATA_ROOT/inventory/all_cases_post_summary.csv.",
    )
    parser.add_argument(
        "--status-csv",
        type=Path,
        default=None,
        help="Default: RO_DATA_ROOT/inventory/all_cases_post_status.csv.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Default: RO_DATA_ROOT/inventory/post_summary_figures.",
    )
    return parser.parse_args(argv)


def resolve_path_defaults(args: argparse.Namespace) -> argparse.Namespace:
    inventory = data_root() / "inventory"
    args.summary_csv = (args.summary_csv or inventory / "all_cases_post_summary.csv")
    args.status_csv = args.status_csv or inventory / "all_cases_post_status.csv"
    args.out_dir = args.out_dir or inventory / "post_summary_figures"
    return args


def main(argv: list[str] | None = None) -> None:
    args = resolve_path_defaults(parse_args(argv))
    summary_csv = args.summary_csv
    status_csv = args.status_csv
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    output_files: list[Path] = []

    # ---- Load data ----
    summary = load_summary(summary_csv)
    status_df = load_status(status_csv)
    n_total = len(summary)

    check_required_columns(summary, REQUIRED_ID_COLS, "all_cases_post_summary.csv")
    check_required_columns(summary, REQUIRED_METRIC_COLS, "all_cases_post_summary.csv")

    # ---- Derive kPa/m column ----
    summary["pressure_drop_spacer_per_m_kPa_m"] = summary["pressure_drop_spacer_per_m"] / 1000.0

    # ---- Filter ----
    valid_df, excluded_counts = filter_valid_cases(summary, status_df)
    n_valid = len(valid_df)

    if valid_df.empty:
        warnings.warn(
            "\nWARNING: No valid cases remain after filtering. "
            "All figure and table outputs will be empty or skipped.",
            stacklevel=1,
        )

    geos = ordered_geos(valid_df) if not valid_df.empty else []

    # Unique conditions sorted by (u, P) for heatmap columns
    if not valid_df.empty:
        cond_pairs = sorted(
            set(
                zip(
                    valid_df["inlet_velocity_value"].round(4),
                    valid_df["outlet_pressure_MPa"].round(4),
                )
            )
        )
    else:
        cond_pairs = []

    # ---- Output 1: cleaned CSV ----
    output_files.append(write_cleaned_csv(valid_df, out_dir))

    # ---- Output 2: representative condition table ----
    output_files.append(write_representative_table(valid_df, out_dir))

    # ---- Output 3: metric ranges ----
    if not valid_df.empty:
        ranges_path, range_df = write_metric_ranges(valid_df, out_dir)
        output_files.append(ranges_path)
    else:
        range_df = pd.DataFrame()

    # ---- Output 4: range figure ----
    if not valid_df.empty and not range_df.empty:
        png, pdf = make_range_figure(valid_df, range_df, out_dir)
        output_files.extend([png, pdf])

    # ---- Output 5: performance ranking heatmap ----
    if not valid_df.empty and cond_pairs:
        png, pdf = make_heatmap_figure(
            df=valid_df,
            geos=geos,
            conditions=cond_pairs,
            out_dir=out_dir,
            stem="performance_ranking_heatmap",
            main_title="Performance Ranking Heatmap\n(rank 1 = best performance per metric)",
            performance_mode=True,
        )
        output_files.extend([png, pdf])

    # ---- Output 6: value ranking heatmap ----
    if not valid_df.empty and cond_pairs:
        png, pdf = make_heatmap_figure(
            df=valid_df,
            geos=geos,
            conditions=cond_pairs,
            out_dir=out_dir,
            stem="value_ranking_heatmap",
            main_title="Value Ranking Heatmap\n(rank 1 = largest numerical value for all metrics)",
            performance_mode=False,
        )
        output_files.extend([png, pdf])

    # ---- Output 7: LMH vs pressure drop scatter ----
    if not valid_df.empty:
        png, pdf = make_scatter(
            df=valid_df,
            geos=geos,
            out_dir=out_dir,
            x_col="pressure_drop_spacer_per_m_kPa_m",
            y_col="lmh_mass_balance",
            x_label="Spacer ΔP per length [kPa/m]",
            y_label="LMH mass balance [LMH]",
            title="LMH vs. Spacer Pressure Drop",
            stem="lmh_vs_pressure_drop_scatter",
        )
        output_files.extend([png, pdf])

    # ---- Output 8: CP vs pressure drop scatter ----
    if not valid_df.empty:
        png, pdf = make_scatter(
            df=valid_df,
            geos=geos,
            out_dir=out_dir,
            x_col="pressure_drop_spacer_per_m_kPa_m",
            y_col="cp_inlet_avg",
            x_label="Spacer ΔP per length [kPa/m]",
            y_label="CP inlet avg [-]",
            title="CP Inlet Avg vs. Spacer Pressure Drop",
            stem="cp_vs_pressure_drop_scatter",
        )
        output_files.extend([png, pdf])

    # ---- Output 9: analysis summary ----
    summary_path = write_analysis_summary(
        n_total=n_total,
        n_valid=n_valid,
        excluded_counts=excluded_counts,
        valid_df=valid_df,
        output_files=output_files,
        out_dir=out_dir,
    )
    output_files.append(summary_path)

    print(f"\nDone. Outputs written to:\n  {out_dir}")


if __name__ == "__main__":
    main()
