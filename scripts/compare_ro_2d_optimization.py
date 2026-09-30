"""Compare one HF-only run with one MFBO run. Does not launch Fluent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print HF-only BO and MFBO summaries side by side.",
    )
    parser.add_argument("--hf", type=Path, required=True, help="HF experiment directory.")
    parser.add_argument("--mfbo", type=Path, required=True, help="MFBO experiment directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        hf = _load(args.hf)
        mf = _load(args.mfbo)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format(hf))
    print()
    print(_format(mf))
    print()
    print(
        "Comparison uses HF-validated feasible LMH against cumulative "
        "HF-equivalent solver cost. This does not declare a winner."
    )
    return 0


def _load(directory: Path) -> dict[str, object]:
    path = directory / "final_summary.json"
    if not path.is_file():
        raise ValueError(f"Missing {path}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not an object.")
    return payload


def _format(summary: dict[str, object]) -> str:
    final = summary.get("final_hf_validated")
    if not isinstance(final, dict):
        final = {}
    return "\n".join(
        [
            f"method={summary.get('method')} mode={summary.get('mode')}",
            f"n_medium={summary.get('n_medium')} n_very_fine={summary.get('n_very_fine')}",
            f"solver_s={summary.get('cumulative_solver_s')}",
            f"hf_equivalent={summary.get('cumulative_hf_equivalent')}",
            f"wall_s={summary.get('cumulative_wall_s')}",
            f"n_failures={summary.get('n_failures')}",
            (
                "final_hf "
                f"d_m={final.get('d_m')} L_m={final.get('L_m')} "
                f"lmh={final.get('hf_lmh')} dp_per_l={final.get('hf_dp_per_l')} "
                f"cp={final.get('hf_cp')}"
            ),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
