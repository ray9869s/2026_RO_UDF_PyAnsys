"""Checkpoint and setup files in a finished run leaf. Does not launch Fluent.

A checkpoint is ``{geo}_{run}_checkpoint_{iteration}.cas.h5`` plus the
matching ``.dat.h5``. The setup case is ``{geo}_{run}_setup.cas.h5``.
The final pair is ``{geo}_{run}_final.cas.h5`` and ``.dat.h5``. Only
the checkpoint files and the setup case are removable. ``post/``, logs,
the manifest, and the final pair stay.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ro.convergence_quality import QUALITY_PASS

_SUMMARY = Path("post") / "reports" / "summary_metrics_wide.csv"
_EXTRACT_SOURCE = Path("post") / "reports" / "extract_source.json"


def checkpoint_files(directory, geo: str, run: str) -> list[Path]:
    """Checkpoint case and data files in the run directory, including orphans."""
    root = Path(directory)
    if not root.is_dir():
        return []
    pattern = re.compile(
        rf"^{re.escape(geo)}_{re.escape(run)}_checkpoint_\d+\.(?:cas|dat)\.h5$"
    )
    return sorted(
        path
        for path in root.iterdir()
        if path.is_file() and pattern.fullmatch(path.name)
    )


def setup_case_files(directory, geo: str, run: str) -> list[Path]:
    """``{geo}_{run}_setup.cas.h5`` when it is in the run directory."""
    root = Path(directory)
    path = root / f"{geo}_{run}_setup.cas.h5"
    if path.is_file():
        return [path]
    return []


def final_pair_files(directory, geo: str, run: str) -> list[Path]:
    """Final case and data files present in the run directory."""
    root = Path(directory)
    names = (
        f"{geo}_{run}_final.cas.h5",
        f"{geo}_{run}_final.dat.h5",
    )
    return [root / name for name in names if (root / name).is_file()]


def final_pair_ready(directory, geo: str, run: str) -> bool:
    """True when both final files exist and are non-empty."""
    root = Path(directory)
    paths = [
        root / f"{geo}_{run}_final.cas.h5",
        root / f"{geo}_{run}_final.dat.h5",
    ]
    return all(_nonempty_file(path) for path in paths)


def releasable_files(directory, geo: str, run: str) -> list[Path]:
    """Checkpoint files and the setup case. The final pair is not included."""
    return checkpoint_files(directory, geo, run) + setup_case_files(
        directory, geo, run
    )


def convergence_gate(run_directory) -> str | None:
    """``convergence_quality`` from the run manifest, or None when absent."""
    path = Path(run_directory) / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("convergence_quality")
    if isinstance(value, str) and value:
        return value
    return None


def extract_record_present(run_directory) -> bool:
    """True when the wide CSV and extract source record are non-empty files."""
    root = Path(run_directory)
    return _nonempty_file(root / _SUMMARY) and _nonempty_file(root / _EXTRACT_SOURCE)


def directory_bytes(directory) -> int:
    """Sum of regular file sizes under ``directory``."""
    root = Path(directory)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
    return total


def format_bytes(size: int) -> str:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"size must be an integer >= 0, got {size!r}.")
    return f"{size} bytes ({size / (1024 ** 3):.2f} GiB)"


def unlink_files(paths) -> tuple[list[Path], list[str]]:
    """Unlink each path. Return the paths removed and any OS errors."""
    removed: list[Path] = []
    errors: list[str] = []
    for path in paths:
        try:
            Path(path).unlink()
        except OSError as exc:
            errors.append(f"{path}: {exc}")
        else:
            removed.append(Path(path))
    return removed, errors


def release_after_pass(run_directory, geo: str, run: str, *, keep: bool) -> dict:
    """Drop checkpoint files and the setup case when the gate is PASS.

    ``keep`` True leaves every file. A gate other than PASS, or a missing
    final pair, also leaves them. The caller decides that the extract
    succeeded. Returns removed paths, unlink errors, a keep reason, and
    the leaf size after the decision.
    """
    if not isinstance(keep, bool):
        raise TypeError(f"keep must be a bool, got {keep!r}.")
    gate = convergence_gate(run_directory)
    if keep:
        reason = "--keep-checkpoints"
    elif gate != QUALITY_PASS:
        reason = "gate missing" if gate is None else f"gate {gate}"
    elif not final_pair_ready(run_directory, geo, run):
        reason = "final pair missing"
    else:
        reason = None
    if reason is not None:
        return {
            "removed": [],
            "errors": [],
            "kept_reason": reason,
            "leaf_bytes": directory_bytes(run_directory),
        }
    removed, errors = unlink_files(releasable_files(run_directory, geo, run))
    return {
        "removed": removed,
        "errors": errors,
        "kept_reason": None,
        "leaf_bytes": directory_bytes(run_directory),
    }


def cleanup_line(outcome: dict) -> str:
    """One line: leaf size, and whether checkpoint files were removed."""
    size = format_bytes(outcome["leaf_bytes"])
    if outcome["kept_reason"]:
        return (
            f"Leaf size after cleanup: {size}. "
            f"Kept checkpoints and setup case ({outcome['kept_reason']})."
        )
    return (
        f"Leaf size after cleanup: {size}. "
        f"Removed {len(outcome['removed'])} checkpoint and setup files."
    )


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
