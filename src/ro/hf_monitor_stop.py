"""HF monitor stop for one fidelity level.

Importing this module does not launch Fluent. The stop is the LF window
rule plus a continuity cap: both monitors have
``(max - min) / |last|`` within tolerance over the last N iterations,
the window starts strictly after ``ramp_end``, and continuity is at most
``continuity_max``. The production convergence gate is a separate check
after extraction. This module does not change the LF stop.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Callable

from ro.lf_monitor_stop import lf_monitor_decision
from ro.solver_common import (
    STOP_REASON_DIVERGED,
    STOP_REASON_MAX_ITER_REACHED,
    STOP_REASON_QOI_CONVERGED,
    STOP_REASON_RESIDUAL_CONVERGED,
    STOP_REASON_UNKNOWN_EARLY_STOP,
)

STOP_REASON_HF_MONITOR_STABLE = "hf_monitor_stable"
PHASE_HF_MONITOR = "hf_monitor"
PHASE_PRODUCTION = "production"

_STOP_KEYS = ("tol_lmh", "tol_dp", "n", "continuity_max")
_RESUME_KEYS = ("iterations_already_run", "max_iterations_total")
_KEPT_CLASSIFIED = (
    STOP_REASON_RESIDUAL_CONVERGED,
    STOP_REASON_QOI_CONVERGED,
    STOP_REASON_DIVERGED,
    STOP_REASON_MAX_ITER_REACHED,
)


def require_hf_monitor_stop(value: Any) -> dict[str, Any]:
    """Return the stop spec. Every key is required. No defaults."""
    if not isinstance(value, Mapping):
        raise TypeError(
            "hf_monitor_stop must be an object, got "
            f"{type(value).__name__}."
        )
    unknown = sorted(set(value) - set(_STOP_KEYS))
    if unknown:
        raise ValueError(f"hf_monitor_stop has unknown keys: {unknown}.")
    missing = [key for key in _STOP_KEYS if key not in value]
    if missing:
        raise KeyError(f"hf_monitor_stop is missing {missing}.")
    return {
        "tol_lmh": _positive_float("tol_lmh", value["tol_lmh"]),
        "tol_dp": _positive_float("tol_dp", value["tol_dp"]),
        "n": _positive_int("n", value["n"]),
        "continuity_max": _positive_float(
            "continuity_max", value["continuity_max"]
        ),
    }


def require_hf_production_resume(value: Any) -> dict[str, Any]:
    """Resume budget after a monitor stop. Both bounds are required."""
    if not isinstance(value, Mapping):
        raise TypeError(
            "hf_production_resume must be an object, got "
            f"{type(value).__name__}."
        )
    unknown = sorted(set(value) - set(_RESUME_KEYS))
    if unknown:
        raise ValueError(f"hf_production_resume has unknown keys: {unknown}.")
    missing = [key for key in _RESUME_KEYS if key not in value]
    if missing:
        raise KeyError(f"hf_production_resume is missing {missing}.")
    already = _positive_int(
        "iterations_already_run", value["iterations_already_run"]
    )
    total = _positive_int(
        "max_iterations_total", value["max_iterations_total"]
    )
    if already >= total:
        raise ValueError(
            "iterations_already_run must be < max_iterations_total, "
            f"got {already} and {total}."
        )
    return {
        "iterations_already_run": already,
        "max_iterations_total": total,
    }


def hf_monitor_ready(
    decision: Mapping[str, Any],
    continuity: Any,
    continuity_max: float,
) -> bool:
    """True when the monitor window is stable and continuity is inside the cap."""
    if not decision.get("stable"):
        return False
    limit = _positive_float("continuity_max", continuity_max)
    number = _finite_or_none(continuity)
    if number is None:
        return False
    return number <= limit


def run_hf_monitor_iterations(
    iterate: Callable[[int], Any],
    read_decision: Callable[[], Mapping[str, Any]],
    read_continuity: Callable[[], Any],
    *,
    continuity_max: float,
    ramp_end: int,
    n: int,
    max_iterations: int,
) -> dict[str, Any]:
    """Iterate until the HF monitor rule holds or ``max_iterations``.

    ``iterate(count)`` runs that many iterations with residual and QoI
    stopping already off. The first check is at ``ramp_end + n``.
    Continuity comes from ``read_continuity``; a missing value is not a stop.
    """
    limit_continuity = _positive_float("continuity_max", continuity_max)
    ramp = _nonnegative_int("ramp_end", ramp_end)
    count = _positive_int("n", n)
    limit = _positive_int("max_iterations", max_iterations)
    earliest = ramp + count

    def _finish(done: int, reason: str) -> dict[str, Any]:
        decision = dict(read_decision())
        continuity = _finite_or_none(read_continuity())
        decision["continuity"] = continuity
        decision["continuity_max"] = limit_continuity
        decision["stop_reason"] = reason
        decision["iterations_run"] = done
        return decision

    if earliest > limit:
        iterate(limit)
        return _finish(limit, STOP_REASON_MAX_ITER_REACHED)
    iterate(earliest)
    decision = read_decision()
    continuity = read_continuity()
    if hf_monitor_ready(decision, continuity, limit_continuity):
        ready = dict(decision)
        ready["continuity"] = _finite_or_none(continuity)
        ready["continuity_max"] = limit_continuity
        ready["stop_reason"] = STOP_REASON_HF_MONITOR_STABLE
        ready["iterations_run"] = earliest
        return ready
    done = earliest
    while done < limit:
        iterate(1)
        done += 1
        decision = read_decision()
        continuity = read_continuity()
        if hf_monitor_ready(decision, continuity, limit_continuity):
            ready = dict(decision)
            ready["continuity"] = _finite_or_none(continuity)
            ready["continuity_max"] = limit_continuity
            ready["stop_reason"] = STOP_REASON_HF_MONITOR_STABLE
            ready["iterations_run"] = done
            return ready
    return _finish(limit, STOP_REASON_MAX_ITER_REACHED)


def hf_monitor_manifest_fields(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Non-parameter manifest fields for one HF monitor decision."""
    return {
        "iterations_run": decision["iterations_run"],
        "hf_monitor_ramp_end": decision["ramp_end"],
        "hf_monitor_window": {
            "start": decision.get("window_start"),
            "end": decision.get("window_end"),
            "n": decision["n"],
        },
        "hf_monitor_lmh_range": decision.get("lmh_range"),
        "hf_monitor_dp_range": decision.get("dp_range"),
        "hf_monitor_tol_lmh": decision["tol_lmh"],
        "hf_monitor_tol_dp": decision["tol_dp"],
        "hf_monitor_n": decision["n"],
        "hf_monitor_continuity": decision.get("continuity"),
        "hf_monitor_continuity_max": decision["continuity_max"],
    }


def should_resume_production(
    *,
    stop_reason: Any,
    convergence_quality: Any,
    iterations_run: Any,
    max_iterations: Any,
) -> bool:
    """True when the monitor stopped early and the production gate failed.

    A missing gate result after ``hf_monitor_stable`` raises. PASS and a
    stop that already used the iteration cap do not resume.
    """
    if stop_reason != STOP_REASON_HF_MONITOR_STABLE:
        return False
    if convergence_quality not in ("PASS", "FAIL"):
        raise ValueError(
            "HF monitor stop requires a production gate result of "
            f"PASS or FAIL, got {convergence_quality!r}."
        )
    done = _positive_int("iterations_run", iterations_run)
    limit = _positive_int("max_iterations", max_iterations)
    if convergence_quality == "PASS":
        return False
    return done < limit


def resume_iteration_budget(
    iterations_already_run: int,
    max_iterations_total: int,
) -> int:
    """Iterations still available under the total cap."""
    spec = require_hf_production_resume(
        {
            "iterations_already_run": iterations_already_run,
            "max_iterations_total": max_iterations_total,
        }
    )
    return spec["max_iterations_total"] - spec["iterations_already_run"]


def resume_stop_reason(
    classified: str,
    *,
    iterations_already_run: int,
    final_iteration: int | None,
    max_iterations_total: int,
) -> str:
    """Keep a production classification. A full second phase is the cap.

    A restarted transcript numbers the second phase from 1, so a completed
    budget is below ``max_iterations_total`` and would otherwise look like
    an early stop. Continuity of the iteration counter is the other case:
    the last iteration itself reaches the cap.
    """
    if classified in _KEPT_CLASSIFIED:
        return classified
    if classified != STOP_REASON_UNKNOWN_EARLY_STOP or final_iteration is None:
        return classified
    absolute = int(final_iteration)
    restarted = int(iterations_already_run) + absolute
    if absolute >= int(max_iterations_total) or restarted >= int(max_iterations_total):
        return STOP_REASON_MAX_ITER_REACHED
    return classified


def iterations_after_resume(phase1: int, first: int, last: int) -> int:
    """Total iterations after a second session.

    ``first`` and ``last`` are the iteration numbers in that session's
    transcript only. A continued counter and a counter that starts again
    at 1 both contribute ``last - first + 1`` new iterations.
    """
    done = _positive_int("phase1", phase1)
    start = _positive_int("first", first)
    end = _positive_int("last", last)
    if end < start:
        raise ValueError(
            f"phase-2 iteration {end} is before {start}."
        )
    return done + (end - start + 1)


def phase_record(
    name: str,
    stop_reason: str,
    iterations_run: int,
    convergence_quality: str,
    failures: list[Any] | None = None,
) -> dict[str, Any]:
    """One recorded phase. ``name`` is ``hf_monitor`` or ``production``."""
    if name not in (PHASE_HF_MONITOR, PHASE_PRODUCTION):
        raise ValueError(
            f"phase name must be {PHASE_HF_MONITOR!r} or "
            f"{PHASE_PRODUCTION!r}, got {name!r}."
        )
    if convergence_quality not in ("PASS", "FAIL"):
        raise ValueError(
            "convergence_quality must be PASS or FAIL, got "
            f"{convergence_quality!r}."
        )
    return {
        "name": name,
        "stop_reason": stop_reason,
        "iterations_run": _positive_int("iterations_run", iterations_run),
        "convergence_quality": convergence_quality,
        "failures": list(failures or []),
    }


def monitor_decision_for_series(
    lmh,
    dp,
    *,
    ramp_end: int,
    n: int,
    tol_lmh: float,
    tol_dp: float,
) -> dict[str, Any]:
    """Monitor-window decision used by the HF stop. Same rule as the LF window."""
    return lf_monitor_decision(
        lmh,
        dp,
        ramp_end=ramp_end,
        n=n,
        tol_lmh=tol_lmh,
        tol_dp=tol_dp,
    )


def _positive_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}.")
    return number


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value!r}.")
    return value


def _nonnegative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}.")
    return value


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return number
