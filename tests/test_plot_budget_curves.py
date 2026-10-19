"""Poster curves from experiment histories. No Fluent."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from helpers import REPO_ROOT, load_module


def test_budget_curves_write_csv_and_png(tmp_path: Path) -> None:
    hf = tmp_path / "hf_bo" / "v2"
    mf = tmp_path / "mfbo" / "v2"
    _write_experiment(
        hf,
        method="hf_bo",
        experiment_id="v2",
        seed=0,
        budget=22.98,
        rows=[
            (1.0, None),
            (2.0, 20.0),
            (22.14, 24.414),
        ],
    )
    _write_experiment(
        mf,
        method="mfbo",
        experiment_id="v2",
        seed=0,
        budget=22.98,
        rows=[
            (1.5, None),
            (10.0, 22.0),
            (25.98, 24.2),
        ],
    )
    script = load_module(
        "plot_budget_curves_under_test",
        REPO_ROOT / "scripts" / "mfbo" / "plot_budget_curves.py",
    )
    out_csv = tmp_path / "curves.csv"
    out_png = tmp_path / "curves.png"
    code = script.main([str(hf), str(mf), "--out-csv", str(out_csv), "--out-png", str(out_png)])
    assert code == 0
    with out_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["label"] for row in rows] == ["hf_bo v2 seed 0"] * 3 + ["mfbo v2 seed 0"] * 3
    assert rows[2]["cumulative_hf_equivalent"] == "22.14"
    assert rows[2]["best_hf_feasible_lmh"] == "24.414"
    assert rows[5]["cumulative_hf_equivalent"] == "25.98"
    assert rows[5]["best_hf_feasible_lmh"] == "24.2"
    assert rows[0]["best_hf_feasible_lmh"] == ""
    assert out_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out_png.stat().st_size > 1000


def test_budget_curves_refuse_a_missing_history(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    script = load_module(
        "plot_budget_curves_missing",
        REPO_ROOT / "scripts" / "mfbo" / "plot_budget_curves.py",
    )
    code = script.main(
        [str(empty), "--out-csv", str(tmp_path / "curves.csv"), "--out-png", str(tmp_path / "curves.png")]
    )
    assert code == 1
    assert not (tmp_path / "curves.csv").is_file()


def _write_experiment(
    directory: Path,
    *,
    method: str,
    experiment_id: str,
    seed: int,
    budget: float,
    rows: list[tuple[float, float | None]],
) -> None:
    directory.mkdir(parents=True)
    history = []
    for step, (cost, best) in enumerate(rows, start=1):
        history.append(
            {
                "step": step,
                "cumulative_hf_equivalent": cost,
                "best_hf_feasible_lmh": best,
            }
        )
    (directory / "history.json").write_text(json.dumps(history), encoding="utf-8")
    (directory / "final_summary.json").write_text(
        json.dumps(
            {
                "method": method,
                "experiment_id": experiment_id,
                "seed": seed,
                "budget_hf_equivalent": budget,
            }
        ),
        encoding="utf-8",
    )
