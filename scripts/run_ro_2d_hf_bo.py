"""Run the HF-only BO pilot. offline_replay does not launch Fluent."""

from __future__ import annotations

import argparse
import sys

from ro_2d_pilot.optimization.experiment import (
    METHOD_HF,
    MODE_LIVE,
    MODE_REPLAY,
    ExperimentConfig,
    LiveEvaluator,
    ReplayEvaluator,
    experiment_dir,
    run_optimization,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HF-only constrained BO. Live mode runs one 2D case at a time.",
    )
    parser.add_argument("--mode", choices=(MODE_REPLAY, MODE_LIVE), required=True)
    parser.add_argument("--experiment-id", default="pilot")
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = ExperimentConfig(
        method=METHOD_HF,
        mode=args.mode,
        experiment_id=args.experiment_id,
        seed=args.seed,
    )
    try:
        from ro_2d_pilot.paths import data_root

        root = data_root()
        destination = experiment_dir(root, METHOD_HF, args.experiment_id)
        evaluator = LiveEvaluator(root) if args.mode == MODE_LIVE else ReplayEvaluator()
        summary = run_optimization(config, evaluator, destination)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        f"method={summary['method']} mode={summary['mode']} "
        f"hf_equivalent={summary['cumulative_hf_equivalent']} "
        f"hf_lmh={summary['final_hf_validated']['hf_lmh']}"
    )
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
