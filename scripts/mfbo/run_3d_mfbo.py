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
    GEOMETRY_PREGENERATED,
    METHOD_HF,
    METHOD_MF,
    LiveEvaluator,
    build_campaign_replay_table,
    experiment_dir,
    format_plan,
    format_recorded_initial_charges,
    load_experiment,
    require_initial_designs,
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
    run.add_argument(
        "--method",
        choices=(METHOD_HF, METHOD_MF),
        default=None,
        help="Override the method in the experiment file. The other fields stay shared.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the file and print the initial design and budget. Does not launch Fluent.",
    )
    run.add_argument(
        "--geometry-worker",
        action="store_true",
        help="Keep one Discovery session for every new geometry in this run.",
    )
    run.add_argument(
        "--stop-file",
        type=Path,
        default=None,
        help="Also release Discovery when this file appears. The experiment STOP file is always watched.",
    )
    build = sub.add_parser(
        "build-replay",
        help="Build an offline table from campaign pillar leaves at one mesh.",
    )
    build.add_argument("--data-root", required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--mesh-id", required=True)
    build.add_argument("--fidelity", required=True)
    build.add_argument("--out", required=True, type=Path)
    emit = sub.add_parser(
        "emit-queue",
        help="Write an hf_bo job and an mfbo job. Does not launch Fluent.",
    )
    emit.add_argument("--config", required=True, type=Path)
    emit.add_argument("--data-root", required=True)
    emit.add_argument("--out", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build-replay":
            return _build_replay(args)
        if args.command == "emit-queue":
            return _emit_queue(args)
        return _run(args)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _run(args) -> int:
    root = resolve_data_root(args.data_root)
    config = load_experiment(
        args.config,
        repo_root=_REPO,
        data_root=root,
        method_override=args.method,
    )
    if args.dry_run:
        if args.geometry_worker:
            raise ValueError("--dry-run does not launch Fluent or Discovery.")
        require_initial_designs(config)
        print(format_plan(config), flush=True)
        print(format_recorded_initial_charges(config, root), flush=True)
        _warn_screen_ratios(config, root)
        return 0
    destination = experiment_dir(root, config.method, config.experiment_id)
    supervisor = None
    if config.geometry == GEOMETRY_PREGENERATED and args.geometry_worker:
        raise ValueError(
            "geometry pregenerated does not launch Discovery. Omit --geometry-worker."
        )
    try:
        if config.mode == MODE_LIVE:
            from ro.mfbo_drivers import make_drivers

            if args.geometry_worker:
                from ro.geometry_worker import open_geometry_worker

                stop = args.stop_file
                if stop is None:
                    stop = destination / "STOP"
                supervisor = open_geometry_worker(root, enabled=True, stop_file=stop)
                drivers = make_drivers(
                    root,
                    geometry_worker=supervisor.directory,
                    geometry_supervisor=supervisor,
                )
            else:
                drivers = make_drivers(root)
            evaluator = LiveEvaluator(
                root,
                config.fidelity_settings,
                config.run_id,
                config.d_f_mm,
                drivers,
                geometry=config.geometry,
            )
        else:
            if args.geometry_worker:
                raise ValueError("--geometry-worker applies to live mode.")
            if config.replay is None:
                raise ValueError("offline_replay requires a replay table.")
            evaluator = config.replay
        summary = run_optimization(config, evaluator, destination)
    finally:
        if supervisor is not None:
            supervisor.shutdown()
    if not summary.get("wrote"):
        print(f"stop file present ({destination / 'STOP'}); not starting.", flush=True)
        return 0
    final = summary["final_hf_validated"]
    print(
        f"method={summary['method']} mode={summary['mode']} "
        f"hf_equivalent={summary['cumulative_hf_equivalent']} "
        f"n_lf={summary['n_lf']} n_hf={summary['n_hf']} "
        f"hf_lmh={final['hf_lmh']} stopped={summary['stopped']} "
        f"stop_reason={summary.get('stop_reason')} "
        f"infrastructure_wall_s={summary.get('infrastructure_wall_s')}"
    )
    print(f"wrote {destination}")
    if summary.get("stop_reason") == "infrastructure":
        return 1
    return 0


def _emit_queue(args) -> int:
    root = resolve_data_root(args.data_root)
    configs = []
    for method in (METHOD_HF, METHOD_MF):
        config = load_experiment(
            args.config,
            repo_root=_REPO,
            data_root=root,
            method_override=method,
        )
        require_initial_designs(config)
        configs.append(config)
    config_text = str(args.config)
    script = "scripts/mfbo/run_3d_mfbo.py"
    jobs = []
    for config in configs:
        argv = [
            "{python}",
            script,
            "run",
            "--config",
            config_text,
            "--data-root",
            root.as_posix(),
            "--method",
            config.method,
        ]
        if config.geometry != GEOMETRY_PREGENERATED:
            argv.append("--geometry-worker")
        jobs.append(
            {
                "id": f"{config.experiment_id}-{config.method}",
                "argv": argv,
                "cwd": str(_REPO),
                "env": {"RO_DATA_ROOT": root.as_posix()},
            }
        )
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(jobs)} jobs to {out}")
    return 0


def _warn_screen_ratios(config, data_root) -> None:
    if not config.screen_csv:
        return
    from ro.mfbo_landscape import cost_medians, read_screen_rows

    path = data_root / config.screen_csv
    if not path.is_file():
        return
    medians = cost_medians(read_screen_rows(path))
    _warn_ratio("lf_over_hf_cost_ratio", config.lf_over_hf_cost_ratio, medians["wall_cost_ratio_median"])
    _warn_ratio("cost_proxy_ratio", config.cost_proxy_ratio, medians["cost_proxy_ratio_median"])


def _warn_ratio(name: str, configured, measured) -> None:
    if configured is None or measured is None:
        return
    if abs(float(configured) - float(measured)) <= 5.0e-3:
        return
    print(
        f"warning: {name}={configured} and the screen median is {measured}.",
        file=sys.stderr,
    )


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
