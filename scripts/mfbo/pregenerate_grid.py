"""Write every valid grid geometry before the optimization loop.

Importing this module does not launch Discovery. ``emit-queue`` writes one
``job_queue`` job per chunk, so ``<queue>.stop`` finishes the current chunk
and does not start the next. ``run-chunk`` builds that chunk with
``generate_pillar_cad.py --batch`` in a new process (one Discovery session)
and, if that process fails, retries once in another process. A geometry
that already passed is skipped. The batch path writes
``<geo_id>_generate_wall_time.json``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ro.geometry_registry import format_mfbo_pillar_geo_id  # noqa: E402
from ro.mfbo_3d import (  # noqa: E402
    HF,
    LF,
    PillarPoint,
    _candidates,
    geometry_block_reason,
    load_experiment,
)
from ro.mfbo_adapter import (  # noqa: E402
    FAMILY,
    PillarDesign,
    _geometry_state,
    resolve_data_root,
)
from ro.mfbo_fidelity import parse_fidelity_level  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_GENERATE = _REPO / "scripts" / "generate_pillar_cad.py"
_CHUNK_ATTEMPTS = 2
_DEFAULT_CHUNK_SIZE = 40
_BLOCKING_SUFFIXES = (".pmdb", ".scdocx", "_meta.json")


def geometries_for_grid(config, data_root: Path) -> list[dict]:
    """HF (unsuffixed, 7 cells) then LF (``_n3``) for every point that passes."""
    if HF not in config.fidelity_settings or LF not in config.fidelity_settings:
        raise ValueError("pregenerate needs both LF and HF in the fidelity table.")
    root = Path(data_root)
    entries = []
    for point in _candidates(config):
        for fidelity in (HF, LF):
            entry = _entry(config, root, point, fidelity)
            if entry is not None:
                entries.append(entry)
    return entries


def chunk_entries(entries: list[dict], chunk_size: int) -> list[list[dict]]:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError(f"chunk size must be an integer >= 1, got {chunk_size!r}.")
    return [entries[start : start + chunk_size] for start in range(0, len(entries), chunk_size)]


def geometry_passed(entry: dict) -> bool:
    design = PillarDesign(float(entry["d_p_mm"]), float(entry["d_h_mm"]), float(entry["d_f_mm"]))
    return _geometry_state(Path(entry["out_dir"]), entry["geo_id"], design) == "success"


def clear_incomplete(entry: dict) -> None:
    """Drop partial CAD so a retry can write. A passing geometry is left alone."""
    if geometry_passed(entry):
        return
    directory = Path(entry["out_dir"])
    geo_id = entry["geo_id"]
    for suffix in _BLOCKING_SUFFIXES:
        path = directory / f"{geo_id}{suffix}"
        if path.is_file():
            path.unlink()


def default_batch_command(batch_path: Path) -> list[str]:
    """One new interpreter. ``--batch`` is one Discovery session for the file."""
    return [sys.executable, str(_GENERATE), "--batch", str(batch_path)]


def execute_chunk(batch_path: Path, *, command=None, attempts: int = _CHUNK_ATTEMPTS) -> int:
    """Run one chunk. Returns 0 when every entry passed, else 1 after the retries."""
    path = Path(batch_path)
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"chunk must be a non-empty JSON list: {path}")
    skipped = sum(1 for entry in entries if geometry_passed(entry))
    builder = default_batch_command if command is None else command
    started = 0
    for _ in range(attempts):
        pending = [entry for entry in entries if not geometry_passed(entry)]
        if not pending:
            break
        for entry in pending:
            clear_incomplete(entry)
        pending_path = path.with_suffix(".pending.json")
        pending_path.write_text(json.dumps(pending, indent=2) + "\n", encoding="utf-8")
        started += 1
        code = _run_batch(builder(pending_path))
        print(
            f"attempt={started} return_code={code} pending={len(pending)}",
            flush=True,
        )
        if all(geometry_passed(entry) for entry in pending):
            break
    failed = sum(1 for entry in entries if not geometry_passed(entry))
    generated = len(entries) - skipped - failed
    print(
        f"chunk={path.name} skipped={skipped} generated={generated} "
        f"failed={failed} attempts={started}",
        flush=True,
    )
    return 0 if failed == 0 else 1


def write_queue(config, data_root: Path, out: Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> list[dict]:
    """One job per chunk of the full valid grid. Already-passed geometries stay in the chunk."""
    entries = geometries_for_grid(config, data_root)
    if not entries:
        raise ValueError("no grid geometries passed the opening check.")
    chunks = chunk_entries(entries, chunk_size)
    chunk_dir = out.parent / f"{out.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for entry in entries if geometry_passed(entry))
    jobs = []
    for index, chunk in enumerate(chunks):
        chunk_path = chunk_dir / f"chunk-{index:03d}.json"
        chunk_path.write_text(json.dumps(chunk, indent=2) + "\n", encoding="utf-8")
        jobs.append(
            {
                "id": f"{config.experiment_id}-pregenerate-{index:03d}",
                "argv": [
                    "{python}",
                    "scripts/mfbo/pregenerate_grid.py",
                    "run-chunk",
                    "--batch",
                    chunk_path.as_posix(),
                ],
                "cwd": str(_REPO),
                "env": {"RO_DATA_ROOT": data_root.as_posix()},
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    points = len({(entry["d_p_mm"], entry["d_h_mm"]) for entry in entries})
    print(
        f"points={points} geometries={len(entries)} already_passed={passed} "
        f"pending={len(entries) - passed} chunks={len(chunks)} chunk_size={chunk_size}",
        flush=True,
    )
    print(f"wrote {len(jobs)} jobs to {out}", flush=True)
    return jobs


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Pregenerate HF and LF pillar geometries for a 3D experiment grid.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    emit = sub.add_parser(
        "emit-queue",
        help="Write a job_queue JSON, one chunk per job. Does not launch Discovery.",
    )
    emit.add_argument("--config", required=True, type=Path)
    emit.add_argument("--data-root", required=True)
    emit.add_argument("--out", required=True, type=Path)
    emit.add_argument("--chunk-size", type=int, default=_DEFAULT_CHUNK_SIZE)
    run = sub.add_parser(
        "run-chunk",
        help="Build one chunk in a new process, and retry that process once.",
    )
    run.add_argument("--batch", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "emit-queue":
            root = resolve_data_root(args.data_root)
            config = load_experiment(
                args.config,
                repo_root=_REPO,
                data_root=root,
                method_override="mfbo",
            )
            write_queue(config, root, args.out, chunk_size=args.chunk_size)
            return 0
        return execute_chunk(args.batch)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _entry(config, data_root: Path, point: PillarPoint, fidelity: str) -> dict | None:
    settings = parse_fidelity_level(config.fidelity_settings[fidelity])
    if geometry_block_reason(
        point.d_p_mm,
        point.d_h_mm,
        config.d_f_mm,
        float(settings["m_min"]),
    ):
        return None
    n_active = settings["n_active"] if settings.get("extended") else None
    geo_id = format_mfbo_pillar_geo_id(
        point.d_p_mm,
        point.d_h_mm,
        config.d_f_mm,
        n_active=n_active,
    )
    return {
        "d_p_mm": point.d_p_mm,
        "d_h_mm": point.d_h_mm,
        "d_f_mm": config.d_f_mm,
        "geo_id": geo_id,
        "out_dir": (data_root / "geometries" / FAMILY / geo_id).as_posix(),
        "n_active": 7 if n_active is None else int(n_active),
    }


def _run_batch(argv: list[str]) -> int:
    try:
        completed = subprocess.run(argv, check=False)
    except OSError as exc:
        print(f"batch process failed to start: {exc}", file=sys.stderr)
        return 1
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
