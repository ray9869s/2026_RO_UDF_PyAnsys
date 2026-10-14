"""Run 3D pillar HF-only BO or MFBO, or build an offline replay table.

Importing this module does not launch Fluent. ``run --mode`` comes from
the experiment file. Live mode imports ``ro.mfbo_drivers`` and does launch
the existing CAD, mesh, solve, and extract scripts, one job at a time.
``offline_replay`` only reads the replay table. A ``TBD`` constraint or
fidelity is refused before any driver starts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.mfbo_3d import (  # noqa: E402
    LiveEvaluator,
    build_campaign_replay_table,
    experiment_dir,
    load_experiment,
    run_optimization,
)
from ro.mfbo_adapter import resolve_data_root  # noqa: E402
from ro_2d_pilot.optimization.experiment import MODE_LIVE  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="3D pillar constrained BO. Live mode runs one case at a time.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run hf_bo or mfbo from an experiment file.")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--data-root", required=True)
    build = sub.add_parser(
        "build-replay",
        help="Build an offline table from campaign pillar leaves at one mesh.",
    )
    build.add_argument("--data-root", required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--mesh-id", required=True)
    build.add_argument("--fidelity", required=True)
    build.add_argument("--out", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build-replay":
            return _build_replay(args)
        return _run(args)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _run(args) -> int:
    root = resolve_data_root(args.data_root)
    config = load_experiment(args.config, repo_root=_REPO, data_root=root)
    if config.mode == MODE_LIVE:
        from ro.mfbo_drivers import make_drivers

        evaluator = LiveEvaluator(
            root,
            config.fidelity_settings,
            config.run_id,
            config.d_f_mm,
            make_drivers(root),
        )
    else:
        if config.replay is None:
            raise ValueError("offline_replay requires a replay table.")
        evaluator = config.replay
    destination = experiment_dir(root, config.method, config.experiment_id)
    summary = run_optimization(config, evaluator, destination)
    if not summary.get("wrote"):
        print(f"stop file present ({destination / 'STOP'}); not starting.", flush=True)
        return 0
    final = summary["final_hf_validated"]
    print(
        f"method={summary['method']} mode={summary['mode']} "
        f"hf_equivalent={summary['cumulative_hf_equivalent']} "
        f"n_lf={summary['n_lf']} n_hf={summary['n_hf']} "
        f"hf_lmh={final['hf_lmh']} stopped={summary['stopped']}"
    )
    print(f"wrote {destination}")
    return 0


def _build_replay(args) -> int:
    out = args.out
    if not out.is_absolute():
        raise ValueError(f"--out must be absolute, got {args.out!r}.")
    resolve_data_root(out.parent)
    table = build_campaign_replay_table(
        args.data_root,
        run_id=args.run_id,
        mesh_id=args.mesh_id,
        fidelity=args.fidelity,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    absent = table["absent_geo_ids"]
    print(
        f"wrote {len(table['rows'])} rows to {out}. "
        f"absent={absent if absent else 'none'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
