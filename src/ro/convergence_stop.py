"""Convergence-stop settings recorded on the run manifest.

Production keeps the existing LMH and spacer-dP stop. ``campaign_v3``
tightens the relative-change criterion, adds the CP monitor, and refuses
a window that starts during the UDF flux ramp. ``max_iterations`` stays
the production cap.
"""

from __future__ import annotations

from typing import Any

CAMPAIGN_V3_PROFILE = "campaign_v3"


def require_window_after_ramp(
    *,
    enabled: bool,
    include_cp: bool,
    initial_values_to_ignore: int,
    ramp_full_iteration: int,
) -> None:
    """Refuse a campaign_v3 window that overlaps the flux ramp.

    Fluent ignores the first ``initial_values_to_ignore`` samples, then
    applies ``previous_values_to_consider``. The ignored prefix has to
    cover ``ramp_full_iteration`` so the monitored window starts after
    the ramp.
    """
    if not enabled:
        return
    if not include_cp:
        raise ValueError(
            "qoi_window_after_ramp requires qoi_stop_include_cp=True "
            "so LMH, pressure drop, and CP share the post-ramp window."
        )
    ignore = int(initial_values_to_ignore)
    ramp = int(ramp_full_iteration)
    if ignore < ramp:
        raise ValueError(
            "qoi_window_after_ramp requires qoi_initial_values_to_ignore "
            f">= ramp_full_iteration ({ramp}) so the monitored window "
            f"starts after the flux ramp. Got {ignore}."
        )


def campaign_v3_stop_overrides(
    *,
    stop_criterion: float,
    window: int,
    initial_values_to_ignore: int,
    ramp_full_iteration: int,
    residual_target: float,
    max_iterations: int,
) -> dict[str, Any]:
    """Solver overrides for the campaign_v3 stop. Window starts after the ramp."""
    criterion = float(stop_criterion)
    samples = int(window)
    ramp = int(ramp_full_iteration)
    ignore = int(initial_values_to_ignore)
    if criterion <= 0.0:
        raise ValueError(f"stop criterion must be positive, got {criterion!r}.")
    if samples < 1:
        raise ValueError(f"monitor window must be >= 1, got {samples!r}.")
    if ignore < ramp:
        ignore = ramp
    residual = float(residual_target)
    iterations = int(max_iterations)
    if residual <= 0.0 or iterations < 1:
        raise ValueError(
            "campaign_v3 stop requires a positive residual target and "
            f"max_iterations, got residual={residual!r}, "
            f"max_iterations={iterations!r}."
        )
    overrides = {
        "qoi_stop_criterion": criterion,
        "qoi_previous_values_to_consider": samples,
        "qoi_initial_values_to_ignore": ignore,
        "qoi_stop_include_cp": True,
        "qoi_window_after_ramp": True,
        "residual_target": residual,
        "max_iterations": iterations,
    }
    require_window_after_ramp(
        enabled=True,
        include_cp=True,
        initial_values_to_ignore=ignore,
        ramp_full_iteration=ramp,
    )
    return overrides


def convergence_stop_manifest_fields(
    *,
    stop_criterion: float,
    previous_values: int,
    initial_values_to_ignore: int,
    include_cp: bool,
    window_after_ramp: bool,
    ramp_full_iteration: int,
    stop_reports: list[str],
    cp_monitor_name: str | None,
    cp_monitor_scope: str | None,
) -> dict[str, Any]:
    """Non-parameter manifest fields: the stop rule this run actually used."""
    return {
        "qoi_stop_criterion": float(stop_criterion),
        "qoi_previous_values_to_consider": int(previous_values),
        "qoi_initial_values_to_ignore": int(initial_values_to_ignore),
        "qoi_stop_include_cp": bool(include_cp),
        "qoi_window_after_ramp": bool(window_after_ramp),
        "ramp_full_iteration": int(ramp_full_iteration),
        "qoi_stop_reports": list(stop_reports),
        "cp_monitor_name": cp_monitor_name,
        "cp_monitor_scope": cp_monitor_scope,
    }


def launch_limit_overrides(
    *,
    processor_count: int | None = None,
    max_iterations: int | None = None,
    disable_ramp_convergence_safety: bool = False,
) -> dict[str, Any]:
    """Optional short-run limits. Omitted keys are left to run_config."""
    overrides: dict[str, Any] = {}
    if processor_count is not None:
        count = int(processor_count)
        if count < 1:
            raise ValueError(f"processor_count must be >= 1, got {count!r}.")
        overrides["processor_count"] = count
    if max_iterations is not None:
        count = int(max_iterations)
        if count < 1:
            raise ValueError(f"max_iterations must be >= 1, got {count!r}.")
        overrides["max_iterations"] = count
    if disable_ramp_convergence_safety:
        overrides["use_ramp_convergence_safety"] = False
    return overrides
