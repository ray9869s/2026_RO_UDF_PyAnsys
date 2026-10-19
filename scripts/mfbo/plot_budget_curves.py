"""Best feasible HF LMH against cumulative HF-equivalent cost.

One directory is one experiment. The directory needs ``history.json``
or ``history.csv``. ``final_summary.json`` supplies the curve label
when it is present. The CSV is the long curve and the PNG draws one
step curve per experiment. This does not launch Fluent.

Compare runs at the same cumulative HF-equivalent by reading the best
feasible HF LMH on these curves. An endpoint past the budget is the
recorded cost of the query that crossed it, not a higher budget.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_COMPARE = (
    "Compare runs at the same cumulative HF-equivalent by the best "
    "feasible HF LMH. An endpoint past the budget is the recorded cost "
    "of the query that crossed it."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write best-feasible-HF-LMH vs cumulative HF-equivalent curves. "
            + _COMPARE
        ),
    )
    parser.add_argument(
        "experiments",
        nargs="+",
        type=Path,
        help="Experiment directories, one curve each.",
    )
    parser.add_argument("--out-csv", required=True, type=Path)
    parser.add_argument("--out-png", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        curves = [_load_curve(path) for path in args.experiments]
        _write_csv(args.out_csv, curves)
        _write_png(args.out_png, curves)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(_COMPARE)
    print(f"wrote {args.out_csv}")
    print(f"wrote {args.out_png}")
    return 0


def _load_curve(directory: Path) -> dict[str, object]:
    if not directory.is_dir():
        raise ValueError(f"Experiment directory does not exist: {directory}")
    history = _read_history(directory)
    summary = _read_summary(directory)
    label = _label(directory, summary)
    budget = _optional_float(summary.get("budget_hf_equivalent")) if summary else None
    rows = []
    for index, row in enumerate(history):
        if not isinstance(row, dict):
            raise ValueError(f"{directory} history row {index} is not an object.")
        cost = _required_float(row.get("cumulative_hf_equivalent"), directory, index)
        rows.append(
            {
                "label": label,
                "method": summary.get("method") if summary else None,
                "experiment_id": summary.get("experiment_id") if summary else None,
                "seed": summary.get("seed") if summary else None,
                "step": row.get("step", index + 1),
                "cumulative_hf_equivalent": cost,
                "best_hf_feasible_lmh": _optional_float(row.get("best_hf_feasible_lmh")),
                "budget_hf_equivalent": budget,
            }
        )
    return {"label": label, "budget_hf_equivalent": budget, "rows": rows}


def _read_history(directory: Path) -> list[object]:
    json_path = directory / "history.json"
    if json_path.is_file():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"{json_path} must be a non-empty list.")
        return payload
    csv_path = directory / "history.csv"
    if not csv_path.is_file():
        raise ValueError(f"{directory} has no history.json or history.csv.")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{csv_path} has no rows.")
    return rows


def _read_summary(directory: Path) -> dict[str, object] | None:
    path = directory / "final_summary.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not an object.")
    return payload


def _label(directory: Path, summary: dict[str, object] | None) -> str:
    if summary is None:
        return directory.name
    method = summary.get("method") or directory.name
    experiment_id = summary.get("experiment_id") or ""
    seed = summary.get("seed")
    text = f"{method} {experiment_id}".strip()
    if seed is not None:
        text = f"{text} seed {seed}"
    return text or directory.name


def _write_csv(path: Path, curves: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "label",
        "method",
        "experiment_id",
        "seed",
        "step",
        "cumulative_hf_equivalent",
        "best_hf_feasible_lmh",
        "budget_hf_equivalent",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for curve in curves:
            for row in curve["rows"]:
                writer.writerow({name: row.get(name) for name in fields})


def _write_png(path: Path, curves: list[dict[str, object]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7.0, 4.5))
    budgets = {
        curve["budget_hf_equivalent"]
        for curve in curves
        if curve["budget_hf_equivalent"] is not None
    }
    for curve in curves:
        xs = []
        ys = []
        for row in curve["rows"]:
            if row["best_hf_feasible_lmh"] is None:
                continue
            xs.append(row["cumulative_hf_equivalent"])
            ys.append(row["best_hf_feasible_lmh"])
        if not xs:
            continue
        axis.step(xs, ys, where="post", label=str(curve["label"]))
        axis.plot(xs, ys, linestyle="none", marker="o", markersize=3.5)
    if len(budgets) == 1:
        budget = next(iter(budgets))
        axis.axvline(budget, color="0.4", linestyle="--", linewidth=1.0, label=f"budget {budget:g}")
    axis.set_xlabel("Cumulative HF-equivalent")
    axis.set_ylabel("Best feasible HF LMH")
    axis.grid(True, color="0.9", linewidth=0.8)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _required_float(value: object, directory: Path, index: int) -> float:
    number = _optional_float(value)
    if number is None:
        raise ValueError(
            f"{directory} history row {index} is missing cumulative_hf_equivalent."
        )
    return number


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.casefold() == "none":
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a number, got {value!r}.") from exc
    return number


if __name__ == "__main__":
    raise SystemExit(main())
