"""Case+data checkpoints for a long solve. Does not launch Fluent.

``checkpoint_every`` is off unless it is a positive integer. A checkpoint
is ``{geo}_{run}_checkpoint_{iteration:06d}.cas.h5`` plus the matching
``.dat.h5``. Only the latest two complete pairs are kept. The setup case
and the final case use different names and are not checkpoints: reading
the setup case would start the iteration counter over and restart the
UDF source ramp.

Resume reads the latest pair in place and requests only the iterations
still inside ``max_iterations``. Fluent's iteration counter is the one
stored in the data file, so ``N_ITER`` continues and a checkpoint past
the source ramp stays on the full source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, NamedTuple

CHECKPOINT_KEEP = 2


class Checkpoint(NamedTuple):
    iteration: int
    case_path: Path
    data_path: Path


def require_checkpoint_every(value) -> int | None:
    """Return None when checkpointing is off, else a positive integer."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            "checkpoint_every must be a positive integer or None, "
            f"got {value!r}."
        )
    return value


def require_checkpoint_resume(value) -> bool:
    if not isinstance(value, bool):
        raise TypeError(
            f"checkpoint_resume must be a bool, got {value!r}."
        )
    return value


def checkpoint_case_name(geo: str, run: str, iteration: int) -> str:
    _positive_int("iteration", iteration)
    return f"{geo}_{run}_checkpoint_{iteration:06d}.cas.h5"


def checkpoint_case_path(
    directory: str | Path,
    geo: str,
    run: str,
    iteration: int,
) -> Path:
    return Path(directory) / checkpoint_case_name(geo, run, iteration)


def checkpoint_data_path(case_path: str | Path) -> Path:
    text = str(case_path)
    if not text.endswith(".cas.h5"):
        raise ValueError(
            f"checkpoint case path must end in .cas.h5, got {case_path!r}."
        )
    return Path(text[: -len(".cas.h5")] + ".dat.h5")


def list_checkpoints(
    directory: str | Path,
    geo: str,
    run: str,
) -> list[Checkpoint]:
    """Complete case+data pairs, oldest iteration first."""
    root = Path(directory)
    if not root.is_dir():
        return []
    pattern = re.compile(
        rf"^{re.escape(geo)}_{re.escape(run)}_checkpoint_"
        rf"(?P<iteration>\d+)\.cas\.h5$"
    )
    found: list[Checkpoint] = []
    for case_path in root.iterdir():
        if not case_path.is_file():
            continue
        match = pattern.fullmatch(case_path.name)
        if match is None:
            continue
        data_path = checkpoint_data_path(case_path)
        if not data_path.is_file():
            continue
        found.append(
            Checkpoint(
                int(match.group("iteration")),
                case_path,
                data_path,
            )
        )
    found.sort(key=lambda item: item.iteration)
    return found


def latest_checkpoint(
    directory: str | Path,
    geo: str,
    run: str,
) -> Checkpoint | None:
    found = list_checkpoints(directory, geo, run)
    if not found:
        return None
    return found[-1]


def require_latest_checkpoint(
    directory: str | Path,
    geo: str,
    run: str,
) -> Checkpoint:
    found = latest_checkpoint(directory, geo, run)
    if found is None:
        raise FileNotFoundError(
            f"No checkpoint case+data pair in {directory}. "
            "The setup case is not a solution checkpoint, so the "
            "iteration counter cannot be continued."
        )
    return found


def prune_checkpoints(
    directory: str | Path,
    geo: str,
    run: str,
    *,
    keep: int = CHECKPOINT_KEEP,
) -> list[Path]:
    """Delete complete pairs older than the newest ``keep``. Return paths."""
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError(f"keep must be a positive integer, got {keep!r}.")
    found = list_checkpoints(directory, geo, run)
    stale = found[:-keep]
    removed: list[Path] = []
    for item in stale:
        for path in (item.case_path, item.data_path):
            path.unlink()
            removed.append(path)
    return removed


def iteration_chunks(count: int, every: int | None) -> tuple[int, ...]:
    """Split ``count`` into steps of at most ``every``. None is one step."""
    total = _positive_int("count", count)
    interval = require_checkpoint_every(every)
    if interval is None:
        return (total,)
    chunks: list[int] = []
    left = total
    while left:
        step = min(interval, left)
        chunks.append(step)
        left -= step
    return tuple(chunks)


def should_write_checkpoint(
    *,
    every: int | None,
    end_iteration: int,
    stopped_early: bool,
    chunk_index: int,
    chunk_count: int,
) -> bool:
    """True at multiples of ``every``, on an early stop, or on a remainder."""
    interval = require_checkpoint_every(every)
    if interval is None or end_iteration < 1:
        return False
    if stopped_early:
        return True
    if end_iteration % interval == 0:
        return True
    return chunk_count > 1 and chunk_index == chunk_count - 1


def run_checkpointed_iterations(
    iterate: Callable[[int], object],
    *,
    count: int,
    every: int | None,
    start_iteration: int,
    read_iteration: Callable[[], int | None] | None = None,
    on_checkpoint: Callable[[int], object] | None = None,
) -> dict:
    """Run ``count`` iterations after ``start_iteration``.

    ``start_iteration`` is the counter already stored in the data file
    (0 on a fresh solve). ``iterate(step)`` requests that many more
    iterations; it does not reset the counter.

    When ``read_iteration`` reports a new value strictly inside a chunk,
    the chunk stopped early. Later chunks are not requested, and the
    checkpoint uses that value. A read that did not advance is ignored
    so a stale transcript cannot skip the rest of the solve.
    """
    interval = require_checkpoint_every(every)
    start = _nonnegative_int("start_iteration", start_iteration)
    chunks = iteration_chunks(count, interval)
    cursor = start
    stopped = False
    written: list[int] = []
    for index, step in enumerate(chunks):
        before = read_iteration() if read_iteration is not None else None
        iterate(step)
        requested_end = cursor + step
        end = requested_end
        chunk_stopped = False
        if read_iteration is not None and interval is not None:
            after = read_iteration()
            advanced = after is not None and (before is None or after > before)
            if advanced and cursor < after < requested_end:
                end = after
                chunk_stopped = True
        if on_checkpoint is not None and should_write_checkpoint(
            every=interval,
            end_iteration=end,
            stopped_early=chunk_stopped,
            chunk_index=index,
            chunk_count=len(chunks),
        ):
            on_checkpoint(end)
            written.append(end)
        cursor = end
        if chunk_stopped:
            stopped = True
            break
    return {
        "end_iteration": cursor,
        "iterations_completed": cursor - start,
        "stopped_early": stopped,
        "checkpoint_iterations": written,
    }


def resume_iteration_plan(
    *,
    checkpoint_iteration: int,
    max_iterations: int,
    pre_convergence_iterations: int,
    use_ramp: bool,
) -> dict:
    """Iterations still to request. Does not include iterations already run.

    Phase 1 is the convergence-off ramp window. A checkpoint at or after
    that window has phase 1 remaining of 0, so resume does not spend
    another ramp with stopping disabled. The UDF ramp follows ``N_ITER``
    in the data file, which this plan does not reset.
    """
    done = _positive_int("checkpoint_iteration", checkpoint_iteration)
    limit = _positive_int("max_iterations", max_iterations)
    if done >= limit:
        raise ValueError(
            "checkpoint iteration is already at max_iterations: "
            f"{done} >= {limit}."
        )
    if not isinstance(use_ramp, bool):
        raise TypeError(f"use_ramp must be a bool, got {use_ramp!r}.")
    if use_ramp:
        pre = _positive_int(
            "pre_convergence_iterations",
            pre_convergence_iterations,
        )
        if pre > limit:
            raise ValueError(
                "pre_convergence_iterations must be <= max_iterations, "
                f"got {pre} and {limit}."
            )
        if done < pre:
            phase1 = pre - done
            phase2 = limit - pre
            phase2_start = pre
        else:
            phase1 = 0
            phase2 = limit - done
            phase2_start = done
    else:
        phase1 = 0
        phase2 = limit - done
        phase2_start = done
    return {
        "checkpoint_iteration": done,
        "phase1_remaining": phase1,
        "phase2_remaining": phase2,
        "phase2_start": phase2_start,
    }


def refuse_finished_checkpoint(iteration: int, max_iterations) -> None:
    """Raise when the manifest limit says this checkpoint has nothing left."""
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        return
    if iteration >= max_iterations:
        raise ValueError(
            f"Checkpoint iteration {iteration} is already at "
            f"max_iterations {max_iterations}. Nothing remains to solve."
        )


def _positive_int(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return value


def _nonnegative_int(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an integer >= 0, got {value!r}.")
    return value
